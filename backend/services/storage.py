import os
from collections import defaultdict
from typing import List, Tuple

import numpy as np

from ..db.schema import get_conn


def _safe_float(v):
    if isinstance(v, (float, int)):
        return float(v)
    if isinstance(v, bytes):
        import struct

        return struct.unpack("<d", v)[0]
    raise TypeError(f"Unexpected bbox type: {type(v)}")


def _face_row_to_dict(r):
    return {
        "id": r["id"],
        "image_id": r["image_id"],
        "image_path": r["image_path"],
        "bbox_x": _safe_float(r["bbox_x"]),
        "bbox_y": _safe_float(r["bbox_y"]),
        "bbox_w": _safe_float(r["bbox_w"]),
        "bbox_h": _safe_float(r["bbox_h"]),
        "cluster_id": r["cluster_id"],
    }


def normalize_folder_path(folder_path: str):
    return os.path.normpath(folder_path.strip())


def _descendant_filter(folders):
    conditions = []
    params = []
    for folder in dict.fromkeys(normalize_folder_path(path) for path in folders if path):
        escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            "(location.directory = ? OR location.directory LIKE ? ESCAPE '\\')"
        )
        params.extend((folder, f"{escaped}{os.sep}%"))
    return conditions, params


def list_images(folders=None):
    folders = folders or []
    conditions, params = _descendant_filter(folders)
    where = ["f.cluster_id IS NOT NULL"]
    location_where = f"WHERE {' OR '.join(conditions)}" if conditions else ""

    conn = get_conn()
    rows = conn.execute(
        f"""
        WITH ranked_locations AS (
            SELECT
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                ROW_NUMBER() OVER (
                    PARTITION BY location.image_id ORDER BY location.path
                ) AS location_rank
            FROM image_location location
            {location_where}
        )
        SELECT
            i.id AS image_id,
            location.path AS image_path,
            location.directory,
            location.filename,
            i.content_hash,
            (
                SELECT COUNT(*) FROM image_location all_locations
                WHERE all_locations.image_id = i.id
            ) AS location_count,
            f.id AS face_id,
            f.bbox_x,
            f.bbox_y,
            f.bbox_w,
            f.bbox_h,
            f.cluster_id,
            p.name AS person_name
        FROM image i
        JOIN ranked_locations location
            ON location.image_id = i.id AND location.location_rank = 1
        JOIN face f ON f.image_id = i.id
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY location.path, f.id
        """,
        params,
    ).fetchall()
    conn.close()
    return rows


def build_folder_tree():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT location.image_id, location.directory
        FROM image_location location
        JOIN image i ON i.id = location.image_id
        WHERE i.processed_at IS NOT NULL
        ORDER BY location.directory
        """
    ).fetchall()
    conn.close()

    direct_images = defaultdict(set)
    all_image_ids = set()
    for row in rows:
        direct_images[row["directory"]].add(row["image_id"])
        all_image_ids.add(row["image_id"])

    nodes = {}

    def ensure_node(path):
        if path in nodes:
            return nodes[path]
        parent = os.path.dirname(path) if path != os.path.dirname(path) else None
        node = {
            "path": path,
            "name": os.path.basename(path) or path,
            "direct_image_count": len(direct_images.get(path, set())),
            "image_count": 0,
            "children": [],
            "_parent": parent,
        }
        nodes[path] = node
        if parent is not None:
            ensure_node(parent)
        return node

    for directory in direct_images:
        ensure_node(directory)

    totals = defaultdict(set)
    for directory, image_ids in direct_images.items():
        current = directory
        while True:
            totals[current].update(image_ids)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    roots = []
    for path, node in nodes.items():
        node["image_count"] = len(totals[path])
        parent = node.pop("_parent")
        if parent is None or parent not in nodes:
            roots.append(node)
        else:
            nodes[parent]["children"].append(node)

    def sort_nodes(items):
        items.sort(key=lambda item: item["name"].casefold())
        for item in items:
            sort_nodes(item["children"])

    sort_nodes(roots)
    return {
        "roots": roots,
        "image_count": len(all_image_ids),
        "folder_count": len(nodes),
    }


def get_available_image_path(image_id: int, preferred_path=None):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT path
        FROM image_location
        WHERE image_id = ?
        ORDER BY CASE WHEN path = ? THEN 0 ELSE 1 END, path
        """,
        (image_id, preferred_path),
    ).fetchall()
    conn.close()
    return next((row["path"] for row in rows if os.path.isfile(row["path"])), None)


