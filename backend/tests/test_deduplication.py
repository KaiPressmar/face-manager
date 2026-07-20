import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.cache import app_cache
from backend.services.storage import (
    build_folder_tree,
    list_available_image_persons,
    list_image_locations,
    list_images_page,
)


class DeduplicationMigrationTest(unittest.TestCase):
    def setUp(self):
        app_cache.clear()

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
                    review_status TEXT NOT NULL DEFAULT 'active',
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

    def test_list_images_page_supports_filters_and_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photos = root / "photos"
            archive = photos / "archive"
            photos.mkdir()
            archive.mkdir()
            first_path = photos / "anna.jpg"
            second_path = archive / "bert.jpg"
            first_path.write_bytes(b"first")
            second_path.write_bytes(b"second")
            db_path = root / "database.sqlite"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
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
                    content_hash TEXT,
                    processed_at TEXT
                );
                CREATE TABLE image_location (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT
                );
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(name) VALUES ('Anna')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (1, 'Cluster 1', 1)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (2, 'Cluster 2', NULL)")
            conn.execute(
                """
                INSERT INTO image(path, directory, filename, content_hash, processed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(first_path), str(photos), first_path.name, "hash-1"),
            )
            conn.execute(
                """
                INSERT INTO image(path, directory, filename, content_hash, processed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(second_path), str(archive), second_path.name, "hash-2"),
            )
            conn.execute(
                """
                INSERT INTO image_location(image_id, path, directory, filename, created_at)
                VALUES (1, ?, ?, ?, ?)
                """,
                (
                    str(first_path),
                    str(photos),
                    first_path.name,
                    "2026-06-02T00:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO image_location(image_id, path, directory, filename, created_at)
                VALUES (2, ?, ?, ?, ?)
                """,
                (
                    str(second_path),
                    str(archive),
                    second_path.name,
                    "2026-06-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)
                VALUES (1, 1, 2, 3, 4, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)
                VALUES (2, 1, 2, 3, 4, 2)
                """
            )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.storage.get_conn", schema.get_conn
            ):
                rows, total = list_images_page(
                    folders=[str(photos)],
                    persons=["Anna"],
                    sort_by="date",
                    sort_direction="desc",
                    limit=1,
                    offset=0,
                )
                self.assertEqual(total, 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["filename"], "anna.jpg")

                persons = list_available_image_persons([str(photos)])
                self.assertEqual(persons, ["Anna", "Unbekannt"])

    def test_person_names_are_deduplicated_and_filtered_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photos = root / "photos"
            photos.mkdir()
            first_path = photos / "anna.jpg"
            second_path = photos / "anna-2.jpg"
            first_path.write_bytes(b"first")
            second_path.write_bytes(b"second")
            db_path = root / "database.sqlite"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
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
                    content_hash TEXT,
                    processed_at TEXT
                );
                CREATE TABLE image_location (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT
                );
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Kai')")
            conn.execute("INSERT INTO person(id, name) VALUES (2, 'kai')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (1, 'Cluster 1', 1)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (2, 'Cluster 2', 2)")
            conn.execute(
                """
                INSERT INTO image(path, directory, filename, content_hash, processed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(first_path), str(photos), first_path.name, "hash-1"),
            )
            conn.execute(
                """
                INSERT INTO image(path, directory, filename, content_hash, processed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(second_path), str(photos), second_path.name, "hash-2"),
            )
            conn.execute(
                """
                INSERT INTO image_location(image_id, path, directory, filename, created_at)
                VALUES (1, ?, ?, ?, ?)
                """,
                (str(first_path), str(photos), first_path.name, "2026-06-02T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO image_location(image_id, path, directory, filename, created_at)
                VALUES (2, ?, ?, ?, ?)
                """,
                (str(second_path), str(photos), second_path.name, "2026-06-01T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)
                VALUES (1, 1, 2, 3, 4, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)
                VALUES (2, 1, 2, 3, 4, 2)
                """
            )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.storage.get_conn", schema.get_conn
            ):
                schema.init_db()

                conn = schema.get_conn()
                person_rows = conn.execute(
                    "SELECT id, name FROM person ORDER BY id"
                ).fetchall()
                cluster_rows = conn.execute(
                    "SELECT id, person_id FROM cluster ORDER BY id"
                ).fetchall()
                conn.close()

                self.assertEqual(
                    [(row["id"], row["name"]) for row in person_rows],
                    [(1, "Kai")],
                )
                self.assertEqual(
                    [(row["id"], row["person_id"]) for row in cluster_rows],
                    [(1, 1), (2, 1)],
                )

                rows, total = list_images_page(
                    folders=[str(photos)],
                    persons=["kAi"],
                    sort_by="date",
                    sort_direction="desc",
                    limit=10,
                    offset=0,
                )
                self.assertEqual(total, 2)
                self.assertEqual([row["filename"] for row in rows], ["anna.jpg", "anna-2.jpg"])

                persons = list_available_image_persons([str(photos)])
                self.assertEqual(persons, ["Kai"])

    def test_hidden_review_status_faces_are_excluded_from_image_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photos = root / "photos"
            photos.mkdir()
            visible_path = photos / "visible.jpg"
            hidden_path = photos / "hidden.jpg"
            visible_path.write_bytes(b"visible")
            hidden_path.write_bytes(b"hidden")
            db_path = root / "database.sqlite"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
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
                    content_hash TEXT,
                    processed_at TEXT
                );
                CREATE TABLE image_location (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT
                );
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            for image_id, path, created_at in (
                (1, visible_path, "2026-06-02T00:00:00+00:00"),
                (2, hidden_path, "2026-06-01T00:00:00+00:00"),
            ):
                conn.execute(
                    """
                    INSERT INTO image(id, path, directory, filename, content_hash, processed_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (image_id, str(path), str(photos), path.name, f"hash-{image_id}"),
                )
                conn.execute(
                    """
                    INSERT INTO image_location(image_id, path, directory, filename, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (image_id, str(path), str(photos), path.name, created_at),
                )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status)
                VALUES (1, 1, 2, 3, 4, NULL, 'active')
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status)
                VALUES (2, 1, 2, 3, 4, NULL, 'not_face')
                """
            )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.storage.get_conn", schema.get_conn
            ):
                rows, total = list_images_page(
                    folders=[str(photos)],
                    persons=[],
                    sort_by="date",
                    sort_direction="desc",
                    limit=10,
                    offset=0,
                )

            self.assertEqual(total, 1)
            self.assertEqual([row["filename"] for row in rows], ["visible.jpg"])

    def test_image_pagination_binds_limit_offset_before_review_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photos = root / "photos"
            photos.mkdir()
            image_path = photos / "visible.jpg"
            image_path.write_bytes(b"visible")
            db_path = root / "database.sqlite"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
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
                    content_hash TEXT,
                    processed_at TEXT
                );
                CREATE TABLE image_location (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT
                );
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute(
                """
                INSERT INTO image(id, path, directory, filename, content_hash, processed_at)
                VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(image_path), str(photos), image_path.name, "hash-1"),
            )
            conn.execute(
                """
                INSERT INTO image_location(image_id, path, directory, filename, created_at)
                VALUES (1, ?, ?, ?, ?)
                """,
                (
                    str(image_path),
                    str(photos),
                    image_path.name,
                    "2026-06-02T00:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status)
                VALUES (1, 1, 2, 3, 4, NULL, 'active')
                """
            )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.storage.get_conn", schema.get_conn
            ):
                rows, total = list_images_page(
                    folders=[str(photos)],
                    persons=[],
                    sort_by="date",
                    sort_direction="desc",
                    limit=1,
                    offset=0,
                )

            self.assertEqual(total, 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filename"], "visible.jpg")
