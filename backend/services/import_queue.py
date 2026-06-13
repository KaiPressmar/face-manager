"""Single-worker import queue with observable and cancellable jobs."""

import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from ..db.schema import get_conn
from .pipeline import ImportCancelled, ImportProcessor


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        Current timezone-aware UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImportJob:
    """Represent one queued folder import.

    Args:
        id: Stable unique job identifier.
        folder_path: Folder requested by the client.
        status: Current queue or processing state.
        created_at: UTC timestamp when the job was queued.
        started_at: UTC timestamp when processing started.
        finished_at: UTC timestamp when processing stopped.
        total_images: Number of discovered images.
        processed_images: Number of completed or skipped images.
        total_faces: Number of faces discovered so far.
        processed_faces: Number of persisted faces.
        last_error: Most recent recoverable or terminal error.
    """

    id: str
    folder_path: str
    status: str = "queued"
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_images: int = 0
    processed_images: int = 0
    total_faces: int = 0
    processed_faces: int = 0
    last_error: Optional[str] = None

    def to_dict(self, queue_position: Optional[int] = None) -> dict:
        """Serialize the job for API clients.

        Args:
            queue_position: One-based queued position or ``None``.

        Returns:
            JSON-compatible job representation.
        """
        result = asdict(self)
        result["queue_position"] = queue_position
        return result


class ImportRunner(Protocol):
    """Define the processing interface required by :class:`ImportQueue`."""

    def process(
        self,
        folder_path: str,
        progress_callback: Callable[[dict], None],
        cancel_event: threading.Event,
    ) -> None:
        """Process one import job.

        Args:
            folder_path: Folder selected by the queued request.
            progress_callback: Callback receiving job progress updates.
            cancel_event: Cooperative cancellation signal.
        """


