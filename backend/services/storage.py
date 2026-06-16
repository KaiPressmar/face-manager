import logging
import os
import re
import sqlite3
from collections import defaultdict
from typing import List, Tuple

import numpy as np

from ..db.schema import get_conn
from ..error_logging import (
    DEFAULT_FILE_LOG_LEVEL,
    configure_error_logging,
    normalize_file_log_level,
)
from .cache import app_cache

configure_error_logging()
logger = logging.getLogger("face_manager.storage")

DEFAULT_CLUSTER_DISTANCE_THRESHOLD = 0.5
DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT = " {names}"
DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR = " "
DEFAULT_FILENAME_PERSON_JOINER = ", "
DEFAULT_PERSISTED_FILE_LOG_LEVEL = DEFAULT_FILE_LOG_LEVEL
UNKNOWN_PERSON_LABEL = "Unbekannt"
MAX_IMAGE_PAGE_SIZE = 200
QUERY_CACHE_TTL_SECONDS = 5.0
QUERY_CACHE_TAG_IMAGES = "images"
QUERY_CACHE_TAG_FOLDERS = "folders"
QUERY_CACHE_TAG_PERSONS = "persons"
QUERY_CACHE_TAG_RENAMES = "renames"
QUERY_CACHE_TAG_CLUSTERS = "clusters"
QUERY_CACHE_TAG_SETTINGS = "settings"
QUERY_CACHE_TAG_PERSON_FACES = "person_faces"
QUERY_CACHE_TAG_CLUSTER_FACES = "cluster_faces"


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


def get_filename_person_suffix_format() -> str:
    """Return the persisted filename suffix format used for person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_suffix_format",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT
    value = (row["value"] or "").strip()
    if not value or "{names}" not in value:
        return DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT
    return value


def get_filename_person_joiner() -> str:
    """Return the separator used between multiple person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_joiner",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_JOINER
    return row["value"] if row["value"] is not None else DEFAULT_FILENAME_PERSON_JOINER


def get_filename_person_block_separator() -> str:
    """Return the separator between filename and appended person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_block_separator",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    return (
        row["value"]
        if row["value"] is not None
        else DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    )


def get_file_log_level() -> str:
    """Return the persisted log level used for the local error file."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("file_log_level",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_PERSISTED_FILE_LOG_LEVEL
    try:
        return normalize_file_log_level(row["value"])
    except ValueError:
        return DEFAULT_PERSISTED_FILE_LOG_LEVEL


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
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return threshold


def set_filename_person_suffix_format(value: str) -> str:
    """Persist the filename suffix format used for detected person names."""
    suffix_format = (value or "").strip()
    if not suffix_format or "{names}" not in suffix_format:
        raise ValueError("Filename suffix format must contain the {names} placeholder.")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_suffix_format", suffix_format),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return suffix_format


def set_filename_person_joiner(value: str) -> str:
    """Persist the separator used between person names."""
    joiner = value if value is not None else DEFAULT_FILENAME_PERSON_JOINER
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_joiner", joiner),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return joiner


def set_filename_person_block_separator(value: str) -> str:
    """Persist the separator between filename and appended person names."""
    separator = (
        value if value is not None else DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    )
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_block_separator", separator),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return separator


def set_file_log_level(value: str) -> str:
    """Persist the log level used for the local rotating error log."""
    normalized = normalize_file_log_level(value)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("file_log_level", normalized),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return normalized


def build_filename_person_format_summary(
    joiner: str | None = None,
    block_separator: str | None = None,
) -> str:
    """Return a human-readable format string for UI display and compatibility."""
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    names_placeholder = f"Name 1{joiner}Name 2"
    return f"DATEI{block_separator}{names_placeholder}.jpg"


def _dedupe_names_in_order(names):
    """Keep the first occurrence of each non-empty name."""
    seen = set()
    ordered = []
    for name in names:
        normalized = (name or "").strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _normalize_person_name_key(name: str) -> str:
    """Normalize a person name for case-insensitive comparisons."""
    return (name or "").strip().casefold()


