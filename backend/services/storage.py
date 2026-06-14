import os
import threading
import time
from collections import defaultdict
from typing import List, Tuple

import numpy as np

from ..db.schema import get_conn

DEFAULT_CLUSTER_DISTANCE_THRESHOLD = 0.5
UNKNOWN_PERSON_LABEL = "Unbekannt"
MAX_IMAGE_PAGE_SIZE = 200
IMAGE_QUERY_CACHE_TTL_SECONDS = 5.0
_IMAGE_QUERY_CACHE_LOCK = threading.Lock()
_IMAGE_QUERY_CACHE: dict[tuple, tuple[float, object]] = {}


def _safe_float(v):
    """Convert SQLite numeric representations to a float.

    Args:
        v: Numeric value or little-endian float bytes.

    Returns:
        Converted floating-point value.

    Raises:
        TypeError: If the value uses an unsupported representation.
    """
    if isinstance(v, (float, int)):
        return float(v)
    if isinstance(v, bytes):
        import struct

        return struct.unpack("<d", v)[0]
    raise TypeError(f"Unexpected bbox type: {type(v)}")


def get_cluster_distance_threshold() -> float:
    """Return the persisted clustering distance threshold."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("cluster_distance_threshold",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_CLUSTER_DISTANCE_THRESHOLD
    try:
        return float(row["value"])
    except (TypeError, ValueError):
        return DEFAULT_CLUSTER_DISTANCE_THRESHOLD


def set_cluster_distance_threshold(value: float) -> float:
    """Persist the clustering distance threshold."""
    threshold = float(value)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("cluster_distance_threshold", str(threshold)),
    )
    conn.commit()
    conn.close()
    return threshold


def _face_row_to_dict(r):
    """Convert a face database row to an API dictionary.

    Args:
        r: Mapping containing face and image columns.

    Returns:
        JSON-compatible face representation.
    """
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
    """Trim and normalize a folder path.

    Args:
        folder_path: User-provided folder path.

    Returns:
        Platform-normalized folder path.
    """
    return os.path.normpath(folder_path.strip())


def _descendant_filter(folders):
    """Build SQL conditions selecting folders and descendants.

    Args:
        folders: Folder paths to include.

    Returns:
        SQL condition fragments and bound parameters.
    """
    conditions = []
    params = []
    for folder in dict.fromkeys(normalize_folder_path(path) for path in folders if path):
        escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            "(location.directory = ? OR location.directory LIKE ? ESCAPE '\\')"
        )
        params.extend((folder, f"{escaped}{os.sep}%"))
    return conditions, params


def _cache_get(key):
    """Return a cached value when it is still fresh."""
    with _IMAGE_QUERY_CACHE_LOCK:
        cached = _IMAGE_QUERY_CACHE.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at < time.monotonic():
            _IMAGE_QUERY_CACHE.pop(key, None)
            return None
        return value


def _cache_set(key, value):
    """Store one cached query result."""
    with _IMAGE_QUERY_CACHE_LOCK:
        _IMAGE_QUERY_CACHE[key] = (time.monotonic() + IMAGE_QUERY_CACHE_TTL_SECONDS, value)


def invalidate_image_query_cache():
    """Clear cached image query results after image-related writes."""
    with _IMAGE_QUERY_CACHE_LOCK:
        _IMAGE_QUERY_CACHE.clear()


def _normalize_person_filters(persons):
    """Normalize person names for deterministic SQL filtering."""
    return [person.strip() for person in dict.fromkeys(persons or []) if person.strip()]


def _matching_images_cte(folders=None, persons=None):
    """Build the common CTE used for image pagination queries."""
    folders = folders or []
    persons = _normalize_person_filters(persons)
    conditions, params = _descendant_filter(folders)
    location_where = f"WHERE {' OR '.join(conditions)}" if conditions else ""

    person_clause = ""
    person_params = []
    if persons:
        placeholders = ",".join("?" for _ in persons)
        person_clause = f"""
        AND i.id IN (
            SELECT f.image_id
            FROM face f
            LEFT JOIN cluster c ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE f.cluster_id IS NOT NULL
              AND COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}') IN ({placeholders})
            GROUP BY f.image_id
            HAVING COUNT(DISTINCT COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}')) = ?
        )
        """
        person_params = [*persons, len(persons)]

    sql = f"""
    WITH ranked_locations AS (
        SELECT
            location.image_id,
            location.path,
            location.directory,
            location.filename,
            location.created_at,
            ROW_NUMBER() OVER (
                PARTITION BY location.image_id ORDER BY location.path
            ) AS location_rank
        FROM image_location location
        {location_where}
    ),
    matching_images AS (
        SELECT
            i.id AS image_id,
            location.path AS image_path,
            location.directory,
            location.filename,
            location.created_at
        FROM image i
        JOIN ranked_locations location
            ON location.image_id = i.id AND location.location_rank = 1
        WHERE EXISTS (
            SELECT 1
            FROM face f
            WHERE f.image_id = i.id AND f.cluster_id IS NOT NULL
        )
        {person_clause}
    )
    """
    return sql, [*params, *person_params]


def _image_order_by(prefix: str, sort_by: str, sort_direction: str):
    """Return the ORDER BY clause for image pagination."""
    direction = "ASC" if sort_direction == "asc" else "DESC"
    if sort_by == "folder":
        return (
            f"{prefix}directory COLLATE NOCASE {direction}, "
            f"{prefix}filename COLLATE NOCASE ASC, "
            f"{prefix}image_path COLLATE NOCASE ASC"
        )
    return (
        f"{prefix}created_at IS NULL ASC, "
        f"{prefix}created_at {direction}, "
        f"{prefix}filename COLLATE NOCASE ASC, "
        f"{prefix}image_path COLLATE NOCASE ASC"
    )


def list_available_image_persons(folders=None):
    """List person names available within the current folder selection."""
    folders = folders or []
    cache_key = ("available_persons", tuple(folders))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cte, params = _matching_images_cte(folders=folders, persons=[])
    conn = get_conn()
    rows = conn.execute(
        f"""
        {cte}
        SELECT DISTINCT COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}') AS person_name
        FROM matching_images
        JOIN face f ON f.image_id = matching_images.image_id
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE f.cluster_id IS NOT NULL
        ORDER BY person_name COLLATE NOCASE
        """,
        params,
    ).fetchall()
    conn.close()
    people = [row["person_name"] for row in rows]
    _cache_set(cache_key, people)
    return people


def list_images_page(
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
    limit: int = 40,
    offset: int = 0,
):
    """List one page of images with clustered faces and preferred locations."""
    folders = folders or []
    persons = _normalize_person_filters(persons)
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    limit = max(1, min(int(limit), MAX_IMAGE_PAGE_SIZE))
    offset = max(0, int(offset))

    cache_key = (
        "image_page",
        tuple(folders),
        tuple(persons),
        sort_by,
        sort_direction,
        limit,
        offset,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cte, params = _matching_images_cte(folders=folders, persons=persons)
    matching_order = _image_order_by("matching_images.", sort_by, sort_direction)
    paged_order = _image_order_by("paged_images.", sort_by, sort_direction)

    conn = get_conn()
    total = conn.execute(
        f"""
        {cte}
        SELECT COUNT(*) AS total
        FROM matching_images
        """,
        params,
    ).fetchone()["total"]
    rows = conn.execute(
        f"""
        {cte}
        , paged_images AS (
            SELECT *
            FROM matching_images
            ORDER BY {matching_order}
            LIMIT ? OFFSET ?
        )
        SELECT
            paged_images.image_id,
            paged_images.image_path,
            paged_images.directory,
            paged_images.filename,
            paged_images.created_at,
            i.content_hash,
            (
                SELECT COUNT(*)
                FROM image_location all_locations
                WHERE all_locations.image_id = paged_images.image_id
            ) AS location_count,
            f.id AS face_id,
            f.bbox_x,
            f.bbox_y,
            f.bbox_w,
            f.bbox_h,
            f.cluster_id,
            p.name AS person_name
        FROM paged_images
        JOIN image i ON i.id = paged_images.image_id
        JOIN face f ON f.image_id = paged_images.image_id
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE f.cluster_id IS NOT NULL
        ORDER BY {paged_order}, f.id
        """,
        [*params, limit, offset],
    ).fetchall()
    conn.close()
    result = (rows, total)
    _cache_set(cache_key, result)
    return result


def list_images(folders=None):
    """List images with clustered faces and preferred locations.

    Args:
        folders: Optional folder roots used to filter results.

    Returns:
        SQLite rows containing image, face, cluster, and person data.
    """
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


def list_image_locations(image_ids):
    """List every assigned location for canonical images.

    Args:
        image_ids: Canonical image identifiers to load.

    Returns:
        Mapping from image ID to ordered location dictionaries.
    """
    unique_ids = list(dict.fromkeys(image_ids))
    if not unique_ids:
        return {}

    placeholders = ",".join("?" for _ in unique_ids)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT image_id, path, directory, filename
        FROM image_location
        WHERE image_id IN ({placeholders})
        ORDER BY image_id, path
        """,
        unique_ids,
    ).fetchall()
    conn.close()

    locations = defaultdict(list)
    for row in rows:
        locations[row["image_id"]].append(
            {
                "path": row["path"],
                "directory": row["directory"],
                "filename": row["filename"],
            }
        )
    return dict(locations)


