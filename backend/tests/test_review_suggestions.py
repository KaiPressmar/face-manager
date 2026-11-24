import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.services import storage


class ReviewSuggestionTest(unittest.TestCase):
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

    def test_explicit_unknown_faces_create_proposal_without_auto_classification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.sqlite"
            conn = self._connect(path)
            conn.executescript(
                """
                CREATE TABLE cluster(id INTEGER PRIMARY KEY, label TEXT, person_id INTEGER);
                CREATE TABLE face(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    cluster_id INTEGER,
                    review_status TEXT NOT NULL,
                    embedding BLOB
                );
                CREATE TABLE cluster_review_suggestion(
                    cluster_id INTEGER PRIMARY KEY,
                    review_status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    best_distance REAL NOT NULL,
                    support_count INTEGER NOT NULL,
                    face_count INTEGER NOT NULL,
                    support_ratio REAL NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT
                );
                INSERT INTO cluster VALUES (30, NULL, NULL);
                """
            )
            for index, vector in enumerate(((1.0, 0.0), (0.99, 0.01), (0.98, -0.01))):
                conn.execute(
                    "INSERT INTO face(image_id, cluster_id, review_status, embedding) VALUES (?, NULL, 'unknown_person', ?)",
                    (index + 1, self._embedding(*vector)),
                )
            for index, vector in enumerate(((0.99, 0.01), (1.0, 0.0), (0.98, 0.02))):
                conn.execute(
                    "INSERT INTO face(image_id, cluster_id, review_status, embedding) VALUES (?, 30, 'active', ?)",
                    (index + 10, self._embedding(*vector)),
                )
            conn.commit()
            conn.close()

            with (
                patch("backend.services.storage.get_conn", lambda: self._connect(path)),
                patch(
                    "backend.services.storage.get_clustering_profile",
                    return_value={"person_anchor_threshold": 0.4},
                ),
            ):
                self.assertEqual(storage.refresh_review_suggestions(), 1)
                suggestions = storage.list_review_suggestions()
                self.assertEqual(len(suggestions), 1)
                self.assertEqual(suggestions[0]["review_status"], "unknown_person")
                self.assertTrue(suggestions[0]["recommended"])

                check = self._connect(path)
                before = check.execute(
                    "SELECT DISTINCT review_status FROM face WHERE cluster_id = 30"
                ).fetchall()
                check.close()
                self.assertEqual([row["review_status"] for row in before], ["active"])

                self.assertEqual(storage.accept_review_suggestions(
                    [30], expected_face_ids={"30": suggestions[0]["preview_face_ids"]},
                ), 1)
                check = self._connect(path)
                accepted = check.execute(
                    "SELECT cluster_id, review_status FROM face WHERE image_id >= 10 ORDER BY id"
                ).fetchall()
                check.close()
                self.assertTrue(all(row["cluster_id"] is None for row in accepted))
                self.assertTrue(all(row["review_status"] == "unknown_person" for row in accepted))

    def test_stale_archive_cannot_overwrite_a_confirmed_person(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.sqlite"
            conn = self._connect(path)
            conn.executescript(
                """
                CREATE TABLE cluster(id INTEGER PRIMARY KEY, person_id INTEGER);
                CREATE TABLE face(id INTEGER PRIMARY KEY, cluster_id INTEGER, review_status TEXT);
                CREATE TABLE cluster_person_suggestion(cluster_id INTEGER PRIMARY KEY, person_id INTEGER, status TEXT);
                CREATE TABLE cluster_review_suggestion(
                    cluster_id INTEGER PRIMARY KEY,
                    review_status TEXT,
                    confidence REAL,
                    best_distance REAL,
                    support_count INTEGER,
                    face_count INTEGER,
                    support_ratio REAL,
                    status TEXT,
                    updated_at TEXT
                );
                INSERT INTO cluster VALUES (30, NULL), (31, NULL), (32, NULL);
                INSERT INTO face VALUES (1, 30, 'active'), (2, 31, 'active');
                INSERT INTO cluster_person_suggestion VALUES (30, 4, 'pending');
                INSERT INTO cluster_review_suggestion(cluster_id, review_status, confidence, best_distance,
                                                       support_count, face_count, support_ratio, status) VALUES
                    (30, 'unknown_person', 0.9, 0.11, 1, 1, 1.0, 'pending'),
                    (31, 'not_face', 0.9, 0.11, 1, 1, 1.0, 'pending'),
                    (32, 'unknown_person', 0.9, 0.11, 1, 1, 1.0, 'pending'),
                    (99, 'not_face', 0.9, 0.11, 1, 1, 1.0, 'pending');
                """
            )
            conn.close()

            with patch("backend.services.storage.get_conn", lambda: self._connect(path)):
                shown_statuses = {
                    str(item["cluster_id"]): item["review_status"]
                    for item in storage.list_review_suggestions()
                }
                self.assertEqual(
                    shown_statuses,
                    {"30": "unknown_person", "31": "not_face"},
                )
                modified = self._connect(path)
                modified.execute(
                    "UPDATE cluster_review_suggestion SET review_status = 'unknown_person' WHERE cluster_id = 31"
                )
                modified.commit()
                modified.close()
                with self.assertRaisesRegex(ValueError, "Aussortiervorschlag hat sich geändert"):
                    storage.accept_review_suggestions([30, 31], shown_statuses)
                unchanged = self._connect(path)
                faces_before_confirmation = unchanged.execute(
                    "SELECT cluster_id, review_status FROM face ORDER BY id"
                ).fetchall()
                unchanged.close()
                self.assertEqual(
                    [tuple(face) for face in faces_before_confirmation],
                    [(30, "active"), (31, "active")],
                )
                changed = self._connect(path)
                changed.execute("INSERT INTO face VALUES (3, 30, 'active')")
                changed.commit()
                changed.close()
                self.assertEqual(next(item for item in storage.list_review_suggestions()
                                      if item["cluster_id"] == 30)["face_count"], 2)
                with self.assertRaisesRegex(ValueError, "Aussortiervorschlag hat sich geändert"):
                    storage.accept_review_suggestions(
                        [30], {"30": "unknown_person"}, {"30": [1]},
                    )
                with self.assertRaises(LookupError):
                    storage.dismiss_review_suggestion(30, "not_face")

                confirmed = self._connect(path)
                confirmed.execute("UPDATE cluster SET person_id = 4 WHERE id = 30")
                confirmed.commit()
                confirmed.close()
                self.assertEqual(
                    [item["cluster_id"] for item in storage.list_review_suggestions()],
                    [31],
                )
                for stale_id in (30, 32, 99):
                    with self.subTest(stale_id=stale_id):
                        with self.assertRaisesRegex(ValueError, "Aussortiervorschlag hat sich geändert"):
                            storage.accept_review_suggestions([31, stale_id])
                self.assertEqual(storage.accept_review_suggestions([31], {"31": "unknown_person"}), 1)

            check = self._connect(path)
            cluster = check.execute("SELECT person_id FROM cluster WHERE id = 30").fetchone()
            faces = check.execute("SELECT cluster_id, review_status FROM face ORDER BY id").fetchall()
            check.close()
            self.assertEqual(cluster["person_id"], 4)
            self.assertEqual(
                [tuple(face) for face in faces],
                [(30, "active"), (None, "unknown_person"), (30, "active")],
            )


if __name__ == "__main__":
    unittest.main()
