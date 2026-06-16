import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import storage


class ClusterAssignmentTest(unittest.TestCase):
    def _make_connection(self, db_path):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_assign_cluster_to_person_recreates_missing_cluster_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"

            conn = self._make_connection(db_path)
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

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.assign_cluster_to_person(7, "Kai")

                check_conn = self._make_connection(db_path)
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

            conn = self._make_connection(db_path)
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

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                with self.assertRaises(LookupError):
                    storage.assign_cluster_to_person(7, "Kai")

    def test_assign_cluster_to_person_repairs_broken_cluster_person_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"

            conn = self._make_connection(db_path)
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
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'c7', 99)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 7, NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.assign_cluster_to_person(7, "Kai")

                check_conn = self._make_connection(db_path)
                cluster_row = check_conn.execute(
                    """
                    SELECT c.person_id, p.name AS person_name
                    FROM cluster c
                    LEFT JOIN person p ON p.id = c.person_id
                    WHERE c.id = 7
                    """
                ).fetchone()
                check_conn.close()

            self.assertEqual(cluster_row["person_name"], "Kai")

    def test_assign_cluster_to_person_merges_duplicate_person_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"

            conn = self._make_connection(db_path)
            conn.executescript(
                """
                CREATE TABLE person (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL
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
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Kai')")
            conn.execute("INSERT INTO person(id, name) VALUES (2, ' Kai ')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'c7', 2)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (8, 'c8', 2)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 7, NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.assign_cluster_to_person(7, "Kai")

                check_conn = self._make_connection(db_path)
                person_rows = check_conn.execute(
                    "SELECT id, name FROM person ORDER BY id"
                ).fetchall()
                cluster_rows = check_conn.execute(
                    "SELECT id, person_id FROM cluster ORDER BY id"
                ).fetchall()
                check_conn.close()

            self.assertEqual([(row["id"], row["name"]) for row in person_rows], [(1, "Kai")])
            self.assertEqual(
                [(row["id"], row["person_id"]) for row in cluster_rows],
                [(7, 1), (8, 1)],
            )


if __name__ == "__main__":
    unittest.main()
