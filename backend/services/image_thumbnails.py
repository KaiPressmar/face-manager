"""Disk-backed, size-limited thumbnails for library photos."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from threading import Lock, Semaphore

from PIL import Image, ImageOps

from ..config import get_data_root
from .filesystem_paths import filesystem_path

IMAGE_THUMBNAIL_MAX_SIZE = 480
IMAGE_THUMBNAIL_QUALITY = 85

# Photo decodes can be large even with JPEG draft mode. Keep request bursts from
# decoding many originals simultaneously; striped locks also coalesce requests
# for the same image within one process without retaining one lock per image.
_decode_slots = Semaphore(2)
_image_locks = tuple(Lock() for _ in range(64))


def _source_signature(path: str) -> tuple[str, int, int]:
    source = filesystem_path(path)
    stat = os.stat(source)
    return source, stat.st_size, stat.st_mtime_ns


def _thumbnail_path(image_id: int, signature: tuple[str, int, int]) -> Path:
    digest = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:20]
    return (
        get_data_root()
        / "thumbnails"
        / "images"
        / f"{image_id // 1000:04d}"
        / f"{image_id}-{digest}.jpg"
    )


def ensure_image_thumbnail(image_id: int, path: str) -> str:
    """Return a cached upright RGB JPEG for the current version of a photo.

    Missing or unreadable originals raise to the caller. A source that changes
    during rendering also raises, so pixels are never stored under an obsolete
    source signature.
    """
    image_id = int(image_id)
    signature = _source_signature(path)
    thumbnail_path = _thumbnail_path(image_id, signature)
    if thumbnail_path.is_file():
        return str(thumbnail_path)

    with _image_locks[image_id % len(_image_locks)]:
        signature = _source_signature(path)
        thumbnail_path = _thumbnail_path(image_id, signature)
        if thumbnail_path.is_file():
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

            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f"{image_id}-", suffix=".jpg", dir=thumbnail_path.parent
            )
            os.close(fd)
            temp_path = Path(temp_name)
            try:
                thumbnail.save(
                    temp_path,
                    format="JPEG",
                    quality=IMAGE_THUMBNAIL_QUALITY,
                    optimize=True,
                )
                if _source_signature(path) != signature:
                    raise OSError(f"Source image changed while rendering: {path}")
                os.replace(temp_path, thumbnail_path)
            finally:
                temp_path.unlink(missing_ok=True)

    return str(thumbnail_path)
