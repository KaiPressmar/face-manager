import hashlib
import json
import logging
import os
import sys
from importlib import metadata
from pathlib import Path
from threading import Lock
from weakref import ref

import numpy as np
_ANDROID = os.environ.get("FACE_MANAGER_PLATFORM") == "android"
if not _ANDROID:
    import onnxruntime as ort
    from insightface.app import FaceAnalysis
    from onnxruntime.capi.onnxruntime_pybind11_state import EPFail, Fail, RuntimeException
else:
    ort = None

from ..config import get_build_variant
from ..error_logging import configure_error_logging

configure_error_logging()
logger = logging.getLogger("face_manager.face_model")
_active_model = None
CPU_PROVIDER = "CPUExecutionProvider"
CUDA_PROVIDER = "CUDAExecutionProvider"
INTEL_PROVIDER = "OpenVINOExecutionProvider"
FACE_MODELS = ("detection", "recognition")
DETECTION_SIZE = (1024, 1024)
_openvino_lock = Lock()
_openvino_registered = False
_openvino_dll_directory = None
_SHARED_PREPROCESSING_VERSION = "raw-pillow-rgb-insightface-l2-f32le-512-v1"


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
    if _ANDROID:
        return CPU_PROVIDER, True
    active_model_ref = _active_model
    active_model = active_model_ref() if active_model_ref is not None else None
    return (active_model.execution_provider, True) if active_model is not None else (CPU_PROVIDER, False)


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
        # Let the application select the next accelerator if Run fails.
        session.disable_fallback()
        width, height = DETECTION_SIZE if task == "detection" else model.input_size
        input_name = session.get_inputs()[0].name
        session.run(None, {input_name: np.zeros((1, 3, height, width), dtype=np.float32)})


class FaceModel:
    """Detect faces and create normalized recognition embeddings."""

    @staticmethod
    def _create_face_analysis(providers, ctx_id):
        """Build and prepare one InsightFace application instance."""
        app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=list(FACE_MODELS),
            providers=providers,
        )
        app.prepare(ctx_id=ctx_id, det_size=DETECTION_SIZE)
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
        global _active_model
        self._lock = Lock()
        providers = []
        if os.environ.get("FACE_MANAGER_CPU_ONLY") != "1":
            preload_gpu_runtime_dlls()
            try:
                if CUDA_PROVIDER in ort.get_available_providers():
                    providers.append(CUDA_PROVIDER)
            except Exception:
                logger.exception("Could not query CUDA availability")
            providers.append(INTEL_PROVIDER)
        self._remaining_providers = iter(providers)
        self._active = self._load_next_provider()
        self._shared_signature_cache = None
        _active_model = ref(self)

    def _load_next_provider(self):
        cpu_app = None
        for provider in self._remaining_providers:
            try:
                if provider == CUDA_PROVIDER:
                    app = self._create_face_analysis([CUDA_PROVIDER, CPU_PROVIDER], 0)
                else:
                    device = _find_intel_gpu()
                    if device is None:
                        continue
                    # InsightFace 0.7 forwards legacy provider names but drops custom
                    # SessionOptions. Reuse its preprocessing and model metadata and
                    # attach the plugin sessions through the public ORT API instead.
                    cpu_app = self._create_face_analysis([CPU_PROVIDER], -1)
                    options = ort.SessionOptions()
                    options.add_provider_for_devices([device], {})
                    sessions = {
                        task: ort.InferenceSession(cpu_app.models[task].model_file, sess_options=options)
                        for task in FACE_MODELS
                    }
                    original = {task: cpu_app.models[task].session for task in FACE_MODELS}
                    for task, session in sessions.items():
                        cpu_app.models[task].session = session
                    try:
                        _check_accelerated_sessions(cpu_app, provider)
                    except Exception:
                        for task, session in original.items():
                            cpu_app.models[task].session = session
                        raise
                    return cpu_app, provider
                _check_accelerated_sessions(app, provider)
                return app, provider
            except Exception:
                logger.warning("%s could not start; trying the next provider", provider, exc_info=True)
        return cpu_app or self._create_face_analysis([CPU_PROVIDER], -1), CPU_PROVIDER

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
                faces = app.get(image_np)
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
                    self._active = self._load_next_provider()
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
