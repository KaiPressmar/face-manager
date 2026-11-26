import json
import os
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.services import gpu_diagnostics


class GpuDiagnosticTest(unittest.TestCase):
    def test_worker_is_bounded_and_uses_a_private_data_directory(self):
        def run(command, **options):
            reply = Path(command[-1])
            self.assertEqual(options["timeout"], 30)
            self.assertEqual(Path(options["env"]["FACE_MANAGER_DATA_DIR"]).parent, reply.parent)
            reply.write_text(json.dumps(gpu_diagnostics._error("diagnostic_models_missing")), encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with patch.object(gpu_diagnostics.subprocess, "run", side_effect=run):
            result = gpu_diagnostics.run_gpu_diagnostic()
        self.assertEqual(result["provider_reason"], "diagnostic_models_missing")

    def test_timeout_and_overlapping_test_report_specific_reasons(self):
        with patch.object(gpu_diagnostics.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 30)):
            self.assertEqual(gpu_diagnostics.run_gpu_diagnostic()["provider_reason"], "diagnostic_timeout")
        with gpu_diagnostics._diagnostic_lock:
            self.assertEqual(gpu_diagnostics.run_gpu_diagnostic()["provider_reason"], "diagnostic_busy")

    def test_android_does_not_run_the_desktop_probe(self):
        with (patch.dict(os.environ, {"FACE_MANAGER_PLATFORM": "android"}),
              patch.object(gpu_diagnostics.subprocess, "run") as run):
            self.assertEqual(gpu_diagnostics.run_gpu_diagnostic()["provider_reason"], "diagnostic_unsupported")
        run.assert_not_called()

    def test_frozen_entry_point_skips_window_and_backend_imports(self):
        source = Path(__file__).resolve().parents[1] / "desktop_main.py"
        worker = Mock(return_value=0)
        with (patch.object(sys, "frozen", True, create=True),
              patch.object(sys, "argv", [str(source), "--gpu-diagnostic-worker", "reply.json"]),
              patch.dict(sys.modules, {"uvicorn": None, "webview": None}),
              patch.object(gpu_diagnostics, "diagnostic_worker_main", worker),
              self.assertRaises(SystemExit) as stopped):
            runpy.run_path(str(source), run_name="__main__")
        self.assertEqual(stopped.exception.code, 0)
        worker.assert_called_once_with(["reply.json"])
