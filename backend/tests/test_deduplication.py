import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.storage import build_folder_tree, list_image_locations


class DeduplicationMigrationTest(unittest.TestCase):
    def test_legacy_duplicate_images_are_merged_and_keep_all_locations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first_path = first_dir / "photo.jpg"
            second_path = second_dir / "copy.jpg"
            first_path.write_bytes(b"identical image contents")
            second_path.write_bytes(first_path.read_bytes())
            db_path = root / "database.sqlite"

            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE person (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE cluster (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT,
                    person_id INTEGER
                );
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    processed_at TEXT
                );
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    embedding BLOB,
                    FOREIGN KEY(image_id) REFERENCES image(id) ON DELETE CASCADE
                );
                """
            )
            for path in (first_path, second_path):
                cursor = conn.execute(
                    """
                    INSERT INTO image(path, directory, filename, processed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (str(path), str(path.parent), path.name),
                )
                conn.execute(
                    """
                    INSERT INTO face(
                        image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id
                    )
                    VALUES (?, 1, 2, 3, 4, 1)
                    """,
                    (cursor.lastrowid,),
                )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.storage.get_conn", schema.get_conn
            ):
                schema.init_db()
                conn = schema.get_conn()
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM image_location"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM face").fetchone()[0], 1
                )
                image_id = conn.execute("SELECT id FROM image").fetchone()[0]
                conn.close()

                locations = list_image_locations([image_id])
                self.assertEqual(
                    [location["path"] for location in locations[image_id]],
                    sorted((str(first_path), str(second_path))),
                )

                tree = build_folder_tree()
                self.assertEqual(tree["image_count"], 1)

                def flatten(nodes):
                    for node in nodes:
                        yield node
                        yield from flatten(node["children"])

                counts = {
                    node["path"]: node["image_count"]
                    for node in flatten(tree["roots"])
                }
                self.assertEqual(counts[str(first_dir)], 1)
                self.assertEqual(counts[str(second_dir)], 1)
                self.assertEqual(counts[str(root)], 1)
