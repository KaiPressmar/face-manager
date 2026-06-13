import hashlib
import os
import sqlite3
from pathlib import Path

from ..config import DB_PATH

HASH_CHUNK_SIZE = 1024 * 1024


def get_conn():
    """Open a configured SQLite connection.

    Returns:
        SQLite connection with row mapping, WAL, and foreign keys enabled.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _split_image_path(image_path: str):
    """Split an image path into stored path metadata.

    Args:
        image_path: Image path to normalize.

    Returns:
        Original path, normalized directory, and filename.
    """
    normalized = os.path.normpath(image_path)
    return image_path, os.path.dirname(normalized), os.path.basename(normalized)


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
    cur.execute("DROP INDEX IF EXISTS idx_image_content_hash")
    cur.execute(
        """
        INSERT OR IGNORE INTO image_location(image_id, path, directory, filename)
        SELECT id, path, directory, filename FROM image
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
            embedding BLOB,
            FOREIGN KEY(image_id) REFERENCES image(id) ON DELETE CASCADE,
            FOREIGN KEY(cluster_id) REFERENCES cluster(id)
        )
        """
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
            last_error TEXT,
            queue_order INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_import_job_queue_order
        ON import_job(queue_order)
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
            id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding
        )
        SELECT
            f.id, i.id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h,
            f.cluster_id, f.embedding
        FROM face f
        JOIN image i ON i.path = f.image_path
        """
    )
    cur.execute("DROP TABLE face")
    cur.execute("ALTER TABLE face_migrated RENAME TO face")


def init_db():
    """Create and migrate the application database schema."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
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
    _create_image_table(cur)

    face_exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'face'"
    ).fetchone()
    if face_exists:
        _migrate_legacy_faces(conn)
    else:
        _create_face_table(cur)

    _migrate_image_locations(conn)
    _create_import_job_table(cur)

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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_image_id ON face(image_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_cluster_id ON face(cluster_id)")

    conn.commit()
    conn.close()
