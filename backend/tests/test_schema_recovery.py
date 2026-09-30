import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema


class SchemaRecoveryTest(unittest.TestCase):
    def test_recover_database_moves_broken_db_and_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            db_path.write_text("broken", encoding="utf-8")
            wal_path = db_path.with_name("database.sqlite-wal")
            shm_path = db_path.with_name("database.sqlite-shm")
            wal_path.write_text("wal", encoding="utf-8")
            shm_path.write_text("shm", encoding="utf-8")

            error = sqlite3.DatabaseError("database disk image is malformed")
            with patch.object(schema, "DB_PATH", str(db_path)):
                archived_path = schema.recover_database(error, "unit-test")

            self.assertIsNotNone(archived_path)
            self.assertFalse(db_path.exists())
            self.assertTrue(archived_path.exists())
            self.assertTrue(
                archived_path.with_name(f"{archived_path.name}-wal").exists()
            )
            self.assertTrue(
                archived_path.with_name(f"{archived_path.name}-shm").exists()
            )

    def test_init_db_retries_after_recoverable_database_error(self):
        marker_error = sqlite3.DatabaseError("database disk image is malformed")
        healthy_connection = sqlite3.connect(":memory:")
        healthy_connection.row_factory = sqlite3.Row

        with patch.object(schema, "_open_connection", side_effect=[marker_error, healthy_connection]), patch.object(
            schema,
            "recover_database",
            return_value=Path("/tmp/database.corrupt.sqlite"),
        ) as recover_database:
            schema.init_db()

        recover_database.assert_called_once()
        healthy_connection.close()

    def test_init_db_repairs_cluster_integrity_issues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"

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
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL,
                    bbox_y REAL,
                    bbox_w REAL,
                    bbox_h REAL,
                    cluster_id INTEGER,
                    embedding BLOB
                );
                """
            )
            conn.execute(
                """
                INSERT INTO image(path, directory, filename, content_hash, processed_at)
                VALUES ('/photos/a.jpg', '/photos', 'a.jpg', 'hash-a', CURRENT_TIMESTAMP)
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Kai')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (1, 'empty', 1)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (2, 'broken', 99)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 2, NULL)
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 7, NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                cluster_rows = conn.execute(
                    "SELECT id, person_id FROM cluster ORDER BY id"
                ).fetchall()
                conn.close()

            self.assertEqual(
                [(row["id"], row["person_id"]) for row in cluster_rows],
                [(2, None), (7, None)],
            )

    def test_repair_reassigns_dangling_zero_cluster_id(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE person(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
            CREATE TABLE cluster(
                id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, person_id INTEGER
            );
            CREATE TABLE face(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id INTEGER,
                review_status TEXT DEFAULT 'active'
            );
            INSERT INTO cluster(id, label) VALUES (3, 'Cluster 3');
            INSERT INTO face(cluster_id) VALUES (0), (0), (3);
            """
        )
        cur = conn.cursor()
        schema._repair_cluster_integrity(cur)
        conn.commit()

        # No face should still point at the falsy id 0.
        self.assertEqual(
            cur.execute("SELECT COUNT(*) FROM face WHERE cluster_id = 0").fetchone()[0],
            0,
        )
        # The migrated faces now share a single positive cluster that exists.
        migrated = cur.execute(
            "SELECT DISTINCT cluster_id FROM face WHERE cluster_id != 3"
        ).fetchall()
        self.assertEqual(len(migrated), 1)
        migrated_cluster_id = migrated[0]["cluster_id"]
        self.assertGreater(migrated_cluster_id, 0)
        self.assertIsNotNone(
            cur.execute(
                "SELECT 1 FROM cluster WHERE id = ?", (migrated_cluster_id,)
            ).fetchone()
        )
        conn.close()

    def test_repair_preserves_zero_cluster_person_and_label(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE person(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
            CREATE TABLE cluster(
                id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, person_id INTEGER
            );
            CREATE TABLE face(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id INTEGER,
                review_status TEXT DEFAULT 'active'
            );
            INSERT INTO person(id, name) VALUES (1, 'Anna');
            INSERT INTO cluster(id, label, person_id) VALUES (0, 'Zero', 1);
            INSERT INTO face(cluster_id) VALUES (0), (0);
            """
        )
        cur = conn.cursor()
        schema._repair_cluster_integrity(cur)
        conn.commit()

        self.assertIsNone(
            cur.execute("SELECT 1 FROM cluster WHERE id = 0").fetchone()
        )
        rows = cur.execute(
            "SELECT id, label, person_id FROM cluster"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["id"], 0)
        self.assertEqual(rows[0]["label"], "Zero")
        self.assertEqual(rows[0]["person_id"], 1)
        conn.close()
