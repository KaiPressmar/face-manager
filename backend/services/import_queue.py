"""Adaptive import queue with request-level cancellation and staged ETA."""

import json
import logging
import math
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from statistics import median
from typing import Callable, ContextManager, Optional, Protocol

from ..models.face_model import get_compute_mode
from ..db.schema import get_conn
from ..error_logging import configure_error_logging
from .pipeline import (
    IMPORT_ISSUE_MESSAGES,
    ImportCancelled,
    ImportProcessor,
    ImportSourceUnavailable,
    check_import_source,
    configure_processing_slots,
)
from .task_control import BackgroundTaskControl

configure_error_logging()
logger = logging.getLogger("face_manager.import_queue")
MAX_IMPORT_ISSUES = 40
WAITING_FOR_SOURCE = "waiting_for_source"


def _issue_entries(value, *, limit: int | None = MAX_IMPORT_ISSUES) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    if limit is not None:
        value = value[:limit]
    return [
        {"path": item["path"], "kind": item["kind"]}
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"]
        and "\0" not in item["path"]
        and isinstance(item.get("kind"), str)
        and item["kind"] in IMPORT_ISSUE_MESSAGES
    ]


def _load_json(value, default):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return default
    return result if isinstance(result, type(default)) else default


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
class LiveStageTiming:
    """Track recent unit throughput without letting one outlier dominate ETA."""

    stage: str
    last_progress: int
    last_sample_at: float
    samples: list[float] = field(default_factory=list)

    def observe(self, progress: int, observed_at: float) -> None:
        """Record elapsed time per newly completed unit."""
        if progress < self.last_progress:
            self.last_progress = progress
            self.last_sample_at = observed_at
            self.samples.clear()
            return

        completed = progress - self.last_progress
        elapsed = max(0.0, observed_at - self.last_sample_at)
        if completed <= 0:
            return

        sample = elapsed / completed
        if sample > 0:
            if self.samples:
                baseline = median(self.samples)
                sample = max(baseline * 0.4, min(sample, baseline * 2.5))
            self.samples.append(sample)
            # A wider median window keeps a short burst of cached or unusually
            # complex images from redefining the throughput of a large import.
            del self.samples[:-25]
        self.last_progress = progress
        self.last_sample_at = observed_at

    def rebase(self, progress: int, observed_at: float) -> None:
        """Exclude a pause from the next throughput interval."""
        self.last_progress = progress
        self.last_sample_at = observed_at

    @property
    def seconds_per_unit(self) -> Optional[float]:
        return median(self.samples) if self.samples else None

    def overdue_seconds(self, observed_at: float) -> float:
        """Return time spent beyond the typical duration of the current unit."""
        rate = self.seconds_per_unit
        if rate is None:
            return 0.0
        return max(0.0, observed_at - self.last_sample_at - rate)


