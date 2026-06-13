import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app


class ImportApiTest(unittest.TestCase):
    @patch.object(app, "import_queue")
    @patch("backend.app.os.path.isdir", return_value=True)
    def test_create_import_queues_normalized_folder(self, _, import_queue):
        import_queue.enqueue.return_value = {"id": "job-1", "status": "queued"}

        result = app.api_create_import({"folder_path": "/photos/../photos"})

        import_queue.enqueue.assert_called_once_with("/photos")
        self.assertEqual(result["id"], "job-1")

    @patch("backend.app.os.path.isdir", return_value=False)
    def test_create_import_rejects_missing_folder(self, _):
        with self.assertRaises(HTTPException) as raised:
            app.api_create_import({"folder_path": "/missing"})

        self.assertEqual(raised.exception.status_code, 400)

    @patch.object(app, "import_queue")
    def test_delete_unknown_import_returns_not_found(self, import_queue):
        import_queue.cancel_or_remove.return_value = None

        with self.assertRaises(HTTPException) as raised:
            app.api_cancel_or_remove_import("missing")

        self.assertEqual(raised.exception.status_code, 404)
