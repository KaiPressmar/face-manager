"""Transactional local undo and redo for individual face assignments."""

from __future__ import annotations

import json
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from ..db.schema import get_conn

_ACTIONS = ("assign_person", "mark_unknown_person", "mark_not_face")
_HISTORY_LIMIT = 50
_PREVIEW_LIMIT = 10
_SUMMARY_FIELDS = "id, action, person_name, face_count, created_at, undone"
_CONFLICT = "Die betroffenen Gesichter oder ihre Zuordnungen wurden inzwischen geändert."
_STALE = "Der Änderungsverlauf wurde inzwischen geändert. Lade ihn erneut."


class FaceActionConflict(ValueError):
    """The history head or its current face evidence no longer matches."""


def initialize_face_action_history(cur):
    """Add a local history and watch only faces that have appeared in it."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS face_action_history (
            position INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL,
            person_name TEXT,
            face_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0,
            before_state TEXT NOT NULL, after_state TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS face_action_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            revision INTEGER NOT NULL DEFAULT 0,
            pause_watch INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("INSERT OR IGNORE INTO face_action_state(id) VALUES (1)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS face_action_watch (
            face_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
        )
    """)
    triggers = (
        ("face_update", """AFTER UPDATE OF image_id, cluster_id, review_status, bbox_x, bbox_y, bbox_w, bbox_h, is_manual ON face
                           WHEN OLD.image_id IS NOT NEW.image_id OR OLD.cluster_id IS NOT NEW.cluster_id
                             OR OLD.review_status IS NOT NEW.review_status OR OLD.is_manual IS NOT NEW.is_manual
                             OR OLD.bbox_x IS NOT NEW.bbox_x OR OLD.bbox_y IS NOT NEW.bbox_y
                             OR OLD.bbox_w IS NOT NEW.bbox_w OR OLD.bbox_h IS NOT NEW.bbox_h""", "OLD.id"),
        ("face_delete", "AFTER DELETE ON face", "OLD.id"),
        ("cluster_update", """AFTER UPDATE OF label, person_id ON cluster
                              WHEN OLD.label IS NOT NEW.label OR OLD.person_id IS NOT NEW.person_id""",
         "SELECT id FROM face WHERE cluster_id = OLD.id"),
        ("person_update", "AFTER UPDATE OF name ON person WHEN OLD.name IS NOT NEW.name",
         "SELECT f.id FROM face f JOIN cluster c ON c.id = f.cluster_id WHERE c.person_id = OLD.id"),
        ("image_update", "AFTER UPDATE OF content_hash ON image WHEN OLD.content_hash IS NOT NEW.content_hash",
         "SELECT id FROM face WHERE image_id = OLD.id"),
    )
    for name, event, affected in triggers:
        cur.execute(f"""
            CREATE TRIGGER IF NOT EXISTS face_action_{name} {event}
            BEGIN
                UPDATE face_action_watch SET revision = revision + 1
                WHERE face_id IN ({affected}) AND (SELECT pause_watch FROM face_action_state WHERE id = 1) = 0;
            END
        """)


def face_action_watch_available(cur):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'face_action_state'"
    ).fetchone() is not None


@contextmanager
def pause_face_action_watch(cur, *, enabled=True):
    """Suspend external-change tracking inside a known, atomic transition."""
    if enabled:
        cur.execute("UPDATE face_action_state SET pause_watch = 1 WHERE id = 1")
    try:
        yield
    finally:
        if enabled:
            cur.execute("UPDATE face_action_state SET pause_watch = 0 WHERE id = 1")


def _json(value):
    return json.dumps(value, separators=(",", ":"))


