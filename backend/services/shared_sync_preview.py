"""Background library comparison and explicitly enabled write trials."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..config import get_data_root
from ..db import schema
from ..db.schema import get_conn
from .cache import app_cache
from .desktop import normalize_import_folder_path
from .events import event_hub, publish_cluster_change
from .shared_sync_apply import (
    apply_unambiguous, compare_for_scope, confirmed_conflict_head, is_competing_assignment_available, is_conflict_person_available,
    resolve_competing_face_conflict, resolve_face_conflict, scoped_snapshot,
)
from .shared_sync_conflicts import (
    ConflictEvidencePending, apply_verified_choice, build_competing_conflict_case, build_conflict_case,
    reserve_conflict_guard, stage_conflict_choice, validate_conflict_packet, verified_competing_parents,
    verified_current_parents,
)
from .shared_sync_io import SharedSyncLane, SharedSyncProcess
from .shared_sync_import import ImportShare
from .shared_sync_journal import ExchangeJournal, digest, encode
from .shared_sync_outbox import (
    DirtyImage, acknowledge_changes, enqueue_baseline_page, initialize_outbox, pending_changes, register_scope,
    retire_scope, revisions_match,
)
from .shared_sync_reconcile import compare_verified_photo, matching_source_path, photo_event
from .shared_sync_resolution import record_resolution, shared_library_scope
from .shared_sync_snapshot import capture_image
from .shared_sync_transport import MAX_EVENT_BATCH_BYTES
from .shared_sync_worker import MAX_SOURCE_CHECKS
from .storage import invalidate_image_query_cache


logger = logging.getLogger("face_manager.shared_sync")
_MAX_VISIBLE_CASES = 100


def contains_path(root: str | None, path: str) -> bool:
    """Compare complete local path components without touching the share."""
    if root is None or not os.path.isabs(path):
        return False
    root = os.path.normcase(os.path.normpath(root))
    try:
        return os.path.commonpath((os.path.normcase(os.path.normpath(path)), root)) == root
    except ValueError:
        return False


def _decision(face, device: str) -> dict:
    if face is None:
        return {"kind": "missing", "device": device}
    decision = face.decision
    if decision is None:
        return {"kind": "detected", "device": device}
    return {
        "kind": decision.archive_status if decision.kind == "archived" else decision.kind,
        "person_name": decision.person_name, "device": device,
    }


class SharedLibraryPreview:
    """Keep status reads and local writes independent of shared filesystem I/O."""

    def __init__(self, config_path: str = "shared-library-preview.json", *, claim_manifest=None, trusted_share=False) -> None:
        self._config_path = config_path
        self._claim_manifest = claim_manifest
        self._trusted_share = trusted_share
        self._excluded_paths = ()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._process = SharedSyncProcess(publish_capable=True)
        self._device = socket.gethostname()[:255]
        self._config = {
            "source_path": None, "sync_path": None, "paused": False,
            "share_embeddings": False, "write_enabled": False, "initialize_share": False, "assist_paused": False,
            "linked_paths": [], "blocked_paths": [],
        }
        self._status = {"status": "off"}
        self._revision = 0
        self._restoring = False
        self._mutating = False
        self._analysis_share = None
        self._analysis_peer_names = OrderedDict()
        self._visible_choices = {}
        self._selected_choice = None
        self._visible_media = {}
        self._media_cache = OrderedDict()
        self._media_lane = SharedSyncLane(publish_capable=False)
        self._media_request_lock = threading.Lock()
        self._preparing_backup = False
        self._baseline_backup = None

    @staticmethod
    def available() -> bool:
        return os.environ.get("FACE_MANAGER_SHARED_LIBRARY_PREVIEW") == "1"

    @staticmethod
    def write_available() -> bool:
        return SharedLibraryPreview.available() and os.environ.get("FACE_MANAGER_SHARED_LIBRARY_WRITE_TEST") == "1"

    @staticmethod
    def analysis_available() -> bool:
        return SharedLibraryPreview.write_available() and os.environ.get("FACE_MANAGER_SHARED_LIBRARY_ANALYSIS_TEST") == "1"

    def analysis_share(self):
        with self._lock:
            if self._restoring or self._config["paused"] or not self._config["share_embeddings"]:
                return None
            return replace(
                self._analysis_share, assist_paused=self._config["assist_paused"], excluded_roots=self._excluded_paths,
            ) if self._analysis_share else None

    def contains_source(self, path: str) -> bool:
        """Check configured membership without opening a network directory."""
        with self._lock:
            source = self._config["source_path"]
            excluded = self._excluded_paths
        return contains_path(source, path) and not any(contains_path(boundary, path) for boundary in excluded)

    def set_exclusions(self, excluded: tuple[str, ...]) -> None:
        with self._lock:
            if self._excluded_paths != excluded:
                self._excluded_paths = excluded
                self._analysis_share = None
                self.library_changed()

    def _in_scope(self, source: str, relatives) -> bool:
        with self._lock:
            excluded = self._excluded_paths
        return not any(
            contains_path(boundary, os.path.join(source, *relative.split("/")))
            for relative in relatives for boundary in excluded
        )

    def _adopt_manifest(self, manifest: dict, revision: int) -> bool:
        with self._lock:
            if not self._current(revision):
                return False
            if self._claim_manifest and not self._claim_manifest(manifest):
                self._update(revision, status="attention", error="Dieser gemeinsame Stand ist bereits unter einem anderen Pfad eingerichtet.")
                return False
            return True

    def snapshot(self) -> dict:
        with self._lock:
            visible = {
                **self._config, **self._status,
                "available": self.available(), "preview": not self._config["write_enabled"],
                "write_available": self.write_available(), "analysis_available": self.analysis_available(),
                "device_name": self._device,
            }
            if self._selected_choice is not None:
                case_id = self._selected_choice[0]
                visible["resolution_pending"] = True
                visible["conflicts"] = [
                    {**case, "resolution_pending": True} if case["id"] == case_id else case
                    for case in visible.get("conflicts", [])
                ]
            if self._analysis_peer_names:
                visible["peer_names"] = sorted(set(visible.get("peer_names", ())) | set(self._analysis_peer_names), key=str.casefold)
            return visible

    def note_analysis_peer(self, name: str) -> None:
        with self._lock:
            if name != self._device:
                self._analysis_peer_names[name] = None
                self._analysis_peer_names.move_to_end(name)
                if len(self._analysis_peer_names) > 256:
                    self._analysis_peer_names.popitem(last=False)

    def select_conflict(self, case_id, case_revision, choice) -> dict:
        """Queue a witnessed choice. The request handler does not access the share."""
        with self._lock:
            offered = self._visible_choices.get(case_id) if isinstance(case_id, str) else None
            if (
                self._status.get("status") != "online" or self._restoring or not offered
                or offered[0].revision != case_revision or choice not in ("local", "shared")
                or self._selected_choice is not None or not offered[2 if choice == "shared" else 3]
            ):
                raise ValueError("Der Fall ist gerade nicht verfügbar. Lade ihn neu und prüfe beide Entscheidungen.")
            self._selected_choice = (case_id, case_revision, choice, *offered)
            self._wake.set()
            return self.snapshot()

    def conflict_evidence(self, case_id: str, case_revision: str) -> dict | None:
        with self._lock:
            media = self._visible_media.get(case_id)
            if self._restoring or media is None or media[0] != case_revision:
                return None
            revision = self._revision
            key = (revision, case_id, case_revision)
            if cached := self._media_cache.get(key):
                self._media_cache.move_to_end(key)
                return cached
            if self._status.get("status") not in ("online", "read_only"):
                return None
        if not self._media_request_lock.acquire(blocking=False):
            return None
        try:
            result = self._media_lane.request(media[1])
        finally:
            self._media_request_lock.release()
        with self._lock:
            if (
                self._revision != revision or self._visible_media.get(case_id) != media
                or result.get("status") != "ready" or result.get("photo_sha256") != media[1]["expected_hash"]
            ):
                return None
            images = result["images"]
            self._media_cache[key] = images
            if len(self._media_cache) > 6:
                self._media_cache.popitem(last=False)
            return images

    def _update(self, revision: int, **fields) -> None:
        with self._lock:
            if revision == self._revision and not self._restoring:
                self._status = {**self._status, **fields}
                if fields.get("status") in ("off", "offline", "read_only", "attention"):
                    self._analysis_share = None

    def _current(self, revision: int) -> bool:
        with self._lock:
            return revision == self._revision and not self._restoring and not self._config["paused"]

    def library_changed(self, *, restoring: bool | None = None) -> None:
        if not self.available() and restoring is None:
            return
        with self._lock:
            if restoring is not None:
                self._restoring = restoring
                self._analysis_share = None
            if self._config["source_path"]:
                self._revision += 1
                self._visible_choices = {}
                self._selected_choice = None
                self._visible_media.clear()
                self._media_cache.clear()
                self._status = {"status": "paused" if self._config["paused"] else "checking"}
                self._wake.set()

    @staticmethod
    def _scope(config: dict) -> str:
        return digest([os.path.normcase(config["source_path"]), os.path.normcase(config["sync_path"])])

    @staticmethod
    def _journal() -> ExchangeJournal:
        return ExchangeJournal(get_data_root() / "shared-library-witness.sqlite")

    def _publication_pending(self, journal: ExchangeJournal, scope: str) -> bool:
        return bool(
            (self._mutating and self._process.busy)
            or journal.checkpoint(scope)["outgoing"]
            or journal.acknowledged_local_rows(scope)
        )

    def begin_restore(self) -> bool:
        """Fence the local library switch while a publication outcome is outstanding."""
        with self._lock:
            try:
                if self._preparing_backup or (self._mutating and self._process.busy) or self._journal().publications_pending():
                    return False
            except sqlite3.Error:
                return False
            self.library_changed(restoring=True)
            return True

    def prepare_replacement(self) -> None:
        with self._lock:
            if self._restoring:
                # Persist the new identity before the database swap can succeed.
                self._journal().rotate_producers()

    @staticmethod
    def _validated(raw: dict) -> dict:
        paths = []
        for name in ("source_path", "sync_path"):
            value = raw.get(name)
            if value is not None and (not isinstance(value, str) or len(value) > 4096 or "\0" in value):
                raise ValueError("Die Ordnerpfade sind ungültig.")
            value = normalize_import_folder_path(value) if value and value.strip() else None
            if value is not None and (not os.path.isabs(value) or any(part.casefold() == ".fm" for part in Path(value).parts)):
                raise ValueError("Wähle den übergeordneten Ordner als absoluten Pfad aus.")
            paths.append(value)
        source, sync = paths
        if source is None and sync is not None:
            raise ValueError("Wähle zuerst den Fotoordner.")
        sync = sync or source
        paused, share_embeddings = raw.get("paused", False), raw.get("share_embeddings", False)
        write_enabled = raw.get("write_enabled", False)
        initialize_share = raw.get("initialize_share", False)
        assist_paused = raw.get("assist_paused", False)
        if any(type(value) is not bool for value in (paused, share_embeddings, write_enabled, initialize_share, assist_paused)):
            raise ValueError("Die Einstellungen sind ungültig.")
        if share_embeddings and not (write_enabled and SharedLibraryPreview.analysis_available()):
            raise ValueError("Der gemeinsame Abgleich von Gesichtsmerkmalen ist für diesen Ordner nicht verfügbar.")
        if write_enabled and not (source and SharedLibraryPreview.write_available()):
            raise ValueError("Schreibender Abgleich ist für diesen Ordner nicht verfügbar.")
        if initialize_share and not write_enabled:
            raise ValueError("Für einen neuen gemeinsamen Stand muss der Abgleichordner beschreibbar sein.")
        return {
            "source_path": source, "sync_path": sync, "paused": paused,
            "share_embeddings": share_embeddings, "write_enabled": write_enabled, "initialize_share": initialize_share,
            "assist_paused": assist_paused and share_embeddings,
        }

    def _save_config(self, config: dict) -> None:
        path = get_data_root() / self._config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="shared-config-", dir=path.parent) as directory:
            temporary = Path(directory) / "config.json"
            temporary.write_text(encode(config), encoding="utf-8")
            os.replace(temporary, path)

    def _consume_initialization(self, revision: int) -> bool:
        """Persist the consumed authorization before a filesystem mutation can start."""
        with self._lock:
            if not self._current(revision) or not self._config["initialize_share"]:
                return False
            config = {**self._config, "initialize_share": False}
            self._save_config(config)
            self._config = config
            return True

    def configure(self, raw: dict, *, linked_paths=None, blocked_paths=None) -> dict:
        config = self._validated(raw)
        with self._lock:
            if self._restoring:
                raise ValueError("Die Fotobibliothek wird gerade wiederhergestellt.")
            config["linked_paths"] = list(linked_paths if linked_paths is not None else (
                self._config["linked_paths"] if config["source_path"] == self._config["source_path"] else ()
            ))
            config["blocked_paths"] = list(blocked_paths if blocked_paths is not None else (
                self._config["blocked_paths"] if config["source_path"] == self._config["source_path"] else ()
            ))
            if (
                "initialize_share" not in raw and config["write_enabled"] and self._config["initialize_share"]
                and (config["source_path"], config["sync_path"]) == (self._config["source_path"], self._config["sync_path"])
            ):
                config["initialize_share"] = True
            retiring = None
            if self._config["write_enabled"] and (
                not config["write_enabled"] or config["source_path"] != self._config["source_path"]
                or config["sync_path"] != self._config["sync_path"]
            ):
                journal, scope = self._journal(), self._scope(self._config)
                if self._publication_pending(journal, scope):
                    raise ValueError("Warte auf die Bestätigung der laufenden Freigabeänderung, bevor du den Ordner wechselst.")
                previous = journal.checkpoint(scope)
                manifest = previous["known_manifest"]
                if manifest:
                    decision_scope = digest([scope, manifest["scope"], manifest["generation"]])
                    retiring = digest([decision_scope, previous["producer_id"]])
            self._save_config(config)
            if any(config[key] != self._config[key] for key in ("source_path", "sync_path")):
                self._analysis_peer_names.clear()
            if any(config[key] != self._config[key] for key in ("source_path", "sync_path", "paused", "write_enabled", "share_embeddings")):
                self._analysis_share = None
            self._config = config
            self._visible_choices = {}
            self._selected_choice = None
            self._visible_media.clear()
            self._media_cache.clear()
            self._revision += 1
            self._status = {"status": "paused" if config["paused"] else "checking" if config["source_path"] else "off"}
            self._wake.set()
            if retiring:
                try:
                    with closing(get_conn()) as connection, connection:
                        connection.execute("BEGIN IMMEDIATE")
                        initialize_outbox(connection)
                        retire_scope(connection, retiring)
                except sqlite3.Error:
                    logger.warning("Could not retire the previous shared library outbox", exc_info=True)
        return self.snapshot()

    def load(self) -> None:
        try:
            config_path = get_data_root() / self._config_path
            if config_path.is_file():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if not self.write_available():
                    config["write_enabled"] = False
                    config["initialize_share"] = False
                if not self.analysis_available():
                    config["share_embeddings"] = False
                self._config = self._validated(config)
                for key in ("linked_paths", "blocked_paths"):
                    paths = config.get(key, [])
                    self._config[key] = [
                        path for path in paths if isinstance(path, str) and path != self._config["source_path"]
                        and contains_path(self._config["source_path"], path)
                    ][:128] if isinstance(paths, list) else []
                self._status = {"status": "paused" if self._config["paused"] else "checking" if self._config["source_path"] else "off"}
        except (OSError, ValueError, TypeError):
            logger.warning("Could not load the shared library preview configuration")

    def start(self, *, reload=True) -> None:
        if not self.available() or (self._thread is not None and self._thread.is_alive()):
            return
        if reload:
            self.load()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="shared-library-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._process.stop(timeout_seconds=1.5)
            self._thread.join(timeout=0.5)

    def _request(self, req: dict, revision: int, *, before_start: Callable[[], bool] | None = None) -> dict:
        if not self._current(revision):
            return {"status": "interrupted"}
        if self._process.busy:
            self._process.poll()
            return {"status": "unavailable"}
        self._process.poll()  # A stop may leave a completion pending.
        with TemporaryDirectory(prefix="shared-request-") as directory:
            pathname = Path(directory) / "request.json"
            pathname.write_text(encode(req), encoding="utf-8")
            with self._lock:
                if self._revision != revision or self._restoring or self._config["paused"]:
                    return {"status": "interrupted"}
                if before_start is not None and not before_start():
                    return {"status": "stale"}
                self._mutating = bool(req.get("allow_writes") and (req["action"] == "probe" or req.get("outgoing")))
                self._process.start([str(pathname)])
            while not self._stop.is_set():
                result = self._process.poll()
                if result is not None:
                    if result.status == "ok":
                        return result.payload
                    uncertain_write = result.status == "published_unknown" and req.get("allow_writes")
                    return {"status": "published_unknown" if uncertain_write else "unavailable"}
                self._stop.wait(0.05)
        return {"status": "unavailable"}

    def _drain_receipt(self, journal, scope, outbox_scope) -> None:
        receipt = journal.acknowledged_local_rows(scope)
        if receipt is None:
            return
        batch_number, rows = receipt
        with closing(get_conn()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            acknowledge_changes(connection, outbox_scope, [DirtyImage(*row) for row in rows])
        journal.forget_local_receipt(scope, batch_number)

    def _backup_before_first_sync(self, manifest: dict, device_id: str, revision: int) -> bool:
        from .shared_sync_backup import ensure_initial_share_backup

        logical_scope = shared_library_scope(manifest)
        key = logical_scope, device_id
        with self._lock:
            if not self._current(revision):
                return False
            if self._baseline_backup == key:
                return True
            self._preparing_backup = True
        try:
            with closing(get_conn()) as connection:
                ensure_initial_share_backup(
                    connection, get_data_root() / "shared-library-backups",
                    library_scope=logical_scope, producer_id=device_id,
                )
        except (OSError, ValueError, sqlite3.Error):
            logger.warning("Could not prepare the local library before shared sync", exc_info=True)
            self._update(revision, status="attention", error="Die bestehende Fotobibliothek konnte vor dem Abgleich nicht gesichert werden.")
            return False
        finally:
            with self._lock:
                self._preparing_backup = False
        with self._lock:
            if not self._current(revision):
                return False
            self._baseline_backup = key
        return True

    def _finish_choice(self, revision, choice, message=None) -> None:
        with self._lock:
            if self._current(revision) and self._selected_choice is choice:
                self._selected_choice = None
                self._update(revision, choice_error=message)

    def _stage_choice(self, journal, scope, outbox_scope, decision_scope, manifest, config, device_id, peer, state, revision):
        with self._lock:
            selected = self._selected_choice
        if selected is None or state.get("staged_choice") is selected:
            return False
        _, expected, side, case, image_id, _, _ = selected
        if not self._in_scope(config["source_path"], (case.relative_path,)):
            self._finish_choice(revision, selected, "Der Fotoordner wurde inzwischen separat eingerichtet.")
            return False
        result = self._request({
            "action": "verify_source", "source_root": config["source_path"],
            "relative_path": case.relative_path, "expected_hash": case.photo_sha256,
        }, revision)
        if result["status"] in ("unavailable", "interrupted"):
            return False
        try:
            with self._lock:
                if not self._current(revision) or self._selected_choice is not selected:
                    return False
                if result.get("photo_sha256") != case.photo_sha256 or peer != case.peer_proof.producer:
                    raise ValueError("Conflict source or peer changed")
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    local = capture_image(connection, config["source_path"], device_id, image_id)
                    if local and not self._in_scope(config["source_path"], local.relative_paths):
                        raise ValueError("Photo belongs to a separately configured folder")
                    chosen = case.local_branch if side == "local" else case.peer_branch
                    parents = ()
                    if case.resolution_proofs:
                        if local is None:
                            raise ValueError("The reviewed photo disappeared")
                        witnessed = validate_conflict_packet(journal, scope, manifest, device_id, peer, device_id, case.packet(chosen.revision))
                        mapped = scoped_snapshot(connection, scope=decision_scope, device_id=device_id, local=local)
                        parents = verified_competing_parents(connection, journal, scope, decision_scope, witnessed, mapped)
                        face = next((face for face in mapped.faces if face.face_id == case.local_proof.face_id), None)
                        if not parents or not self._choice_available(connection, decision_scope, device_id, face, chosen.decision):
                            raise ValueError("The competing decisions changed")
                        fresh = witnessed.case
                    else:
                        fresh = self._current_conflict(journal, scope, decision_scope, manifest, device_id,
                                                       peer, case, local, connection)
                    if fresh is None or fresh.revision != expected:
                        raise ValueError("The reviewed decisions changed")
                    if side == "shared" and not case.resolution_proofs:
                        remote = journal.latest_published_photo(scope, peer, case.photo_sha256).event
                        plan = compare_for_scope(connection, scope=decision_scope, device_id=device_id,
                                                 local=local, remote=remote, verified_hash=case.photo_sha256)
                        if not any(
                            len(issue.local) == len(issue.shared) == 1
                            and issue.local[0].face_id == case.local_proof.face_id
                            and issue.shared[0].face_id == case.peer_proof.face_id
                            and is_conflict_person_available(connection, scope=decision_scope,
                                                             left=issue.local[0], right=issue.shared[0])
                            for issue in plan.conflicts
                        ):
                            raise ValueError("Shared person is no longer available")
                    dirty = reserve_conflict_guard(connection, outbox_scope, image_id)
                    stage_conflict_choice(connection, journal, scope, outbox_scope, fresh, expected, chosen.revision, dirty,
                                          current_parents=parents)
                state["staged_choice"] = selected
                return True
        except ValueError:
            self._finish_choice(revision, selected, "Die geprüften Angaben haben sich geändert. Prüfe den Fall erneut.")
            return False

    @staticmethod
    def _current_conflict(journal, scope, decision_scope, manifest, device_id, peer, case, local, connection):
        if local is None:
            return None
        mapped = scoped_snapshot(connection, scope=decision_scope, device_id=device_id, local=local)
        return build_conflict_case(
            journal, scope, manifest, device_id, peer, mapped, case.local_proof.face_id, case.peer_proof.face_id,
            verified_hash=case.photo_sha256, verified_relative_path=case.relative_path,
        )

    @staticmethod
    def _choice_available(connection, scope, device_id, face, decision) -> bool:
        return face is not None and is_competing_assignment_available(
            connection, scope=scope, device_id=device_id, face=face, decision=decision,
        )

    def _register_competing_case(self, connection, journal, scope, decision_scope, manifest, device_id, peer,
                                 entry, related, mapped, state, source_path):
        for other in related:
            try:
                case = build_competing_conflict_case(journal, scope, manifest, device_id, peer, entry, other)
            except ValueError:
                continue
            witnessed = validate_conflict_packet(journal, scope, manifest, device_id, peer, device_id,
                                                  case.packet(case.local_branch.revision))
            parents = verified_competing_parents(connection, journal, scope, decision_scope, witnessed, mapped)
            face = next((face for face in mapped.faces if face.face_id == case.local_proof.face_id), None)
            local_allowed = bool(parents) and self._choice_available(connection, decision_scope, device_id, face, case.local_branch.decision)
            shared_allowed = bool(parents) and self._choice_available(connection, decision_scope, device_id, face, case.peer_branch.decision)
            case_id = digest(["competing-face-choices", case.subject_id, case.revision])
            original = witnessed.original_case
            left = _decision(replace(face, decision=case.local_branch.decision), self._device) if face else {"kind": "missing", "device": self._device}
            peer_name = witnessed.peer_event.event["device_name"]
            right = _decision(replace(face, decision=case.peer_branch.decision), peer_name) if face else {"kind": "missing", "device": peer_name}
            evidence = None
            if mapped.exif_orientation and mapped.raw_width and mapped.raw_height:
                boxes = {}
                for side, published, proof in (("local", witnessed.local_event, original.local_proof),
                                                ("shared", witnessed.peer_event, original.peer_proof)):
                    box = next((item["bbox"] for item in published.event["faces"] if item["face_id"] == proof.face_id), None)
                    if box is not None:
                        boxes[side] = box
                if len(boxes) == 2:
                    evidence = (case.revision, {
                        "action": "conflict_preview", "source_root": source_path, "relative_path": case.relative_path,
                        "expected_hash": case.photo_sha256, "geometry": (mapped.exif_orientation, mapped.raw_width, mapped.raw_height),
                        "boxes": boxes,
                    })
            visible = {
                "id": case_id, "revision": case.revision, "subject_id": case.subject_id, "evidence_available": evidence is not None,
                "can_resolve": evidence is not None and (local_allowed or shared_allowed),
                "can_keep_local": local_allowed, "can_use_shared": shared_allowed,
                "relative_path": case.relative_path, "reason": "concurrent_choices", "local": left, "shared": right,
            }
            state.setdefault("competing_cases", {})[case.subject_id] = (visible, (case, mapped.image_id, shared_allowed, local_allowed), evidence)
            return

    def _consume_choice(self, journal, scope, decision_scope, manifest, config, device_id, peer, entry, revision, state):
        try:
            witnessed = validate_conflict_packet(journal, scope, manifest, device_id, peer, entry.producer, entry.event, published=entry)
        except ConflictEvidencePending:
            return "retry"
        except ValueError:
            logger.warning("Ignored an unsupported shared conflict decision")
            return "ignored"
        case, resolution = witnessed.case, witnessed.resolution
        if not self._in_scope(config["source_path"], (case.relative_path,)):
            return "ignored"
        try:
            related = journal.resolution_packets_for_photo(scope, case.photo_sha256)
        except ValueError:
            return "concurrent"
        competing = []
        for _, packet in related:
            if packet == entry or packet.producer not in (peer, device_id):
                continue
            try:
                other = validate_conflict_packet(journal, scope, manifest, device_id, peer, packet.producer, packet.event, published=packet)
            except ValueError:
                continue
            if other.resolution.subject_id == resolution.subject_id and other.resolution.parents == resolution.parents:
                competing.append((packet, other.resolution))
        result = self._request({
            "action": "verify_source", "source_root": config["source_path"],
            "relative_path": case.relative_path, "expected_hash": case.photo_sha256,
        }, revision)
        if result["status"] in ("unavailable", "interrupted"):
            return "retry"
        if result.get("photo_sha256") != case.photo_sha256:
            return "stale"
        try:
            with self._lock:
                if not self._current(revision):
                    return "retry"
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute("SELECT id FROM image WHERE content_hash = ?", (case.photo_sha256,)).fetchone()
                    if row is None:
                        return "retry"
                    image_id = row["id"]
                    local = capture_image(connection, config["source_path"], device_id, image_id)
                    if local and not self._in_scope(config["source_path"], local.relative_paths):
                        return "ignored"
                    mapped = scoped_snapshot(connection, scope=decision_scope, device_id=device_id, local=local) if local else None
                    parents = ()
                    if mapped:
                        parents = (
                            verified_competing_parents(connection, journal, scope, decision_scope, witnessed, mapped)
                            if witnessed.original_case else verified_current_parents(journal, scope, witnessed, mapped)
                        )
                    for _, other in competing:
                        record_resolution(connection, other)

                    def write(assignment, event_id):
                        if witnessed.original_case:
                            face = next(face for face in mapped.faces if face.face_id == case.local_proof.face_id)
                            head = confirmed_conflict_head(connection, scope=decision_scope, image_id=image_id,
                                                           device_id=device_id, face=face)
                            resolve_competing_face_conflict(
                                connection, scope=decision_scope, source_path=config["source_path"], device_id=device_id,
                                image_id=image_id, original_remote=witnessed.peer_event.event, verified_hash=case.photo_sha256,
                                verified_relative_path=case.relative_path, local_face_id=case.local_proof.face_id,
                                shared_face_id=case.peer_proof.face_id, expected_local=face.decision, expected_head=head,
                                chosen=assignment, resolution_event_id=event_id,
                            )
                            return
                        resolve_face_conflict(
                            connection, scope=decision_scope, source_path=config["source_path"], device_id=device_id,
                            image_id=image_id, remote=witnessed.peer_event.event, verified_hash=case.photo_sha256,
                            verified_relative_path=case.relative_path, local_face_id=case.local_proof.face_id,
                            shared_face_id=case.peer_proof.face_id, expected_local=case.local_branch.decision,
                            expected_shared=case.peer_branch.decision,
                            choice="local" if resolution.selected_revision == case.local_branch.revision else "shared",
                            resolution_event_id=event_id,
                        )

                    result = apply_verified_choice(connection, witnessed, parents, write)
                    if result == "concurrent" and mapped:
                        self._register_competing_case(connection, journal, scope, decision_scope, manifest, device_id,
                                                      peer, entry, (packet for packet, _ in competing), mapped, state,
                                                      config["source_path"])
                    if result == "applied" and witnessed.original_case:
                        projected = state.setdefault("competing_cases", {})
                        current = projected.get(case.subject_id)
                        if current and current[1][0].revision == case.revision:
                            del projected[case.subject_id]
                    return result
        except ValueError:
            return "stale"

    @staticmethod
    def _local_revisions_match(outbox_scope: str, receipts: list) -> bool:
        with closing(get_conn()) as connection, connection:
            connection.execute("BEGIN")
            return revisions_match(connection, outbox_scope, [DirtyImage(*row) for row in receipts])

    def _prepare_publication(self, journal, scope, outbox_scope, config, request, revision) -> str:
        outgoing = request["outgoing"]
        batch_number = outgoing["batch"]
        local = journal.pending_local_rows(scope, batch_number)
        if local is None:
            return "reconcile"
        attempted, receipts = local
        current = self._local_revisions_match(outbox_scope, receipts)
        if current:
            current = all(self._in_scope(config["source_path"], event["relative_paths"]) for event in outgoing["events"])
        if current:
            for verification in self._verify_outgoing_sources(config["source_path"], outgoing["events"], revision):
                if verification["status"] == "source_changed":
                    current = False
                    break
                if verification["status"] != "verified":
                    return "reconcile" if attempted else "wait"
        with self._lock:
            if not self._current(revision):
                return "wait"
            if current and self._local_revisions_match(outbox_scope, receipts):
                return "publish"
            if attempted:
                return "reconcile"
            journal.discard_unsent(scope, batch_number)
            return "discarded"

    def _mark_publication_started(self, journal, scope, outbox_scope, batch_number: int) -> bool:
        local = journal.pending_local_rows(scope, batch_number)
        if local is None or not self._local_revisions_match(outbox_scope, local[1]):
            return False
        journal.mark_attempted(scope, batch_number)
        return True

    def _verify_outgoing_sources(self, source_root: str, events: list[dict], revision: int):
        check = {"source_root": source_root}
        sources = [
            {"relative_path": event["relative_paths"][0], "expected_hash": event["photo_sha256"]}
            for event in events
        ]
        if 1 < len(sources) <= MAX_SOURCE_CHECKS:
            result = self._request({"action": "verify_sources", "sources": sources, **check}, revision)
            if result.get("status") == "verified_sources" and len(result.get("results", ())) == len(sources):
                yield from result["results"]
                return
        # Slow individual files may fit their own worker deadline even if the
        # combined request could not finish. Keep their original fallback.
        for source in sources:
            yield self._request({"action": "verify_source", **check, **source}, revision)

    def _stage_local(self, journal, scope, outbox_scope, decision_scope, config, device_id, state, revision) -> bool:
        with self._lock:
            if not self._current(revision):
                return False
            with closing(get_conn()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not state.get("outbox_ready"):
                    initialize_outbox(connection)
                    register_scope(connection, outbox_scope)
                more_baseline = enqueue_baseline_page(connection, outbox_scope)
                candidates = pending_changes(connection, outbox_scope, limit=8, after=state.get("outbox_cursor"))
                snapshots = [
                    capture_image(connection, config["source_path"], device_id, change.image_id)
                    for change in candidates
                ]
                snapshots = [
                    scoped_snapshot(connection, scope=decision_scope, device_id=device_id, local=photo) if photo else None
                    for photo in snapshots
                ]
            state["outbox_ready"] = True
            if not candidates:
                state.pop("outbox_cursor", None)
                return more_baseline

        events, receipts, skipped = [], [], []
        for change, photo in zip(candidates, snapshots):
            if not self._current(revision):
                return False
            state["outbox_cursor"] = change
            if photo is None or not self._in_scope(config["source_path"], photo.relative_paths):
                # No scope-approved current record exists. A new local revision will requeue it.
                skipped.append(change)
                continue
            relative = photo.relative_paths[0]
            try:
                event = photo_event(replace(photo, relative_paths=(relative,)), self._device)
                if journal.has_current_photo(scope, device_id, event):
                    skipped.append(change)
                    continue
                if len(encode([*events, event]).encode("utf-8")) > MAX_EVENT_BATCH_BYTES:
                    if events:
                        break
                    raise ValueError("The local photo snapshot exceeds the shared event size limit")
            except ValueError:
                logger.warning("Skipping an unsupported local photo snapshot")
                skipped.append(change)
                continue
            events.append(event)
            receipts.append((change.image_id, change.revision))

        verified = self._verify_outgoing_sources(config["source_path"], events, revision)
        verified_candidates = [
            (event, receipt) for event, receipt, proof in zip(events, receipts, verified)
            if proof.get("status") == "verified" and proof.get("photo_sha256") == event["photo_sha256"]
        ]

        with self._lock:
            if not self._current(revision):
                return False
            if skipped:
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    acknowledge_changes(connection, outbox_scope, skipped)
            if verified_candidates:
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = []
                    for event, receipt in verified_candidates:
                        image_id, captured_revision = receipt
                        row = connection.execute(
                            "SELECT revision FROM shared_sync_dirty_image WHERE scope_id = ? AND image_id = ?",
                            (outbox_scope, image_id),
                        ).fetchone()
                        if row and row[0] == captured_revision:
                            current.append((event, receipt))
                    if current:
                        journal.stage(scope, [event for event, _ in current], local_rows=tuple(row for _, row in current))
        return bool(verified_candidates or skipped or more_baseline)

    def _review(self, journal, scope, producer, batch, source_path, cases, proposed, device_id, revision,
                *, decision_scope=None, apply=False, stats=None, manifest=None) -> bool:
        needs_retry = False
        current_positions = journal.latest_photo_positions(scope, producer, batch)
        for position, remote in enumerate(journal.events(scope, producer, batch)):
            if not self._current(revision) or self._stop.is_set():
                return False
            if position not in current_positions:
                continue
            photo_hash = remote.get("photo_sha256")
            if not isinstance(photo_hash, str):
                continue
            peer = remote.get("device_name")
            if stats is not None and isinstance(peer, str) and 0 < len(peer) <= 255:
                stats.setdefault("peer_names", {})[producer] = peer
            with closing(get_conn()) as connection, connection:
                connection.execute("BEGIN")
                row = connection.execute("SELECT id FROM image WHERE content_hash = ?", (photo_hash,)).fetchone()
                if row is None:
                    continue
                local = capture_image(connection, source_path, device_id, row["id"])
            if local is None:
                continue
            if not self._in_scope(source_path, local.relative_paths):
                continue
            relative = matching_source_path(local, remote)
            if relative is None:
                continue
            result = self._request({
                "action": "verify_source", "source_root": source_path,
                "relative_path": relative, "expected_hash": photo_hash,
            }, revision)
            if not self._current(revision):
                return False
            if result["status"] != "verified" or result.get("photo_sha256") != photo_hash:
                needs_retry = needs_retry or result["status"] in ("unavailable", "interrupted")
                cases.pop((producer, photo_hash), None)
                proposed.pop((producer, photo_hash), None)
                continue
            actionable = {}
            applied = 0
            try:
                with self._lock:
                    if not self._current(revision):
                        return False
                    with closing(get_conn()) as connection, connection:
                        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
                        fresh = capture_image(connection, source_path, device_id, local.image_id)
                        if (fresh is None or relative not in fresh.relative_paths
                                or not self._in_scope(source_path, fresh.relative_paths)):
                            group = producer, photo_hash
                            cases.pop(group, None)
                            proposed.pop(group, None)
                            continue
                        local = fresh
                        if decision_scope is None:
                            plan = compare_verified_photo(local, remote, verified_hash=photo_hash)
                        else:
                            if apply:
                                applied = apply_unambiguous(
                                    connection, scope=decision_scope, source_path=source_path, device_id=device_id,
                                    image_id=local.image_id, remote=remote, verified_hash=photo_hash,
                                    verified_relative_path=relative, remote_producer=producer, remote_sequence=batch,
                                )
                                local = capture_image(connection, source_path, device_id, local.image_id)
                            plan = compare_for_scope(
                                connection, scope=decision_scope, device_id=device_id, local=local,
                                remote=remote, verified_hash=photo_hash,
                            )
                            if apply:
                                mapped = scoped_snapshot(connection, scope=decision_scope, device_id=device_id, local=local)
                                for issue in plan.conflicts:
                                    if len(issue.local) != 1 or len(issue.shared) != 1:
                                        continue
                                    left, right = issue.local[0], issue.shared[0]
                                    case = build_conflict_case(
                                        journal, scope, manifest, device_id, producer, mapped,
                                        left.face_id, right.face_id, verified_hash=photo_hash, verified_relative_path=relative,
                                    )
                                    if case:
                                        allowed = is_conflict_person_available(connection, scope=decision_scope, left=left, right=right)
                                        actionable[left.face_id, right.face_id] = (case, local.image_id, allowed, True)
                if applied:
                    stats["applied"] = stats.get("applied", 0) + applied
                    invalidate_image_query_cache()
                    app_cache.clear()
                    with self._lock:
                        if self._current(revision):
                            publish_cluster_change("shared_library_apply")
            except (TypeError, ValueError):
                logger.warning("Skipped an unsupported shared photo snapshot in batch %d", batch)
                continue
            group_key = producer, photo_hash
            if plan.carry_over:
                proposed[group_key] = len(plan.carry_over)
            else:
                proposed.pop(group_key, None)
            open_faces = [
                (issue.reason, issue.local[0] if len(issue.local) == 1 else None,
                 issue.shared[0] if len(issue.shared) == 1 else None)
                for issue in plan.conflicts
            ]
            open_faces.extend(("unmatched", face, None) for face in plan.unmatched_local if face.decision is not None)
            open_faces.extend(("unmatched", None, face) for face in plan.unmatched_shared if face.decision is not None)
            offers = stats.setdefault("choices", {}) if stats is not None else {}
            media = stats.setdefault("media", {}) if stats is not None else {}
            for previous in cases.pop(group_key, ()):
                offers.pop(previous["id"], None)
                media.pop(previous["id"], None)
            if not open_faces:
                continue
            seen = cases[group_key] = []
            for index, (reason, left, right) in enumerate(open_faces):
                case_id = digest([producer, photo_hash, reason, left.face_id if left else None,
                                  right.face_id if right else None, index])
                offer = actionable.get((left.face_id, right.face_id)) if left and right else None
                if offer:
                    offers[case_id] = offer
                case_revision = offer[0].revision if offer else digest([batch, case_id])
                has_geometry = all(type(value) is int for value in (local.exif_orientation, local.raw_width, local.raw_height))
                if has_geometry:
                    media[case_id] = (case_revision, {
                        "action": "conflict_preview", "source_root": source_path, "relative_path": relative,
                        "expected_hash": photo_hash, "geometry": (local.exif_orientation, local.raw_width, local.raw_height),
                        "boxes": {key: face.bbox for key, face in (("local", left), ("shared", right)) if face and face.geometry_reliable},
                    })
                seen.append({
                    "id": case_id, "revision": case_revision, "evidence_available": has_geometry,
                    "subject_id": digest(["photo-conflict-face-v1", photo_hash, sorted((left.face_id, right.face_id))]) if left and right else None,
                    "can_resolve": offer is not None, "can_keep_local": offer is not None,
                    "can_use_shared": bool(offer and offer[2]),
                    "relative_path": relative,
                    "reason": {"different_decisions": "different_decision", "unverified_person_identity": "different_person"}.get(reason, reason),
                    "local": _decision(left, self._device), "shared": _decision(right, remote["device_name"]),
                })
        return needs_retry

    def _reproject_photos(self, journal, scope, producer, photo_hashes, read_state):
        pending = read_state.setdefault("pending", {})
        for photo_hash in photo_hashes:
            latest = journal.latest_published_photo(scope, producer, photo_hash)
            if latest is not None:
                pending[producer, latest.batch] = None

    def _project_choices(self, journal, scope, decision_scope, manifest, config, device_id, peer, state, revision) -> bool:
        packets = journal.resolution_packets(scope, state.get("resolution_cursor", 0), limit=1)
        retries = state.setdefault("resolution_retries", {})
        fresh = bool(packets)
        if not packets and retries:
            sequence = next(iter(retries))
            producer, batch, position = retries.pop(sequence)
            entry = journal.published_event(scope, producer, batch, position)
            if entry is not None:
                packets = [(sequence, entry)]
        for sequence, entry in packets:
            if fresh:
                state["resolution_cursor"] = sequence
            if entry.producer not in (peer, device_id):
                return fresh
            result = self._consume_choice(journal, scope, decision_scope, manifest, config, device_id, peer, entry, revision, state)
            if result == "retry":
                retries[sequence] = (entry.producer, entry.batch, entry.position)
                return fresh
            if result != "ignored":
                self._reproject_photos(journal, scope, peer, (entry.event["photo_sha256"],), state)
            if result == "applied":
                invalidate_image_query_cache()
                app_cache.clear()
                with self._lock:
                    if self._current(revision):
                        publish_cluster_change("shared_library_resolution")
            selected = state.get("staged_choice")
            if selected and entry.producer == device_id and result != "ignored":
                _, _, side, case, _, _, _ = selected
                branch = case.local_branch if side == "local" else case.peer_branch
                if entry.event["resolution"]["event_id"] == case.packet(branch.revision)["resolution"]["event_id"]:
                    message = None if result == "applied" else "Der Fall bleibt offen. Prüfe den aktuellen gemeinsamen Stand."
                    self._finish_choice(revision, selected, message)
                    state.pop("staged_choice", None)
            return True
        return False

    def _loop(self) -> None:
        journal = self._journal()
        try:
            journal.initialize()
        except (OSError, sqlite3.Error):
            logger.exception("Could not initialize the shared library journal")
            with self._lock:
                self._update(self._revision, status="attention", error="Der lokale Abgleichverlauf konnte nicht geöffnet werden.")
            return
        last_revision, read_state, cases, proposed = -1, {}, {}, {}
        while not self._stop.is_set():
            did_work = False
            with self._lock:
                config, revision = self._config.copy(), self._revision
            if last_revision != revision:
                last_revision, read_state, cases, proposed = revision, {}, {}, {}
            if config["source_path"] and self._current(revision):
                scope = self._scope(config)
                try:
                    request = journal.checkpoint(scope)
                    device_id = request["producer_id"]
                    write_requested = config["write_enabled"] and self.write_available()
                    if write_requested and not read_state.get("writable"):
                        known = request["known_manifest"]
                        initialize = config["initialize_share"] and self._consume_initialization(revision)
                        probe = self._request({
                            "action": "probe", "sync_root": config["sync_path"], "allow_writes": True,
                            "known_manifest": known, "initialize_share": initialize and known is None,
                            "find_parent": config["source_path"] == config["sync_path"],
                        }, revision)
                        if probe.get("status") == "parent_share":
                            self._update(revision, status="attention", error="In einem übergeordneten Fotoordner gibt es bereits einen gemeinsamen Stand.", parent_source_path=probe["parent"])
                            self._wake.wait(3)
                            self._wake.clear()
                            continue
                        if self._current(revision) and probe["status"] == "ready":
                            if not self._adopt_manifest(probe["manifest"], revision):
                                self._wake.wait(3)
                                self._wake.clear()
                                continue
                            journal.bind(scope, probe["manifest"])
                            request = journal.checkpoint(scope)
                            read_state["writable"] = probe["writable"]
                    writable = write_requested and read_state.get("writable", False)
                    manifest = request["known_manifest"]
                    if writable:
                        if not self._backup_before_first_sync(manifest, device_id, revision):
                            self._wake.wait(3)
                            self._wake.clear()
                            continue
                        decision_scope = digest([scope, manifest["scope"], manifest["generation"]])
                        outbox_scope = digest([decision_scope, device_id])
                        with self._lock:
                            if not self._current(revision):
                                continue
                            self._drain_receipt(journal, scope, outbox_scope)
                    else:
                        request.pop("outgoing")
                    publication = "read"
                    if writable and request["outgoing"]:
                        publication = self._prepare_publication(journal, scope, outbox_scope, config, request, revision)
                        if publication in ("discarded", "wait"):
                            if publication == "discarded":
                                selected = read_state.pop("staged_choice", None)
                                if selected is not None:
                                    self._finish_choice(revision, selected, "Der Fall hat sich vor der Veröffentlichung geändert.")
                                self._wake.set()
                            else:
                                self._update(revision, status="offline", error=None)
                            self._wake.wait(0.1 if publication == "discarded" else 3)
                            self._wake.clear()
                            continue
                    allowed_to_start = None
                    if publication == "publish":
                        batch_number = request["outgoing"]["batch"]
                        _, rows = journal.pending_local_rows(scope, batch_number)
                        request["local_verification"] = {
                            "path": str(Path(schema.DB_PATH).absolute()), "scope": outbox_scope, "rows": rows,
                        }
                        allowed_to_start = lambda: self._mark_publication_started(journal, scope, outbox_scope, batch_number)
                    result = self._request({
                        "action": "exchange", "sync_root": config["sync_path"],
                        "find_parent": config["source_path"] == config["sync_path"],
                        "after_device": read_state.get("last_producer", ""),
                        "audit_cursors": read_state.get("audit_cursors", {}), "allow_writes": publication == "publish",
                        "reconcile_only": publication == "reconcile", **request,
                    }, revision, before_start=allowed_to_start)
                    if not self._current(revision):
                        continue
                    status = result["status"]
                    if status == "published" and writable:
                        with self._lock:
                            if not self._current(revision):
                                continue
                            journal.accept(scope, device_id, result["batch"], result["digest"])
                            self._drain_receipt(journal, scope, outbox_scope)
                        peer = read_state.get("approved_peer") or os.environ.get("FACE_MANAGER_SHARED_LIBRARY_TEST_PEER")
                        if peer:
                            self._reproject_photos(journal, scope, peer, (
                                entry["photo_sha256"] for entry in result["batch"]["events"] if entry.get("type") == "photo_snapshot"
                            ), read_state)
                        did_work = True
                    elif status == "received" and writable:
                        producer, batch = result["producer"], result["batch"]
                        journal.accept(scope, producer, batch, result["digest"])
                        read_state["last_producer"] = producer
                        did_work = True
                    elif status in ("received", "up_to_date"):
                        if not self._adopt_manifest(result["manifest"], revision):
                            self._wake.wait(3)
                            self._wake.clear()
                            continue
                        journal.bind(scope, result["manifest"])
                        if status == "received":
                            producer, batch = result["producer"], result["batch"]
                            journal.accept(scope, producer, batch, result["digest"])
                            read_state["last_producer"] = producer
                            request["cursor"][producer] = batch["batch_no"]
                        else:
                            read_state["audit_cursors"] = result.get("audit_cursors", {})
                        # Rebuild the read-only projection from the journal after restarting.
                        # Its receipt cursor is independent from reviewing records locally.
                        projected = read_state.setdefault("projected", {})
                        pending = read_state.setdefault("pending", {})
                        local_producers = set(request["local_producers"])
                        remote_producers = {key: tail for key, tail in request["cursor"].items() if key not in local_producers}
                        approved_peer = os.environ.get("FACE_MANAGER_SHARED_LIBRARY_TEST_PEER")
                        if self._trusted_share and not approved_peer and len(remote_producers) == 1:
                            approved_peer = next(iter(remote_producers))
                        read_state["approved_peer"] = approved_peer
                        apply = writable and set(remote_producers) == {approved_peer}
                        review = {
                            "decision_scope": decision_scope if writable else None,
                            "apply": apply, "stats": read_state, "manifest": manifest,
                        }
                        if apply:
                            did_work = self._project_choices(
                                journal, scope, decision_scope, manifest, config, device_id, approved_peer, read_state, revision,
                            )
                        for producer, tail in remote_producers.items():
                            next_batch = projected.get(producer, 0) + 1
                            if next_batch <= tail:
                                needs_retry = self._review(journal, scope, producer, next_batch, config["source_path"],
                                                           cases, proposed, device_id, revision, **review)
                                if needs_retry:
                                    pending[producer, next_batch] = None
                                projected[producer] = next_batch
                                did_work = True
                                break
                        if not did_work and pending:
                            producer, previous_batch = next(iter(pending))
                            del pending[producer, previous_batch]
                            if self._review(journal, scope, producer, previous_batch, config["source_path"],
                                            cases, proposed, device_id, revision, **review):
                                pending[producer, previous_batch] = None
                        competing_cases = read_state.get("competing_cases", {}) if apply else {}
                        visible = [case for case, _, _ in list(competing_cases.values())[:_MAX_VISIBLE_CASES]]
                        normal_count = 0
                        for group in cases.values():
                            distinct = [case for case in group if case.get("subject_id") not in competing_cases]
                            normal_count += len(distinct)
                            visible.extend(distinct[:_MAX_VISIBLE_CASES - len(visible)])
                        with self._lock:
                            if self._current(revision):
                                choices = {
                                    **read_state.get("choices", {}),
                                    **{case["id"]: offer for case, offer, _ in competing_cases.values() if case["can_resolve"]},
                                }
                                self._visible_choices = {case["id"]: choices[case["id"]] for case in visible if case["id"] in choices} if apply else {}
                                media = {
                                    **read_state.get("media", {}),
                                    **{case["id"]: evidence for case, _, evidence in competing_cases.values() if evidence},
                                }
                                self._visible_media = {case["id"]: media[case["id"]] for case in visible if case["id"] in media}
                        if writable and config["share_embeddings"] and self.analysis_available():
                            with self._lock:
                                if self._current(revision) and self._analysis_share is None:
                                    self._analysis_share = ImportShare(
                                        config["source_path"], config["sync_path"], manifest, device_id, uuid4().hex,
                                        fresh_reads=os.environ.get("FACE_MANAGER_SHARED_LIBRARY_FRESH_READ_TEST") == "1",
                                        device_name=self._device,
                                    )
                        self._update(revision, status="online" if writable else "read_only", conflicts=visible,
                                     imported_decisions=read_state.get("applied", 0),
                                     peer_names=sorted(set(read_state.get("peer_names", {}).values()), key=str.casefold),
                                     concurrent_choices=bool(competing_cases),
                                     conflict_count=normal_count + len(competing_cases), proposed_count=sum(proposed.values()), error=None,
                                     parent_source_path=None)
                        if writable and not did_work:
                            did_work = apply and self._stage_choice(
                                journal, scope, outbox_scope, decision_scope, manifest, config, device_id, approved_peer,
                                read_state, revision,
                            )
                        if writable and not did_work:
                            did_work = self._stage_local(
                                journal, scope, outbox_scope, decision_scope, config, device_id, read_state, revision,
                            )
                    elif status == "parent_share":
                        self._update(revision, status="attention", error="In einem übergeordneten Fotoordner gibt es bereits einen gemeinsamen Stand.", parent_source_path=result["parent"])
                    elif status == "stale":
                        did_work = True
                    elif status == "unpublished" and publication == "reconcile":
                        with self._lock:
                            if not self._current(revision):
                                continue
                            journal.abandon_attempted(scope, request["outgoing"]["batch"])
                            if (selected := read_state.get("staged_choice")) is not None:
                                self._finish_choice(revision, selected, "Die Entscheidung konnte nach der Unterbrechung nicht bestätigt werden.")
                            read_state.clear()
                            with closing(get_conn()) as connection, connection:
                                connection.execute("BEGIN IMMEDIATE")
                                retire_scope(connection, outbox_scope)
                        did_work = True
                    elif status in ("share_changed", "transport_error", "error", "published_unknown"):
                        if status in ("share_changed", "transport_error"):
                            read_state.pop("writable", None)
                        self._update(revision, status="attention", error="Der gemeinsame Stand muss überprüft werden.")
                    else:
                        self._update(revision, status="offline", error=None)
                except (KeyError, OSError, ValueError, TypeError, sqlite3.Error):
                    logger.warning("Could not evaluate a shared library preview", exc_info=True)
                    self._update(revision, status="attention", error="Der gemeinsame Stand muss überprüft werden.")
            self._wake.wait(0.1 if did_work else 3)
            self._wake.clear()
