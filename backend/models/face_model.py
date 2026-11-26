import hashlib
import json
import logging
import os
import sys
from contextlib import contextmanager
from copy import copy
from importlib import metadata
from pathlib import Path
from threading import Lock
from time import perf_counter
from weakref import ref

import numpy as np
_ANDROID = os.environ.get("FACE_MANAGER_PLATFORM") == "android"
if not _ANDROID:
    import onnxruntime as ort
    from insightface.app import FaceAnalysis
    from insightface.model_zoo.model_zoo import ModelRouter, get_model
    from insightface.utils import ensure_available
    from onnxruntime.capi.onnxruntime_pybind11_state import EPFail, Fail, RuntimeException
else:
    ort = None

from ..config import get_build_variant
from ..error_logging import configure_error_logging

configure_error_logging()
logger = logging.getLogger("face_manager.face_model")
_active_model = None
_last_provider_error = None
CPU_PROVIDER = "CPUExecutionProvider"
CUDA_PROVIDER = "CUDAExecutionProvider"
INTEL_PROVIDER = "OpenVINOExecutionProvider"
FACE_MODELS = ("detection", "recognition")
DETECTION_SIZE = (1024, 1024)
SUGGESTION_SCORE_THRESHOLD = .36
_openvino_lock = Lock()
_openvino_registered = False
_openvino_dll_directory = None
_SHARED_PREPROCESSING_VERSION = "raw-pillow-rgb-insightface-l2-f32le-512-v1"
_BUFFALO_WEIGHTS = {"detection": "det_10g.onnx", "recognition": "w600k_r50.onnx"}
_BUFFALO_UNUSED = {"1k3d68.onnx", "2d106det.onnx", "genderage.onnx"}


class FaceDetectionBusy(RuntimeError):
    """The active detection model is serving another operation."""


def _weight_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _configure_bundled_cuda_search_path() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or get_build_variant() not in {"gpu", "windows"}:
        return
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is None:
        return

    # cuDNN can load NVRTC and companion DLLs by name during inference. Plain
    # LoadLibrary calls also need their bundled directories on the Windows PATH.
    runtime_root = Path(bundle_root) / "nvidia"
    candidates = (runtime_root / "cuda_nvrtc" / "bin", runtime_root / "cudnn" / "bin")
    bundled = [str(directory) for directory in candidates if directory.is_dir()]
    if not bundled:
        return

    def path_key(value: str) -> str:
        return os.path.normcase(os.path.normpath(value))

    bundled_keys = {path_key(directory) for directory in bundled}
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    path = os.pathsep.join(bundled + [entry for entry in entries if path_key(entry) not in bundled_keys])
    if path != current:
        os.environ["PATH"] = path


def preload_gpu_runtime_dlls():
    """Preload CUDA/cuDNN DLLs when ONNX Runtime exposes the helper."""
    if hasattr(ort, "preload_dlls"):
        try:
            _configure_bundled_cuda_search_path()
            ort.preload_dlls(directory="")
        except Exception:
            logger.exception("Could not preload ONNX Runtime GPU DLLs; continuing")


def get_execution_provider(available_providers=None):
    """Report a verified active provider, or check an explicit provider list."""
    if available_providers is not None and not _ANDROID:
        return CUDA_PROVIDER if CUDA_PROVIDER in available_providers else CPU_PROVIDER
    return get_execution_provider_status()[0]


def get_execution_provider_status() -> tuple[str, bool]:
    """Return the active provider and whether desktop inference has initialized."""
    status = get_runtime_provider_details()
    return status["execution_provider"], status["provider_initialized"]


def provider_details(state, reason, provider=None, attempts=(), timings=None):
    return {
        "provider_state": state,
        "provider_reason": reason,
        "execution_provider": provider or CPU_PROVIDER,
        "provider_initialized": provider is not None,
        "compute_mode": get_compute_mode(provider or CPU_PROVIDER),
        "provider_attempts": list(attempts),
        "provider_timings_ms": dict(timings or {}),
    }


def get_runtime_provider_details():
    """Report verified runtime status without loading models or probing hardware."""
    if _ANDROID:
        return provider_details("active", "android_cpu", CPU_PROVIDER)
    active_model_ref = _active_model
    active_model = active_model_ref() if active_model_ref is not None else None
    if active_model is not None:
        return active_model._provider_snapshot()
    return _last_provider_error or provider_details("idle", "not_checked")


def get_compute_mode(execution_provider=None):
    """Map an ONNX Runtime provider to a UI-facing compute mode.

    Args:
        execution_provider: Optional provider name to map.

    Returns:
        Either ``gpu`` or ``cpu``.
    """
    if execution_provider is None:
        execution_provider = get_execution_provider()
    return "gpu" if execution_provider in {CUDA_PROVIDER, INTEL_PROVIDER} else "cpu"


