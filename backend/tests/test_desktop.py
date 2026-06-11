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
