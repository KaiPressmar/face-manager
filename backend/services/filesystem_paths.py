"""Cross-platform path forms used for local filesystem access."""

from __future__ import annotations

import ntpath
import os


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
