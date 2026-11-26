"""Coordinate independently authorized local mappings of shared folders."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
import logging
import re
import sqlite3
from threading import RLock
from uuid import uuid4

from ..config import get_data_root
from .shared_sync_io import SharedSyncLane
from .shared_sync_preview import SharedLibraryPreview, contains_path


logger = logging.getLogger("face_manager.shared_sync")
_LEGACY = "library"
_ID = re.compile(r"[a-f0-9]{32}")
_MAX_FOLDERS = 8
_MAX_LINKS = 128
_CONSENT = ("write_enabled", "share_embeddings", "paused", "assist_paused")


class SharedLibraries:
    def __init__(self) -> None:
        self._lock = RLock()
        self._manifest_lock = RLock()
        self._manifest_owners = {}
        self._profiles = {}
        self._analysis_requests = {}
        self._folder_probe = SharedSyncLane(publish_capable=False)
        self._started = False
        self._restoring = False

    available = staticmethod(SharedLibraryPreview.available)
    write_available = staticmethod(SharedLibraryPreview.write_available)
    analysis_available = staticmethod(SharedLibraryPreview.analysis_available)

    def _profile(self, folder_id: str) -> SharedLibraryPreview:
        path = "shared-library-preview.json" if folder_id == _LEGACY else f"shared-libraries/{folder_id}.json"

        def claim(manifest):
            key = manifest["scope"], manifest["generation"]
            with self._manifest_lock:
                return self._manifest_owners.setdefault(key, {folder_id}) == {folder_id}

        return SharedLibraryPreview(path, claim_manifest=claim, trusted_share=True)

    def _forget_manifest(self, folder_id: str) -> None:
        with self._manifest_lock:
            self._manifest_owners = {key: owners - {folder_id} for key, owners in self._manifest_owners.items()
                                     if owners - {folder_id}}

    def start(self) -> None:
        if not self.available():
            return
        with self._lock:
            if self._started:
                return
            try:
                SharedLibraryPreview._journal().initialize()
            except (OSError, sqlite3.Error):
                logger.warning("Could not prepare the shared-folder journal", exc_info=True)
            folder_ids = [path.stem for path in sorted((get_data_root() / "shared-libraries").glob("*.json"))
                          if _ID.fullmatch(path.stem)]
            for folder_id in (_LEGACY, *folder_ids):
                profile = self._profile(folder_id)
                profile.load()
                if profile.snapshot()["source_path"]:
                    self._profiles[folder_id] = profile
            journal = SharedLibraryPreview._journal()
            try:
                for folder_id, profile in self._profiles.items():
                    if manifest := journal.bound_manifest(SharedLibraryPreview._scope(profile.snapshot())):
                        key = manifest["scope"], manifest["generation"]
                        self._manifest_owners.setdefault(key, set()).add(folder_id)
            except sqlite3.Error:
                logger.warning("Could not read previous shared-folder ownership", exc_info=True)
            self._rebuild_exclusions()
            self._started = True
            for profile in self._profiles.values():
                profile.start(reload=False)

    def stop(self) -> None:
        with self._lock:
            self._started = False
            for profile in self._profiles.values():
                profile._stop.set()
                profile._wake.set()
            for profile in self._profiles.values():
                profile.stop()
            self._profiles.clear()
            with self._manifest_lock:
                self._manifest_owners.clear()

    def _snapshot(self) -> dict:
        folders = []
        for folder_id, profile in self._profiles.items():
            state = profile.snapshot()
            cases = [
                {**case, "id": f"{folder_id}.{case['id']}", "folder_id": folder_id, "source_path": state["source_path"]}
                for case in state.get("conflicts", ())
            ]
            folders.append({**state, "folder_id": folder_id, "conflicts": cases})
        if not folders:
            return {**self._profile(_LEGACY).snapshot(), "folders": []}
        return {
            **folders[0],
            "folders": folders,
            "conflict_count": sum(state.get("conflict_count", 0) for state in folders),
            "conflicts": [case for state in folders for case in state["conflicts"]],
        }

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    @contextmanager
    def local_face_history_allowed(self):
        """Keep enabling shared writes and local history replay mutually exclusive."""
        if not self.write_available():
            yield True
            return
        with self._lock:
            states = (profile.snapshot() for profile in self._profiles.values())
            yield not any(state["source_path"] and state["write_enabled"] for state in states)

    def _rebuild_exclusions(self) -> None:
        states = {key: profile.snapshot() for key, profile in self._profiles.items()}
        for key, profile in self._profiles.items():
            state = states[key]
            boundaries = set(state["blocked_paths"])
            boundaries.update(
                other["source_path"] for other_key, other in states.items()
                if other_key != key and contains_path(state["source_path"], other["source_path"])
            )
            profile.set_exclusions(tuple(sorted(boundaries)))

    def _protect_ancestors(self, folder_id, source) -> None:
        for key, parent in self._profiles.items():
            state = parent.snapshot()
            if key == folder_id or not contains_path(state["source_path"], source):
                continue
            paths = state["blocked_paths"]
            if source in paths:
                continue
            if len(paths) >= _MAX_LINKS:
                raise ValueError("In diesem Fotoordner sind bereits zu viele getrennte Unterordner hinterlegt.")
            parent.configure(state, blocked_paths=[*paths, source])

    def _get(self, case_id):
        folder_id, separator, local_id = case_id.partition(".") if isinstance(case_id, str) else ("", "", "")
        profile = self._profiles.get(folder_id) if separator else next(iter(self._profiles.values()), None)
        return profile, local_id if separator else folder_id

    def select_conflict(self, case_id, case_revision, choice) -> dict:
        with self._lock:
            profile, local_id = self._get(case_id)
            if profile is None:
                raise ValueError("Der Fall ist gerade nicht verfügbar.")
            profile.select_conflict(local_id, case_revision, choice)
            return self._snapshot()

    def conflict_evidence(self, case_id: str, case_revision: str):
        with self._lock:
            profile, local_id = self._get(case_id)
        return profile.conflict_evidence(local_id, case_revision) if profile else None

    def contains_source(self, path: str) -> bool:
        with self._lock:
            return any(profile.contains_source(path) for profile in self._profiles.values())

    def analysis_share(self, path: str):
        with self._lock:
            if self._restoring:
                return None
            return next((profile.analysis_share() for profile in self._profiles.values() if profile.contains_source(path)), None)

    def analysis_shares(self) -> tuple:
        with self._lock:
            if self._restoring:
                return ()
            return tuple(share for profile in self._profiles.values() if (share := profile.analysis_share()) is not None)

    def note_analysis_peer(self, binding, name) -> None:
        with self._lock:
            for profile in self._profiles.values():
                if (current := profile.analysis_share()) and current.session == binding.session:
                    profile.note_analysis_peer(name)
                    return

    def dispatch_analysis(self, binding, lane, payload, *, staging_root=None):
        """Fence pending and active analysis IPC against source changes and restore."""
        token = object()
        with self._lock:
            candidates = self.analysis_shares()
            current = next((share for share in candidates if replace(share, assist_paused=False) == binding), None)
            if current is None or (current.assist_paused and payload.get("operation") in ("claim", "expire")):
                return {"status": "unavailable"}
            self._analysis_requests[token] = (lane, binding.source_path, True)
        try:
            return lane.request(payload, staging_root=staging_root)
        finally:
            with self._lock:
                if lane.process.busy:
                    self._analysis_requests[token] = (lane, binding.source_path, False)
                else:
                    self._analysis_requests.pop(token, None)

    def _analysis_busy(self, source=None) -> bool:
        active_lanes = {lane for lane, _, active in self._analysis_requests.values() if active}
        for token, (lane, _, active) in tuple(self._analysis_requests.items()):
            if not active and lane not in active_lanes:
                lane.process.poll()
                if not lane.process.busy:
                    self._analysis_requests.pop(token, None)
        return any(source is None or contains_path(root, source) or contains_path(source, root)
                   for _, root, _ in self._analysis_requests.values())

    def library_changed(self, *, restoring=None) -> None:
        with self._lock:
            if restoring is not None:
                self._restoring = restoring
            for profile in self._profiles.values():
                profile.library_changed(restoring=restoring)

    def begin_restore(self) -> bool:
        with self._lock, ExitStack() as locks:
            for profile in self._profiles.values():
                locks.enter_context(profile._lock)
            if self._restoring or self._analysis_busy() or any(profile._preparing_backup or (profile._mutating and profile._process.busy)
                                      for profile in self._profiles.values()):
                return False
            try:
                if SharedLibraryPreview._journal().publications_pending():
                    return False
            except sqlite3.Error:
                return False
            self.library_changed(restoring=True)
            return True

    def prepare_replacement(self) -> None:
        with self._lock:
            if self._restoring:
                SharedLibraryPreview._journal().rotate_producers()

    @staticmethod
    def _link_compatible(child, parent) -> bool:
        return (
            contains_path(parent["source_path"], child["source_path"])
            and child["sync_path"] in (child["source_path"], parent["sync_path"])
            and all(child[key] == parent[key] for key in _CONSENT)
        )

    @classmethod
    def _ordinary_link(cls, child, parent, witnessed) -> bool:
        if not cls._link_compatible(child, parent):
            return False
        if child["source_path"] == parent["source_path"]:
            return True
        protected = [path for path in parent.get("blocked_paths", ()) if contains_path(path, child["source_path"])]
        if protected:
            return protected == [child["source_path"]] and child["sync_path"] == parent["sync_path"]
        return child["sync_path"] == parent["sync_path"] or (parent["source_path"], child["source_path"]) in witnessed

    def _inspect_links(self, desired) -> set[tuple[str, str]]:
        if not desired["source_path"]:
            return set()
        with self._lock:
            existing = [profile.snapshot() for profile in self._profiles.values()]
        candidates = {
            (parent["source_path"], child["source_path"])
            for state in existing for parent, child in ((state, desired), (desired, state))
            if parent["source_path"] != child["source_path"]
            and child["sync_path"] != parent["sync_path"] and self._link_compatible(child, parent)
        }
        return {
            (parent, child) for parent, child in sorted(candidates)
            if self._folder_probe.request({"action": "inspect_nested_scope", "parent": parent, "child": child}).get("status")
            == "ordinary_child"
        }

    def _require_idle_parent(self, path: str, states, folder_id: str) -> None:
        journal = SharedLibraryPreview._journal()
        for other, state in states.items():
            if other != folder_id and contains_path(state["source_path"], path):
                if self._profiles[other]._publication_pending(journal, SharedLibraryPreview._scope(state)):
                    raise ValueError("Warte auf den laufenden Abgleich des übergeordneten Ordners.")

    def configure(self, raw: dict) -> dict:
        desired = SharedLibraryPreview._validated(raw)
        witnessed = self._inspect_links(desired) if raw.get("folder_id") == "new" else set()
        with self._lock, ExitStack() as locks:
            if self._restoring:
                raise ValueError("Die Fotobibliothek wird gerade wiederhergestellt.")
            for profile in self._profiles.values():
                locks.enter_context(profile._lock)
            folder_id = raw.get("folder_id", next(iter(self._profiles), _LEGACY))
            if not isinstance(folder_id, str) or (
                folder_id not in self._profiles and folder_id not in (_LEGACY, "new")
            ):
                raise ValueError("Der gemeinsame Ordner wurde inzwischen entfernt.")
            profile = self._profiles.get(folder_id)
            if "remove_linked_path" in raw:
                if profile is None:
                    raise ValueError("Der gemeinsame Ordner wurde inzwischen entfernt.")
                state = profile.snapshot()
                path = raw["remove_linked_path"]
                profile.configure(state, linked_paths=[link for link in state["linked_paths"] if link != path])
                return self._snapshot()

            source = desired["source_path"]
            if source is None:
                if profile is not None:
                    old_source = profile.snapshot()["source_path"]
                    if self._analysis_busy(old_source):
                        raise ValueError("Warte auf den laufenden Analyseauftrag, bevor du den Ordner entfernst.")
                    self._protect_ancestors(folder_id, old_source)
                    profile.configure(raw)
                    profile._stop.set()
                    del self._profiles[folder_id]
                    self._forget_manifest(folder_id)
                    self._rebuild_exclusions()
                return self._snapshot()

            states = {key: item.snapshot() for key, item in self._profiles.items()}
            if profile is None:
                parents = sorted(
                    ((key, state) for key, state in states.items() if self._ordinary_link(desired, state, witnessed)),
                    key=lambda pair: len(pair[1]["source_path"]), reverse=True,
                )
                if parents:
                    parent_id, parent = parents[0]
                    links = parent["linked_paths"]
                    blocked = parent["blocked_paths"]
                    if source != parent["source_path"] and (source not in links or source in blocked):
                        if source in blocked and self._analysis_busy(source):
                            raise ValueError("Warte auf den laufenden Analyseauftrag dieses Ordners.")
                        if source not in links and len(links) >= _MAX_LINKS:
                            raise ValueError("Für diesen Fotoordner sind bereits zu viele Unterordner verknüpft.")
                        self._profiles[parent_id].configure(
                            parent, linked_paths=list(dict.fromkeys([*links, source])),
                            blocked_paths=[path for path in blocked if path != source],
                        )
                        self._rebuild_exclusions()
                    return self._snapshot()
                journal = SharedLibraryPreview._journal()
                for child_id, child in states.items():
                    if not self._ordinary_link(child, desired, witnessed) or child["source_path"] == source:
                        continue
                    if any(
                        key != child_id and contains_path(state["sync_path"], desired["sync_path"])
                        and contains_path(desired["sync_path"], state["sync_path"])
                        for key, state in states.items()
                    ):
                        continue
                    scope = SharedLibraryPreview._scope(child)
                    child_profile = self._profiles[child_id]
                    if child_profile._mutating or child_profile._publication_pending(journal, scope) or journal.bound_manifest(scope):
                        continue
                    if self._analysis_busy(source):
                        raise ValueError("Warte auf den laufenden Analyseauftrag dieses Ordners.")
                    self._require_idle_parent(source, states, child_id)
                    links = list(dict.fromkeys([child["source_path"], *child["linked_paths"]]))[:_MAX_LINKS]
                    child_profile.configure(raw, linked_paths=links, blocked_paths=child["blocked_paths"])
                    self._forget_manifest(child_id)
                    self._rebuild_exclusions()
                    return self._snapshot()
                if len(self._profiles) >= _MAX_FOLDERS:
                    raise ValueError("Entferne zuerst einen der acht eingerichteten gemeinsamen Ordner.")
            for key, state in states.items():
                if key != folder_id and (
                    contains_path(state["source_path"], source) and contains_path(source, state["source_path"])
                    or contains_path(state["sync_path"], desired["sync_path"]) and contains_path(desired["sync_path"], state["sync_path"])
                ):
                    raise ValueError("Dieser Foto- oder Abgleichordner ist bereits eingerichtet.")

            old = states.get(folder_id)
            changed_path = not old or any(desired[key] != old[key] for key in ("source_path", "sync_path"))
            changes_access = old and any(desired[key] != old[key] for key in ("write_enabled", "share_embeddings", "paused"))
            if (changed_path or changes_access) and (
                self._analysis_busy(source) or (old and self._analysis_busy(old["source_path"]))
            ):
                raise ValueError("Warte auf den laufenden Analyseauftrag, bevor du die Freigabe änderst.")
            if changed_path:
                self._require_idle_parent(source, states, folder_id)
                if old and old["source_path"] != source:
                    self._protect_ancestors(folder_id, old["source_path"])
            if profile is None:
                folder_id = _LEGACY if folder_id == _LEGACY else uuid4().hex
                profile = self._profile(folder_id)
            profile.configure(raw)
            self._profiles[folder_id] = profile
            if changed_path:
                self._forget_manifest(folder_id)
            self._rebuild_exclusions()
            if self._started:
                profile.start(reload=False)
            return self._snapshot()


shared_library_preview = SharedLibraries()
