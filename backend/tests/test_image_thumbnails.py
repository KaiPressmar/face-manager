import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.services.image_thumbnails import ensure_image_thumbnail


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


if __name__ == "__main__":
    unittest.main()
