"""Cross-platform path forms used for local filesystem access."""

from __future__ import annotations

import ntpath
import os
import stat


def filesystem_path(path: str | os.PathLike[str]) -> str:
    """Return an extended-length path for Windows filesystem operations.

    The application keeps familiar drive and UNC paths in its database. Only
    filesystem calls receive the extended form, which also handles paths over
    the traditional 260-character Windows limit.
    """
    value = os.fspath(path)
    if os.name != "nt":
        return value

    if not ntpath.isabs(value):
        value = os.path.abspath(value)
    value = value.replace("/", "\\")
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return f"\\\\?\\UNC\\{value[2:]}"
    if len(value) >= 3 and value[1] == ":" and value[2] == "\\":
        return f"\\\\?\\{value}"
    return value


def stored_path(path: str | os.PathLike[str]) -> str:
    """Remove an extended prefix before persisting or displaying a path."""
    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        return f"\\\\{value[8:]}"
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def is_confirmed_missing(path: str, expected_device: int | None = None) -> bool:
    """Check for a missing file without mistaking an unmounted volume for a deletion."""
    source = filesystem_path(path)
    try:
        os.stat(source, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    else:
        return False

    # Older entries do not have device metadata. Avoid deleting paths on
    # recognizable removable or remote sources until they can be reimported.
    if expected_device is None:
        normalized = stored_path(path).replace("\\", "/")
        if normalized.startswith(("//", "/mnt/", "/media/", "/run/media/")):
            return False
    try:
        parent = os.stat(os.path.dirname(source) or ".")
    except OSError:
        return False
    return stat.S_ISDIR(parent.st_mode) and (
        expected_device is None or parent.st_dev == expected_device
    )
