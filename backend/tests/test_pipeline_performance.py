import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from backend.db import schema
from backend.services.pipeline import (
    ImagePreparer,
    ImportCancelled,
    ImportProcessor,
    get_import_worker_count,
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


class HashedImageIteratorTest(unittest.TestCase):
    @patch.object(ImagePreparer, "hash_image")
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
            for path, future in ImagePreparer(2).iter_hashed(paths)
        ]

        self.assertEqual(
            results,
            [
                ("first.jpg", "first.jpg"),
                ("second.jpg", "second.jpg"),
                ("third.jpg", "third.jpg"),
            ],
        )


class ParallelImportIntegrationTest(unittest.TestCase):
    def test_parallel_preparation_imports_images_and_reports_progress(self):
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
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            progress = {}

            def update(changes):
                progress.update(changes)

            db_path = root / "database.sqlite"
            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                ImportProcessor(resources).process(
                    str(photo_dir),
                    update,
                    threading.Event(),
                )

                conn = schema.get_conn()
                processed_count = conn.execute(
                    "SELECT COUNT(*) FROM image WHERE processed_at IS NOT NULL"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(processed_count, 3)
            self.assertEqual(model.detect_and_embed.call_count, 3)
            self.assertEqual(progress["processed_images"], 3)

    def test_cancellation_stops_before_the_next_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            for index in range(3):
                Image.new("RGB", (32, 32), color=(index * 100, 0, 0)).save(
                    photo_dir / f"photo-{index}.jpg"
                )

            cancel_event = threading.Event()
            model = Mock(compute_mode="gpu")

            def detect(_):
                cancel_event.set()
                return []

            model.detect_and_embed.side_effect = detect
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                with self.assertRaises(ImportCancelled):
                    ImportProcessor(resources).process(
                        str(photo_dir),
                        lambda changes: None,
                        cancel_event,
                    )

                conn = schema.get_conn()
                processed_count = conn.execute(
                    "SELECT COUNT(*) FROM image WHERE processed_at IS NOT NULL"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(processed_count, 1)
            self.assertEqual(model.detect_and_embed.call_count, 1)

            resumed_model = Mock(compute_mode="gpu")
            resumed_model.detect_and_embed.return_value = []
            resumed_resources = Mock()
            resumed_resources.get_model.return_value = resumed_model
            resumed_resources.get_clusterer.return_value = Mock()

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                ImportProcessor(resumed_resources).process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                resumed_count = conn.execute(
                    "SELECT COUNT(*) FROM image WHERE processed_at IS NOT NULL"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(resumed_count, 3)
            self.assertEqual(resumed_model.detect_and_embed.call_count, 2)

    def test_repeat_import_hashes_every_file_without_repeating_inference(self):
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
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
                patch.object(
                    ImagePreparer,
                    "hash_image",
                    wraps=ImagePreparer.hash_image,
                ) as hash_image,
            ):
                schema.init_db()
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                first_inference_count = model.detect_and_embed.call_count
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )

            self.assertEqual(first_inference_count, 3)
            self.assertEqual(model.detect_and_embed.call_count, 3)
            self.assertEqual(hash_image.call_count, 6)

    def test_duplicate_content_in_one_request_is_inferred_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            first = photo_dir / "first.jpg"
            duplicate = photo_dir / "duplicate.jpg"
            Image.new("RGB", (32, 32), color=(100, 20, 30)).save(first)
            duplicate.write_bytes(first.read_bytes())

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                ImportProcessor(resources).process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM image"
                ).fetchone()[0]
                location_count = conn.execute(
                    "SELECT COUNT(*) FROM image_location"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(image_count, 1)
            self.assertEqual(location_count, 2)

    def test_moved_duplicate_is_registered_without_new_inference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "photo.jpg"
            moved_copy = second_dir / "moved.jpg"
            Image.new("RGB", (32, 32), color=(100, 20, 30)).save(first)

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                processor.process(
                    str(first_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                moved_copy.write_bytes(first.read_bytes())
                processor.process(
                    str(second_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM image"
                ).fetchone()[0]
                location_count = conn.execute(
                    "SELECT COUNT(*) FROM image_location"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(image_count, 1)
            self.assertEqual(location_count, 2)

    def test_new_location_removes_missing_old_location(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "photo.jpg"
            moved = second_dir / "moved.jpg"
            Image.new("RGB", (32, 32), color=(100, 20, 30)).save(first)

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                processor.process(
                    str(first_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                moved.write_bytes(first.read_bytes())
                first.unlink()
                processor.process(
                    str(second_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                locations = [
                    row["path"]
                    for row in conn.execute(
                        "SELECT path FROM image_location ORDER BY path"
                    ).fetchall()
                ]
                canonical_path = conn.execute(
                    "SELECT path FROM image"
                ).fetchone()["path"]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(locations, [str(moved)])
            self.assertEqual(canonical_path, str(moved))

    def test_new_location_removes_old_path_with_changed_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "photo.jpg"
            copy = second_dir / "copy.jpg"
            Image.new("RGB", (32, 32), color=(100, 20, 30)).save(first)
            original_bytes = first.read_bytes()

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                processor.process(
                    str(first_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                Image.new("RGB", (32, 32), color=(200, 20, 30)).save(first)
                copy.write_bytes(original_bytes)
                processor.process(
                    str(second_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                locations = [
                    row["path"]
                    for row in conn.execute(
                        "SELECT path FROM image_location ORDER BY path"
                    ).fetchall()
                ]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(locations, [str(copy)])

    def test_changed_file_at_existing_path_is_reprocessed_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            photo = photo_dir / "photo.jpg"
            Image.new("RGB", (32, 32), color=(10, 20, 30)).save(photo)

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=1,
                ),
            ):
                schema.init_db()
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                Image.new("RGB", (32, 32), color=(200, 20, 30)).save(photo)
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM image"
                ).fetchone()[0]
                location_count = conn.execute(
                    "SELECT COUNT(*) FROM image_location"
                ).fetchone()[0]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 2)
            self.assertEqual(image_count, 1)
            self.assertEqual(location_count, 1)

    def test_changed_duplicate_path_preserves_other_location(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            first = photo_dir / "first.jpg"
            second = photo_dir / "second.jpg"
            Image.new("RGB", (32, 32), color=(10, 20, 30)).save(first)
            second.write_bytes(first.read_bytes())

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            processor = ImportProcessor(resources)
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                Image.new("RGB", (32, 32), color=(200, 20, 30)).save(first)
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM image"
                ).fetchone()[0]
                locations = conn.execute(
                    """
                    SELECT location.path, i.content_hash
                    FROM image_location location
                    JOIN image i ON i.id = location.image_id
                    ORDER BY location.path
                    """
                ).fetchall()
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 2)
            self.assertEqual(image_count, 2)
            self.assertEqual(len(locations), 2)
            self.assertNotEqual(
                locations[0]["content_hash"],
                locations[1]["content_hash"],
            )
