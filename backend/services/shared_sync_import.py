"""Connect imports and idle assistance to a qualified shared analysis scope."""

import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from inspect import signature
from threading import Lock

from .shared_sync_analysis import SharedAnalysisExchange
from .shared_sync_io import SharedSyncLane
from .shared_sync_job_announcements import (
    MAX_ANNOUNCEMENT_PAGE,
    make_job_announcement,
    validate_job_announcement,
)
from .shared_sync_snapshot import _relative_path
from .shared_sync_transport import InvalidTransportData

_MAX_SCOPE_STATES = 16
_MAX_ANNOUNCED = 256
_MAX_FINISHED = 4096
logger = logging.getLogger("face_manager.shared_sync_import")


@dataclass(frozen=True)
class ImportShare:
    source_path: str
    sync_path: str
    manifest: dict
    producer: str
    session: str
    fresh_reads: bool = False
    assist_paused: bool = False
    excluded_roots: tuple[str, ...] = ()
    device_name: str | None = field(default=None, compare=False)

    def contains_path(self, path: str) -> bool:
        return self._relative(path) is not None

    def _relative(self, path: str) -> str | None:
        relative = _relative_path(path, self.source_path)
        if relative is None:
            return None
        identity = os.path.normcase(os.path.normpath(path))
        for excluded in self.excluded_roots:
            if identity == os.path.normcase(os.path.normpath(excluded)) or _relative_path(path, excluded) is not None:
                return None
        return relative


@dataclass
class _Scope:
    binding: ImportShare
    exchange: SharedAnalysisExchange
    reader: SharedAnalysisExchange
    after: str | None = None
    announced: OrderedDict = field(default_factory=OrderedDict)
    finished: OrderedDict = field(default_factory=OrderedDict)


def _remember(items: OrderedDict, key: str, limit: int) -> None:
    items[key] = None
    items.move_to_end(key)
    if len(items) > limit:
        items.popitem(last=False)


