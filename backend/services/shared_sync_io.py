"""Supervise one shared-index filesystem worker from the coordinator thread."""

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal


logger = logging.getLogger("face_manager.shared_sync")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_RESULT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SharedSyncResult:
    status: Literal["ok", "error", "timeout", "published_unknown"]
    payload: dict | None = None
    detail: str | None = None


@dataclass
class _Child:
    process: subprocess.Popen
    result_path: Path
    deadline: float
    interrupted: bool = False


def _worker_command(args: list[str], result_path: Path) -> tuple[list[str], dict]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--shared-sync-worker", *args, str(result_path)], {
            "env": dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
        }
    return [
        sys.executable, "-E", "-s", "-m", "backend.services.shared_sync_worker",
        *args, str(result_path),
    ], {"cwd": _PROJECT_ROOT}


class SharedSyncProcess:
    """Run a bounded operation without a shared-filesystem handle in this process.

    A timeout reports an uncertain publication immediately but leaves the child
    in quarantine until it exits. The coordinator must consume each result and
    reconcile uncertain writes before permitting a new publishing operation.
    """

    def __init__(self, *, timeout_seconds: float = 10.0, publish_capable: bool = True):
        if not 0 < timeout_seconds < float("inf"):
            raise ValueError("Shared sync timeout must be positive and finite")
        self._timeout_seconds = timeout_seconds
        self._publish_capable = publish_capable
        self._child: _Child | None = None
        self._result: SharedSyncResult | None = None
        self._lock = Lock()

    @property
    def busy(self) -> bool:
        """Whether a child is still active or quarantined."""
        with self._lock:
            return self._child is not None

    def start(self, args: list[str]) -> bool:
        """Start one worker, rejecting overlaps and unread results."""
        with self._lock:
            if self._child is not None or self._result is not None:
                return False
            result_dir = None
            try:
                result_dir = Path(tempfile.mkdtemp(prefix="face-manager-shared-sync-"))
                result_path = result_dir / "reply.json"
                command, options = _worker_command(args, result_path)
                deadline = time.monotonic() + self._timeout_seconds
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    **options,
                )
            except (OSError, ValueError):
                if result_dir is not None:
                    self._cleanup(result_dir)
                self._result = SharedSyncResult("error", detail="Could not start shared sync worker")
                return False
            self._child = _Child(process, result_path, deadline)
            return True

    def poll(self) -> SharedSyncResult | None:
        """Reap without waiting and return the current outcome once."""
        with self._lock:
            self._advance()
            result = self._result
            self._result = None
            return result

    def stop(self, timeout_seconds: float = 2.0) -> bool:
        """Interrupt the child and return whether it was safely reaped."""
        deadline = time.monotonic() + min(2.0, max(0.0, timeout_seconds))
        while True:
            if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                return False
            try:
                self._advance()
                if self._child is None:
                    return True
                self._interrupt(self._child, "Shared sync worker was stopped")
            finally:
                self._lock.release()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.025, remaining))

    def _interrupt(self, child: _Child, detail: str) -> None:
        if child.interrupted:
            return
        child.interrupted = True
        status = "published_unknown" if self._publish_capable else "timeout"
        self._result = SharedSyncResult(status, detail=detail)
        try:
            child.process.kill()
        except OSError:
            # Exiting concurrently or stuck with uninterruptible filesystem I/O.
            # The child stays registered until poll confirms that it is gone.
            pass

    def _advance(self) -> None:
        child = self._child
        if child is None:
            return
        exit_code = child.process.poll()
        if exit_code is None:
            if time.monotonic() >= child.deadline:
                self._interrupt(child, "Shared sync worker timed out")
            return
        if not child.interrupted:
            self._result = self._read_result(child.result_path, exit_code)
        self._cleanup(child.result_path.parent)
        self._child = None

    def _read_result(self, path: Path, exit_code: int) -> SharedSyncResult:
        if exit_code == 0:
            try:
                if stat.S_ISREG(path.lstat().st_mode):
                    with path.open("rb") as stream:
                        data = stream.read(_MAX_RESULT_BYTES + 1)
                    if len(data) <= _MAX_RESULT_BYTES:
                        payload = json.loads(data.decode("utf-8"))
                        if isinstance(payload, dict):
                            return SharedSyncResult("ok", payload=payload)
            except (OSError, ValueError, RecursionError):
                pass
        status = "published_unknown" if self._publish_capable else "error"
        return SharedSyncResult(status, detail="Shared sync worker returned no valid result")

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except OSError:
            logger.warning("Could not remove a shared sync worker's local result directory")
