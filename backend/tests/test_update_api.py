import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app


class UpdateApiTest(unittest.TestCase):
    @patch("backend.app.update_manager.check")
    @patch("backend.app.get_automatic_update_checks", return_value=False)
    def test_disabled_automatic_check_does_not_contact_github(self, _enabled, check):
        result = app.api_check_updates(force=False)

        check.assert_not_called()
        self.assertFalse(result["enabled"])
        self.assertFalse(result["update_available"])
        self.assertEqual(result["check_interval_seconds"], 3600)

    @patch("backend.app.update_manager.can_install", return_value=True)
    @patch("backend.app.get_skipped_update_version", return_value="1.2.0")
    @patch("backend.app.get_build_variant", return_value="gpu")
    @patch("backend.app.get_automatic_update_checks", return_value=True)
    @patch("backend.app.update_manager.check")
    def test_check_marks_skipped_release_and_build_variant(
        self, check, _enabled, _variant, _skipped, _can_install
    ):
        check.return_value = {
            "current_version": "1.1.0",
            "latest_version": "1.2.0",
            "update_available": True,
        }

        result = app.api_check_updates(force=False)

        self.assertTrue(result["skipped"])
        self.assertTrue(result["can_install"])
        self.assertEqual(result["check_interval_seconds"], 3600)
        check.assert_called_once_with(app.APP_VERSION, "gpu", force=False)

    @patch("backend.app.set_skipped_update_version")
    @patch("backend.app.update_manager.get_cached_release")
    @patch("backend.app.APP_VERSION", "1.1.0")
    def test_skip_persists_only_a_cached_newer_release(self, cached, persist):
        result = app.api_skip_update({"version": "1.2.0"})

        cached.assert_called_once_with("1.2.0")
        persist.assert_called_once_with("1.2.0")
        self.assertTrue(result["skipped"])

    @patch("backend.app._describe_background_activity", return_value="Import läuft")
    def test_install_is_blocked_during_background_work(self, _activity):
        with self.assertRaises(HTTPException) as raised:
            app.api_install_update({"version": "1.2.0"})

        self.assertEqual(raised.exception.status_code, 409)

    @patch("backend.app.schedule_process_exit")
    @patch("backend.app.update_manager.launch_downloaded_installer")
    @patch("backend.app._describe_background_activity", return_value=None)
    def test_verified_installer_launch_schedules_app_exit(
        self, _activity, launch, schedule_exit
    ):
        result = app.api_install_update({"version": "1.2.0"})

        launch.assert_called_once_with("1.2.0")
        schedule_exit.assert_called_once_with()
        self.assertTrue(result["installing"])


if __name__ == "__main__":
    unittest.main()
