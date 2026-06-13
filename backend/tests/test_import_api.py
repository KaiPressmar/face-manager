import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend import app


class ImportApiTest(unittest.TestCase):
    @staticmethod
    def make_request(display_platform: str = "linux"):
        return SimpleNamespace(
            headers={"x-face-manager-display-platform": display_platform}
        )

    @patch.object(app, "import_queue")
    @patch("backend.app.os.path.isdir", return_value=True)
    @patch("backend.app.normalize_import_folder_path", return_value="/photos")
    @patch("backend.app.to_display_path", return_value=r"D:\Photos")
    def test_create_import_queues_normalized_folder(
        self, to_display_path, normalize_import_folder_path, _, import_queue
    ):
        import_queue.enqueue.return_value = {
            "id": "job-1",
            "status": "queued",
            "folder_path": "/photos",
        }

        result = app.api_create_import(
            self.make_request("windows"),
            {"folder_path": "/photos/../photos"},
        )

        normalize_import_folder_path.assert_called_once_with("/photos/../photos")
        import_queue.enqueue.assert_called_once_with("/photos")
        to_display_path.assert_called_once_with("/photos")
        self.assertEqual(result["id"], "job-1")
        self.assertEqual(result["folder_path"], r"D:\Photos")

    @patch("backend.app.os.path.isdir", return_value=False)
    def test_create_import_rejects_missing_folder(self, _):
        with self.assertRaises(HTTPException) as raised:
            app.api_create_import(
                self.make_request(),
                {"folder_path": "/missing"},
            )

        self.assertEqual(raised.exception.status_code, 400)

    @patch.object(app, "import_queue")
    @patch("backend.app.os.path.isdir", return_value=True)
    @patch("backend.app.normalize_import_folder_path", return_value="/photos")
    @patch("backend.app.to_display_path")
    def test_linux_client_keeps_unix_paths(
        self, to_display_path, normalize_import_folder_path, _, import_queue
    ):
        import_queue.enqueue.return_value = {
            "id": "job-1",
            "status": "queued",
            "folder_path": "/photos",
        }

        result = app.api_create_import(
            self.make_request("linux"),
            {"folder_path": "/photos/../photos"},
        )

        normalize_import_folder_path.assert_called_once_with("/photos/../photos")
        import_queue.enqueue.assert_called_once_with("/photos")
        to_display_path.assert_not_called()
        self.assertEqual(result["folder_path"], "/photos")

    @patch("backend.app.pick_folder", return_value="/photos/library")
    @patch("backend.app.to_display_path", return_value=r"D:\Photos\Library")
    def test_select_folder_returns_normalized_folder(self, to_display_path, pick_folder):
        with patch("backend.app.normalize_import_folder_path", return_value="/photos/library"):
            result = app.api_select_folder(self.make_request("windows"))

        pick_folder.assert_called_once_with(prefer_windows_dialog=True)
        to_display_path.assert_called_once_with("/photos/library")
        self.assertEqual(result, {"folder_path": r"D:\Photos\Library"})

    @patch("backend.app.pick_folder", return_value=None)
    def test_select_folder_handles_cancel(self, pick_folder):
        result = app.api_select_folder(self.make_request())

        pick_folder.assert_called_once_with(prefer_windows_dialog=False)
        self.assertEqual(result, {"folder_path": None})

    @patch.object(app, "import_queue")
    def test_delete_unknown_import_returns_not_found(self, import_queue):
        import_queue.cancel_or_remove.return_value = None

        with self.assertRaises(HTTPException) as raised:
            app.api_cancel_or_remove_import("missing")

        self.assertEqual(raised.exception.status_code, 404)
