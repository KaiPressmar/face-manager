import hashlib
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH
from ..error_logging import configure_error_logging

configure_error_logging()
logger = logging.getLogger("face_manager.db")

HASH_CHUNK_SIZE = 1024 * 1024
DATABASE_RECOVERY_LOCK = threading.Lock()
FACE_REVIEW_STATUS_ACTIVE = "active"
FACE_REVIEW_STATUS_UNKNOWN_PERSON = "unknown_person"
FACE_REVIEW_STATUS_NOT_FACE = "not_face"
VALID_FACE_REVIEW_STATUSES = {
    FACE_REVIEW_STATUS_ACTIVE,
    FACE_REVIEW_STATUS_UNKNOWN_PERSON,
    FACE_REVIEW_STATUS_NOT_FACE,
}
RECOVERABLE_DATABASE_ERROR_MARKERS = (
    "database disk image is malformed",
    "malformed",
    "not a database",
    "file is not a database",
)


def _normalize_person_name_key(name: str) -> str:
    """Normalize a person name for case-insensitive deduplication."""
    return (name or "").strip().casefold()


def _repair_person_name_duplicates(cur):
    """Merge duplicate people that differ only by case or surrounding whitespace."""
    rows = cur.execute(
        "SELECT id, name FROM person ORDER BY id"
    ).fetchall()
    grouped = {}
    for row in rows:
        normalized_name = (row["name"] or "").strip()
        if not normalized_name:
            continue
        key = _normalize_person_name_key(row["name"])
        grouped.setdefault(key, []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "trimmed_name": normalized_name,
            }
        )

    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        duplicates.sort(
            key=lambda row: (
                0 if row["name"] == row["trimmed_name"] else 1,
                row["id"],
            )
        )
        canonical = duplicates[0]
        duplicate_ids = [row["id"] for row in duplicates[1:]]
        placeholders = ",".join("?" for _ in duplicate_ids)
        cur.execute(
            f"""
            UPDATE cluster
            SET person_id = ?
            WHERE person_id IN ({placeholders})
            """,
            (canonical["id"], *duplicate_ids),
        )
        cur.execute(
            f"DELETE FROM person WHERE id IN ({placeholders})",
            duplicate_ids,
        )
        if canonical["name"] != canonical["trimmed_name"]:
            cur.execute(
                "UPDATE person SET name = ? WHERE id = ?",
                (canonical["trimmed_name"], canonical["id"]),
            )


def _ensure_person_name_indexes(cur):
    """Enforce case-insensitive uniqueness for person names."""
    _repair_person_name_duplicates(cur)
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_person_name_normalized
        ON person(TRIM(name) COLLATE NOCASE)
        """
    )


def _reassign_zero_cluster_id(cur):
    """Migrate the legacy cluster id 0 onto a fresh positive identifier.

    Older clustering runs numbered clusters from 0. A cluster id of 0 is falsy
    in both Python and JavaScript, which lets it slip through "no cluster"
    guards and, when the row is missing entirely, leaves faces pointing at a
    cluster that never appears in the overview. Rewriting id 0 to a normal
    positive id removes the ambiguity for good.
    """
    references_zero = cur.execute(
        """
        SELECT 1
        FROM face
        WHERE cluster_id = 0
        LIMIT 1
        """
    ).fetchone()
    zero_cluster = cur.execute(
        "SELECT id, label, person_id FROM cluster WHERE id = 0"
    ).fetchone()
    if references_zero is None and zero_cluster is None:
        return

    label = zero_cluster["label"] if zero_cluster is not None else None
    person_id = zero_cluster["person_id"] if zero_cluster is not None else None
    cur.execute(
        "INSERT INTO cluster(label, person_id) VALUES (?, ?)",
        (label, person_id),
    )
    new_cluster_id = int(cur.lastrowid)
    cur.execute(
        "UPDATE face SET cluster_id = ? WHERE cluster_id = 0",
        (new_cluster_id,),
    )
    cur.execute("DELETE FROM cluster WHERE id = 0")
    logger.info(
        "Reassigned legacy cluster id 0 to cluster %s during integrity repair",
        new_cluster_id,
    )


def _repair_cluster_integrity(cur):
    """Repair common cluster-table inconsistencies."""
    _reassign_zero_cluster_id(cur)

    missing_cluster_ids = [
        row["cluster_id"]
        for row in cur.execute(
            """
            SELECT DISTINCT f.cluster_id
            FROM face f
            LEFT JOIN cluster c ON c.id = f.cluster_id
            WHERE f.cluster_id IS NOT NULL
              AND c.id IS NULL
            ORDER BY f.cluster_id
            """
        ).fetchall()
    ]
    for cluster_id in missing_cluster_ids:
        cur.execute(
            "INSERT INTO cluster(id, label, person_id) VALUES (?, NULL, NULL)",
            (cluster_id,),
        )

    cur.execute(
        """
        UPDATE cluster
        SET person_id = NULL
        WHERE person_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM person p
              WHERE p.id = cluster.person_id
          )
        """
    )

    cur.execute(
        """
        DELETE FROM cluster
        WHERE NOT EXISTS (
            SELECT 1
            FROM face
            WHERE face.cluster_id = cluster.id
        )
        """
    )


def _open_connection():
    """Open a configured SQLite connection without recovery side effects."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _is_recoverable_database_error(exc: sqlite3.DatabaseError) -> bool:
    """Return whether a SQLite error suggests on-disk corruption."""
    message = str(exc).lower()
    return any(marker in message for marker in RECOVERABLE_DATABASE_ERROR_MARKERS)


