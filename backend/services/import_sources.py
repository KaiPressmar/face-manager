"""Keep the source registry independent of import history and schedule its scans."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from ..db.schema import get_conn
from .filesystem_paths import filesystem_path
from .photo_identity import matches_file_identity
from .source_reconciliation import (
    Location, ObservedFile, ScanInterrupted, SourceUnavailable,
    find_moves, inspect_source, path_key, root_identity,
)

logger = logging.getLogger("face_manager.import_sources")
SCAN_INTERVAL = 15 * 60
INITIAL_DELAY = 30
OFFLINE_RETRY = 60
OFFLINE_RETRY_LIMIT = 15 * 60
COUNTERS = ("scanned_files", "new_files", "new_folders", "relinked_files", "unavailable_files", "conflicts")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_payload(row) -> dict:
    data = {name: row[name] for name in (
        "id", "folder_path", "status", "last_scan_at", "last_success_at", "pending_path", "error",
    )}
    for name in ("auto_scan", "auto_import"):
        data[name] = bool(row[name])
    summary = json.loads(row["summary"])
    data.update({name: summary.get(name, 0) for name in COUNTERS})
    data["conflict_candidates"] = summary.get("conflict_candidates", [])
    data["auto_import_blocked"] = bool(row["auto_import"] and row["auto_import_blocked"])
    return data


def _source(conn, source_id: str):
    row = conn.execute("SELECT * FROM import_source WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise LookupError("Der Bilderordner wurde entfernt.")
    return row


def _locations(conn, root: str) -> list[Location]:
    # Directory prefix range allows SQLite to use the UNIQUE path index. LIKE
    # with the default case-insensitive collation would scan the entire table.
    prefix = root.rstrip(os.sep) + os.sep
    end = prefix[:-1] + chr(ord(os.sep) + 1)
    path_column = "l.path COLLATE NOCASE" if os.name == "nt" else "l.path"
    rows = conn.execute(
        f"""
        SELECT l.id, l.image_id, l.path, COALESCE(l.content_hash, i.content_hash) AS digest,
               l.file_device, l.file_inode, l.file_size, l.modified_at_ns, l.file_changed_at_ns
        FROM image_location l JOIN image i ON i.id = l.image_id
        WHERE {path_column} >= ? AND {path_column} < ? AND i.processed_at IS NOT NULL
        """,
        (prefix, end),
    )
    return [Location(row[0], row[1], row[2], row[3], tuple(row)[4:]) for row in rows]


def _digest_matches(conn, files) -> set[str]:
    digests = list({photo.digest for photo in files})
    known = set()
    for start in range(0, len(digests), 400):
        batch = digests[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        known.update(row[0] for row in conn.execute(
            f"SELECT content_hash FROM image WHERE processed_at IS NOT NULL AND content_hash IN ({placeholders})",
            batch,
        ))
        known.update(row[0] for row in conn.execute(
            f"""SELECT DISTINCT location.content_hash FROM image_location location
                JOIN image ON image.id = location.image_id
                WHERE image.processed_at IS NOT NULL AND location.content_hash IN ({placeholders})""",
            batch,
        ))
    return known


def _move_location(conn, old: Location, new: ObservedFile) -> bool:
    # Refuse stale decisions after a manual rename, reconciliation, or edit.
    # Check destination in both tables because the canonical path is unique.
    existing = conn.execute(
        """SELECT l.path, l.image_id, COALESCE(l.content_hash, i.content_hash),
                  l.file_device, l.file_inode, l.file_size, l.modified_at_ns, l.file_changed_at_ns
           FROM image_location l JOIN image i ON i.id = l.image_id WHERE l.id = ?
           AND NOT EXISTS (SELECT 1 FROM image_rename_intent WHERE location_id = l.id)""",
        (old.id,),
    ).fetchone()
    if existing is None or tuple(existing) != (old.path, old.image_id, new.digest, *old.identity):
        return False
    if conn.execute(
        """SELECT 1 FROM image_location WHERE path = ?
           UNION ALL SELECT 1 FROM image WHERE path = ?
           UNION ALL SELECT 1 FROM image_rename_intent WHERE source_path = ? OR target_path = ?""",
        (new.path, new.path, new.path, new.path),
    ).fetchone():
        return False
    device, inode, size, modified, changed = new.identity
    device = device if -(2**63) <= device < 2**63 else None
    inode = inode if -(2**63) <= inode < 2**63 else None
    directory, filename = os.path.split(new.path)
    conn.execute(
        """UPDATE image_location SET path = ?, directory = ?, filename = ?,
           file_size = ?, modified_at_ns = ?, file_device = ?, file_inode = ?, file_changed_at_ns = ?
           WHERE id = ?""",
        (new.path, directory, filename, size, modified, device, inode, changed, old.id),
    )
    conn.execute(
        "UPDATE image SET path = ?, directory = ?, filename = ? WHERE id = ? AND path = ?",
        (new.path, directory, filename, old.image_id, old.path),
    )
    return True


class ImportSources:
    """Run one inventory at a time, with short, library-guarded database writes."""

    def __init__(self, *, library_lock, generation, is_idle, enqueue, relocate, suffixes, publish, changed):
        self._library_lock = library_lock
        self._generation = generation
        self._is_idle = is_idle
        self._enqueue = enqueue
        self._relocate = relocate
        self._suffixes = frozenset(suffixes)
        self._publish = publish
        self._changed = changed
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()
        self._restart_pending = False
        self._worker = None
        self._running: dict[str, int] = {}
        self._offline_attempts: dict[str, int] = {}

    def list(self) -> list[dict]:
        with self._library_lock, closing(get_conn()) as conn:
            rows = conn.execute("SELECT * FROM import_source ORDER BY folder_path COLLATE NOCASE").fetchall()
            sources = [_source_payload(row) for row in rows]
            for source in sources:
                if source["status"] == "running":
                    source["scanned_files"] = self._running.get(source["id"], 0)
            return sources

    def register(self, path: str) -> dict:
        with self._library_lock, closing(get_conn()) as conn, conn:
            existing = conn.execute("SELECT * FROM import_source").fetchall()
            for row in existing:
                if path_key(row["folder_path"]) == path_key(path):
                    return _source_payload(row)
            source_id = uuid4().hex
            conn.execute(
                "INSERT INTO import_source(id, folder_path, due_at) VALUES (?, ?, ?)",
                (source_id, path, time.time() + INITIAL_DELAY),
            )
            return _source_payload(_source(conn, source_id))

    def update(self, source_id: str, *, auto_scan: bool | None, auto_import: bool | None) -> dict:
        with self._library_lock, closing(get_conn()) as conn, conn:
            row = _source(conn, source_id)
            scanning = row["auto_scan"] if auto_scan is None else auto_scan
            importing = row["auto_import"] if auto_import is None else auto_import
            blocked = 0 if auto_import is True else row["auto_import_blocked"]
            conn.execute(
                "UPDATE import_source SET auto_scan = ?, auto_import = ?, auto_import_blocked = ? WHERE id = ?",
                (scanning, importing, blocked, source_id),
            )
            if auto_scan is True:
                conn.execute("UPDATE import_source SET due_at = 0 WHERE id = ?", (source_id,))
            result = _source_payload(_source(conn, source_id))
        self._wake.set()
        return result

    def block_automatic_import(self, job_id: str) -> None:
        with self._library_lock, closing(get_conn()) as conn, conn:
            conn.execute(
                """UPDATE import_source SET auto_import_blocked = 1 WHERE folder_path = (
                   SELECT folder_path FROM import_job WHERE id = ?)""",
                (job_id,),
            )

    def remove(self, source_id: str) -> None:
        with self._library_lock, closing(get_conn()) as conn, conn:
            _source(conn, source_id)
            conn.execute("DELETE FROM import_source WHERE id = ?", (source_id,))
            self._offline_attempts.pop(source_id, None)

    def schedule(self, source_id: str, *, import_new: bool = False) -> dict:
        return self._request(source_id, "import" if import_new else "scan")

    def relink(self, source_id: str, path: str, *, confirm: bool) -> dict:
        return self._request(source_id, "relink" if confirm else "preview", path)

    def resolve(self, source_id: str, original: str, destination: str) -> dict:
        return self._request(source_id, "resolve", resolution=(original, destination))

    def cancel_preview(self, source_id: str) -> dict:
        with self._library_lock, closing(get_conn()) as conn, conn:
            row = _source(conn, source_id)
            if row["pending_path"] is not None and row["mode"] != "relink":
                conn.execute(
                    """UPDATE import_source SET status = 'idle', mode = 'scan', pending_path = NULL,
                       summary = '{}', error = NULL, due_at = ?, revision = revision + 1 WHERE id = ?""",
                    (time.time() + SCAN_INTERVAL, source_id),
                )
            return _source_payload(_source(conn, source_id))

    def _request(self, source_id: str, mode: str, path: str | None = None, *, resolution=None) -> dict:
        with self._library_lock, closing(get_conn()) as conn, conn:
            row = _source(conn, source_id)
            if mode == "relink" and (row["status"] != "preview_ready" or row["pending_path"] != path):
                raise ValueError("Prüfe den gewählten Speicherort zuerst.")
            if path is not None:
                for other in conn.execute("SELECT id, folder_path FROM import_source WHERE id != ?", (source_id,)):
                    if path_key(other["folder_path"]) == path_key(path):
                        raise ValueError("Dieser Ordner wird bereits verwaltet.")
            if resolution is not None:
                original, destination = resolution
                matches = _source_payload(row)["conflict_candidates"]
                if row["status"] != "completed" or not any(
                    item["original_path"] == original and destination in item["candidate_paths"] for item in matches
                ):
                    raise ValueError("Die Auswahl ist nicht mehr aktuell. Prüfe den Ordner erneut.")
            if row["status"] in {"queued", "running"} and row["mode"] != "auto":
                if row["mode"] == mode and row["pending_path"] == path:
                    return _source_payload(row)
                raise ValueError("Warte, bis die laufende Ordnerprüfung abgeschlossen ist.")
            conn.execute(
                """UPDATE import_source SET status = 'queued', mode = ?, pending_path = ?, resolution = ?, due_at = 0,
                   revision = revision + 1, error = NULL WHERE id = ?""",
                (mode, path, json.dumps(resolution) if resolution else None, source_id),
            )
            result = _source_payload(_source(conn, source_id))
        self._wake.set()
        return result

    def start(self) -> None:
        with self._lifecycle:
            if self._worker is not None:
                self._restart_pending = self._stop.is_set()
                return
            self._start_worker()

    def _start_worker(self) -> None:
        self._stop.clear()
        self.recover_interrupted_scans()
        self._worker = threading.Thread(target=self._run, name="import-source-scan", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        with self._lifecycle:
            self._restart_pending = False
            self._stop.set()
            self._wake.set()
            worker = self._worker
        if worker:
            worker.join(timeout=0.5)

    def reset_restored_work(self) -> None:
        # A backup can contain an in-flight move or import from another device.
        with self._library_lock, closing(get_conn()) as conn, conn:
            conn.execute("UPDATE import_source SET auto_scan = 0, auto_import = 0")
            conn.execute(
                """UPDATE import_source SET status = 'idle', mode = 'scan', pending_path = NULL,
                   resolution = NULL, auto_scan = 0, auto_import = 0, due_at = 0, revision = revision + 1
                   WHERE status IN ('queued', 'running', 'preview_ready')"""
            )

    def recover_interrupted_scans(self) -> None:
        with self._library_lock, closing(get_conn()) as conn, conn:
            conn.execute("UPDATE import_source SET status = 'queued' WHERE status = 'running'")
        self._wake.set()

    def _next(self):
        with self._library_lock, closing(get_conn()) as conn, conn:
            generation = self._generation()
            row = conn.execute(
                """SELECT * FROM import_source
                   WHERE (status = 'queued' OR (auto_scan AND status NOT IN ('running', 'preview_ready')
                     AND pending_path IS NULL)) AND due_at <= ?
                   ORDER BY (status = 'queued') DESC, due_at, id LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            mode = row["mode"] if row["status"] == "queued" else "auto"
            conn.execute(
                "UPDATE import_source SET status = 'running', mode = ?, last_scan_at = ?, error = NULL WHERE id = ?",
                (mode, _now(), row["id"]),
            )
            source = dict(row)
            source["mode"] = mode
            root = source["pending_path"] or source["folder_path"]
            source_locations = _locations(conn, source["folder_path"])
            destination_locations = source_locations if path_key(root) == path_key(source["folder_path"]) else _locations(conn, root)
            previous_locations = (location for earlier in json.loads(source["previous_roots"])
                                  for location in _locations(conn, earlier))
            all_locations = {location.id: location for location in previous_locations}
            all_locations.update((location.id, location) for location in source_locations)
            cached = {
                path_key(item[0]): ObservedFile(item[0], tuple(json.loads(item[1])), item[2])
                for item in conn.execute(
                    "SELECT path, identity, digest FROM import_source_file WHERE source_id = ?",
                    (source["id"],),
                )
            }
            return generation, source, root, list(all_locations.values()), destination_locations, cached

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    job = self._next() if self._is_idle() else None
                    if job is not None:
                        self._scan(*job)
                        continue
                except Exception:
                    logger.exception("Could not dispatch a source scan")
                self._wake.wait(2)
                self._wake.clear()
        finally:
            with self._lifecycle:
                self._worker = None
                if self._restart_pending:
                    self._restart_pending = False
                    self._start_worker()

    def _scan(self, generation, source, root, known, destination, cached) -> None:
        source_id = source["id"]
        self._running[source_id] = 0
        self._publish(source_id, "running", 0)
        last_update = 0.0

        def progress(count: int) -> None:
            nonlocal last_update
            self._running[source_id] = count
            now = time.monotonic()
            if now - last_update < 0.5:
                return
            while not self._is_idle():
                if self._stop.wait(0.25):
                    raise ScanInterrupted()
            self._generation(generation)
            last_update = time.monotonic()
            self._publish(source_id, "running", count)

        preview = source["mode"] == "preview"
        relink = source["mode"] == "relink"
        locations = {path_key(location.path): location for location in destination}
        expected = None
        if not (preview or relink) and source["device"] is not None:
            expected = int(source["device"]), int(source["inode"])
        try:
            inventory = inspect_source(
                root, locations, cached, self._suffixes, self._stop, progress, expected_root=expected,
            )
            reconciliation = find_moves(
                source["folder_path"], root, known, locations, inventory,
            )
            if preview or relink:
                if inventory.failed_paths or (known and not (
                    reconciliation.moves or reconciliation.verified or reconciliation.choices
                )):
                    raise ValueError("Der Ordner enthält keine sicher zuordenbare Kopie. Prüfe den ausgewählten Speicherort.")
            if source["mode"] == "resolve":
                original, selected = json.loads(source["resolution"])
                old = next((location for location in known if location.path == original), None)
                key = path_key(selected)
                new = inventory.files.get(key)
                if old is None or new is None or old.digest != new.digest or key in locations:
                    raise ValueError("Die Datei wurde geändert oder verschoben. Prüfe den Ordner erneut.")
                if (old, new) not in reconciliation.moves:
                    if not any(choice["original_path"] == original and selected in choice["candidate_paths"]
                               for choice in reconciliation.choices) or any(
                        location == old or photo.path == new.path for location, photo in reconciliation.moves
                    ):
                        raise ValueError("Die Auswahl ist nicht mehr aktuell. Prüfe den Ordner erneut.")
                    reconciliation.moves.insert(0, (old, new))
                    reconciliation.unresolved = max(0, reconciliation.unresolved - 1)
                reconciliation.matched.add(key)
                reconciliation.choices = [
                    {**choice, "candidate_paths": [path for path in choice["candidate_paths"] if path != selected]}
                    for choice in reconciliation.choices if choice["original_path"] != original
                ]
                reconciliation.choices = [choice for choice in reconciliation.choices if choice["candidate_paths"]]
            self._finish(generation, source, root, preview, relink, inventory, reconciliation, cached)
        except ScanInterrupted:
            pass  # start() requeues an interrupted scan.
        except SourceUnavailable as exc:
            self._record_failure(generation, source, "offline", str(exc))
        except Exception as exc:
            logger.info("Source scan could not finish: %s", source_id, exc_info=True)
            self._record_failure(generation, source, "failed", str(exc))
        finally:
            self._running.pop(source_id, None)

    def _current(self, conn, generation, source):
        self._generation(generation)
        row = conn.execute("SELECT * FROM import_source WHERE id = ?", (source["id"],)).fetchone()
        return row if row is not None and row["revision"] == source["revision"] else None

    def _finish(self, generation, source, root, preview, relink, inventory, reconciliation, cached):
        source_id = source["id"]
        if not preview:
            stable = []
            for old, new in reconciliation.moves:
                if self._stop.is_set():
                    raise ScanInterrupted()
                try:
                    if matches_file_identity(filesystem_path(new.path), new.identity):
                        stable.append((old, new))
                except OSError:
                    pass
            changed = len(reconciliation.moves) - len(stable)
            if changed and (relink or source["mode"] == "resolve"):
                raise ValueError("Dateien wurden während der Prüfung geändert. Prüfe den Ordner erneut.")
            reconciliation.unresolved += changed
            reconciliation.ambiguities += changed
            reconciliation.moves = stable
        if root_identity(root) != inventory.root_identity:
            raise SourceUnavailable("Der Bilderordner wurde während der Prüfung getrennt oder ersetzt.")
        with self._library_lock, closing(get_conn()) as conn:
            row = self._current(conn, generation, source)
            if row is None or self._stop.is_set():
                return
            known_hashes = _digest_matches(conn, inventory.files.values())
            unknown = [photo for key, photo in inventory.files.items() if key not in reconciliation.matched
                       and photo.digest not in known_hashes]
            # Changed bytes at an existing location are reported as conflicts.
            current_locations = _locations(conn, root)
            blocked_paths = {path_key(location.path) for location in current_locations}
            unknown = [photo for photo in unknown if path_key(photo.path) not in blocked_paths]
            directories = set(json.loads(row["directories"]))
            if not row["last_success_at"] and not (preview or relink):
                for location in current_locations:
                    ancestor = os.path.dirname(os.path.relpath(location.path, root))
                    while ancestor and ancestor not in directories:
                        directories.add(ancestor)
                        ancestor = os.path.dirname(ancestor)
            summary = {
                "scanned_files": len(inventory.seen),
                "new_files": len(unknown),
                "new_folders": len(inventory.directories - directories),
                "relinked_files": len(reconciliation.moves),
                "unavailable_files": reconciliation.unresolved + len(inventory.failed_paths),
                "conflicts": inventory.conflicts + reconciliation.ambiguities,
                "conflict_candidates": reconciliation.choices,
            }
            status = "preview_ready" if preview else "completed"
            error = "Einige Pfade konnten nicht geprüft werden." if inventory.failed_paths else None
            if inventory.failed_paths:
                directories |= inventory.directories
            else:
                directories = inventory.directories

            def persist(connection):
                if relink:
                    # Dormant jobs and the new root commit in the same transaction.
                    earlier = set(json.loads(row["previous_roots"]))
                    if source["folder_path"] != root and reconciliation.unresolved:
                        earlier.add(source["folder_path"])
                    earlier.discard(root)
                    connection.execute("UPDATE import_source SET folder_path = ?, previous_roots = ? WHERE id = ?",
                                       (root, json.dumps(sorted(earlier)), source_id))
                if not preview:
                    moves = reconciliation.moves
                    applied = sum(_move_location(connection, old, new) for old, new in moves)
                    if (relink or source["mode"] == "resolve") and applied != len(moves):
                        raise ValueError("Speicherorte haben sich geändert. Prüfe den Ordner erneut.")
                    summary["conflicts"] += len(moves) - applied
                    summary["unavailable_files"] += len(moves) - applied
                    summary["relinked_files"] = applied
                    remaining = {key: photo for key, photo in inventory.files.items() if key not in reconciliation.matched}
                    connection.executemany(
                        "DELETE FROM import_source_file WHERE source_id = ? AND path = ?",
                        ((source_id, photo.path) for key, photo in cached.items()
                         if key not in remaining or remaining[key].path != photo.path),
                    )
                    connection.executemany(
                        """INSERT INTO import_source_file(source_id, path, identity, digest) VALUES (?, ?, ?, ?)
                           ON CONFLICT (source_id, path) DO UPDATE SET identity = excluded.identity, digest = excluded.digest""",
                        ((source_id, photo.path, json.dumps(photo.identity), photo.digest)
                         for key, photo in remaining.items() if cached.get(key) != photo),
                    )
                connection.execute(
                    """UPDATE import_source SET status = ?, mode = 'scan', pending_path = ?, resolution = NULL, summary = ?,
                       error = ?, last_success_at = ?, directories = ?, device = ?, inode = ?, due_at = ?
                       WHERE id = ?""",
                    (status, root if preview else None, json.dumps(summary), error,
                     row["last_success_at"] if preview or error else _now(),
                     row["directories"] if preview else json.dumps(sorted(directories)),
                     row["device"] if preview else str(inventory.root_identity[0]),
                     row["inode"] if preview else str(inventory.root_identity[1]),
                     time.time() + SCAN_INTERVAL, source_id),
                )

            if relink:
                self._relocate(source["folder_path"], root, persist=persist)
            else:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    persist(conn)
            self._offline_attempts.pop(source_id, None)
            explicit_import = source["mode"] == "import"
            auto_import = row["auto_scan"] and row["auto_import"] and not row["auto_import_blocked"]
            if not (preview or relink) and unknown and (explicit_import or auto_import):
                active = conn.execute(
                    """SELECT 1 FROM import_job WHERE folder_path = ?
                       AND status IN ('queued', 'running', 'paused', 'waiting_for_source', 'cancelling') LIMIT 1""",
                    (root,),
                ).fetchone()
                if active is None:
                    self._enqueue(root, retry_paths=[{"path": photo.path, "kind": "image_unavailable"} for photo in unknown])
            if summary["relinked_files"] and not preview:
                self._changed()
        self._publish(source_id, "completed", summary["scanned_files"], summary["unavailable_files"])

    def _record_failure(self, generation, source, status: str, message: str) -> None:
        source_id = source["id"]
        try:
            with self._library_lock, closing(get_conn()) as conn, conn:
                if self._current(conn, generation, source) is None or self._stop.is_set():
                    return
                attempts = min(5, self._offline_attempts.get(source_id, 0) + 1)
                self._offline_attempts[source_id] = attempts
                delay = min(OFFLINE_RETRY * 2 ** (attempts - 1), OFFLINE_RETRY_LIMIT)
                conn.execute(
                    """UPDATE import_source SET status = ?, error = ?, due_at = ? WHERE id = ?""",
                    (status, message, time.time() + delay, source_id),
                )
            self._publish(source_id, "failed", self._running.get(source_id, 0))
        except Exception:
            logger.debug("Ignoring scan result after a library replacement", exc_info=True)
