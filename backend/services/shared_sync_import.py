"""Connect imports to a qualified, explicitly opted-in shared analysis scope."""

from dataclasses import dataclass
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


class SharedImportAnalysis:
    """Reuse compatible results for imports without running SMB calls in SQLite."""

    def __init__(self, current_share):
        self.current_share = current_share
        self._lock = Lock()
        self._work = SharedSyncLane()
        self._heartbeat = SharedSyncLane()
        self._binding = None
        self._exchange = None

    def _analyzer(self, binding: ImportShare):
        with self._lock:
            if self._binding != binding:
                self._binding = binding

                def request_for(lane):
                    def request(payload, *, staging_root=None):
                        if self.current_share() != binding:
                            return {"status": "unavailable"}
                        return lane.request(payload, staging_root=staging_root)
                    return request

                self._exchange = SharedAnalysisExchange(
                    request=request_for(self._work), heartbeat_request=request_for(self._heartbeat),
                    sync_root=binding.sync_path, source_root=binding.source_path,
                    manifest=binding.manifest, device_id=binding.producer, session_id=binding.session,
                    allow_writes=True,
                )
            return self._exchange

    def analyze(self, model, path: str, photo_sha256: str):
        binding = self.current_share()
        if binding is None or (relative := _relative_path(path, binding.source_path)) is None:
            return None
        outcome = self._analyzer(binding).analyze_or_reuse(
            model, relative, photo_sha256, server_fresh=binding.fresh_reads,
        )
        return outcome if binding == self.current_share() else None

    def available_for(self, path: str) -> bool:
        binding = self.current_share()
        return binding is not None and _relative_path(path, binding.source_path) is not None