def _selected_states(cur, face_ids):
    return [dict(row) for row in cur.execute("""
        SELECT f.id, f.image_id, f.is_manual, i.content_hash,
               f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h,
               f.review_status, f.cluster_id, c.label AS cluster_label, c.person_id, p.name AS person_name,
               COALESCE(w.revision, 0) AS watch_revision
        FROM face f JOIN image i ON i.id = f.image_id
        LEFT JOIN cluster c ON c.id = f.cluster_id LEFT JOIN person p ON p.id = c.person_id
        LEFT JOIN face_action_watch w ON w.face_id = f.id
        WHERE f.id IN (SELECT value FROM json_each(?)) ORDER BY f.id
    """, (_json(face_ids),))]


def _summary(row) -> dict:
    return {
        "id": row["id"], "action": row["action"], "face_count": row["face_count"],
        "person_name": row["person_name"], "created_at": row["created_at"], "undone": bool(row["undone"]),
    }


def record_face_action(cur, face_ids, action, mutation):
    """Run a normal assignment/archive mutation with a journal in its SQLite transaction."""
    if not cur.connection.in_transaction or action not in _ACTIONS:
        raise RuntimeError("A face action must have a write transaction and a reversible operation")
    before = _selected_states(cur, face_ids)
    if len(before) != len(face_ids):
        raise LookupError("Mindestens ein ausgewähltes Gesicht wurde nicht gefunden.")
    with pause_face_action_watch(cur):
        result = mutation()
    after = _selected_states(cur, face_ids)
    changed = [(previous, current) for previous, current in zip(before, after) if previous != current]
    if not changed:
        return result, None
    previous, current = map(list, zip(*changed))
    cur.executemany("INSERT OR IGNORE INTO face_action_watch(face_id) VALUES (?)", ((row["id"],) for row in previous))
    cur.execute("DELETE FROM face_action_history WHERE undone = 1")
    entry = {
        "id": uuid4().hex, "action": action, "face_count": len(changed),
        "person_name": current[0]["person_name"] if action == "assign_person" else None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "undone": False,
    }
    cur.execute("""
        INSERT INTO face_action_history (id, action, person_name, face_count, created_at, before_state, after_state)
        VALUES (:id, :action, :person_name, :face_count, :created_at, :before_state, :after_state)
    """, {**entry, "before_state": _json(previous), "after_state": _json(current)})
    cur.execute("UPDATE face_action_state SET revision = revision + 1 WHERE id = 1")
    cur.execute("""
        DELETE FROM face_action_history WHERE position < (
            SELECT position FROM face_action_history ORDER BY position DESC LIMIT 1 OFFSET ?
        )
    """, (_HISTORY_LIMIT - 1,))
    return result, entry


def _history_head(cur, undone, *, with_states=False):
    order = "ASC" if undone else "DESC"
    fields = "*" if with_states else _SUMMARY_FIELDS
    return cur.execute(
        f"SELECT {fields} FROM face_action_history WHERE undone = ? ORDER BY position {order} LIMIT 1", (undone,),
    ).fetchone()


def _history(cur, library_id, *, blocked_reason=None):
    revision = cur.execute("SELECT revision FROM face_action_state WHERE id = 1").fetchone()[0]
    if blocked_reason:
        return {"library_id": library_id, "revision": revision, "undo": None, "redo": None,
                "entries": [], "blocked_reason": blocked_reason}
    undo, redo = (_history_head(cur, undone) for undone in (False, True))
    entries = cur.execute(
        f"SELECT {_SUMMARY_FIELDS} FROM face_action_history ORDER BY position DESC LIMIT ?", (_PREVIEW_LIMIT,),
    ).fetchall()
    return {
        "library_id": library_id, "revision": revision, "undo": _summary(undo) if undo else None,
        "redo": _summary(redo) if redo else None, "entries": [_summary(row) for row in entries],
    }


def face_action_history(library_id, *, blocked_reason=None):
    with closing(get_conn()) as conn, conn:
        conn.execute("BEGIN")
        return _history(conn.cursor(), library_id, blocked_reason=blocked_reason)


def _same_assignment(actual, expected):
    # Trusted reclustering may regenerate active groups without changing the
    # person or archive decision. The watch revision catches manual regrouping.
    if actual["review_status"] == expected["review_status"] == "active":
        ignored = {"cluster_id", "cluster_label"}
        return all(actual[key] == value for key, value in expected.items() if key not in ignored)
    return actual == expected


