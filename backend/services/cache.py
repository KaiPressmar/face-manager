"""Shared in-memory cache utilities for API and storage queries."""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any, Hashable

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - optional during lightweight tests
    np = None

DEFAULT_CACHE_TTL_SECONDS = 5.0
DEFAULT_CACHE_MAX_BYTES = 1536 * 1024 * 1024
DEFAULT_CACHE_MAX_ENTRIES = 4096
DEFAULT_CACHE_MAX_ENTRY_BYTES = 256 * 1024 * 1024


def _read_int_env(name: str, default: int) -> int:
    """Return an integer environment variable or a safe default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass(slots=True)
class CacheEntry:
    """Represent one cached value and its eviction metadata."""

    value: Any
    expires_at: float
    size_bytes: int
    tags: frozenset[str] = field(default_factory=frozenset)
    created_at: float = field(default_factory=time.monotonic)
    last_accessed_at: float = field(default_factory=time.monotonic)
    access_count: int = 0


@dataclass(slots=True)
class CacheStats:
    """Expose aggregate cache statistics for logging and diagnostics."""

    entry_count: int
    total_bytes: int
    hits: int
    misses: int
    evictions: int
    expirations: int
    invalidations: int
    rejected_entries: int


class AppCache:
    """Bounded in-memory cache with TTL, tag invalidation, and byte budgets."""

    def __init__(
        self,
        default_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        max_entry_bytes: int = DEFAULT_CACHE_MAX_ENTRY_BYTES,
    ):
        self.default_ttl_seconds = max(0.1, float(default_ttl_seconds))
        self.max_bytes = max(1, int(max_bytes))
        self.max_entries = max(1, int(max_entries))
        self.max_entry_bytes = max(1, int(max_entry_bytes))
        self._entries: dict[Hashable, CacheEntry] = {}
        self._lock = threading.RLock()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        self._invalidations = 0
        self._rejected_entries = 0

    def get(self, key: Hashable) -> Any | None:
        """Return a cached value when present and not expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                self._delete_key(key, entry, reason="expired")
                self._misses += 1
                return None
            entry.last_accessed_at = now
            entry.access_count += 1
            self._hits += 1
            return entry.value

    def set(
        self,
        key: Hashable,
        value: Any,
        ttl_seconds: float | None = None,
        tags: set[str] | tuple[str, ...] | None = None,
        size_bytes: int | None = None,
    ) -> Any:
        """Store one cache entry when it fits within configured budgets."""
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(0.1, float(ttl_seconds))
        computed_size = self.estimate_size_bytes(value) if size_bytes is None else max(0, int(size_bytes))
        if computed_size > self.max_entry_bytes or computed_size > self.max_bytes:
            with self._lock:
                self._rejected_entries += 1
            return value

        entry = CacheEntry(
            value=value,
            expires_at=time.monotonic() + ttl,
            size_bytes=computed_size,
            tags=frozenset(tags or ()),
        )

        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._total_bytes -= previous.size_bytes
            self._entries[key] = entry
            self._total_bytes += entry.size_bytes
            self._prune_expired_locked(time.monotonic())
            self._evict_if_needed_locked()
        return value

    def get_or_set(
        self,
        key: Hashable,
        loader: Callable[[], Any],
        ttl_seconds: float | None = None,
        tags: set[str] | tuple[str, ...] | None = None,
        size_bytes: int | None = None,
    ) -> Any:
        """Load and cache a value when absent."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = loader()
        return self.set(
            key,
            value,
            ttl_seconds=ttl_seconds,
            tags=tags,
            size_bytes=size_bytes,
        )

    def invalidate_key(self, key: Hashable) -> None:
        """Remove one cached entry if it exists."""
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return
            self._total_bytes -= entry.size_bytes
            self._invalidations += 1

    def invalidate_tags(self, *tags: str) -> int:
        """Remove every entry containing any requested tag."""
        wanted = {tag for tag in tags if tag}
        if not wanted:
            return 0
        with self._lock:
            matching_keys = [
                key for key, entry in self._entries.items() if entry.tags.intersection(wanted)
            ]
            for key in matching_keys:
                entry = self._entries.pop(key)
                self._total_bytes -= entry.size_bytes
                self._invalidations += 1
            return len(matching_keys)

    def prune_expired(self) -> int:
        """Remove expired entries and return the number removed."""
        with self._lock:
            return self._prune_expired_locked(time.monotonic())

    def clear(self) -> None:
        """Remove every cached entry."""
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            self._total_bytes = 0
            self._invalidations += removed

    def get_stats(self) -> CacheStats:
        """Return a snapshot of cache health metrics."""
        with self._lock:
            return CacheStats(
                entry_count=len(self._entries),
                total_bytes=self._total_bytes,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
                invalidations=self._invalidations,
                rejected_entries=self._rejected_entries,
            )

    def _prune_expired_locked(self, now: float) -> int:
        removed = 0
        expired = [
            (key, entry)
            for key, entry in self._entries.items()
            if entry.expires_at <= now
        ]
        for key, entry in expired:
            self._delete_key(key, entry, reason="expired")
            removed += 1
        return removed

    def _evict_if_needed_locked(self) -> None:
        while self._entries and (
            self._total_bytes > self.max_bytes or len(self._entries) > self.max_entries
        ):
            key, entry = max(
                self._entries.items(),
                key=lambda item: self._eviction_score(item[1]),
            )
            self._delete_key(key, entry, reason="evicted")

    @staticmethod
    def _eviction_score(entry: CacheEntry) -> tuple[float, int, float]:
        stale_for = time.monotonic() - entry.last_accessed_at
        age = time.monotonic() - entry.created_at
        return (stale_for, entry.size_bytes, age)

    def _delete_key(self, key: Hashable, entry: CacheEntry, reason: str) -> None:
        self._entries.pop(key, None)
        self._total_bytes -= entry.size_bytes
        if reason == "expired":
            self._expirations += 1
        elif reason == "evicted":
            self._evictions += 1

    @classmethod
    def estimate_size_bytes(cls, value: Any, seen: set[int] | None = None) -> int:
        """Estimate the in-memory footprint of one cached value."""
        if seen is None:
            seen = set()
        object_id = id(value)
        if object_id in seen:
            return 0
        seen.add(object_id)

        if value is None:
            return 0
        if np is not None and isinstance(value, np.ndarray):
            return int(value.nbytes)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return len(value)
        if isinstance(value, str):
            return len(value.encode("utf-8"))
        if isinstance(value, sqlite3.Row):
            size = sys.getsizeof(value)
            for key in value.keys():
                size += cls.estimate_size_bytes(key, seen)
                size += cls.estimate_size_bytes(value[key], seen)
            return size
        if isinstance(value, Mapping):
            size = sys.getsizeof(value)
            for key, item in value.items():
                size += cls.estimate_size_bytes(key, seen)
                size += cls.estimate_size_bytes(item, seen)
            return size
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            size = sys.getsizeof(value)
            for item in value:
                size += cls.estimate_size_bytes(item, seen)
            return size
        if isinstance(value, AbstractSet):
            size = sys.getsizeof(value)
            for item in value:
                size += cls.estimate_size_bytes(item, seen)
            return size
        if hasattr(value, "__dict__"):
            return sys.getsizeof(value) + cls.estimate_size_bytes(vars(value), seen)
        return sys.getsizeof(value)


app_cache = AppCache(
    default_ttl_seconds=float(
        os.getenv("FACE_MANAGER_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    ),
    max_bytes=_read_int_env("FACE_MANAGER_CACHE_MAX_BYTES", DEFAULT_CACHE_MAX_BYTES),
    max_entries=_read_int_env("FACE_MANAGER_CACHE_MAX_ENTRIES", DEFAULT_CACHE_MAX_ENTRIES),
    max_entry_bytes=_read_int_env(
        "FACE_MANAGER_CACHE_MAX_ENTRY_BYTES",
        DEFAULT_CACHE_MAX_ENTRY_BYTES,
    ),
)
