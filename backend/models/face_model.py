import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis


def get_execution_provider(available_providers=None):
    """Choose the preferred ONNX Runtime execution provider.

    Args:
        available_providers: Optional provider names reported by ONNX Runtime.

    Returns:
        CUDA when available, otherwise the CPU provider.
    """
    if available_providers is None:
        available_providers = ort.get_available_providers()
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

    def __init__(self):
        """Load only the detection and recognition InsightFace modules."""
        available_providers = ort.get_available_providers()
        execution_provider = get_execution_provider(available_providers)
        self.compute_mode = get_compute_mode(execution_provider)
        if (
            execution_provider == "CUDAExecutionProvider"
            and hasattr(ort, "preload_dlls")
        ):
            ort.preload_dlls(directory="")
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if execution_provider == "CUDAExecutionProvider"
            else ["CPUExecutionProvider"]
        )
        ctx_id = 0 if execution_provider == "CUDAExecutionProvider" else -1
        self.app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        self.app.prepare(ctx_id=ctx_id, det_size=(1024, 1024))

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
