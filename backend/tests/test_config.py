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
