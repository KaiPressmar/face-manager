"""Runtime lifecycle checks which do not require an Android device."""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[2] / "android/app/src/main/python/android_runtime.py"


def fresh_runtime():
    spec = importlib.util.spec_from_file_location("android_runtime_lifecycle_test", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DummyThread:
    def __init__(self, alive=True):
        self.alive = alive
        self.joined = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined = True


class AndroidRuntimeLifecycleTests(unittest.TestCase):
    def test_start_rejects_terminating_server(self):
        runtime = fresh_runtime()
        runtime._server = type("Server", (), {"should_exit": True})()
        runtime._thread = DummyThread()
        runtime._port, runtime._token = 12345, "old"
        with self.assertRaisesRegex(RuntimeError, "still stopping"):
            runtime.start(None, None)

    def test_stop_timeout_preserves_state_for_safe_retry(self):
        runtime = fresh_runtime()
        runtime._server = type("Server", (), {"should_exit": False})()
        runtime._thread = DummyThread()
        runtime._port, runtime._token = 12345, "old"
        with self.assertRaisesRegex(TimeoutError, "did not stop"):
            runtime.stop()
        self.assertTrue(runtime._server.should_exit)
        self.assertEqual(runtime._token, "old")
        with self.assertRaises(RuntimeError):
            runtime.start(None, None)

    def test_startup_timeout_keeps_live_thread_registered(self):
        runtime = fresh_runtime()
        server = types.SimpleNamespace(started=False, servers=[], should_exit=False)
        bridge = object()

        class Thread(DummyThread):
            def __init__(self, **kwargs):
                super().__init__(alive=True)

            def start(self):
                pass

        class Files:
            def __init__(self, path):
                self.path = path

            def getAbsolutePath(self):
                return self.path

        class Context:
            def __init__(self, path):
                self.path = path

            def getFilesDir(self):
                return Files(self.path)

        def assets(_, root):
            (root / "frontend").mkdir(parents=True)
            (root / "VERSION").write_text("0.0.0")
            (root / "frontend/index.html").write_text("<html></html>")

        java = types.ModuleType("java")
        java.jclass = lambda name: types.SimpleNamespace(initialize=lambda context: None)
        backend_app = types.ModuleType("backend.app")
        backend_app.app = object()
        uvicorn = types.ModuleType("uvicorn")
        uvicorn.Config = lambda *args, **kwargs: object()
        uvicorn.Server = lambda config: server

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}), \
                patch.dict(sys.modules, {"java": java, "backend.app": backend_app, "uvicorn": uvicorn}), \
                patch.object(runtime, "_extract_assets", assets), \
                patch.object(runtime.threading, "Thread", Thread), \
                patch.object(runtime.time, "monotonic", side_effect=[0, 31]), \
                patch.object(runtime.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                runtime.start(Context(folder), bridge)
            self.assertIs(runtime._server, server)
            self.assertTrue(runtime._thread.is_alive())
            self.assertIs(runtime._bridge, bridge)
            self.assertTrue(server.should_exit)
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                runtime.start(None, None)


if __name__ == "__main__":
    unittest.main()
