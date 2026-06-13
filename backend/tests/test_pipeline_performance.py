import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from backend.db import schema
from backend.services.pipeline import (
    PROCESS_STATE,
    _get_processed_paths,
    _prepare_images,
    get_import_worker_count,
    process_folder,
)


class ImportWorkerCountTest(unittest.TestCase):
    def test_gpu_uses_up_to_four_workers(self):
        self.assertEqual(get_import_worker_count("gpu", cpu_count=12), 4)
        self.assertEqual(get_import_worker_count("gpu", cpu_count=6), 2)

    def test_cpu_uses_up_to_two_workers(self):
        self.assertEqual(get_import_worker_count("cpu", cpu_count=12), 2)
        self.assertEqual(get_import_worker_count("cpu", cpu_count=2), 1)

    @patch.dict(os.environ, {"FACE_MANAGER_IMPORT_WORKERS": "6"})
    def test_environment_override_is_honored(self):
        self.assertEqual(get_import_worker_count("gpu", cpu_count=2), 6)


class PreparedImageIteratorTest(unittest.TestCase):
    @patch("backend.services.pipeline._prepare_image")
    def test_results_remain_in_input_order(self, prepare_image):
        def prepare(path):
            time.sleep(0.01 if path.name == "first.jpg" else 0)
            return path.name

        prepare_image.side_effect = prepare
        paths = [
            Path("first.jpg"),
            Path("second.jpg"),
            Path("third.jpg"),
        ]

        results = [
            (path.name, future.result())
            for path, future in _prepare_images(paths, worker_count=2)
        ]

        self.assertEqual(
            results,
            [
                ("first.jpg", "first.jpg"),
                ("second.jpg", "second.jpg"),
                ("third.jpg", "third.jpg"),
            ],
        )


class ProcessedPathLookupTest(unittest.TestCase):
    def test_only_processed_selected_paths_are_returned(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE image (
                id INTEGER PRIMARY KEY,
                processed_at TEXT
            );
            CREATE TABLE image_location (
                image_id INTEGER,
                path TEXT
            );
            INSERT INTO image VALUES (1, CURRENT_TIMESTAMP);
            INSERT INTO image VALUES (2, NULL);
            INSERT INTO image_location VALUES (1, '/photos/done.jpg');
            INSERT INTO image_location VALUES (2, '/photos/pending.jpg');
            INSERT INTO image_location VALUES (1, '/photos/not-selected.jpg');
            """
        )

        processed = _get_processed_paths(
            conn.cursor(),
            [Path("/photos/done.jpg"), Path("/photos/pending.jpg")],
        )

        self.assertEqual(processed, {"/photos/done.jpg"})
        conn.close()


class ParallelImportIntegrationTest(unittest.TestCase):
    def test_parallel_preparation_imports_images_and_updates_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            for index in range(3):
                Image.new("RGB", (32, 32), color=(index * 100, 0, 0)).save(
                    photo_dir / f"photo-{index}.jpg"
                )

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline._ensure_clusterer_loaded"
                ),
                patch(
                    "backend.services.pipeline._ensure_face_model_loaded",
                    return_value=model,
                ),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                process_folder(str(photo_dir))

                conn = schema.get_conn()
                processed_count = conn.execute(
                    "SELECT COUNT(*) FROM image WHERE processed_at IS NOT NULL"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(processed_count, 3)
            self.assertEqual(model.detect_and_embed.call_count, 3)
            self.assertEqual(PROCESS_STATE["status"], "done")
            self.assertEqual(PROCESS_STATE["processed_images"], 3)
