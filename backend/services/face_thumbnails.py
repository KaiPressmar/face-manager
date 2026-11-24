"""Disk-backed face crop thumbnail cache."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageOps

from ..config import get_data_root
from ..db.schema import get_conn
from .filesystem_paths import filesystem_path
from .preview_cache import decode_verified_preview, orient_image, write_preview_jpeg
from .preview_cache_budget import delete_preview_file, preview_exists

logger = logging.getLogger("face_manager.face_thumbnails")

FACE_THUMBNAIL_MAX_SIZE = 256
FACE_THUMBNAIL_QUALITY = 86
# JPEG encoding releases the GIL, so a small worker pool speeds up bulk warmup
# without starving request handlers.
FACE_THUMBNAIL_WARMUP_WORKERS = min(2, (os.cpu_count() or 2))
_face_locks = tuple(Lock() for _ in range(64))

FileIdentity = tuple[int, int, int, int, int]
FaceBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class FaceThumbnailWarmupResult:
    """Summarize one bounded thumbnail warmup scan."""

    total_faces: int = 0
    scanned_faces: int = 0
    created_thumbnails: int = 0
    skipped_existing: int = 0
    skipped_missing_source: int = 0
    failed_faces: int = 0
    highest_face_id: int = 0
    reached_end: bool = False


def get_face_thumbnail_root() -> Path:
    """Return the root directory used for cached face crop thumbnails."""
    return get_data_root() / "thumbnails" / "faces"


def _shard(face_id: int) -> Path:
    return get_face_thumbnail_root() / f"{face_id // 1000:04d}"


def _file_identity(info: os.stat_result) -> FileIdentity:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _thumbnail_target(face_id: int, source: str, identity: FileIdentity, bbox: FaceBox) -> Path:
    digest = hashlib.sha256(repr((source, identity, bbox)).encode("utf-8")).hexdigest()[:24]
    return _shard(face_id) / f"{face_id}-{digest}.jpg"


def _registered_thumbnail_target(face_id: int, content_hash: str, bbox: FaceBox) -> Path:
    digest = hashlib.sha256(repr(("registered-v1", content_hash, bbox)).encode()).hexdigest()[:24]
    return _shard(face_id) / f"{face_id}-{digest}.jpg"


def get_cached_face_thumbnail(
    face_id: int, content_hash: str, bbox: FaceBox, *, touch: bool = True
) -> Path | None:
    """Look up a crop of registered content without touching the original."""
    destination = _registered_thumbnail_target(face_id, content_hash, bbox)
    return destination if preview_exists(destination, touch=touch) else None


def write_face_thumbnail(
    face_id: int, content_hash: str, bbox: FaceBox, image: Image.Image, orientation: int
) -> Path:
    """Write an oriented crop from already verified raw image pixels."""
    return _write_registered_face_thumbnail(
        face_id, content_hash, bbox, orient_image(image, orientation), orientation
    )


def _write_registered_face_thumbnail(
    face_id: int, content_hash: str, bbox: FaceBox, oriented: Image.Image, orientation: int
) -> Path:
    with _face_locks[face_id % len(_face_locks)]:
        destination = _registered_thumbnail_target(face_id, content_hash, bbox)
        if not preview_exists(destination, touch=False):
            crop = _crop_thumbnail(oriented, orientation, face_id, bbox)
            write_preview_jpeg(crop, destination, FACE_THUMBNAIL_QUALITY)
        return destination


def write_face_thumbnails(
    content_hash: str, faces: Iterable[tuple[int, FaceBox]], image: Image.Image, orientation: int
) -> list[Path]:
    """Write crops from a shared oriented image."""
    results = []
    oriented = orient_image(image, orientation)
    for face_id, bbox in faces:
        try:
            results.append(_write_registered_face_thumbnail(face_id, content_hash, bbox, oriented, orientation))
        except (OSError, ValueError):
            logger.warning("Could not prepare preview for face %s", face_id, exc_info=True)
    return results


def get_face_thumbnail_path(face_id: int, image_path: str, bbox: FaceBox) -> Path:
    """Return the cache path for this face, photo version and crop rectangle."""
    source = filesystem_path(image_path)
    return _thumbnail_target(int(face_id), source, _file_identity(os.stat(source)), bbox)


def delete_face_thumbnail(face_id: int) -> None:
    """Remove cached variants of a deleted face without failing library writes."""
    face_id = int(face_id)
    with _face_locks[face_id % len(_face_locks)]:
        directory = _shard(face_id)
        candidates = [directory / f"{face_id}.jpg", *directory.glob(f"{face_id}-*.jpg")]
        for path in candidates:
            try:
                delete_preview_file(path)
            except OSError:
                logger.warning("Could not remove cached thumbnail %s", path, exc_info=True)


def _transform_bbox(
    orientation: int,
    oriented_width: int,
    oriented_height: int,
    x: int,
    y: int,
    w: int,
    h: int,
) -> Tuple[int, int, int, int]:
    """Map a raw-image bbox onto the orientation-corrected image."""
    if orientation == 2:
        return oriented_width - x - w, y, w, h
    if orientation == 3:
        return oriented_width - x - w, oriented_height - y - h, w, h
    if orientation == 4:
        return x, oriented_height - y - h, w, h
    if orientation == 5:
        return y, x, h, w
    if orientation == 6:
        return oriented_width - y - h, x, h, w
    if orientation == 7:
        return oriented_width - y - h, oriented_height - x - w, h, w
    if orientation == 8:
        return y, oriented_height - x - w, h, w
    return x, y, w, h


def _crop_thumbnail(
    oriented_image: Image.Image, orientation: int, face_id: int, bbox: FaceBox
) -> Image.Image:
    x, y, w, h = _transform_bbox(
        orientation, oriented_image.width, oriented_image.height, *bbox
    )
    left = max(0, x)
    top = max(0, y)
    right = min(oriented_image.width, x + max(1, w))
    bottom = min(oriented_image.height, y + max(1, h))
    if right <= left or bottom <= top:
        raise ValueError(f"Invalid face crop bounds for face {face_id}")

    crop = oriented_image.crop((left, top, right, bottom)).convert("RGB")
    crop.thumbnail(
        (FACE_THUMBNAIL_MAX_SIZE, FACE_THUMBNAIL_MAX_SIZE),
        Image.Resampling.LANCZOS,
    )
    return crop


def _render_face_thumbnail(
    oriented_image: Image.Image,
    orientation: int,
    face_id: int,
    bbox: FaceBox,
    thumbnail_path: Path,
    source: str,
    identity: FileIdentity,
) -> Path:
    crop = _crop_thumbnail(oriented_image, orientation, face_id, bbox)
    def verify_source():
        if _file_identity(os.stat(source)) != identity:
            raise OSError("Source image changed while rendering face thumbnail")

    return write_preview_jpeg(
        crop, thumbnail_path, FACE_THUMBNAIL_QUALITY, before_publish=verify_source
    )


def _decode_oriented(source: str, identity: FileIdentity) -> tuple[Image.Image, int]:
    with open(source, "rb") as file:
        if _file_identity(os.fstat(file.fileno())) != identity:
            raise OSError("Source image changed before rendering face thumbnail")
        with Image.open(file) as image:
            try:
                orientation = image.getexif().get(274, 1)
            except Exception:
                orientation = 1
            oriented = ImageOps.exif_transpose(image) if orientation in range(2, 9) else image.copy()
        if _file_identity(os.fstat(file.fileno())) != identity:
            raise OSError("Source image changed while reading face thumbnail")
    return oriented, orientation


def ensure_face_thumbnail(face_id: int, image_path: str, bbox: FaceBox) -> Path:
    """Create a cached JPEG for the current source and face coordinates."""
    face_id = int(face_id)
    source = filesystem_path(image_path)
    with _face_locks[face_id % len(_face_locks)]:
        identity = _file_identity(os.stat(source))
        destination = _thumbnail_target(face_id, source, identity, bbox)
        if preview_exists(destination):
            return destination
        image, orientation = _decode_oriented(source, identity)
        return _render_face_thumbnail(image, orientation, face_id, bbox, destination, source, identity)


def create_face_thumbnails_for_image(
    image_path: str,
    faces: Iterable[Tuple[int, FaceBox]],
) -> None:
    """Render uncached faces from a shared decode of one source version."""
    source = filesystem_path(image_path)
    try:
        identity = _file_identity(os.stat(source))
        pending = [
            (face_id, bbox, _thumbnail_target(face_id, source, identity, bbox))
            for face_id, bbox in faces
        ]
        pending = [entry for entry in pending if not preview_exists(entry[2], touch=False)]
        if not pending:
            return

        image, orientation = _decode_oriented(source, identity)
        for face_id, bbox, destination in pending:
            try:
                with _face_locks[face_id % len(_face_locks)]:
                    if not preview_exists(destination, touch=False):
                        _render_face_thumbnail(
                            image, orientation, face_id, bbox, destination, source, identity
                        )
            except Exception:
                logger.exception("Could not create face thumbnail for face %s", face_id)
    except Exception:
        logger.exception("Could not open %s to create face thumbnails", image_path)


def get_face_library_signature() -> Tuple[int, int]:
    """Return a cheap ``(face_count, max_face_id)`` fingerprint of the library.

    The warmup worker compares this fingerprint across idle cycles to decide
    whether anything changed. It avoids re-scanning the whole table (one stat
    call per face) once every thumbnail already exists.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM face"
        ).fetchone()
    finally:
        conn.close()
    return (int(row["count"]), int(row["max_id"]))


