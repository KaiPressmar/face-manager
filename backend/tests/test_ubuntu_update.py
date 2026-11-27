import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.update_manager import UpdateError, UpdateManager
from backend.services.ubuntu_update import (
    _INSTALL_SCRIPT,
    launch_ubuntu_installer,
    terminal_command,
)


@unittest.skipUnless(sys.platform == "linux", "Ubuntu terminal handoff")
class UbuntuUpdateTest(unittest.TestCase):
    def test_terminal_selection_requires_system_tools(self):
        with patch("backend.services.ubuntu_update.os.access", side_effect=lambda path, _: path != "/usr/bin/ptyxis"):
            self.assertEqual(terminal_command(), ("/usr/bin/gnome-terminal", "--"))
        with patch("backend.services.ubuntu_update.os.access", side_effect=lambda path, _: path != "/usr/bin/sudo"):
            self.assertIsNone(terminal_command())

    def test_handoff_uses_literal_arguments_and_waits_for_terminal(self):
        package = Path("/tmp/Fotos & Bilder/face-manager_1.2.0_amd64.deb")

        def terminal(args, **options):
            self.assertEqual(args[:3], ["/usr/bin/ptyxis", "--new-window", "--"])
            self.assertEqual(args[-4:-1], [str(os.getpid()), str(package), "expected-checksum"])
            self.assertNotIn("LD_LIBRARY_PATH", options["env"])
            Path(args[-1]).touch()

        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/opt/face-manager/vendor"}), patch(
            "backend.services.ubuntu_update.subprocess.Popen", side_effect=terminal
        ):
            launch_ubuntu_installer(("/usr/bin/ptyxis", "--new-window", "--"), package, "expected-checksum")

    def test_failed_terminal_launch_raises_before_app_shutdown(self):
        with patch("backend.services.ubuntu_update.subprocess.Popen") as terminal:
            terminal.return_value.poll.return_value = 1

            with self.assertRaisesRegex(OSError, "Terminal"):
                launch_ubuntu_installer(("/usr/bin/ptyxis", "--new-window", "--"), Path("/tmp/example.deb"), "checksum")

    def test_packaged_desktop_hands_off_only_once(self):
        command = ("/usr/bin/ptyxis", "--new-window", "--")
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "face-manager_1.2.0_amd64.deb"
            package.write_bytes(b"verified")
            checksum = hashlib.sha256(b"verified").hexdigest()
            manager = UpdateManager()
            manager._cached_result = {"update_available": True}
            manager._candidate = {
                "version": "1.2.0",
                "installer_name": package.name,
                "installer_digest": f"sha256:{checksum}",
            }
            manager._set_download_ready(manager._candidate, package, 8, checksum)

            with patch.dict(os.environ, {"FACE_MANAGER_PLATFORM": ""}), patch(
                "backend.services.update_manager.get_project_root", return_value=Path("/opt/face-manager")
            ), patch(
                "backend.services.update_manager.get_build_variant", return_value="linux"
            ), patch(
                "backend.services.update_manager.terminal_command", return_value=command
            ), patch("backend.services.update_manager.launch_ubuntu_installer") as handoff:
                with patch("backend.services.update_manager._shutdown_callback", None):
                    self.assertFalse(manager.can_install())
                with patch("backend.services.update_manager._shutdown_callback"):
                    self.assertTrue(manager.can_install())
                    manager.launch_downloaded_installer("1.2.0")
                    with self.assertRaisesRegex(UpdateError, "bereits gestartet"):
                        manager.launch_downloaded_installer("1.2.0")
                handoff.assert_called_once_with(command, package, checksum)

    def test_terminal_waits_for_app_exit_then_refuses_corrupt_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "example package.deb"
            package.write_bytes(b"changed")
            ready = Path(directory) / "ready"

            with subprocess.Popen(["/usr/bin/sleep", "10"]) as application:
                with subprocess.Popen(
                    [
                        "/bin/sh", "-c", _INSTALL_SCRIPT, "face-manager-update",
                        str(application.pid), str(package), "bad", str(ready),
                    ],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                ) as terminal:
                    try:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            terminal.wait(timeout=0.2)
                    finally:
                        application.terminate()
                        application.wait(timeout=5)
                    stdout, _ = terminal.communicate(input="\n", timeout=5)

            self.assertTrue(ready.is_file())
            self.assertEqual(terminal.returncode, 1)
            self.assertIn("nicht verifiziert", stdout)


if __name__ == "__main__":
    unittest.main()
