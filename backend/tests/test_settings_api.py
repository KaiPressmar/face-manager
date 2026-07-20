import unittest
from unittest.mock import patch

from fastapi import HTTPException
from types import SimpleNamespace

from backend import app
from backend.services.cache import app_cache


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        app_cache.clear()

    @patch("backend.app.get_error_log_path", return_value="/tmp/face-manager/logs/error.log")
    @patch("backend.app.get_file_log_level", return_value="WARNING")
    @patch("backend.app.get_filename_person_block_separator", return_value=" - ")
    @patch("backend.app.get_filename_person_joiner", return_value=", ")
    @patch("backend.app.get_cluster_distance_threshold", return_value=0.42)
    def test_get_settings_returns_threshold_and_database_path(
        self,
        get_threshold,
        get_joiner,
        get_block_separator,
        get_file_log_level,
        get_error_log_path,
    ):
        result = app.api_get_settings()

        get_threshold.assert_called_once_with()
        get_joiner.assert_called_once_with()
        get_block_separator.assert_called_once_with()
        get_file_log_level.assert_called_once_with()
        get_error_log_path.assert_called_once_with()
        self.assertEqual(result["cluster_distance_threshold"], 0.42)
        self.assertEqual(
            result["cluster_distance_threshold_default"],
            app.DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
        )
        self.assertEqual(result["filename_person_suffix_format"], "DATEI - Name 1, Name 2.jpg")
        self.assertEqual(
            result["filename_person_joiner"],
            ", ",
        )
        self.assertEqual(result["filename_person_block_separator"], " - ")
        self.assertEqual(result["file_log_level"], "WARNING")
        self.assertEqual(result["file_log_level_default"], app.DEFAULT_FILE_LOG_LEVEL)
        self.assertEqual(result["database_path"], app.DB_PATH)
        self.assertEqual(result["error_log_path"], "/tmp/face-manager/logs/error.log")

    @patch("backend.app.schedule_full_recluster")
    @patch("backend.app.set_cluster_distance_threshold", return_value=0.61)
    def test_update_settings_persists_threshold_without_scheduling_recluster(
        self,
        set_threshold,
        schedule_full_recluster,
    ):
        result = app.api_update_settings({"cluster_distance_threshold": 0.61})

        set_threshold.assert_called_once_with(0.61)
        schedule_full_recluster.assert_not_called()
        self.assertEqual(result["cluster_distance_threshold"], 0.61)

    @patch("backend.app.schedule_full_recluster")
    @patch("backend.app.set_cluster_distance_threshold", return_value=0.50)
    def test_update_settings_skips_recluster_when_threshold_is_unchanged(
        self,
        set_threshold,
        schedule_full_recluster,
    ):
        with patch("backend.app.get_cluster_distance_threshold", return_value=0.5):
            result = app.api_update_settings({"cluster_distance_threshold": 0.5})

        set_threshold.assert_called_once_with(0.5)
        schedule_full_recluster.assert_not_called()
        self.assertEqual(result["cluster_distance_threshold"], 0.5)

    @patch("backend.app.apply_persisted_file_log_level", return_value="DEBUG")
    @patch("backend.app.set_file_log_level", return_value="DEBUG")
    def test_update_settings_persists_file_log_level(
        self,
        set_file_log_level,
        apply_persisted_file_log_level,
    ):
        result = app.api_update_settings({"file_log_level": "debug"})

        set_file_log_level.assert_called_once_with("debug")
        apply_persisted_file_log_level.assert_called_once_with()
        self.assertEqual(result["file_log_level"], "DEBUG")

    @patch("backend.app.set_automatic_update_checks", return_value=False)
    @patch("backend.app.get_automatic_update_checks", return_value=True)
    def test_update_settings_persists_automatic_update_opt_out(
        self, _get_automatic_update_checks, set_automatic_update_checks
    ):
        result = app.api_update_settings({"automatic_update_checks": False})

        set_automatic_update_checks.assert_called_once_with(False)
        self.assertFalse(result["automatic_update_checks"])

    @patch("backend.app.set_filename_person_block_separator", return_value=" - ")
    @patch("backend.app.set_filename_person_joiner", return_value=" / ")
    @patch("backend.app.get_filename_person_block_separator", return_value=" ")
    @patch("backend.app.get_filename_person_joiner", return_value=", ")
    @patch("backend.app.get_cluster_distance_threshold", return_value=0.5)
    def test_update_settings_persists_filename_person_rules(
        self,
        get_threshold,
        get_joiner,
        get_block_separator,
        set_joiner,
        set_block_separator,
    ):
        result = app.api_update_settings(
            {
                "filename_person_block_separator": " - ",
                "filename_person_joiner": " / ",
            }
        )

        get_threshold.assert_called_once_with()
        get_joiner.assert_called_once_with()
        get_block_separator.assert_called_once_with()
        set_joiner.assert_called_once_with(" / ")
        set_block_separator.assert_called_once_with(" - ")
        self.assertEqual(result["filename_person_joiner"], " / ")
        self.assertEqual(result["filename_person_block_separator"], " - ")

    @patch("backend.app.get_ui_theme", return_value="dark")
    def test_get_settings_includes_ui_theme(self, get_ui_theme):
        result = app.api_get_settings()

        get_ui_theme.assert_called_once_with()
        self.assertEqual(result["ui_theme"], "dark")
        self.assertEqual(result["ui_theme_default"], app.DEFAULT_UI_THEME)

    @patch("backend.app.set_ui_theme", return_value="light")
    def test_update_settings_persists_ui_theme(self, set_ui_theme):
        result = app.api_update_settings({"ui_theme": "light"})

        set_ui_theme.assert_called_once_with("light")
        self.assertEqual(result["ui_theme"], "light")
        self.assertEqual(result["ui_theme_default"], app.DEFAULT_UI_THEME)

    @patch("backend.app.set_ui_theme", side_effect=ValueError("Invalid UI theme"))
    def test_update_settings_rejects_invalid_ui_theme(self, set_ui_theme):
        with self.assertRaises(HTTPException) as raised:
            app.api_update_settings({"ui_theme": "rainbow"})

        self.assertEqual(raised.exception.status_code, 400)

    def test_update_settings_rejects_invalid_threshold(self):
        with self.assertRaises(HTTPException) as raised:
            app.api_update_settings({"cluster_distance_threshold": 1.5})

        self.assertEqual(raised.exception.status_code, 400)

    def test_update_settings_requires_mutable_payload(self):
        with self.assertRaises(HTTPException) as raised:
            app.api_update_settings({})

        self.assertEqual(raised.exception.status_code, 400)

    @patch("backend.app.schedule_full_recluster")
    @patch("backend.app.auto_tune_cluster_distance_threshold")
    def test_auto_tune_threshold_does_not_schedule_recluster(
        self,
        auto_tune,
        schedule_full_recluster,
    ):
        auto_tune.return_value = {
            "threshold": 0.37,
            "sample_size": 12,
            "person_count": 3,
            "same_person_accuracy": 0.9,
            "different_person_accuracy": 0.95,
            "balanced_accuracy": 0.925,
        }

        result = app.api_auto_tune_cluster_threshold()

        self.assertEqual(result["threshold"], 0.37)
        schedule_full_recluster.assert_not_called()

    @patch(
        "backend.app.auto_tune_cluster_distance_threshold",
        side_effect=ValueError("Assign more people."),
    )
    def test_auto_tune_threshold_reports_insufficient_training_data(self, auto_tune):
        with self.assertRaises(HTTPException) as raised:
            app.api_auto_tune_cluster_threshold()

        self.assertEqual(raised.exception.status_code, 422)

    @patch("backend.app.list_available_image_persons", return_value=["Kai"])
    @patch("backend.app.count_filename_rename_candidates", return_value=1)
    @patch("backend.app.list_filename_rename_candidates")
    @patch("backend.app.normalize_import_folder_path", return_value="/photos")
    def test_get_image_rename_candidates_returns_payload(
        self,
        normalize_import_folder_path,
        list_filename_rename_candidates,
        count_filename_rename_candidates,
        list_available_image_persons,
    ):
        request = SimpleNamespace(headers={"x-face-manager-display-platform": "linux"})
        list_filename_rename_candidates.return_value = (
            [
                {
                    "location_id": 1,
                    "image_id": 2,
                    "path": "/photos/a.jpg",
                    "directory": "/photos",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "current_filename": "a.jpg",
                    "proposed_filename": "a Kai.jpg",
                    "proposed_path": "/photos/a Kai.jpg",
                    "detected_person_names": ["Kai"],
                    "current_suffix_person_names": [],
                }
            ],
            1,
        )

        result = app.api_get_image_rename_candidates(
            request,
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
            limit=100,
            offset=0,
        )

        normalize_import_folder_path.assert_called_once_with("/photos")
        list_filename_rename_candidates.assert_called_once_with(
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
            limit=100,
            offset=0,
        )
        count_filename_rename_candidates.assert_called_once_with(
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
        )
        list_available_image_persons.assert_called_once_with(["/photos"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["path"], "/photos/a.jpg")

    @patch("backend.app.list_available_image_persons", return_value=["Kai"])
    @patch("backend.app.count_filename_rename_candidates")
    @patch("backend.app.list_filename_rename_candidates")
    @patch("backend.app.normalize_import_folder_path", return_value="/photos")
    def test_get_image_rename_candidates_can_skip_total_count(
        self,
        normalize_import_folder_path,
        list_filename_rename_candidates,
        count_filename_rename_candidates,
        list_available_image_persons,
    ):
        request = SimpleNamespace(headers={"x-face-manager-display-platform": "linux"})
        list_filename_rename_candidates.return_value = ([], 0)

        result = app.api_get_image_rename_candidates(
            request,
            folders=["/photos"],
            persons=[],
            sort_by="date",
            sort_direction="desc",
            limit=25,
            offset=0,
            include_total=False,
        )

        normalize_import_folder_path.assert_called_once_with("/photos")
        list_filename_rename_candidates.assert_called_once_with(
            folders=["/photos"],
            persons=[],
            sort_by="date",
            sort_direction="desc",
            limit=25,
            offset=0,
        )
        count_filename_rename_candidates.assert_not_called()
        list_available_image_persons.assert_called_once_with(["/photos"])
        self.assertIsNone(result["total"])

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
