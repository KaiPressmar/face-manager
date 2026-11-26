import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from backend.services import album_export


def png_page(size=(3508, 2480)):
    with Image.new("RGB", size, "#b3c8c1") as image, BytesIO() as output:
        image.save(output, "PNG")
        return output.getvalue()


def encoded(data):
    return base64.b64encode(data).decode("ascii")


class AlbumPngExportTest(unittest.TestCase):
    def test_supported_sizes_write_exact_bytes_to_the_chosen_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Ausgewählt.PNG"
            picker = Mock(return_value=str(destination))
            exporter = album_export.AlbumPngExport(picker)
            for size in ((3508, 2480), (2480, 3508), (3000, 3000), (3700, 2077), (1200, 2400)):
                with self.subTest(size=size):
                    data = png_page(size)
                    self.assertEqual(exporter.save_album_png(r"..\Fotos\CON.jpg", encoded(data)), {"status": "saved"})
                    self.assertEqual(destination.read_bytes(), data)
            picker.assert_called_with("_CON.png")
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_invalid_payloads_do_not_open_disk_picker(self):
        picker = Mock()
        exporter = album_export.AlbumPngExport(picker)
        for data in ("@", encoded(b"not PNG"), encoded(png_page((40, 40))), encoded(png_page()[:50]),
                     encoded(png_page((1200, 2401))), encoded(png_page((4200, 4200))),
                     encoded(png_page((4801, 2500)))):
            with self.subTest(data=data[:20]):
                self.assertEqual(exporter.save_album_png("Album.png", data)["status"], "error")
        with patch.object(album_export, "_MAX_ENCODED_LENGTH", 8):
            self.assertEqual(exporter.save_album_png("Album.png", "A" * 12)["status"], "error")
        picker.assert_not_called()

    def test_cancellation_and_declined_overwrite_leave_the_original_alone(self):
        data = encoded(png_page())
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "existing.png"
            destination.write_bytes(b"original")
            confirm = Mock(return_value=False)
            self.assertEqual(album_export.AlbumPngExport(lambda _: None).save_album_png("Album.png", data),
                             {"status": "cancelled"})
            exporter = album_export.AlbumPngExport(lambda _: str(destination), confirm)
            self.assertEqual(exporter.save_album_png("Album.png", data), {"status": "cancelled"})
            confirm.assert_called_once_with("existing.png")
            self.assertEqual(destination.read_bytes(), b"original")

    def test_failed_replacement_keeps_original_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "existing.png"
            destination.write_bytes(b"original")
            exporter = album_export.AlbumPngExport(lambda _: str(destination))
            with (patch.object(album_export.os, "replace", side_effect=OSError("read-only")),
                  self.assertLogs(album_export.logger, "ERROR")):
                result = exporter.save_album_png("Album.png", encoded(png_page()))
            self.assertEqual(result["status"], "error")
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list(destination.parent.iterdir()), [destination])
