"""Capture a local library's registered records for a single shared folder."""

from __future__ import annotations

import math
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID, uuid5

from .shared_sync_merge import Assignment, Box, FaceEvidence


MAX_SNAPSHOT_PAGE = 256
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_DEVICE_NAMESPACE = UUID("ad185f51-2ffa-40e9-af8a-80aa44173216")
_PORTABLE_PATH_FORBIDDEN = frozenset('\\:<>"|?*')
_WINDOWS_DEVICE_NAME = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SnapshotPhoto:
    image_id: int  # Local SQLite key, excluded from any published snapshot.
    photo_sha256: str
    relative_paths: tuple[str, ...]
    faces: tuple[FaceEvidence, ...]
    raw_width: int | None
    raw_height: int | None
    exif_orientation: int | None


@dataclass(frozen=True, slots=True)
class SnapshotPage:
    records: tuple[SnapshotPhoto, ...]
    next_image_id: int | None
    skipped_images: int


def snapshot_device_namespace(device_id: str) -> UUID:
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("A stable device identity is required")
    return uuid5(_DEVICE_NAMESPACE, device_id)


def snapshot_face_id(namespace: UUID, face_id: int) -> str:
    return str(uuid5(namespace, f"face/{face_id}"))


def _capture_context(conn: sqlite3.Connection, source_path: str | os.PathLike[str], device_id: str) -> tuple[str, UUID]:
    if not conn.in_transaction:
        raise ValueError("Library capture requires a caller-owned read transaction")
    root = os.fspath(source_path)
    if not isinstance(root, str) or not os.path.isabs(root) or "\x00" in root:
        raise ValueError("A local absolute source path is required")
    return os.path.normpath(root), snapshot_device_namespace(device_id)


