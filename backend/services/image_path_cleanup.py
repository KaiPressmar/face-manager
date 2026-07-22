"""Low-priority validation and cleanup of unavailable image locations."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Callable

from ..db.schema import get_conn
from .cache import app_cache
from .face_thumbnails import delete_face_thumbnail
from .filesystem_paths import filesystem_path
from .storage import invalidate_image_query_cache

logger = logging.getLogger("face_manager.image_path_cleanup")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImagePathCleanup:
    """Validate paths off the request thread and prune missing locations in batches."""

    def __init__(
        self,
        is_idle: Callable[[], bool],
        on_change: Callable[[dict], None] | None = None,
        *,
        batch_size: int = 50,
        check_delay_seconds: float = 0.01,
        retry_seconds: float = 15.0,
    ) -> None:
        self._is_idle = is_idle
        self._on_change = on_change
        self._batch_size = max(1, int(batch_size))
        self._check_delay_seconds = max(0.0, check_delay_seconds)
        self._retry_seconds = max(0.1, retry_seconds)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._pending_reason: str | None = None
        self._state = self._empty_state()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "status": "idle",
            "reason": None,
            "scanned_paths": 0,
            "removed_paths": 0,
            "removed_images": 0,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    def start(self, reason: str = "manual") -> dict:
        """Start or return the single cleanup task."""
        with self._lock:
            if self._stop_event.is_set():
                return dict(self._state)
            if self._worker is not None and self._worker.is_alive():
                return dict(self._state)
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending_reason = None
            self._state = {
                **self._empty_state(),
                "status": "queued" if not self._is_idle() else "running",
                "reason": reason,
                "started_at": _utc_now(),
            }
            worker = threading.Thread(
                target=self._run,
                name="image-path-cleanup",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            snapshot = dict(self._state)
        self._notify(snapshot)
        return snapshot

    def schedule(self, reason: str, delay_seconds: float = 30.0) -> None:
        """Coalesce an automatic cleanup request and defer it until idle."""
        with self._lock:
            if self._stop_event.is_set():
                return
            self._pending_reason = reason
            if self._worker is not None and self._worker.is_alive():
                return
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(max(0.0, delay_seconds), self._start_scheduled)
            timer.name = "image-path-cleanup-scheduler"
            timer.daemon = True
            self._timer = timer
            timer.start()

    def resume(self) -> None:
        """Allow scheduling after an application lifecycle restart in tests."""
        self._stop_event.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5.0)

    def _start_scheduled(self) -> None:
        with self._lock:
            self._timer = None
            reason = self._pending_reason
            if not reason or self._stop_event.is_set():
                return
            if not self._is_idle():
                timer = threading.Timer(self._retry_seconds, self._start_scheduled)
                timer.name = "image-path-cleanup-scheduler"
                timer.daemon = True
                self._timer = timer
                timer.start()
                return
        self.start(reason)

    def _wait_until_idle(self) -> bool:
        while not self._is_idle():
            with self._lock:
                if self._state["status"] != "queued":
                    self._state["status"] = "queued"
                    snapshot = dict(self._state)
                else:
                    snapshot = None
            if snapshot:
                self._notify(snapshot)
            if self._stop_event.wait(0.25):
                return False
        with self._lock:
            self._state["status"] = "running"
            snapshot = dict(self._state)
        self._notify(snapshot)
        return True

    def _run(self) -> None:
        try:
            if not self._wait_until_idle():
                return
            last_id = 0
            while not self._stop_event.is_set():
                if not self._wait_until_idle():
                    return
                conn = get_conn()
                try:
                    rows = conn.execute(
                        """
                        SELECT id, path
                        FROM image_location
                        WHERE id > ?
                        ORDER BY id
                        LIMIT ?
                        """,
                        (last_id, self._batch_size),
                    ).fetchall()
                finally:
                    conn.close()
                if not rows:
                    break
                last_id = int(rows[-1]["id"])
                missing_paths = []
                for row in rows:
                    if self._stop_event.is_set():
                        return
                    if not self._wait_until_idle():
                        return
                    if not os.path.isfile(filesystem_path(row["path"])):
                        missing_paths.append(row["path"])
                    with self._lock:
                        self._state["scanned_paths"] += 1
                    if self._check_delay_seconds:
                        self._stop_event.wait(self._check_delay_seconds)
                if missing_paths:
                    removed_paths, removed_images, face_ids = self._remove_missing(
                        missing_paths
                    )
                    for face_id in face_ids:
                        delete_face_thumbnail(face_id)
                    with self._lock:
                        self._state["removed_paths"] += removed_paths
                        self._state["removed_images"] += removed_images
                self._notify(self.snapshot())

            with self._lock:
                self._state["status"] = "completed"
                self._state["completed_at"] = _utc_now()
                snapshot = dict(self._state)
            if snapshot["removed_paths"]:
                invalidate_image_query_cache()
                app_cache.clear()
            self._notify(snapshot)
        except Exception as exc:
            logger.exception("Image path cleanup failed")
            with self._lock:
                self._state["status"] = "failed"
                self._state["error"] = str(exc)
                self._state["completed_at"] = _utc_now()
                snapshot = dict(self._state)
            self._notify(snapshot)
        finally:
            with self._lock:
                self._worker = None

    @staticmethod
    def _remove_missing(paths: list[str]) -> tuple[int, int, list[int]]:
        """Remove paths still missing and images left without any location."""
        conn = get_conn()
        try:
            cur = conn.cursor()
            affected_image_ids: set[int] = set()
            removed_paths = 0
            for path in paths:
                # A removable drive or network share may have returned meanwhile.
                if os.path.isfile(filesystem_path(path)):
                    continue
                row = cur.execute(
                    "SELECT image_id FROM image_location WHERE path = ?", (path,)
                ).fetchone()
                if row is None:
                    continue
                affected_image_ids.add(int(row["image_id"]))
                cur.execute("DELETE FROM image_location WHERE path = ?", (path,))
                removed_paths += cur.rowcount

            removed_images = 0
            removed_face_ids: list[int] = []
            for image_id in affected_image_ids:
                location = cur.execute(
                    """
                    SELECT path, directory, filename
                    FROM image_location
                    WHERE image_id = ?
                    ORDER BY path COLLATE NOCASE
                    LIMIT 1
                    """,
                    (image_id,),
                ).fetchone()
                if location is None:
                    removed_face_ids.extend(
                        row["id"]
                        for row in cur.execute(
                            "SELECT id FROM face WHERE image_id = ?", (image_id,)
                        ).fetchall()
                    )
                    cur.execute("DELETE FROM image WHERE id = ?", (image_id,))
                    removed_images += cur.rowcount
                else:
                    cur.execute(
                        """
                        UPDATE image SET path = ?, directory = ?, filename = ?
                        WHERE id = ?
                        """,
                        (
                            location["path"],
                            location["directory"],
                            location["filename"],
                            image_id,
                        ),
                    )
            if removed_images:
                cur.execute(
                    """
                    DELETE FROM cluster
                    WHERE NOT EXISTS (
                        SELECT 1 FROM face WHERE face.cluster_id = cluster.id
                    )
                    """
                )
            conn.commit()
            return removed_paths, removed_images, removed_face_ids
        finally:
            conn.close()

    def _notify(self, snapshot: dict) -> None:
        if self._on_change is not None:
            try:
                self._on_change(snapshot)
            except Exception:
                logger.exception("Could not publish image path cleanup state")
