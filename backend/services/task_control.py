"""Cooperative pause and cancellation control for background workers."""

from __future__ import annotations

import threading


class BackgroundTaskControl:
    """Expose Event-compatible cancellation plus cooperative pause checkpoints."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._cancelled = False
        self._paused = False

    def set(self) -> None:
        """Cancel the task and wake every paused worker."""
        with self._condition:
            self._cancelled = True
            self._paused = False
            self._condition.notify_all()

    def is_set(self) -> bool:
        """Match ``threading.Event`` cancellation checks."""
        with self._condition:
            return self._cancelled

    def pause(self) -> None:
        """Hold the task at its next cooperative checkpoint."""
        with self._condition:
            if not self._cancelled:
                self._paused = True

    def resume(self) -> None:
        """Release a paused task."""
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def wait_if_paused(self) -> bool:
        """Wait until resumed or cancelled and return cancellation state."""
        with self._condition:
            while self._paused and not self._cancelled:
                self._condition.wait(timeout=0.25)
            return self._cancelled

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused
