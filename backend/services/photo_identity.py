"""Verify the byte and visual identities used to register photo sources."""

from __future__ import annotations

import hashlib
import os
import stat
import struct

import numpy as np
from PIL import Image

from ..db.schema import HASH_CHUNK_SIZE
from .filesystem_paths import filesystem_path


class RegisteredPhotoChanged(OSError):
    """An available path no longer contains the registered photo."""


def file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def matches_file_identity(path: str, identity: tuple[int, int, int, int, int]) -> bool:
    if os.name == "nt":
        # Windows path-based stat may report creation time where the handle
        # reports change time. Use the same stat representation as import.
        with open(path, "rb") as source:
            return file_identity(os.fstat(source.fileno())) == identity
    return file_identity(os.stat(path)) == identity


def _open_nonblocking(path: str, flags: int) -> int:
    return os.open(path, flags | getattr(os, "O_NONBLOCK", 0))


def open_registered_photo(path, content_hash, identity):
    """Return an open, verified photo and its file metadata.

    The caller owns the handle on success. A fresh handle avoids resolving the
    path again after verification and works with both local and mounted files.
    """
    source = open(filesystem_path(path), "rb", opener=_open_nonblocking)
    try:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise RegisteredPhotoChanged("Der Speicherort enthält keine reguläre Fotodatei.")
        current_identity = file_identity(before)
        if identity != current_identity:
            if not content_hash:
                raise RegisteredPhotoChanged("Die Originaldatei kann nicht sicher überprüft werden.")
            digest = hashlib.sha256()
            while chunk := source.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
            if digest.hexdigest() != content_hash or file_identity(os.fstat(source.fileno())) != current_identity:
                raise RegisteredPhotoChanged("Die Datei entspricht nicht mehr dem importierten Foto.")
            source.seek(0)
        return source, before
    except BaseException:
        source.close()
        raise


def visual_photo_hash(
    image: Image.Image, geometry: tuple[int, int, int], rgb_pixels: np.ndarray | None = None,
) -> str | None:
    """Identify exact decoded pixels, orientation, and rendering metadata."""
    if (image.format not in {"JPEG", "PNG"} or getattr(image, "n_frames", 1) != 1
            or image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}):
        # Preserve the original byte identity for formats we cannot compare
        # losslessly with this decoder, including animated and high-bit-depth PNG.
        return None
    profile = image.info.get("icc_profile") or b""
    if not isinstance(profile, bytes):
        return None
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    mode = "RGBA" if has_alpha else "RGB"
    digest = hashlib.sha256(b"face-manager-photo-pixels-v1\0")
    digest.update(struct.pack(">BII", *geometry))
    digest.update(mode.encode("ascii"))
    digest.update(hashlib.sha256(profile).digest())
    digest.update(repr(tuple(image.info.get(key) for key in (
        "gamma", "chromaticity", "srgb", "adobe_transform",
    ))).encode("ascii"))
    if rgb_pixels is not None and not has_alpha:
        digest.update(memoryview(rgb_pixels).cast("B"))
    else:
        with image.convert(mode) as pixels:
            digest.update(memoryview(np.asarray(pixels)).cast("B"))
    return digest.hexdigest()
