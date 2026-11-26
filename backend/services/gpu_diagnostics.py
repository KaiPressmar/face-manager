"""Run an explicit model check outside the app process and its library."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

_TIMEOUT_SECONDS = 30
_MAX_REPLY_BYTES = 16 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_diagnostic_lock = Lock()


def _error(reason):
    return {
        "provider_state": "error",
        "provider_reason": reason,
        "execution_provider": "CPUExecutionProvider",
        "provider_initialized": False,
        "compute_mode": "cpu",
        "provider_attempts": [],
        "provider_timings_ms": {},
    }


def run_gpu_diagnostic():
    """Check the installed models with a fresh runtime and bounded wait."""
    if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
        return _error("diagnostic_unsupported")
    if not _diagnostic_lock.acquire(blocking=False):
        return _error("diagnostic_busy")
    try:
        with tempfile.TemporaryDirectory(prefix="face-manager-gpu-") as folder:
            result_path = Path(folder) / "result.json"
            env = dict(os.environ, FACE_MANAGER_DATA_DIR=str(Path(folder) / "data"))
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--gpu-diagnostic-worker", str(result_path)]
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                cwd = None
            else:
                command = [sys.executable, "-E", "-s", "-m", "backend.services.gpu_diagnostics", str(result_path)]
                cwd = _PROJECT_ROOT
            completed = subprocess.run(
                command, cwd=cwd, env=env, timeout=_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode or result_path.stat().st_size > _MAX_REPLY_BYTES:
                return _error("diagnostic_worker_failed")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(result, dict) and result.get("provider_state") in {"active", "fallback", "error"}:
                return result
            return _error("diagnostic_worker_failed")
    except subprocess.TimeoutExpired:
        return _error("diagnostic_timeout")
    except (OSError, ValueError):
        return _error("diagnostic_worker_failed")
    finally:
        _diagnostic_lock.release()


def _check_installed_models():
    from backend.models.face_model import (
        CPU_PROVIDER, FaceModel, _BUFFALO_WEIGHTS, _check_accelerated_sessions,
        get_runtime_provider_details,
    )

    # Do not start an InsightFace download from a diagnostic action.
    cache = Path.home() / ".insightface" / "models" / "buffalo_l"
    if not all((cache / name).is_file() for name in _BUFFALO_WEIGHTS.values()):
        return _error("diagnostic_models_missing")
    try:
        model = FaceModel()
    except Exception:
        return get_runtime_provider_details()
    if model.execution_provider == CPU_PROVIDER:
        try:
            with model._measure("cpu_warmup"):
                _check_accelerated_sessions(model._active[0], CPU_PROVIDER)
        except Exception:
            model._publish("error", "cpu_inference_failed")
    return get_runtime_provider_details()


def diagnostic_worker_main(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    try:
        result = _check_installed_models()
    except Exception:
        result = _error("diagnostic_worker_failed")
    try:
        Path(args[0]).write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(diagnostic_worker_main(sys.argv[1:]))
