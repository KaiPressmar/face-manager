import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.services import storage


class PersonSuggestionTest(unittest.TestCase):
    @staticmethod
    def _connect(path: Path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _embedding(x: float, y: float) -> bytes:
        value = np.zeros(512, dtype=np.float32)
        value[0] = x
        value[1] = y
        value /= np.linalg.norm(value)
        return value.tobytes()

    def test_mixed_cluster_peels_only_safe_anchors_into_reviewable_proposal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.sqlite"
            conn = self._connect(path)
            conn.executescript(
                """
                CREATE TABLE person(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE cluster(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT,
                    person_id INTEGER
                );
                CREATE TABLE face(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL DEFAULT 'active',
                    embedding BLOB
                );
                CREATE TABLE cluster_person_suggestion(
                    cluster_id INTEGER PRIMARY KEY,
                    person_id INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    best_distance REAL NOT NULL,
                    runner_up_margin REAL NOT NULL,
                    support_count INTEGER NOT NULL,
                    face_count INTEGER NOT NULL,
                    support_ratio REAL NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT
                );
                INSERT INTO person VALUES (1, 'Anna'), (2, 'Bob');
                INSERT INTO cluster(id, person_id) VALUES (10, 1), (20, 2), (30, NULL);
                """
            )
            image_id = 1
            for cluster_id, values in (
                (10, [(1.0, 0.0), (0.99, 0.02), (0.98, -0.02)]),
                (20, [(0.0, 1.0), (0.02, 0.99), (-0.02, 0.98)]),
                (30, [(0.99, 0.01)] * 6 + [(-1.0, 0.0)] * 4),
            ):
                for x, y in values:
                    conn.execute(
                        """
                        INSERT INTO face(image_id, cluster_id, review_status, embedding)
                        VALUES (?, ?, 'active', ?)
                        """,
                        (image_id, cluster_id, self._embedding(x, y)),
                    )
                    image_id += 1
            conn.commit()
            conn.close()

            with (
                patch("backend.services.storage.get_conn", lambda: self._connect(path)),
                patch("backend.services.storage.get_cluster_distance_threshold", return_value=0.65),
            ):
                self.assertEqual(storage.refresh_person_suggestions(), 1)
                suggestions = storage.list_person_suggestions()

                self.assertEqual(len(suggestions), 1)
                suggestion = suggestions[0]
                self.assertEqual(suggestion["person_id"], 1)
                self.assertEqual(suggestion["face_count"], 6)
                self.assertTrue(suggestion["recommended"])
                self.assertNotEqual(suggestion["cluster_id"], 30)

                check = self._connect(path)
                original = check.execute(
                    "SELECT COUNT(*) count FROM face WHERE cluster_id = 30"
                ).fetchone()
                proposal_person = check.execute(
                    "SELECT person_id FROM cluster WHERE id = ?",
                    (suggestion["cluster_id"],),
                ).fetchone()
                check.close()
                self.assertEqual(original["count"], 4)
                self.assertIsNone(proposal_person["person_id"])

                self.assertEqual(
                    storage.accept_person_suggestions(1, [suggestion["cluster_id"]]),
                    1,
                )
                check = self._connect(path)
                accepted = check.execute(
                    "SELECT person_id FROM cluster WHERE id = ?",
                    (suggestion["cluster_id"],),
                ).fetchone()
                check.close()
                self.assertEqual(accepted["person_id"], 1)

    def test_first_confirmed_person_can_already_produce_safe_suggestions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.sqlite"
            conn = self._connect(path)
            conn.executescript(
                """
                CREATE TABLE person(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE cluster(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, person_id INTEGER);
                CREATE TABLE face(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER NOT NULL,
                    cluster_id INTEGER, review_status TEXT NOT NULL DEFAULT 'active', embedding BLOB
                );
                CREATE TABLE cluster_person_suggestion(
                    cluster_id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL,
                    confidence REAL NOT NULL, best_distance REAL NOT NULL,
                    runner_up_margin REAL NOT NULL, support_count INTEGER NOT NULL,
                    face_count INTEGER NOT NULL, support_ratio REAL NOT NULL,
                    status TEXT NOT NULL, updated_at TEXT
                );
                INSERT INTO person VALUES (1, 'Anna');
                INSERT INTO cluster(id, person_id) VALUES (10, 1), (30, NULL);
                """
            )
            for image_id, cluster_id, vector in (
                (1, 10, (1.0, 0.0)), (2, 10, (0.99, 0.01)), (3, 10, (0.98, -0.01)),
                (4, 30, (0.99, 0.01)), (5, 30, (1.0, 0.0)), (6, 30, (0.98, 0.02)),
            ):
                conn.execute(
                    "INSERT INTO face(image_id, cluster_id, review_status, embedding) VALUES (?, ?, 'active', ?)",
                    (image_id, cluster_id, self._embedding(*vector)),
                )
            conn.commit()
            conn.close()

            with (
                patch("backend.services.storage.get_conn", lambda: self._connect(path)),
                patch("backend.services.storage.get_cluster_distance_threshold", return_value=0.5),
            ):
                self.assertEqual(storage.refresh_person_suggestions(), 1)
                suggestions = storage.list_person_suggestions()
                self.assertEqual(len(suggestions), 1)
                self.assertEqual(suggestions[0]["person_name"], "Anna")
                self.assertTrue(suggestions[0]["recommended"])


if __name__ == "__main__":
    unittest.main()
