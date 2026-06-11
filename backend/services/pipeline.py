import os
from pathlib import Path
from typing import Dict, Any

from PIL import Image
import numpy as np

from ..models.face_model import FaceModel
from ..models.clustering import FaceClustering
from ..db.schema import get_conn
from .storage import load_all_embeddings

face_model = None
clusterer = FaceClustering()

PROCESS_STATE: Dict[str, Any] = {
    "status": "idle",
    "total_images": 0,
    "processed_images": 0,
    "total_faces": 0,
    "processed_faces": 0,
    "last_error": None,
}

_loaded_existing = False


def get_process_state():
    return dict(PROCESS_STATE)


def _ensure_face_model_loaded():
    global face_model
    if face_model is None:
        face_model = FaceModel()
    return face_model


def _ensure_clusterer_loaded():
    global _loaded_existing
    if _loaded_existing:
        return

    embs, cids = load_all_embeddings()
    if embs.size > 0:
        clusterer.load_existing(embs, cids)
        print(f"[pipeline] Existing embeddings loaded: {len(cids)}")
    else:
        print("[pipeline] No existing embeddings to load.")

    _loaded_existing = True


def process_folder(folder_path: str):
    try:
        _ensure_clusterer_loaded()
        model = _ensure_face_model_loaded()

        PROCESS_STATE.update(
            {
                "status": "running",
                "total_images": 0,
                "processed_images": 0,
                "total_faces": 0,
                "processed_faces": 0,
                "last_error": None,
            }
        )

        folder = Path(folder_path)
        image_paths = [
            p
            for p in folder.rglob("*")
            if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
        ]

        PROCESS_STATE["total_images"] = len(image_paths)
        print(f"[pipeline] LIVE processing {len(image_paths)} images in {folder_path}")

        conn = get_conn()
        cur = conn.cursor()

        for idx, img_path in enumerate(image_paths, start=1):
            try:
                normalized_path = os.path.normpath(str(img_path))
                existing = cur.execute(
                    "SELECT id, processed_at FROM image WHERE path = ?",
                    (normalized_path,),
                ).fetchone()
                if existing and existing["processed_at"]:
                    PROCESS_STATE["processed_images"] = idx
                    continue

                if existing:
                    image_id = existing["id"]
                else:
                    cur.execute(
                        """
                        INSERT INTO image(path, directory, filename)
                        VALUES (?, ?, ?)
                        """,
                        (
                            normalized_path,
                            os.path.dirname(normalized_path),
                            os.path.basename(normalized_path),
                        ),
                    )
                    image_id = cur.lastrowid

                img = Image.open(img_path).convert("RGB")
                img_np = np.array(img)

                # DETECTION + EMBEDDING in einem Schritt
                faces = model.detect_and_embed(img_np)

                PROCESS_STATE["total_faces"] += len(faces)

                for f in faces:
                    x1, y1, w, h = f["bbox"]
                    emb = f["embedding"]
                    emb /= np.linalg.norm(emb) + 1e-12

                    # LIVE CLUSTERING pro Face
                    cid, _ = clusterer.add_and_assign(np.expand_dims(emb, axis=0))
                    cid = int(cid[0])

                    # Cluster in DB anlegen
                    cur.execute(
                        "INSERT OR IGNORE INTO cluster(id, label) VALUES (?, ?)",
                        (cid, f"Cluster {cid}"),
                    )

                    # Face sofort speichern
                    cur.execute(
                        """
                        INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            image_id,
                            float(x1),
                            float(y1),
                            float(w),
                            float(h),
                            cid,
                            emb.astype("float32").tobytes(),
                        ),
                    )

                    PROCESS_STATE["processed_faces"] += 1

                cur.execute(
                    "UPDATE image SET processed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (image_id,),
                )
                conn.commit()

            except Exception as e:
                print(f"[pipeline] Error on image {img_path}: {e}")

            PROCESS_STATE["processed_images"] = idx
            print(f"[pipeline] LIVE image {idx}/{len(image_paths)} processed")

        conn.close()

        PROCESS_STATE["status"] = "done"
        print("[pipeline] LIVE processing finished.")

    except Exception as e:
        PROCESS_STATE["status"] = "error"
        PROCESS_STATE["last_error"] = str(e)
        print(f"[pipeline] Error in process_folder: {e}")
