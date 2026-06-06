from typing import List, Tuple
import numpy as np
import logging

from ..db.schema import get_conn

logger = logging.getLogger(__name__)


def _safe_float(v):
    if isinstance(v, float) or isinstance(v, int):
        return float(v)
    if isinstance(v, bytes):
        # SQLite stored it as an 8‑byte little‑endian float64
        import struct

        return struct.unpack("<d", v)[0]
    raise TypeError(f"Unexpected bbox type: {type(v)}")


def _face_row_to_dict(r):
    return {
        "id": r["id"],
        "image_path": r["image_path"],
        "bbox_x": _safe_float(r["bbox_x"]),
        "bbox_y": _safe_float(r["bbox_y"]),
        "bbox_w": _safe_float(r["bbox_w"]),
        "bbox_h": _safe_float(r["bbox_h"]),
        "cluster_id": r["cluster_id"],
    }


def list_faces_by_folder(folder_path: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id
        FROM face
        WHERE image_path LIKE ?
    """,
        (folder_path + "%",),
    )
    rows = cur.fetchall()
    conn.close()
    return [_face_row_to_dict(r) for r in rows]


def list_clusters():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.label, p.name AS person_name
        FROM cluster c
        LEFT JOIN person p ON c.person_id = p.id
        ORDER BY c.id
    """)
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "label": r["label"],
            "person_name": r["person_name"],
        }
        for r in rows
    ]


def get_cluster_faces(cluster_id: int):

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, embedding
        FROM face
        WHERE cluster_id = ?
    """,
        (cluster_id,),
    )
    rows = cur.fetchall()
    conn.close()

    # Debug: check for bytes
    for idx, r in enumerate(rows):
        for key in r.keys():
            val = r[key]
            if isinstance(val, bytes):
                logger.error(
                    "get_cluster_faces: row %d column %s is bytes (len=%d)",
                    idx,
                    key,
                    len(val),
                )

    faces = [_face_row_to_dict(r) for r in rows]
    return faces


def load_all_embeddings() -> Tuple[np.ndarray, np.ndarray]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT embedding, cluster_id
        FROM face
        WHERE embedding IS NOT NULL AND cluster_id IS NOT NULL
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return np.empty((0, 512), dtype=np.float32), np.empty((0,), dtype=int)

    embs: List[np.ndarray] = []
    cids: List[int] = []
    for r in rows:
        emb_bytes = r["embedding"]
        if emb_bytes is None:
            continue
        emb = np.frombuffer(emb_bytes, dtype=np.float32)
        embs.append(emb)
        cids.append(int(r["cluster_id"]))

    return np.vstack(embs), np.array(cids, dtype=int)


def assign_cluster_to_person(cluster_id: int, person_name: str):
    conn = get_conn()
    cur = conn.cursor()

    # Person existiert?
    cur.execute("SELECT id FROM person WHERE name = ?", (person_name,))
    row = cur.fetchone()

    if row:
        person_id = row["id"]
    else:
        cur.execute("INSERT INTO person(name) VALUES (?)", (person_name,))
        person_id = cur.lastrowid

    # Cluster aktualisieren
    cur.execute(
        "UPDATE cluster SET person_id = ? WHERE id = ?", (person_id, cluster_id)
    )

    conn.commit()
    conn.close()


def remove_face_from_cluster(face_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE face SET cluster_id = NULL WHERE id = ?", (face_id,))
    conn.commit()
    conn.close()


def list_persons():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM person ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def get_person_faces(person_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.id, f.image_path, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id
        FROM face f
        JOIN cluster c ON f.cluster_id = c.id
        WHERE c.person_id = ?
    """,
        (person_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [_face_row_to_dict(r) for r in rows]
