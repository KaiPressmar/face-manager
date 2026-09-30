"""Guarantees that keep interactive work unblocked during reclustering.

Background clustering is a low-priority optimisation. These tests pin the two
properties that let us run it without any interactive lock: a background pass
never overwrites a change the user made meanwhile (compare-and-set), and it can
be stopped at a group boundary without leaving a partial state behind.
"""

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from backend.services import storage


SCHEMA = """
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
    path TEXT
);
CREATE TABLE face (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    cluster_id INTEGER,
    review_status TEXT NOT NULL DEFAULT 'active',
    embedding BLOB
);
CREATE TABLE recluster_dirty_person (
    person_id INTEGER PRIMARY KEY
);
"""


class _VariableLimitedCursor:
    """Emulate the conservative SQLite parameter limit used in production."""

    def __init__(self, cursor, limit=999):
        self._cursor = cursor
        self._limit = limit

    def execute(self, statement, parameters=()):
        if len(parameters) > self._limit:
            raise sqlite3.OperationalError("too many SQL variables")
        self._cursor.execute(statement, parameters)
        return self

    def executemany(self, statement, parameters):
        self._cursor.executemany(statement, parameters)
        return self

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _VariableLimitedConnection:
    def __init__(self, connection):
        self._connection = connection

    @property
    def isolation_level(self):
        return self._connection.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self._connection.isolation_level = value

    def cursor(self):
        return _VariableLimitedCursor(self._connection.cursor())

    def execute(self, statement, parameters=()):
        return self.cursor().execute(statement, parameters)

    def close(self):
        self._connection.close()


