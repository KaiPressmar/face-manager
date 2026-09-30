"""Coordinate background clustering work so requests are never dropped.

Reclustering shares SQLite's single writer with imports, so the two cannot run
at the same time. Instead of rejecting a reclustering request while an import
is busy (which silently lost the user's action), the queue *accepts* every
request and keeps it visible as ``queued`` until the writer is free. A readiness
gate decides when a queued task may actually start, and :meth:`notify_ready`
re-checks that gate whenever the surrounding activity changes (an import
finishes, finalization ends). Only one clustering task runs at a time; a request
that arrives while another runs is coalesced into a single pending slot, with
higher-priority requests superseding lower-priority ones.
"""

from __future__ import annotations

import inspect
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from .storage import count_active_inbox_faces, repair_active_inbox_faces
from .task_control import BackgroundTaskControl

logger = logging.getLogger("face_manager.autocluster_queue")


# Relative importance when several requests compete for the single pending slot.
# A more thorough or more urgent request supersedes a lighter one.
PRIORITY_STARTUP_REPAIR = 5
PRIORITY_IDLE_RECLUSTER = 10
PRIORITY_MANUAL_RECLUSTER = 20
PRIORITY_VERSION_UPGRADE = 30


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
class ReclusterRequest:
    """One accepted clustering request awaiting or occupying the worker."""

    reason: str
    kind: str
    priority: int
    count_callable: Callable[[], int]
    repair_callable: Callable[..., int]


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
    """Run one clustering task at a time without ever dropping a request."""

    def __init__(
        self,
        count_callable: Callable[[], int] = count_active_inbox_faces,
        repair_callable: Callable[..., int] = repair_active_inbox_faces,
        on_success: Optional[Callable[[int], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
        ready_gate: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._count_callable = count_callable
        self._repair_callable = repair_callable
        self._on_success = on_success
        self._on_change = on_change
        # Whether a queued task may start now. When it returns ``False`` the task
        # stays visibly queued until :meth:`notify_ready` finds the gate open.
        self._ready_gate = ready_gate or (lambda: True)
        self._lock = threading.Lock()
        self._task: Optional[AutoClusterTask] = None
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = BackgroundTaskControl()
        # Request bound to ``self._task`` (queued or running).
        self._active_request: Optional[ReclusterRequest] = None
        # Single coalesced request to run once the active task finishes.
        self._pending_request: Optional[ReclusterRequest] = None

    def start(
        self,
        reason: str,
        *,
        kind: str = "auto_cluster_repair",
        count_callable: Optional[Callable[[], int]] = None,
        repair_callable: Optional[Callable[..., int]] = None,
        priority: int = 0,
    ) -> Optional[dict]:
        """Accept a clustering request, queuing or starting it as appropriate.

        Returns the visible task (``queued`` while deferred, ``running`` once it
        starts). Returns ``None`` only when there is genuinely nothing to do.
        """
        request = ReclusterRequest(
            reason=reason,
            kind=kind,
            priority=priority,
            count_callable=count_callable or self._count_callable,
            repair_callable=repair_callable or self._repair_callable,
        )
        with self._lock:
            active = self._task is not None and self._task.status in {
                "queued", "running", "paused", "cancelling"
            }
            if active and self._task.status in {"running", "paused", "cancelling"}:
                # Coalesce behind the running task; it is picked up on completion.
                self._coalesce_pending_locked(request)
                result = self._task.to_dict()
            elif active:  # a deferred (queued, not yet started) task exists
                if request.priority >= self._active_request.priority:
                    self._active_request = request
                    self._task.kind = request.kind
                    self._task.reason = request.reason
                    self._task.total_faces = max(0, request.count_callable())
                self._maybe_launch_locked()
                result = self._task.to_dict()
            else:
                total_faces = request.count_callable()
                if total_faces <= 0:
                    return None
                self._task = AutoClusterTask(
                    id=f"autocluster-{uuid.uuid4().hex[:12]}",
                    kind=request.kind,
                    reason=request.reason,
                    status="queued",
                    created_at=_utc_now(),
                    total_faces=total_faces,
                    stage="preparing",
                )
                self._active_request = request
                self._maybe_launch_locked()
                result = self._task.to_dict()
        self._notify_change()
        return result

    def notify_ready(self) -> None:
        """Re-check the readiness gate after surrounding activity changed."""
        launched = False
        with self._lock:
            if (
                self._task is not None
                and self._task.status == "queued"
                and self._active_request is not None
            ):
                launched = self._maybe_launch_locked()
        if launched:
            self._notify_change()

    def _coalesce_pending_locked(self, request: ReclusterRequest) -> None:
        """Keep the highest-priority request in the single pending slot."""
        if (
            self._pending_request is None
            or request.priority >= self._pending_request.priority
        ):
            self._pending_request = request

    def _maybe_launch_locked(self) -> bool:
        """Start the queued task if the writer is free. Caller holds the lock."""
        if self._task is None or self._task.status != "queued":
            return False
        if self._active_request is None:
            return False
        try:
            if not self._ready_gate():
                return False
        except Exception:  # pragma: no cover - a gate defect must not lose work
            logger.exception("Auto-cluster readiness gate failed; deferring")
            return False

        task_id = self._task.id
        self._cancel_event = BackgroundTaskControl()
        self._task.status = "running"
        self._task.started_at = _utc_now()
        self._task.stage = "processing"
        self._thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"autocluster-repair-{task_id}",
            daemon=True,
        )
        self._thread.start()
        return True

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
        notify = False
        promote_task_id = None
        with self._lock:
            active = self._task is not None and self._task.status in {
                "queued", "running", "paused", "cancelling"
            }
            if not active:
                return True
            if self._thread is None or not self._thread.is_alive():
                self._task.status = "cancelled"
                self._task.finished_at = _utc_now()
                self._task.stage = "cancelled"
                promote_task_id = self._task.id
                notify = True
            else:
                self._task.status = "cancelling"
                self._cancel_event.set()
            thread = self._thread
        if notify:
            self._notify_change()
            self._promote_pending(promote_task_id)
        if timeout > 0 and thread is not None:
            thread.join(timeout)
        with self._lock:
            return not (
                self._task is not None and self._task.status in {
                    "queued", "running", "paused", "cancelling"
                }
            )

    def pause(self, task_id: str) -> Optional[dict]:
        """Pause queued or running clustering at a committed group boundary."""
        with self._lock:
            if (
                self._task is None
                or self._task.id != task_id
                or self._task.status not in {"queued", "running"}
            ):
                return None
            if self._task.status == "running":
                self._cancel_event.pause()
            self._task.status = "paused"
            result = self._task.to_dict()
        self._notify_change()
        return result

    def resume(self, task_id: str) -> Optional[dict]:
        """Resume a paused clustering task."""
        with self._lock:
            if self._task is None or self._task.id != task_id or self._task.status != "paused":
                return None
            if self._thread is not None and self._thread.is_alive():
                self._task.status = "running"
                self._cancel_event.resume()
            else:
                self._task.status = "queued"
                self._maybe_launch_locked()
            result = self._task.to_dict()
        self._notify_change()
        return result

    def cancel(self, task_id: str) -> Optional[dict]:
        """Cancel a queued, running, or paused clustering task."""
        with self._lock:
            if (
                self._task is None
                or self._task.id != task_id
                or self._task.status not in {"queued", "running", "paused", "cancelling"}
            ):
                return None
        self.request_cancel()
        with self._lock:
            return self._task.to_dict() if self._task is not None else None

    def dismiss(self, task_id: str) -> bool:
        """Remove one terminal clustering task from visible history."""
        with self._lock:
            if (
                self._task is None
                or self._task.id != task_id
                or self._task.status not in {"completed", "failed", "cancelled"}
            ):
                return False
            self._task = None
            self._active_request = None
            self._thread = None
        self._notify_change()
        return True

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
        self._notify_change()

        def progress_callback(processed: int, total: int) -> None:
            with self._lock:
                if self._task is None or self._task.id != task_id:
                    return
                self._task.processed_faces = processed
                self._task.total_faces = total
            self._notify_change()

        repaired_faces = 0
        try:
            with self._lock:
                request = self._active_request
                cancel_event = self._cancel_event
            repair_callable = request.repair_callable if request else self._repair_callable
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
        finally:
            self._promote_pending(task_id)

    def _promote_pending(self, finished_task_id: str) -> None:
        """Start the coalesced pending request once the worker is free."""
        launched = False
        with self._lock:
            if self._task is None or self._task.id != finished_task_id:
                return
            if self._task.status not in {"completed", "failed", "cancelled"}:
                return
            request = self._pending_request
            self._pending_request = None
            if request is None:
                self._active_request = None
                return
            total_faces = max(0, request.count_callable())
            if total_faces <= 0:
                self._active_request = None
                return
            self._task = AutoClusterTask(
                id=f"autocluster-{uuid.uuid4().hex[:12]}",
                kind=request.kind,
                reason=request.reason,
                status="queued",
                created_at=_utc_now(),
                total_faces=total_faces,
                stage="preparing",
            )
            self._active_request = request
            self._maybe_launch_locked()
            launched = True
        if launched:
            self._notify_change()