def _quarantine_database_file() -> Path | None:
    """Move a broken database and its sidecars out of the active location."""
    database_path = Path(DB_PATH)
    if not database_path.exists():
        return None

    recovery_dir = database_path.parent / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target_path = recovery_dir / f"{database_path.stem}.corrupt-{timestamp}{database_path.suffix}"
    suffix_counter = 1
    while target_path.exists():
        suffix_counter += 1
        target_path = recovery_dir / (
            f"{database_path.stem}.corrupt-{timestamp}-{suffix_counter}{database_path.suffix}"
        )

    shutil.move(str(database_path), str(target_path))
    for sidecar_suffix in ("-wal", "-shm"):
        sidecar_path = database_path.with_name(f"{database_path.name}{sidecar_suffix}")
        if sidecar_path.exists():
            shutil.move(
                str(sidecar_path),
                str(target_path.with_name(f"{target_path.name}{sidecar_suffix}")),
            )
    return target_path


def recover_database(exc: sqlite3.DatabaseError, context: str) -> Path | None:
    """Archive a corrupted database so the app can recreate a healthy one."""
    with DATABASE_RECOVERY_LOCK:
        archived_path = _quarantine_database_file()
    if archived_path is not None:
        logger.exception(
            "Recovered corrupted database during %s; archived previous file to %s",
            context,
            archived_path,
            exc_info=exc,
        )
    else:
        logger.exception(
            "Detected recoverable database issue during %s, but no active database file was present",
            context,
            exc_info=exc,
        )
    return archived_path


def get_conn():
    """Open a configured SQLite connection.

    Returns:
        SQLite connection with row mapping, WAL, and foreign keys enabled.
    """
    try:
        return _open_connection()
    except sqlite3.DatabaseError as exc:
        if not _is_recoverable_database_error(exc):
            raise
        recover_database(exc, "connection open")
        return _open_connection()


def _split_image_path(image_path: str):
    """Split an image path into stored path metadata.

    Args:
        image_path: Image path to normalize.

    Returns:
        Original path, normalized directory, and filename.
    """
    normalized = os.path.normpath(image_path)
    return image_path, os.path.dirname(normalized), os.path.basename(normalized)


def get_file_created_at(path: str) -> str | None:
    """Return the best available filesystem creation timestamp for one path."""
    try:
        stat_result = os.stat(path)
    except OSError:
        return None

    created_timestamp = getattr(stat_result, "st_birthtime", None)
    if created_timestamp is None:
        created_timestamp = stat_result.st_mtime

    try:
        return datetime.fromtimestamp(created_timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _create_image_table(cur):
    """Create the canonical image table when missing.

    Args:
        cur: SQLite cursor executing schema statements.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS image (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            directory TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_hash TEXT,
            processed_at TEXT
        )
        """
    )


def _create_image_location_table(cur):
    """Create the table mapping images to filesystem locations.

    Args:
        cur: SQLite cursor executing schema statements.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS image_location (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            path TEXT NOT NULL UNIQUE,
            directory TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY(image_id) REFERENCES image(id) ON DELETE CASCADE
        )
        """
    )


