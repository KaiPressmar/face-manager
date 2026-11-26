"""Repair explicit archive contradictions and review evidence from local backups."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..db.schema import get_conn
from .shared_sync_backup import _sync_directory
from .shared_sync_merge import _intersection_over_union
from .shared_sync_snapshot import _bbox, _geometry

logger = logging.getLogger("face_manager.archive_repair")
_ARCHIVED = ("unknown_person", "not_face")
_HASH = re.compile(r"[a-f0-9]{64}", re.IGNORECASE)
_MAX_BACKUPS = 8
_MAX_FACES = 200_000
_MAX_SELECTION = 200
_LINEAGE = "archive_repair_library_lineage"
_BACKUP_PATTERNS = {
    "update-backups": "database-*.sqlite",
    "shared-library-backups": "database-pre-share-*.sqlite",
    "restore-backups": "database-before-restore-*.sqlite",
    "repair-snapshots": "*.sqlite",
}
_FIELDS = """f.id AS face_id, f.image_id, f.cluster_id, f.review_status,
             f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, c.label AS cluster_label,
             p.id AS person_id, p.name AS person_name, i.content_hash,
             i.raw_width, i.raw_height, i.exif_orientation"""
_JOIN = """FROM face f JOIN image i ON i.id = f.image_id
           LEFT JOIN cluster c ON c.id = f.cluster_id LEFT JOIN person p ON p.id = c.person_id"""


def initialize_archive_repair(connection):
    """Journal before-state with the mutation and keep a stable snapshot lineage."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS archive_repair_batch (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
            snapshot_name TEXT NOT NULL, faces_before TEXT NOT NULL, undone_at TEXT
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS archive_repair_watch (
            batch_id TEXT NOT NULL REFERENCES archive_repair_batch(id) ON DELETE CASCADE,
            face_id INTEGER NOT NULL, changed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (batch_id, face_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_archive_repair_watch_face ON archive_repair_watch(face_id)")
    connection.execute("""
        CREATE TRIGGER IF NOT EXISTS archive_repair_face_changed
        AFTER UPDATE OF image_id, cluster_id, review_status, bbox_x, bbox_y, bbox_w, bbox_h ON face
        WHEN OLD.image_id IS NOT NEW.image_id OR OLD.cluster_id IS NOT NEW.cluster_id
          OR OLD.review_status IS NOT NEW.review_status OR OLD.bbox_x IS NOT NEW.bbox_x
          OR OLD.bbox_y IS NOT NEW.bbox_y OR OLD.bbox_w IS NOT NEW.bbox_w OR OLD.bbox_h IS NOT NEW.bbox_h
        BEGIN UPDATE archive_repair_watch SET changed = 1 WHERE face_id = OLD.id; END
    """)
    connection.execute("""
        CREATE TRIGGER IF NOT EXISTS archive_repair_face_deleted AFTER DELETE ON face
        BEGIN UPDATE archive_repair_watch SET changed = 1 WHERE face_id = OLD.id; END
    """)
    connection.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)", (_LINEAGE, uuid4().hex))
    connection.commit()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT {_FIELDS} {_JOIN} WHERE f.review_status IN (?, ?) AND p.id IS NOT NULL",
                _ARCHIVED,
            ).fetchall()
            if rows:
                _record_and_detach(connection, [(dict(row), row["review_status"]) for row in rows], "automatic")
    except (OSError, ValueError, sqlite3.Error):
        # The live library remains usable and the repair can safely retry at the next launch.
        logger.warning("Archive integrity repair deferred because its recovery copy or transaction failed", exc_info=True)


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_file(connection):
    filename = connection.execute("PRAGMA database_list").fetchone()[2]
    return Path(filename).resolve() if filename else None


