import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config
from backend.services import desktop, update_manager


class AndroidPortTests(unittest.TestCase):
    def test_runtime_root_and_variant_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "FACE_MANAGER_PROJECT_ROOT": folder,
            "FACE_MANAGER_BUILD_VARIANT": "android",
        }):
            self.assertEqual(config.get_project_root(), Path(folder))
            self.assertEqual(config.get_build_variant(), "android")

    def test_android_picker_and_file_location_use_bridge(self):
        class Bridge:
            def chooseFolder(self, initial_path):
                self.initial_path = initial_path
                return "/storage/emulated/0/DCIM"

            def openFileLocation(self, path):
                self.opened = path
                return True

        bridge = Bridge()
        runtime = types.ModuleType("android_runtime")
        runtime.get_bridge = lambda: bridge
        with patch.dict(os.environ, {"FACE_MANAGER_PLATFORM": "android"}), patch.dict(sys.modules, {"android_runtime": runtime}):
            self.assertEqual(desktop.pick_folder(), "/storage/emulated/0/DCIM")
            desktop.open_file_location("/storage/emulated/0/DCIM/photo.jpg")
        self.assertEqual(bridge.initial_path, "")
        self.assertEqual(bridge.opened, "/storage/emulated/0/DCIM/photo.jpg")

    def test_android_release_asset_is_apk(self):
        asset = "FaceManager-Android-1.2.3.apk"
        self.assertEqual(update_manager._asset_name("1.2.3", "android"), asset)
        base = "https://github.com/KaiPressmar/face-manager/releases/download/v1.2.3/"
        release = {
            "tag_name": "v1.2.3",
            "html_url": "https://github.com/KaiPressmar/face-manager/releases/tag/v1.2.3",
            "assets": [
                {"name": asset, "browser_download_url": base + asset},
                {"name": asset + ".sha256", "browser_download_url": base + asset + ".sha256"},
            ],
        }
        result, candidate = update_manager.parse_latest_release(release, "1.2.2", "android")
        self.assertTrue(result["download_available"])
        self.assertEqual(candidate["installer_name"], asset)
        with patch.dict(os.environ, {"FACE_MANAGER_PLATFORM": "android"}):
            self.assertTrue(update_manager.UpdateManager.can_install())


if __name__ == "__main__":
    unittest.main()
