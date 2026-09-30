import os
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config


class DataPathTest(unittest.TestCase):
    def test_dev_data_root_stays_in_repo(self):
        with patch.object(config.sys, "frozen", False, create=True):
            data_root = config.get_data_root()

        self.assertEqual(
            data_root, config.get_project_root() / "backend" / "db"
        )

    def test_override_data_root_is_respected(self):
        with patch.dict(os.environ, {"FACE_MANAGER_DATA_DIR": "/tmp/face-manager-test"}):
            data_root = config.get_data_root()

        self.assertEqual(data_root, Path("/tmp/face-manager-test"))

    def test_frozen_windows_uses_local_app_data(self):
        with patch.object(config.sys, "frozen", True, create=True), patch.object(
            config.sys, "platform", "win32"
        ), patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"}):
            data_root = config.get_data_root()

        self.assertEqual(
            str(data_root).replace("\\", "/"),
            "C:/Users/Test/AppData/Local/FaceManager",
        )

    def test_error_log_path_lives_inside_log_dir(self):
        with patch.dict(os.environ, {"FACE_MANAGER_DATA_DIR": "/tmp/face-manager-test"}):
            self.assertEqual(
                config.get_log_dir(),
                Path("/tmp/face-manager-test/logs"),
            )
            self.assertEqual(
                config.get_error_log_path(),
                Path("/tmp/face-manager-test/logs/error.log"),
            )

    def test_changelog_lives_in_project_root(self):
        self.assertEqual(
            config.get_changelog_path(),
            config.get_project_root() / "CHANGELOG.md",
        )

    def test_build_variant_can_be_overridden_for_development(self):
        with patch.dict(os.environ, {"FACE_MANAGER_BUILD_VARIANT": "gpu"}):
            self.assertEqual(config.get_build_variant(), "gpu")

    def test_unknown_build_variant_falls_back_to_cpu(self):
        with patch.dict(os.environ, {"FACE_MANAGER_BUILD_VARIANT": "unknown"}):
            self.assertEqual(config.get_build_variant(), "cpu")
