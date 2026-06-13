import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Set, Tuple

import numpy as np
from PIL import Image

from ..db.schema import calculate_file_hash, get_conn
from ..models.clustering import FaceClustering
from ..models.face_model import FaceModel
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


@dataclass
class PreparedImage:
    path: Path
    normalized_path: str
    content_hash: str
    image_np: np.ndarray


def get_process_state():
    return dict(PROCESS_STATE)


def get_import_worker_count(compute_mode: str, cpu_count: int = None) -> int:
    configured = os.getenv("FACE_MANAGER_IMPORT_WORKERS")
    if configured is not None:
        try:
            return max(1, int(configured))
        except ValueError:
            print(
                "[pipeline] Ignoring invalid FACE_MANAGER_IMPORT_WORKERS="
                f"{configured!r}"
            )

    available_cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if compute_mode == "gpu":
        return min(4, available_cpus, max(2, available_cpus // 3))
    return min(2, max(1, available_cpus // 4))


def _prepare_image(path: Path) -> PreparedImage:
    normalized_path = os.path.normpath(str(path))
    content_hash = calculate_file_hash(normalized_path)
    with Image.open(path) as image:
        image_np = np.asarray(image.convert("RGB"))
    return PreparedImage(path, normalized_path, content_hash, image_np)


def _prepare_images(
    paths: Iterable[Path], worker_count: int
) -> Iterator[Tuple[Path, Any]]:
    iterator = iter(paths)
    pending = deque()

    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix="image-prep"
    ) as executor:
        for _ in range(worker_count):
            try:
                path = next(iterator)
            except StopIteration:
                break
            pending.append((path, executor.submit(_prepare_image, path)))

        while pending:
            path, future = pending.popleft()
            try:
                next_path = next(iterator)
            except StopIteration:
                pass
            else:
                pending.append((next_path, executor.submit(_prepare_image, next_path)))
            yield path, future


def _get_processed_paths(cur, image_paths: Iterable[Path]) -> Set[str]:
    normalized_paths = [os.path.normpath(str(path)) for path in image_paths]
    processed_paths = set()
    query_chunk_size = 500

    for start in range(0, len(normalized_paths), query_chunk_size):
        chunk = normalized_paths[start : start + query_chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = cur.execute(
            f"""
            SELECT location.path
            FROM image_location location
            JOIN image i ON i.id = location.image_id
            WHERE i.processed_at IS NOT NULL
              AND location.path IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        processed_paths.update(row["path"] for row in rows)

    return processed_paths


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
        processed_paths = _get_processed_paths(cur, image_paths)
        pending_images = [
            path
            for path in image_paths
            if os.path.normpath(str(path)) not in processed_paths
        ]
        skipped_images = len(image_paths) - len(pending_images)
        PROCESS_STATE["processed_images"] = skipped_images

        worker_count = get_import_worker_count(model.compute_mode)
        print(
            f"[pipeline] Preparing images with {worker_count} workers "
            f"({skipped_images} already processed)"
        )

        for completed, (img_path, prepared_future) in enumerate(
            _prepare_images(pending_images, worker_count),
            start=skipped_images + 1,
        ):
            try:
                prepared = prepared_future.result()
                normalized_path = prepared.normalized_path
                existing = cur.execute(
                    """
                    SELECT i.id, i.processed_at
                    FROM image_location location
                    JOIN image i ON i.id = location.image_id
                    WHERE location.path = ?
                    """,
                    (normalized_path,),
                ).fetchone()
                if existing and existing["processed_at"]:
                    continue

                matching_content = cur.execute(
                    "SELECT id, processed_at FROM image WHERE content_hash = ?",
                    (prepared.content_hash,),
                ).fetchone()

                if matching_content:
                    image_id = matching_content["id"]
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO image_location(
                            image_id, path, directory, filename
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            image_id,
                            normalized_path,
                            os.path.dirname(normalized_path),
                            os.path.basename(normalized_path),
                        ),
                    )
                    conn.commit()
                    if matching_content["processed_at"]:
                        continue
                elif existing:
                    image_id = existing["id"]
                    cur.execute(
                        "UPDATE image SET content_hash = ? WHERE id = ?",
                        (prepared.content_hash, image_id),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO image(
                            path, directory, filename, content_hash
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            normalized_path,
                            os.path.dirname(normalized_path),
                            os.path.basename(normalized_path),
                            prepared.content_hash,
                        ),
                    )
                    image_id = cur.lastrowid
                    cur.execute(
                        """
                        INSERT INTO image_location(
                            image_id, path, directory, filename
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            image_id,
                            normalized_path,
                            os.path.dirname(normalized_path),
                            os.path.basename(normalized_path),
                        ),
                    )

                # Release DB write locks before heavy CPU work (face detection/embedding).
                conn.commit()

                # DETECTION + EMBEDDING in einem Schritt
                faces = model.detect_and_embed(prepared.image_np)

                PROCESS_STATE["total_faces"] += len(faces)

                for f in faces:
                    x1, y1, w, h = f["bbox"]
                    emb = f["embedding"]

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
            finally:
                PROCESS_STATE["processed_images"] = completed
                print(
                    f"[pipeline] LIVE image {completed}/{len(image_paths)} processed"
                )

        conn.close()

        PROCESS_STATE["status"] = "done"
        print("[pipeline] LIVE processing finished.")

    except Exception as e:
        PROCESS_STATE["status"] = "error"
        PROCESS_STATE["last_error"] = str(e)
        print(f"[pipeline] Error in process_folder: {e}")
