"""Background library comparison and explicitly enabled write trials."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import get_data_root
from ..db import schema
from ..db.schema import get_conn
from .cache import app_cache
from .desktop import normalize_import_folder_path
from .events import event_hub
from .shared_sync_apply import apply_unambiguous, compare_for_scope, scoped_snapshot
from .shared_sync_io import SharedSyncProcess
from .shared_sync_journal import ExchangeJournal, digest, encode
from .shared_sync_outbox import (
    DirtyImage, acknowledge_changes, enqueue_baseline_page, initialize_outbox, pending_changes, register_scope,
    retire_scope, revisions_match,
)
from .shared_sync_reconcile import compare_verified_photo, matching_source_path, photo_event
from .shared_sync_snapshot import capture_image
from .shared_sync_transport import MAX_EVENT_BATCH_BYTES
from .storage import invalidate_image_query_cache


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
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._process = SharedSyncProcess(publish_capable=True)
        self._device = socket.gethostname()[:255]
        self._config = {
            "source_path": None, "sync_path": None, "paused": False,
            "share_embeddings": False, "write_enabled": False,
        }
        self._status = {"status": "off"}
        self._revision = 0
        self._restoring = False
        self._mutating = False

    @staticmethod
    def available() -> bool:
        return os.environ.get("FACE_MANAGER_SHARED_LIBRARY_PREVIEW") == "1"

    @staticmethod
    def write_available() -> bool:
        return SharedLibraryPreview.available() and os.environ.get("FACE_MANAGER_SHARED_LIBRARY_WRITE_TEST") == "1"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                **self._config, **self._status,
                "available": self.available(), "preview": not self._config["write_enabled"],
                "write_available": self.write_available(), "device_name": self._device,
            }

    def _update(self, revision: int, **fields) -> None:
        with self._lock:
            if revision == self._revision and not self._restoring:
                self._status = {**self._status, **fields}

    def _current(self, revision: int) -> bool:
        with self._lock:
            return revision == self._revision and not self._restoring and not self._config["paused"]

    def library_changed(self, *, restoring: bool | None = None) -> None:
        if not self.available() and restoring is None:
            return
        with self._lock:
            if restoring is not None:
                self._restoring = restoring
            if self._config["source_path"]:
                self._revision += 1
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
                if (self._mutating and self._process.busy) or self._journal().publications_pending():
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
        if bool(paths[0]) != bool(paths[1]):
            raise ValueError("Wähle den Fotoordner und den Ordner für den gemeinsamen Stand.")
        paused, share_embeddings = raw.get("paused", False), raw.get("share_embeddings", False)
        write_enabled = raw.get("write_enabled", False)
        if any(type(value) is not bool for value in (paused, share_embeddings, write_enabled)):
            raise ValueError("Die Einstellungen sind ungültig.")
        if share_embeddings:
            raise ValueError("Die Vorschau speichert keine Gesichtsmerkmale auf der Freigabe.")
        if write_enabled and not (paths[0] and SharedLibraryPreview.write_available()):
            raise ValueError("Schreibender Abgleich ist für diesen Ordner nicht verfügbar.")
        return {
            "source_path": paths[0], "sync_path": paths[1], "paused": paused,
            "share_embeddings": False, "write_enabled": write_enabled,
        }

    def configure(self, raw: dict) -> dict:
        config = self._validated(raw)
        with self._lock:
            if self._restoring:
                raise ValueError("Die Fotobibliothek wird gerade wiederhergestellt.")
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
            if retiring:
                try:
                    with closing(get_conn()) as connection, connection:
                        connection.execute("BEGIN IMMEDIATE")
                        initialize_outbox(connection)
                        retire_scope(connection, retiring)
                except sqlite3.Error:
                    logger.warning("Could not retire the previous shared library outbox", exc_info=True)
        return self.snapshot()

    def start(self) -> None:
        if not self.available() or (self._thread is not None and self._thread.is_alive()):
            return
        try:
            config_path = get_data_root() / "shared-library-preview.json"
            if config_path.is_file():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if not self.write_available():
                    config["write_enabled"] = False
                self._config = self._validated(config)
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
            for event in outgoing["events"]:
                verification = self._request({
                    "action": "verify_source", "source_root": config["source_path"],
                    "relative_path": event["relative_paths"][0], "expected_hash": event["photo_sha256"],
                }, revision)
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
            if photo is None:
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
            verified = self._request({
                "action": "verify_source", "source_root": config["source_path"],
                "relative_path": relative, "expected_hash": photo.photo_sha256,
            }, revision)
            if verified.get("status") != "verified" or verified.get("photo_sha256") != photo.photo_sha256:
                continue
            events.append(event)
            receipts.append((change.image_id, change.revision))

        with self._lock:
            if not self._current(revision):
                return False
            if skipped:
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    acknowledge_changes(connection, outbox_scope, skipped)
            if events:
                with closing(get_conn()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = []
                    for event, receipt in zip(events, receipts):
                        image_id, captured_revision = receipt
                        row = connection.execute(
                            "SELECT revision FROM shared_sync_dirty_image WHERE scope_id = ? AND image_id = ?",
                            (outbox_scope, image_id),
                        ).fetchone()
                        if row and row[0] == captured_revision:
                            current.append((event, receipt))
                    if current:
                        journal.stage(scope, [event for event, _ in current], local_rows=tuple(row for _, row in current))
        return bool(events or skipped or more_baseline)

    def _review(self, journal, scope, producer, batch, source_path, cases, proposed, device_id, revision,
                *, decision_scope=None, apply=False, stats=None) -> bool:
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
                if decision_scope is None:
                    plan = compare_verified_photo(local, remote, verified_hash=result["photo_sha256"])
                else:
                    applied = 0
                    with self._lock:
                        if not self._current(revision):
                            return False
                        with closing(get_conn()) as connection, connection:
                            connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
                            if apply:
                                applied = apply_unambiguous(
                                    connection, scope=decision_scope, source_path=source_path, device_id=device_id,
                                    image_id=local.image_id, remote=remote, verified_hash=photo_hash,
                                    verified_relative_path=relative,
                                )
                            fresh = capture_image(connection, source_path, device_id, local.image_id)
                            if fresh is None:
                                continue
                            plan = compare_for_scope(
                                connection, scope=decision_scope, device_id=device_id, local=fresh,
                                remote=remote, verified_hash=photo_hash,
                            )
                    if applied:
                        stats["applied"] = stats.get("applied", 0) + applied
                        invalidate_image_query_cache()
                        app_cache.clear()
                        event_hub.publish("clusters", {"reason": "shared_library_apply"})
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
                        probe = self._request({
                            "action": "probe", "sync_root": config["sync_path"], "allow_writes": True,
                            "known_manifest": request["known_manifest"],
                        }, revision)
                        if self._current(revision) and probe["status"] == "ready":
                            journal.bind(scope, probe["manifest"])
                            request = journal.checkpoint(scope)
                            read_state["writable"] = probe["writable"]
                    writable = write_requested and read_state.get("writable", False)
                    manifest = request["known_manifest"]
                    if writable:
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
                        did_work = True
                    elif status == "received" and writable:
                        producer, batch = result["producer"], result["batch"]
                        journal.accept(scope, producer, batch, result["digest"])
                        read_state["last_producer"] = producer
                        did_work = True
                    elif status in ("received", "up_to_date"):
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
                        review = {
                            "decision_scope": decision_scope if writable else None,
                            "apply": writable and set(remote_producers) == {approved_peer},
                            "stats": read_state,
                        }
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
                        visible = []
                        for group in cases.values():
                            visible.extend(group[:_MAX_VISIBLE_CASES - len(visible)])
                            if len(visible) == _MAX_VISIBLE_CASES:
                                break
                        self._update(revision, status="online" if writable else "read_only", conflicts=visible,
                                     imported_decisions=read_state.get("applied", 0),
                                     conflict_count=sum(map(len, cases.values())), proposed_count=sum(proposed.values()), error=None)
                        if writable and not did_work:
                            did_work = self._stage_local(
                                journal, scope, outbox_scope, decision_scope, config, device_id, read_state, revision,
                            )
                    elif status == "stale":
                        did_work = True
                    elif status == "unpublished" and publication == "reconcile":
                        with self._lock:
                            if not self._current(revision):
                                continue
                            journal.abandon_attempted(scope, request["outgoing"]["batch"])
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


shared_library_preview = SharedLibraryPreview()