def build_folder_tree():
    """Build the imported folder hierarchy with unique image counts.

    Returns:
        Tree roots and aggregate image and folder counts.
    """
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
        """Create a folder node and any missing ancestors.

        Args:
            path: Folder path represented by the node.

        Returns:
            Mutable folder node dictionary.
        """
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
        """Sort folder nodes recursively by display name.

        Args:
            items: Mutable list of folder node dictionaries.
        """
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
    """Find an existing filesystem location for an image.

    Args:
        image_id: Canonical image identifier.
        preferred_path: Optional location to try first.

    Returns:
        Existing path, or ``None`` when all locations are unavailable.
    """
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
    """Delete an image and clean up empty clusters.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Whether an image row was deleted.
    """
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
    if deleted:
        invalidate_image_query_cache()
    return deleted


def list_faces_by_folder(folder_path: str):
    """List clustered faces below a folder.

    Args:
        folder_path: Folder root used to filter images.

    Returns:
        Face dictionaries for matching images.
    """
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
    """List all clusters with optional assigned person names.

    Returns:
        Cluster dictionaries ordered by identifier.
    """
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
    """List faces assigned to a cluster.

    Args:
        cluster_id: Cluster identifier.

    Returns:
        Face dictionaries belonging to the cluster.
    """
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
    """Load persisted face embeddings and cluster identifiers.

    Returns:
        Embedding matrix and aligned cluster ID array.
    """
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
    """Assign a cluster to an existing or newly created person.

    Args:
        cluster_id: Cluster identifier to update.
        person_name: Unique person display name.
    """
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
    invalidate_image_query_cache()


def remove_face_from_cluster(face_id: int):
    """Remove one face from its cluster.

    Args:
        face_id: Face identifier to update.
    """
    conn = get_conn()
    conn.execute("UPDATE face SET cluster_id = NULL WHERE id = ?", (face_id,))
    conn.commit()
    conn.close()
    invalidate_image_query_cache()


def list_persons():
    """List known people alphabetically.

    Returns:
        Person identifier and name dictionaries.
    """
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM person ORDER BY name").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def get_person_faces(person_id: int):
    """List faces assigned to a person through clusters.

    Args:
        person_id: Person identifier.

    Returns:
        Face dictionaries assigned to the person.
    """
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
