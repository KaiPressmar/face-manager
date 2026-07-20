import os
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog


WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_UNC_PATH = re.compile(r"^\\\\[^\\]+\\[^\\]+")
WSL_DRIVE_PATH = re.compile(r"^/mnt/([a-zA-Z])(?:/(.*))?$")


def _is_wsl():
    """Detect whether the backend runs inside Windows Subsystem for Linux.

    Returns:
        ``True`` when WSL environment markers are present.
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as version_file:
            return "microsoft" in version_file.read().lower()
    except OSError:
        return False


def is_windows_host():
    """Return whether the backend runs directly on Windows."""
    return sys.platform == "win32"


def is_wsl_host():
    """Return whether the backend runs inside Windows Subsystem for Linux."""
    return _is_wsl()


def is_windows_path(path: str) -> bool:
    """Return whether a path string uses a Windows drive or UNC form."""
    return bool(WINDOWS_DRIVE_PATH.match(path) or WINDOWS_UNC_PATH.match(path))


def translate_windows_path(path: str) -> str:
    """Translate a Windows path for the current host when needed."""
    normalized = path.strip()
    if not normalized:
        return normalized
    if is_windows_host():
        return os.path.normpath(normalized)
    if not is_windows_path(normalized):
        return os.path.normpath(normalized)

    forward_slashes = normalized.replace("\\", "/")
    if forward_slashes.startswith("//"):
        return os.path.normpath(forward_slashes)

    drive = forward_slashes[0].lower()
    suffix = forward_slashes[2:].lstrip("/")
    translated = Path("/mnt") / drive / suffix
    return os.path.normpath(str(translated))


def normalize_import_folder_path(path: str) -> str:
    """Normalize user input for folder imports across Windows and Linux/WSL."""
    return translate_windows_path(path.strip())


def to_display_path(path: str) -> str:
    """Convert an internal path to the UI display format for the current host."""
    normalized = path.strip()
    if not normalized or not is_wsl_host():
        return normalized

    match = WSL_DRIVE_PATH.match(normalized.replace("\\", "/"))
    if not match:
        return normalized

    drive_letter = match.group(1).upper()
    suffix = match.group(2) or ""
    windows_suffix = suffix.replace("/", "\\")
    return f"{drive_letter}:\\{windows_suffix}" if windows_suffix else f"{drive_letter}:\\"


def pick_folder(prefer_windows_dialog: bool = False) -> str | None:
    """Open a native folder chooser and return the selected folder path."""
    if is_wsl_host() and prefer_windows_dialog:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    '$dialog.Description = "Select a folder to import into Face Manager"; '
                    "$dialog.UseDescriptionForTitle = $true; "
                    "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
                    "{ Write-Output $dialog.SelectedPath }"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        selected = result.stdout.strip()
        return selected or None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(parent=root, mustexist=True)
        return selected or None
    finally:
        root.destroy()


def open_file_location(path: str):
    """Open the system file manager and reveal a file.

    Args:
        path: Existing file path to reveal.

    Raises:
        OSError: If the platform file manager cannot be launched.
        subprocess.SubprocessError: If path conversion fails.
    """
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise OSError("Missing file path")

    if _is_wsl():
        result = subprocess.run(
            ["wslpath", "-w", normalized_path],
            check=True,
            capture_output=True,
            text=True,
        )
        windows_path = result.stdout.strip()
        if not windows_path:
            raise OSError("Could not translate the file path for Windows Explorer")
        subprocess.Popen(["explorer.exe", f"/select,{windows_path}"])
        return

    if sys.platform == "win32":
        subprocess.Popen(
            ["explorer.exe", f"/select,{os.path.normpath(normalized_path)}"]
        )
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", normalized_path])
        return

    containing_directory = os.path.dirname(os.path.abspath(normalized_path))
    subprocess.Popen(["xdg-open", containing_directory])
