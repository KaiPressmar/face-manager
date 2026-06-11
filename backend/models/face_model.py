import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis


class FaceModel:
    def __init__(self):
        available_providers = ort.get_available_providers()
        if (
            "CUDAExecutionProvider" in available_providers
            and hasattr(ort, "preload_dlls")
        ):
            ort.preload_dlls(directory="")
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in available_providers
            else ["CPUExecutionProvider"]
        )
        ctx_id = 0 if providers[0] == "CUDAExecutionProvider" else -1
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        self.app.prepare(ctx_id=ctx_id, det_size=(1024, 1024))

    def detect_and_embed(self, image_np):
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
