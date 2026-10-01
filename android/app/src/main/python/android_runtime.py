"""Run the complete Face Manager backend inside the Android application.

Java calls start on a background thread. The bundled React app then uses the
loopback API with a per-process session cookie; no LAN listener is opened.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

_lock = threading.RLock()
_bridge = None
_server = None
_thread = None
_port = None
_token = None


def get_bridge():
    if _bridge is None:
        raise RuntimeError("Android bridge is not initialized")
    return _bridge


def _extract_assets(context, root: Path) -> None:
    from java import jarray, jbyte, jclass

    assets = context.getAssets()
    FileOutputStream = jclass("java.io.FileOutputStream")

    def visit(relative: str) -> None:
        asset_path = "runtime" + ("/" + relative if relative else "")
        children = list(assets.list(asset_path))
        if children:
            for child in children:
                visit(relative + "/" + child if relative else child)
            return
        if not relative:
            raise RuntimeError("Bundled runtime assets are missing")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        stream = assets.open(asset_path)
        output = FileOutputStream(str(temporary))
        try:
            buffer = jarray(jbyte)(65536)
            while True:
                count = stream.read(buffer)
                if count == -1:
                    break
                output.write(buffer, 0, count)
            output.getFD().sync()
            output.close()
            os.replace(temporary, target)
        finally:
            stream.close()
            output.close()
            temporary.unlink(missing_ok=True)

    visit("")


def start(context, bridge, data_root=None):
    """Start Uvicorn and return its bound loopback port and session token."""
    global _bridge, _server, _thread, _port, _token
    with _lock:
        if _thread is not None and _thread.is_alive():
            if _server is None or _server.should_exit:
                raise RuntimeError("Previous embedded backend is still stopping")
            if _port is None or _token is None:
                raise RuntimeError("Previous embedded backend is still starting")
            return {"port": _port, "token": _token}
        _server = _thread = _port = _token = None
        _bridge = None

        files = Path(str(context.getFilesDir().getAbsolutePath()))
        runtime_root = files / "runtime"
        data = Path(str(data_root)) if data_root is not None else files / "backend"
        data.mkdir(parents=True, exist_ok=True)
        _extract_assets(context, runtime_root)
        if not (runtime_root / "VERSION").is_file() or not (runtime_root / "frontend" / "index.html").is_file():
            raise RuntimeError("Android runtime assets are incomplete")

        os.environ["FACE_MANAGER_PROJECT_ROOT"] = str(runtime_root)
        os.environ["FACE_MANAGER_DATA_DIR"] = str(data)
        os.environ["FACE_MANAGER_FRONTEND_DIST"] = str(runtime_root / "frontend")
        os.environ["FACE_MANAGER_BUILD_VARIANT"] = "android"
        os.environ["FACE_MANAGER_PLATFORM"] = "android"
        os.environ["FACE_MANAGER_ANDROID"] = "1"
        token = secrets.token_urlsafe(32)
        os.environ["FACE_MANAGER_ANDROID_SESSION_TOKEN"] = token
        _bridge = bridge

        # Native model setup precedes imports of backend.app, which imports its
        # model and constructs persistent queues at module import time.
        try:
            from java import jclass
            jclass("de.face_manager.app.FaceEngine").initialize(context)
            from backend.app import app
            import uvicorn

            config = uvicorn.Config(app, host="127.0.0.1", port=0, loop="asyncio",
                                    http="h11", lifespan="on", log_level="warning",
                                    timeout_graceful_shutdown=10)
            server = uvicorn.Server(config)
        except Exception:
            _bridge = None
            os.environ.pop("FACE_MANAGER_ANDROID_SESSION_TOKEN", None)
            raise
        errors = []

        def run_server():
            try:
                server.run()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run_server, name="face-manager-api", daemon=True)
        _server, _thread, _token = server, thread, token
        try:
            thread.start()
        except Exception:
            _server = _thread = _bridge = _port = _token = None
            os.environ.pop("FACE_MANAGER_ANDROID_SESSION_TOKEN", None)
            raise
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if errors or not thread.is_alive():
                break
            if server.started and server.servers:
                sockets = server.servers[0].sockets
                if sockets:
                    _port = sockets[0].getsockname()[1]
                    return {"port": _port, "token": token}
            time.sleep(0.05)

        server.should_exit = True
        thread.join(timeout=5)
        if not thread.is_alive():
            _server = _thread = _bridge = _port = _token = None
            os.environ.pop("FACE_MANAGER_ANDROID_SESSION_TOKEN", None)
        if errors:
            raise RuntimeError("Embedded backend failed to start") from errors[0]
        raise RuntimeError("Embedded backend did not become ready")


def stop():
    """Drain the backend lifespan before returning to Java or test code."""
    global _bridge, _server, _thread, _port, _token
    with _lock:
        if _server is not None:
            _server.should_exit = True
        if _thread is not None:
            _thread.join(timeout=30)
            if _thread.is_alive():
                raise TimeoutError("Embedded backend did not stop")
        _server = _thread = _bridge = _port = _token = None
        os.environ.pop("FACE_MANAGER_ANDROID_SESSION_TOKEN", None)


def export_database():
    """Return a consistent private temporary SQLite snapshot for SAF export."""
    from backend.config import DB_PATH, get_data_root

    folder = get_data_root() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="face-manager-", suffix=".sqlite", dir=folder)
    os.close(fd)
    try:
        with sqlite3.connect(DB_PATH) as source, sqlite3.connect(name) as target:
            source.backup(target)
        return name
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
