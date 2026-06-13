import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app


class SettingsApiTest(unittest.TestCase):
    @patch("backend.app.get_cluster_distance_threshold", return_value=0.42)
    def test_get_settings_returns_threshold_and_database_path(self, get_threshold):
        result = app.api_get_settings()

        get_threshold.assert_called_once_with()
        self.assertEqual(result["cluster_distance_threshold"], 0.42)
        self.assertEqual(
            result["cluster_distance_threshold_default"],
            app.DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
        )
        self.assertEqual(result["database_path"], app.DB_PATH)

    @patch("backend.app.set_cluster_distance_threshold", return_value=0.61)
    def test_update_settings_persists_threshold(self, set_threshold):
        result = app.api_update_settings({"cluster_distance_threshold": 0.61})

        set_threshold.assert_called_once_with(0.61)
        self.assertEqual(result["cluster_distance_threshold"], 0.61)

    def test_update_settings_rejects_invalid_threshold(self):
        with self.assertRaises(HTTPException) as raised:
            app.api_update_settings({"cluster_distance_threshold": 1.5})

        self.assertEqual(raised.exception.status_code, 400)

    @patch("backend.app.import_queue")
    def test_export_database_requires_idle_queue(self, import_queue):
        import_queue.snapshot.return_value = {"running_count": 1, "queued_count": 0}

        with self.assertRaises(HTTPException) as raised:
            app.api_export_database()

        self.assertEqual(raised.exception.status_code, 409)

    @patch("backend.app.validate_database_file")
    @patch("backend.app.reset_import_resources")
    @patch("backend.app.init_db")
    @patch("backend.app.shutil.move")
    @patch("backend.app.import_queue")
    @patch("backend.app.os.close")
    @patch("backend.app.tempfile.mkstemp")
    def test_import_database_replaces_file_and_refreshes_resources(
        self,
        mkstemp,
        _,
        import_queue,
        move,
        init_db,
        reset_import_resources,
        validate_database_file,
    ):
        import_queue.snapshot.return_value = {"running_count": 0, "queued_count": 0}
        mkstemp.return_value = (12, "/tmp/import.sqlite")

        with patch.object(app.Path, "write_bytes") as write_bytes, patch.object(
            app.Path, "exists", return_value=False
        ), patch.object(app.Path, "unlink"):
            result = app.api_import_database(b"sqlite")

        write_bytes.assert_called_once_with(b"sqlite")
        validate_database_file.assert_called_once()
        move.assert_called_once_with("/tmp/import.sqlite", app.DB_PATH)
        init_db.assert_called_once_with()
        reset_import_resources.assert_called_once_with()
        self.assertEqual(result, {"status": "imported"})
