"""Runtime lifecycle checks which do not require an Android device."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / "android/app/src/main/python/android_runtime.py"


def import_android_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fresh_runtime():
    return import_android_module(RUNTIME)


async def request_asgi(app, method, path, body=b"", authorize=True):
    headers = [(b"content-type", b"image/png")]
    if authorize:
        headers.append((b"cookie", b"fm_session=fixture-session"))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "query_string": b"", "headers": headers,
    }
    messages = []
    pending_body = True

    async def receive():
        nonlocal pending_body
        if pending_body:
            pending_body = False
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    payload = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, payload


async def verify_packaged_android_routes():
    # Only the export module is added to the desktop Python process. The
    # device's hnswlib shim needs the Android runtime and is outside this test.
    sys.modules["android_exports"] = import_android_module(RUNTIME.with_name("android_exports.py"))
    from backend.app import app
    from backend.config import get_data_root

    route = "/api/android/exports"
    png = b"\x89PNG\r\n\x1a\nexample"
    status, _ = await request_asgi(app, "POST", route, png, authorize=False)
    assert status == 403, status
    status, payload = await request_asgi(app, "POST", route, png)
    assert status == 200, (status, payload)
    token = json.loads(payload)["token"]
    staged = get_data_root() / "exports" / f"album-{token}.upload"
    assert staged.read_bytes() == png
    status, _ = await request_asgi(app, "DELETE", f"{route}/{token}")
    assert status == 204 and not staged.exists(), status
    assert await request_asgi(app, "GET", "/") == (200, b"<html>Bundled UI</html>")


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

    def test_exports_preserve_wal_and_allow_copying_previous_library(self):
        from backend import app
        from backend.db import schema

        runtime = fresh_runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            live = root / "library.sqlite"
            previous = root / "previous.sqlite"
            with sqlite3.connect(previous) as conn:
                conn.execute("CREATE TABLE backup_marker (value INTEGER)")
                conn.execute("INSERT INTO backup_marker VALUES (1)")
            with sqlite3.connect(live) as conn, \
                    patch.object(schema, "DB_PATH", str(live)), \
                    patch.object(app, "_latest_restore_backup", return_value=previous), \
                    patch("backend.config.get_data_root", return_value=root):
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("CREATE TABLE backup_marker (value INTEGER)")
                conn.execute("INSERT INTO backup_marker VALUES (2)")
                conn.commit()
                for old, expected in ((False, 2), (True, 1)):
                    snapshot = Path(runtime.export_database(old))
                    with sqlite3.connect(snapshot) as saved:
                        self.assertEqual(saved.execute("SELECT value FROM backup_marker").fetchone(), (expected,))
                    self.assertEqual(snapshot.parent, root / "exports")

    def test_album_stage_checks_type_signature_and_size_then_discards(self):
        exports = import_android_module(RUNTIME.with_name("android_exports.py"))
        payload = b"\x89PNG\r\n\x1a\nexample"

        def stage(body, content_type=b"image/png"):
            messages = iter((
                {"type": "http.request", "body": body[:3], "more_body": True},
                {"type": "http.request", "body": body[3:], "more_body": False},
            ))

            async def receive():
                return next(messages)

            return asyncio.run(exports.stage_export(Request({
                "type": "http", "headers": [(b"content-type", content_type)],
            }, receive)))

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(exports, "get_data_root", return_value=Path(folder)):
            token = stage(payload)["token"]
            path = Path(folder) / "exports" / f"album-{token}.upload"
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(exports.discard_export(token).status_code, 204)
            self.assertFalse(path.exists())
            with self.assertRaises(HTTPException) as signature_error:
                stage(b"bad photo")
            self.assertEqual(signature_error.exception.status_code, 422)
            with self.assertRaises(HTTPException) as format_error:
                stage(payload, b"text/plain")
            self.assertEqual(format_error.exception.status_code, 415)
            with patch.dict(exports._FORMATS, {"image/png": (payload[:8], 8)}), \
                    self.assertRaises(HTTPException) as limit_error:
                stage(payload)
            self.assertEqual(limit_error.exception.status_code, 413)
            self.assertEqual(list(path.parent.iterdir()), [])

    def test_packaged_frontend_routes_android_exports_before_static_mount(self):
        # Android is chosen when backend.app is imported. A fresh process
        # isolates its routes, environment and library from the rest of the suite.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.html").write_text("<html>Bundled UI</html>", encoding="utf-8")
            env = {
                **os.environ,
                "FACE_MANAGER_DATA_DIR": str(root / "library"),
                "FACE_MANAGER_FRONTEND_DIST": str(frontend),
                "FACE_MANAGER_PLATFORM": "android",
                "FACE_MANAGER_ANDROID_SESSION_TOKEN": "fixture-session",
            }
            result = subprocess.run([
                sys.executable, "-c",
                "import asyncio\n"
                "from backend.tests.test_android_runtime_lifecycle import verify_packaged_android_routes\n"
                "asyncio.run(verify_packaged_android_routes())\n",
            ], cwd=PROJECT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