def _find_intel_gpu():
    """Register the standalone plugin once and select only an Intel GPU."""
    global _openvino_registered, _openvino_dll_directory
    try:
        from onnxruntime_ep_openvino import get_library_path
    except ImportError:
        return None

    with _openvino_lock:
        if not _openvino_registered:
            path = Path(get_library_path())
            if sys.platform == "win32" and _openvino_dll_directory is None:
                # Keep the handle alive for the plugin and its adjacent OpenVINO DLLs.
                _openvino_dll_directory = os.add_dll_directory(str(path.parent))
            ort.register_execution_provider_library("face_manager_openvino", str(path))
            _openvino_registered = True
        return next((
            device for device in ort.get_ep_devices()
            if device.ep_name == INTEL_PROVIDER
            and device.ep_metadata.get("ov_device", "").split(".", 1)[0] == "GPU"
        ), None)


def _check_accelerated_sessions(app, provider):
    """Exercise both real ONNX sessions before reporting GPU acceleration."""
    for task in FACE_MODELS:
        model = app.models[task]
        session = model.session
        if provider not in session.get_providers():
            raise RuntimeError(f"{task} did not activate {provider}")
        if getattr(app, "_face_manager_checks_assignment", False) is True:
            assignments = session.get_provider_graph_assignment_info()
            if not any(subgraph.ep_name == provider and subgraph.get_nodes() for subgraph in assignments):
                raise RuntimeError(f"{task} did not assign any model nodes to {provider}")
        # Let the application select the next accelerator if Run fails.
        session.disable_fallback()
        width, height = DETECTION_SIZE if task == "detection" else model.input_size
        input_name = session.get_inputs()[0].name
        session.run(None, {input_name: np.zeros((1, 3, height, width), dtype=np.float32)})


def _enable_assignment_check(options) -> bool:
    if not hasattr(ort.InferenceSession, "get_provider_graph_assignment_info"):
        return False
    options.add_session_config_entry("session.record_ep_graph_assignment_info", "1")
    return True


def _canonical_buffalo_weights(directory: Path) -> dict[str, Path] | None:
    """Recognize the standard pack without changing model choice for custom packs."""
    try:
        files = {path.name for path in directory.glob("*.onnx")}
    except OSError:
        return None
    names = _BUFFALO_WEIGHTS.values()
    return ({task: directory / name for task, name in _BUFFALO_WEIGHTS.items()}
            if files.issuperset(names) and files <= set(names) | _BUFFALO_UNUSED else None)


