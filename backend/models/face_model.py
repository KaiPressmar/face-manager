import logging

import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis

from ..error_logging import configure_error_logging

configure_error_logging()
logger = logging.getLogger("face_manager.face_model")


def preload_gpu_runtime_dlls():
    """Preload CUDA/cuDNN DLLs when ONNX Runtime exposes the helper."""
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls(directory="")
        except Exception:
            logger.exception("Could not preload ONNX Runtime GPU DLLs; continuing")


def get_execution_provider(available_providers=None):
    """Choose the preferred ONNX Runtime execution provider.

    Args:
        available_providers: Optional provider names reported by ONNX Runtime.

    Returns:
        CUDA when available, otherwise the CPU provider.
    """
    if available_providers is None:
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
        preload_gpu_runtime_dlls()
        try:
            available_providers = ort.get_available_providers()
        except Exception:
            logger.exception(
                "Could not read ONNX Runtime providers during model startup; using CPU mode"
            )
            available_providers = ["CPUExecutionProvider"]
        execution_provider = get_execution_provider(available_providers)
        if execution_provider == "CUDAExecutionProvider":
            try:
                self.app = self._create_face_analysis(
                    ["CUDAExecutionProvider", "CPUExecutionProvider"],
                    0,
                )
                self.compute_mode = "gpu"
                return
            except Exception:
                logger.exception(
                    "GPU face model startup failed; retrying with CPU execution"
                )

        self.app = self._create_face_analysis(["CPUExecutionProvider"], -1)
        self.compute_mode = "cpu"

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
