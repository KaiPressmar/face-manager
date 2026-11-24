import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from backend.db import schema
from backend.services import storage
from backend.services.face_thumbnails import get_cached_face_thumbnail
from backend.services.image_thumbnails import get_cached_image_thumbnail
from backend.services.person_suggestion_worker import PersonSuggestionWorker
from backend.services.pipeline import (
    ImagePreparer,
    ImportCancelled,
    ImportFailed,
    ImportProcessor,
    _filesystem_path,
    _stored_filesystem_path,
    get_import_worker_count,
)


class ImportWorkerCountTest(unittest.TestCase):
    def test_gpu_uses_up_to_eight_workers(self):
        self.assertEqual(get_import_worker_count("gpu", cpu_count=12), 6)
        self.assertEqual(get_import_worker_count("gpu", cpu_count=6), 3)

    def test_cpu_uses_up_to_four_workers(self):
        self.assertEqual(get_import_worker_count("cpu", cpu_count=12), 4)
        self.assertEqual(get_import_worker_count("cpu", cpu_count=2), 1)

    @patch.dict(os.environ, {"FACE_MANAGER_IMPORT_WORKERS": "6"})
    def test_environment_override_is_honored(self):
        self.assertEqual(get_import_worker_count("gpu", cpu_count=2), 6)


