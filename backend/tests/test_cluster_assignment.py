import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import storage


class ClusterAssignmentTest(unittest.TestCase):
    def test_assign_cluster_to_person_recreates_missing_cluster_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 7, NULL)
                """
            )
            conn.commit()
            conn.close()

            def get_test_conn():
                connection = sqlite3.connect(db_path)
                connection.row_factory = sqlite3.Row
                return connection

            with patch("backend.services.storage.get_conn", get_test_conn):
                storage.assign_cluster_to_person(7, "Kai")

                check_conn = get_test_conn()
                cluster_row = check_conn.execute(
                    """
                    SELECT c.id, p.name AS person_name
                    FROM cluster c
                    LEFT JOIN person p ON p.id = c.person_id
                    WHERE c.id = 7
                    """
                ).fetchone()
                check_conn.close()

            self.assertIsNotNone(cluster_row)
            self.assertEqual(cluster_row["person_name"], "Kai")

    def test_assign_cluster_to_person_rejects_unknown_cluster_without_faces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
            conn.commit()
            conn.close()

            def get_test_conn():
                connection = sqlite3.connect(db_path)
                connection.row_factory = sqlite3.Row
                return connection

            with patch("backend.services.storage.get_conn", get_test_conn):
                with self.assertRaises(LookupError):
                    storage.assign_cluster_to_person(7, "Kai")


if __name__ == "__main__":
    unittest.main()
