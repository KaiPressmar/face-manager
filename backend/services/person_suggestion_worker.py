"""Poll for durable person suggestion work without delaying interactive work."""

from __future__ import annotations

import logging
import threading
from typing import Callable


logger = logging.getLogger("face_manager.person_suggestion_worker")


class PersonSuggestionWorker:
    """Coalesce wake requests and retry durable work when the backend is ready."""

    def __init__(
        self,
        is_ready: Callable[[], bool],
        refresh: Callable[[], bool],
        on_publish: Callable[[], None],
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self._is_ready = is_ready
        self._refresh = refresh
        self._on_publish = on_publish
        self._interval = max(0.01, interval_seconds)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start polling and check for work left by a previous process."""
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run, name="person-suggestion-worker", daemon=True
            )
            thread.start()
            self._thread = thread

    def wake(self) -> None:
        """Ask for an early check after the surrounding state changes."""
        self._wake.set()

    def stop(self) -> bool:
        """Stop polling and report whether there was a worker to stop."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return False
            self._stop.set()
            self._wake.set()
        if thread is not threading.current_thread():
            thread.join()
        return True

    def _run(self) -> None:
        publish_pending = False
        try:
            while True:
                self._wake.clear()
                if self._stop.is_set():
                    return
                try:
                    if self._is_ready() and not self._stop.is_set():
                        if self._refresh():
                            publish_pending = True
                        if publish_pending and not self._stop.is_set():
                            self._on_publish()
                            publish_pending = False
                except Exception:
                    logger.exception("Could not refresh person suggestions")
                self._wake.wait(self._interval)
        finally:
            with self._lock:
                self._thread = None
