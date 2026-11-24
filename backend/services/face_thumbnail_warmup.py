"""Idle background worker for prebuilding face crop thumbnails."""

from __future__ import annotations

import logging
import threading
from time import monotonic
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .face_thumbnails import (
    get_face_library_signature,
    warm_missing_face_thumbnails,
)
from .preview_cache_budget import cache_warmup_has_room

logger = logging.getLogger("face_manager.face_thumbnail_warmup")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FaceThumbnailWarmupState:
    status: str = "stopped"
    started_at: Optional[str] = None
    last_run_at: Optional[str] = None
    next_face_id: int = 0
    total_faces: int = 0
    cycle_scanned_faces: int = 0
    scanned_faces: int = 0
    created_thumbnails: int = 0
    skipped_existing: int = 0
    skipped_missing_source: int = 0
    failed_faces: int = 0
    eta_seconds: Optional[float] = None
    last_error: Optional[str] = None
    # True once a full sweep confirmed every face already has a thumbnail.
    cache_complete: bool = False
    user_paused: bool = False


class FaceThumbnailWarmupQueue:
    """Warm missing thumbnails only while the rest of the backend is idle."""

    def __init__(
        self,
        *,
        is_idle: Callable[[], bool],
        batch_size: int = 128,
        scan_limit: int = 1024,
        idle_poll_seconds: float = 10.0,
        busy_poll_seconds: float = 3.0,
        batch_pause_seconds: float = 0.02,
        retry_seconds: float = 60.0,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._is_idle = is_idle
        self._on_change = on_change
        self._batch_size = max(1, int(batch_size))
        self._scan_limit = max(self._batch_size, int(scan_limit))
        self._idle_poll_seconds = max(0.25, float(idle_poll_seconds))
        self._busy_poll_seconds = max(0.25, float(busy_poll_seconds))
        self._batch_pause_seconds = max(0.0, float(batch_pause_seconds))
        self._retry_seconds = max(self._idle_poll_seconds, float(retry_seconds))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._restart_on_exit = False
        self._active_batch = False
        self._generation = 0
        self._state = FaceThumbnailWarmupState()
        self._visible = True
        # Fingerprint of the library at the last completed full sweep. When the
        # sweep created nothing and the fingerprint is unchanged, the worker
        # knows the cache is fully warm and stops re-scanning until faces are
        # added or removed.
        self._last_completed_signature: Optional[tuple[int, int]] = None
        self._fully_warm = False
        self._retry_at = 0.0
        self._retry_interval = self._retry_seconds

    def start(self) -> None:
        """Start the daemon worker if it is not already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._restart_on_exit = self._stop_event.is_set()
                return
            self._start_locked()

    def _start_locked(self) -> None:
        self._restart_on_exit = False
        self._stop_event.clear()
        self._wake_event.set()
        self._state.status = "paused" if self._state.user_paused else "idle"
        self._state.started_at = _utc_now()
        self._thread = threading.Thread(
            target=self._run_worker, name="face-thumbnail-warmup", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5) -> bool:
        """Return whether the worker stopped, retaining blocked workers on timeout."""
        with self._lock:
            self._restart_on_exit = False
            self._stop_event.set()
            self._wake_event.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

    def _run_worker(self) -> None:
        try:
            self._run_loop()
        finally:
            with self._lock:
                self._active_batch = False
                self._state.status = "stopped"
                self._thread = None
                if self._restart_on_exit:
                    self._start_locked()

    def has_active_batch(self) -> bool:
        with self._lock:
            return self._active_batch

    def invalidate_library(self) -> None:
        """Discard the previous library's cursor and warmup fingerprint."""
        with self._lock:
            self._generation += 1
            paused = self._state.user_paused
            self._state = FaceThumbnailWarmupState(
                status="paused" if paused else "idle", user_paused=paused
            )
            self._last_completed_signature = None
            self._fully_warm = False
            self._retry_at = 0.0
            self._retry_interval = self._retry_seconds
            self._visible = True
        self.wake()

    def wake(self) -> None:
        """Wake the worker so it can re-check idle state soon."""
        self._wake_event.set()

    def pause(self) -> dict:
        """Pause thumbnail preparation after the current small batch."""
        with self._lock:
            self._visible = True
            self._state.user_paused = True
            self._state.status = "paused"
            self._state.last_error = None
            result = asdict(self._state)
        self._wake_event.set()
        self._notify_change()
        return result

    def resume(self) -> dict:
        """Resume user-paused or cancelled thumbnail preparation."""
        with self._lock:
            self._visible = True
            self._state.user_paused = False
            self._state.status = "idle"
            self._state.last_error = None
            self._retry_at = 0.0
            self._retry_interval = self._retry_seconds
            result = asdict(self._state)
        self._wake_event.set()
        self._notify_change()
        return result

    def cancel(self) -> dict:
        """Stop the current warmup cycle until explicitly resumed."""
        with self._lock:
            self._visible = True
            self._state.user_paused = True
            self._state.status = "cancelled"
            self._state.last_run_at = _utc_now()
            self._state.eta_seconds = None
            self._state.cache_complete = False
            result = asdict(self._state)
        self._wake_event.set()
        self._notify_change()
        return result

    def dismiss_history(self) -> bool:
        """Clear a terminal warmup result while preserving the cache fingerprint."""
        with self._lock:
            if not (
                self._state.cache_complete
                or self._state.status in {"failed", "cancelled"}
            ):
                return False
            user_paused = self._state.user_paused
            status = "cancelled" if user_paused else "idle"
            self._state = FaceThumbnailWarmupState(
                status=status,
                user_paused=user_paused,
            )
            self._visible = False
        self._notify_change()
        return True

    def snapshot(self) -> dict:
        """Return current warmup state for diagnostics."""
        with self._lock:
            return {"task": asdict(self._state) if self._visible else None}

    def _notify_change(self) -> None:
        """Notify the change subscriber, isolating it from worker failures.

        Must be called outside ``self._lock`` because the subscriber typically
        reads :meth:`snapshot`, which re-acquires the non-reentrant lock.
        """
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # pragma: no cover - subscriber must never break work
            logger.exception("Thumbnail warmup change notification failed")

    def _set_status(self, status: str, last_error: Optional[str] = None) -> None:
        with self._lock:
            if self._state.user_paused or self._stop_event.is_set():
                return
            if status in {"running", "failed"}:
                self._visible = True
            self._state.status = status
            self._state.last_error = last_error
        self._notify_change()

    def _record_batch(self, result, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._stop_event.is_set():
                return False
            self._visible = True
            if not self._state.user_paused:
                self._state.status = "running"
            self._state.last_run_at = _utc_now()
            self._state.total_faces = result.total_faces
            if result.reached_end:
                cycle_scanned_faces = result.total_faces
                next_face_id = 0
            else:
                cycle_scanned_faces = min(
                    result.total_faces,
                    self._state.cycle_scanned_faces + result.scanned_faces,
                )
                next_face_id = result.highest_face_id
            self._state.next_face_id = next_face_id
            self._state.cycle_scanned_faces = cycle_scanned_faces
            self._state.scanned_faces += result.scanned_faces
            self._state.created_thumbnails += result.created_thumbnails
            self._state.skipped_existing += result.skipped_existing
            self._state.skipped_missing_source += result.skipped_missing_source
            self._state.failed_faces += result.failed_faces
            self._state.eta_seconds = self._estimate_eta_locked()
            self._state.last_error = None
        self._notify_change()
        return True

    def _estimate_eta_locked(self) -> Optional[float]:
        if not self._state.started_at or self._state.cycle_scanned_faces <= 0:
            return None
        if self._state.total_faces <= self._state.cycle_scanned_faces:
            return 0.0
        try:
            started_at = datetime.fromisoformat(self._state.started_at)
        except ValueError:
            return None
        elapsed_seconds = max(
            1e-6,
            (datetime.now(timezone.utc) - started_at).total_seconds(),
        )
        rate = self._state.cycle_scanned_faces / elapsed_seconds
        if rate <= 0:
            return None
        remaining = max(0, self._state.total_faces - self._state.cycle_scanned_faces)
        return remaining / rate

    def _next_face_id(self) -> int:
        with self._lock:
            return self._state.next_face_id

    def _begin_cycle_if_needed(self) -> None:
        with self._lock:
            if self._state.cycle_scanned_faces == 0:
                self._state.started_at = _utc_now()

    def _wait(self, seconds: float) -> None:
        self._wake_event.wait(seconds)
        self._wake_event.clear()

    def _run_loop(self) -> None:
        cycle_unresolved = 0
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    user_paused = self._state.user_paused
                if user_paused:
                    self._wait(self._busy_poll_seconds)
                    continue
                if not self._is_idle():
                    self._set_status("paused")
                    self._wait(self._busy_poll_seconds)
                    continue
                if not cache_warmup_has_room():
                    with self._lock:
                        self._state.cache_complete = False
                        self._fully_warm = False
                    self._set_status("idle")
                    self._wait(self._idle_poll_seconds)
                    continue

                with self._lock:
                    generation = self._generation
                signature = get_face_library_signature()
                with self._lock:
                    if generation != self._generation:
                        continue
                    skip_sweep = signature == self._last_completed_signature and (
                        self._fully_warm or monotonic() < self._retry_at
                    )
                if skip_sweep:
                    self._set_status("idle")
                    self._wait(self._idle_poll_seconds)
                    continue

                if self._next_face_id() == 0:
                    cycle_unresolved = 0
                self._begin_cycle_if_needed()
                with self._lock:
                    if (generation != self._generation or self._state.user_paused
                            or self._stop_event.is_set()):
                        continue
                    self._active_batch = True
                try:
                    self._set_status("running")
                    result = warm_missing_face_thumbnails(
                        after_face_id=self._next_face_id(),
                        max_created=self._batch_size,
                        scan_limit=self._scan_limit,
                        stop_event=self._stop_event,
                    )
                    if not self._record_batch(result, generation):
                        continue
                finally:
                    with self._lock:
                        self._active_batch = False
                cycle_unresolved += result.skipped_missing_source + result.failed_faces

                if result.reached_end:
                    with self._lock:
                        if generation != self._generation:
                            continue
                        if signature != self._last_completed_signature:
                            self._retry_interval = self._retry_seconds
                        self._last_completed_signature = signature
                        self._fully_warm = cycle_unresolved == 0
                        self._retry_at = monotonic() + self._retry_interval
                        if not self._fully_warm:
                            self._retry_interval = min(
                                self._retry_interval * 2, max(900.0, self._retry_seconds)
                            )
                        self._state.cycle_scanned_faces = 0
                        self._state.eta_seconds = None
                        self._state.cache_complete = self._fully_warm
                        if not self._state.user_paused:
                            self._state.status = "idle"
                    self._notify_change()
                    self._wait(self._idle_poll_seconds)
                elif result.created_thumbnails > 0:
                    with self._lock:
                        self._state.cache_complete = False
                    self._wait(self._batch_pause_seconds)
                else:
                    self._wait(0)
            except Exception as exc:
                logger.exception("Face thumbnail warmup failed")
                self._set_status("failed", str(exc))
                self._wait(self._idle_poll_seconds)