def _relative_path(path: str, root: str) -> str | None:
    """Check lexical membership in a root already validated by the caller."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        return None
    path_parts = path.replace(os.altsep, os.sep).split(os.sep) if os.altsep else path.split(os.sep)
    if ".." in path_parts:
        return None
    local = os.path.normpath(path)
    checked_path, checked_root = os.path.normcase(local), os.path.normcase(root)
    try:
        if os.path.commonpath((checked_path, checked_root)) != checked_root:
            return None
        relative = os.path.relpath(local, root)
    except ValueError:
        return None
    parts = relative.split(os.sep)
    if any(
        part in ("", ".", "..")
        or part.casefold() == ".fm"
        or part.endswith((".", " "))
        or _WINDOWS_DEVICE_NAME.fullmatch(part.split(".", 1)[0].rstrip())
        or any(character in _PORTABLE_PATH_FORBIDDEN or ord(character) < 32 for character in part)
        for part in parts
    ):
        return None
    return "/".join(parts)


def _geometry(row: sqlite3.Row) -> tuple[int | None, int | None, int | None]:
    width, height, orientation = row["raw_width"], row["raw_height"], row["exif_orientation"]
    if (
        type(width) is int and width > 0 and type(height) is int and height > 0
        and type(orientation) is int and 1 <= orientation <= 8
    ):
        return width, height, orientation
    return None, None, None


def _bbox(row: sqlite3.Row, geometry: tuple[int | None, int | None, int | None]) -> Box | None:
    width, height, _ = geometry
    if width is None or height is None:
        return None
    values = tuple(row[key] for key in ("bbox_x", "bbox_y", "bbox_w", "bbox_h"))
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        return None
    x, y, box_width, box_height = values
    right, bottom = x + box_width, y + box_height
    if not (0 <= x < right <= width and 0 <= y < bottom <= height):
        return None
    return x / width, y / height, right / width, bottom / height


def _face_evidence(
    row: sqlite3.Row,
    device_namespace: UUID,
    digest: str,
    geometry: tuple[int | None, int | None, int | None],
) -> FaceEvidence:
    decision = None
    status = row["review_status"]
    if status in ("unknown_person", "not_face"):
        decision = Assignment("archived", archive_status=status, provenance="legacy")
    elif status == "active" and row["person_id"] is not None:
        name = row["person_name"] or ""
        decision = Assignment(
            "person",
            person_key=str(uuid5(device_namespace, f"person/{row['person_id']}")),
            person_name=name if name.strip() else None,
            provenance="legacy",
        )
    box = _bbox(row, geometry)
    return FaceEvidence(
        snapshot_face_id(device_namespace, row["face_id"]),
        digest,
        box,
        decision,
        photo_verified=True,
        geometry_reliable=box is not None,
    )


def _capture_records(
    conn: sqlite3.Connection, root: str, device_namespace: UUID, rows: list[sqlite3.Row],
) -> tuple[SnapshotPhoto, ...]:
    if not rows:
        return ()
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in ids)
    locations = defaultdict(list)
    for row in conn.execute(
        f"SELECT image_id, path FROM image_location WHERE image_id IN ({placeholders}) ORDER BY path",
        ids,
    ):
        locations[row["image_id"]].append(_relative_path(row["path"], root))

    verified = {}
    for row in rows:
        digest = row["content_hash"]
        relative = locations.get(row["id"])
        if isinstance(digest, str) and _SHA256.fullmatch(digest) and relative and all(relative):
            verified[row["id"]] = (digest.lower(), tuple(sorted(set(relative))), _geometry(row))
    if not verified:
        return ()

    faces = defaultdict(list)
    placeholders = ",".join("?" for _ in verified)
    for row in conn.execute(
        f"""
        SELECT f.image_id, f.id AS face_id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.review_status,
               p.id AS person_id, p.name AS person_name
        FROM face f
        LEFT JOIN cluster c ON c.id = f.cluster_id
        LEFT JOIN person p ON p.id = c.person_id
        WHERE f.image_id IN ({placeholders})
        ORDER BY f.image_id, f.id
        """,
        tuple(verified),
    ):
        digest, _, geometry = verified[row["image_id"]]
        faces[row["image_id"]].append(_face_evidence(row, device_namespace, digest, geometry))
    return tuple(
        SnapshotPhoto(image_id, digest, relative, tuple(faces[image_id]), *geometry)
        for image_id, (digest, relative, geometry) in verified.items()
    )


def capture_library(
    conn: sqlite3.Connection,
    source_path: str | os.PathLike[str],
    device_id: str,
    *,
    after_image_id: int = 0,
    limit: int = 64,
) -> SnapshotPage:
    """Read one photo page from an open caller transaction using sqlite3.Row.

    The root must be a verified scope on this device. This function does no
    filesystem I/O and never infers mount or symlink identity. A registered hash
    identifies historical content, even if a path later changes. Every known
    location must be inside the scope before any face data can be captured.
    """
    root, namespace = _capture_context(conn, source_path, device_id)
    if type(after_image_id) is not int or after_image_id < 0:
        raise ValueError("The snapshot cursor must be a nonnegative image ID")
    if type(limit) is not int or not 1 <= limit <= MAX_SNAPSHOT_PAGE:
        raise ValueError("The snapshot page limit is invalid")
    rows = conn.execute(
        """
        SELECT id, content_hash, raw_width, raw_height, exif_orientation
        FROM image
        WHERE id > ? AND processed_at IS NOT NULL
        ORDER BY id
        LIMIT ?
        """,
        (after_image_id, limit + 1),
    ).fetchall()
    page = rows[:limit]
    next_image_id = page[-1]["id"] if len(rows) > limit else None
    records = _capture_records(conn, root, namespace, page)
    return SnapshotPage(records, next_image_id, len(page) - len(records))


def capture_image(
    conn: sqlite3.Connection, source_path: str | os.PathLike[str], device_id: str, image_id: int,
) -> SnapshotPhoto | None:
    """Read a single changed image with the same scope and trust rules."""
    return capture_images(conn, source_path, device_id, (image_id,)).get(image_id)


def capture_images(
    conn: sqlite3.Connection, source_path: str | os.PathLike[str], device_id: str, image_ids: tuple[int, ...],
) -> dict[int, SnapshotPhoto]:
    """Read a bounded batch of changed images, omitting missing or ineligible records."""
    root, namespace = _capture_context(conn, source_path, device_id)
    if len(image_ids) > MAX_SNAPSHOT_PAGE:
        raise ValueError("The snapshot batch limit is invalid")
    if any(type(image_id) is not int or image_id <= 0 for image_id in image_ids):
        raise ValueError("A positive image ID is required")
    if not image_ids:
        return {}
    placeholders = ",".join("?" for _ in image_ids)
    rows = conn.execute(
        f"""
        SELECT id, content_hash, raw_width, raw_height, exif_orientation
        FROM image WHERE id IN ({placeholders}) AND processed_at IS NOT NULL
        """,
        image_ids,
    ).fetchall()
    return {record.image_id: record for record in _capture_records(conn, root, namespace, rows)}