def _extract_trailing_person_names(
    stem: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
):
    """Split a filename stem into root text and a trailing person appendix."""
    if not stem or not detected_names:
        return stem, []

    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    if not block_separator:
        return stem, []

    normalized_names = _dedupe_names_in_order(detected_names)
    if not normalized_names:
        return stem, []

    known_names = {name.casefold(): name for name in normalized_names}
    name_patterns = sorted(normalized_names, key=len, reverse=True)
    separator_pattern = re.compile(r"[\s,;:+&/|()[\]{}._-]+")

    def _parse_suffix_names(suffix_text: str):
        stripped_suffix = suffix_text.strip()
        if not stripped_suffix:
            return []

        suffix_names = []
        position = 0
        while position < len(stripped_suffix):
            matched_name = None
            for candidate in name_patterns:
                candidate_length = len(candidate)
                if stripped_suffix[position : position + candidate_length].casefold() == (
                    candidate.casefold()
                ):
                    matched_name = known_names[candidate.casefold()]
                    suffix_names.append(matched_name)
                    position += candidate_length
                    break
            if matched_name is None:
                return []
            if position >= len(stripped_suffix):
                return suffix_names
            separator_match = separator_pattern.match(stripped_suffix, position)
            if separator_match is None:
                return []
            position = separator_match.end()

    search_from = 0
    while True:
        separator_index = stem.find(block_separator, search_from)
        if separator_index < 0:
            return stem, []

        suffix_text = stem[separator_index + len(block_separator) :]
        if suffix_text:
            suffix_names = _parse_suffix_names(suffix_text)
            root_stem = stem[:separator_index]
            if suffix_names and root_stem:
                return root_stem, suffix_names

        search_from = separator_index + len(block_separator)


def build_person_filename_preview(
    filename: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
):
    """Build a rename preview for one filename based on detected people."""
    ordered_names = _dedupe_names_in_order(detected_names)
    if not ordered_names:
        return None
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner

    stem, extension = os.path.splitext(filename)
    root_stem, current_suffix_names = _extract_trailing_person_names(
        stem,
        ordered_names,
        block_separator=block_separator,
        joiner=joiner,
    )
    if not root_stem:
        root_stem = stem

    names_text = joiner.join(ordered_names)
    next_stem = f"{root_stem}{block_separator}{names_text}".strip()
    next_filename = f"{next_stem}{extension}"

    if next_filename == filename and current_suffix_names == ordered_names:
        return None

    return {
        "current_filename": filename,
        "proposed_filename": next_filename,
        "detected_person_names": ordered_names,
        "current_suffix_person_names": current_suffix_names,
    }


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


def invalidate_query_cache_tags(*tags: str) -> None:
    """Clear cached query groups by their logical data tags."""
    app_cache.invalidate_tags(*tags)


def invalidate_image_query_cache():
    """Clear cached image query results after image-related writes."""
    invalidate_query_cache_tags(
        QUERY_CACHE_TAG_IMAGES,
        QUERY_CACHE_TAG_FOLDERS,
        QUERY_CACHE_TAG_PERSONS,
        QUERY_CACHE_TAG_RENAMES,
        QUERY_CACHE_TAG_CLUSTERS,
        QUERY_CACHE_TAG_PERSON_FACES,
        QUERY_CACHE_TAG_CLUSTER_FACES,
    )


