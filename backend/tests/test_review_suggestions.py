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

                self.assertEqual(storage.accept_review_suggestions([30]), 1)
                check = self._connect(path)
                accepted = check.execute(
                    "SELECT cluster_id, review_status FROM face WHERE image_id >= 10 ORDER BY id"
                ).fetchall()
                check.close()
                self.assertTrue(all(row["cluster_id"] is None for row in accepted))
                self.assertTrue(all(row["review_status"] == "unknown_person" for row in accepted))


if __name__ == "__main__":
    unittest.main()