def _save_recovery_copy(connection, batch_id):
    """Snapshot the last committed state from a separate reader while writes are locked."""
    source_path = _database_file(connection)
    if source_path is None:
        raise ValueError("A local database file is required to back up archive changes")
    directory = source_path.parent / "archive-repair-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("The archive recovery directory was redirected")
    _sync_directory(directory.parent)
    destination = directory / f"database-before-archive-repair-{batch_id}.sqlite"
    fd, name = tempfile.mkstemp(suffix=".partial", dir=directory)
    os.close(fd)
    partial = Path(name)
    deadline = time.monotonic() + 20

    def check_deadline(*_):
        if time.monotonic() > deadline:
            raise TimeoutError("The archive recovery copy took too long")

    try:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as reader:
            with closing(sqlite3.connect(partial)) as snapshot:
                reader.backup(snapshot, pages=256, progress=check_deadline, sleep=0.025)
                snapshot.execute("PRAGMA journal_mode = DELETE")
                snapshot.set_progress_handler(lambda: int(time.monotonic() > deadline), 5_000)
                if snapshot.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                    raise sqlite3.DatabaseError("Archive recovery copy failed validation")
        with partial.open("rb+") as synced:
            os.fsync(synced.fileno())
        partial.rename(destination)
        _sync_directory(directory)
        return destination.name
    finally:
        partial.unlink(missing_ok=True)


def _mark_people_dirty(connection, selected):
    person_ids = {row["person_id"] for row, _ in selected if row["person_id"] is not None}
    connection.executemany(
        "INSERT OR IGNORE INTO recluster_dirty_person(person_id) VALUES (?)",
        ((person_id,) for person_id in sorted(person_ids)),
    )


def _record_and_detach(connection, selected, kind):
    batch_id = uuid4().hex
    snapshot_name = _save_recovery_copy(connection, batch_id)
    created_at = _timestamp()
    connection.execute(
        """INSERT INTO archive_repair_batch (id, kind, created_at, snapshot_name, faces_before)
           VALUES (?, ?, ?, ?, ?)""",
        (batch_id, kind, created_at, snapshot_name, json.dumps(selected, separators=(",", ":"))),
    )
    connection.executemany(
        "UPDATE face SET cluster_id = NULL, review_status = ? WHERE id = ?",
        ((status, row["face_id"]) for row, status in selected),
    )
    if kind == "confirmed":
        connection.executemany(
            "INSERT INTO archive_repair_watch(batch_id, face_id) VALUES (?, ?)",
            ((batch_id, row["face_id"]) for row, _ in selected),
        )
    _mark_people_dirty(connection, selected)
    return {"batch_id": batch_id, "count": len(selected), "created_at": created_at, "snapshot_name": snapshot_name}


def _file_identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _candidate_backups(directory):
    candidates = []
    for folder, pattern in _BACKUP_PATTERNS.items():
        location = directory / folder
        try:
            if location.resolve() != location:
                continue
            for path in location.glob(pattern):
                if not path.is_symlink() and path.is_file() and path.resolve() == path:
                    identity = _file_identity(path)
                    candidates.append((identity[3], path, identity))
        except (OSError, RuntimeError, ValueError):
            logger.debug("Could not list an archive repair backup directory", exc_info=True)
    return sorted(candidates, reverse=True)


def _open_snapshot(path, *, scan_seconds=3):
    # Only managed local snapshot files, with SQLite changes and extension
    # loading disabled. Immutable opens avoid creating journals or WAL files.
    for suffix in ("-wal", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError("Uncommitted snapshot sidecar")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0.15)
    try:
        connection.row_factory = sqlite3.Row
        deadline = time.monotonic() + scan_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        required_tables = {"image", "face", "cluster", "person"}
        actual_tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND sql LIKE 'CREATE TABLE %'"
            )
        }
        if not required_tables <= actual_tables:
            raise ValueError("Snapshot has no supported library schema")
    except (sqlite3.Error, ValueError):
        connection.close()
        raise
    return connection


def _read_lineage(connection):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'app_settings' AND type = 'table'").fetchone():
        row = connection.execute("SELECT value FROM app_settings WHERE key = ?", (_LINEAGE,)).fetchone()
        if row is not None:
            return row[0]
    return None


def _lineage_anchors(connection):
    return {
        (row["id"], row["content_hash"].lower(), row["path"], row["processed_at"])
        for row in connection.execute(
            """SELECT id, content_hash, path, processed_at FROM image
               WHERE content_hash IS NOT NULL AND processed_at IS NOT NULL ORDER BY id LIMIT 512"""
        )
        if isinstance(row["content_hash"], str) and _HASH.fullmatch(row["content_hash"])
    }


def _same_library(connection, active_lineage, active_anchors):
    snapshot_lineage = _read_lineage(connection)
    if snapshot_lineage is not None:
        return snapshot_lineage == active_lineage
    # Before lineage was recorded, require two identical database import
    # records, including local row identity, image bytes hash, path and time.
    return len(active_anchors & _lineage_anchors(connection)) >= 2


