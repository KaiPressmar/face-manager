"""Track dirty image IDs in the same SQLite transaction as local changes."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass


_MAX_PAGE_SIZE = 256
_SCOPE_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")


@dataclass(frozen=True, slots=True)
class DirtyImage:
    image_id: int
    revision: int


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise ValueError("A caller-owned database transaction is required")


def _validate_scope(scope_id: str) -> None:
    if not isinstance(scope_id, str) or not _SCOPE_ID.fullmatch(scope_id):
        raise ValueError("An opaque local scope identifier is required")


def _validate_page(scope_id: str, limit: int) -> None:
    _validate_scope(scope_id)
    if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ValueError("The outbox page limit is invalid")


def _row_triggers(table: str, key: str, watched: tuple[str, ...]):
    """Yield fixed schema triggers that mark both sides of a row move."""
    changed = " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in watched)
    yield f"{table}_insert", f"AFTER INSERT ON {table}", f"SELECT NEW.{key} AS image_id"
    yield f"{table}_delete", f"AFTER DELETE ON {table}", f"SELECT OLD.{key} AS image_id"
    yield (
        f"{table}_update",
        f"AFTER UPDATE OF {', '.join(watched)} ON {table} WHEN {changed}",
        f"SELECT OLD.{key} AS image_id UNION SELECT NEW.{key}",
    )


def _triggers():
    yield from _row_triggers(
        "image", "id", ("id", "path", "content_hash", "processed_at", "raw_width", "raw_height", "exif_orientation"),
    )
    yield from _row_triggers("image_location", "image_id", ("image_id", "path"))
    yield from _row_triggers(
        "face", "image_id", ("id", "image_id", "cluster_id", "review_status", "bbox_x", "bbox_y", "bbox_w", "bbox_h", "embedding"),
    )
    yield (
        "cluster_person",
        "AFTER UPDATE OF person_id ON cluster WHEN OLD.person_id IS NOT NEW.person_id",
        "SELECT DISTINCT image_id FROM face WHERE cluster_id = NEW.id",
    )
    yield (
        "person_name",
        "AFTER UPDATE OF name ON person WHEN OLD.name IS NOT NEW.name",
        """SELECT DISTINCT f.image_id
           FROM cluster c JOIN face f ON f.cluster_id = c.id
           WHERE c.person_id = NEW.id""",
    )


def initialize_outbox(conn: sqlite3.Connection) -> None:
    """Install tables and mutation triggers after the library schema is ready."""
    _require_transaction(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shared_sync_outbox_scope (
               id TEXT PRIMARY KEY,
               revision INTEGER NOT NULL DEFAULT 0,
               baseline_cursor INTEGER NOT NULL DEFAULT 0,
               baseline_high_water INTEGER NOT NULL
           ) WITHOUT ROWID"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shared_sync_dirty_image (
               scope_id TEXT NOT NULL REFERENCES shared_sync_outbox_scope(id) ON DELETE CASCADE,
               image_id INTEGER NOT NULL,
               revision INTEGER NOT NULL,
               PRIMARY KEY (scope_id, image_id)
           ) WITHOUT ROWID"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS shared_sync_dirty_image_revision
           ON shared_sync_dirty_image(scope_id, revision, image_id)"""
    )
    for name, event, affected_images in _triggers():
        conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS shared_sync_dirty_{name} {event}
                BEGIN
                    UPDATE shared_sync_outbox_scope SET revision = revision + 1;
                    INSERT INTO shared_sync_dirty_image (scope_id, image_id, revision)
                    SELECT scope.id, affected.image_id, scope.revision
                    FROM shared_sync_outbox_scope scope CROSS JOIN ({affected_images}) affected
                    WHERE affected.image_id > 0
                    ON CONFLICT (scope_id, image_id)
                    DO UPDATE SET revision = excluded.revision;
                END"""
        )


def register_scope(conn: sqlite3.Connection, scope_id: str) -> None:
    """Freeze the existing ID range and immediately track subsequent writes."""
    _require_transaction(conn)
    _validate_scope(scope_id)
    conn.execute(
        """INSERT INTO shared_sync_outbox_scope (id, baseline_high_water)
           VALUES (?, (SELECT COALESCE(MAX(id), 0) FROM image))
           ON CONFLICT (id) DO NOTHING""",
        (scope_id,),
    )


def retire_scope(conn: sqlite3.Connection, scope_id: str) -> None:
    """Remove an inactive scope after its in-flight acknowledgements are fenced."""
    _require_transaction(conn)
    _validate_scope(scope_id)
    conn.execute("DELETE FROM shared_sync_dirty_image WHERE scope_id = ?", (scope_id,))
    conn.execute("DELETE FROM shared_sync_outbox_scope WHERE id = ?", (scope_id,))


def enqueue_baseline_page(conn: sqlite3.Connection, scope_id: str, *, limit: int = 64) -> bool:
    """Queue a bounded part of the first inventory. Return whether work remains."""
    _require_transaction(conn)
    _validate_page(scope_id, limit)
    state = conn.execute(
        """SELECT baseline_cursor, baseline_high_water, revision
           FROM shared_sync_outbox_scope WHERE id = ?""",
        (scope_id,),
    ).fetchone()
    if state is None:
        raise ValueError("The local scope is not registered")
    cursor, high_water, revision = state
    if cursor >= high_water:
        return False
    ids = conn.execute(
        """SELECT id FROM image
           WHERE id > ? AND id <= ? AND processed_at IS NOT NULL
           ORDER BY id LIMIT ?""",
        (cursor, high_water, limit + 1),
    ).fetchall()
    more = len(ids) > limit
    page_ids = [row[0] for row in ids[:limit]]
    conn.executemany(
        """INSERT INTO shared_sync_dirty_image (scope_id, image_id, revision)
           VALUES (?, ?, ?)
           ON CONFLICT (scope_id, image_id) DO NOTHING""",
        ((scope_id, image_id, revision + 1) for image_id in page_ids),
    )
    conn.execute(
        """UPDATE shared_sync_outbox_scope SET baseline_cursor = ?, revision = revision + 1
           WHERE id = ?""",
        (page_ids[-1] if more else high_water, scope_id),
    )
    return more


def pending_changes(
    conn: sqlite3.Connection, scope_id: str, *, limit: int = 64, after: DirtyImage | None = None,
) -> tuple[DirtyImage, ...]:
    """Read opaque local IDs, oldest dirty revisions first.

    Before publishing anything, use the approved source scope to capture and
    verify the photo. A queued ID alone says nothing about membership or consent.
    Pass the last visited item to continue a scan past deferred changes. Restart
    without a cursor to retry skipped items when the scan reaches the end.
    """
    _require_transaction(conn)
    _validate_page(scope_id, limit)
    start = (after.revision, after.image_id) if after is not None else (0, 0)
    return tuple(
        DirtyImage(*row)
        for row in conn.execute(
            """SELECT image_id, revision FROM shared_sync_dirty_image
               WHERE scope_id = ? AND (revision, image_id) > (?, ?)
               ORDER BY revision, image_id LIMIT ?""",
            (scope_id, *start, limit),
        )
    )


def acknowledge_changes(conn: sqlite3.Connection, scope_id: str, changes: Iterable[DirtyImage]) -> int:
    """Clear only captured revisions; a newer edit survives a delayed receipt."""
    _require_transaction(conn)
    _validate_scope(scope_id)
    rows = conn.executemany(
        """DELETE FROM shared_sync_dirty_image WHERE scope_id = ? AND image_id = ? AND revision = ?""",
        ((scope_id, item.image_id, item.revision) for item in changes),
    )
    return rows.rowcount


def revisions_match(conn: sqlite3.Connection, scope_id: str, changes: Iterable[DirtyImage]) -> bool:
    """Check a prepared publication against current revisions without clearing them."""
    _require_transaction(conn)
    _validate_scope(scope_id)
    for change in changes:
        row = conn.execute(
            "SELECT revision FROM shared_sync_dirty_image WHERE scope_id = ? AND image_id = ?",
            (scope_id, change.image_id),
        ).fetchone()
        if row is None or row[0] != change.revision:
            return False
    return True
