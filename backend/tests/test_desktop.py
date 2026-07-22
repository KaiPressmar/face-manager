import base64
import unittest
from unittest.mock import MagicMock, call, mock_open, patch

from backend.services import desktop


class OpenFileLocationTest(unittest.TestCase):
    @patch("backend.services.desktop.subprocess.run")
    @patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"})
    def test_wsl_opens_windows_explorer_and_selects_file(self, run):
        windows_path = r"D:\Photos & Familie\O'Brien $(1) 100%.jpg"
        translated = MagicMock(stdout=f"{windows_path}\n")
        revealed = MagicMock(stdout="")
        run.side_effect = [translated, revealed]

        desktop.open_file_location("/mnt/d/Photos/image.jpg")

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0],
            call(
                ["wslpath", "-w", "/mnt/d/Photos/image.jpg"],
                check=True,
                capture_output=True,
                text=True,
            ),
        )
        reveal_command = run.call_args_list[1].args[0]
        self.assertEqual(reveal_command[:4], [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"
        ])
        decoded_script = base64.b64decode(reveal_command[4]).decode("utf-16-le")
        self.assertNotIn(windows_path, decoded_script)
        path_payload = base64.b64encode(windows_path.encode("utf-8")).decode(
            "ascii"
        )
        self.assertIn(path_payload, decoded_script)
        self.assertEqual(
            run.call_args_list[1].kwargs,
            {"check": True, "capture_output": True, "text": True},
        )

    @patch("backend.services.desktop.subprocess.Popen")
    @patch("backend.services.desktop.sys.platform", "linux")
    @patch.dict("os.environ", {}, clear=True)
    @patch("builtins.open", mock_open(read_data="Linux version"))
    def test_linux_opens_containing_directory(self, popen):
        desktop.open_file_location("/home/user/photos/image.jpg")

        popen.assert_called_once_with(["xdg-open", "/home/user/photos"])

    @patch("backend.services.desktop._reveal_with_windows_shell")
    @patch("backend.services.desktop.sys.platform", "win32")
    @patch("backend.services.desktop._is_wsl", return_value=False)
    def test_windows_reveals_file_with_spaces_and_special_characters(
        self, _is_wsl, reveal
    ):
        desktop.open_file_location(
            r"C:\Users\Test\My Photos & 100%\März_[1] # Urlaub.jpg"
        )

        reveal.assert_called_once_with(
            r"C:\Users\Test\My Photos & 100%\März_[1] # Urlaub.jpg"
        )

    @patch("backend.services.desktop._reveal_with_windows_shell")
    @patch("backend.services.desktop.sys.platform", "win32")
    @patch("backend.services.desktop._is_wsl", return_value=False)
    def test_windows_preserves_unc_path(self, _is_wsl, reveal):
        desktop.open_file_location(
            r"\\server name\Family Photos\Sommer & Meer\Bild 01.jpg"
        )

        reveal.assert_called_once_with(
            r"\\server name\Family Photos\Sommer & Meer\Bild 01.jpg"
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
