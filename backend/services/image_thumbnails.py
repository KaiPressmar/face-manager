"""Disk-backed, size-limited thumbnails for library photos."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from itertools import islice
from pathlib import Path
from threading import Lock, Semaphore

from PIL import Image, ImageOps

from ..config import get_data_root
from .filesystem_paths import filesystem_path
from .preview_cache import orient_image, write_preview_jpeg
from .preview_cache_budget import preview_exists

IMAGE_THUMBNAIL_MAX_SIZE = 480
IMAGE_THUMBNAIL_QUALITY = 85
_TOUCH_INTERVAL = 24 * 60 * 60
_MAX_CACHE_AGE = 90 * _TOUCH_INTERVAL
_MAX_SCAN_ENTRIES = 25_000
_MAX_PRUNED_ENTRIES = 1_000
_SHARD_NAME = re.compile(r"[0-9]{4,}")
_THUMBNAIL_NAME = re.compile(r"([0-9]+)-[0-9a-f]{20}\.jpg")

# Photo decodes can be large even with JPEG draft mode. Keep request bursts from
# decoding many originals simultaneously; striped locks also coalesce requests
# for the same image within one process without retaining one lock per image.
_decode_slots = Semaphore(2)
_image_locks = tuple(Lock() for _ in range(64))


def _source_signature(path: str) -> tuple[str, int, int, int, int, int]:
    source = filesystem_path(path)
    info = os.stat(source)
    return source, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _thumbnail_root() -> Path:
    return get_data_root() / "thumbnails" / "images"


def _thumbnail_path(image_id: int, signature: tuple[str, int, int, int, int, int]) -> Path:
    digest = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:20]
    return _thumbnail_root() / f"{image_id // 1000:04d}" / f"{image_id}-{digest}.jpg"


def _registered_thumbnail_path(image_id: int, content_hash: str) -> Path:
    digest = hashlib.sha256(f"registered-v1:{content_hash}".encode()).hexdigest()[:20]
    return _thumbnail_root() / f"{image_id // 1000:04d}" / f"{image_id}-{digest}.jpg"


def get_cached_image_thumbnail(image_id: int, content_hash: str) -> Path | None:
    """Look up a thumbnail of registered content without touching the original."""
    destination = _registered_thumbnail_path(image_id, content_hash)
    return destination if preview_exists(destination) else None


def write_image_thumbnail(
    image_id: int, content_hash: str, image: Image.Image, orientation: int
) -> Path:
    """Write a preview from already verified raw image pixels."""
    with _image_locks[image_id % len(_image_locks)]:
        destination = _registered_thumbnail_path(image_id, content_hash)
        if preview_exists(destination):
            return destination
        thumbnail = image
        if max(image.size) > IMAGE_THUMBNAIL_MAX_SIZE:
            thumbnail = ImageOps.contain(
                image, (IMAGE_THUMBNAIL_MAX_SIZE,) * 2, Image.Resampling.LANCZOS
            )
        thumbnail = orient_image(thumbnail, orientation)
        return write_preview_jpeg(thumbnail, destination, IMAGE_THUMBNAIL_QUALITY)


def _is_directory(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISDIR(info.st_mode) and not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


def prune_image_thumbnails() -> int:
    """Discard a bounded number of expired generated JPEGs before serving requests."""
    root = _thumbnail_root()
    now = time.time()
    try:
        if not _is_directory(root.parent) or not _is_directory(root):
            return 0
        with os.scandir(root) as entries:
            shards = sorted(
                Path(entry.path) for entry in islice(entries, _MAX_SCAN_ENTRIES)
                if _SHARD_NAME.fullmatch(entry.name)
            )
    except OSError:
        return 0
    if not shards:
        return 0

    # Rotate the starting shard daily so large libraries do not always begin
    # cleanup with the same files, even if the previous scan exhausted its budget.
    start = int(now // _TOUCH_INTERVAL) % len(shards)
    remaining = _MAX_SCAN_ENTRIES
    removed = 0
    for shard in shards[start:] + shards[:start]:
        try:
            if not _is_directory(shard):
                continue
            with os.scandir(shard) as entries:
                for entry in islice(entries, remaining):
                    remaining -= 1
                    match = _THUMBNAIL_NAME.fullmatch(entry.name)
                    if not match:
                        continue
                    image_id = int(match[1])
                    if f"{image_id // 1000:04d}" != shard.name:
                        continue
                    try:
                        with _image_locks[image_id % len(_image_locks)]:
                            info = entry.stat(follow_symlinks=False)
                            if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                                    and info.st_mtime < now - _MAX_CACHE_AGE):
                                os.unlink(entry.path)
                                removed += 1
                                if removed == _MAX_PRUNED_ENTRIES:
                                    return removed
                    except OSError:
                        continue
        except OSError:
            continue
        if remaining == 0:
            break
    return removed


def ensure_image_thumbnail(image_id: int, path: str) -> str:
    """Return a cached upright RGB JPEG for the current version of a photo.

    Missing or unreadable originals raise to the caller. A source that changes
    during rendering also raises, so pixels are never stored under an obsolete
    source signature.
    """
    image_id = int(image_id)
    with _image_locks[image_id % len(_image_locks)]:
        signature = _source_signature(path)
        thumbnail_path = _thumbnail_path(image_id, signature)
        if preview_exists(thumbnail_path):
            return str(thumbnail_path)

        with _decode_slots:
            with Image.open(signature[0]) as image:
                # JPEG draft mode decodes at a reduced resolution when possible.
                image.draft("RGB", (IMAGE_THUMBNAIL_MAX_SIZE * 2,) * 2)
                oriented = ImageOps.exif_transpose(image)
                thumbnail = oriented.convert("RGB")
                thumbnail.thumbnail(
                    (IMAGE_THUMBNAIL_MAX_SIZE, IMAGE_THUMBNAIL_MAX_SIZE),
                    Image.Resampling.LANCZOS,
                )

            def verify_source():
                if _source_signature(path) != signature:
                    raise OSError(f"Source image changed while rendering: {path}")

            write_preview_jpeg(
                thumbnail, thumbnail_path, IMAGE_THUMBNAIL_QUALITY, before_publish=verify_source
            )

    return str(thumbnail_path)
