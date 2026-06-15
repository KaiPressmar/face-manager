"""Shared persistent error logging for packaged desktop deployments."""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DB_PATH, get_error_log_path

MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5
DEFAULT_FILE_LOG_LEVEL = "ERROR"
VALID_FILE_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

_LOCK = threading.Lock()
_CONFIGURED = False
_HOOKS_INSTALLED = False
_HANDLER: RotatingFileHandler | None = None


def normalize_file_log_level(value: str | None) -> str:
    """Validate and normalize one persisted file log level."""
    normalized = str(value or "").strip().upper()
    if normalized not in VALID_FILE_LOG_LEVELS:
        raise ValueError(
            "File log level must be one of: "
            + ", ".join(level.lower() for level in VALID_FILE_LOG_LEVELS)
            + "."
        )
    return normalized


def get_persisted_file_log_level() -> str:
    """Return the configured file log level or the safe default."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        return DEFAULT_FILE_LOG_LEVEL

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("file_log_level",),
        ).fetchone()
    except sqlite3.Error:
        return DEFAULT_FILE_LOG_LEVEL
    finally:
        if conn is not None:
            conn.close()

    if not row or row[0] is None:
        return DEFAULT_FILE_LOG_LEVEL
    try:
        return normalize_file_log_level(row[0])
    except ValueError:
        return DEFAULT_FILE_LOG_LEVEL


def set_runtime_file_log_level(level: str) -> str:
    """Apply one normalized log level to the persistent file logger."""
    normalized = normalize_file_log_level(level)
    numeric_level = getattr(logging, normalized)
    for logger_name in ("face_manager", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(numeric_level)
    if _HANDLER is not None:
        _HANDLER.setLevel(numeric_level)
    return normalized


def apply_persisted_file_log_level() -> str:
    """Refresh runtime log verbosity from the persisted setting."""
    configure_error_logging()
    return set_runtime_file_log_level(get_persisted_file_log_level())


def configure_error_logging() -> Path:
    """Attach a rotating file handler to the app and server loggers."""
    global _CONFIGURED, _HANDLER
    with _LOCK:
        if _CONFIGURED:
            return get_error_log_path()

        log_path = get_error_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(threadName)s: %(message)s"
            )
        )

        for logger_name in ("face_manager", "uvicorn", "uvicorn.error"):
            logger = logging.getLogger(logger_name)
            if not any(
                isinstance(existing, RotatingFileHandler)
                and getattr(existing, "baseFilename", None) == handler.baseFilename
                for existing in logger.handlers
            ):
                logger.addHandler(handler)

        _HANDLER = handler
        _CONFIGURED = True
        set_runtime_file_log_level(get_persisted_file_log_level())
        return log_path


def get_logger(name: str = "face_manager") -> logging.Logger:
    """Return an application logger backed by the persistent error log."""
    configure_error_logging()
    return logging.getLogger(name)


def log_exception(message: str) -> None:
    """Write the current exception traceback into the persistent error log."""
    get_logger("face_manager.errors").exception(message)


def install_global_exception_hooks() -> Path:
    """Capture uncaught process and thread exceptions in the error log."""
    global _HOOKS_INSTALLED
    log_path = configure_error_logging()
    with _LOCK:
        if _HOOKS_INSTALLED:
            return log_path

        default_excepthook = sys.excepthook

        def _sys_excepthook(exc_type, exc_value, exc_traceback):
            get_logger("face_manager.crash").error(
                "Uncaught process exception",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            if default_excepthook is not None:
                default_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = _sys_excepthook

        if hasattr(threading, "excepthook"):
            default_threading_excepthook = threading.excepthook

            def _threading_excepthook(args):
                get_logger("face_manager.crash").error(
                    "Uncaught thread exception in %s",
                    args.thread.name if args.thread else "unknown-thread",
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
                default_threading_excepthook(args)

            threading.excepthook = _threading_excepthook

        _HOOKS_INSTALLED = True
        return log_path