@dataclass
class StableEta:
    """Ease a noisy raw ETA toward reality using elapsed wall-clock time."""

    seconds: float
    updated_at: float

    def update(self, target_seconds: float, observed_at: float) -> float:
        """Count down naturally and absorb corrections without visible jumps."""
        elapsed = max(0.0, observed_at - self.updated_at)
        countdown = max(0.0, self.seconds - elapsed)
        difference = target_seconds - countdown
        # Underestimates are corrected deliberately more slowly than genuine
        # speed-ups. Both paths are time-based, so frequent API snapshots cannot
        # accidentally amplify the adjustment rate.
        time_constant = 75.0 if difference > 0 else 20.0
        blend = 1.0 - math.exp(-elapsed / time_constant)
        self.seconds = max(0.0, countdown + difference * blend)
        self.updated_at = observed_at
        return self.seconds

    def rebase(self, observed_at: float) -> None:
        """Exclude a user-requested pause from the visible countdown."""
        self.updated_at = observed_at


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
        unavailable_images: Image files that could not be accessed.
        unreadable_images: Accessible files that could not be decoded as images.
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
    unavailable_images: int = 0
    unreadable_images: int = 0
    total_faces: int = 0
    processed_faces: int = 0
    stage: Optional[str] = None
    stage_started_at: Optional[str] = None
    stage_current: int = 0
    stage_total: int = 0
    current_file: Optional[str] = None
    last_error: Optional[str] = None
    issue_counts: dict[str, int] = field(default_factory=dict)
    issues: list[dict[str, str]] = field(default_factory=list)
    retry_paths: list[dict[str, str]] = field(default_factory=list, repr=False)

    def can_retry_issues(self) -> bool:
        return (
            self.status == "completed"
            and bool(self.issues)
            and len(self.issues) == sum(self.issue_counts.values())
        )

    def reset_progress(self) -> None:
        self.started_at = self.finished_at = self.stage_started_at = None
        self.stage = self.current_file = self.last_error = None
        self.total_images = self.hashed_images = self.processed_images = 0
        self.unavailable_images = self.unreadable_images = 0
        self.total_faces = self.processed_faces = 0
        self.stage_current = self.stage_total = 0
        self.issue_counts.clear()
        self.issues.clear()

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
        result = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "retry_paths"}
        result["issue_counts"] = self.issue_counts.copy()
        result["issues"] = [issue.copy() for issue in self.issues]
        result["can_retry_issues"] = self.can_retry_issues()
        result["queue_position"] = queue_position
        result["elapsed_seconds"] = (
            None if self.status == WAITING_FOR_SOURCE else _duration_seconds(self.started_at, self.finished_at)
        )
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

    def __init__(self, connection_factory: Callable = get_conn):
        """Initialize the repository and ensure its table exists.

        Args:
            connection_factory: Callable returning a SQLite connection.
        """
        self._connection_factory = connection_factory
        self._ensure_schema()

    def load(self, *, pause_pending: bool = False) -> tuple[list[ImportJob], list[str]]:
        """Load visible jobs and recover interrupted work.

        Interrupted work resumes by rescanning committed images. An explicit
        cancellation survives a restart. Restored work requires a local resume.

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
                if job.status in {"running", "queued"}:
                    job.status = "paused" if pause_pending else "queued"
                    job.reset_progress()
                    self._save(connection, job, row["queue_order"])
                elif pause_pending and job.status == WAITING_FOR_SOURCE:
                    job.status = "paused"
                    self._save(connection, job, row["queue_order"])
                elif job.status == "cancelling":
                    job.status = "cancelled"
                    job.current_file = None
                    job.finished_at = job.finished_at or _utc_now()
                    self._save(connection, job, row["queue_order"])
                if job.status == "queued":
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
                    unavailable_images = ?,
                    unreadable_images = ?,
                    total_faces = ?,
                    processed_faces = ?,
                    stage = ?,
                    stage_started_at = ?,
                    stage_current = ?,
                    stage_total = ?,
                    current_file = ?,
                    last_error = ?,
                    issue_report = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    job.started_at,
                    job.finished_at,
                    job.total_images,
                    job.hashed_images,
                    job.processed_images,
                    job.unavailable_images,
                    job.unreadable_images,
                    job.total_faces,
                    job.processed_faces,
                    job.stage,
                    job.stage_started_at,
                    job.stage_current,
                    job.stage_total,
                    job.current_file,
                    job.last_error,
                    self._serialize_issues(job),
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

    def update_sources(self, jobs: list[ImportJob], persist: Callable[[Connection], None] | None = None) -> None:
        """Commit all retargeted jobs together while retaining their queue order."""
        connection = self._connection_factory()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                for job in jobs:
                    row = connection.execute(
                        "SELECT queue_order FROM import_job WHERE id = ?", (job.id,)
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("Eine Importaufgabe ist nicht mehr vorhanden.")
                    self._save(connection, job, row["queue_order"])
                if persist is not None:
                    persist(connection)
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
                    unavailable_images INTEGER NOT NULL DEFAULT 0,
                    unreadable_images INTEGER NOT NULL DEFAULT 0,
                    total_faces INTEGER NOT NULL DEFAULT 0,
                    processed_faces INTEGER NOT NULL DEFAULT 0,
                    stage TEXT,
                    stage_started_at TEXT,
                    stage_current INTEGER NOT NULL DEFAULT 0,
                    stage_total INTEGER NOT NULL DEFAULT 0,
                    current_file TEXT,
                    last_error TEXT,
                    issue_report TEXT NOT NULL DEFAULT '{}',
                    retry_paths TEXT NOT NULL DEFAULT '[]',
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
                "unavailable_images": "INTEGER NOT NULL DEFAULT 0",
                "unreadable_images": "INTEGER NOT NULL DEFAULT 0",
                "issue_report": "TEXT NOT NULL DEFAULT '{}'",
                "retry_paths": "TEXT NOT NULL DEFAULT '[]'",
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
                hashed_images, unavailable_images, unreadable_images,
                stage, stage_started_at, stage_current, stage_total,
                current_file, last_error, issue_report, retry_paths, queue_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                folder_path = excluded.folder_path,
                status = excluded.status,
                created_at = excluded.created_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                total_images = excluded.total_images,
                hashed_images = excluded.hashed_images,
                processed_images = excluded.processed_images,
                unavailable_images = excluded.unavailable_images,
                unreadable_images = excluded.unreadable_images,
                total_faces = excluded.total_faces,
                processed_faces = excluded.processed_faces,
                stage = excluded.stage,
                stage_started_at = excluded.stage_started_at,
                stage_current = excluded.stage_current,
                stage_total = excluded.stage_total,
                current_file = excluded.current_file,
                last_error = excluded.last_error,
                issue_report = excluded.issue_report,
                retry_paths = excluded.retry_paths,
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
                job.unavailable_images,
                job.unreadable_images,
                job.stage,
                job.stage_started_at,
                job.stage_current,
                job.stage_total,
                job.current_file,
                job.last_error,
                ImportJobRepository._serialize_issues(job),
                json.dumps(job.retry_paths, ensure_ascii=True),
                queue_order,
            ),
        )

    @staticmethod
    def _serialize_issues(job: ImportJob) -> str:
        return json.dumps({"counts": job.issue_counts, "entries": job.issues}, ensure_ascii=True)

    @staticmethod
    def _row_to_job(row) -> ImportJob:
        """Convert a SQLite row to an import job.

        Args:
            row: SQLite row containing import job columns.

        Returns:
            Hydrated import job.
        """
        report = _load_json(row["issue_report"], {})
        counts = report.get("counts")
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
            unavailable_images=row["unavailable_images"],
            unreadable_images=row["unreadable_images"],
            total_faces=row["total_faces"],
            processed_faces=row["processed_faces"],
            stage=row["stage"],
            stage_started_at=row["stage_started_at"],
            stage_current=row["stage_current"],
            stage_total=row["stage_total"],
            current_file=row["current_file"],
            last_error=row["last_error"],
            issue_counts={
                kind: value
                for kind, value in counts.items()
                if kind in IMPORT_ISSUE_MESSAGES and type(value) is int and value > 0
            } if isinstance(counts, dict) else {},
            issues=_issue_entries(report.get("entries")),
            retry_paths=_issue_entries(_load_json(row["retry_paths"], []), limit=None),
        )


class ImportQueue:
    """Run queued imports with adaptive request-level parallelism.

    Args:
        processor: Processor used to execute one folder import.
        auto_start: Whether to start the daemon worker immediately.
        history_limit: Maximum number of terminal jobs retained.
    """

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
    SOURCE_PROBE_INITIAL_DELAY = 5.0
    SOURCE_PROBE_MAX_DELAY = 120.0
    SOURCE_PROBE_LIMIT = 2
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
        source_resume_lock: ContextManager[object] | None = None,
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
            source_resume_lock: Serializes automatic requeues with library replacement.
        """
        self._on_change = on_change
        self._on_before_terminal = on_before_terminal
        self._on_after_terminal = on_after_terminal
        self._source_resume_lock = source_resume_lock or nullcontext()
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
        self._source_retries: dict[str, tuple[float, float]] = {}
        self._source_probes: set[str] = set()
        self._cancel_events: dict[str, BackgroundTaskControl] = {}
        self._last_progress_persisted: dict[str, float] = {}
        self._worker: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: dict[str, Future] = {}
        self._stage_stats: dict[str, StageTimingStats] = {
            stage: StageTimingStats() for stage in self.STAGE_ORDER
        }
        self._live_stage_timing: dict[str, LiveStageTiming] = {}
        self._stable_etas: dict[tuple[str, str], StableEta] = {}
        self._avg_images_per_completed_job: Optional[float] = None
        self._stopping = False
        self._load_jobs()
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

    def _load_jobs(self, *, pause_pending: bool = False) -> None:
        jobs, pending_ids = self._repository.load(pause_pending=pause_pending)
        self._jobs = OrderedDict((job.id, job) for job in jobs)
        self._pending = deque(pending_ids)
        self._cancel_events = {
            job.id: BackgroundTaskControl() for job in jobs if job.status not in self.TERMINAL_STATUSES
        }
        deadline = time.monotonic() + self.SOURCE_PROBE_INITIAL_DELAY
        self._source_retries = {
            job.id: (deadline, self.SOURCE_PROBE_INITIAL_DELAY)
            for job in jobs if job.status == WAITING_FOR_SOURCE
        }

    def reload_from_library(self) -> None:
        """Reload restored history while leaving pending imports paused."""
        with self._condition:
            if self._active_job_ids or self._pending:
                raise RuntimeError("Aktive Importe müssen vor der Wiederherstellung beendet sein.")
            self._load_jobs(pause_pending=True)
            self._last_progress_persisted.clear()
            self._live_stage_timing.clear()
            self._stable_etas.clear()
            self._stage_stats = {stage: StageTimingStats() for stage in self.STAGE_ORDER}
            self._avg_images_per_completed_job = None

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

    def enqueue(self, folder_path: str, *, retry_paths: list[dict[str, str]] | None = None) -> dict:
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
            retry_paths=retry_paths or [],
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

    def retry_issues(self, job_id: str) -> dict:
        """Requeue only the known skipped files and subfolders of a finished import."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or not job.can_retry_issues():
                raise ValueError("Starte den Bilderordner erneut, um alle ausgelassenen Pfade zu prüfen.")
            folder_path = job.folder_path
            if any(
                other.folder_path == folder_path and other.status not in self.TERMINAL_STATUSES
                for other in self._jobs.values()
            ):
                raise ValueError("Für diesen Bilderordner ist bereits eine Aufgabe offen.")
            retry_paths = list(job.issues)
        return self.enqueue(folder_path, retry_paths=retry_paths)

    def relocate_source(
        self, old_path: str, new_path: str, *, persist: Callable[[Connection], None] | None = None,
    ) -> int:
        """Retarget dormant jobs, including caller updates in the same transaction."""
        old_root, new_root = Path(old_path), Path(new_path)
        with self._condition:
            candidates = [
                job for job in self._jobs.values()
                if Path(job.folder_path) == old_root and job.status not in self.TERMINAL_STATUSES
            ]
            if any(job.id in self._active_job_ids for job in candidates):
                raise ValueError("Der Import für diesen Ordner muss erst beendet werden.")
            moved = []
            for job in candidates:
                retry_paths = []
                for entry in job.retry_paths:
                    try:
                        relative = Path(entry["path"]).relative_to(old_root)
                        if ".." in relative.parts:
                            raise ValueError
                    except ValueError as exc:
                        raise ValueError("Ein Wiederholungspfad liegt außerhalb des bisherigen Bilderordners.") from exc
                    retry_paths.append({**entry, "path": str(new_root / relative)})
                relocated = replace(job, folder_path=new_path, retry_paths=retry_paths, issues=[], issue_counts={})
                relocated.reset_progress()
                if job.status == WAITING_FOR_SOURCE:
                    relocated.status = "queued"
                moved.append(relocated)
            if moved or persist is not None:
                self._repository.update_sources(moved, persist)
            for job in moved:
                self._jobs[job.id] = job
                self._source_retries.pop(job.id, None)
                self._cancel_events[job.id] = BackgroundTaskControl()
                if job.status == "queued" and job.id not in self._pending:
                    self._queue_dormant_job(job.id)
            self._condition.notify_all()
        if moved:
            self._notify_change()
        return len(moved)

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
                "waiting_for_source_count": len(self._source_retries),
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
            if job.status in {"queued", WAITING_FOR_SOURCE}:
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
            observed_at = time.monotonic()
            for state in self._stable_etas.values():
                state.rebase(observed_at)
            return {
                job_id: None
                for job_id in [*active_ids, *self._pending]
            }
        avg_images = self._average_images_per_job()
        job_etas: dict[str, Optional[float]] = {}

        slot_times = [0.0 for _ in range(self._max_concurrent_jobs)]
        active_remaining = {
            job_id: (
                self._estimate_job_remaining_seconds(self._jobs[job_id], avg_images)
                if self._has_reliable_job_eta(self._jobs[job_id])
                else None
            )
            for job_id in active_ids
        }
        if any(eta is None for eta in active_remaining.values()):
            # A queued completion time cannot be credible while the critical
            # path is still discovering its size or calibrating throughput.
            return {
                job_id: None
                for job_id in [*active_ids, *self._pending]
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
        observed_at = time.monotonic()
        return {
            job_id: self._stabilize_eta(job_id, "job", eta, observed_at)
            for job_id, eta in job_etas.items()
        }

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
        if job.status == WAITING_FOR_SOURCE:
            return {stage: None for stage in self.STAGE_ORDER}

        image_count = job.total_images if job.total_images > 0 else self._average_images_per_job()
        if job.stage not in self.STAGE_ORDER:
            cumulative = 0.0
            result: dict[str, Optional[float]] = {}
            for stage in self.STAGE_ORDER:
                cumulative += self._estimate_stage_full_seconds(stage, image_count)
                result[stage] = cumulative
            observed_at = time.monotonic()
            return {
                stage: self._stabilize_eta(
                    job.id,
                    f"station:{stage}",
                    eta,
                    observed_at,
                )
                for stage, eta in result.items()
            }

        current_index = self.STAGE_ORDER.index(job.stage)
        if not self._has_reliable_live_eta(job):
            return {
                stage: 0.0 if index < current_index else None
                for index, stage in enumerate(self.STAGE_ORDER)
            }
        result: dict[str, Optional[float]] = {}
        cumulative = self._estimate_stage_remaining_seconds(job, image_count)
        result[job.stage] = cumulative
        trailing = cumulative
        for stage in self.STAGE_ORDER[current_index + 1 :]:
            trailing += self._estimate_stage_full_seconds(stage, image_count)
            result[stage] = trailing
            future_range = self.STAGE_ORDER[
                current_index + 1 : self.STAGE_ORDER.index(stage) + 1
            ]
            if any(
                future_stage in self.STAGE_DEFAULT_UNIT_SECONDS
                and self._stage_stats[future_stage].avg_seconds_per_unit is None
                for future_stage in future_range
            ):
                result[stage] = None
        for stage in self.STAGE_ORDER[:current_index]:
            result[stage] = 0.0
        observed_at = time.monotonic()
        return {
            stage: (
                0.0
                if eta == 0.0
                else self._stabilize_eta(
                    job.id,
                    f"station:{stage}",
                    eta,
                    observed_at,
                )
            )
            for stage, eta in result.items()
        }

    def _stabilize_eta(
        self,
        job_id: str,
        key: str,
        raw_seconds: Optional[float],
        observed_at: float,
    ) -> Optional[float]:
        """Return a stable ETA shared by every live view of the same target."""
        if raw_seconds is None:
            return None
        target = max(0.0, raw_seconds)
        state_key = (job_id, key)
        state = self._stable_etas.get(state_key)
        if state is None:
            self._stable_etas[state_key] = StableEta(target, observed_at)
            return target
        return state.update(target, observed_at)

    def _rebase_stable_etas(self, job_id: str, observed_at: float) -> None:
        """Prevent a pause from consuming the displayed remaining time."""
        for (eta_job_id, _key), state in self._stable_etas.items():
            if eta_job_id == job_id:
                state.rebase(observed_at)

    def _drop_stable_etas(self, job_id: str) -> None:
        """Discard smoothing state once an import is no longer live."""
        for state_key in [key for key in self._stable_etas if key[0] == job_id]:
            self._stable_etas.pop(state_key, None)

    def _has_reliable_live_eta(self, job: ImportJob) -> bool:
        """Hide guesses until size and live throughput are meaningful."""
        if job.stage == "scanning":
            return False
        if job.stage not in self.STAGE_DEFAULT_UNIT_SECONDS:
            return True
        if self._stage_stats[job.stage].avg_seconds_per_unit is not None:
            return True
        tracker = self._live_stage_timing.get(job.id)
        return bool(
            tracker is not None
            and tracker.stage == job.stage
            and tracker.seconds_per_unit is not None
        )

    def _has_reliable_job_eta(self, job: ImportJob) -> bool:
        """Require measured rates for every remaining variable-cost stage."""
        if not self._has_reliable_live_eta(job):
            return False
        if job.stage not in self.STAGE_ORDER:
            return False
        current_index = self.STAGE_ORDER.index(job.stage)
        return all(
            stage not in self.STAGE_DEFAULT_UNIT_SECONDS
            or self._stage_stats[stage].avg_seconds_per_unit is not None
            for stage in self.STAGE_ORDER[current_index + 1 :]
        )

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

            tracker = self._live_stage_timing.get(job.id)
            if tracker is not None and tracker.stage == stage:
                live_rate = tracker.seconds_per_unit
                if live_rate is not None:
                    rate = live_rate
            elif elapsed is not None and current > 0:
                live_rate = elapsed / current
                rate = rate * 0.6 + live_rate * 0.4
            remaining = (total - current) * rate
            if tracker is not None and tracker.stage == stage:
                # A currently slow image contributes only its overdue time. It
                # must not instantly make every remaining image look equally
                # slow, which was a major source of ETA spikes.
                remaining += tracker.overdue_seconds(time.monotonic())
            return remaining

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
            self._source_retries.pop(job_id, None)
            self._drop_stable_etas(job_id)
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
            self._repository.update(replace(job, status="paused"))
            control = self._cancel_events[job_id]
            if job_id in self._active_job_ids:
                control.pause()
            elif job_id in self._pending:
                self._pending.remove(job_id)
            job.status = "paused"
            self._source_retries.pop(job_id, None)
            self._condition.notify_all()
        self._notify_change()
        return {"id": job_id, "status": "paused"}

    def resume(self, job_id: str) -> Optional[dict]:
        """Resume a paused import or immediately retry a parked source."""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"paused", WAITING_FOR_SOURCE}:
                return None
            next_status = "running" if job_id in self._active_job_ids else "queued"
            last_error = None if job.status == WAITING_FOR_SOURCE else job.last_error
            self._repository.update(replace(job, status=next_status, last_error=last_error))
            job.status, job.last_error = next_status, last_error
            self._source_retries.pop(job_id, None)
            control = self._cancel_events.setdefault(job_id, BackgroundTaskControl())
            if next_status == "running":
                control.resume()
                resumed_at = time.monotonic()
                tracker = self._live_stage_timing.get(job_id)
                if tracker is not None and tracker.stage == job.stage:
                    tracker.rebase(
                        self._stage_progress(job),
                        resumed_at,
                    )
                self._rebase_stable_etas(job_id, resumed_at)
            else:
                self._queue_dormant_job(job_id)
            self._condition.notify_all()
        self._notify_change()
        return {"id": job_id, "status": next_status}

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
                cancelled = replace(job, status="cancelled", finished_at=_utc_now(), current_file=None)
                self._repository.update(cancelled)
                self._jobs[job_id] = cancelled
                if job_id in self._pending:
                    self._pending.remove(job_id)
                self._cancel_events.pop(job_id, None)
                self._source_retries.pop(job_id, None)
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
            self._drop_stable_etas(job_id)
        self._notify_change()
        return {"id": job_id, "status": "removed"}

    def clear_history(self) -> int:
        """Permanently delete every terminal import history entry."""
        with self._condition:
            deleted_ids = self._repository.delete_terminal_history()
            for job_id in deleted_ids:
                self._jobs.pop(job_id, None)
                self._drop_stable_etas(job_id)
        if deleted_ids:
            self._notify_change()
        return len(deleted_ids)

    def _worker_loop(self) -> None:
        """Dispatch queued jobs while respecting concurrency limits."""
        while True:
            with self._condition:
                while not self._stopping:
                    probe_delay = self._launch_due_source_probes()
                    if self._pending and len(self._active_job_ids) < self._max_concurrent_jobs:
                        break
                    self._condition.wait(probe_delay)
                if self._stopping:
                    return

                job_id = self._pending.popleft()
                job = self._jobs[job_id]
                job.reset_progress()
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
            # A finished Future invokes its callback inline. Never hold the
            # queue condition while calling the external finalization guard.
            future.add_done_callback(
                lambda done_future, active_job_id=job_id: self._on_job_done(
                    active_job_id,
                    done_future,
                )
            )
            self._notify_change()

    def _queue_dormant_job(self, job_id: str) -> None:
        self._pending.append(job_id)
        order = {key: index for index, key in enumerate(self._jobs)}
        self._pending = deque(sorted(self._pending, key=order.__getitem__))

    def _launch_due_source_probes(self) -> float | None:
        """Start bounded probes without delaying the dispatch of other imports."""
        now = time.monotonic()
        retries = sorted(self._source_retries.items(), key=lambda item: item[1][0])
        for job_id, (deadline, _) in retries:
            if len(self._source_probes) >= self.SOURCE_PROBE_LIMIT:
                break
            if job_id in self._source_probes:
                continue
            if deadline > now:
                return deadline - now
            self._source_probes.add(job_id)
            threading.Thread(
                target=self._probe_source,
                args=(job_id, self._jobs[job_id].folder_path, self._cancel_events[job_id]),
                name="face-import-source-probe",
                daemon=True,
            ).start()
        return None

    def _probe_source(self, job_id: str, folder_path: str, control: BackgroundTaskControl) -> None:
        available = False
        try:
            check_import_source(folder_path)
            available = True
        except ImportSourceUnavailable:
            pass
        except Exception:
            logger.exception("Could not check import source %s", folder_path)

        with self._source_resume_lock, self._condition:
            self._source_probes.discard(job_id)
            self._condition.notify_all()
            job = self._jobs.get(job_id)
            if (
                self._stopping or job is None or job.status != WAITING_FOR_SOURCE
                or self._cancel_events.get(job_id) is not control
            ):
                return
            if available:
                resumed = replace(job, status="queued", last_error=None)
                try:
                    self._repository.update(resumed)
                except Exception:
                    logger.exception("Could not persist automatic resume of import %s", job_id)
                else:
                    self._jobs[job_id] = resumed
                    self._source_retries.pop(job_id, None)
                    self._queue_dormant_job(job_id)
            if job_id in self._source_retries:
                _, delay = self._source_retries[job_id]
                delay = min(self.SOURCE_PROBE_MAX_DELAY, delay * 2)
                self._source_retries[job_id] = (time.monotonic() + delay, delay)
                return
        self._notify_change()

    def _run_job(self, job_id: str, cancel_event: threading.Event) -> tuple[str, Optional[str]]:
        """Execute one job and return its outcome with an optional error."""
        job = self._jobs[job_id]
        try:
            args = (job.folder_path, self._progress_callback(job_id), cancel_event)
            if job.retry_paths:
                self._processor.process(*args, retry_paths=job.retry_paths)
            else:
                self._processor.process(*args)
        except ImportCancelled:
            return "cancelled", None
        except ImportSourceUnavailable as exc:
            return WAITING_FOR_SOURCE, str(exc)
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Import job %s failed for %s", job_id, job.folder_path)
            return "failed", str(exc)
        return ("cancelled", None) if cancel_event.is_set() else ("completed", None)

    def _on_job_done(self, job_id: str, future: Future) -> None:
        """Persist the job state after its worker exits."""
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

                if job.status == "cancelling":
                    final_status, error_message = "cancelled", None
                elif final_status == WAITING_FOR_SOURCE and job.status == "paused":
                    final_status = "paused"
                interrupted = final_status == "cancelled" and self._stopping and job.status != "cancelling"
                if interrupted:
                    final_status = "paused" if job.status == "paused" else "queued"
                if final_status != WAITING_FOR_SOURCE:
                    self._record_stage_stats(job)
                if error_message:
                    job.last_error = error_message
                job.status = final_status
                job.finished_at = _utc_now() if final_status in self.TERMINAL_STATUSES else None
                if final_status == "queued":
                    job.reset_progress()
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
                try:
                    self._repository.update(job)
                except Exception as exc:
                    logger.exception("Could not persist terminal state of import %s", job_id)
                    job.status = "failed"
                    job.last_error = f"Der Importstatus konnte nicht gespeichert werden. {exc}"
                finally:
                    self._active_job_ids.discard(job_id)
                    self._cancel_events.pop(job_id, None)
                    self._last_progress_persisted.pop(job_id, None)
                    self._live_stage_timing.pop(job_id, None)
                    self._drop_stable_etas(job_id)
                    self._futures.pop(job_id, None)
                    if job.status not in self.TERMINAL_STATUSES:
                        self._cancel_events[job_id] = BackgroundTaskControl()
                    if job.status == "queued":
                        self._queue_dormant_job(job_id)
                    if job.status == WAITING_FOR_SOURCE:
                        delay = self.SOURCE_PROBE_INITIAL_DELAY
                        self._source_retries[job_id] = (time.monotonic() + delay, delay)
                    self._condition.notify_all()
                try:
                    self._trim_history()
                except Exception:
                    logger.exception("Could not trim import history")
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
        if job.stage in self.STAGE_DEFAULT_UNIT_SECONDS and not units:
            return
        self._stage_stats[job.stage].update(elapsed, units)

    @staticmethod
    def _stage_unit_count(stage: str, job: ImportJob) -> Optional[int]:
        if stage == "scanning":
            return max(0, job.stage_current)
        if stage == "hashing":
            return max(0, job.hashed_images)
        if stage == "processing":
            return max(0, job.processed_images)
        return None

    @staticmethod
    def _stage_progress(job: ImportJob) -> int:
        if job.stage == "processing":
            return max(0, job.processed_images)
        if job.stage == "hashing":
            return max(0, job.hashed_images)
        return max(0, job.stage_current)

    def _observe_live_stage_timing(self, job: ImportJob, observed_at: float) -> None:
        """Update the recent-throughput window for a unit-based stage."""
        if job.stage not in self.STAGE_DEFAULT_UNIT_SECONDS:
            self._live_stage_timing.pop(job.id, None)
            return

        progress = self._stage_progress(job)
        tracker = self._live_stage_timing.get(job.id)
        if tracker is None or tracker.stage != job.stage:
            self._live_stage_timing[job.id] = LiveStageTiming(
                stage=job.stage,
                last_progress=progress,
                last_sample_at=observed_at,
            )
            return
        was_calibrated = tracker.seconds_per_unit is not None
        tracker.observe(progress, observed_at)
        if (
            not was_calibrated
            and tracker.seconds_per_unit is not None
            and self._stage_stats[job.stage].avg_seconds_per_unit is None
        ):
            # Replace the placeholder queued estimate once with the first real
            # rate, then smooth only subsequent corrections.
            self._drop_stable_etas(job.id)

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
                issue = update_values.pop("issue", None)
                if issue:
                    kind = issue["kind"]
                    job.issue_counts[kind] = job.issue_counts.get(kind, 0) + 1
                    if len(job.issues) < MAX_IMPORT_ISSUES:
                        job.issues.append(issue)
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
                self._observe_live_stage_timing(job, now)
                last_persisted = self._last_progress_persisted.get(job_id, 0.0)
                import_finished = (
                    job.total_images > 0 and job.processed_images >= job.total_images
                )
                if import_finished or update_values.get("last_error") or now - last_persisted >= 0.5:
                    self._repository.update(job)
                    self._last_progress_persisted[job_id] = now
            self._notify_change()

        return update

    def _trim_history(self) -> None:
        """Drop oldest terminal jobs beyond the configured history limit."""
        deleted_ids = self._repository.trim_terminal_history(self._history_limit)
        for job_id in deleted_ids:
            self._jobs.pop(job_id, None)
            self._drop_stable_etas(job_id)