def calculate_file_hash(path: str):
    """Calculate a streaming SHA-256 digest.

    Args:
        path: File path to hash.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_content_hash_column(cur):
    """Add the image content hash column to legacy databases.

    Args:
        cur: SQLite cursor executing migration statements.
    """
    columns = {
        row["name"] for row in cur.execute("PRAGMA table_info(image)").fetchall()
    }
    if "content_hash" not in columns:
        cur.execute("ALTER TABLE image ADD COLUMN content_hash TEXT")


def _migrate_image_locations(conn):
    """Migrate legacy image paths and merge duplicate content.

    Args:
        conn: SQLite connection to migrate.
    """
    cur = conn.cursor()
    _ensure_content_hash_column(cur)
    _create_image_location_table(cur)
    location_columns = {
        row["name"] for row in cur.execute("PRAGMA table_info(image_location)").fetchall()
    }
    if "created_at" not in location_columns:
        cur.execute("ALTER TABLE image_location ADD COLUMN created_at TEXT")
    cur.execute("DROP INDEX IF EXISTS idx_image_content_hash")
    cur.execute(
        """
        INSERT OR IGNORE INTO image_location(
            image_id, path, directory, filename, created_at
        )
        SELECT id, path, directory, filename, NULL FROM image
        """
    )

    unhashed = cur.execute(
        "SELECT id, path FROM image WHERE content_hash IS NULL"
    ).fetchall()
    for row in unhashed:
        if not os.path.isfile(row["path"]):
            continue
        try:
            content_hash = calculate_file_hash(row["path"])
        except OSError:
            continue
        cur.execute(
            "UPDATE image SET content_hash = ? WHERE id = ?",
            (content_hash, row["id"]),
        )

    duplicate_hashes = cur.execute(
        """
        SELECT content_hash
        FROM image
        WHERE content_hash IS NOT NULL
        GROUP BY content_hash
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_hashes:
        image_ids = [
            image_row["id"]
            for image_row in cur.execute(
                """
                SELECT id
                FROM image
                WHERE content_hash = ?
                ORDER BY processed_at IS NULL, id
                """,
                (row["content_hash"],),
            ).fetchall()
        ]
        canonical_id, duplicate_ids = image_ids[0], image_ids[1:]
        for duplicate_id in duplicate_ids:
            cur.execute(
                "UPDATE image_location SET image_id = ? WHERE image_id = ?",
                (canonical_id, duplicate_id),
            )
            cur.execute("DELETE FROM image WHERE id = ?", (duplicate_id,))

    missing_created_at = cur.execute(
        "SELECT id, path FROM image_location WHERE created_at IS NULL"
    ).fetchall()
    for row in missing_created_at:
        cur.execute(
            "UPDATE image_location SET created_at = ? WHERE id = ?",
            (get_file_created_at(row["path"]), row["id"]),
        )


def _create_face_table(cur, table_name="face"):
    """Create a face table.

    Args:
        cur: SQLite cursor executing schema statements.
        table_name: Table name used for normal creation or migration.
    """
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            bbox_x REAL,
            bbox_y REAL,
            bbox_w REAL,
            bbox_h REAL,
            cluster_id INTEGER,
            review_status TEXT NOT NULL DEFAULT '{FACE_REVIEW_STATUS_ACTIVE}',
            embedding BLOB,
            FOREIGN KEY(image_id) REFERENCES image(id) ON DELETE CASCADE,
            FOREIGN KEY(cluster_id) REFERENCES cluster(id)
        )
        """
    )


def _ensure_face_review_columns(cur):
    """Ensure the face review lifecycle columns exist and contain valid values."""
    columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(face)").fetchall()
    }
    if "review_status" not in columns:
        cur.execute(
            f"""
            ALTER TABLE face
            ADD COLUMN review_status TEXT NOT NULL DEFAULT '{FACE_REVIEW_STATUS_ACTIVE}'
            """
        )
    placeholders = ",".join("?" for _ in VALID_FACE_REVIEW_STATUSES)
    cur.execute(
        f"""
        UPDATE face
        SET review_status = ?
        WHERE review_status IS NULL
           OR TRIM(review_status) = ''
           OR review_status NOT IN ({placeholders})
        """,
        (FACE_REVIEW_STATUS_ACTIVE, *sorted(VALID_FACE_REVIEW_STATUSES)),
    )


def _create_import_job_table(cur):
    """Create the durable import queue table when missing.

    Args:
        cur: SQLite cursor executing schema statements.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS import_job (
            id TEXT PRIMARY KEY,
            folder_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            total_images INTEGER NOT NULL DEFAULT 0,
            processed_images INTEGER NOT NULL DEFAULT 0,
            total_faces INTEGER NOT NULL DEFAULT 0,
            processed_faces INTEGER NOT NULL DEFAULT 0,
            stage TEXT,
            stage_started_at TEXT,
            stage_current INTEGER NOT NULL DEFAULT 0,
            stage_total INTEGER NOT NULL DEFAULT 0,
            current_file TEXT,
            last_error TEXT,
            queue_order INTEGER NOT NULL
        )
        """
    )
    columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(import_job)").fetchall()
    }
    additions = {
        "stage": "TEXT",
        "stage_started_at": "TEXT",
        "stage_current": "INTEGER NOT NULL DEFAULT 0",
        "stage_total": "INTEGER NOT NULL DEFAULT 0",
        "current_file": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            cur.execute(
                f"ALTER TABLE import_job ADD COLUMN {column} {definition}"
            )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_import_job_queue_order
        ON import_job(queue_order)
        """
    )


