import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app
from backend.services import storage


class ImageLocationApiTest(unittest.TestCase):
    @patch("backend.app.open_file_location")
    @patch("backend.app.get_available_image_path", return_value="/photos/image.jpg")
    @patch("backend.app.normalize_import_folder_path", return_value="/photos/image.jpg")
    def test_preferred_location_is_validated_then_revealed(
        self, normalize, available_path, open_location
    ):
        result = app.open_image_location(
            42,
            {"image_path": r"D:\Photos\image.jpg"},
        )

        normalize.assert_called_once_with(r"D:\Photos\image.jpg")
        available_path.assert_called_once_with(
            42,
            "/photos/image.jpg",
            require_preferred=True,
        )
        open_location.assert_called_once_with("/photos/image.jpg")
        self.assertEqual(result, {"status": "opened"})

    @patch("backend.app.get_available_image_path", return_value=None)
    def test_missing_image_location_returns_not_found(self, _available_path):
        with self.assertRaises(HTTPException) as raised:
            app.open_image_location(42, {"image_path": "/missing/image.jpg"})

        self.assertEqual(raised.exception.status_code, 404)

    @patch("backend.app.open_file_location", side_effect=OSError("no file manager"))
    @patch("backend.app.get_available_image_path", return_value="/photos/image.jpg")
    def test_file_manager_failure_is_reported(self, _available_path, _open_location):
        with self.assertRaises(HTTPException) as raised:
            app.open_image_location(42, {"image_path": "/photos/image.jpg"})

        self.assertEqual(raised.exception.status_code, 500)


class AvailableImagePathTest(unittest.TestCase):
    def test_required_preferred_location_does_not_fall_back(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE image_location (
                image_id INTEGER NOT NULL, path TEXT NOT NULL, content_hash TEXT,
                file_device INTEGER, file_inode INTEGER, file_size INTEGER,
                modified_at_ns INTEGER, file_changed_at_ns INTEGER
            )"""
        )
        connection.execute("CREATE TABLE image (id INTEGER PRIMARY KEY, content_hash TEXT)")
        connection.execute(
            "INSERT INTO image(id, content_hash) VALUES (?, ?)",
            (42, "registered"),
        )
        connection.executemany(
            "INSERT INTO image_location(image_id, path) VALUES(?, ?)",
            [(42, "/missing/selected.jpg"), (42, "/available/other.jpg")],
        )

        with patch("backend.services.storage.get_conn", return_value=connection), patch(
            "backend.services.storage.open_registered_photo", side_effect=FileNotFoundError,
        ) as open_source:
            result = storage.get_available_image_path(
                42,
                "/missing/selected.jpg",
                require_preferred=True,
            )

        self.assertIsNone(result)
        open_source.assert_called_once_with("/missing/selected.jpg", "registered", (None,) * 5)


if __name__ == "__main__":
    unittest.main()