class FaceModel:
    """Detect faces and create normalized recognition embeddings."""

    def _new_face_analysis(self, weights, providers, *, session_options=None):
        if weights is None:
            return FaceAnalysis(name="buffalo_l", allowed_modules=list(FACE_MODELS), providers=providers)

        # The upstream constructor creates ONNX sessions before checking its
        # allowed_modules list. Build its usual model wrappers for exactly the
        # two standard assets, then let the same app prepare and run them.
        ort.set_default_logger_severity(3)
        app = FaceAnalysis.__new__(FaceAnalysis)
        app.model_dir = str(weights["detection"].parent)
        app.models = {}
        if session_options is None and providers and providers[0] == CUDA_PROVIDER:
            candidate_options = ort.SessionOptions()
            if _enable_assignment_check(candidate_options):
                app._face_manager_checks_assignment = True
                session_options = candidate_options
        for task, path in weights.items():
            with self._measure(f"{self._stage}_{task}_session"):
                if session_options is None:
                    model = get_model(str(path), providers=providers)
                else:
                    options = {"sess_options": session_options}
                    if providers is not None:
                        options["providers"] = providers
                    model = ModelRouter(str(path)).get_model(**options)
                if model is None or model.taskname != task:
                    raise RuntimeError("The standard model does not match its expected task")
                app.models[task] = model
        app.det_model = app.models["detection"]
        return app

    def _create_face_analysis(self, providers, ctx_id, *, session_options=None):
        """Build and prepare one InsightFace application instance."""
        with self._measure(f"{self._stage}_sessions"):
            app = self._new_face_analysis(self._weights, providers, session_options=session_options)
        with self._measure(f"{self._stage}_prepare"):
            # A negative context only switches these two standard wrappers to
            # CPU, rebuilding sessions that are already explicitly CPU-only.
            safe_ctx = 0 if self._weights is not None and providers == [CPU_PROVIDER] else ctx_id
            app.prepare(ctx_id=safe_ctx, det_size=DETECTION_SIZE)
        try:
            app._face_manager_weight_assets = tuple(
                (task, path, _weight_identity(path.stat()))
                for task in FACE_MODELS
                for path in (Path(app.models[task].model_file),)
            )
        except (AttributeError, KeyError, OSError, TypeError):
            app._face_manager_weight_assets = None
        return app

    def __init__(self):
        """Load only the detection and recognition InsightFace modules."""
        global _active_model, _last_provider_error
        self._lock = Lock()
        self._provider_lock = Lock()
        self._active = None
        self._status = ("checking", "initializing", None)
        self._attempts = []
        self._timings = {}
        self._cpu_only = os.environ.get("FACE_MANAGER_CPU_ONLY") == "1"
        self._shared_signature_cache = None
        _active_model = ref(self)
        _last_provider_error = None

        try:
            with self._measure("model_assets"):
                directory = Path(ensure_available("models", "buffalo_l"))
                self._weights = _canonical_buffalo_weights(directory)
            with self._measure("provider_probe"):
                self._remaining_providers = iter(self._accelerated_providers())
            self._activate(self._load_next_provider())
        except Exception:
            if self._status[1] != "cpu_startup_failed":
                self._publish("error", "model_assets_failed")
            _last_provider_error = self._provider_snapshot()
            raise

    @contextmanager
    def _measure(self, stage):
        started = perf_counter()
        try:
            yield
        finally:
            duration = round((perf_counter() - started) * 1000, 1)
            with self._provider_lock:
                self._timings[stage] = duration
            logger.info("Face recognition stage %s: %.1f ms", stage, duration)

    def _publish(self, state, reason, provider=None):
        with self._provider_lock:
            self._status = (state, reason, provider)

    def _note(self, attempt):
        with self._provider_lock:
            if attempt not in self._attempts:
                self._attempts.append(attempt)

    def _provider_snapshot(self):
        with self._provider_lock:
            return provider_details(*self._status, self._attempts, self._timings)

    def _accelerated_providers(self):
        if self._cpu_only:
            return []
        providers = []
        try:
            if CUDA_PROVIDER in ort.get_available_providers():
                providers.append(CUDA_PROVIDER)
            else:
                self._note("cuda_unavailable")
        except Exception:
            self._note("cuda_probe_failed")
            logger.exception("Could not query CUDA availability")
        providers.append(INTEL_PROVIDER)
        return providers

    def _activate(self, active):
        self._active = active
        provider = active[1]
        if provider == CUDA_PROVIDER:
            self._publish("active", "cuda_active", provider)
        elif provider == INTEL_PROVIDER:
            self._publish("active", "intel_active", provider)
        elif self._cpu_only:
            self._publish("active", "cpu_only", provider)
        else:
            failures = [attempt for attempt in self._attempts if attempt.endswith("_failed")]
            self._publish("fallback", failures[-1] if failures else "gpu_unavailable", provider)

    def _load_next_provider(self):
        cpu_app = None
        for provider in self._remaining_providers:
            self._stage = "cuda" if provider == CUDA_PROVIDER else "intel"
            self._publish("checking", f"checking_{self._stage}")
            try:
                if provider == CUDA_PROVIDER:
                    with self._measure("cuda_runtime_preload"):
                        preload_gpu_runtime_dlls()
                    app = self._create_face_analysis([CUDA_PROVIDER, CPU_PROVIDER], 0)
                else:
                    with self._measure("intel_device_probe"):
                        device = _find_intel_gpu()
                    if device is None:
                        self._note("intel_unavailable")
                        continue
                    options = ort.SessionOptions()
                    options.add_provider_for_devices([device], {})
                    verify_assignment = _enable_assignment_check(options)
                    if self._weights is not None:
                        app = self._create_face_analysis(None, 0, session_options=options)
                        app._face_manager_checks_assignment = verify_assignment
                    else:
                        # Keep the established discovery and session swap for
                        # custom packs whose filenames we cannot identify.
                        cpu_app = self._create_face_analysis([CPU_PROVIDER], -1)
                        original = {task: cpu_app.models[task].session for task in FACE_MODELS}
                        sessions = {
                            task: ort.InferenceSession(cpu_app.models[task].model_file, sess_options=options)
                            for task in FACE_MODELS
                        }
                        for task, session in sessions.items():
                            cpu_app.models[task].session = session
                        cpu_app._face_manager_checks_assignment = verify_assignment
                        try:
                            with self._measure("intel_warmup"):
                                _check_accelerated_sessions(cpu_app, provider)
                        except Exception:
                            for task, session in original.items():
                                cpu_app.models[task].session = session
                            cpu_app._face_manager_checks_assignment = False
                            raise
                        return cpu_app, provider
                with self._measure(f"{self._stage}_warmup"):
                    _check_accelerated_sessions(app, provider)
                return app, provider
            except Exception:
                self._note(f"{self._stage}_startup_failed")
                logger.warning("%s could not start; trying the next provider", provider, exc_info=True)

        self._stage = "cpu"
        self._publish("checking", "checking_cpu")
        try:
            return cpu_app or self._create_face_analysis([CPU_PROVIDER], -1), CPU_PROVIDER
        except Exception:
            self._publish("error", "cpu_startup_failed")
            raise

    @property
    def execution_provider(self):
        return self._active[1]

    @property
    def compute_mode(self):
        return get_compute_mode(self.execution_provider)

    def shared_pipeline_signature(self) -> str:
        """Fingerprint the model assets and preprocessing used for shared results."""
        with self._lock:
            app, _ = self._active
            assets = getattr(app, "_face_manager_weight_assets", None)
            if assets is None:
                raise RuntimeError("Shared analysis requires identifiable model weights")
            if any(_weight_identity(path.stat()) != identity for _, path, identity in assets):
                raise RuntimeError("Model weights changed after the active model was loaded")
            detector, recognizer = (app.models[task] for task in FACE_MODELS)
            settings = (
                tuple(tuple(size) for size in getattr(detector, "input_sizes", (detector.input_size,))),
                float(detector.det_thresh), float(detector.nms_thresh),
                float(detector.input_mean), float(detector.input_std), tuple(recognizer.input_size),
                float(recognizer.input_mean), float(recognizer.input_std),
            )
            cached = self._shared_signature_cache
            if cached is not None and cached[0] == (assets, settings):
                return cached[1]

            weights = {}
            for task, path, identity in assets:
                with path.open("rb") as source:
                    if _weight_identity(os.fstat(source.fileno())) != identity:
                        raise RuntimeError("Model weights changed before fingerprinting")
                    digest = hashlib.sha256()
                    while block := source.read(1024 * 1024):
                        digest.update(block)
                    weights[task] = digest.hexdigest()
                    if _weight_identity(os.fstat(source.fileno())) != identity or _weight_identity(path.stat()) != identity:
                        raise RuntimeError("Model weights changed while fingerprinting")
            import cv2
            from PIL import __version__ as pillow_version

            data = {
                "preprocessing": _SHARED_PREPROCESSING_VERSION,
                "insightface": metadata.version("insightface"),
                "opencv": cv2.__version__,
                "pillow": pillow_version,
                "onnxruntime": ort.__version__,
                "settings": settings,
                "weights": weights,
            }
            signature = hashlib.sha256(
                json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            self._shared_signature_cache = ((assets, settings), signature)
            return signature

    def suggest_face_detections(self, image_np):
        """Return scored detections from the active detector without computing embeddings."""
        if not self._lock.acquire(blocking=False):
            raise FaceDetectionBusy()
        try:
            app, _ = self._active
            detector = app.det_model
            candidate_detector = copy(detector)
            if candidate_detector is detector or not hasattr(candidate_detector, "det_thresh"):
                raise RuntimeError("The active detector cannot use an isolated suggestion threshold")
            # Share the prepared ONNX session, not the detector's mutable
            # threshold or anchor cache. Accelerated imports use this same lock.
            candidate_detector.det_thresh = min(SUGGESTION_SCORE_THRESHOLD, detector.det_thresh)
            if hasattr(candidate_detector, "center_cache"):
                candidate_detector.center_cache = {}
            boxes, _ = candidate_detector.detect(image_np)
            return boxes
        finally:
            self._lock.release()

    def detect_and_embed(self, image_np):
        """Detect faces and calculate normalized embeddings.

        Args:
            image_np: RGB image represented as a NumPy array.

        Returns:
            Face dictionaries containing bounding boxes and embeddings.
        """
        while True:
            active = self._active
            app, provider = active
            if provider == CPU_PROVIDER:
                try:
                    faces = app.get(image_np)
                except (EPFail, Fail, RuntimeException, RuntimeError):
                    self._publish("error", "cpu_inference_failed")
                    raise
                if self._status[0] == "error":
                    self._activate(active)
                break
            # Imports can share a model. Run one inference at a time on the
            # accelerator so switching sessions cannot race another GPU call.
            with self._lock:
                if self._active is not active:
                    continue
                try:
                    faces = app.get(image_np)
                    break
                except (EPFail, Fail, RuntimeException, RuntimeError):
                    logger.warning("%s failed during inference; retrying the image", provider, exc_info=True)
                    self._note("cuda_inference_failed" if provider == CUDA_PROVIDER else "intel_inference_failed")
                    self._activate(self._load_next_provider())
        results = []
        for f in faces:
            x1, y1, x2, y2 = f.bbox.astype(int)
            w = x2 - x1
            h = y2 - y1

            emb = f.embedding.astype(np.float32)
            emb /= np.linalg.norm(emb) + 1e-12

            results.append({"bbox": (x1, y1, w, h), "embedding": emb})

        return results


if _ANDROID:
    from .android_face_model import AndroidFaceModel as FaceModel
