"""Helpers for previews of registered image content."""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image

from .filesystem_paths import filesystem_path
from .preview_cache_budget import note_preview_written, preview_directory

_SPOOL_MEMORY = 4 * 1024 * 1024
_READ_SIZE = 1024 * 1024
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def get_image_geometry(image: Image.Image) -> tuple[int, int, int]:
    try:
        orientation = image.getexif().get(274, 1)
    except (OSError, ValueError, TypeError):
        orientation = 1
    if not isinstance(orientation, int) or not 1 <= orientation <= 8:
        orientation = 1
    return orientation, image.width, image.height


def orient_image(image: Image.Image, orientation: int) -> Image.Image:
    operation = _TRANSPOSE.get(orientation)
    return image.transpose(operation) if operation is not None else image


def decode_verified_preview(
    path: str, content_hash: str
) -> tuple[Image.Image, tuple[int, int, int]]:
    """Read an original once and decode the exact bytes checked against its hash."""
    digest = hashlib.sha256()
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY) as spool:
        with open(filesystem_path(path), "rb") as source:
            while chunk := source.read(_READ_SIZE):
                digest.update(chunk)
                spool.write(chunk)
        if digest.hexdigest() != content_hash:
            raise OSError("Die Originaldatei entspricht nicht dem importierten Bild.")
        spool.seek(0)
        with Image.open(spool) as image:
            return image.convert("RGB"), get_image_geometry(image)


def write_preview_jpeg(
    image: Image.Image,
    destination: Path,
    quality: int,
    *,
    before_publish: Callable[[], None] | None = None,
) -> Path:
    with preview_directory(destination, create=True) as directory_fd:
        if directory_fd is None:
            fd, temporary = tempfile.mkstemp(prefix="preview-", suffix=".tmp", dir=destination.parent)
            name = destination
        else:
            temporary = f"preview-{secrets.token_hex(12)}.tmp"
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory_fd)
            name = destination.name
        try:
            with os.fdopen(fd, "wb") as output:
                image.save(output, format="JPEG", quality=quality, optimize=True)
            if before_publish is not None:
                before_publish()
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            note_preview_written(destination)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    return destination
