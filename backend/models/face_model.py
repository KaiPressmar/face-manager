import torch
import numpy as np
from insightface.app import FaceAnalysis

class FaceModel:
    def __init__(self, device):
        self.app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider"])
        self.app.prepare(ctx_id=0, det_size=(1024, 1024))  # große Detection!

    def detect_and_embed(self, image_np):
        faces = self.app.get(image_np)
        results = []
        for f in faces:
            x1, y1, x2, y2 = f.bbox.astype(int)
            w = x2 - x1
            h = y2 - y1

            emb = f.embedding.astype(np.float32)
            emb /= np.linalg.norm(emb) + 1e-12

            results.append({
                "bbox": (x1, y1, w, h),
                "embedding": emb
            })

        return results