class ReclusterConcurrencyTest(unittest.TestCase):
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

    def _seed(self, db_path, *, persons=(), faces=()):
        conn = self._make_connection(db_path)
        conn.executescript(SCHEMA)
        for name in persons:
            conn.execute("INSERT INTO person(name) VALUES (?)", (name,))
        for cluster_id, person_id in {(c, p) for _, c, p, _ in faces if c is not None}:
            conn.execute(
                "INSERT OR IGNORE INTO cluster(id, label, person_id) VALUES (?, NULL, ?)",
                (cluster_id, person_id),
            )
        for index, (face_id, cluster_id, _person_id, angle) in enumerate(faces, start=1):
            conn.execute("INSERT INTO image(id, path) VALUES (?, ?)", (index, f"/i{index}.jpg"))
            conn.execute(
                """
                INSERT INTO face(
                    id, image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                    cluster_id, review_status, embedding
                ) VALUES (?, ?, 0, 0, 1, 1, ?, 'active', ?)
                """,
                (face_id, index, cluster_id, self._angle_embedding(angle)),
            )
        conn.commit()
        conn.close()

    def test_background_pass_does_not_overwrite_a_concurrent_assignment(self):
        """Compare-and-set: a face the user claimed meanwhile stays claimed.

        This is what replaces the old interactive lock — instead of forbidding
        the write, the background pass simply loses the race for that face.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            self._seed(
                db_path,
                persons=["Anna"],
                faces=[(1, None, None, 0), (2, None, None, 2)],
            )

            conn = self._make_connection(db_path)
            cur = conn.cursor()
            # The user assigned face 1 to a person while the pass was computing.
            cur.execute("INSERT INTO cluster(id, label, person_id) VALUES (99, NULL, 1)")
            cur.execute("UPDATE face SET cluster_id = 99 WHERE id = 1")

            # The pass now tries to claim both faces for its own fresh cluster.
            cur.execute("INSERT INTO cluster(id, label, person_id) VALUES (50, NULL, NULL)")
            claimed = storage._assign_faces_to_cluster(
                cur, [1, 2], 50, only_unclaimed=True
            )
            conn.commit()

            rows = dict(
                (row["id"], row["cluster_id"])
                for row in conn.execute("SELECT id, cluster_id FROM face").fetchall()
            )
            conn.close()

            self.assertEqual(claimed, 1, "only the still-unassigned face may be claimed")
            self.assertEqual(rows[1], 99, "the user's assignment must survive")
            self.assertEqual(rows[2], 50)

    def test_large_group_stays_below_sqlite_variable_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            faces = [
                (face_id, 10, None, float(face_id % 3))
                for face_id in range(1, 1201)
            ]
            self._seed(db_path, faces=faces)

            def limited_connection():
                return _VariableLimitedConnection(self._make_connection(db_path))

            clusterer = Mock()
            clusterer.add_and_assign.return_value = (np.array([0]), None)
            with (
                patch("backend.services.storage.get_conn", limited_connection),
                patch("backend.services.storage.FaceClustering", return_value=clusterer),
                patch(
                    "backend.services.storage.consolidate_small_clusters",
                    side_effect=lambda _embeddings, cluster_ids, *_args, **_kwargs: cluster_ids,
                ),
                patch(
                    "backend.services.storage.split_heterogeneous_clusters",
                    side_effect=lambda _embeddings, cluster_ids, *_args, **_kwargs: cluster_ids,
                ),
                patch(
                    "backend.services.storage.get_cluster_distance_threshold",
                    return_value=0.15,
                ),
            ):
                rebuilt = storage.recluster_all_active_faces()

            check = self._make_connection(db_path)
            assigned = check.execute(
                "SELECT COUNT(*) FROM face WHERE cluster_id IS NOT NULL"
            ).fetchone()[0]
            check.close()

            self.assertEqual(rebuilt, 1200)
            self.assertEqual(assigned, 1200)

    def test_cancelling_between_groups_keeps_state_consistent(self):
        """A cancelled pass leaves finished groups done and the rest untouched."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            self._seed(
                db_path,
                persons=["Anna", "Bob"],
                faces=[
                    (1, 10, 1, 0), (2, 10, 1, 3),
                    (3, 11, 2, 90), (4, 11, 2, 93),
                ],
            )

            cancel = threading.Event()
            cancel.set()  # already cancelled: stop before the first group

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
                rebuilt = storage.recluster_all_active_faces(cancel_token=cancel)

            check = self._make_connection(db_path)
            clusters = dict(
                (row["id"], row["cluster_id"])
                for row in check.execute("SELECT id, cluster_id FROM face").fetchall()
            )
            check.close()

            self.assertEqual(rebuilt, 0)
            # Nothing was touched, so no face is left dangling without a cluster.
            self.assertEqual(clusters, {1: 10, 2: 10, 3: 11, 4: 11})

    def test_scoped_rebuild_only_touches_dirty_persons(self):
        """An untouched person keeps its cluster ids across a scoped rebuild."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            self._seed(
                db_path,
                persons=["Anna", "Bob"],
                faces=[
                    # Anna's faces are far apart, so a rebuild would re-split them.
                    (1, 10, 1, 0), (2, 10, 1, 120),
                    (3, 11, 2, 40), (4, 11, 2, 160),
                ],
            )
            conn = self._make_connection(db_path)
            conn.execute("INSERT INTO recluster_dirty_person(person_id) VALUES (1)")
            conn.commit()
            conn.close()

            committed_checkpoints = []

            def after_commit(processed, total):
                checkpoint_conn = self._make_connection(db_path)
                visible = checkpoint_conn.execute(
                    "SELECT COUNT(*) FROM face WHERE cluster_id IS NOT NULL"
                ).fetchone()[0]
                checkpoint_conn.close()
                committed_checkpoints.append((processed, total, visible))

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
                storage.recluster_all_active_faces(
                    scoped=True,
                    commit_callback=after_commit,
                )

            check = self._make_connection(db_path)
            faces = dict(
                (row["id"], row["cluster_id"])
                for row in check.execute("SELECT id, cluster_id FROM face").fetchall()
            )
            remaining_dirty = [
                row["person_id"]
                for row in check.execute(
                    "SELECT person_id FROM recluster_dirty_person"
                ).fetchall()
            ]
            check.close()

            self.assertEqual(faces[3], 11, "Bob was not dirty and must stay untouched")
            self.assertEqual(faces[4], 11, "Bob was not dirty and must stay untouched")
            self.assertNotEqual(
                faces[1], faces[2], "Anna was dirty, so her faces get re-split"
            )
            self.assertNotIn(1, remaining_dirty, "a rebuilt person is no longer dirty")
            self.assertEqual(committed_checkpoints, [(2, 2, 4)])


if __name__ == "__main__":
    unittest.main()
