import logging
import os
from weakref import ref

import numpy as np
_ANDROID = os.environ.get("FACE_MANAGER_PLATFORM") == "android"
if not _ANDROID:
    import onnxruntime as ort
    from insightface.app import FaceAnalysis
else:
    ort = None

from ..error_logging import configure_error_logging

configure_error_logging()
logger = logging.getLogger("face_manager.face_model")
_active_model = None


def preload_gpu_runtime_dlls():
    """Preload CUDA/cuDNN DLLs when ONNX Runtime exposes the helper."""
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls(directory="")
        except Exception:
            logger.exception("Could not preload ONNX Runtime GPU DLLs; continuing")


def get_execution_provider(available_providers=None):
    """Report the loaded model's provider, or select one before model startup.

    Args:
        available_providers: Optional provider names reported by ONNX Runtime.

    Returns:
        CUDA when effective for both loaded sessions, or available before startup.
    """
    if _ANDROID:
        return "CPUExecutionProvider"
    if available_providers is None:
        active_model_ref = _active_model
        active_model = active_model_ref() if active_model_ref is not None else None
        if active_model is not None:
            return active_model.execution_provider
        preload_gpu_runtime_dlls()
        try:
            available_providers = ort.get_available_providers()
        except Exception:
            logger.exception(
                "Could not query ONNX Runtime providers; falling back to CPU execution"
            )
            return "CPUExecutionProvider"
    if "CUDAExecutionProvider" in available_providers:
        return "CUDAExecutionProvider"
    return "CPUExecutionProvider"


def get_compute_mode(execution_provider=None):
    """Map an ONNX Runtime provider to a UI-facing compute mode.

    Args:
        execution_provider: Optional provider name to map.

    Returns:
        Either ``gpu`` or ``cpu``.
    """
    if execution_provider is None:
        execution_provider = get_execution_provider()
    return "gpu" if execution_provider == "CUDAExecutionProvider" else "cpu"


class FaceModel:
    """Detect faces and create normalized recognition embeddings."""

    @staticmethod
    def _create_face_analysis(providers, ctx_id):
        """Build and prepare one InsightFace application instance."""
        app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        app.prepare(ctx_id=ctx_id, det_size=(1024, 1024))
        return app

    def __init__(self):
        """Load only the detection and recognition InsightFace modules."""
        global _active_model
        preload_gpu_runtime_dlls()
        try:
            available_providers = ort.get_available_providers()
        except Exception:
            logger.exception(
                "Could not read ONNX Runtime providers during model startup; using CPU mode"
            )
            available_providers = ["CPUExecutionProvider"]
        self._requested_provider = get_execution_provider(available_providers)
        if self._requested_provider == "CUDAExecutionProvider":
            try:
                self.app = self._create_face_analysis(
                    ["CUDAExecutionProvider", "CPUExecutionProvider"],
                    0,
                )
                _active_model = ref(self)
                return
            except Exception:
                logger.exception(
                    "GPU face model startup failed; retrying with CPU execution"
                )

        self.app = self._create_face_analysis(["CPUExecutionProvider"], -1)
        self._requested_provider = "CPUExecutionProvider"
        _active_model = ref(self)

    @property
    def execution_provider(self):
        # ONNX Runtime can return a CPU session even when CUDA was requested,
        # and can switch providers later after an inference error.
        if self._requested_provider == "CUDAExecutionProvider" and all(
            name in self.app.models
            and "CUDAExecutionProvider" in self.app.models[name].session.get_providers()
            for name in ("detection", "recognition")
        ):
            return "CUDAExecutionProvider"
        return "CPUExecutionProvider"

    @property
    def compute_mode(self):
        return get_compute_mode(self.execution_provider)

    def detect_and_embed(self, image_np):
        """Detect faces and calculate normalized embeddings.

        Args:
            image_np: RGB image represented as a NumPy array.

        Returns:
            Face dictionaries containing bounding boxes and embeddings.
        """
        faces = self.app.get(image_np)
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
