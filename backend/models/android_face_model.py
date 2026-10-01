"""Android inference using the bundled native detector and desktop ArcFace weights."""

import numpy as np


class AndroidFaceModel:
    compute_mode = "cpu"

    def __init__(self):
        from java import jclass

        self._engine = jclass("de.face_manager.app.FaceEngine").getInstance()

    def detect_and_embed(self, image_np):
        from java import jarray, jbyte

        values = np.asarray(image_np)
        if values.ndim != 3 or values.shape[2] != 3 or values.dtype != np.uint8:
            raise ValueError("Expected an RGB uint8 image")
        height, width = values.shape[:2]
        pixels = np.ascontiguousarray(values).tobytes()
        detections = self._engine.detectRgb(jarray(jbyte)(pixels), width, height)
        result = []
        # Chaquopy implements Python iteration for Java arrays, not java.util.List.
        # FaceEngine returns a Java List, so cross that boundary explicitly.
        for detection in detections.toArray():
            box = detection.bounds
            x1, y1, x2, y2 = map(int, (box.left, box.top, box.right, box.bottom))
            result.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "embedding": np.asarray(detection.embedding, dtype=np.float32),
            })
        return result
