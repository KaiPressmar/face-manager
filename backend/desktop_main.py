import html
import logging
import socket
import threading
import time
import traceback

import uvicorn
import webview

from backend.config import APP_VERSION, get_error_log_path
from backend.error_logging import configure_error_logging, install_global_exception_hooks
from backend.services.update_manager import register_shutdown_callback

logger = logging.getLogger("face_manager.desktop")


class BackendShutdownTimeout(RuntimeError):
    """The previous startup attempt is still draining."""


def _wait_for_server(
    server: uvicorn.Server,
    server_thread: threading.Thread,
    startup_error: list[str],
    timeout_seconds: float = 30.0,
) -> None:
    """Block until the local HTTP server starts accepting connections."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if startup_error:
            raise RuntimeError(
                "Face Manager backend failed to start:\n\n" + startup_error[0]
            )
        if not server_thread.is_alive():
            raise RuntimeError("Face Manager backend stopped before it could listen")
        if server.started:
            return
        time.sleep(0.1)
    raise TimeoutError("Face Manager backend did not start within 30 seconds")


def _load_backend_app():
    # Import initializes the database and native dependencies. Let failures
    # reach the same recovery window as errors raised during server startup.
    from backend.app import app

    return app


def _start_server(host: str) -> tuple[uvicorn.Server, threading.Thread, int]:
    """Start the backend and retain the bound port through handoff to Uvicorn."""
    application = _load_backend_app()
    startup_error: list[str] = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def run_server() -> None:
        try:
            server.run(sockets=[listener])
        except (Exception, SystemExit):
            startup_error.append(traceback.format_exc())
            logger.exception("Desktop backend terminated unexpectedly")
        finally:
            listener.close()

    try:
        listener.bind((host, 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            application, host=host, port=port, workers=1, log_level="warning",
        ))
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
    except (Exception, SystemExit):
        listener.close()
        raise

    try:
        _wait_for_server(server, server_thread, startup_error)
    except Exception as exc:
        server.should_exit = True
        server_thread.join(timeout=5)
        if server_thread.is_alive():
            raise BackendShutdownTimeout(
                f"{exc}. The local backend is still shutting down."
            ) from exc
        raise
    return server, server_thread, port


def _build_error_page(message: str) -> str:
    """Render a small fallback HTML page with the log file location."""
    escaped_message = html.escape(message)
    escaped_log_path = html.escape(str(get_error_log_path()))
    return f"""
    <html>
      <body style="font-family: Segoe UI, sans-serif; background: #111827; color: #f3f4f6; padding: 32px;">
        <h1 style="margin-top: 0;">Face Manager could not recover automatically</h1>
        <p>Application log location:</p>
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
        try:
            configure_error_logging()
            install_global_exception_hooks()
            server, server_thread, port = _start_server(host)
            break
        except Exception as exc:
            last_error = exc
            logger.exception(
                "Desktop backend startup attempt %s failed",
                attempt + 1,
            )
            if isinstance(exc, BackendShutdownTimeout):
                break
            if attempt == 0:
                time.sleep(0.5)

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

    try:
        window = webview.create_window(
            title=f"Face Manager {APP_VERSION}",
            url=f"http://{host}:{port}",
            min_size=(1200, 780),
        )
        register_shutdown_callback(window.destroy)
        webview.start(debug=False)
    except Exception:
        logger.exception("Desktop webview failed during startup or runtime")
        raise
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)


if __name__ == "__main__":
    main()