class ImportJobRepository:
    """Persist import jobs and their FIFO order in SQLite.

    Args:
        connection_factory: Callable returning a configured SQLite connection.
    """

    RECOVERABLE_STATUSES = {"queued", "running", "cancelling"}

    def __init__(self, connection_factory: Callable = get_conn):
        """Initialize the repository and ensure its table exists.

        Args:
            connection_factory: Callable returning a SQLite connection.
        """
        self._connection_factory = connection_factory
        self._ensure_schema()

    def load(self) -> tuple[list[ImportJob], list[str]]:
        """Load visible jobs and recover interrupted work.

        Jobs left in ``running`` or ``cancelling`` state by a process exit are
        changed back to ``queued``. Their progress counters are reset because
        the processor reconstructs progress by rescanning committed images.

        Returns:
            Ordered jobs and the ordered IDs that should resume processing.
        """
        connection = self._connection_factory()
        try:
            rows = connection.execute(
                """
                SELECT *
                FROM import_job
                ORDER BY queue_order
                """
            ).fetchall()
            jobs = []
            pending_ids = []
            for row in rows:
                job = self._row_to_job(row)
                if job.status in self.RECOVERABLE_STATUSES:
                    job.status = "queued"
                    job.started_at = None
                    job.finished_at = None
                    job.total_images = 0
                    job.processed_images = 0
                    job.total_faces = 0
                    job.processed_faces = 0
                    job.last_error = None
                    self._save(connection, job, row["queue_order"])
                    pending_ids.append(job.id)
                jobs.append(job)
            connection.commit()
            return jobs, pending_ids
        finally:
            connection.close()

    def insert(self, job: ImportJob) -> None:
        """Insert a newly queued job at the end of durable FIFO order.

        Args:
            job: New job to persist.
        """
        connection = self._connection_factory()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM import_job"
            ).fetchone()
            self._save(connection, job, int(row[0]))
            connection.commit()
        finally:
            connection.close()

    def update(self, job: ImportJob) -> None:
        """Persist mutable job state without changing FIFO order.

        Args:
            job: Existing job with updated state or progress.
        """
        connection = self._connection_factory()
        try:
            connection.execute(
                """
                UPDATE import_job
                SET status = ?,
                    started_at = ?,
                    finished_at = ?,
                    total_images = ?,
                    processed_images = ?,
                    total_faces = ?,
                    processed_faces = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    job.started_at,
                    job.finished_at,
                    job.total_images,
                    job.processed_images,
                    job.total_faces,
                    job.processed_faces,
                    job.last_error,
                    job.id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def delete(self, job_id: str) -> None:
        """Delete a job permanently.

        Args:
            job_id: Job identifier to remove.
        """
        connection = self._connection_factory()
        try:
            connection.execute("DELETE FROM import_job WHERE id = ?", (job_id,))
            connection.commit()
        finally:
            connection.close()

    def trim_terminal_history(self, history_limit: int) -> set[str]:
        """Delete terminal jobs beyond the configured retention limit.

        Args:
            history_limit: Maximum terminal jobs to retain.

        Returns:
            Identifiers deleted from persistent storage.
        """
        connection = self._connection_factory()
        try:
            rows = connection.execute(
                """
                SELECT id
                FROM import_job
                WHERE status IN ('completed', 'failed', 'cancelled')
                ORDER BY queue_order DESC
                LIMIT -1 OFFSET ?
                """,
                (max(0, history_limit),),
            ).fetchall()
            deleted_ids = {row["id"] for row in rows}
            if deleted_ids:
                placeholders = ",".join("?" for _ in deleted_ids)
                connection.execute(
                    f"DELETE FROM import_job WHERE id IN ({placeholders})",
                    tuple(deleted_ids),
                )
                connection.commit()
            return deleted_ids
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        """Create the import job table for standalone queue construction."""
        connection = self._connection_factory()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS import_job (
                    id TEXT PRIMARY KEY,
                    folder_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    total_images INTEGER NOT NULL DEFAULT 0,
                    processed_images INTEGER NOT NULL DEFAULT 0,
                    total_faces INTEGER NOT NULL DEFAULT 0,
                    processed_faces INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    queue_order INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_import_job_queue_order
                ON import_job(queue_order);
                """
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _save(connection, job: ImportJob, queue_order: int) -> None:
        """Insert or replace a complete job row.

        Args:
            connection: Open SQLite connection.
            job: Job state to persist.
            queue_order: Stable FIFO ordering value.
        """
        connection.execute(
            """
            INSERT INTO import_job(
                id, folder_path, status, created_at, started_at, finished_at,
                total_images, processed_images, total_faces, processed_faces,
                last_error, queue_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                folder_path = excluded.folder_path,
                status = excluded.status,
                created_at = excluded.created_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                total_images = excluded.total_images,
                processed_images = excluded.processed_images,
                total_faces = excluded.total_faces,
                processed_faces = excluded.processed_faces,
                last_error = excluded.last_error,
                queue_order = excluded.queue_order
            """,
            (
                job.id,
                job.folder_path,
                job.status,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.total_images,
                job.processed_images,
                job.total_faces,
                job.processed_faces,
                job.last_error,
                queue_order,
            ),
        )

    @staticmethod
    def _row_to_job(row) -> ImportJob:
        """Convert a SQLite row to an import job.

        Args:
            row: SQLite row containing import job columns.

        Returns:
            Hydrated import job.
        """
        return ImportJob(
            id=row["id"],
            folder_path=row["folder_path"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            total_images=row["total_images"],
            processed_images=row["processed_images"],
            total_faces=row["total_faces"],
            processed_faces=row["processed_faces"],
            last_error=row["last_error"],
        )


class ImportQueue:
    """Serialize import requests through one background worker.

    Args:
        processor: Processor used to execute one folder import.
        auto_start: Whether to start the daemon worker immediately.
        history_limit: Maximum number of terminal jobs retained.
    """

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

    def __init__(
        self,
        processor: Optional[ImportRunner] = None,
        repository: Optional[ImportJobRepository] = None,
        auto_start: bool = True,
        history_limit: int = 50,
    ):
        """Initialize queue state and optionally start its worker.

        Args:
            processor: Import processor invoked by the worker.
            repository: Durable job repository.
            auto_start: Whether to start processing immediately.
            history_limit: Maximum retained terminal jobs.
        """
        self._processor = processor or ImportProcessor()
        self._repository = repository or ImportJobRepository()
        self._history_limit = history_limit
        self._jobs: OrderedDict[str, ImportJob] = OrderedDict()
        self._pending = deque()
        self._condition = threading.Condition()
        self._active_job_id: Optional[str] = None
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_progress_persisted: dict[str, float] = {}
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        jobs, pending_ids = self._repository.load()
        self._jobs.update((job.id, job) for job in jobs)
        self._pending.extend(pending_ids)
        for job_id in pending_ids:
            self._cancel_events[job_id] = threading.Event()
        if auto_start:
            self.start()

    def start(self) -> None:
        """Start the single daemon worker if it is not running."""
        with self._condition:
            if self._worker and self._worker.is_alive():
                return
            self._stopping = False
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="face-import-queue",
                daemon=True,
            )
            self._worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Request worker shutdown without cancelling durable work.

        Args:
            timeout: Maximum seconds to wait for worker termination.
        """
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            worker = self._worker
        if worker:
            worker.join(timeout)

    def enqueue(self, folder_path: str) -> dict:
        """Append a folder import request.

        Args:
            folder_path: Existing folder to import.

        Returns:
            Serialized queued job.
        """
        job = ImportJob(
            id=uuid.uuid4().hex,
            folder_path=folder_path,
            created_at=_utc_now(),
        )
        with self._condition:
            self._repository.insert(job)
            self._jobs[job.id] = job
            self._pending.append(job.id)
            self._cancel_events[job.id] = threading.Event()
            position = len(self._pending)
            self._condition.notify()
        return job.to_dict(position)

    def snapshot(self) -> dict:
        """Return current, queued, and retained terminal jobs.

        Returns:
            Queue summary suitable for the imports API.
        """
        with self._condition:
            positions = {
                job_id: index
                for index, job_id in enumerate(self._pending, start=1)
            }
            jobs = [
                job.to_dict(positions.get(job.id))
                for job in self._jobs.values()
            ]
            return {
                "jobs": jobs,
                "active_job_id": self._active_job_id,
                "queued_count": len(self._pending),
            }

    def cancel_or_remove(self, job_id: str) -> Optional[dict]:
        """Cancel a running job or remove any other known job.

        Args:
            job_id: Identifier returned when the job was queued.

        Returns:
            Operation result, or ``None`` if the job does not exist.
        """
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            if job_id == self._active_job_id:
                job.status = "cancelling"
                self._repository.update(job)
                self._cancel_events[job_id].set()
                return {"id": job_id, "status": "cancelling"}

            if job_id in self._pending:
                self._pending.remove(job_id)
            self._repository.delete(job_id)
            del self._jobs[job_id]
            self._cancel_events.pop(job_id, None)
            return {"id": job_id, "status": "removed"}

    def _worker_loop(self) -> None:
        """Wait for queued jobs and process them serially."""
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                job_id = self._pending.popleft()
                job = self._jobs[job_id]
                job.status = "running"
                job.started_at = _utc_now()
                self._repository.update(job)
                self._last_progress_persisted[job_id] = time.monotonic()
                self._active_job_id = job_id
                cancel_event = self._cancel_events[job_id]

            try:
                self._processor.process(
                    job.folder_path,
                    self._progress_callback(job_id),
                    cancel_event,
                )
            except ImportCancelled:
                final_status = "cancelled"
            except Exception as exc:
                final_status = "failed"
                with self._condition:
                    job.last_error = str(exc)
            else:
                final_status = "cancelled" if cancel_event.is_set() else "completed"

            with self._condition:
                job.status = final_status
                job.finished_at = _utc_now()
                self._repository.update(job)
                self._active_job_id = None
                self._cancel_events.pop(job_id, None)
                self._last_progress_persisted.pop(job_id, None)
                self._trim_history()

    def _progress_callback(self, job_id: str) -> Callable[[dict], None]:
        """Create a synchronized progress callback for one job.

        Args:
            job_id: Job receiving progress updates.

        Returns:
            Callback accepted by :class:`ImportProcessor`.
        """
        def update(changes: dict) -> None:
            """Apply one progress update.

            Args:
                changes: Absolute fields or supported increment fields.
            """
            with self._condition:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                update_values = dict(changes)
                job.total_faces += update_values.pop(
                    "total_faces_increment", 0
                )
                job.processed_faces += update_values.pop(
                    "processed_faces_increment", 0
                )
                for key, value in update_values.items():
                    if hasattr(job, key):
                        setattr(job, key, value)
                now = time.monotonic()
                last_persisted = self._last_progress_persisted.get(
                    job_id, 0.0
                )
                import_finished = (
                    job.total_images > 0
                    and job.processed_images >= job.total_images
                )
                if (
                    import_finished
                    or job.last_error
                    or now - last_persisted >= 0.5
                ):
                    self._repository.update(job)
                    self._last_progress_persisted[job_id] = now

        return update

    def _trim_history(self) -> None:
        """Drop oldest terminal jobs beyond the configured history limit."""
        deleted_ids = self._repository.trim_terminal_history(
            self._history_limit
        )
        for job_id in deleted_ids:
            self._jobs.pop(job_id, None)
