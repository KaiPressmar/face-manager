import hashlib
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PIL import Image

from backend.services.face_thumbnails import (
    FaceThumbnailWarmupResult,
    create_face_thumbnails_for_image,
    delete_face_thumbnail,
    ensure_face_thumbnail,
    get_cached_face_thumbnail,
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

    def test_reused_face_id_matches_current_photo_and_bbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original, replacement = root / "red.png", root / "blue.png"
            Image.new("RGB", (32, 32), "red").save(original)
            Image.new("RGB", (32, 32), "blue").save(replacement)
            bbox = (0, 0, 20, 20)

            with patch("backend.services.face_thumbnails.get_data_root", return_value=root):
                old_crop = ensure_face_thumbnail(42, str(original), bbox)
                restored_crop = ensure_face_thumbnail(42, str(replacement), bbox)
                edited_crop = ensure_face_thumbnail(42, str(replacement), (2, 2, 16, 16))
                self.assertEqual(len({old_crop.name, restored_crop.name, edited_crop.name}), 3)
                with Image.open(edited_crop) as image:
                    self.assertEqual(image.size, (16, 16))
                    self.assertGreater(image.getpixel((0, 0))[2], 240)
                Image.new("RGB", (32, 32), "yellow").save(replacement)
                changed_crop = ensure_face_thumbnail(42, str(replacement), bbox)
                self.assertNotEqual(changed_crop.name, restored_crop.name)
                delete_face_thumbnail(42)
                self.assertEqual(list(old_crop.parent.glob("42-*.jpg")), [])

    def test_mirrored_exif_crop_uses_the_same_source_region(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "mirrored.png"
            photo = Image.new("RGB", (40, 24), "blue")
            photo.paste("red", (0, 0, 20, 24))
            exif = photo.getexif()
            exif[274] = 2
            photo.save(original, exif=exif)

            with patch("backend.services.face_thumbnails.get_data_root", return_value=root):
                path = ensure_face_thumbnail(3, str(original), (0, 0, 16, 20))
                with Image.open(path) as image:
                    self.assertGreater(image.getpixel((6, 6))[0], 240)

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

                for face_id, bbox in faces:
                    thumbnail_path = get_face_thumbnail_path(face_id, str(image_path), bbox)
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
                first_mtime = get_face_thumbnail_path(1, str(image_path), (10, 10, 100, 100)).stat().st_mtime_ns

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
                    get_face_thumbnail_path(1, str(image_path), (10, 10, 100, 100)).stat().st_mtime_ns, first_mtime
                )
                self.assertFalse(get_face_thumbnail_path(2, str(image_path), (10_000, 10_000, 5, 5)).exists())
                self.assertTrue(get_face_thumbnail_path(3, str(image_path), (50, 50, 80, 80)).exists())

    def test_warm_missing_face_thumbnails_creates_versioned_cache_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "faces.sqlite"
            image_path = root / "source.jpg"
            Image.new("RGB", (640, 480), "teal").save(image_path)

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE image (id INTEGER PRIMARY KEY, content_hash TEXT)")
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
                    path TEXT NOT NULL,
                    content_hash TEXT
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
                thumbnail = get_face_thumbnail_path(1, str(image_path), (15, 20, 300, 220))
                self.assertTrue(thumbnail.exists())
                warm_again = warm_missing_face_thumbnails(max_created=10, scan_limit=10)
                self.assertEqual(warm_again.skipped_existing, 1)
                self.assertEqual(warm_again.created_thumbnails, 0)

    def test_registered_warmup_reads_verified_copy_once_and_then_works_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "copy.png"
            Image.new("RGB", (100, 80), "teal").save(source)
            stale = root / "changed.png"
            Image.new("RGB", (100, 80), "red").save(stale)
            content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            db_path = root / "faces.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE image (id INTEGER PRIMARY KEY, content_hash TEXT);
                    CREATE TABLE image_location (image_id INTEGER, path TEXT, content_hash TEXT);
                    CREATE TABLE face (id INTEGER PRIMARY KEY, image_id INTEGER,
                        bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL);
                    INSERT INTO face VALUES (1, 10, 1, 2, 30, 40), (2, 10, 5, 6, 30, 40);
                    """
                )
                conn.execute("INSERT INTO image VALUES (10, ?)", (content_hash,))
                conn.executemany(
                    "INSERT INTO image_location(image_id, path) VALUES (10, ?)",
                    [(str(root / "a-missing.png"),), (str(stale),), (str(source),)],
                )

            def get_conn():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                return conn

            from backend.services.face_thumbnails import decode_verified_preview

            with (
                patch("backend.services.face_thumbnails.get_data_root", return_value=root),
                patch("backend.services.face_thumbnails.get_conn", side_effect=get_conn),
                patch(
                    "backend.services.face_thumbnails.decode_verified_preview",
                    wraps=decode_verified_preview,
                ) as decode,
            ):
                result = warm_missing_face_thumbnails()
                self.assertEqual(result.created_thumbnails, 2)
                self.assertEqual(decode.call_count, 3)  # missing, changed, then verified copy
                cached = get_cached_face_thumbnail(1, content_hash, (1, 2, 30, 40))
                self.assertIsNotNone(cached)
                source.unlink()
                decode.reset_mock()
                self.assertEqual(warm_missing_face_thumbnails().skipped_existing, 2)
                decode.assert_not_called()

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

    def test_incomplete_scan_retries_and_blocked_worker_retains_ownership(self):
        entered, release, restarted, retried = Event(), Event(), Event(), Event()

        def fake_warm(**_kwargs):
            if not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release the first batch")
            elif not restarted.is_set():
                restarted.set()
            else:
                retried.set()
            return FaceThumbnailWarmupResult(
                total_faces=1, scanned_faces=1, skipped_missing_source=1, reached_end=True
            )

        with (
            patch(
                "backend.services.face_thumbnail_warmup.warm_missing_face_thumbnails",
                side_effect=fake_warm,
            ),
            patch(
                "backend.services.face_thumbnail_warmup.get_face_library_signature",
                return_value=(1, 1),
            ),
        ):
            queue = FaceThumbnailWarmupQueue(
                is_idle=lambda: True, idle_poll_seconds=0.25, retry_seconds=0.25
            )
            queue.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(queue.has_active_batch())
                self.assertFalse(queue.stop(timeout=0.01))
                queue.start()
                queue.invalidate_library()
                self.assertFalse(restarted.is_set())
                release.set()
                self.assertTrue(restarted.wait(1))
                self.assertTrue(retried.wait(1))
                self.assertFalse(queue.snapshot()["task"]["cache_complete"])
            finally:
                release.set()
                self.assertTrue(queue.stop())


if __name__ == "__main__":
    unittest.main()
