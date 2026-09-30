import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.services.face_thumbnails import (
    FaceThumbnailWarmupResult,
    create_face_thumbnails_for_image,
    ensure_face_thumbnail,
    get_face_library_signature,
    get_face_thumbnail_path,
    warm_missing_face_thumbnails,
)
from backend.services.face_thumbnail_warmup import FaceThumbnailWarmupQueue


class FaceThumbnailTest(unittest.TestCase):
    def test_ensure_face_thumbnail_creates_resized_cached_crop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "source.jpg"
            Image.new("RGB", (800, 600), "navy").save(image_path)

            with patch("backend.services.face_thumbnails.get_data_root", return_value=root):
                thumbnail_path = ensure_face_thumbnail(12, str(image_path), (10, 20, 500, 300))

                self.assertTrue(thumbnail_path.exists())
                with Image.open(thumbnail_path) as thumbnail:
                    self.assertLessEqual(max(thumbnail.size), 256)
                    self.assertEqual(thumbnail.format, "JPEG")

                first_mtime = thumbnail_path.stat().st_mtime_ns
                time.sleep(0.01)
                reused_path = ensure_face_thumbnail(12, str(image_path), (10, 20, 500, 300))

                self.assertEqual(reused_path, thumbnail_path)
                self.assertEqual(thumbnail_path.stat().st_mtime_ns, first_mtime)

    def test_create_face_thumbnails_for_image_renders_all_faces_from_one_decode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "source.jpg"
            Image.new("RGB", (800, 600), "maroon").save(image_path)

            faces = [
                (1, (10, 10, 200, 200)),
                (2, (300, 100, 150, 150)),
                (3, (500, 300, 120, 120)),
            ]

            with (
                patch("backend.services.face_thumbnails.get_data_root", return_value=root),
                patch(
                    "backend.services.face_thumbnails.Image.open",
                    wraps=Image.open,
                ) as open_spy,
            ):
                create_face_thumbnails_for_image(str(image_path), faces)

                # A single decode is shared across every requested face.
                self.assertEqual(open_spy.call_count, 1)

                for face_id, _ in faces:
                    thumbnail_path = get_face_thumbnail_path(face_id)
                    self.assertTrue(thumbnail_path.exists())
                    with Image.open(thumbnail_path) as thumbnail:
                        self.assertLessEqual(max(thumbnail.size), 256)

    def test_create_face_thumbnails_for_image_skips_existing_and_bad_faces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "source.jpg"
            Image.new("RGB", (400, 400), "olive").save(image_path)

            with patch("backend.services.face_thumbnails.get_data_root", return_value=root):
                ensure_face_thumbnail(1, str(image_path), (10, 10, 100, 100))
                first_mtime = get_face_thumbnail_path(1).stat().st_mtime_ns

                # Face 1 already cached, face 2 has an out-of-bounds/degenerate
                # crop, face 3 is valid; nothing should raise.
                create_face_thumbnails_for_image(
                    str(image_path),
                    [
                        (1, (10, 10, 100, 100)),
                        (2, (10_000, 10_000, 5, 5)),
                        (3, (50, 50, 80, 80)),
                    ],
                )

                self.assertEqual(
                    get_face_thumbnail_path(1).stat().st_mtime_ns, first_mtime
                )
                self.assertFalse(get_face_thumbnail_path(2).exists())
                self.assertTrue(get_face_thumbnail_path(3).exists())

    def test_warm_missing_face_thumbnails_creates_legacy_cache_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "faces.sqlite"
            image_path = root / "source.jpg"
            Image.new("RGB", (640, 480), "teal").save(image_path)

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE face (
                    id INTEGER PRIMARY KEY,
                    image_id INTEGER NOT NULL,
                    bbox_x REAL NOT NULL,
                    bbox_y REAL NOT NULL,
                    bbox_w REAL NOT NULL,
                    bbox_h REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE image_location (
                    id INTEGER PRIMARY KEY,
                    image_id INTEGER NOT NULL,
                    path TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO face(id, image_id, bbox_x, bbox_y, bbox_w, bbox_h)
                VALUES (1, 10, 15, 20, 300, 220)
                """
            )
            conn.execute(
                "INSERT INTO image_location(image_id, path) VALUES (10, ?)",
                (str(image_path),),
            )
            conn.commit()
            conn.close()

            def open_test_conn():
                test_conn = sqlite3.connect(db_path)
                test_conn.row_factory = sqlite3.Row
                return test_conn

            with (
                patch("backend.services.face_thumbnails.get_data_root", return_value=root),
                patch("backend.services.face_thumbnails.get_conn", side_effect=open_test_conn),
            ):
                result = warm_missing_face_thumbnails(max_created=10, scan_limit=10)

                self.assertEqual(result.scanned_faces, 1)
                self.assertEqual(result.created_thumbnails, 1)
                self.assertTrue(result.reached_end)
                self.assertTrue((root / "thumbnails" / "faces" / "0000" / "1.jpg").exists())


    def test_get_face_library_signature_reports_count_and_max_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "faces.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE face (id INTEGER PRIMARY KEY, image_id INTEGER)"
            )
            conn.execute("INSERT INTO face(id, image_id) VALUES (3, 1)")
            conn.execute("INSERT INTO face(id, image_id) VALUES (9, 1)")
            conn.commit()
            conn.close()

            def open_test_conn():
                test_conn = sqlite3.connect(db_path)
                test_conn.row_factory = sqlite3.Row
                return test_conn

            with patch(
                "backend.services.face_thumbnails.get_conn", side_effect=open_test_conn
            ):
                self.assertEqual(get_face_library_signature(), (2, 9))

    def test_warmup_queue_stops_rescanning_when_fully_warm(self):
        warm_calls = []

        def fake_warm(*, after_face_id, max_created, scan_limit, stop_event=None):
            warm_calls.append(after_face_id)
            return FaceThumbnailWarmupResult(
                total_faces=5,
                scanned_faces=5,
                created_thumbnails=0,
                skipped_existing=5,
                highest_face_id=5,
                reached_end=True,
            )

        with (
            patch(
                "backend.services.face_thumbnail_warmup.warm_missing_face_thumbnails",
                side_effect=fake_warm,
            ),
            patch(
                "backend.services.face_thumbnail_warmup.get_face_library_signature",
                return_value=(5, 5),
            ),
        ):
            queue = FaceThumbnailWarmupQueue(is_idle=lambda: True, idle_poll_seconds=0.25)
            queue.start()
            time.sleep(0.8)
            queue.stop()

        # One full sweep, then the unchanged fingerprint makes it stop scanning.
        self.assertEqual(len(warm_calls), 1)
        self.assertEqual(queue.snapshot()["task"]["status"], "stopped")

    def test_warmup_queue_rescans_after_library_changes(self):
        warm_calls = []
        signatures = [(5, 5), (6, 6)]

        def fake_warm(*, after_face_id, max_created, scan_limit, stop_event=None):
            warm_calls.append(after_face_id)
            return FaceThumbnailWarmupResult(
                total_faces=6,
                scanned_faces=6,
                created_thumbnails=0,
                skipped_existing=6,
                highest_face_id=6,
                reached_end=True,
            )

        def fake_signature():
            return signatures.pop(0) if len(signatures) > 1 else signatures[0]

        with (
            patch(
                "backend.services.face_thumbnail_warmup.warm_missing_face_thumbnails",
                side_effect=fake_warm,
            ),
            patch(
                "backend.services.face_thumbnail_warmup.get_face_library_signature",
                side_effect=fake_signature,
            ),
        ):
            queue = FaceThumbnailWarmupQueue(is_idle=lambda: True, idle_poll_seconds=0.25)
            queue.start()
            time.sleep(0.9)
            queue.stop()

        # The fingerprint changing from (5,5) to (6,6) forces a second sweep.
        self.assertGreaterEqual(len(warm_calls), 2)


if __name__ == "__main__":
    unittest.main()