def _create_app_settings_table(cur):
    """Create the key-value application settings table when missing."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _create_recluster_dirty_person_table(cur):
    """Track which persons still need their subclusters rebuilt.

    Rows are written in the same transaction as the assignment change, so the
    scope can never drift from the data. ``person_id = -1`` is the sentinel for
    the pool of faces that belong to no person yet.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recluster_dirty_person (
            person_id INTEGER PRIMARY KEY
        )
        """
    )


def _migrate_legacy_faces(conn):
    """Migrate face rows that referenced image paths directly.

    Args:
        conn: SQLite connection to migrate.
    """
    cur = conn.cursor()
    columns = {row["name"] for row in cur.execute("PRAGMA table_info(face)").fetchall()}
    if "image_path" not in columns or "image_id" in columns:
        return

    paths = cur.execute("SELECT DISTINCT image_path FROM face").fetchall()
    for row in paths:
        path, directory, filename = _split_image_path(row["image_path"])
        cur.execute(
            """
            INSERT OR IGNORE INTO image(path, directory, filename, processed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (path, directory, filename),
        )

    _create_face_table(cur, "face_migrated")
    cur.execute(
        """
        INSERT INTO face_migrated(
            id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding
        )
        SELECT
            f.id, i.id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h,
            f.cluster_id, ?, f.embedding
        FROM face f
        JOIN image i ON i.path = f.image_path
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    )
    cur.execute("DROP TABLE face")
    cur.execute("ALTER TABLE face_migrated RENAME TO face")


def _initialize_schema(conn):
    """Create and migrate the application database schema on one connection."""
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS person (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            person_id INTEGER,
            FOREIGN KEY(person_id) REFERENCES person(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster_person_suggestion (
            cluster_id INTEGER PRIMARY KEY,
            person_id INTEGER NOT NULL,
            confidence REAL NOT NULL,
            best_distance REAL NOT NULL,
            runner_up_margin REAL NOT NULL,
            support_count INTEGER NOT NULL,
            face_count INTEGER NOT NULL,
            support_ratio REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'dismissed')),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(cluster_id) REFERENCES cluster(id) ON DELETE CASCADE,
            FOREIGN KEY(person_id) REFERENCES person(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster_review_suggestion (
            cluster_id INTEGER PRIMARY KEY,
            review_status TEXT NOT NULL
                CHECK(review_status IN ('unknown_person', 'not_face')),
            confidence REAL NOT NULL,
            best_distance REAL NOT NULL,
            support_count INTEGER NOT NULL,
            face_count INTEGER NOT NULL,
            support_ratio REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'dismissed')),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(cluster_id) REFERENCES cluster(id) ON DELETE CASCADE
        )
        """
    )
    _create_image_table(cur)

    face_exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'face'"
    ).fetchone()
    if face_exists:
        _migrate_legacy_faces(conn)
    else:
        _create_face_table(cur)
    _ensure_face_review_columns(cur)

    _migrate_image_locations(conn)
    _create_import_job_table(cur)
    _create_app_settings_table(cur)
    _create_recluster_dirty_person_table(cur)

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_image_content_hash
        ON image(content_hash)
        WHERE content_hash IS NOT NULL
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_image_directory ON image(directory)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_location_image_id "
        "ON image_location(image_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_location_directory "
        "ON image_location(directory)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_location_image_path "
        "ON image_location(image_id, path)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_location_created_at "
        "ON image_location(created_at)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_image_id ON face(image_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_cluster_id ON face(cluster_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cluster_person_suggestion_person "
        "ON cluster_person_suggestion(person_id, status, confidence DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cluster_review_suggestion_status "
        "ON cluster_review_suggestion(review_status, status, confidence DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_review_status ON face(review_status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_review_cluster "
        "ON face(review_status, cluster_id)"
    )
    _repair_cluster_integrity(cur)
    _ensure_person_name_indexes(cur)

    conn.commit()


def init_db():
    """Create and migrate the application database schema."""
    conn = None
    try:
        conn = _open_connection()
        _initialize_schema(conn)
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
            conn = None
        if not _is_recoverable_database_error(exc):
            raise
        recover_database(exc, "database initialization")
        conn = _open_connection()
        _initialize_schema(conn)
    finally:
        if conn is not None:
            conn.close()
