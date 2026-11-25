"""Connect imports to a qualified, explicitly opted-in shared analysis scope."""

from dataclasses import dataclass, replace
from threading import Lock

from .shared_sync_analysis import SharedAnalysisExchange
from .shared_sync_io import SharedSyncLane
from .shared_sync_snapshot import _relative_path


@dataclass(frozen=True)
class ImportShare:
    source_path: str
    sync_path: str
    manifest: dict
    producer: str
    session: str
    fresh_reads: bool = False
    assist_paused: bool = False


class SharedImportAnalysis:
    """Reuse compatible results for imports without running SMB calls in SQLite."""

    def __init__(self, current_share):
        self.current_share = current_share
        self._lock = Lock()
        self._work = SharedSyncLane()
        self._heartbeat = SharedSyncLane()
        self._binding = None
        self._exchange = None
        self._reader = None

    def _make_exchange(self, binding: ImportShare, *, allow_writes: bool):
        def request_for(lane):
            def request(payload, *, staging_root=None):
                current = self.current_share()
                if current is None or replace(current, assist_paused=False) != binding:
                    return {"status": "unavailable"}
                # Allow an already owned attempt to finish, without taking on
                # work started after the user paused this device's assistance.
                if current.assist_paused and payload.get("operation") in ("claim", "expire"):
                    return {"status": "unavailable"}
                return lane.request(payload, staging_root=staging_root)
            return request

        return SharedAnalysisExchange(
            request=request_for(self._work), heartbeat_request=request_for(self._heartbeat),
            sync_root=binding.sync_path, source_root=binding.source_path,
            manifest=binding.manifest, device_id=binding.producer, session_id=binding.session,
            allow_writes=allow_writes,
        )

    def _analyzer(self, binding: ImportShare):
        with self._lock:
            scope = replace(binding, assist_paused=False)
            if self._binding != scope:
                self._binding = scope
                self._exchange = self._make_exchange(scope, allow_writes=True)
                self._reader = self._make_exchange(scope, allow_writes=False)
            return self._reader if binding.assist_paused else self._exchange

    def analyze(self, model, path: str, photo_sha256: str):
        binding = self.current_share()
        if binding is None or (relative := _relative_path(path, binding.source_path)) is None:
            return None
        outcome = self._analyzer(binding).analyze_or_reuse(
            model, relative, photo_sha256, server_fresh=binding.fresh_reads,
        )
        current = self.current_share()
        if current is None or replace(current, assist_paused=False) != replace(binding, assist_paused=False):
            return None
        return outcome

    def available_for(self, path: str) -> bool:
        binding = self.current_share()
        return binding is not None and _relative_path(path, binding.source_path) is not None
