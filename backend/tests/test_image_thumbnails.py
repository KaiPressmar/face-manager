import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.services import image_thumbnails
from backend.services.image_thumbnails import ensure_image_thumbnail, prune_image_thumbnails


class ImageThumbnailTest(unittest.TestCase):
    def test_orients_resizes_and_reuses_cached_jpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (1200, 800), "navy").save(source, exif=exif)

            with patch("backend.services.image_thumbnails.get_data_root", return_value=root):
                result = Path(ensure_image_thumbnail(42, str(source)))
                self.assertTrue(result.is_file())
                with Image.open(result) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, (320, 480))

                first_mtime = result.stat().st_mtime_ns
                self.assertEqual(ensure_image_thumbnail(42, str(source)), str(result))
                self.assertEqual(result.stat().st_mtime_ns, first_mtime)

    def test_changed_source_uses_new_cache_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (600, 300), "red").save(source)
            with patch("backend.services.image_thumbnails.get_data_root", return_value=root):
                first = Path(ensure_image_thumbnail(5, str(source)))
                original_mtime = source.stat().st_mtime_ns
                Image.new("RGB", (600, 300), "blue").save(source)
                os.utime(source, ns=(original_mtime + 1_000_000_000,) * 2)
                second = Path(ensure_image_thumbnail(5, str(source)))

                self.assertNotEqual(first, second)
                with Image.open(second) as image:
                    red, _, blue = image.getpixel((10, 10))
                    self.assertGreater(blue, red)

    def test_concurrent_requests_produce_one_complete_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.jpg"
            Image.new("RGB", (1600, 1000), "green").save(source)
            with patch("backend.services.image_thumbnails.get_data_root", return_value=root):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    paths = list(pool.map(lambda _: ensure_image_thumbnail(8, str(source)), range(8)))
                self.assertEqual(len(set(paths)), 1)
                with Image.open(paths[0]) as image:
                    image.verify()
                self.assertEqual(len(list((root / "thumbnails" / "images").rglob("*jpg"))), 1)

    def test_replacement_with_preserved_size_and_mtime_uses_new_cache_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, replacement = root / "photo.bmp", root / "replacement.bmp"
            Image.new("RGB", (60, 30), "red").save(source)
            Image.new("RGB", (60, 30), "blue").save(replacement)
            original = source.stat()
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            with patch.object(image_thumbnails, "get_data_root", return_value=root):
                first = ensure_image_thumbnail(5, str(source))
                replacement.replace(source)
                self.assertEqual(source.stat().st_size, original.st_size)
                self.assertEqual(source.stat().st_mtime_ns, original.st_mtime_ns)
                second = ensure_image_thumbnail(5, str(source))

            self.assertNotEqual(first, second)
            with Image.open(second) as image:
                red, _, blue = image.getpixel((10, 10))
                self.assertGreater(blue, red)

    def test_prunes_expired_cache_entries_but_preserves_recently_used_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.jpg"
            Image.new("RGB", (8, 8), "green").save(source)
            source_mtime = source.stat().st_mtime_ns
            with patch.object(image_thumbnails, "get_data_root", return_value=root):
                expired = Path(ensure_image_thumbnail(41, str(source)))
                reused = Path(ensure_image_thumbnail(42, str(source)))
                recent = Path(ensure_image_thumbnail(43, str(source)))
                old_time = time.time() - image_thumbnails._MAX_CACHE_AGE - 60
                for thumbnail in (expired, reused):
                    os.utime(thumbnail, (old_time, old_time))
                self.assertEqual(ensure_image_thumbnail(42, str(source)), str(reused))

                self.assertEqual(prune_image_thumbnails(), 1)
                self.assertFalse(expired.exists())
                self.assertTrue(reused.is_file())
                self.assertTrue(recent.is_file())
                self.assertEqual(source.stat().st_mtime_ns, source_mtime)
                self.assertEqual(ensure_image_thumbnail(41, str(source)), str(expired))
                with Image.open(expired) as image:
                    image.verify()

    def test_pruning_respects_delete_budget_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shard = root / "thumbnails" / "images" / "0000"
            shard.mkdir(parents=True)
            named_directory = shard / f"0-{'a' * 20}.jpg"
            named_directory.mkdir()
            old_time = time.time() - image_thumbnails._MAX_CACHE_AGE - 60
            unrelated = [shard / "notes.jpg", shard / f"1000-{'c' * 20}.jpg"]
            expired = [shard / f"{image_id}-{'b' * 20}.jpg" for image_id in (1, 2, 3)]
            for path in unrelated + expired:
                path.touch()
                os.utime(path, (old_time, old_time))
            with (
                patch.object(image_thumbnails, "get_data_root", return_value=root),
                patch.object(image_thumbnails, "_MAX_PRUNED_ENTRIES", 2),
            ):
                self.assertEqual(prune_image_thumbnails(), 2)
                self.assertEqual(prune_image_thumbnails(), 1)
            self.assertTrue(named_directory.is_dir())
            self.assertTrue(all(path.is_file() for path in unrelated))

    def test_pruning_does_not_follow_linked_directories_or_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "thumbnails" / "images"
            shard = cache / "0000"
            shard.mkdir(parents=True)
            original_root = root / "originals"
            original_root.mkdir()
            original = original_root / f"1005-{'b' * 20}.jpg"
            original.write_bytes(b"original")
            old_time = time.time() - image_thumbnails._MAX_CACHE_AGE - 60
            os.utime(original, (old_time, old_time))
            file_link = shard / f"42-{'a' * 20}.jpg"
            shard_link = cache / "0001"
            try:
                file_link.symlink_to(original)
                shard_link.symlink_to(original_root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symbolic links are unavailable: {exc}")
            with patch.object(image_thumbnails, "get_data_root", return_value=root):
                self.assertEqual(prune_image_thumbnails(), 0)
            self.assertTrue(file_link.is_symlink())
            self.assertTrue(shard_link.is_symlink())
            self.assertEqual(original.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
