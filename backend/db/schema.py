import os
import sqlite3
from pathlib import Path

from ..config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _split_image_path(image_path: str):
    normalized = os.path.normpath(image_path)
    return image_path, os.path.dirname(normalized), os.path.basename(normalized)


def _create_image_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS image (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            directory TEXT NOT NULL,
            filename TEXT NOT NULL,
            processed_at TEXT
        )
        """
    )


def _create_face_table(cur, table_name="face"):
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


def _migrate_legacy_faces(conn):
    cur = conn.cursor()
    columns = {
        row["name"] for row in cur.execute("PRAGMA table_info(face)").fetchall()
    }
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

    cur.execute("CREATE INDEX IF NOT EXISTS idx_image_directory ON image(directory)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_image_id ON face(image_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_face_cluster_id ON face(cluster_id)")

    conn.commit()
    conn.close()
