"""Read-only evaluation of an existing shared library during protocol validation."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import get_data_root
from ..db.schema import get_conn
from .desktop import normalize_import_folder_path
from .shared_sync_io import SharedSyncProcess
from .shared_sync_journal import ExchangeJournal, digest, encode
from .shared_sync_reconcile import compare_verified_photo, matching_source_path
from .shared_sync_snapshot import capture_image


logger = logging.getLogger("face_manager.shared_sync")
_MAX_VISIBLE_CASES = 100


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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._process = SharedSyncProcess(publish_capable=False)
        self._device = socket.gethostname()[:255]
        self._config = {"source_path": None, "sync_path": None, "paused": False, "share_embeddings": False}
        self._status = {"status": "off"}
        self._revision = 0
        self._restoring = False

    @staticmethod
    def available() -> bool:
        return os.environ.get("FACE_MANAGER_SHARED_LIBRARY_PREVIEW") == "1"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                **self._config, **self._status,
                "available": self.available(), "preview": True, "device_name": self._device,
            }

    def _update(self, revision: int, **fields) -> None:
        with self._lock:
            if revision == self._revision and not self._restoring:
                self._status = {**self._status, **fields}

    def _current(self, revision: int) -> bool:
        with self._lock:
            return revision == self._revision and not self._restoring and not self._config["paused"]

    def library_changed(self, *, restoring: bool | None = None) -> None:
        if not self.available():
            return
        with self._lock:
            if restoring is not None:
                self._restoring = restoring
            if self._config["source_path"]:
                self._revision += 1
                self._status = {"status": "paused" if self._config["paused"] else "checking"}
                self._wake.set()

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
        if bool(paths[0]) != bool(paths[1]):
            raise ValueError("Wähle den Fotoordner und den Ordner für den gemeinsamen Stand.")
        paused, share_embeddings = raw.get("paused", False), raw.get("share_embeddings", False)
        if type(paused) is not bool or type(share_embeddings) is not bool:
            raise ValueError("Die Einstellungen sind ungültig.")
        if share_embeddings:
            raise ValueError("Die Vorschau speichert keine Gesichtsmerkmale auf der Freigabe.")
        return {"source_path": paths[0], "sync_path": paths[1], "paused": paused, "share_embeddings": False}

    def configure(self, raw: dict) -> dict:
        config = self._validated(raw)
        with self._lock:
            path = get_data_root() / "shared-library-preview.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="shared-config-", dir=path.parent) as directory:
                temporary = Path(directory) / "config.json"
                temporary.write_text(encode(config), encoding="utf-8")
                os.replace(temporary, path)
            self._config = config
            self._revision += 1
            self._status = {"status": "paused" if config["paused"] else "checking" if config["source_path"] else "off"}
            self._wake.set()
        return self.snapshot()

    def start(self) -> None:
        if not self.available() or (self._thread is not None and self._thread.is_alive()):
            return
        try:
            config_path = get_data_root() / "shared-library-preview.json"
            if config_path.is_file():
                self._config = self._validated(json.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            logger.warning("Could not load the shared library preview configuration")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="shared-library-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._process.stop(timeout_seconds=1.5)
            self._thread.join(timeout=0.5)

    def _request(self, req: dict, revision: int) -> dict:
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
                self._process.start([str(pathname)])
            while not self._stop.is_set():
                result = self._process.poll()
                if result is not None:
                    return result.payload if result.status == "ok" else {"status": "unavailable"}
                self._stop.wait(0.05)
        return {"status": "unavailable"}

    def _review(self, journal, scope, producer, batch, source_path, cases, proposed, device_id, revision) -> bool:
        needs_retry = False
        for remote in journal.events(scope, producer, batch):
            if not self._current(revision) or self._stop.is_set():
                return False
            if remote.get("type") != "photo_snapshot":
                continue
            photo_hash = remote.get("photo_sha256")
            if not isinstance(photo_hash, str):
                continue
            with closing(get_conn()) as connection, connection:
                connection.execute("BEGIN")
                row = connection.execute("SELECT id FROM image WHERE content_hash = ?", (photo_hash,)).fetchone()
                if row is None:
                    continue
                local = capture_image(connection, source_path, device_id, row["id"])
            if local is None:
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
            if result["status"] != "verified":
                needs_retry = needs_retry or result["status"] in ("unavailable", "interrupted")
                cases.pop((producer, photo_hash), None)
                proposed.pop((producer, photo_hash), None)
                continue
            try:
                plan = compare_verified_photo(local, remote, verified_hash=result["photo_sha256"])
            except (TypeError, ValueError):
                logger.warning("Skipped an unsupported shared photo snapshot in batch %d", batch)
                continue
            proposed[producer, photo_hash] = len(plan.carry_over)
            open_faces = [
                (issue.reason, issue.local[0] if len(issue.local) == 1 else None,
                 issue.shared[0] if len(issue.shared) == 1 else None)
                for issue in plan.conflicts
            ]
            open_faces.extend(("unmatched", face, None) for face in plan.unmatched_local if face.decision is not None)
            open_faces.extend(("unmatched", None, face) for face in plan.unmatched_shared if face.decision is not None)
            seen = cases.setdefault((producer, photo_hash), [])
            seen.clear()
            for index, (reason, left, right) in enumerate(open_faces[:_MAX_VISIBLE_CASES]):
                case_id = digest([producer, photo_hash, reason, left.face_id if left else None,
                                  right.face_id if right else None, index])
                seen.append({
                    "id": case_id, "revision": digest([batch, case_id]), "can_resolve": False,
                    "relative_path": relative,
                    "reason": {"different_decisions": "different_decision", "unverified_person_identity": "different_person"}.get(reason, reason),
                    "local": _decision(left, self._device), "shared": _decision(right, remote["device_name"]),
                })
        return needs_retry

    def _loop(self) -> None:
        journal = ExchangeJournal(get_data_root() / "shared-library-witness.sqlite")
        journal.initialize()
        last_revision, read_state, cases, proposed = -1, {}, {}, {}
        while not self._stop.is_set():
            did_work = False
            with self._lock:
                config, revision = self._config.copy(), self._revision
            if last_revision != revision:
                last_revision, read_state, cases, proposed = revision, {}, {}, {}
            if config["source_path"] and self._current(revision):
                scope = digest([os.path.normcase(config["source_path"]), os.path.normcase(config["sync_path"])])
                try:
                    request = journal.checkpoint(scope)
                    request.pop("outgoing")  # This preview only reads the shared event log.
                    result = self._request({
                        "action": "exchange", "sync_root": config["sync_path"],
                        "after_device": read_state.get("last_producer", ""),
                        "audit_cursors": read_state.get("audit_cursors", {}), **request,
                    }, revision)
                    if not self._current(revision):
                        continue
                    status = result["status"]
                    if status in ("received", "up_to_date"):
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
                        for producer, tail in request["cursor"].items():
                            next_batch = projected.get(producer, 0) + 1
                            if next_batch <= tail:
                                needs_retry = self._review(journal, scope, producer, next_batch, config["source_path"],
                                                           cases, proposed, request["producer_id"], revision)
                                if needs_retry:
                                    pending[producer, next_batch] = None
                                projected[producer] = next_batch
                                did_work = True
                                break
                        if not did_work and pending:
                            producer, previous_batch = next(iter(pending))
                            del pending[producer, previous_batch]
                            if self._review(journal, scope, producer, previous_batch, config["source_path"],
                                            cases, proposed, request["producer_id"], revision):
                                pending[producer, previous_batch] = None
                        visible = []
                        for group in cases.values():
                            visible.extend(group[:_MAX_VISIBLE_CASES - len(visible)])
                            if len(visible) == _MAX_VISIBLE_CASES:
                                break
                        self._update(revision, status="read_only", conflicts=visible,
                                     conflict_count=sum(map(len, cases.values())), proposed_count=sum(proposed.values()), error=None)
                    elif status in ("share_changed", "transport_error", "error"):
                        self._update(revision, status="attention", error="Der gemeinsame Stand muss überprüft werden.")
                    else:
                        self._update(revision, status="offline", error=None)
                except (KeyError, OSError, ValueError, TypeError, sqlite3.Error):
                    logger.warning("Could not evaluate a shared library preview", exc_info=True)
                    self._update(revision, status="attention", error="Der gemeinsame Stand muss überprüft werden.")
            self._wake.wait(0.1 if did_work else 3)
            self._wake.clear()


shared_library_preview = SharedLibraryPreview()