def _cluster_identity(state):
    return state["cluster_label"], state["person_id"]


def _restore_states(cur, actual, desired):
    active_clusters = {row["cluster_id"]: _cluster_identity(row) for row in actual if row["cluster_id"] is not None}
    target_clusters = {row["cluster_id"]: row for row in desired if row["cluster_id"] is not None}
    affected_ids = _json([row["id"] for row in actual])
    for cluster_id, target in target_clusters.items():
        identity = _cluster_identity(target)
        if target["person_id"] is not None:
            person = cur.execute("SELECT name FROM person WHERE id = ?", (target["person_id"],)).fetchone()
            if person is None or person[0] != target["person_name"]:
                raise FaceActionConflict("Die frühere Person wurde inzwischen geändert oder gelöscht.")
        cluster = cur.execute("SELECT label, person_id FROM cluster WHERE id = ?", (cluster_id,)).fetchone()
        if cluster is None:
            cur.execute("INSERT INTO cluster(id, label, person_id) VALUES (?, ?, ?)", (cluster_id, *identity))
            continue
        if tuple(cluster) != identity:
            outsiders = cur.execute("""
                SELECT 1 FROM face WHERE cluster_id = ?
                AND id NOT IN (SELECT value FROM json_each(?)) LIMIT 1
            """, (cluster_id, affected_ids)).fetchone()
            if outsiders or active_clusters.get(cluster_id) != tuple(cluster):
                raise FaceActionConflict("Eine frühere Gesichtsgruppe wurde inzwischen geändert.")
            cur.execute("UPDATE cluster SET label = ?, person_id = ? WHERE id = ?", (*identity, cluster_id))
    cur.executemany(
        "UPDATE face SET cluster_id = ?, review_status = ? WHERE id = ?",
        ((row["cluster_id"], row["review_status"], row["id"]) for row in desired),
    )
    cur.executemany("""
        DELETE FROM cluster WHERE id = ? AND NOT EXISTS (SELECT 1 FROM face WHERE cluster_id = cluster.id)
    """, ((cluster_id,) for cluster_id in active_clusters.keys() - target_clusters.keys()))


def replay_face_action(action_id, revision, library_id, *, redo=False):
    """Apply one matching head once, returning the new history snapshot."""
    from .storage import get_person_suggestion_epoch, invalidate_image_query_cache, mark_persons_dirty_for_recluster

    with closing(get_conn()) as conn, conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        if library_id != get_person_suggestion_epoch():
            raise FaceActionConflict("Die Bibliothek wurde gewechselt. Lade den Änderungsverlauf neu.")
        entry = _history_head(cur, redo, with_states=True)
        previous_revision = cur.execute("SELECT revision FROM face_action_state WHERE id = 1").fetchone()[0]
        if entry is None or entry["id"] != action_id or previous_revision != revision:
            raise FaceActionConflict(_STALE)
        before, after = (json.loads(entry[key]) for key in ("before_state", "after_state"))
        expected, desired = (before, after) if redo else (after, before)
        actual = _selected_states(cur, [row["id"] for row in expected])
        if len(actual) != len(expected) or any(not _same_assignment(row, snapshot) for row, snapshot in zip(actual, expected)):
            raise FaceActionConflict(_CONFLICT)
        with pause_face_action_watch(cur):
            _restore_states(cur, actual, desired)
        mark_persons_dirty_for_recluster(cur, {row["person_id"] for row in [*actual, *desired]})
        cur.execute("UPDATE face_action_history SET undone = ? WHERE id = ?", (not redo, action_id))
        cur.execute("UPDATE face_action_state SET revision = revision + 1 WHERE id = 1")
        action = {**_summary(entry), "undone": not redo}
        history = _history(cur, library_id)
    invalidate_image_query_cache()
    return {"status": "ok", "action": action, "history": history}
