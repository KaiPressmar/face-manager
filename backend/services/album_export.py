"""Save a locally rendered album page to a user-selected file."""

from __future__ import annotations

import base64
import logging
import ntpath
import os
import re
import struct
import tempfile
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Callable

from .filesystem_paths import filesystem_path

logger = logging.getLogger("face_manager.album_export")

_PAGE_SIZES = {(3508, 2480), (2480, 3508), (3000, 3000)}
_MAX_PNG_BYTES = 40 * 1024 * 1024
_MAX_ENCODED_LENGTH = ((_MAX_PNG_BYTES + 2) // 3) * 4
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_WINDOWS_DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.IGNORECASE)


def _album_filename(filename: str) -> str:
    stem = ntpath.splitext(ntpath.basename(filename[-2048:]))[0] if isinstance(filename, str) else ""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    stem = stem.encode("utf-8", "ignore")[:140].decode("utf-8", "ignore").rstrip(" .")
    if _WINDOWS_DEVICE_NAME.match(stem):
        stem = "_" + stem
    return (stem or "Face-Manager-Album") + ".png"


def _decode_album_png(encoded: str) -> bytes:
    from PIL import Image

    if not isinstance(encoded, str) or not 0 < len(encoded) <= _MAX_ENCODED_LENGTH:
        raise ValueError("Das Albumblatt ist leer oder zu groß.")
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > _MAX_PNG_BYTES or not data.startswith(_PNG_SIGNATURE):
            raise ValueError("Invalid PNG signature or size")
        with Image.open(BytesIO(data), formats=("PNG",)) as image:
            if image.size not in _PAGE_SIZES:
                raise ValueError("Invalid album page dimensions")
            image.verify()
        return data
    except (OSError, SyntaxError, ValueError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError("Das Albumblatt ist keine gültige PNG-Datei in einer unterstützten Größe.") from exc


def _write_png(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".face-manager-album-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AlbumPngExport:
    def __init__(
        self,
        choose_destination: Callable[[str], str | None],
        confirm_overwrite: Callable[[str], bool] | None = None,
    ) -> None:
        self._choose_destination = choose_destination
        self._confirm_overwrite = confirm_overwrite
        self._lock = Lock()

    def save_album_png(self, filename: str, png_base64: str) -> dict[str, str]:
        """Expose only the PNG export to the desktop webview."""
        if not self._lock.acquire(blocking=False):
            return {"status": "error", "message": "Ein Albumblatt wird bereits gespeichert."}
        try:
            data = _decode_album_png(png_base64)
            destination = self._choose_destination(_album_filename(filename))
            if destination is None:
                return {"status": "cancelled"}
            if not os.path.isabs(destination):
                raise ValueError("Wähle einen gültigen Speicherort für das Albumblatt.")
            path = Path(filesystem_path(destination))
            if path.suffix.lower() != ".png":
                raise ValueError("Wähle einen Dateinamen mit der Endung .png.")
            if self._confirm_overwrite and os.path.lexists(path) and not self._confirm_overwrite(path.name):
                return {"status": "cancelled"}
            _write_png(path, data)
            return {"status": "saved"}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            logger.exception("Could not save album PNG")
            return {"status": "error", "message": "Das Albumblatt konnte nicht gespeichert werden."}
        finally:
            self._lock.release()
