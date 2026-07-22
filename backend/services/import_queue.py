"""Adaptive import queue with request-level cancellation and staged ETA."""

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from ..models.face_model import get_compute_mode
from ..db.schema import get_conn
from ..error_logging import configure_error_logging
from .pipeline import ImportCancelled, ImportProcessor, configure_processing_slots
from .task_control import BackgroundTaskControl

configure_error_logging()
logger = logging.getLogger("face_manager.import_queue")


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        Current timezone-aware UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(started_at: Optional[str], finished_at: Optional[str] = None):
    """Return elapsed seconds between ISO timestamps or now."""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = (
            datetime.fromisoformat(finished_at)
            if finished_at
            else datetime.now(timezone.utc)
        )
    except ValueError:
        return None
    return max(0.0, (end - start).total_seconds())


def get_import_job_concurrency(
    compute_mode: str,
    cpu_count: Optional[int] = None,
) -> int:
    """Select how many import requests may run concurrently."""
    configured = os.getenv("FACE_MANAGER_IMPORT_CONCURRENCY")
    if configured is not None:
        try:
            return max(1, int(configured))
        except ValueError:
            pass

    available_cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if compute_mode == "gpu":
        return min(2, max(1, available_cpus // 4))
    return min(3, max(1, available_cpus // 3))


def get_processing_stage_concurrency(
    compute_mode: str,
    cpu_count: Optional[int] = None,
) -> int:
    """Select concurrent processing-stage slots across all jobs."""
    configured = os.getenv("FACE_MANAGER_IMPORT_PROCESSING_SLOTS")
    if configured is not None:
        try:
            return max(1, int(configured))
        except ValueError:
            pass

    available_cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if compute_mode == "gpu":
        return 1
    return min(2, max(1, available_cpus // 6))


@dataclass
class StageTimingStats:
    """Accumulate moving-average timing data for one stage."""

    samples: int = 0
    avg_seconds_per_unit: Optional[float] = None
    avg_seconds_fixed: Optional[float] = None

    def update(self, duration_seconds: float, units: Optional[int] = None) -> None:
        self.samples += 1
        alpha = 0.25
        if units is not None and units > 0:
            value = duration_seconds / units
            if self.avg_seconds_per_unit is None:
                self.avg_seconds_per_unit = value
            else:
                self.avg_seconds_per_unit = (
                    self.avg_seconds_per_unit * (1 - alpha) + value * alpha
                )
            return

        if self.avg_seconds_fixed is None:
            self.avg_seconds_fixed = duration_seconds
        else:
            self.avg_seconds_fixed = (
                self.avg_seconds_fixed * (1 - alpha) + duration_seconds * alpha
            )


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
    hashed_images: int = 0
    processed_images: int = 0
    total_faces: int = 0
    processed_faces: int = 0
    stage: Optional[str] = None
    stage_started_at: Optional[str] = None
    stage_current: int = 0
    stage_total: int = 0
    current_file: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(
        self,
        queue_position: Optional[int] = None,
        eta_seconds: Optional[float] = None,
    ) -> dict:
        """Serialize the job for API clients.

        Args:
            queue_position: One-based queued position or ``None``.

        Returns:
            JSON-compatible job representation.
        """
        result = asdict(self)
        result["queue_position"] = queue_position
        result["elapsed_seconds"] = _duration_seconds(self.started_at, self.finished_at)
        result["eta_seconds"] = (
            max(0, round(eta_seconds)) if eta_seconds is not None else None
        )
        return result


@dataclass
class ImportStation:
    """Summarize one pipeline station for UI consumers."""

    job_id: str
    key: str
    label: str
    state: str
    progress_current: int
    progress_total: int
    eta_seconds: Optional[float] = None
    current_file: Optional[str] = None
    detail: Optional[str] = None


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
                    job.hashed_images = 0
                    job.processed_images = 0
                    job.total_faces = 0
                    job.processed_faces = 0
                    job.stage = None
                    job.stage_started_at = None
                    job.stage_current = 0
                    job.stage_total = 0
                    job.current_file = None
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
                    hashed_images = ?,
                    processed_images = ?,
                    total_faces = ?,
                    processed_faces = ?,
                    stage = ?,
                    stage_started_at = ?,
                    stage_current = ?,
                    stage_total = ?,
                    current_file = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    job.started_at,
                    job.finished_at,
                    job.total_images,
                    job.hashed_images,
                    job.processed_images,
                    job.total_faces,
                    job.processed_faces,
                    job.stage,
                    job.stage_started_at,
                    job.stage_current,
                    job.stage_total,
                    job.current_file,
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

    def delete_terminal_history(self) -> set[str]:
        """Delete every completed, failed, or cancelled job permanently."""
        connection = self._connection_factory()
        try:
            rows = connection.execute(
                """
                SELECT id
                FROM import_job
                WHERE status IN ('completed', 'failed', 'cancelled')
                """
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
                    hashed_images INTEGER NOT NULL DEFAULT 0,
                    processed_images INTEGER NOT NULL DEFAULT 0,
                    total_faces INTEGER NOT NULL DEFAULT 0,
                    processed_faces INTEGER NOT NULL DEFAULT 0,
                    stage TEXT,
                    stage_started_at TEXT,
                    stage_current INTEGER NOT NULL DEFAULT 0,
                    stage_total INTEGER NOT NULL DEFAULT 0,
                    current_file TEXT,
                    last_error TEXT,
                    queue_order INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_import_job_queue_order
                ON import_job(queue_order);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(import_job)"
                ).fetchall()
            }
            additions = {
                "stage": "TEXT",
                "stage_started_at": "TEXT",
                "stage_current": "INTEGER NOT NULL DEFAULT 0",
                "stage_total": "INTEGER NOT NULL DEFAULT 0",
                "current_file": "TEXT",
                "hashed_images": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in additions.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE import_job ADD COLUMN {column} {definition}"
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
                hashed_images,
                stage, stage_started_at, stage_current, stage_total,
                current_file, last_error, queue_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                folder_path = excluded.folder_path,
                status = excluded.status,
                created_at = excluded.created_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                total_images = excluded.total_images,
                hashed_images = excluded.hashed_images,
                processed_images = excluded.processed_images,
                total_faces = excluded.total_faces,
                processed_faces = excluded.processed_faces,
                stage = excluded.stage,
                stage_started_at = excluded.stage_started_at,
                stage_current = excluded.stage_current,
                stage_total = excluded.stage_total,
                current_file = excluded.current_file,
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
                job.hashed_images,
                job.stage,
                job.stage_started_at,
                job.stage_current,
                job.stage_total,
                job.current_file,
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
            hashed_images=row["hashed_images"],
            processed_images=row["processed_images"],
            total_faces=row["total_faces"],
            processed_faces=row["processed_faces"],
            stage=row["stage"],
            stage_started_at=row["stage_started_at"],
            stage_current=row["stage_current"],
            stage_total=row["stage_total"],
            current_file=row["current_file"],
            last_error=row["last_error"],
        )


class ImportQueue:
    """Run queued imports with adaptive request-level parallelism.

    Args:
        processor: Processor used to execute one folder import.
        auto_start: Whether to start the daemon worker immediately.
        history_limit: Maximum number of terminal jobs retained.
    """

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
    STAGE_ORDER = [
        "scanning",
        "hashing",
        "loading_model",
        "loading_index",
        "processing",
        "finalizing",
    ]
    STAGE_LABELS = {
        "scanning": "Scan",
        "hashing": "Hash",
        "loading_model": "Model",
        "loading_index": "Index",
        "processing": "Recognize",
        "finalizing": "Finalize",
    }
    STAGE_DEFAULT_UNIT_SECONDS = {
        "scanning": 0.002,
        "hashing": 0.010,
        "processing": 0.070,
    }
    STAGE_DEFAULT_FIXED_SECONDS = {
        "loading_model": 1.2,
        "loading_index": 0.7,
        "finalizing": 0.4,
    }

    def __init__(
        self,
        processor: Optional[ImportRunner] = None,
        repository: Optional[ImportJobRepository] = None,
        auto_start: bool = True,
        history_limit: int = 50,
        max_concurrent_jobs: Optional[int] = None,
        processing_slots: Optional[int] = None,
        on_change: Optional[Callable[[], None]] = None,
        on_before_terminal: Optional[Callable[[], None]] = None,
        on_after_terminal: Optional[Callable[[], None]] = None,
    ):
        """Initialize queue state and optionally start its worker.

        Args:
            processor: Import processor invoked by the worker.
            repository: Durable job repository.
            auto_start: Whether to start processing immediately.
            history_limit: Maximum retained terminal jobs.
            max_concurrent_jobs: Optional import request concurrency override.
            processing_slots: Optional processing-stage slot override.
            on_change: Optional callback invoked after any state transition so
                subscribers (e.g. the SSE event hub) can push fresh snapshots.
            on_before_terminal: Enables an external write guard before a job
                stops being visible as active.
            on_after_terminal: Releases that guard after terminal state and
                notifications have been published.
        """
        self._on_change = on_change
        self._on_before_terminal = on_before_terminal
        self._on_after_terminal = on_after_terminal
        self._processor = processor or ImportProcessor()
        self._repository = repository or ImportJobRepository()
        self._history_limit = history_limit
        compute_mode = get_compute_mode()
        default_job_concurrency = (
            get_import_job_concurrency(compute_mode)
            if isinstance(self._processor, ImportProcessor)
            else 1
        )
        self._max_concurrent_jobs = max_concurrent_jobs or default_job_concurrency
        self._processing_slots = processing_slots or get_processing_stage_concurrency(
            compute_mode
        )
        configure_processing_slots(self._processing_slots)

        self._jobs: OrderedDict[str, ImportJob] = OrderedDict()
        self._pending = deque()
        self._condition = threading.Condition()
        self._active_job_ids: set[str] = set()
        self._cancel_events: dict[str, BackgroundTaskControl] = {}
        self._last_progress_persisted: dict[str, float] = {}
        self._worker: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: dict[str, Future] = {}
        self._stage_stats: dict[str, StageTimingStats] = {
            stage: StageTimingStats() for stage in self.STAGE_ORDER
        }
        self._avg_images_per_completed_job: Optional[float] = None
        self._stopping = False
        jobs, pending_ids = self._repository.load()
        self._jobs.update((job.id, job) for job in jobs)
        self._pending.extend(pending_ids)
        for job in jobs:
            if job.status not in self.TERMINAL_STATUSES:
                self._cancel_events[job.id] = BackgroundTaskControl()
        if auto_start:
            self.start()

    def start(self) -> None:
        """Start the queue dispatcher if it is not running."""
        with self._condition:
            if self._worker and self._worker.is_alive():
                return
            self._stopping = False
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_concurrent_jobs,
                thread_name_prefix="face-import-job",
            )
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="face-import-queue",
                daemon=True,
            )
            self._worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Request dispatcher shutdown without cancelling active jobs.

        Args:
            timeout: Maximum seconds to wait for worker termination.
        """
        with self._condition:
            self._stopping = True
            for job_id in self._active_job_ids:
                self._cancel_events[job_id].set()
            self._condition.notify_all()
            worker = self._worker
        if worker:
            worker.join(timeout)
        with self._condition:
            executor = self._executor
            self._executor = None
            futures = tuple(self._futures.values())
        if futures:
            wait(futures, timeout=timeout)
        if executor:
            executor.shutdown(wait=True, cancel_futures=False)

    def _notify_change(self) -> None:
        """Notify the change subscriber, isolating it from worker failures."""
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # pragma: no cover - subscriber must never break work
            logger.exception("Import queue change notification failed")

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
            self._cancel_events[job.id] = BackgroundTaskControl()
            position = len(self._pending)
            self._condition.notify()
        self._notify_change()
        return job.to_dict(position)

    def snapshot(self) -> dict:
        """Return current, queued, and retained terminal jobs.

        Returns:
            Queue summary suitable for the imports API.
        """
        with self._condition:
            positions = {
                job_id: index for index, job_id in enumerate(self._pending, start=1)
            }

            active_ids = sorted(
                self._active_job_ids,
                key=lambda job_id: self._jobs[job_id].started_at or "",
            )
            job_etas = self._estimate_job_etas(active_ids)
            jobs = [
                self._serialize_job(
                    job,
                    positions.get(job.id),
                    job_etas.get(job.id),
                )
                for job in self._jobs.values()
            ]
            known_etas = [eta for eta in job_etas.values() if eta is not None]
            overall_eta = max(known_etas) if known_etas else None
            return {
                "jobs": jobs,
                "active_job_id": active_ids[0] if active_ids else None,
                "active_job_ids": active_ids,
                "running_count": len(active_ids),
                "queued_count": len(self._pending),
                "paused_count": sum(
                    1 for job in self._jobs.values() if job.status == "paused"
                ),
                "max_concurrent_jobs": self._max_concurrent_jobs,
                "overall_eta_seconds": (
                    round(overall_eta) if overall_eta is not None else None
                ),
            }

    def _serialize_job(
        self,
        job: ImportJob,
        queue_position: Optional[int],
        eta_seconds: Optional[float],
    ) -> dict:
        """Serialize one job together with its station timeline."""
        payload = job.to_dict(queue_position, eta_seconds)
        station_etas = self._estimate_station_etas(job)
        payload["stations"] = [
            asdict(station)
            for station in self._build_station_timeline(job, station_etas)
        ]
        return payload

    def _build_station_timeline(
        self,
        job: ImportJob,
        station_etas: dict[str, Optional[float]],
    ) -> list[ImportStation]:
        """Build a linear station timeline for the import pipeline."""
        total_images = max(job.total_images, 1)
        hashed_images = min(job.hashed_images, total_images)
        processed_images = min(job.processed_images, total_images)

        if job.status == "completed":
            scanning_state = "done"
            hashing_state = "done"
            loading_model_state = "done"
            loading_index_state = "done"
            processing_state = "done"
            finalizing_state = "done"
        elif job.status in {"failed", "cancelled"}:
            stage = job.stage
            scanning_state = "done" if stage in self.STAGE_ORDER[1:] else job.status
            hashing_state = (
                "done"
                if hashed_images >= total_images
                else (job.status if stage in {"hashing", "processing"} else "queued")
            )
            loading_model_state = (
                job.status if stage == "loading_model" else "done" if stage in {"loading_index", "processing", "finalizing"} else "queued"
            )
            loading_index_state = (
                job.status if stage == "loading_index" else "done" if stage in {"processing", "finalizing"} else "queued"
            )
            processing_state = (
                job.status if stage == "processing" else "done" if processed_images >= total_images else "queued"
            )
            finalizing_state = job.status if stage == "finalizing" else "queued"
        else:
            if job.status == "queued":
                scanning_state = "queued"
                hashing_state = "queued"
                loading_model_state = "queued"
                loading_index_state = "queued"
                processing_state = "queued"
                finalizing_state = "queued"
            else:
                scanning_state = "active" if job.stage == "scanning" else "done"
                hashing_state = "active" if hashed_images < total_images else "done"
                loading_model_state = (
                    "active"
                    if job.stage == "loading_model"
                    else (
                        "done"
                        if job.stage in {"loading_index", "processing", "finalizing"}
                        else "queued"
                    )
                )
                loading_index_state = (
                    "active"
                    if job.stage == "loading_index"
                    else (
                        "done"
                        if job.stage in {"processing", "finalizing"}
                        else "queued"
                    )
                )
                processing_state = "active" if processed_images < total_images and job.stage in {"processing", "finalizing"} else (
                    "done" if processed_images >= total_images else "queued"
                )
                finalizing_state = "active" if job.stage == "finalizing" else "queued"

        stations = [
            ImportStation(
                job_id=job.id,
                key="scanning",
                label=self.STAGE_LABELS["scanning"],
                state=scanning_state,
                progress_current=(
                    job.stage_current if job.stage == "scanning" else int(job.total_images > 0)
                ),
                progress_total=(
                    max(job.stage_total, 1) if job.stage == "scanning" else 1
                ),
                eta_seconds=station_etas.get("scanning"),
                current_file=job.current_file if job.stage == "scanning" else None,
                detail=(
                    f"{job.stage_current} gefunden" if job.stage == "scanning" else None
                ),
            ),
            ImportStation(
                job_id=job.id,
                key="hashing",
                label=self.STAGE_LABELS["hashing"],
                state=hashing_state,
                progress_current=hashed_images,
                progress_total=max(job.stage_total or job.total_images, 1),
                eta_seconds=station_etas.get("hashing"),
                current_file=job.current_file if hashing_state == "active" else None,
                detail=(
                    f"{hashed_images} / {total_images}"
                ),
            ),
            ImportStation(
                job_id=job.id,
                key="loading_model",
                label=self.STAGE_LABELS["loading_model"],
                state=loading_model_state,
                progress_current=(
                    1 if loading_model_state == "done" else int(job.stage == "loading_model")
                ),
                progress_total=1,
                eta_seconds=station_etas.get("loading_model"),
                detail="GPU/CPU model warmup",
            ),
            ImportStation(
                job_id=job.id,
                key="loading_index",
                label=self.STAGE_LABELS["loading_index"],
                state=loading_index_state,
                progress_current=(
                    1 if loading_index_state == "done" else int(job.stage == "loading_index")
                ),
                progress_total=1,
                eta_seconds=station_etas.get("loading_index"),
                detail="Embedding index sync",
            ),
            ImportStation(
                job_id=job.id,
                key="processing",
                label=self.STAGE_LABELS["processing"],
                state=processing_state,
                progress_current=processed_images,
                progress_total=total_images,
                eta_seconds=station_etas.get("processing"),
                current_file=job.current_file if processing_state == "active" else None,
                detail=(
                    f"{processed_images} / {total_images}"
                ),
            ),
            ImportStation(
                job_id=job.id,
                key="finalizing",
                label=self.STAGE_LABELS["finalizing"],
                state=finalizing_state,
                progress_current=(
                    1 if finalizing_state == "done" else int(job.stage == "finalizing")
                ),
                progress_total=1,
                eta_seconds=station_etas.get("finalizing"),
                detail="Persisting metadata",
            ),
        ]
        return stations

    def _estimate_job_etas(self, active_ids: list[str]) -> dict[str, Optional[float]]:
        """Estimate request-level ETA in a bounded parallel scheduler."""
        if any(self._jobs[job_id].status == "paused" for job_id in active_ids):
            return {
                job_id: None
                for job_id in [*active_ids, *self._pending]
            }
        avg_images = self._average_images_per_job()
        job_etas: dict[str, Optional[float]] = {}

        slot_times = [0.0 for _ in range(self._max_concurrent_jobs)]
        active_remaining = {
            job_id: self._estimate_job_remaining_seconds(self._jobs[job_id], avg_images)
            for job_id in active_ids
        }
        for index, job_id in enumerate(active_ids):
            eta = active_remaining[job_id]
            job_etas[job_id] = eta
            slot_index = min(index, len(slot_times) - 1)
            if eta is not None:
                slot_times[slot_index] = eta

        for job_id in self._pending:
            duration = self._estimate_job_total_seconds(self._jobs[job_id], avg_images)
            slot_index = min(range(len(slot_times)), key=lambda i: slot_times[i])
            slot_times[slot_index] += duration
            job_etas[job_id] = slot_times[slot_index]
        return job_etas

    def _average_images_per_job(self) -> int:
        if self._avg_images_per_completed_job is not None:
            return max(1, round(self._avg_images_per_completed_job))
        completed_totals = [
            job.total_images
            for job in self._jobs.values()
            if job.status == "completed" and job.total_images > 0
        ]
        if not completed_totals:
            return 150
        return max(1, round(sum(completed_totals) / len(completed_totals)))

    def _estimate_job_total_seconds(self, job: ImportJob, avg_images: int) -> float:
        image_count = job.total_images if job.total_images > 0 else avg_images
        total = 0.0
        for stage in self.STAGE_ORDER:
            total += self._estimate_stage_full_seconds(stage, image_count)
        return total

    def _estimate_job_remaining_seconds(self, job: ImportJob, avg_images: int) -> Optional[float]:
        if job.status in self.TERMINAL_STATUSES:
            return 0.0

        image_count = job.total_images if job.total_images > 0 else avg_images
        if job.stage not in self.STAGE_ORDER:
            return self._estimate_job_total_seconds(job, avg_images)

        current_index = self.STAGE_ORDER.index(job.stage)
        remaining = self._estimate_stage_remaining_seconds(job, image_count)
        for stage in self.STAGE_ORDER[current_index + 1 :]:
            remaining += self._estimate_stage_full_seconds(stage, image_count)
        return max(0.0, remaining)

    def _estimate_station_etas(self, job: ImportJob) -> dict[str, Optional[float]]:
        """Return ETA per stage as seconds from now until stage completion."""
        if job.status in self.TERMINAL_STATUSES:
            return {stage: 0.0 for stage in self.STAGE_ORDER}

        image_count = job.total_images if job.total_images > 0 else self._average_images_per_job()
        if job.stage not in self.STAGE_ORDER:
            cumulative = 0.0
            result: dict[str, Optional[float]] = {}
            for stage in self.STAGE_ORDER:
                cumulative += self._estimate_stage_full_seconds(stage, image_count)
                result[stage] = cumulative
            return result

        current_index = self.STAGE_ORDER.index(job.stage)
        result: dict[str, Optional[float]] = {}
        cumulative = self._estimate_stage_remaining_seconds(job, image_count)
        result[job.stage] = cumulative
        trailing = cumulative
        for stage in self.STAGE_ORDER[current_index + 1 :]:
            trailing += self._estimate_stage_full_seconds(stage, image_count)
            result[stage] = trailing
        for stage in self.STAGE_ORDER[:current_index]:
            result[stage] = 0.0
        return result

    def _estimate_stage_full_seconds(self, stage: str, image_count: int) -> float:
        stats = self._stage_stats[stage]
        if stage in self.STAGE_DEFAULT_UNIT_SECONDS:
            rate = stats.avg_seconds_per_unit or self.STAGE_DEFAULT_UNIT_SECONDS[stage]
            return rate * max(1, image_count)
        return stats.avg_seconds_fixed or self.STAGE_DEFAULT_FIXED_SECONDS[stage]

    def _estimate_stage_remaining_seconds(self, job: ImportJob, image_count: int) -> float:
        stage = job.stage
        if stage not in self.STAGE_ORDER:
            return self._estimate_job_total_seconds(job, image_count)

        elapsed = _duration_seconds(job.stage_started_at)
        if stage in self.STAGE_DEFAULT_UNIT_SECONDS:
            rate = self._stage_stats[stage].avg_seconds_per_unit or self.STAGE_DEFAULT_UNIT_SECONDS[stage]
            if stage == "scanning":
                total = max(job.total_images, image_count)
                current = min(job.stage_current, total)
            elif stage == "processing":
                total = max(job.total_images, image_count)
                current = min(job.processed_images, total)
            else:
                total = max(job.stage_total or job.total_images, image_count)
                current = min(job.stage_current, total)

            if total <= current:
                return 0.0

            if elapsed is not None and current > 0:
                live_rate = elapsed / current
                rate = rate * 0.6 + live_rate * 0.4
            return (total - current) * rate

        fixed = self._stage_stats[stage].avg_seconds_fixed or self.STAGE_DEFAULT_FIXED_SECONDS[stage]
        if elapsed is None:
            return fixed
        return max(0.0, fixed - elapsed)

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

            if job_id in self._active_job_ids:
                job.status = "cancelling"
                self._repository.update(job)
                self._cancel_events[job_id].set()
                self._notify_change()
                return {"id": job_id, "status": "cancelling"}

            if job_id in self._pending:
                self._pending.remove(job_id)
            self._repository.delete(job_id)
            del self._jobs[job_id]
            self._cancel_events.pop(job_id, None)
            self._notify_change()
            return {"id": job_id, "status": "removed"}

    def pause(self, job_id: str) -> Optional[dict]:
        """Pause a queued or running import at its next safe checkpoint."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status in self.TERMINAL_STATUSES or job.status == "cancelling":
                return None
            if job.status == "paused":
                return {"id": job_id, "status": "paused"}
            control = self._cancel_events[job_id]
            if job_id in self._active_job_ids:
                control.pause()
            elif job_id in self._pending:
                self._pending.remove(job_id)
            job.status = "paused"
            self._repository.update(job)
            self._condition.notify_all()
        self._notify_change()
        return {"id": job_id, "status": "paused"}

    def resume(self, job_id: str) -> Optional[dict]:
        """Resume a paused import without discarding committed progress."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status != "paused":
                return None
            control = self._cancel_events.setdefault(job_id, BackgroundTaskControl())
            if job_id in self._active_job_ids:
                control.resume()
                job.status = "running"
            else:
                job.status = "queued"
                ordered_ids = list(self._jobs)
                insert_at = len(self._pending)
                for index, pending_id in enumerate(self._pending):
                    if ordered_ids.index(job_id) < ordered_ids.index(pending_id):
                        insert_at = index
                        break
                self._pending.insert(insert_at, job_id)
            self._repository.update(job)
            self._condition.notify_all()
        self._notify_change()
        return {"id": job_id, "status": job.status}

    def cancel(self, job_id: str) -> Optional[dict]:
        """Cancel an import while retaining a terminal history entry."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status in self.TERMINAL_STATUSES:
                return None
            if job_id in self._active_job_ids:
                job.status = "cancelling"
                self._repository.update(job)
                self._cancel_events[job_id].set()
                result = {"id": job_id, "status": "cancelling"}
            else:
                if job_id in self._pending:
                    self._pending.remove(job_id)
                job.status = "cancelled"
                job.finished_at = _utc_now()
                job.current_file = None
                self._repository.update(job)
                self._cancel_events.pop(job_id, None)
                self._trim_history()
                result = {"id": job_id, "status": "cancelled"}
            self._condition.notify_all()
        self._notify_change()
        return result

    def delete_terminal(self, job_id: str) -> Optional[dict]:
        """Permanently delete one terminal import history entry."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status not in self.TERMINAL_STATUSES:
                return None
            self._repository.delete(job_id)
            del self._jobs[job_id]
        self._notify_change()
        return {"id": job_id, "status": "removed"}

    def clear_history(self) -> int:
        """Permanently delete every terminal import history entry."""
        with self._condition:
            deleted_ids = self._repository.delete_terminal_history()
            for job_id in deleted_ids:
                self._jobs.pop(job_id, None)
        if deleted_ids:
            self._notify_change()
        return len(deleted_ids)

    def _worker_loop(self) -> None:
        """Dispatch queued jobs while respecting concurrency limits."""
        while True:
            with self._condition:
                while (
                    not self._stopping
                    and (
                        not self._pending
                        or len(self._active_job_ids) >= self._max_concurrent_jobs
                    )
                ):
                    self._condition.wait()
                if self._stopping:
                    return

                job_id = self._pending.popleft()
                job = self._jobs[job_id]
                job.status = "running"
                job.started_at = _utc_now()
                job.stage = "scanning"
                job.stage_started_at = job.started_at
                job.stage_current = 0
                job.stage_total = 0
                job.hashed_images = 0
                self._repository.update(job)
                self._last_progress_persisted[job_id] = time.monotonic()
                self._active_job_ids.add(job_id)
                cancel_event = self._cancel_events[job_id]

                executor = self._executor
                if executor is None:
                    return
                future = executor.submit(
                    self._run_job,
                    job_id,
                    cancel_event,
                )
                self._futures[job_id] = future
                future.add_done_callback(
                    lambda done_future, active_job_id=job_id: self._on_job_done(
                        active_job_id,
                        done_future,
                    )
                )
            self._notify_change()

    def _run_job(self, job_id: str, cancel_event: threading.Event) -> tuple[str, Optional[str]]:
        """Execute one job and return terminal status with optional error."""
        job = self._jobs[job_id]
        try:
            self._processor.process(
                job.folder_path,
                self._progress_callback(job_id),
                cancel_event,
            )
        except ImportCancelled:
            return "cancelled", None
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Import job %s failed for %s", job_id, job.folder_path)
            return "failed", str(exc)
        return ("cancelled", None) if cancel_event.is_set() else ("completed", None)

    def _on_job_done(self, job_id: str, future: Future) -> None:
        """Persist final job state after worker completion."""
        try:
            final_status, error_message = future.result()
        except Exception as exc:  # pragma: no cover - callback safety
            logger.exception("Import job callback failed for %s", job_id)
            final_status, error_message = "failed", str(exc)
        if self._on_before_terminal is not None:
            self._on_before_terminal()
        try:
            with self._condition:
                job = self._jobs.get(job_id)
                if job is None:
                    return

                self._record_stage_stats(job)
                if error_message:
                    job.last_error = error_message
                job.status = final_status
                job.finished_at = _utc_now()
                if final_status == "completed":
                    job.stage = "completed"
                    if job.total_images > 0:
                        prior = self._avg_images_per_completed_job
                        if prior is None:
                            self._avg_images_per_completed_job = float(job.total_images)
                        else:
                            self._avg_images_per_completed_job = (
                                prior * 0.8 + job.total_images * 0.2
                            )
                job.current_file = None
                self._repository.update(job)
                self._active_job_ids.discard(job_id)
                self._cancel_events.pop(job_id, None)
                self._last_progress_persisted.pop(job_id, None)
                self._futures.pop(job_id, None)
                self._trim_history()
                self._condition.notify_all()
            self._notify_change()
        finally:
            if self._on_after_terminal is not None:
                self._on_after_terminal()

    def _record_stage_stats(self, job: ImportJob) -> None:
        """Capture observed stage timing for future ETA prediction."""
        if job.stage not in self._stage_stats:
            return
        elapsed = _duration_seconds(job.stage_started_at, job.finished_at)
        if elapsed is None:
            return
        units = self._stage_unit_count(job.stage, job)
        self._stage_stats[job.stage].update(elapsed, units)

    @staticmethod
    def _stage_unit_count(stage: str, job: ImportJob) -> Optional[int]:
        if stage == "scanning":
            return max(1, job.total_images)
        if stage == "hashing":
            return max(1, job.stage_total or job.total_images)
        if stage == "processing":
            return max(1, job.total_images)
        return None

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
                next_stage = update_values.get("stage")
                if next_stage and next_stage != job.stage:
                    self._record_stage_stats(job)
                    update_values.setdefault("stage_started_at", _utc_now())
                    update_values.setdefault("stage_current", 0)
                    update_values.setdefault("stage_total", 0)
                job.total_faces += update_values.pop("total_faces_increment", 0)
                job.processed_faces += update_values.pop("processed_faces_increment", 0)
                for key, value in update_values.items():
                    if hasattr(job, key):
                        setattr(job, key, value)
                now = time.monotonic()
                last_persisted = self._last_progress_persisted.get(job_id, 0.0)
                import_finished = (
                    job.total_images > 0 and job.processed_images >= job.total_images
                )
                if import_finished or job.last_error or now - last_persisted >= 0.5:
                    self._repository.update(job)
                    self._last_progress_persisted[job_id] = now
            self._notify_change()

        return update

    def _trim_history(self) -> None:
        """Drop oldest terminal jobs beyond the configured history limit."""
        deleted_ids = self._repository.trim_terminal_history(self._history_limit)
        for job_id in deleted_ids:
            self._jobs.pop(job_id, None)
