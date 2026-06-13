import unittest
from unittest.mock import Mock, patch

from backend.models.face_model import FaceModel, get_compute_mode, get_execution_provider


class ExecutionProviderTest(unittest.TestCase):
    def test_cuda_is_selected_when_available(self):
        provider = get_execution_provider(
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
        )

        self.assertEqual(provider, "CUDAExecutionProvider")

    def test_cpu_is_selected_when_cuda_is_unavailable(self):
        provider = get_execution_provider(
            ["AzureExecutionProvider", "CPUExecutionProvider"]
        )

        self.assertEqual(provider, "CPUExecutionProvider")

    def test_compute_mode_matches_execution_provider(self):
        self.assertEqual(get_compute_mode("CUDAExecutionProvider"), "gpu")
        self.assertEqual(get_compute_mode("CPUExecutionProvider"), "cpu")

    @patch("backend.models.face_model.FaceAnalysis")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CPUExecutionProvider"],
    )
    def test_model_loads_only_required_modules(self, _, face_analysis):
        app = Mock()
        face_analysis.return_value = app

        model = FaceModel()

        self.assertEqual(model.compute_mode, "cpu")
        face_analysis.assert_called_once_with(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        app.prepare.assert_called_once_with(ctx_id=-1, det_size=(1024, 1024))
