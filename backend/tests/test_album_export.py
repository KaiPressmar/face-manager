import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PIL import Image

from backend.services import album_export


def png_page(size=(3508, 2480)):
    with Image.new("RGB", size, "#b3c8c1") as image, BytesIO() as output:
        image.save(output, "PNG")
        return output.getvalue()


def encoded(data):
    return base64.b64encode(data).decode("ascii")


class AlbumPngExportTest(unittest.TestCase):
    def test_zip_contains_numbered_pngs_and_rejects_invalid_or_oversized_batches_before_picker(self):
        first, second = png_page((1200, 1200)), png_page((1500, 1200))
        pages = [encoded(first), encoded(second)]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Auswahl.zip"
            picker = Mock(return_value=str(destination))
            exporter = album_export.AlbumPngExport(picker)
            for invalid in (pages[:1], pages + [encoded(b"invalid")], pages * 17):
                self.assertEqual(exporter.save_album_zip("Reise.zip", invalid)["status"], "error")
            with patch.object(album_export, "_MAX_ZIP_PNG_BYTES", len(first) + len(second) - 1):
                self.assertEqual(exporter.save_album_zip("Reise.zip", pages)["status"], "error")
            picker.assert_not_called()

            self.assertEqual(exporter.save_album_zip(r"..\Fotos\CON.zip", pages), {"status": "saved"})
            picker.assert_called_once_with("_CON.zip")
            with ZipFile(destination) as archive:
                self.assertEqual(archive.namelist(), ["Seite-01.png", "Seite-02.png"])
                self.assertEqual(archive.read("Seite-01.png"), first)
                self.assertEqual(archive.read("Seite-02.png"), second)

    def test_supported_sizes_write_exact_bytes_to_the_chosen_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Ausgewählt.PNG"
            picker = Mock(return_value=str(destination))
            exporter = album_export.AlbumPngExport(picker, Mock(return_value=True))
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
            exporter = album_export.AlbumPngExport(lambda _: str(destination), Mock(return_value=True))
            with (patch.object(album_export.os, "replace", side_effect=OSError("read-only")),
                  self.assertLogs(album_export.logger, "ERROR")):
                result = exporter.save_album_png("Album.png", encoded(png_page()))
            self.assertEqual(result["status"], "error")
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_publish_does_not_overwrite_target_created_after_selection(self):
        publish_new_file = album_export._publish_new_file
        page = encoded(png_page((1200, 1200)))
        for suffix in ("png", "zip"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / f"Auswahl.{suffix}"
                confirm = Mock(return_value=True)
                exporter = album_export.AlbumPngExport(lambda _: str(destination), confirm)

                def publish_with_competitor(temporary, target):
                    target.write_bytes(b"new competitor")
                    publish_new_file(temporary, target)

                with (
                    patch.object(album_export, "_publish_new_file", side_effect=publish_with_competitor),
                    patch.object(album_export.os, "replace") as replace,
                ):
                    save = getattr(exporter, f"save_album_{suffix}")
                    result = save(destination.name, page if suffix == "png" else [page, page])
                self.assertEqual(result["status"], "error")
                self.assertIn("Zieldatei wurde inzwischen geändert", result["message"])
                self.assertEqual(destination.read_bytes(), b"new competitor")
                self.assertEqual(list(destination.parent.iterdir()), [destination])
                confirm.assert_not_called()
                replace.assert_not_called()

    def test_replacement_checks_that_destination_is_still_the_confirmed_file(self):
        page = encoded(png_page((1200, 1200)))
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Auswahl.png"
            challenger = Path(folder) / "challenger"
            destination.write_bytes(b"original")
            challenger.write_bytes(b"new competitor")
            exporter = album_export.AlbumPngExport(lambda _: str(destination), Mock(return_value=True))
            fsync = album_export.os.fsync

            def sync_with_changed_destination(fd):
                fsync(fd)
                challenger.replace(destination)

            with patch.object(album_export.os, "fsync", side_effect=sync_with_changed_destination):
                result = exporter.save_album_png(destination.name, page)
            self.assertEqual(result["status"], "error")
            self.assertIn("Zieldatei wurde inzwischen geändert", result["message"])
            self.assertEqual(destination.read_bytes(), b"new competitor")
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_existing_file_is_preserved_without_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Auswahl.png"
            destination.write_bytes(b"original")
            exporter = album_export.AlbumPngExport(lambda _: str(destination))
            self.assertEqual(exporter.save_album_png(destination.name, encoded(png_page((1200, 1200))))["status"], "error")
            self.assertEqual(destination.read_bytes(), b"original")