class WindowsFilesystemPathTest(unittest.TestCase):
    @patch("backend.services.pipeline.os.name", "nt")
    def test_drive_and_unc_paths_use_extended_length_form(self):
        drive_path = r"C:\Users\Kai Prüfer\very long folder\photo 1.jpg"
        unc_path = r"\\server\Fotos mit Leerzeichen\Urlaub\bild.jpg"

        self.assertEqual(_filesystem_path(drive_path), rf"\\?\{drive_path}")
        self.assertEqual(
            _filesystem_path(unc_path),
            r"\\?\UNC\server\Fotos mit Leerzeichen\Urlaub\bild.jpg",
        )
        self.assertEqual(_stored_filesystem_path(_filesystem_path(drive_path)), drive_path)
        self.assertEqual(_stored_filesystem_path(_filesystem_path(unc_path)), unc_path)

    def test_truncated_jpeg_is_decoded_best_effort(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "truncated.jpg"
            Image.new("RGB", (64, 64), color=(100, 50, 25)).save(
                image_path,
                quality=90,
            )
            image_path.write_bytes(image_path.read_bytes()[:-8])

            decoded = ImagePreparer.decode(image_path)

            self.assertEqual(decoded.shape, (64, 64, 3))


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

    @patch.object(ImagePreparer, "hash_image")
    def test_completed_iterator_yields_finished_hashes_first(self, prepare_image):
        second_hash_ready = threading.Event()

        def prepare(path):
            if path.name == "first.jpg":
                return path.name
            second_hash_ready.wait(1)
            return path.name

        prepare_image.side_effect = prepare
        paths = [
            Path("first.jpg"),
            Path("second.jpg"),
        ]

        results = []

        def run_iterator():
            for path, future in ImagePreparer(2).iter_hashed_completed(paths):
                results.append((path.name, future.result()))

        thread = threading.Thread(target=run_iterator)
        thread.start()

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not results:
            time.sleep(0.01)

        self.assertEqual(results, [("first.jpg", "first.jpg")])
        second_hash_ready.set()
        thread.join(timeout=1)
        self.assertEqual(
            results,
            [
                ("first.jpg", "first.jpg"),
                ("second.jpg", "second.jpg"),
            ],
        )


class ParallelImportIntegrationTest(unittest.TestCase):
    def test_committed_import_triggers_person_suggestions_without_reclustering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            embedding = np.zeros(512, dtype=np.float32)
            embedding[0] = 1
            for index in range(3):
                Image.new("RGB", (16, 16), color=(index + 1, 24, 28)).save(root / f"new-{index}.png")

            resources = Mock()
            resources.get_model.return_value.detect_and_embed.return_value = [
                {"embedding": embedding, "bbox": (0, 0, 8, 8)}
            ]
            resources.get_clusterer.return_value.add_and_assign.return_value = ([2], None)

            with patch.object(schema, "DB_PATH", str(root / "library.sqlite")):
                schema.init_db()
                with closing(schema.get_conn()) as conn, conn:
                    conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
                    conn.execute("INSERT INTO cluster(id, person_id) VALUES (1, 1), (2, NULL)")
                    conn.execute(
                        "INSERT INTO image(id, path, filename, directory) VALUES (1, ?, 'reference.png', ?)",
                        (str(root / "reference.png"), str(root)),
                    )
                    conn.execute("INSERT INTO face(image_id, cluster_id, embedding) VALUES (1, 1, ?)",
                                 (embedding.tobytes(),))
                self.assertTrue(storage.refresh_pending_person_suggestions())
                self.assertEqual(storage.list_person_suggestions(), [])

                ImportProcessor(resources).process(str(root), lambda _: None, threading.Event())
                self.assertEqual(storage.count_scoped_reclusterable_faces(), 0)
                published = threading.Event()
                worker = PersonSuggestionWorker(
                    lambda: True, storage.refresh_pending_person_suggestions, published.set
                )
                worker.start()
                try:
                    self.assertTrue(published.wait(timeout=5))
                    [suggestion] = storage.list_person_suggestions()
                    self.assertEqual(suggestion["person_name"], "Anna")
                    self.assertEqual(suggestion["support_count"], 3)
                finally:
                    worker.stop()

    def test_file_with_image_extension_but_invalid_content_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            Image.new("RGB", (16, 16), color=(20, 40, 60)).save(
                photo_dir / "valid.jpg"
            )
            (photo_dir / "cache.jpg").write_text("not an image", encoding="utf-8")

            model = Mock(compute_mode="cpu")
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            errors = []
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=1,
                ),
            ):
                schema.init_db()
                ImportProcessor(resources).process(
                    str(photo_dir),
                    lambda changes: errors.append(changes["last_error"])
                    if "last_error" in changes
                    else None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                stored_paths = [
                    row["path"]
                    for row in conn.execute("SELECT path FROM image").fetchall()
                ]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(stored_paths, [str(photo_dir / "valid.jpg")])
            self.assertEqual(len(errors), 1)
            self.assertIn("cache.jpg", errors[0])

    def test_file_replaced_after_hash_is_not_committed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo = root / "photo.png"
            Image.new("RGB", (32, 32), "red").save(photo)
            resources = Mock()
            original_hash = ImagePreparer.hash_image

            def hash_then_replace(path):
                result = original_hash(path)
                replacement = root / "replacement.tmp"
                Image.new("RGB", (32, 32), "blue").save(replacement, format="PNG")
                os.utime(replacement, ns=(result.modified_at_ns, result.modified_at_ns))
                replacement.replace(path)
                return result

            with (
                patch.object(schema, "DB_PATH", str(root / "database.sqlite")),
                patch.object(ImagePreparer, "hash_image", side_effect=hash_then_replace),
            ):
                schema.init_db()
                with self.assertRaisesRegex(ImportFailed, "während des Imports geändert"):
                    ImportProcessor(resources).process(str(root), lambda _: None, threading.Event())
                conn = schema.get_conn()
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 0)
                finally:
                    conn.close()

            resources.get_model.assert_not_called()

    def test_file_changed_during_inference_keeps_previous_library_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo = root / "photo.png"
            Image.new("RGB", (32, 32), "red").save(photo)
            model = Mock()
            model.detect_and_embed.return_value = []
            resources = Mock()
            resources.get_model.return_value = model
            processor = ImportProcessor(resources)

            def detect_after_change(_pixels):
                Image.new("RGB", (32, 32), "green").save(photo)
                return []

            with patch.object(schema, "DB_PATH", str(root / "database.sqlite")):
                schema.init_db()
                processor.process(str(root), lambda _: None, threading.Event())
                conn = schema.get_conn()
                try:
                    original = conn.execute("SELECT id, content_hash FROM image").fetchone()
                    Image.new("RGB", (48, 48), "blue").save(photo)
                    model.detect_and_embed.side_effect = detect_after_change
                    with self.assertRaises(ImportFailed):
                        processor.process(str(root), lambda _: None, threading.Event())
                    actual = conn.execute("SELECT id, content_hash FROM image").fetchall()
                    self.assertEqual([tuple(row) for row in actual], [tuple(original)])
                    location = conn.execute("SELECT path, image_id FROM image_location").fetchone()
                    self.assertEqual(tuple(location), (str(photo), original["id"]))
                finally:
                    conn.close()

    def test_source_change_after_index_mutation_discards_phantom_embedding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a.png"
            second = root / "b.png"
            Image.new("RGB", (32, 32), "red").save(first)
            Image.new("RGB", (32, 32), "blue").save(second)
            model = Mock()
            model.detect_and_embed.return_value = [
                {"bbox": (1, 2, 10, 12), "embedding": np.ones(512, dtype=np.float32)}
            ]
            poisoned_index, fresh_index = Mock(), Mock()
            fresh_index.add_and_assign.return_value = (np.array([7]), None)

            def index_first_face(*_args, **_kwargs):
                Image.new("RGB", (48, 48), "green").save(first)
                return np.array([99]), None

            poisoned_index.add_and_assign.side_effect = index_first_face
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.side_effect = [poisoned_index, fresh_index]

            with (
                patch.object(schema, "DB_PATH", str(root / "database.sqlite")),
                patch("backend.services.pipeline.get_import_worker_count", return_value=1),
            ):
                schema.init_db()
                ImportProcessor(resources).process(str(root), lambda _: None, threading.Event())
                conn = schema.get_conn()
                try:
                    faces = conn.execute(
                        "SELECT image.path, face.cluster_id FROM image JOIN face ON face.image_id = image.id"
                    ).fetchall()
                    self.assertEqual([tuple(row) for row in faces], [(str(second), 7)])
                    self.assertIsNone(conn.execute("SELECT id FROM cluster WHERE id = 99").fetchone())
                finally:
                    conn.close()

            resources.reset_clusterer.assert_called_once_with(poisoned_index)
            fresh_index.add_and_assign.assert_called_once()

    def test_model_initialization_failure_is_fatal_without_partial_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                Image.new("RGB", (32, 32), (index * 100, 0, 0)).save(root / f"{index}.png")
            resources = Mock()
            resources.get_model.side_effect = RuntimeError("model unavailable")
            progress = {}

            with patch.object(schema, "DB_PATH", str(root / "database.sqlite")):
                schema.init_db()
                with self.assertRaisesRegex(ImportFailed, "model unavailable"):
                    ImportProcessor(resources).process(str(root), progress.update, threading.Event())
                conn = schema.get_conn()
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 0)
                finally:
                    conn.close()

            resources.get_model.assert_called_once()
            self.assertNotEqual(progress["stage"], "finalizing")

    def test_no_successful_inference_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Image.new("RGB", (32, 32), "red").save(root / "photo.png")
            model = Mock()
            model.detect_and_embed.side_effect = RuntimeError("inference failed")
            resources = Mock()
            resources.get_model.return_value = model
            progress = {}

            with patch.object(schema, "DB_PATH", str(root / "database.sqlite")):
                schema.init_db()
                with self.assertRaisesRegex(ImportFailed, "inference failed"):
                    ImportProcessor(resources).process(str(root), progress.update, threading.Event())
                conn = schema.get_conn()
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 0)
                finally:
                    conn.close()

            model.detect_and_embed.assert_called_once()
            self.assertNotEqual(progress["stage"], "finalizing")

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
            stages = []

            def update(changes):
                progress.update(changes)
                if "stage" in changes:
                    stages.append(changes["stage"])

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
            self.assertEqual(resources.get_clusterer.call_count, 3)
            self.assertEqual(progress["processed_images"], 3)
            self.assertEqual(progress["stage"], "finalizing")
            self.assertEqual(progress["stage_current"], 3)
            self.assertEqual(
                stages,
                [
                    "scanning",
                    "hashing",
                    "processing",
                    "loading_model",
                    "loading_index",
                    "processing",
                    "finalizing",
                ],
            )

    def test_stale_cluster_proposal_cannot_join_confirmed_person(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"
            image_path = root / "photo.jpg"
            Image.new("RGB", (32, 32), color=(80, 40, 20)).save(image_path)

            model = Mock()
            embedding = np.zeros(512, dtype=np.float32)
            model.detect_and_embed.return_value = [
                {"bbox": (1, 2, 10, 12), "embedding": embedding},
                {"bbox": (4, 5, 9, 11), "embedding": embedding},
            ]
            clusterer = Mock()
            clusterer.add_and_assign.return_value = (
                np.array([7]),
                None,
            )

            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                conn.execute("INSERT INTO person(id, name) VALUES (1, 'Anna')")
                conn.execute(
                    "INSERT INTO cluster(id, label, person_id) VALUES (7, 'Anna', 1)"
                )
                image_id = conn.execute(
                    "INSERT INTO image(path, directory, filename) VALUES (?, ?, ?)",
                    (str(image_path), str(root), image_path.name),
                ).lastrowid
                conn.commit()

                ImportProcessor()._process_image(
                    conn.cursor(),
                    conn,
                    image_id,
                    np.zeros((32, 32, 3), dtype=np.uint8),
                    model,
                    clusterer,
                    lambda _changes: None,
                )

                rows = conn.execute(
                    """
                    SELECT f.cluster_id, c.person_id
                    FROM face f JOIN cluster c ON c.id = f.cluster_id
                    ORDER BY f.id
                    """
                ).fetchall()
                conn.close()

            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["cluster_id"] != 7 for row in rows))
            self.assertEqual(rows[0]["cluster_id"], rows[1]["cluster_id"])
            self.assertTrue(all(row["person_id"] is None for row in rows))

    def test_face_detection_does_not_hold_sqlite_writer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"
            image_path = root / "photo.jpg"
            Image.new("RGB", (16, 16), color=(20, 40, 80)).save(image_path)
            detection_started = threading.Event()
            allow_detection_to_finish = threading.Event()
            failure = []

            model = Mock()

            def detect(_image):
                detection_started.set()
                allow_detection_to_finish.wait(2)
                return []

            model.detect_and_embed.side_effect = detect

            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                seed = schema.get_conn()
                image_id = seed.execute(
                    "INSERT INTO image(path, directory, filename) VALUES (?, ?, ?)",
                    (str(image_path), str(root), image_path.name),
                ).lastrowid
                seed.commit()
                seed.close()

                def run_processing():
                    connection = schema.get_conn()
                    try:
                        ImportProcessor()._process_image(
                            connection.cursor(),
                            connection,
                            image_id,
                            np.zeros((16, 16, 3), dtype=np.uint8),
                            model,
                            Mock(),
                            lambda _changes: None,
                        )
                    except Exception as exc:  # pragma: no cover - asserted below
                        failure.append(exc)
                    finally:
                        connection.close()

                worker = threading.Thread(target=run_processing)
                worker.start()
                self.assertTrue(detection_started.wait(1))

                interactive = sqlite3.connect(db_path, timeout=0.2)
                try:
                    interactive.execute("INSERT INTO person(name) VALUES ('Bob')")
                    interactive.commit()
                finally:
                    interactive.close()
                    allow_detection_to_finish.set()
                worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(failure, [])

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

    def test_processing_can_start_before_all_hashes_finish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            first = photo_dir / "first.jpg"
            second = photo_dir / "second.jpg"
            Image.new("RGB", (32, 32), color=(100, 0, 0)).save(first)
            Image.new("RGB", (32, 32), color=(0, 100, 0)).save(second)

            second_hash_blocked = threading.Event()
            model_called = threading.Event()

            real_hash_image = ImagePreparer.hash_image

            def slow_hash(path):
                if path == second:
                    second_hash_blocked.wait(1)
                return real_hash_image(path)

            model = Mock(compute_mode="gpu")
            model.detect_and_embed.return_value = []

            def detect(_):
                model_called.set()
                return []

            model.detect_and_embed.side_effect = detect
            resources = Mock()
            resources.get_model.return_value = model
            resources.get_clusterer.return_value = Mock()
            db_path = root / "database.sqlite"

            with (
                patch.object(schema, "DB_PATH", str(db_path)),
                patch.object(ImagePreparer, "hash_image", side_effect=slow_hash),
                patch(
                    "backend.services.pipeline.get_import_worker_count",
                    return_value=2,
                ),
            ):
                schema.init_db()

                def run_import():
                    ImportProcessor(resources).process(
                        str(photo_dir),
                        lambda changes: None,
                        threading.Event(),
                    )

                thread = threading.Thread(target=run_import)
                thread.start()

                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not model_called.is_set():
                    time.sleep(0.01)

                self.assertTrue(model_called.is_set())
                self.assertFalse(second_hash_blocked.is_set())
                second_hash_blocked.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

                conn = schema.get_conn()
                processed_count = conn.execute(
                    "SELECT COUNT(*) FROM image WHERE processed_at IS NOT NULL"
                ).fetchone()[0]
                conn.close()

                self.assertEqual(processed_count, 2)

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

            self.assertEqual(resumed_count, 2)
            self.assertEqual(resumed_model.detect_and_embed.call_count, 0)

    def test_repeat_import_skips_unchanged_hashing_and_inference(self):
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
            self.assertEqual(hash_image.call_count, 3)

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
                image_count = conn.execute("SELECT COUNT(*) FROM image").fetchone()[0]
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
                image_count = conn.execute("SELECT COUNT(*) FROM image").fetchone()[0]
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
                canonical_path = conn.execute("SELECT path FROM image").fetchone()[
                    "path"
                ]
                conn.close()

            self.assertEqual(model.detect_and_embed.call_count, 1)
            self.assertEqual(locations, [str(moved)])
            self.assertEqual(canonical_path, str(moved))

    def test_duplicate_preserves_offline_location_and_import_prewarms_committed_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir, copy_dir = root / "first", root / "copy"
            first_dir.mkdir()
            copy_dir.mkdir()
            first, copy = first_dir / "photo.jpg", copy_dir / "photo.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (64, 32), "teal").save(first, exif=exif)
            copy.write_bytes(first.read_bytes())
            bbox = (4, 5, 10, 12)
            resources = Mock()
            model = resources.get_model.return_value
            model.detect_and_embed.return_value = [
                {"bbox": bbox, "embedding": np.zeros(512, dtype=np.float32)}
            ]
            resources.get_clusterer.return_value.add_and_assign.return_value = (np.array([1]), None)
            processor = ImportProcessor(resources)
            with (
                patch.object(schema, "DB_PATH", str(root / "db.sqlite")),
                patch.dict(os.environ, {"FACE_MANAGER_DATA_DIR": str(root)}),
            ):
                schema.init_db()
                processor.process(str(first_dir), lambda changes: None, threading.Event())
                first_dir.rename(root / "offline-volume")
                processor.process(str(copy_dir), lambda changes: None, threading.Event())
                copy.unlink()
                with closing(schema.get_conn()) as conn:
                    image = conn.execute(
                        "SELECT id, content_hash, raw_width, raw_height, exif_orientation FROM image"
                    ).fetchone()
                    face = conn.execute("SELECT id FROM face").fetchone()
                    paths = {row[0] for row in conn.execute("SELECT path FROM image_location")}
                self.assertEqual(paths, {str(first), str(copy)})
                self.assertEqual(tuple(image)[2:], (64, 32, 6))
                thumbnail = get_cached_image_thumbnail(image["id"], image["content_hash"])
                self.assertIsNotNone(thumbnail)
                with Image.open(thumbnail) as preview:
                    self.assertEqual(preview.size, (32, 64))
                self.assertIsNotNone(get_cached_face_thumbnail(face["id"], image["content_hash"], bbox))
            self.assertEqual(model.detect_and_embed.call_count, 1)

    def test_preview_failure_after_commit_does_not_undo_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Image.new("RGB", (32, 32), "teal").save(root / "photo.jpg")
            resources = Mock()
            resources.get_model.return_value.detect_and_embed.return_value = []

            def fail_write(*_):
                with sqlite3.connect(root / "db.sqlite", timeout=0.1) as conn:
                    conn.execute("INSERT INTO person(name) VALUES ('Interactive')")
                raise OSError("cache is read-only")

            with (
                patch.object(schema, "DB_PATH", str(root / "db.sqlite")),
                patch("backend.services.pipeline.write_image_thumbnail", side_effect=fail_write),
            ):
                schema.init_db()
                ImportProcessor(resources).process(str(root), lambda changes: None, threading.Event())
                with closing(schema.get_conn()) as conn:
                    self.assertIsNotNone(conn.execute("SELECT processed_at FROM image").fetchone()[0])
                    self.assertEqual(conn.execute("SELECT name FROM person").fetchone()[0], "Interactive")

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
            photo.write_bytes(photo.read_bytes().ljust(1024, b"\0"))
            original = photo.stat()

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
                replacement = root / "replacement.jpg"
                Image.new("RGB", (32, 32), color=(200, 20, 30)).save(replacement)
                replacement.write_bytes(replacement.read_bytes().ljust(1024, b"\0"))
                os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
                replacement.replace(photo)
                self.assertEqual(photo.stat().st_size, original.st_size)
                self.assertEqual(photo.stat().st_mtime_ns, original.st_mtime_ns)
                processor.process(
                    str(photo_dir),
                    lambda changes: None,
                    threading.Event(),
                )
                conn = schema.get_conn()
                image_count = conn.execute("SELECT COUNT(*) FROM image").fetchone()[0]
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
                image_count = conn.execute("SELECT COUNT(*) FROM image").fetchone()[0]
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