def register_archive_repair_snapshot(payload: bytes):
    """Register an exported copy as evidence, without restoring its contents."""
    invalid_message = "Die ausgewählte Datei ist keine gültige Face-Manager-Sicherung für den Vergleich."
    if not payload.startswith(b"SQLite format 3\x00"):
        raise ValueError(invalid_message)

    with closing(get_conn()) as connection:
        database = _database_file(connection)
        if database is None:
            raise OSError("A local data directory is needed for archive snapshots")
        directory = database.parent / "repair-snapshots"
        if directory.resolve() != directory:
            raise OSError("The archive snapshot directory was redirected")
        directory.mkdir(mode=0o700, exist_ok=True)
        _sync_directory(directory.parent)
        destination = directory / f"registered-{hashlib.sha256(payload).hexdigest()}.sqlite"
        descriptor, name = tempfile.mkstemp(prefix=".registration-", suffix=".partial", dir=directory)
        partial = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as staged:
                staged.write(payload)
                staged.flush()
                os.fsync(staged.fileno())
            with connection:
                try:
                    snapshot = _open_snapshot(partial, scan_seconds=20)
                except (sqlite3.Error, ValueError) as exc:
                    raise ValueError(invalid_message) from exc
                with closing(snapshot):
                    try:
                        snapshot.execute(f"SELECT {_FIELDS} {_JOIN} LIMIT 0")
                        check = snapshot.execute("PRAGMA quick_check(1)").fetchone()
                        if check is None or check[0] != "ok":
                            raise sqlite3.DatabaseError("Archive evidence failed its integrity check")
                    except sqlite3.Error as exc:
                        raise ValueError(invalid_message) from exc
                    connection.execute("BEGIN IMMEDIATE")
                    live_lineage, live_anchors = _read_lineage(connection), _lineage_anchors(connection)
                    try:
                        same_library = _same_library(snapshot, live_lineage, live_anchors)
                    except sqlite3.Error as exc:
                        raise ValueError(invalid_message) from exc
                if not same_library:
                    raise ValueError("Die ausgewählte Sicherung gehört nicht nachweisbar zu dieser Fotobibliothek.")
                if directory.resolve() != directory:
                    raise OSError("The archive snapshot directory was redirected")
                partial.replace(destination)
                _sync_directory(directory)
        finally:
            partial.unlink(missing_ok=True)
    return {"snapshot_name": destination.name}


def _read_faces(connection, *, include_filename=False):
    grouped = defaultdict(list)
    filename_field = ", i.filename AS photo_filename" if include_filename else ""
    for index, row in enumerate(connection.execute(
        f"""SELECT {_FIELDS}{filename_field} {_JOIN} WHERE length(i.content_hash) = 64
            AND i.raw_width > 0 AND i.raw_height > 0 AND i.exif_orientation BETWEEN 1 AND 8"""
    )):
        if index >= _MAX_FACES:
            raise ValueError("Archive evidence exceeds the scan limit")
        digest = row["content_hash"]
        if isinstance(digest, str) and _HASH.fullmatch(digest):
            grouped[digest.lower()].append(dict(row))
    return grouped


def _reciprocal_matches(active, old):
    if len(active) > 100 or len(old) > 100:
        return (), set()
    # Keep the hash plus raw geometry tied to one canonical photo on each side.
    if len({row["image_id"] for row in active}) != 1 or len({row["image_id"] for row in old}) != 1:
        return (), set()
    if _geometry(active[0]) != _geometry(old[0]):
        return (), set()
    active_boxes = [_bbox(row, _geometry(row)) for row in active]
    old_boxes = [_bbox(row, _geometry(row)) for row in old]
    matches = [
        [
            index for index, other in enumerate(old_boxes)
            if box is not None and other is not None and _intersection_over_union(box, other) >= 0.8
        ]
        for box in active_boxes
    ]
    archival_hints = {
        row["face_id"] for row, choices in zip(active, matches)
        if row["review_status"] == "active" and row["person_id"] is not None
        and any(old[index]["review_status"] in _ARCHIVED for index in choices)
    }
    # Unknown geometry could overlap any valid face and therefore prevents a
    # unique match on a photo with multiple faces.
    if None in active_boxes or None in old_boxes:
        return (), archival_hints
    reverse = defaultdict(int)
    for choices in matches:
        for index in choices:
            reverse[index] += 1
    reciprocal = [
        (row, old[choices[0]]) for row, choices in zip(active, matches)
        if len(choices) == 1 and reverse[choices[0]] == 1
    ]
    return reciprocal, archival_hints


