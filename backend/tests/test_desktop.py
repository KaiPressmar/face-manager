import unittest
from unittest.mock import mock_open, patch

from backend.services import desktop


class OpenFileLocationTest(unittest.TestCase):
    @patch("backend.services.desktop.subprocess.Popen")
    @patch("backend.services.desktop.subprocess.run")
    @patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"})
    def test_wsl_opens_windows_explorer_and_selects_file(self, run, popen):
        run.return_value.stdout = "D:\\Photos\\image.jpg\n"

        desktop.open_file_location("/mnt/d/Photos/image.jpg")

        run.assert_called_once_with(
            ["wslpath", "-w", "/mnt/d/Photos/image.jpg"],
            check=True,
            capture_output=True,
            text=True,
        )
        popen.assert_called_once_with(
            ["explorer.exe", "/select,D:\\Photos\\image.jpg"]
        )

    @patch("backend.services.desktop.subprocess.Popen")
    @patch("backend.services.desktop.sys.platform", "linux")
    @patch.dict("os.environ", {}, clear=True)
    @patch("builtins.open", mock_open(read_data="Linux version"))
    def test_linux_opens_containing_directory(self, popen):
        desktop.open_file_location("/home/user/photos/image.jpg")

        popen.assert_called_once_with(["xdg-open", "/home/user/photos"])

    @patch("backend.services.desktop.subprocess.Popen")
    @patch("backend.services.desktop.sys.platform", "win32")
    @patch("backend.services.desktop._is_wsl", return_value=False)
    def test_windows_reveals_file_with_spaces(self, _is_wsl, popen):
        desktop.open_file_location(r"C:\Users\Test\My Photos\image one.jpg")

        popen.assert_called_once_with(
            ["explorer.exe", r"/select,C:\Users\Test\My Photos\image one.jpg"]
        )

    @patch("backend.services.desktop.subprocess.Popen")
    @patch("backend.services.desktop.sys.platform", "darwin")
    @patch("backend.services.desktop._is_wsl", return_value=False)
    def test_macos_reveals_exact_file(self, _is_wsl, popen):
        desktop.open_file_location("/Users/test/My Photos/image.jpg")

        popen.assert_called_once_with(
            ["open", "-R", "/Users/test/My Photos/image.jpg"]
        )

    @patch("backend.services.desktop.subprocess.run")
    @patch("backend.services.desktop._is_wsl", return_value=True)
    def test_wsl_rejects_empty_path_translation(self, _is_wsl, run):
        run.return_value.stdout = "\n"

        with self.assertRaises(OSError):
            desktop.open_file_location("/mnt/d/missing.jpg")


class ImportFolderPathTest(unittest.TestCase):
    @patch("backend.services.desktop.sys.platform", "win32")
    def test_windows_host_keeps_windows_path(self):
        normalized = desktop.normalize_import_folder_path(
            r"D:\Photos\Library\2025"
        )

        self.assertEqual(normalized, r"D:\Photos\Library\2025")

    @patch("backend.services.desktop.sys.platform", "linux")
    def test_linux_host_translates_windows_drive_path(self):
        normalized = desktop.normalize_import_folder_path(
            r"D:\Photos\Library\2025"
        )

        self.assertEqual(normalized, "/mnt/d/Photos/Library/2025")

    @patch("backend.services.desktop.sys.platform", "linux")
    def test_linux_host_leaves_linux_path_unchanged(self):
        normalized = desktop.normalize_import_folder_path("/photos/library")

        self.assertEqual(normalized, "/photos/library")

    @patch("backend.services.desktop._is_wsl", return_value=True)
    def test_wsl_display_path_is_converted_back_to_windows(self, _):
        display_path = desktop.to_display_path("/mnt/d/Photos/Library/2025")

        self.assertEqual(display_path, r"D:\Photos\Library\2025")

    @patch("backend.services.desktop._is_wsl", return_value=False)
    def test_non_wsl_display_path_is_left_unchanged(self, _):
        display_path = desktop.to_display_path("/photos/library")

        self.assertEqual(display_path, "/photos/library")

    @patch("backend.services.desktop.subprocess.run")
    @patch("backend.services.desktop._is_wsl", return_value=True)
    def test_wsl_folder_picker_uses_windows_dialog(self, _, run):
        run.return_value.stdout = "D:\\Photos\\Library\r\n"

        selected = desktop.pick_folder(prefer_windows_dialog=True)

        self.assertEqual(selected, r"D:\Photos\Library")
        run.assert_called_once()
