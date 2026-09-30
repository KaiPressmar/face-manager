"""Debounce assignment changes and recluster once the backend is idle."""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger("face_manager.idle_recluster")


class IdleReclusterScheduler:
    """Coalesce cluster mutations into one low-priority reclustering run."""

    def __init__(
        self,
        is_idle: Callable[[], bool],
        schedule: Callable[[str], object],
        *,
        debounce_seconds: float = 5.0,
        retry_seconds: float = 5.0,
    ) -> None:
        self._is_idle = is_idle
        self._schedule = schedule
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._retry_seconds = max(0.01, retry_seconds)
        self._lock = threading.Lock()
        self._dirty = False
        self._last_reason = "cluster_assignment_change"
        self._timer: threading.Timer | None = None
        self._stopped = False

    def mark_dirty(self, reason: str) -> None:
        """Record a mutation and restart the quiet-period countdown."""
        with self._lock:
            if self._stopped:
                return
            self._dirty = True
            self._last_reason = reason or "cluster_assignment_change"
            self._arm_locked(self._debounce_seconds)

    def start(self) -> None:
        """Accept new work after a previous lifecycle shutdown."""
        with self._lock:
            self._stopped = False

    def check_now(self) -> bool:
        """Schedule pending work when idle; otherwise arrange another check."""
        with self._lock:
            current_timer = self._timer
            self._timer = None
            if (
                current_timer is not None
                and current_timer is not threading.current_thread()
            ):
                current_timer.cancel()
            if self._stopped or not self._dirty:
                return False
            if not self._is_idle():
                self._arm_locked(self._retry_seconds)
                return False
            reason = self._last_reason
            self._dirty = False

        try:
            scheduled = self._schedule(f"idle:{reason}")
            if scheduled is None:
                with self._lock:
                    if not self._stopped:
                        self._dirty = True
                        self._arm_locked(self._retry_seconds)
                return False
        except Exception:
            logger.exception("Could not schedule idle reclustering")
            with self._lock:
                if not self._stopped:
                    self._dirty = True
                    self._arm_locked(self._retry_seconds)
            return False
        return True

    def clear(self) -> None:
        """Discard pending automatic work, for example before a manual run."""
        with self._lock:
            self._dirty = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def stop(self) -> None:
        """Cancel timers and reject new work during application shutdown."""
        with self._lock:
            self._stopped = True
            self._dirty = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _arm_locked(self, delay: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        timer = threading.Timer(delay, self.check_now)
        timer.name = "idle-recluster-scheduler"
        timer.daemon = True
        self._timer = timer
        timer.start()
