import unittest
from unittest.mock import Mock, patch

from backend.models.face_model import FaceModel, get_compute_mode, get_execution_provider


class ExecutionProviderTest(unittest.TestCase):
    @patch("backend.models.face_model.ort.preload_dlls")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CPUExecutionProvider"],
    )
    def test_execution_provider_preloads_runtime_dlls(self, _, preload_dlls):
        provider = get_execution_provider()

        self.assertEqual(provider, "CPUExecutionProvider")
        preload_dlls.assert_called_once_with(directory="")

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
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CPUExecutionProvider"],
    )
    def test_model_loads_only_required_modules(
        self, _, preload_gpu_runtime_dlls, face_analysis
    ):
        app = Mock()
        face_analysis.return_value = app

        model = FaceModel()

        self.assertEqual(model.compute_mode, "cpu")
        preload_gpu_runtime_dlls.assert_called_once_with()
        face_analysis.assert_called_once_with(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        app.prepare.assert_called_once_with(ctx_id=-1, det_size=(1024, 1024))

    @patch("backend.models.face_model.FaceAnalysis")
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    def test_model_falls_back_to_cpu_when_gpu_startup_fails(
        self, _, preload_gpu_runtime_dlls, face_analysis
    ):
        gpu_app = Mock()
        gpu_app.prepare.side_effect = RuntimeError("GPU init failed")
        cpu_app = Mock()
        face_analysis.side_effect = [gpu_app, cpu_app]

        model = FaceModel()

        self.assertEqual(model.compute_mode, "cpu")
        preload_gpu_runtime_dlls.assert_called_once_with()
        self.assertEqual(face_analysis.call_count, 2)
        self.assertEqual(
            face_analysis.call_args_list[0].kwargs["providers"],
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.assertEqual(
            face_analysis.call_args_list[1].kwargs["providers"],
            ["CPUExecutionProvider"],
        )
        cpu_app.prepare.assert_called_once_with(ctx_id=-1, det_size=(1024, 1024))
