import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from backend.db import schema
from backend.services.pipeline import ImportProcessor


class ManualFaceDurabilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "faces.db"
        db_override = patch.object(schema, "DB_PATH", str(self.db_path))
        db_override.start()
        self.addCleanup(db_override.stop)

    def prepare_library(self):
        schema.init_db(scan_sources=False)
        with closing(schema.get_conn()) as connection, connection:
            connection.executescript("""
                INSERT INTO image(id, path, directory, filename)
                VALUES (1, 'photo.jpg', '', 'photo.jpg'), (2, 'alias.jpg', '', 'alias.jpg');
                INSERT INTO person(id, name) VALUES (1, 'Anna'), (2, 'Ben');
                INSERT INTO cluster(id, label, person_id)
                VALUES (1, 'Anna', 1), (2, 'Ben', 2), (3, 'Cluster 3', NULL);
            """)

    def test_existing_rows_migrate_to_automatic_without_changing_assignments(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE cluster (id INTEGER PRIMARY KEY, label TEXT, person_id INTEGER);
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL, bbox_x REAL, bbox_y REAL,
                    bbox_w REAL, bbox_h REAL, cluster_id INTEGER, review_status TEXT NOT NULL, embedding BLOB
                );
                INSERT INTO person VALUES (1, 'Anna');
                INSERT INTO cluster VALUES (1, 'Anna', 1);
                INSERT INTO face VALUES (1, 1, 12, 10, 35, 30, 1, 'active', X'00000000');
            """)
        schema.init_db(scan_sources=False)
        schema.init_db(scan_sources=False)
        with closing(schema.get_conn()) as connection:
            row = connection.execute(
                "SELECT id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, review_status, embedding, is_manual FROM face"
            ).fetchone()
        self.assertEqual(tuple(row), (1, 1, 12, 10, 35, 30, 1, "active", b"\x00" * 4, 0))

    def test_reprocessing_preserves_manual_id_assignment_and_nearby_distinct_faces(self):
        self.prepare_library()
        resources = Mock()
        clusterer = Mock()
        clusterer.add_and_assign.return_value = ([3], None)
        model = Mock()
        model.detect_and_embed.return_value = [
            {"bbox": (6, 6, 48, 48), "embedding": np.ones(4)},
            {"bbox": (35, 10, 40, 40), "embedding": np.ones(4)},
        ]
        processor = ImportProcessor(resources)
        progress = Mock()
        with closing(schema.get_conn()) as connection:
            connection.executescript("""
                INSERT INTO face(id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, is_manual)
                VALUES (10, 1, 10, 10, 40, 40, 1, 1), (11, 1, 70, 10, 40, 40, 3, 0);
            """)
            with patch("backend.services.pipeline.delete_face_thumbnail") as remove_thumbnail:
                processor._process_image(connection.cursor(), connection, 1, None, model, clusterer, progress)
            rows = connection.execute(
                "SELECT id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, is_manual FROM face ORDER BY id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(10, 10, 10, 40, 40, 1, 1), (12, 35, 10, 40, 40, 3, 0)])
        clusterer.add_and_assign.assert_called_once()
        resources.reset_clusterer.assert_called_once_with(clusterer)
        remove_thumbnail.assert_called_once_with(11)
        progress.assert_called_once_with({"total_faces_increment": 1, "processed_faces_increment": 1})

    def test_canonical_dedup_preserves_manual_marking_for_either_image_id(self):
        self.prepare_library()
        with closing(schema.get_conn()) as connection, connection:
            connection.executescript("""
                DROP INDEX idx_image_content_hash;
                UPDATE image SET content_hash = 'same-content';
                INSERT INTO face(id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding, is_manual)
                VALUES
                    (10, 1, 12, 12, 40, 40, 3, X'00000000', 0),
                    (11, 2, 10, 10, 40, 40, 1, NULL, 1),
                    (12, 1, 60, 10, 40, 40, 2, NULL, 1),
                    (13, 2, 60, 10, 40, 40, 3, X'00000000', 0),
                    (14, 2, 85, 10, 40, 40, 3, X'00000000', 0),
                    (15, 1, 150, 10, 40, 40, 1, NULL, 1),
                    (16, 2, 150, 10, 40, 40, 2, X'00000000', 0);
            """)
        schema.init_db(scan_sources=False)
        with closing(schema.get_conn()) as connection:
            rows = connection.execute(
                """SELECT f.id, f.image_id, f.bbox_x, c.person_id, f.is_manual
                   FROM face f JOIN cluster c ON c.id = f.cluster_id ORDER BY f.id"""
            ).fetchall()
            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(
            [tuple(row) for row in rows],
            [(11, 1, 10, 1, 1), (12, 1, 60, 2, 1), (14, 1, 85, None, 0), (15, 1, 150, 1, 1), (16, 1, 150, 2, 0)],
        )
