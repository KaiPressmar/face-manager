"""Contract checks for Android inference without requiring a device at import time."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from backend.models.android_face_model import AndroidFaceModel


class _JavaList:
    """Model Chaquopy's Java List: toArray works, Python iteration does not."""

    def __init__(self, values):
        self._values = values

    def toArray(self):
        return self._values


class AndroidFaceModelTest(unittest.TestCase):
    def test_bridge_preserves_rgb_bytes_and_desktop_result_contract(self):
        engine = Mock()
        engine.detectRgb.return_value = _JavaList([SimpleNamespace(
            bounds=SimpleNamespace(left=1.9, top=2.8, right=8.1, bottom=10.2),
            embedding=[1.0] + [0.0] * 511,
        )])
        java = SimpleNamespace(
            jclass=Mock(return_value=SimpleNamespace(getInstance=lambda: engine)),
            jarray=lambda _: bytes,
            jbyte=object(),
        )
        pixels = np.arange(360, dtype=np.uint8).reshape(12, 10, 3)[:, ::-1]
        with patch.dict(sys.modules, {"java": java}):
            model = AndroidFaceModel()
            faces = model.detect_and_embed(pixels)
        engine.detectRgb.assert_called_once_with(pixels.tobytes(), 10, 12)
        self.assertEqual(model.compute_mode, "cpu")
        self.assertEqual(faces[0]["bbox"], (1, 2, 7, 8))
        self.assertEqual(faces[0]["embedding"].shape, (512,))
        self.assertEqual(faces[0]["embedding"].dtype, np.float32)

    def test_explicit_android_platform_does_not_import_desktop_native_packages(self):
        environment = dict(os.environ, FACE_MANAGER_PLATFORM="android")
        completed = subprocess.run(
            [sys.executable, "-c", "\n".join([
                "import sys",
                "from backend.models.face_model import FaceModel, get_compute_mode, get_execution_provider",
                "assert FaceModel.__name__ == 'AndroidFaceModel'",
                "assert get_compute_mode() == 'cpu'",
                "assert get_execution_provider() == 'CPUExecutionProvider'",
                "assert 'insightface' not in sys.modules",
                "assert 'onnxruntime' not in sys.modules",
            ])],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
