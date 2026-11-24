import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.models.face_model import FaceModel, get_compute_mode, get_execution_provider, preload_gpu_runtime_dlls


class ExecutionProviderTest(unittest.TestCase):
    def test_frozen_windows_gpu_paths_are_available_before_preload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundled = [root / "nvidia" / component / "bin" for component in ("cuda_nvrtc", "cudnn")]
            for path in bundled:
                path.mkdir(parents=True)
            original = os.pathsep.join(("original-first", str(bundled[0]), "original-last"))
            expected = os.pathsep.join([*(str(path) for path in bundled), "original-first", "original-last"])
            observed = []
            with (
                patch.dict(os.environ, {"PATH": original}),
                patch("backend.models.face_model.sys.platform", "win32"),
                patch("backend.models.face_model.sys.frozen", True, create=True),
                patch("backend.models.face_model.sys._MEIPASS", folder, create=True),
                patch("backend.models.face_model.get_build_variant", return_value="gpu"),
                patch("backend.models.face_model.ort.preload_dlls", side_effect=lambda **_: observed.append(os.environ["PATH"])),
            ):
                preload_gpu_runtime_dlls()
                preload_gpu_runtime_dlls()
            self.assertEqual(observed, [expected, expected])

    def test_windows_cpu_and_unfrozen_builds_keep_their_path(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "nvidia" / "cuda_nvrtc" / "bin").mkdir(parents=True)
            for frozen, variant in ((True, "cpu"), (False, "gpu")):
                with (
                    self.subTest(frozen=frozen, variant=variant),
                    patch.dict(os.environ, {"PATH": "original"}),
                    patch("backend.models.face_model.sys.platform", "win32"),
                    patch("backend.models.face_model.sys.frozen", frozen, create=True),
                    patch("backend.models.face_model.sys._MEIPASS", folder, create=True),
                    patch("backend.models.face_model.get_build_variant", return_value=variant),
                    patch("backend.models.face_model.ort.preload_dlls"),
                ):
                    preload_gpu_runtime_dlls()
                    self.assertEqual(os.environ["PATH"], "original")

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
        return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    def test_loaded_session_providers_override_available_providers(self, _, preload, face_analysis):
        app = face_analysis.return_value
        detection = Mock()
        recognition = Mock()
        app.models = {"detection": detection, "recognition": recognition}
        detection.session.get_providers.return_value = ["CPUExecutionProvider"]
        recognition.session.get_providers.return_value = ["CPUExecutionProvider"]

        model = FaceModel()
        self.assertEqual(model.compute_mode, "cpu")
        self.assertEqual(get_execution_provider(), "CPUExecutionProvider")

        for model_session in (detection, recognition):
            model_session.session.get_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.assertEqual(model.compute_mode, "gpu")
        self.assertEqual(get_execution_provider(), "CUDAExecutionProvider")

        detection.session.get_providers.return_value = ["CPUExecutionProvider"]
        self.assertEqual(get_compute_mode(), "cpu")
        preload.assert_called_once_with()

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
