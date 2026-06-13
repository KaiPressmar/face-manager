import socket
import threading
import time
import traceback

import uvicorn
import webview

from backend.app import app as face_manager_app
from backend.config import APP_VERSION


def _reserve_local_port() -> int:
    """Reserve an ephemeral localhost port for the embedded server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_server(
    host: str,
    port: int,
    server_thread: threading.Thread,
    startup_error: list[str],
    timeout_seconds: float = 30.0,
) -> None:
    """Block until the local HTTP server starts accepting connections."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if startup_error:
            raise RuntimeError(
                "Face Manager backend failed to start:\n\n" + startup_error[0]
            )
        if not server_thread.is_alive():
            raise RuntimeError(
                f"Face Manager backend stopped before listening on {host}:{port}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"Face Manager backend did not start on {host}:{port}")


def main() -> None:
    """Launch the packaged desktop application."""
    host = "127.0.0.1"
    port = _reserve_local_port()
    startup_error: list[str] = []
    server = uvicorn.Server(
        uvicorn.Config(
            face_manager_app,
            host=host,
            port=port,
            log_level="warning",
        )
    )

    def run_server() -> None:
        try:
            server.run()
        except Exception:  # pragma: no cover - GUI packaging path
            startup_error.append(traceback.format_exc())
            raise

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    _wait_for_server(host, port, server_thread, startup_error)

    window = webview.create_window(
        title=f"Face Manager {APP_VERSION}",
        url=f"http://{host}:{port}",
        min_size=(1200, 780),
    )
    try:
        webview.start(debug=False)
    finally:
        if window is not None:
            server.should_exit = True
            server_thread.join(timeout=10)


if __name__ == "__main__":
    main()
