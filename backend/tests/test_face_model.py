import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.models.face_model import (
    FaceModel, _check_accelerated_sessions, get_compute_mode, get_execution_provider,
    get_runtime_provider_details, preload_gpu_runtime_dlls,
)


class ExecutionProviderTest(unittest.TestCase):
    def setUp(self):
        for target, options in (
            ("_active_model", {"new": None}),
            ("_last_provider_error", {"new": None}),
            ("ensure_available", {"return_value": "buffalo_l"}),
            ("_canonical_buffalo_weights", {"return_value": None}),
            ("_find_intel_gpu", {"return_value": None}),
        ):
            override = patch(f"backend.models.face_model.{target}", **options)
            override.start()
            self.addCleanup(override.stop)

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

    @patch("backend.models.face_model._active_model", None)
    @patch("backend.models.face_model.ort.get_available_providers")
    def test_unloaded_model_does_not_claim_gpu_acceleration(self, available):
        self.assertEqual(get_execution_provider(), "CPUExecutionProvider")
        available.assert_not_called()

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
        self.assertEqual(get_compute_mode("OpenVINOExecutionProvider"), "gpu")
        self.assertEqual(get_compute_mode("CPUExecutionProvider"), "cpu")

    @patch("backend.models.face_model.FaceAnalysis")
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    def test_silent_cuda_session_fallback_is_not_reported_as_gpu(self, _, preload, face_analysis):
        app = Mock()
        face_analysis.side_effect = [app, Mock()]
        detection = Mock()
        recognition = Mock()
        app.models = {"detection": detection, "recognition": recognition}
        detection.session.get_providers.return_value = ["CPUExecutionProvider"]
        recognition.session.get_providers.return_value = ["CPUExecutionProvider"]

        model = FaceModel()
        self.assertEqual(model.compute_mode, "cpu")
        self.assertEqual(get_execution_provider(), "CPUExecutionProvider")
        details = get_runtime_provider_details()
        self.assertEqual(details["provider_state"], "fallback")
        self.assertEqual(details["provider_reason"], "cuda_startup_failed")
        self.assertEqual(face_analysis.call_count, 2)
        preload.assert_called_once_with()

    @patch("backend.models.face_model.FaceAnalysis")
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch(
        "backend.models.face_model.ort.get_available_providers",
        return_value=["CPUExecutionProvider"],
    )
    def test_custom_pack_preserves_insightface_discovery(
        self, _, preload_gpu_runtime_dlls, face_analysis
    ):
        app = Mock()
        face_analysis.return_value = app

        model = FaceModel()

        self.assertEqual(model.compute_mode, "cpu")
        preload_gpu_runtime_dlls.assert_not_called()
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

    @patch("backend.models.face_model._find_intel_gpu")
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch("backend.models.face_model._check_accelerated_sessions")
    @patch("backend.models.face_model.FaceModel._create_face_analysis")
    @patch("backend.models.face_model.ort.get_available_providers", return_value=["CUDAExecutionProvider"])
    def test_working_cuda_precedes_intel(self, _available, create, warm, _preload, intel):
        model = FaceModel()
        self.assertEqual(model.execution_provider, "CUDAExecutionProvider")
        warm.assert_called_once_with(create.return_value, "CUDAExecutionProvider")
        intel.assert_not_called()

    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch("backend.models.face_model._find_intel_gpu")
    @patch("backend.models.face_model.ort.SessionOptions")
    @patch("backend.models.face_model.ort.InferenceSession")
    @patch("backend.models.face_model._check_accelerated_sessions")
    @patch("backend.models.face_model.FaceModel._create_face_analysis")
    @patch("backend.models.face_model.ort.get_available_providers", return_value=["CUDAExecutionProvider"])
    def test_inference_retries_cuda_then_intel_then_cpu_before_returning_faces(
        self, _available, create, _warm, sessions, options, intel, _preload
    ):
        cuda = Mock()
        cuda.get.side_effect = RuntimeError("CUDA runtime failure")
        intel_app = Mock(models={
            "detection": SimpleNamespace(model_file="detect.onnx", session=object()),
            "recognition": SimpleNamespace(model_file="recognize.onnx", session=object()),
        })
        intel_app.get.side_effect = RuntimeError("Intel runtime failure")
        cpu = Mock()
        cpu.get.return_value = []
        create.side_effect = [cuda, intel_app, cpu]

        model = FaceModel()
        image = object()
        self.assertEqual(model.detect_and_embed(image), [])
        self.assertEqual(get_execution_provider(), "CPUExecutionProvider")
        self.assertEqual(get_runtime_provider_details()["provider_reason"], "intel_inference_failed")
        for app in (cuda, intel_app, cpu):
            app.get.assert_called_once_with(image)
        options.return_value.add_provider_for_devices.assert_called_once_with([intel.return_value], {})
        self.assertEqual([call.args[0] for call in sessions.call_args_list], ["detect.onnx", "recognize.onnx"])

    @patch.dict(os.environ, {"FACE_MANAGER_CPU_ONLY": "1"})
    @patch("backend.models.face_model.preload_gpu_runtime_dlls")
    @patch("backend.models.face_model._find_intel_gpu")
    @patch("backend.models.face_model.FaceModel._create_face_analysis")
    def test_cpu_override_skips_both_accelerators(self, create, intel, preload):
        model = FaceModel()
        self.assertEqual(model.compute_mode, "cpu")
        status = get_runtime_provider_details()
        self.assertEqual(status["provider_state"], "active")
        self.assertEqual(status["provider_reason"], "cpu_only")
        create.assert_called_once_with(["CPUExecutionProvider"], -1)
        intel.assert_not_called()
        preload.assert_not_called()

    def test_reports_initializing_and_reports_failed_cpu_start_after_model_disposal(self):
        self.assertEqual(get_runtime_provider_details()["provider_state"], "idle")

        def fail_model(*_):
            self.assertEqual(get_runtime_provider_details()["provider_state"], "checking")
            raise RuntimeError("private model path")

        with (patch.dict(os.environ, {"FACE_MANAGER_CPU_ONLY": "1"}),
              patch.object(FaceModel, "_create_face_analysis", side_effect=fail_model)):
            with self.assertRaises(RuntimeError):
                FaceModel()

        status = get_runtime_provider_details()
        self.assertEqual(status["provider_state"], "error")
        self.assertEqual(status["provider_reason"], "cpu_startup_failed")
        self.assertNotIn("private model path", str(status))

    def test_standard_pack_only_starts_two_cpu_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            weights = {"detection": Path(folder) / "det_10g.onnx", "recognition": Path(folder) / "w600k_r50.onnx"}
            for path in weights.values():
                path.touch()
            detector = Mock(taskname="detection", model_file=str(weights["detection"]))
            recognizer = Mock(taskname="recognition", model_file=str(weights["recognition"]))
            with (patch.dict(os.environ, {"FACE_MANAGER_CPU_ONLY": "1"}),
                  patch("backend.models.face_model._canonical_buffalo_weights", return_value=weights),
                  patch("backend.models.face_model.get_model", side_effect=[detector, recognizer]) as load):
                model = FaceModel()

            self.assertEqual([call.args[0] for call in load.call_args_list], [str(path) for path in weights.values()])
            self.assertTrue(all(call.kwargs == {"providers": ["CPUExecutionProvider"]} for call in load.call_args_list))
            self.assertIs(model._active[0].det_model, detector)
            detector.prepare.assert_called_once_with(0, input_size=(1024, 1024), det_thresh=0.5)
            recognizer.prepare.assert_called_once_with(0)
            self.assertTrue({"model_assets", "cpu_detection_session", "cpu_recognition_session", "cpu_prepare"}
                            <= get_runtime_provider_details()["provider_timings_ms"].keys())

    def test_standard_pack_loads_intel_sessions_directly_with_device_options(self):
        weights = {"detection": Path("det_10g.onnx"), "recognition": Path("w600k_r50.onnx")}
        device = object()
        detector = Mock(taskname="detection", model_file=str(weights["detection"]))
        recognizer = Mock(taskname="recognition", model_file=str(weights["recognition"]))
        with (patch("backend.models.face_model._canonical_buffalo_weights", return_value=weights),
              patch("backend.models.face_model._find_intel_gpu", return_value=device),
              patch("backend.models.face_model.ort.get_available_providers", return_value=["CPUExecutionProvider"]),
              patch("backend.models.face_model.ort.SessionOptions") as make_options,
              patch("backend.models.face_model.ModelRouter") as router,
              patch("backend.models.face_model.get_model") as cpu_loader,
              patch("backend.models.face_model._check_accelerated_sessions") as warm):
            router.return_value.get_model.side_effect = [detector, recognizer]
            model = FaceModel()

        details = get_runtime_provider_details()
        self.assertEqual((details["provider_state"], details["provider_reason"]), ("active", "intel_active"))
        self.assertEqual(model.execution_provider, "OpenVINOExecutionProvider")
        self.assertEqual([call.args[0] for call in router.call_args_list], [str(path) for path in weights.values()])
        make_options.return_value.add_provider_for_devices.assert_called_once_with([device], {})
        self.assertTrue(all(call.kwargs == {"sess_options": make_options.return_value}
                            for call in router.return_value.get_model.call_args_list))
        cpu_loader.assert_not_called()
        warm.assert_called_once_with(model._active[0], "OpenVINOExecutionProvider")

    def test_registered_accelerator_with_cpu_only_graph_is_rejected(self):
        session = Mock()
        session.get_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        session.get_provider_graph_assignment_info.return_value = [
            SimpleNamespace(ep_name="CPUExecutionProvider", get_nodes=lambda: [object()]),
        ]
        app = SimpleNamespace(
            models={"detection": SimpleNamespace(session=session)},
            _face_manager_checks_assignment=True,
        )
        with self.assertRaisesRegex(RuntimeError, "did not assign any model nodes"):
            _check_accelerated_sessions(app, "CUDAExecutionProvider")
        session.run.assert_not_called()
