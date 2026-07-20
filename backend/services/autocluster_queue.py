"""Track background auto-clustering repair work for UI consumers."""

from __future__ import annotations

import inspect
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .storage import count_active_inbox_faces, repair_active_inbox_faces

logger = logging.getLogger("face_manager.autocluster_queue")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_repair(repair_callable, progress_callback, cancel_event):
    """Invoke a repair callable, passing the cancel token only if it takes one.

    The inbox repair pass has no cancellation support (it is short and must
    finish), while the full rebuild accepts ``cancel_token``.
    """
    try:
        accepts_token = "cancel_token" in inspect.signature(repair_callable).parameters
    except (TypeError, ValueError):  # builtins / C callables
        accepts_token = False
    if accepts_token:
        return repair_callable(
            progress_callback=progress_callback,
            cancel_token=cancel_event,
        )
    return repair_callable(progress_callback=progress_callback)


def _duration_seconds(started_at: Optional[str], finished_at: Optional[str] = None):
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


@dataclass
class AutoClusterTask:
    id: str
    kind: str
    reason: str
    status: str = "queued"
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_faces: int = 0
    processed_faces: int = 0
    repaired_faces: int = 0
    stage: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["elapsed_seconds"] = _duration_seconds(self.started_at, self.finished_at)
        return payload


class AutoClusterQueue:
    """Run one visible background auto-clustering repair task at a time."""

    def __init__(
        self,
        count_callable: Callable[[], int] = count_active_inbox_faces,
        repair_callable: Callable[..., int] = repair_active_inbox_faces,
        on_success: Optional[Callable[[int], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._count_callable = count_callable
        self._repair_callable = repair_callable
        self._on_success = on_success
        self._on_change = on_change
        self._lock = threading.Lock()
        self._task: Optional[AutoClusterTask] = None
        self._thread: Optional[threading.Thread] = None
        # Set when an interactive write asks the running pass to step aside.
        self._cancel_event = threading.Event()
        # Repair callable bound to the currently scheduled task. Only one task
        # runs at a time, so a single slot guarded by the lock is sufficient.
        self._active_repair_callable: Callable[..., int] = repair_callable

    def start(
        self,
        reason: str,
        *,
        kind: str = "auto_cluster_repair",
        count_callable: Optional[Callable[[], int]] = None,
        repair_callable: Optional[Callable[..., int]] = None,
    ) -> Optional[dict]:
        """Start a visible background clustering task when work exists."""
        with self._lock:
            if self._task is not None and self._task.status in {"queued", "running"}:
                return self._task.to_dict()

            effective_count_callable = count_callable or self._count_callable
            effective_repair_callable = repair_callable or self._repair_callable
            total_faces = effective_count_callable()
            if total_faces <= 0:
                return None

            task = AutoClusterTask(
                id=f"autocluster-{uuid.uuid4().hex[:12]}",
                kind=kind,
                reason=reason,
                status="queued",
                created_at=_utc_now(),
                total_faces=total_faces,
                stage="preparing",
            )
            self._active_repair_callable = effective_repair_callable
            self._cancel_event = threading.Event()
            self._task = task
            self._thread = threading.Thread(
                target=self._run_task,
                args=(task.id,),
                name=f"autocluster-repair-{task.id}",
                daemon=True,
            )
            self._thread.start()
            result = task.to_dict()
        self._notify_change()
        return result

    def request_cancel(self, timeout: float = 0.0) -> bool:
        """Ask a running pass to stop so an interactive write can proceed.

        Reclustering is a low-priority optimisation, so the user always wins.
        The pass stops at its next group boundary, which leaves a consistent
        state and keeps unfinished groups marked for the next run.

        Args:
            timeout: How long to wait for the pass to actually step aside.

        Returns:
            Whether no clustering pass is active anymore.
        """
        with self._lock:
            active = self._task is not None and self._task.status in {"queued", "running"}
            if not active:
                return True
            self._cancel_event.set()
            thread = self._thread
        if timeout > 0 and thread is not None:
            thread.join(timeout)
        with self._lock:
            return not (
                self._task is not None and self._task.status in {"queued", "running"}
            )

    def snapshot(self) -> dict:
        with self._lock:
            task = self._task.to_dict() if self._task is not None else None
        return {"task": task}

    def _notify_change(self) -> None:
        """Notify the change subscriber, isolating it from task failures.

        Must be called outside ``self._lock`` because the subscriber typically
        reads :meth:`snapshot`, which re-acquires the non-reentrant lock.
        """
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # pragma: no cover - subscriber must never break work
            logger.exception("Auto-cluster change notification failed")

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            if self._task is None or self._task.id != task_id:
                return
            self._task.status = "running"
            self._task.started_at = _utc_now()
            self._task.stage = "processing"
        self._notify_change()

        def progress_callback(processed: int, total: int) -> None:
            with self._lock:
                if self._task is None or self._task.id != task_id:
                    return
                self._task.processed_faces = processed
                self._task.total_faces = total
            self._notify_change()

        try:
            with self._lock:
                repair_callable = self._active_repair_callable
                cancel_event = self._cancel_event
            repaired_faces = _call_repair(
                repair_callable,
                progress_callback,
                cancel_event,
            )
            if cancel_event.is_set():
                with self._lock:
                    if self._task is None or self._task.id != task_id:
                        return
                    self._task.status = "cancelled"
                    self._task.finished_at = _utc_now()
                    self._task.stage = "cancelled"
                    self._task.repaired_faces = repaired_faces
                self._notify_change()
                return
            with self._lock:
                if self._task is None or self._task.id != task_id:
                    return
                self._task.processed_faces = self._task.total_faces
                self._task.repaired_faces = repaired_faces
                self._task.stage = "finalizing"
            self._notify_change()
            if repaired_faces > 0 and self._on_success is not None:
                self._on_success(repaired_faces)
            # Keep the task in its blocking "running" state until caches and
            # clients have been notified. This closes the stale-UI window in
            # which an interactive write could otherwise target old clusters.
            with self._lock:
                if self._task is None or self._task.id != task_id:
                    return
                self._task.status = "completed"
                self._task.finished_at = _utc_now()
                self._task.stage = "completed"
            self._notify_change()
        except Exception as exc:
            logger.exception("Auto-clustering repair task failed")
            with self._lock:
                if self._task is None or self._task.id != task_id:
                    return
                self._task.status = "failed"
                self._task.finished_at = _utc_now()
                self._task.stage = "failed"
                self._task.last_error = str(exc)
            self._notify_change()