def delete_image(image_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM image WHERE id = ?", (image_id,))
    deleted = cur.rowcount > 0
    if deleted:
        cur.execute(
            """
            DELETE FROM cluster
            WHERE NOT EXISTS (
                SELECT 1 FROM face WHERE face.cluster_id = cluster.id
            )
            """
        )
        conn.commit()
    conn.close()
    return deleted


def list_faces_by_folder(folder_path: str):
    rows = list_images([folder_path])
    return [
        _face_row_to_dict(
            {
                "id": row["face_id"],
                "image_id": row["image_id"],
                "image_path": row["image_path"],
                "bbox_x": row["bbox_x"],
                "bbox_y": row["bbox_y"],
                "bbox_w": row["bbox_w"],
                "bbox_h": row["bbox_h"],
                "cluster_id": row["cluster_id"],
            }
        )
        for row in rows
    ]


def list_clusters():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT c.id, c.label, p.name AS person_name
        FROM cluster c
        LEFT JOIN person p ON c.person_id = p.id
        ORDER BY c.id
        """
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "label": r["label"], "person_name": r["person_name"]}
        for r in rows
    ]


def get_cluster_faces(cluster_id: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            f.id, f.image_id, i.path AS image_path,
            f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id
        FROM face f
        JOIN image i ON i.id = f.image_id
        WHERE f.cluster_id = ?
        """,
        (cluster_id,),
    ).fetchall()
    conn.close()
    return [_face_row_to_dict(r) for r in rows]


def load_all_embeddings() -> Tuple[np.ndarray, np.ndarray]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT embedding, cluster_id
        FROM face
        WHERE embedding IS NOT NULL AND cluster_id IS NOT NULL
        """
    ).fetchall()
    conn.close()

    if not rows:
        return np.empty((0, 512), dtype=np.float32), np.empty((0,), dtype=int)

    embs: List[np.ndarray] = []
    cids: List[int] = []
    for r in rows:
        if r["embedding"] is None:
            continue
        embs.append(np.frombuffer(r["embedding"], dtype=np.float32))
        cids.append(int(r["cluster_id"]))

    return np.vstack(embs), np.array(cids, dtype=int)


def assign_cluster_to_person(cluster_id: int, person_name: str):
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM person WHERE name = ?", (person_name,)).fetchone()
    if row:
        person_id = row["id"]
    else:
        cur.execute("INSERT INTO person(name) VALUES (?)", (person_name,))
        person_id = cur.lastrowid
    cur.execute("UPDATE cluster SET person_id = ? WHERE id = ?", (person_id, cluster_id))
    conn.commit()
    conn.close()


def remove_face_from_cluster(face_id: int):
    conn = get_conn()
    conn.execute("UPDATE face SET cluster_id = NULL WHERE id = ?", (face_id,))
    conn.commit()
    conn.close()


def list_persons():
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM person ORDER BY name").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def get_person_faces(person_id: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            f.id, f.image_id, i.path AS image_path,
            f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id
        FROM face f
        JOIN image i ON i.id = f.image_id
        JOIN cluster c ON f.cluster_id = c.id
        WHERE c.person_id = ?
        """,
        (person_id,),
    ).fetchall()
    conn.close()
    return [_face_row_to_dict(r) for r in rows]