def _normalize_person_filters(persons):
    """Normalize person names for deterministic SQL filtering."""
    normalized = []
    seen = set()
    for person in persons or []:
        trimmed = (person or "").strip()
        if not trimmed:
            continue
        key = _normalize_person_name_key(trimmed)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(trimmed)
    return normalized


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
              AND LOWER(TRIM(COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}'))) IN ({placeholders})
            GROUP BY f.image_id
            HAVING COUNT(DISTINCT LOWER(TRIM(COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}')))) = ?
        )
        """
        person_params = [*[_normalize_person_name_key(person) for person in persons], len(persons)]

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

    def load_people():
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
        return [row["person_name"] for row in rows]

    return app_cache.get_or_set(
        cache_key,
        load_people,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
        },
    )


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

    def load_page():
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
        return rows, total

    return app_cache.get_or_set(
        cache_key,
        load_page,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
        },
    )


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
    def load_tree():
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
            """Create a folder node and any missing ancestors."""
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
            """Sort folder nodes recursively by display name."""
            items.sort(key=lambda item: item["name"].casefold())
            for item in items:
                sort_nodes(item["children"])

        sort_nodes(roots)
        return {
            "roots": roots,
            "image_count": len(all_image_ids),
            "folder_count": len(nodes),
        }

    return app_cache.get_or_set(
        ("folder_tree",),
        load_tree,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_FOLDERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


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


def list_cluster_summaries():
    """List compact cluster summaries for the cluster sidebar."""
    def load_clusters():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT
                c.id AS cluster_id,
                p.name AS person_name,
                COUNT(f.id) AS face_count
            FROM cluster c
            JOIN face f ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            GROUP BY c.id, p.name
            ORDER BY
                face_count DESC,
                CASE WHEN COALESCE(TRIM(p.name), '') = '' THEN 1 ELSE 0 END ASC,
                COALESCE(p.name, ?) COLLATE NOCASE ASC,
                c.id ASC
            """,
            (UNKNOWN_PERSON_LABEL,),
        ).fetchall()
        conn.close()
        return [
            {
                "cluster_id": r["cluster_id"],
                "person_name": r["person_name"],
                "face_count": int(r["face_count"]),
            }
            for r in rows
        ]

    return app_cache.get_or_set(
        ("cluster_summaries",),
        load_clusters,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def get_cluster_summary(cluster_id: int):
    """Load compact metadata for one cluster."""
    return app_cache.get_or_set(
        ("cluster_summary", int(cluster_id)),
        lambda: _load_cluster_summary(cluster_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_cluster_summary(cluster_id: int):
    """Query compact metadata for one cluster from the database."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT
            c.id AS cluster_id,
            p.name AS person_name,
            COUNT(f.id) AS face_count
        FROM cluster c
        JOIN face f ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE c.id = ?
        GROUP BY c.id, p.name
        """,
        (cluster_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "cluster_id": row["cluster_id"],
        "person_name": row["person_name"],
        "face_count": int(row["face_count"]),
    }


def get_cluster_faces(cluster_id: int):
    """List faces assigned to a cluster.

    Args:
        cluster_id: Cluster identifier.

    Returns:
        Face dictionaries belonging to the cluster.
    """
    return app_cache.get_or_set(
        ("cluster_faces", int(cluster_id)),
        lambda: _load_cluster_faces(cluster_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTER_FACES,
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_cluster_faces(cluster_id: int):
    """Load faces for one cluster from the database."""
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
    normalized_person_name = (person_name or "").strip()
    if not normalized_person_name:
        raise ValueError("Missing person_name")
    normalized_name_key = _normalize_person_name_key(normalized_person_name)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cluster_row = cur.execute(
            """
            SELECT
                c.id,
                c.person_id,
                p.id AS resolved_person_id
            FROM cluster c
            LEFT JOIN person p ON p.id = c.person_id
            WHERE c.id = ?
            """,
            (cluster_id,),
        ).fetchone()
        if cluster_row is None:
            referenced_face = cur.execute(
                "SELECT 1 FROM face WHERE cluster_id = ? LIMIT 1",
                (cluster_id,),
            ).fetchone()
            if referenced_face is None:
                raise LookupError(f"Cluster {cluster_id} not found")
            cur.execute(
                "INSERT INTO cluster(id, label, person_id) VALUES (?, NULL, NULL)",
                (cluster_id,),
            )
            logger.warning(
                "Recreated missing cluster row %s during person assignment",
                cluster_id,
            )
            cluster_row = cur.execute(
                """
                SELECT
                    c.id,
                    c.person_id,
                    p.id AS resolved_person_id
                FROM cluster c
                LEFT JOIN person p ON p.id = c.person_id
                WHERE c.id = ?
                """,
                (cluster_id,),
            ).fetchone()

        referenced_face = cur.execute(
            "SELECT 1 FROM face WHERE cluster_id = ? LIMIT 1",
            (cluster_id,),
        ).fetchone()
        if referenced_face is None:
            raise LookupError(f"Cluster {cluster_id} has no faces")

        if (
            cluster_row["person_id"] is not None
            and cluster_row["resolved_person_id"] is None
        ):
            cur.execute(
                "UPDATE cluster SET person_id = NULL WHERE id = ?",
                (cluster_id,),
            )
            logger.warning(
                "Cleared broken person reference %s from cluster %s during assignment",
                cluster_row["person_id"],
                cluster_id,
            )

        matching_people = cur.execute(
            """
            SELECT id, name
            FROM person
            WHERE LOWER(TRIM(name)) = LOWER(?)
            ORDER BY
                CASE WHEN TRIM(name) = name THEN 0 ELSE 1 END,
                CASE WHEN TRIM(name) = ? THEN 0 ELSE 1 END,
                CASE WHEN name = ? THEN 0 ELSE 1 END,
                id ASC
            """,
            (normalized_person_name, normalized_person_name, normalized_person_name),
        ).fetchall()

        person_id = None
        duplicate_person_ids = []
        for row in matching_people:
            row_name = (row["name"] or "").strip()
            if row_name.casefold() != normalized_name_key:
                continue
            if person_id is None:
                person_id = row["id"]
            else:
                duplicate_person_ids.append(row["id"])

        if person_id is None:
            cur.execute(
                "INSERT INTO person(name) VALUES (?)",
                (normalized_person_name,),
            )
            person_id = cur.lastrowid
        else:
            placeholders = ",".join("?" for _ in duplicate_person_ids)
            if duplicate_person_ids:
                cur.execute(
                    f"""
                    UPDATE cluster
                    SET person_id = ?
                    WHERE person_id IN ({placeholders})
                    """,
                    (person_id, *duplicate_person_ids),
                )
                cur.execute(
                    f"DELETE FROM person WHERE id IN ({placeholders})",
                    duplicate_person_ids,
                )
                logger.warning(
                    "Merged duplicate person rows %s into canonical person %s during cluster assignment",
                    duplicate_person_ids,
                    person_id,
                )
            canonical_name = (matching_people[0]["name"] or "").strip()
            if canonical_name != matching_people[0]["name"]:
                cur.execute(
                    "UPDATE person SET name = ? WHERE id = ?",
                    (canonical_name, person_id),
                )

        cur.execute(
            "UPDATE cluster SET person_id = ? WHERE id = ?",
            (person_id, cluster_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"Cluster {cluster_id} not found")
        conn.commit()
    finally:
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
    return app_cache.get_or_set(
        ("person_list",),
        _load_persons,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={QUERY_CACHE_TAG_PERSONS},
    )


def _load_persons():
    """Load known people from the database."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name FROM person ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def list_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
    limit: int | None = 100,
    offset: int = 0,
):
    """List image locations whose filenames should be updated with person names."""
    suffix_format = suffix_format or get_filename_person_suffix_format()
    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    if limit is not None:
        limit = max(1, min(int(limit), MAX_IMAGE_PAGE_SIZE))
    offset = max(0, int(offset))

    cache_key = (
        "filename_rename_candidates",
        suffix_format,
        block_separator,
        joiner,
        tuple(folders),
        tuple(persons),
        sort_by,
        sort_direction,
        limit,
        offset,
    )

    def load_candidates():
        conn = get_conn()
        try:
            if limit is None:
                rows = _fetch_filename_rename_candidate_rows(
                    conn,
                    folders=folders,
                    persons=persons,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    limit=None,
                    offset=0,
                )
                candidates = _build_filename_rename_candidates_from_rows(
                    rows,
                    block_separator=block_separator,
                    joiner=joiner,
                )
                return candidates, len(candidates)

            scan_size = max(MAX_IMAGE_PAGE_SIZE, limit * 4)
            raw_offset = 0
            collected_candidates = []
            target_size = offset + limit

            while len(collected_candidates) < target_size:
                rows = _fetch_filename_rename_candidate_rows(
                    conn,
                    folders=folders,
                    persons=persons,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    limit=scan_size,
                    offset=raw_offset,
                )
                if not rows:
                    break
                collected_candidates.extend(
                    _build_filename_rename_candidates_from_rows(
                        rows,
                        block_separator=block_separator,
                        joiner=joiner,
                    )
                )
                raw_offset += scan_size

            total = len(collected_candidates)
            return collected_candidates[offset : offset + limit], total
        finally:
            conn.close()

    return app_cache.get_or_set(
        cache_key,
        load_candidates,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_RENAMES,
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_SETTINGS,
        },
    )


def _filename_rename_location_order(prefix: str, sort_by: str, sort_direction: str):
    """Return the ORDER BY clause for filename rename candidate locations."""
    direction = "ASC" if sort_direction == "asc" else "DESC"
    if sort_by == "folder":
        return (
            f"{prefix}directory COLLATE NOCASE {direction}, "
            f"{prefix}filename COLLATE NOCASE ASC, "
            f"{prefix}path COLLATE NOCASE ASC"
        )
    return (
        f"{prefix}created_at IS NULL ASC, "
        f"{prefix}created_at {direction}, "
        f"{prefix}filename COLLATE NOCASE ASC, "
        f"{prefix}path COLLATE NOCASE ASC"
    )


def _fetch_filename_rename_candidate_rows(
    conn,
    *,
    folders,
    persons,
    sort_by: str,
    sort_direction: str,
    limit: int | None,
    offset: int,
):
    """Fetch ordered face rows for a slice of image locations."""
    cte, params = _matching_images_cte(folders=folders, persons=persons)
    location_order = _filename_rename_location_order(
        "matching_locations.", sort_by, sort_direction
    )
    if limit is None:
        paging_cte = ""
        from_table = "matching_locations"
        query_params = params
    else:
        paging_cte = f"""
        , paged_locations AS (
            SELECT *
            FROM matching_locations
            ORDER BY {location_order}
            LIMIT ? OFFSET ?
        )
        """
        from_table = "paged_locations"
        query_params = [*params, limit, offset]

    return conn.execute(
        f"""
        {cte}
        , matching_locations AS (
            SELECT
                location.id AS location_id,
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                location.created_at
            FROM image_location location
            JOIN matching_images
                ON matching_images.image_id = location.image_id
        )
        {paging_cte}
        SELECT
            {from_table}.location_id,
            {from_table}.image_id,
            {from_table}.path,
            {from_table}.directory,
            {from_table}.filename,
            {from_table}.created_at,
            f.id AS face_id,
            f.bbox_x,
            p.name AS person_name
        FROM {from_table}
        JOIN face f ON f.image_id = {from_table}.image_id
        JOIN cluster c ON f.cluster_id = c.id
        JOIN person p ON c.person_id = p.id
        ORDER BY {_filename_rename_location_order(f'{from_table}.', sort_by, sort_direction)}, f.bbox_x ASC, f.id ASC
        """,
        query_params,
    ).fetchall()


def _build_filename_rename_candidates_from_rows(
    rows,
    *,
    block_separator: str,
    joiner: str,
):
    """Build rename candidates from ordered face rows."""
    candidates = []
    grouped_rows = []
    current_location_rows = []
    current_location_id = None
    for row in rows:
        location_id = row["location_id"]
        if current_location_id is None or current_location_id == location_id:
            current_location_rows.append(row)
            current_location_id = location_id
            continue
        grouped_rows.append(current_location_rows)
        current_location_rows = [row]
        current_location_id = location_id
    if current_location_rows:
        grouped_rows.append(current_location_rows)

    for location_rows in grouped_rows:
        first_row = location_rows[0]
        if not os.path.isfile(first_row["path"]):
            continue
        preview = build_person_filename_preview(
            first_row["filename"],
            [row["person_name"] for row in location_rows],
            block_separator=block_separator,
            joiner=joiner,
        )
        if not preview:
            continue
        candidates.append(
            {
                "location_id": first_row["location_id"],
                "image_id": first_row["image_id"],
                "path": first_row["path"],
                "directory": first_row["directory"],
                "created_at": first_row["created_at"],
                "current_filename": preview["current_filename"],
                "proposed_filename": preview["proposed_filename"],
                "proposed_path": os.path.join(
                    first_row["directory"], preview["proposed_filename"]
                ),
                "detected_person_names": preview["detected_person_names"],
                "current_suffix_person_names": preview["current_suffix_person_names"],
            }
        )
    return candidates


def count_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Count rename candidates after preview filtering."""
    suffix_format = suffix_format or get_filename_person_suffix_format()
    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    cache_key = (
        "filename_rename_candidates_count",
        suffix_format,
        block_separator,
        joiner,
        tuple(folders),
        tuple(persons),
        sort_by,
        sort_direction,
    )
    return app_cache.get_or_set(
        cache_key,
        lambda: list_filename_rename_candidates(
            suffix_format=suffix_format,
            folders=folders,
            persons=persons,
            sort_by=sort_by,
            sort_direction=sort_direction,
            limit=None,
            offset=0,
        )[1],
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_RENAMES,
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_SETTINGS,
        },
    )


def list_filename_rename_candidates_for_paths(
    paths: list[str] | None,
    *,
    suffix_format: str | None = None,
    folders=None,
    persons=None,
):
    """Build rename candidates for specific image paths only."""
    requested_paths = list(dict.fromkeys(path for path in (paths or []) if path))
    if not requested_paths:
        return []

    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    placeholders = ",".join("?" for _ in requested_paths)

    conn = get_conn()
    try:
        cte, params = _matching_images_cte(folders=folders, persons=persons)
        rows = conn.execute(
            f"""
            {cte}
            SELECT
                location.id AS location_id,
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                location.created_at,
                f.id AS face_id,
                f.bbox_x,
                p.name AS person_name
            FROM image_location location
            JOIN matching_images
                ON matching_images.image_id = location.image_id
            JOIN face f ON f.image_id = location.image_id
            JOIN cluster c ON f.cluster_id = c.id
            JOIN person p ON c.person_id = p.id
            WHERE location.path IN ({placeholders})
            ORDER BY location.path COLLATE NOCASE ASC, f.bbox_x ASC, f.id ASC
            """,
            [*params, *requested_paths],
        ).fetchall()
    finally:
        conn.close()

    candidates = _build_filename_rename_candidates_from_rows(
        rows,
        block_separator=block_separator,
        joiner=joiner,
    )
    candidate_by_path = {candidate["path"]: candidate for candidate in candidates}
    return [
        candidate_by_path[path]
        for path in requested_paths
        if path in candidate_by_path
    ]


def list_all_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Return the complete rename candidate list without pagination."""
    candidates, _ = list_filename_rename_candidates(
        suffix_format=suffix_format,
        folders=folders,
        persons=persons,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=None,
        offset=0,
    )
    return candidates


def _refresh_canonical_image_location(cur, image_id: int):
    """Keep the legacy canonical image columns aligned with image locations."""
    row = cur.execute(
        """
        SELECT path, directory, filename
        FROM image_location
        WHERE image_id = ?
        ORDER BY path COLLATE NOCASE ASC
        LIMIT 1
        """,
        (image_id,),
    ).fetchone()
    if row:
        cur.execute(
            """
            UPDATE image
            SET path = ?, directory = ?, filename = ?
            WHERE id = ?
            """,
            (row["path"], row["directory"], row["filename"], image_id),
        )


def rename_image_locations_to_match_people(
    selected_paths: list[str] | None = None,
    rename_all: bool = False,
    excluded_paths: list[str] | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Rename selected candidate image locations and sync database metadata."""
    selected_paths = list(dict.fromkeys(selected_paths or []))
    excluded = {path for path in (excluded_paths or []) if path}

    if rename_all:
        candidates = list_all_filename_rename_candidates(
            suffix_format=None,
            folders=folders,
            persons=persons,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
        target_candidates = [
            candidate
            for candidate in candidates
            if candidate["path"] not in excluded
        ]
    else:
        target_candidates = list_filename_rename_candidates_for_paths(
            selected_paths,
            suffix_format=None,
            folders=folders,
            persons=persons,
        )

    renamed = []
    skipped = []
    errors = []

    for candidate in target_candidates:
        source_path = candidate["path"]
        target_path = candidate["proposed_path"]
        if source_path == target_path:
            skipped.append({"path": source_path, "reason": "already_matches"})
            continue
        if not os.path.isfile(source_path):
            skipped.append({"path": source_path, "reason": "missing_source"})
            continue
        if os.path.exists(target_path):
            skipped.append({"path": source_path, "reason": "target_exists"})
            continue

        try:
            os.rename(source_path, target_path)
        except OSError as exc:
            logger.exception(
                "Could not rename image from %s to %s",
                source_path,
                target_path,
            )
            errors.append({"path": source_path, "reason": str(exc)})
            continue

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE image_location
                SET path = ?, filename = ?
                WHERE id = ?
                """,
                (target_path, candidate["proposed_filename"], candidate["location_id"]),
            )
            _refresh_canonical_image_location(cur, candidate["image_id"])
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            logger.exception(
                "Database update failed after renaming %s to %s",
                source_path,
                target_path,
            )
            try:
                os.rename(target_path, source_path)
            except OSError:
                logger.exception(
                    "Could not roll back file rename from %s to %s",
                    target_path,
                    source_path,
                )
                pass
            errors.append({"path": source_path, "reason": str(exc)})
            conn.close()
            continue
        conn.close()
        renamed.append(
            {
                "from_path": source_path,
                "to_path": target_path,
                "image_id": candidate["image_id"],
            }
        )

    if renamed:
        invalidate_image_query_cache()

    return {
        "renamed": renamed,
        "skipped": skipped,
        "errors": errors,
        "renamed_count": len(renamed),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


def get_person_faces(person_id: int):
    """List faces assigned to a person through clusters.

    Args:
        person_id: Person identifier.

    Returns:
        Face dictionaries assigned to the person.
    """
    return app_cache.get_or_set(
        ("person_faces", int(person_id)),
        lambda: _load_person_faces(person_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_PERSON_FACES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_person_faces(person_id: int):
    """Load faces assigned to a person through clusters."""
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