def _token(row, status, source_name, source_identity, generation, lineage):
    encoded = json.dumps(
        [row, status, source_name, source_identity, generation, lineage], sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _scan_archive_evidence(connection):
    directory = _database_file(connection)
    if directory is None:
        return [], 0, 0, False, None, []
    backups = _candidate_backups(directory.parent)
    if not backups:
        return [], 0, 0, False, None, []
    active_lineage = _read_lineage(connection)
    active_anchors = _lineage_anchors(connection)
    generation = connection.execute("SELECT generation FROM person_suggestion_state WHERE id = 1").fetchone()[0]
    try:
        live = _read_faces(connection, include_filename=True)
    except ValueError:
        return [], 0, 0, True, generation, backups

    checked = 0
    proposals = {}
    conflicts = set()
    archival_hints = set()
    for stamp, path, identity in backups[:_MAX_BACKUPS]:
        try:
            with closing(_open_snapshot(path)) as snapshot:
                if not _same_library(snapshot, active_lineage, active_anchors):
                    continue
                previous = _read_faces(snapshot)
                if _file_identity(path) != identity:
                    continue
        except (OSError, sqlite3.Error, ValueError):
            logger.debug("A local backup could not be used as evidence for archive repair", exc_info=True)
            continue
        checked += 1
        for digest in live.keys() & previous.keys():
            matches, hints = _reciprocal_matches(live[digest], previous[digest])
            archival_hints.update(hints)
            matched_ids = {current["face_id"] for current, _ in matches}
            # A newer or older backup on the same photo without an unambiguous
            # match provides no reassurance that an apparent change is valid.
            conflicts.update(row["face_id"] for row in live[digest] if row["face_id"] not in matched_ids)
            for current, old in matches:
                if current["review_status"] != "active" or current["person_id"] is None:
                    continue
                face_id = current["face_id"]
                status = old["review_status"]
                if status not in _ARCHIVED:
                    conflicts.add(face_id)
                    continue
                existing = proposals.get(face_id)
                if existing is None:
                    proposals[face_id] = current, status, path.name, stamp, identity
                elif existing[1] != status:
                    conflicts.add(face_id)

    cases = []
    for face_id in sorted(proposals.keys() - conflicts):
        row, status, name, stamp, identity = proposals[face_id]
        fields = {key: value for key, value in row.items() if key != "photo_filename"}
        cases.append((fields, {
            "face_id": face_id, "image_id": row["image_id"], "person_name": row["person_name"],
            "photo_filename": row["photo_filename"] or None,
            "restore_status": status, "source_name": name,
            "source_date": datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat(timespec="seconds"),
            "token": _token(fields, status, name, identity, generation, active_lineage),
        }))
    truncated = len(backups) > _MAX_BACKUPS or len(cases) > _MAX_SELECTION
    return cases[:_MAX_SELECTION], checked, len(archival_hints & conflicts), truncated, generation, backups


def _batch_summary(row):
    return {
        "batch_id": row["id"], "count": len(json.loads(row["faces_before"])),
        "created_at": row["created_at"], "snapshot_name": row["snapshot_name"],
    }


def preview_archive_repair():
    """Show historical archive changes without writing or inferring user intent."""
    with closing(get_conn()) as connection:
        connection.execute("BEGIN")
        cases, checked, skipped_conflicts, truncated, _, _ = _scan_archive_evidence(connection)
        automatic = connection.execute(
            "SELECT faces_before FROM archive_repair_batch WHERE kind = 'automatic'",
        ).fetchall()
        batch = connection.execute(
            """SELECT * FROM archive_repair_batch WHERE kind = 'confirmed' AND undone_at IS NULL
               ORDER BY rowid DESC LIMIT 1""",
        ).fetchone()
        return {
            "candidates": [preview for _, preview in cases], "checked_backups": checked,
            "skipped_conflicts": skipped_conflicts,
            "automatic_repaired": sum(len(json.loads(row[0])) for row in automatic),
            "latest_batch": _batch_summary(batch) if batch else None, "truncated": truncated,
        }


def _face_rows(connection, face_ids):
    return {
        row["face_id"]: dict(row)
        for row in connection.execute(
            f"SELECT {_FIELDS} {_JOIN} WHERE f.id IN (SELECT value FROM json_each(?))",
            (json.dumps(face_ids),),
        )
    }


def apply_archive_repair(tokens: list[str]):
    """Apply an explicitly selected batch only if all evidence and active rows still agree."""
    if not (1 <= len(tokens) <= _MAX_SELECTION) or len(set(tokens)) != len(tokens):
        raise ValueError("Bitte wähle zwischen 1 und 200 eindeutige Reparaturvorschläge aus.")
    with closing(get_conn()) as connection:
        with connection:
            connection.execute("BEGIN")
            cases, _, _, _, generation, sources = _scan_archive_evidence(connection)
            candidates = {item["token"]: (row, item["restore_status"]) for row, item in cases}
        if any(token not in candidates for token in tokens):
            raise LookupError("Die Vorschau ist veraltet. Bitte lade die Vorschläge erneut.")
        selected = [candidates[token] for token in tokens]
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            now = connection.execute("SELECT generation FROM person_suggestion_state WHERE id = 1").fetchone()[0]
            current = _face_rows(connection, [row["face_id"] for row, _ in selected])
            if generation != now or any(current.get(row["face_id"]) != row for row, _ in selected):
                raise LookupError("Die Bibliothek wurde seit der Vorschau geändert. Bitte lade die Vorschläge erneut.")
            if sources != _candidate_backups(_database_file(connection).parent):
                raise LookupError("Die Sicherungen wurden geändert. Bitte lade die Vorschläge erneut.")
            return _record_and_detach(connection, selected, "confirmed")


def undo_archive_repair(batch_id: str):
    """Restore a confirmed batch only while affected faces still match the repair."""
    with closing(get_conn()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            """SELECT * FROM archive_repair_batch WHERE kind = 'confirmed' AND undone_at IS NULL
               ORDER BY rowid DESC LIMIT 1""",
        ).fetchone()
        if batch is None or batch["id"] != batch_id:
            raise LookupError("Diese Reparatur kann nicht mehr rückgängig gemacht werden.")
        changed = connection.execute(
            "SELECT 1 FROM archive_repair_watch WHERE batch_id = ? AND changed = 1 LIMIT 1", (batch_id,),
        ).fetchone()
        if changed:
            raise LookupError("Gesichter wurden seit der Reparatur erneut bearbeitet.")
        selected = json.loads(batch["faces_before"])
        current = _face_rows(connection, [before["face_id"] for before, _ in selected])
        for before, status in selected:
            expected = {
                **before, "review_status": status, "cluster_id": None,
                "cluster_label": None, "person_id": None, "person_name": None,
            }
            person = connection.execute("SELECT name FROM person WHERE id = ?", (before["person_id"],)).fetchone()
            if current.get(before["face_id"]) != expected or person is None or person[0] != before["person_name"]:
                raise LookupError("Gesichter oder Personen wurden seit der Reparatur geändert.")
        _save_recovery_copy(connection, uuid4().hex)
        clusters = {}
        for before, _ in selected:
            original_id = before["cluster_id"]
            if original_id not in clusters:
                cluster = connection.execute("SELECT label, person_id FROM cluster WHERE id = ?", (original_id,)).fetchone()
                identity = before["cluster_label"], before["person_id"]
                if cluster is None or tuple(cluster) != identity:
                    original = connection.execute("INSERT INTO cluster(label, person_id) VALUES (?, ?)", identity)
                    clusters[original_id] = original.lastrowid
                else:
                    clusters[original_id] = original_id
            connection.execute(
                "UPDATE face SET review_status = ?, cluster_id = ? WHERE id = ?",
                (before["review_status"], clusters[original_id], before["face_id"]),
            )
        _mark_people_dirty(connection, selected)
        connection.execute("UPDATE archive_repair_batch SET undone_at = ? WHERE id = ?", (_timestamp(), batch_id))
        connection.execute("DELETE FROM archive_repair_watch WHERE batch_id = ?", (batch_id,))
        return _batch_summary(batch)
