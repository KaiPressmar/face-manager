"""Helpers for previews of registered image content."""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from PIL import Image

from .filesystem_paths import filesystem_path
from .preview_cache_budget import note_preview_written, preview_directory

_SPOOL_MEMORY = 4 * 1024 * 1024
_READ_SIZE = 1024 * 1024
FACE_PREVIEW_MAX_SOURCE_BYTES = 96 * 1024 * 1024
_FACE_PREVIEW_MAX_DECODE_PIXELS = 40_000_000
_FACE_PREVIEW_MAX_SIDE = 1536
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


def correct_bbox_for_orientation(geometry, x, y, w, h):
    """Transform a raw-pixel face box to match an upright preview."""
    orientation, width, height = geometry
    if orientation == 2:
        return width - x - w, y, w, h
    if orientation == 3:
        return width - x - w, height - y - h, w, h
    if orientation == 4:
        return x, height - y - h, w, h
    if orientation == 5:
        return y, x, h, w
    if orientation == 6:
        return height - y - h, x, h, w
    if orientation == 7:
        return height - y - h, width - x - w, h, w
    if orientation == 8:
        return y, width - x - w, h, w
    return x, y, w, h


@contextmanager
def snapshot_preview_source(
    path: str, *, maximum_bytes: int | None = None, require_stable_path: bool = False,
):
    """Make a stable snapshot of the exact bytes a preview will decode."""
    digest = hashlib.sha256()
    native_path = filesystem_path(path)
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY) as spool:
        with open(native_path, "rb") as source:
            before = os.fstat(source.fileno()) if require_stable_path else None
            while chunk := source.read(_READ_SIZE):
                if maximum_bytes is not None and spool.tell() + len(chunk) > maximum_bytes:
                    raise OSError("Die Originaldatei ist für diese Analyse zu groß.")
                digest.update(chunk)
                spool.write(chunk)
            if before is not None:
                after = os.fstat(source.fileno())
                registered = os.stat(native_path)
                if not os.path.samestat(before, registered) or any(
                    getattr(before, key) != getattr(after, key) or getattr(after, key) != getattr(registered, key)
                    for key in ("st_size", "st_mtime_ns", "st_ctime_ns")
                ):
                    raise OSError("Die Originaldatei wurde während der Prüfung geändert.")
        spool.seek(0)
        yield spool, digest.hexdigest()


@contextmanager
def _verified_preview_source(path: str, content_hash: str, *, maximum_bytes: int | None = None):
    with snapshot_preview_source(path, maximum_bytes=maximum_bytes) as (source, digest):
        if digest != content_hash:
            raise OSError("Die Originaldatei entspricht nicht dem importierten Bild.")
        yield source


@contextmanager
def bounded_upright_face_preview(source):
    """Decode the same upright pixels and raw geometry for marking and suggestions."""
    with Image.open(source) as image:
        geometry = get_image_geometry(image)
        image.draft("RGB", (_FACE_PREVIEW_MAX_SIDE, _FACE_PREVIEW_MAX_SIDE))
        if image.width * image.height > _FACE_PREVIEW_MAX_DECODE_PIXELS:
            raise OSError("Das Bild ist für diese Analyse zu groß.")
        with image.convert("RGB") as rgb:
            rgb.thumbnail((_FACE_PREVIEW_MAX_SIDE, _FACE_PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
            upright = orient_image(rgb, geometry[0])
            try:
                yield upright, geometry
            finally:
                if upright is not rgb:
                    upright.close()


def decode_verified_preview(
    path: str, content_hash: str
) -> tuple[Image.Image, tuple[int, int, int]]:
    """Decode the exact original bytes checked against the registered hash."""
    with _verified_preview_source(path, content_hash) as source, Image.open(source) as image:
        return image.convert("RGB"), get_image_geometry(image)


def read_verified_preview_geometry(path: str, content_hash: str) -> tuple[int, int, int]:
    """Read geometry from the registered original without decoding pixels."""
    with _verified_preview_source(path, content_hash) as source, Image.open(source) as image:
        return get_image_geometry(image)


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
