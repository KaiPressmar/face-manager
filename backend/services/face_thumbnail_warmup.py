"""Idle background worker for prebuilding face crop thumbnails."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .face_thumbnails import (
    get_face_library_signature,
    warm_missing_face_thumbnails,
)

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
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._is_idle = is_idle
        self._on_change = on_change
        self._batch_size = max(1, int(batch_size))
        self._scan_limit = max(self._batch_size, int(scan_limit))
        self._idle_poll_seconds = max(0.25, float(idle_poll_seconds))
        self._busy_poll_seconds = max(0.25, float(busy_poll_seconds))
        self._batch_pause_seconds = max(0.0, float(batch_pause_seconds))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = FaceThumbnailWarmupState()
        # Fingerprint of the library at the last completed full sweep. When the
        # sweep created nothing and the fingerprint is unchanged, the worker
        # knows the cache is fully warm and stops re-scanning until faces are
        # added or removed.
        self._last_completed_signature: Optional[tuple[int, int]] = None
        self._fully_warm = False

    def start(self) -> None:
        """Start the daemon worker if it is not already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.set()
            self._state.status = "idle"
            self._state.started_at = _utc_now()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="face-thumbnail-warmup",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the daemon worker and wait briefly for shutdown."""
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        with self._lock:
            self._state.status = "stopped"
            self._thread = None

    def wake(self) -> None:
        """Wake the worker so it can re-check idle state soon."""
        self._wake_event.set()

    def snapshot(self) -> dict:
        """Return current warmup state for diagnostics."""
        with self._lock:
            return {"task": asdict(self._state)}

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
            self._state.status = status
            self._state.last_error = last_error
        self._notify_change()

    def _record_batch(self, result) -> None:
        with self._lock:
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
        # Thumbnails created during the current forward sweep. When a sweep
        # finishes having created nothing, the cache is fully warm.
        cycle_created = 0
        while not self._stop_event.is_set():
            try:
                if not self._is_idle():
                    self._set_status("paused")
                    self._wait(self._busy_poll_seconds)
                    continue

                signature = get_face_library_signature()
                if self._fully_warm and signature == self._last_completed_signature:
                    # Nothing changed since the last full sweep — stay asleep and
                    # only re-check the cheap fingerprint, no per-face stat storm.
                    self._set_status("idle")
                    self._wait(self._idle_poll_seconds)
                    continue

                if self._next_face_id() == 0:
                    cycle_created = 0
                self._begin_cycle_if_needed()
                self._set_status("running")
                result = warm_missing_face_thumbnails(
                    after_face_id=self._next_face_id(),
                    max_created=self._batch_size,
                    scan_limit=self._scan_limit,
                    stop_event=self._stop_event,
                )
                cycle_created += result.created_thumbnails
                self._record_batch(result)

                if result.reached_end:
                    self._last_completed_signature = signature
                    self._fully_warm = cycle_created == 0
                    cycle_created = 0
                    with self._lock:
                        self._state.cycle_scanned_faces = 0
                        self._state.eta_seconds = None
                        self._state.cache_complete = self._fully_warm
                    self._set_status("idle")
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
