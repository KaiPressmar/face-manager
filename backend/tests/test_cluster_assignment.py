import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.services import storage


class ClusterAssignmentTest(unittest.TestCase):
    @staticmethod
    def _embedding(scale: float = 1.0) -> bytes:
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = scale
        return vector.tobytes()

    @staticmethod
    def _angle_embedding(angle_degrees: float) -> bytes:
        angle = np.deg2rad(angle_degrees)
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = np.cos(angle)
        vector[1] = np.sin(angle)
        return vector.tobytes()

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
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL
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
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL
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
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL
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
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL
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

    def test_assign_cluster_to_person_reuses_existing_person_case_insensitively(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Kai')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'c7', NULL)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                VALUES (1, 0, 0, 1, 1, 7, NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.assign_cluster_to_person(7, "kai")

                check_conn = self._make_connection(db_path)
                person_rows = check_conn.execute(
                    "SELECT id, name FROM person ORDER BY id"
                ).fetchall()
                cluster_row = check_conn.execute(
                    "SELECT person_id FROM cluster WHERE id = 7"
                ).fetchone()
                check_conn.close()

            self.assertEqual([(row["id"], row["name"]) for row in person_rows], [(1, "Kai")])
            self.assertEqual(cluster_row["person_id"], 1)

    def test_remove_faces_from_cluster_reclusters_faces_into_inbox(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'c7', NULL)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, 7, 'active', NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                updated = storage.remove_faces_from_cluster(7, [1])
                self.assertEqual(updated, 1)

                check_conn = self._make_connection(db_path)
                face_row = check_conn.execute(
                    "SELECT cluster_id, review_status FROM face WHERE id = 1"
                ).fetchone()
                cluster_row = check_conn.execute(
                    "SELECT id FROM cluster WHERE id = 7"
                ).fetchone()
                reclustered_row = check_conn.execute(
                    "SELECT id, person_id FROM cluster WHERE id = ?",
                    (face_row["cluster_id"],),
                ).fetchone()
                check_conn.close()

            self.assertIsNotNone(face_row["cluster_id"])
            self.assertEqual(face_row["review_status"], "active")
            self.assertIsNone(cluster_row)
            self.assertIsNotNone(reclustered_row)
            self.assertIsNone(reclustered_row["person_id"])

    def test_mark_and_restore_faces_review_status(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'c7', NULL)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, 7, 'active', NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.mark_faces_with_review_status([1], "not_face")
                storage.restore_faces_to_manual_review([1])

                check_conn = self._make_connection(db_path)
                face_row = check_conn.execute(
                    "SELECT cluster_id, review_status FROM face WHERE id = 1"
                ).fetchone()
                cluster_row = check_conn.execute(
                    "SELECT person_id FROM cluster WHERE id = ?",
                    (face_row["cluster_id"],),
                ).fetchone()
                check_conn.close()

            self.assertIsNotNone(face_row["cluster_id"])
            self.assertEqual(face_row["review_status"], "active")
            self.assertIsNotNone(cluster_row)
            self.assertIsNone(cluster_row["person_id"])

    def test_mark_faces_with_review_status_preserves_full_cluster_grouping(self):
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
                CREATE TABLE image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    filename TEXT NOT NULL
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
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'anna', 1)")
            conn.execute(
                "INSERT INTO image(id, path, directory, filename) VALUES (1, '/photos/1.jpg', '/photos', '1.jpg')"
            )
            conn.execute(
                "INSERT INTO image(id, path, directory, filename) VALUES (2, '/photos/2.jpg', '/photos', '2.jpg')"
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, 7, 'active', ?)
                """,
                (self._embedding(1.0),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (2, 0, 0, 1, 1, 7, 'active', ?)
                """,
                (self._embedding(2.0),),
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                updated = storage.mark_faces_with_review_status([1, 2], "unknown_person")
                self.assertEqual(updated, 2)

                check_conn = self._make_connection(db_path)
                face_rows = check_conn.execute(
                    "SELECT id, cluster_id, review_status FROM face ORDER BY id ASC"
                ).fetchall()
                cluster_row = check_conn.execute(
                    "SELECT id, person_id FROM cluster WHERE id = 7"
                ).fetchone()
                group_summary = storage.list_face_review_groups()
                group_details = storage.get_faces_for_review_group("unknown_person")
                check_conn.close()

            self.assertEqual([row["cluster_id"] for row in face_rows], [7, 7])
            self.assertTrue(all(row["review_status"] == "unknown_person" for row in face_rows))
            self.assertIsNotNone(cluster_row)
            self.assertIsNone(cluster_row["person_id"])
            self.assertEqual(
                next(group for group in group_summary if group["group_key"] == "unknown_person")[
                    "cluster_count"
                ],
                1,
            )
            self.assertEqual(group_details["cluster_count"], 1)
            self.assertEqual(group_details["face_count"], 2)

    def test_remove_faces_from_cluster_prefers_unassigned_clusters_over_person_clusters(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'assigned-source', 1)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (8, 'inbox', NULL)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (9, 'assigned-other', 1)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, 7, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (2, 0, 0, 1, 1, 8, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (3, 0, 0, 1, 1, 9, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                updated = storage.remove_faces_from_cluster(7, [1])
                self.assertEqual(updated, 1)

                check_conn = self._make_connection(db_path)
                face_row = check_conn.execute(
                    "SELECT cluster_id, review_status FROM face WHERE id = 1"
                ).fetchone()
                cluster_row = check_conn.execute(
                    "SELECT person_id FROM cluster WHERE id = ?",
                    (face_row["cluster_id"],),
                ).fetchone()
                check_conn.close()

            self.assertEqual(face_row["cluster_id"], 8)
            self.assertEqual(face_row["review_status"], "active")
            self.assertIsNone(cluster_row["person_id"])

    def test_restore_faces_to_manual_review_does_not_rejoin_person_cluster(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'assigned', 1)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, NULL, 'unknown_person', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (2, 0, 0, 1, 1, 7, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                storage.restore_faces_to_manual_review([1])

                check_conn = self._make_connection(db_path)
                face_row = check_conn.execute(
                    "SELECT cluster_id, review_status FROM face WHERE id = 1"
                ).fetchone()
                cluster_row = check_conn.execute(
                    "SELECT person_id FROM cluster WHERE id = ?",
                    (face_row["cluster_id"],),
                ).fetchone()
                check_conn.close()

            self.assertIsNotNone(face_row["cluster_id"])
            self.assertNotEqual(face_row["cluster_id"], 7)
            self.assertEqual(face_row["review_status"], "active")
            self.assertIsNotNone(cluster_row)
            self.assertIsNone(cluster_row["person_id"])

    def test_repair_active_inbox_faces_reclusters_legacy_null_cluster_rows(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, NULL, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (2, 0, 0, 1, 1, NULL, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                repaired = storage.repair_active_inbox_faces()
                self.assertEqual(repaired, 2)

                check_conn = self._make_connection(db_path)
                face_rows = check_conn.execute(
                    "SELECT id, cluster_id, review_status FROM face ORDER BY id ASC"
                ).fetchall()
                cluster_rows = check_conn.execute(
                    "SELECT id, person_id FROM cluster ORDER BY id ASC"
                ).fetchall()
                check_conn.close()

            self.assertEqual([row["review_status"] for row in face_rows], ["active", "active"])
            self.assertTrue(all(row["cluster_id"] is not None for row in face_rows))
            self.assertEqual(face_rows[0]["cluster_id"], face_rows[1]["cluster_id"])
            self.assertEqual(
                [(row["id"], row["person_id"]) for row in cluster_rows],
                [(face_rows[0]["cluster_id"], None)],
            )

    def test_recluster_unassigned_faces_rebuilds_visible_unassigned_bucket(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (7, 'free-a', NULL)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (8, 'free-b', NULL)")
            conn.execute("INSERT INTO cluster(id, label, person_id) VALUES (9, 'assigned', 1)")
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, 7, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (2, 0, 0, 1, 1, 8, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (3, 0, 0, 1, 1, 9, 'active', ?)
                """,
                (self._embedding(),),
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                reclustered = storage.recluster_unassigned_faces()
                self.assertEqual(reclustered, 2)

                check_conn = self._make_connection(db_path)
                face_rows = check_conn.execute(
                    "SELECT id, cluster_id, review_status FROM face ORDER BY id ASC"
                ).fetchall()
                cluster_rows = check_conn.execute(
                    "SELECT id, person_id FROM cluster ORDER BY id ASC"
                ).fetchall()
                check_conn.close()

            self.assertEqual(face_rows[0]["cluster_id"], face_rows[1]["cluster_id"])
            self.assertEqual(face_rows[2]["cluster_id"], 9)
            self.assertTrue(all(row["review_status"] == "active" for row in face_rows))
            self.assertEqual(
                sorted((row["id"], row["person_id"]) for row in cluster_rows),
                sorted([(face_rows[0]["cluster_id"], None), (9, 1)]),
            )

    def test_recluster_dissolves_and_splits_one_heterogeneous_legacy_cluster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            conn = self._make_connection(db_path)
            conn.executescript(
                """
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                INSERT INTO cluster(id, label, person_id)
                VALUES (7, 'heterogeneous-old-cluster', NULL);
                """
            )
            for image_id, angle in enumerate([0, 30, 60, 90], start=1):
                conn.execute(
                    """
                    INSERT INTO face(
                        image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                        cluster_id, review_status, embedding
                    )
                    VALUES (?, 0, 0, 1, 1, 7, 'active', ?)
                    """,
                    (image_id, self._angle_embedding(angle)),
                )
            conn.commit()
            conn.close()

            with (
                patch(
                    "backend.services.storage.get_conn",
                    lambda: self._make_connection(db_path),
                ),
                patch(
                    "backend.services.storage.get_cluster_distance_threshold",
                    return_value=0.15,
                ),
            ):
                self.assertEqual(storage.recluster_unassigned_faces(), 4)

            check_conn = self._make_connection(db_path)
            face_cluster_ids = [
                int(row["cluster_id"])
                for row in check_conn.execute(
                    "SELECT cluster_id FROM face ORDER BY id"
                ).fetchall()
            ]
            remaining_cluster_ids = {
                int(row["id"])
                for row in check_conn.execute("SELECT id FROM cluster").fetchall()
            }
            check_conn.close()

            self.assertNotIn(7, remaining_cluster_ids)
            self.assertEqual(len(set(face_cluster_ids)), 2)
            self.assertEqual(remaining_cluster_ids, set(face_cluster_ids))

    def test_full_recluster_rebuilds_subclusters_inside_each_person(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                INSERT INTO person(id, name) VALUES (1, 'Anna'), (2, 'Bob');
                INSERT INTO cluster(id, label, person_id) VALUES
                    (7, 'anna-old', 1),
                    (8, 'bob-old', 2),
                    (9, 'free-old', NULL);
                """
            )
            image_id = 1
            for angle in [0, 30, 60, 90]:
                conn.execute(
                    """
                    INSERT INTO face(
                        image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                        cluster_id, review_status, embedding
                    ) VALUES (?, 0, 0, 1, 1, 7, 'active', ?)
                    """,
                    (image_id, self._angle_embedding(angle)),
                )
                image_id += 1
            for angle in [170, 175]:
                conn.execute(
                    """
                    INSERT INTO face(
                        image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                        cluster_id, review_status, embedding
                    ) VALUES (?, 0, 0, 1, 1, 8, 'active', ?)
                    """,
                    (image_id, self._angle_embedding(angle)),
                )
                image_id += 1
            conn.execute(
                """
                INSERT INTO face(
                    image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                    cluster_id, review_status, embedding
                ) VALUES (?, 0, 0, 1, 1, 9, 'active', ?)
                """,
                (image_id, self._angle_embedding(120)),
            )
            conn.commit()
            conn.close()

            with (
                patch(
                    "backend.services.storage.get_conn",
                    lambda: self._make_connection(db_path),
                ),
                patch(
                    "backend.services.storage.get_cluster_distance_threshold",
                    return_value=0.15,
                ),
            ):
                self.assertEqual(storage.recluster_all_active_faces(), 7)

            check_conn = self._make_connection(db_path)
            rebuilt = check_conn.execute(
                """
                SELECT f.id, f.cluster_id, c.person_id
                FROM face f
                JOIN cluster c ON c.id = f.cluster_id
                ORDER BY f.id
                """
            ).fetchall()
            old_clusters = check_conn.execute(
                "SELECT COUNT(*) AS count FROM cluster WHERE id IN (7, 8, 9)"
            ).fetchone()["count"]
            check_conn.close()

            anna_clusters = {row["cluster_id"] for row in rebuilt[:4]}
            bob_clusters = {row["cluster_id"] for row in rebuilt[4:6]}
            self.assertEqual(len(anna_clusters), 2)
            self.assertEqual(len(bob_clusters), 1)
            self.assertTrue(all(row["person_id"] == 1 for row in rebuilt[:4]))
            self.assertTrue(all(row["person_id"] == 2 for row in rebuilt[4:6]))
            self.assertIsNone(rebuilt[6]["person_id"])
            self.assertEqual(old_clusters, 0)

    def test_assign_faces_to_person_creates_dedicated_cluster(self):
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
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                """
            )
            conn.execute(
                """
                INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding)
                VALUES (1, 0, 0, 1, 1, NULL, 'active', NULL)
                """
            )
            conn.commit()
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._make_connection(db_path)):
                cluster_id = storage.assign_faces_to_person([1], "Kai")

                check_conn = self._make_connection(db_path)
                face_row = check_conn.execute(
                    "SELECT cluster_id, review_status FROM face WHERE id = 1"
                ).fetchone()
                person_row = check_conn.execute(
                    """
                    SELECT p.name
                    FROM cluster c
                    JOIN person p ON p.id = c.person_id
                    WHERE c.id = ?
                    """,
                    (cluster_id,),
                ).fetchone()
                check_conn.close()

            self.assertEqual(face_row["cluster_id"], cluster_id)
            self.assertEqual(face_row["review_status"], "active")
            self.assertEqual(person_row["name"], "Kai")


if __name__ == "__main__":
    unittest.main()
