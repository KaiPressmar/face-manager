import html
import logging
import socket
import threading
import time
import traceback

import uvicorn
import webview

from backend.app import app as face_manager_app
from backend.config import APP_VERSION, get_error_log_path
from backend.error_logging import configure_error_logging, install_global_exception_hooks
from backend.services.update_manager import register_shutdown_callback

configure_error_logging()
install_global_exception_hooks()
logger = logging.getLogger("face_manager.desktop")


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


def _start_server(host: str, port: int):
    """Start the embedded backend server on one reserved localhost port."""
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
    return server, server_thread


def _build_error_page(message: str) -> str:
    """Render a small fallback HTML page with the log file location."""
    escaped_message = html.escape(message)
    escaped_log_path = html.escape(str(get_error_log_path()))
    return f"""
    <html>
      <body style="font-family: Segoe UI, sans-serif; background: #111827; color: #f3f4f6; padding: 32px;">
        <h1 style="margin-top: 0;">Face Manager could not recover automatically</h1>
        <p>The app wrote diagnostic details to:</p>
        <pre style="padding: 12px; background: #1f2937; border-radius: 8px;">{escaped_log_path}</pre>
        <p style="white-space: pre-wrap;">{escaped_message}</p>
      </body>
    </html>
    """


def main() -> None:
    """Launch the packaged desktop application."""
    logger.info("Launching Face Manager desktop %s", APP_VERSION)
    host = "127.0.0.1"
    server = None
    server_thread = None
    port = None
    last_error: Exception | None = None

    for attempt in range(2):
        port = _reserve_local_port()
        try:
            server, server_thread = _start_server(host, port)
            break
        except Exception as exc:
            last_error = exc
            logger.exception(
                "Desktop backend startup attempt %s failed on %s:%s",
                attempt + 1,
                host,
                port,
            )
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=5)
            time.sleep(0.5 * (attempt + 1))

    if server is None or server_thread is None or port is None:
        message = (
            str(last_error)
            if last_error is not None
            else "The desktop backend failed to start for an unknown reason."
        )
        logger.error("Desktop backend could not be recovered automatically")
        try:
            error_window = webview.create_window(
                title=f"Face Manager {APP_VERSION}",
                html=_build_error_page(message),
                min_size=(720, 480),
            )
            webview.start(debug=False)
            if error_window is not None:
                return
        except Exception:
            logger.exception("Could not display the desktop recovery error window")
        raise RuntimeError(message)

    window = webview.create_window(
        title=f"Face Manager {APP_VERSION}",
        url=f"http://{host}:{port}",
        min_size=(1200, 780),
    )
    register_shutdown_callback(window.destroy)
    try:
        webview.start(debug=False)
    except Exception:
        logger.exception("Desktop webview failed during startup or runtime")
        raise
    finally:
        if window is not None:
            server.should_exit = True
            server_thread.join(timeout=10)


if __name__ == "__main__":
    main()