def _create_thumbnails_in_parallel(
    pending: List[Tuple[int, str, Tuple[int, int, int, int]]],
    stop_event: Event | None,
) -> Tuple[int, int]:
    """Create the collected missing thumbnails concurrently.

    Returns:
        A ``(created, failed)`` count pair.
    """
    if not pending:
        return 0, 0

    def _worker(item: Tuple[int, str, Tuple[int, int, int, int]]) -> Optional[bool]:
        face_id, image_path, bbox = item
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            ensure_face_thumbnail(face_id, image_path, bbox)
            return True
        except Exception:
            logger.exception("Could not warm face thumbnail for face %s", face_id)
            return False

    created = 0
    failed = 0
    workers = min(FACE_THUMBNAIL_WARMUP_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(_worker, pending):
            if outcome is True:
                created += 1
            elif outcome is False:
                failed += 1
    return created, failed


def _warm_registered_groups(
    groups: dict[tuple[str, tuple[str, ...]], list[tuple[int, FaceBox]]],
    stop_event: Event | None,
) -> tuple[int, int, int]:
    if not groups:
        return 0, 0, 0

    def warm(item):
        (content_hash, paths), faces = item
        if stop_event is not None and stop_event.is_set():
            return 0, 0, 0
        for path in paths:
            if stop_event is not None and stop_event.is_set():
                return 0, 0, 0
            try:
                raw, (orientation, _, _) = decode_verified_preview(path, content_hash)
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
            with raw:
                written = write_face_thumbnails(content_hash, faces, raw, orientation)
            return len(written), 0, len(faces) - len(written)
        return 0, len(faces), 0

    with ThreadPoolExecutor(max_workers=min(FACE_THUMBNAIL_WARMUP_WORKERS, len(groups))) as pool:
        totals = [0, 0, 0]
        for created, missing, failed in pool.map(warm, groups.items()):
            totals[0] += created
            totals[1] += missing
            totals[2] += failed
        return tuple(totals)


def warm_missing_face_thumbnails(
    *,
    after_face_id: int = 0,
    max_created: int = 128,
    scan_limit: int = 1024,
    stop_event: Event | None = None,
) -> FaceThumbnailWarmupResult:
    """Create a bounded batch of missing face thumbnails.

    Missing thumbnails are collected during a cheap sequential scan and then
    rendered concurrently, since JPEG encoding dominates the cost.

    Args:
        after_face_id: Resume scanning after this face id.
        max_created: Maximum thumbnails to create in this batch.
        scan_limit: Maximum face rows to inspect in this batch.
        stop_event: Optional cooperative cancellation signal.

    Returns:
        Summary of the scan and the highest inspected face id.
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        total_faces = int(
            conn.execute("SELECT COUNT(*) AS count FROM face").fetchone()["count"]
        )
        rows = conn.execute(
            """
            SELECT
                f.id,
                f.image_id,
                i.content_hash,
                f.bbox_x,
                f.bbox_y,
                f.bbox_w,
                f.bbox_h
            FROM face f
            LEFT JOIN image i ON i.id = f.image_id
            WHERE f.id > ?
            ORDER BY f.id ASC
            LIMIT ?
            """,
            (int(after_face_id), int(scan_limit)),
        ).fetchall()
        locations = defaultdict(list)
        for row in conn.execute(
            """
            SELECT image_id, path
            FROM image_location
            WHERE image_id IN (
                SELECT image_id FROM face WHERE id > ? ORDER BY id LIMIT ?
            )
            ORDER BY image_id, path
            """,
            (int(after_face_id), int(scan_limit)),
        ):
            locations[row["image_id"]].append(row["path"])
    finally:
        conn.close()

    scanned_faces = 0
    skipped_existing = 0
    skipped_missing_source = 0
    highest_face_id = int(after_face_id)
    stopped_early = False
    pending: List[Tuple[int, str, Tuple[int, int, int, int]]] = []
    registered_groups = defaultdict(list)
    pending_count = 0

    for row in rows:
        if stop_event is not None and stop_event.is_set():
            stopped_early = True
            break

        face_id = int(row["id"])
        highest_face_id = face_id
        scanned_faces += 1

        bbox = (
            int(float(row["bbox_x"] or 0)),
            int(float(row["bbox_y"] or 0)),
            int(float(row["bbox_w"] or 0)),
            int(float(row["bbox_h"] or 0)),
        )
        image_paths = locations[row["image_id"]]
        if row["content_hash"]:
            content_hash = row["content_hash"]
            if get_cached_face_thumbnail(face_id, content_hash, bbox, touch=False) is not None:
                skipped_existing += 1
                continue
            registered_groups[(content_hash, tuple(image_paths))].append((face_id, bbox))
        else:
            image_path = next(
                (path for path in image_paths if os.path.isfile(filesystem_path(path))),
                None,
            )
            if image_path is None:
                skipped_missing_source += 1
                continue
            try:
                if preview_exists(
                    get_face_thumbnail_path(face_id, image_path, bbox), touch=False
                ):
                    skipped_existing += 1
                    continue
            except OSError:
                skipped_missing_source += 1
                continue
            pending.append((face_id, image_path, bbox))

        pending_count += 1
        if pending_count >= max_created:
            stopped_early = True
            break

    created_thumbnails, failed_faces = _create_thumbnails_in_parallel(
        pending, stop_event
    )
    registered_created, registered_missing, registered_failed = _warm_registered_groups(
        registered_groups, stop_event
    )

    return FaceThumbnailWarmupResult(
        total_faces=total_faces,
        scanned_faces=scanned_faces,
        created_thumbnails=created_thumbnails + registered_created,
        skipped_existing=skipped_existing,
        skipped_missing_source=skipped_missing_source + registered_missing,
        failed_faces=failed_faces + registered_failed,
        highest_face_id=highest_face_id,
        reached_end=(not stopped_early and len(rows) < scan_limit),
    )
