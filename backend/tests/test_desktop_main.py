import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import app  # Load shared dependencies before substituting native WebView.


def load_desktop():
    source = Path(__file__).resolve().parents[1] / "desktop_main.py"
    spec = importlib.util.spec_from_file_location("face_manager_desktop_test", source)
    module = importlib.util.module_from_spec(spec)
    webview = SimpleNamespace(create_window=Mock(), start=Mock(), SAVE_DIALOG=30)
    with patch.dict(sys.modules, {"webview": webview}):
        spec.loader.exec_module(module)
    return module


class DesktopStartupTests(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop()

    def test_wait_requires_uvicorn_readiness(self):
        server = SimpleNamespace(started=False)
        thread = Mock()
        with (
            patch.object(self.desktop.time, "monotonic", side_effect=[0, 0, 30]),
            patch.object(self.desktop.time, "sleep"),
        ):
            with self.assertRaises(TimeoutError):
                self.desktop._wait_for_server(server, thread, [])

    def test_failed_attempt_requests_shutdown_before_returning(self):
        wait_error = TimeoutError("Startup took too long")
        with (
            patch.object(self.desktop.socket, "socket") as socket_factory,
            patch.object(self.desktop.uvicorn, "Server") as server_factory,
            patch.object(self.desktop.threading, "Thread") as thread_factory,
            patch.object(self.desktop, "_wait_for_server", side_effect=wait_error),
        ):
            socket_factory.return_value.getsockname.return_value = ("127.0.0.1", 12345)
            thread_factory.return_value.is_alive.return_value = False
            with self.assertRaisesRegex(TimeoutError, "Startup took too long"):
                self.desktop._start_server("127.0.0.1")

        self.assertTrue(server_factory.return_value.should_exit)
        thread_factory.return_value.join.assert_called_once_with(timeout=5)

    def test_main_skips_retry_if_previous_server_is_still_stopping(self):
        error = self.desktop.BackendShutdownTimeout("Backend is still stopping")
        with patch.object(self.desktop, "_start_server", side_effect=error) as start:
            self.desktop.main()

        start.assert_called_once_with("127.0.0.1")
        self.assertIn("Backend is still stopping", self.desktop.webview.create_window.call_args.kwargs["html"])

    def test_backend_import_failure_shows_recovery_window(self):
        with (
            patch.object(self.desktop, "_load_backend_app", side_effect=ImportError("Backend import failed")) as load,
            patch.object(self.desktop.time, "sleep"),
        ):
            self.desktop.main()

        self.assertEqual(load.call_count, 2)
        self.assertIn("Backend import failed", self.desktop.webview.create_window.call_args.kwargs["html"])

    def test_log_setup_failure_shows_recovery_window(self):
        with (
            patch.object(self.desktop, "configure_error_logging", side_effect=PermissionError("Log path unavailable")),
            patch.object(self.desktop, "_start_server") as start,
            patch.object(self.desktop.time, "sleep"),
        ):
            self.desktop.main()

        start.assert_not_called()
        self.assertIn("Log path unavailable", self.desktop.webview.create_window.call_args.kwargs["html"])

    def test_window_creation_failure_stops_the_started_backend(self):
        server = SimpleNamespace(should_exit=False)
        thread = Mock()
        self.desktop.webview.create_window.side_effect = RuntimeError("WebView unavailable")
        with patch.object(self.desktop, "_start_server", return_value=(server, thread, 12345)):
            with self.assertRaisesRegex(RuntimeError, "WebView unavailable"):
                self.desktop.main()

        self.assertTrue(server.should_exit)
        thread.join.assert_called_once_with(timeout=10)

    def test_native_window_can_reach_narrow_layout_and_stops_picker_before_server(self):
        server = SimpleNamespace(should_exit=False)
        thread = Mock()

        def stop_picker():
            self.assertFalse(server.should_exit)

        with (
            patch.object(self.desktop, "_start_server", return_value=(server, thread, 12345)),
            patch.object(self.desktop, "stop_native_folder_picker", side_effect=stop_picker) as stop,
        ):
            self.desktop.main()

        self.assertEqual(self.desktop.webview.create_window.call_args.kwargs["min_size"], (320, 400))
        stop.assert_called_once_with()
        self.assertTrue(server.should_exit)
        thread.join.assert_called_once_with(timeout=10)

    def test_export_bridge_only_saves_from_the_app_and_handles_native_picker_results(self):
        server = SimpleNamespace(should_exit=False)
        window = self.desktop.webview.create_window.return_value
        with patch.object(self.desktop, "_start_server", return_value=(server, Mock(), 12345)):
            self.desktop.main()
        save_album_png = window.expose.call_args.args[0]
        self.assertEqual(save_album_png.__name__, "save_album_png")

        with (tempfile.TemporaryDirectory() as folder,
              patch("backend.services.album_export._decode_album_png", return_value=b"png bytes")):
            for wrap in (lambda path: path, lambda path: (path,)):
                destination = Path(folder) / "target.png"
                window.create_file_dialog.return_value = wrap(str(destination))
                window.get_current_url.return_value = "http://external.invalid/"
                self.assertEqual(save_album_png("Album.png", "payload")["status"], "error")
                self.assertFalse(destination.exists())
                window.create_file_dialog.assert_not_called()

                window.get_current_url.return_value = "http://127.0.0.1:12345/#/weltkarte"
                self.assertEqual(save_album_png("Album.png", "payload"), {"status": "saved"})
                window.create_file_dialog.assert_called_once_with(
                    self.desktop.webview.SAVE_DIALOG, save_filename="Album.png", file_types=("PNG (*.png)",),
                )
                self.assertEqual(destination.read_bytes(), b"png bytes")
                destination.unlink()
                window.create_file_dialog.reset_mock()