class SharedImportAnalysis:
    """Make a single import discoverable without scanning peers' source folders."""

    def __init__(self, current_share, *, available_shares=None, on_reused=None, dispatch=None):
        self.current_share = current_share
        self.available_shares = available_shares
        self._on_reused = on_reused
        self._dispatch = dispatch
        try:
            signature(current_share).bind("")
        except (TypeError, ValueError):
            self._lookup = lambda _path: current_share()
        else:
            self._lookup = current_share
        self._lock = Lock()
        self._assist_lock = Lock()
        self._work = SharedSyncLane()
        self._heartbeat = SharedSyncLane()
        self._scopes = OrderedDict()

    @staticmethod
    def _identity(binding: ImportShare) -> tuple:
        return (
            binding.source_path, binding.sync_path, binding.manifest.get("scope"),
            binding.manifest.get("generation"), binding.producer, binding.session,
            binding.fresh_reads, binding.excluded_roots, binding.device_name,
        )

    def _current(self, binding: ImportShare) -> ImportShare | None:
        candidates = self.available_shares() if self.available_shares is not None else (self._lookup(binding.source_path),)
        return next((
            current for current in candidates
            if current is not None and replace(current, assist_paused=False) == binding
        ), None)

    def _request_for(self, binding: ImportShare, lane):
        def request(payload, *, staging_root=None):
            current = self._current(binding)
            if current is None:
                return {"status": "unavailable"}
            # Let the current owner finish safely, and stop taking new work.
            if current.assist_paused and payload.get("operation") in ("claim", "expire"):
                return {"status": "unavailable"}
            if self._dispatch is not None:
                return self._dispatch(binding, lane, payload, staging_root=staging_root)
            return lane.request(payload, staging_root=staging_root)
        return request

    def _scope(self, binding: ImportShare) -> _Scope:
        binding = replace(binding, assist_paused=False)
        key = self._identity(binding)
        with self._lock:
            scope = self._scopes.get(key)
            if scope is None:
                options = {
                    "request": self._request_for(binding, self._work),
                    "heartbeat_request": self._request_for(binding, self._heartbeat),
                    "sync_root": binding.sync_path, "source_root": binding.source_path,
                    "manifest": binding.manifest, "device_id": binding.producer, "session_id": binding.session,
                    "worker_name": binding.device_name,
                }
                scope = _Scope(
                    binding, SharedAnalysisExchange(**options, allow_writes=True),
                    SharedAnalysisExchange(**options, allow_writes=False),
                )
                self._scopes[key] = scope
            self._scopes.move_to_end(key)
            if len(self._scopes) > _MAX_SCOPE_STATES:
                self._scopes.popitem(last=False)
            return scope

    def _queue(self, scope: _Scope, operation: str, **fields):
        binding = scope.binding
        return self._request_for(binding, self._work)({
            "action": "job_queue", "operation": operation, "sync_root": binding.sync_path,
            "known_manifest": binding.manifest, "allow_writes": operation == "publish", **fields,
        })

    def _publish(self, scope: _Scope, job: dict, relative: str) -> bool:
        expected = make_job_announcement(scope.binding.manifest, job, relative)
        identity = expected["announcement_id"]
        with self._lock:
            if identity in scope.announced:
                return True
        response = self._queue(scope, "publish", job=job, relative_path=relative)
        if not isinstance(response, dict) or response.get("status") != "job_queue":
            return False
        if validate_job_announcement(scope.binding.manifest, response.get("value")) != expected:
            raise InvalidTransportData("Shared analysis publication differs from its intent")
        with self._lock:
            _remember(scope.announced, identity, _MAX_ANNOUNCED)
        return True

    def announce(self, model, path: str, photo_sha256: str) -> bool:
        """Publish only a previously verified import digest, ahead of inference."""
        binding = self._lookup(path)
        if binding is None or (relative := binding._relative(path)) is None:
            return False
        scope = self._scope(binding)
        return self._publish(scope, scope.exchange.describe(model, photo_sha256), relative)

    def analyze(self, model, path: str, photo_sha256: str):
        binding = self._lookup(path)
        if binding is None or (relative := binding._relative(path)) is None:
            return None
        scope = self._scope(binding)
        job = scope.exchange.describe(model, photo_sha256)
        # An uncertain announcement is retried at its immutable ID. It does
        # not authorize a claim until the worker confirms the exact record.
        announced = self._publish(scope, job, relative)
        analyzer = scope.reader if binding.assist_paused or not announced else scope.exchange
        outcome = analyzer.analyze_announced(model, job, relative, server_fresh=binding.fresh_reads)
        current = self._lookup(path)
        if current is None or replace(current, assist_paused=False) != scope.binding:
            return None
        self._report_reuse(current, outcome)
        return outcome

    def _report_reuse(self, binding, outcome) -> None:
        if self._on_reused is None or outcome.state != "reused":
            return
        name = outcome.worker_name
        device = outcome.worker_device_id
        if name is not None and device is not None and device != binding.producer:
            try:
                self._on_reused(binding, name)
            except Exception:
                logger.warning("Could not report reused shared analysis", exc_info=True)

    def assist(self, binding: ImportShare, model, *, limit: int = 8) -> dict:
        """Process a bounded page of verified announcements on a background thread.

        Accept either an initialized model or a getter, loading lazily when a
        page contains work. The assistant publishes results; the initiating
        importer remains responsible for its local SQLite writes.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_ANNOUNCEMENT_PAGE:
            raise ValueError("Invalid shared analysis assistant page size")
        counts = {"inspected": 0, "completed": 0, "pending": 0, "more": False}
        if not self._assist_lock.acquire(blocking=False):
            return counts
        try:
            return self._assist(binding, model, limit, counts)
        finally:
            self._assist_lock.release()

    def _assist(self, binding, model, limit, counts):
        scope = self._scope(binding)
        current = self._current(scope.binding)
        if current is None or current.assist_paused:
            return counts
        response = self._queue(scope, "list", after=scope.after, limit=limit)
        if not isinstance(response, dict) or response.get("status") != "job_queue":
            scope.exchange.reset_observations()
            return counts
        page = response.get("value")
        if (
            not isinstance(page, dict) or type(page.get("at_end")) is not bool
            or not isinstance(page.get("announcements"), list) or len(page["announcements"]) > limit
            or (not page["at_end"] and not page["announcements"])
        ):
            raise InvalidTransportData("Invalid shared analysis work page")
        notices = [validate_job_announcement(binding.manifest, item) for item in page["announcements"]]
        ids = [notice["announcement_id"] for notice in notices]
        if ids and (ids != sorted(set(ids)) or page.get("after") != ids[-1] or (scope.after and ids[0] <= scope.after)):
            raise InvalidTransportData("Invalid shared analysis work cursor")
        for notice in notices:
            counts["inspected"] += 1
            relative, job = notice["relative_path"], notice["job"]
            absolute = os.path.join(binding.source_path, *relative.split("/"))
            if not binding.contains_path(absolute):
                continue
            current = self._current(scope.binding)
            if current is None or current.assist_paused:
                return counts
            with self._lock:
                if job["job_id"] in scope.finished:
                    continue
            if not callable(getattr(model, "shared_pipeline_signature", None)):
                model = model()
            outcome = scope.exchange.analyze_announced(
                model, job, relative, server_fresh=current.fresh_reads,
            )
            if outcome.state in ("analyzed", "reused", "exhausted"):
                latest = self._current(scope.binding)
                if latest is not None:
                    self._report_reuse(latest, outcome)
                with self._lock:
                    _remember(scope.finished, job["job_id"], _MAX_FINISHED)
                counts["completed"] += outcome.state != "exhausted"
            elif outcome.state in ("pending", "published_unknown"):
                counts["pending"] += 1
        scope.after = None if page["at_end"] else page["after"]
        counts["more"] = not page["at_end"]
        return counts

    def available_for(self, path: str) -> bool:
        binding = self._lookup(path)
        return binding is not None and binding.contains_path(path)

    def can_announce_for_import(self, folder: str) -> bool:
        if self.available_shares is None:
            return self._lookup(folder) is not None
        normalized = os.path.normcase(os.path.normpath(folder))
        return any(
            normalized == os.path.normcase(os.path.normpath(binding.source_path))
            or binding.contains_path(folder)
            or _relative_path(binding.source_path, folder) is not None
            for binding in self.available_shares()
        )
