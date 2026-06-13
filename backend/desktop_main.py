import socket
import threading
import time

import uvicorn
import webview

from backend.config import APP_VERSION


def _reserve_local_port() -> int:
    """Reserve an ephemeral localhost port for the embedded server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout_seconds: float = 30.0) -> None:
    """Block until the local HTTP server starts accepting connections."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
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
    server = uvicorn.Server(
        uvicorn.Config(
            "backend.app:app",
            host=host,
            port=port,
            log_level="warning",
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    _wait_for_server(host, port)

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
