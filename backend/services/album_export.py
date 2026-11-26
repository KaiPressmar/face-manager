"""Save locally rendered album pages to a user-selected file."""

from __future__ import annotations

import base64
import logging
import ntpath
import os
import re
import stat
import struct
import tempfile
from io import BytesIO
from pathlib import Path
from shutil import copyfileobj
from threading import Lock
from typing import BinaryIO, Callable
from zipfile import ZIP_STORED, ZipFile

from .filesystem_paths import filesystem_path

logger = logging.getLogger("face_manager.album_export")

# Match the size limits of the browser artwork renderer.
_MIN_PAGE_EDGE = 1200
_MAX_PAGE_EDGE = 4800
_MAX_PAGE_PIXELS = 16_000_000
_MAX_PNG_BYTES = 40 * 1024 * 1024
_MAX_ENCODED_LENGTH = ((_MAX_PNG_BYTES + 2) // 3) * 4
_MAX_ZIP_PAGES = 32
_MAX_ZIP_PNG_BYTES = 64 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_WINDOWS_DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.IGNORECASE)
_DESTINATION_CHANGED = "Die Zieldatei wurde inzwischen geändert. Wähle den Speicherort erneut."


def _album_filename(filename: str, extension: str) -> str:
    stem = ntpath.splitext(ntpath.basename(filename[-2048:]))[0] if isinstance(filename, str) else ""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    stem = stem.encode("utf-8", "ignore")[:140].decode("utf-8", "ignore").rstrip(" .")
    if _WINDOWS_DEVICE_NAME.match(stem):
        stem = "_" + stem
    return (stem or "Face-Manager-Album") + extension


def _decode_album_png(encoded: str) -> bytes:
    from PIL import Image

    if not isinstance(encoded, str) or not 0 < len(encoded) <= _MAX_ENCODED_LENGTH:
        raise ValueError("Das Albumblatt ist leer oder zu groß.")
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > _MAX_PNG_BYTES or not data.startswith(_PNG_SIGNATURE):
            raise ValueError("Invalid PNG signature or size")
        with Image.open(BytesIO(data), formats=("PNG",)) as image:
            width, height = image.size
            short, long = sorted((width, height))
            if not (_MIN_PAGE_EDGE <= short <= long <= _MAX_PAGE_EDGE
                    and long <= 2 * short and width * height <= _MAX_PAGE_PIXELS):
                raise ValueError("Invalid album page dimensions")
            image.verify()
        return data
    except (OSError, SyntaxError, ValueError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError("Das Albumblatt ist keine gültige PNG-Datei in einer unterstützten Größe.") from exc


def _destination_identity(path: Path) -> tuple[int, ...] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Wähle eine reguläre Datei als Ziel für den Fotobuchexport.")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _publish_new_file(temporary: Path, path: Path) -> None:
    if os.name == "nt":
        # Windows rename fails if the destination exists.
        os.rename(temporary, path)
    else:
        # Linking the complete temporary file publishes it exclusively.
        os.link(temporary, path)


def _write_output(path: Path, source: BinaryIO, approved: tuple[int, ...] | None) -> None:
    fd, name = tempfile.mkstemp(prefix=".face-manager-album-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        if approved is None:
            try:
                _publish_new_file(temporary, path)
            except FileExistsError as exc:
                raise ValueError(_DESTINATION_CHANGED) from exc
        else:
            try:
                unchanged = _destination_identity(path) == approved
            except ValueError as exc:
                raise ValueError(_DESTINATION_CHANGED) from exc
            if not unchanged:
                raise ValueError(_DESTINATION_CHANGED)
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
        """Save one validated PNG page."""
        return self._save(filename, ".png", lambda: BytesIO(_decode_album_png(png_base64)))

    def save_album_zip(self, filename: str, pages: list[str]) -> dict[str, str]:
        """Save all validated pages in one ZIP with numbered PNG entries."""
        return self._save(filename, ".zip", lambda: self._prepare_zip(pages))

    @staticmethod
    def _prepare_zip(pages: list[str]) -> BinaryIO:
        if (not isinstance(pages, list) or not 2 <= len(pages) <= _MAX_ZIP_PAGES
                or any(not isinstance(page, str) or not 0 < len(page) <= _MAX_ENCODED_LENGTH for page in pages)):
            raise ValueError("Ein ZIP kann 2 bis 32 Albumseiten enthalten.")
        if sum(map(len, pages)) > 4 * ((_MAX_ZIP_PNG_BYTES + 2) // 3 + len(pages)):
            raise ValueError("Das ZIP ist zu groß. Speichere die Seiten einzeln.")
        output = tempfile.TemporaryFile()
        try:
            total = 0
            with ZipFile(output, "w", compression=ZIP_STORED, allowZip64=False) as archive:
                for index, page in enumerate(pages, start=1):
                    png = _decode_album_png(page)
                    total += len(png)
                    if total > _MAX_ZIP_PNG_BYTES:
                        raise ValueError("Das ZIP ist zu groß. Speichere die Seiten einzeln.")
                    archive.writestr(f"Seite-{index:02}.png", png)
            output.seek(0)
            return output
        except BaseException:
            output.close()
            raise

    def _save(self, filename: str, extension: str, prepare: Callable[[], BinaryIO]) -> dict[str, str]:
        if not self._lock.acquire(blocking=False):
            return {"status": "error", "message": "Ein Fotobuchexport wird bereits gespeichert."}
        try:
            with prepare() as source:
                return self._save_output(filename, extension, source)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            logger.exception("Could not save album export")
            return {"status": "error", "message": "Der Fotobuchexport konnte nicht gespeichert werden."}
        finally:
            self._lock.release()

    def _save_output(self, filename: str, extension: str, source: BinaryIO) -> dict[str, str]:
        destination = self._choose_destination(_album_filename(filename, extension))
        if destination is None:
            return {"status": "cancelled"}
        if not os.path.isabs(destination):
            raise ValueError("Wähle einen gültigen Speicherort für den Fotobuchexport.")
        path = Path(filesystem_path(destination))
        if path.suffix.lower() != extension:
            raise ValueError(f"Wähle einen Dateinamen mit der Endung {extension}.")
        approved = _destination_identity(path)
        if approved is not None:
            if self._confirm_overwrite is None:
                raise ValueError("Die Zieldatei ist bereits vorhanden. Wähle einen anderen Namen.")
            if not self._confirm_overwrite(path.name):
                return {"status": "cancelled"}
        _write_output(path, source, approved)
        return {"status": "saved"}
