import base64
import ctypes
import ntpath
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


POWERSHELL_REVEAL_TYPE = r"""
using System;
using System.Runtime.InteropServices;

public static class FaceManagerExplorerReveal
{
    [DllImport("ole32.dll")]
    private static extern int CoInitializeEx(IntPtr reserved, uint mode);

    [DllImport("ole32.dll")]
    private static extern void CoUninitialize();

    [DllImport("ole32.dll")]
    private static extern void CoTaskMemFree(IntPtr pointer);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    private static extern int SHParseDisplayName(
        string name, IntPtr bindContext, out IntPtr itemId, uint attributesIn,
        out uint attributesOut);

    [DllImport("shell32.dll", PreserveSig = true)]
    private static extern int SHOpenFolderAndSelectItems(
        IntPtr itemId, uint childCount, IntPtr children, uint flags);

    public static void Open(string path)
    {
        const int RpcChangedMode = unchecked((int)0x80010106);
        int initialized = CoInitializeEx(IntPtr.Zero, 2);
        bool uninitialize = initialized == 0 || initialized == 1;
        if (initialized < 0 && initialized != RpcChangedMode)
            Marshal.ThrowExceptionForHR(initialized);

        IntPtr itemId = IntPtr.Zero;
        try
        {
            uint attributes;
            int parsed = SHParseDisplayName(path, IntPtr.Zero, out itemId, 0, out attributes);
            if (parsed < 0) Marshal.ThrowExceptionForHR(parsed);
            int opened = SHOpenFolderAndSelectItems(itemId, 0, IntPtr.Zero, 0);
            if (opened < 0) Marshal.ThrowExceptionForHR(opened);
        }
        finally
        {
            if (itemId != IntPtr.Zero) CoTaskMemFree(itemId);
            if (uninitialize) CoUninitialize();
        }
    }
}
"""


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


def _reveal_with_windows_shell(path: str) -> None:
    """Select a filesystem item through the Unicode-aware Windows Shell API."""
    from ctypes import wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    co_initialize = ole32.CoInitializeEx
    co_initialize.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    co_initialize.restype = ctypes.c_long
    co_uninitialize = ole32.CoUninitialize
    co_uninitialize.argtypes = []
    co_uninitialize.restype = None
    free_item_id = ole32.CoTaskMemFree
    free_item_id.argtypes = [ctypes.c_void_p]
    free_item_id.restype = None
    parse_name = shell32.SHParseDisplayName
    parse_name.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    parse_name.restype = ctypes.c_long
    open_and_select = shell32.SHOpenFolderAndSelectItems
    open_and_select.argtypes = [
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    open_and_select.restype = ctypes.c_long

    rpc_changed_mode = -2147417850  # 0x80010106: COM was initialized differently.
    initialized = co_initialize(None, 2)  # COINIT_APARTMENTTHREADED
    should_uninitialize = initialized in (0, 1)
    if initialized < 0 and initialized != rpc_changed_mode:
        raise OSError(
            "Could not initialize the Windows Shell "
            f"(0x{initialized & 0xFFFFFFFF:08X})"
        )

    item_id = ctypes.c_void_p()
    try:
        attributes = wintypes.DWORD()
        result = parse_name(
            path,
            None,
            ctypes.byref(item_id),
            0,
            ctypes.byref(attributes),
        )
        if result < 0:
            raise OSError(
                "Windows could not resolve the image path "
                f"(0x{result & 0xFFFFFFFF:08X})"
            )
        result = open_and_select(item_id, 0, None, 0)
        if result < 0:
            raise OSError(
                "Windows Explorer could not select the image "
                f"(0x{result & 0xFFFFFFFF:08X})"
            )
    finally:
        if item_id.value:
            free_item_id(item_id)
        if should_uninitialize:
            co_uninitialize()


def _build_powershell_reveal_command(path: str) -> list[str]:
    """Build an injection-safe WSL bridge to the same native Shell API."""
    path_payload = base64.b64encode(path.encode("utf-8")).decode("ascii")
    type_payload = base64.b64encode(
        POWERSHELL_REVEAL_TYPE.encode("utf-8")
    ).decode("ascii")
    script = (
        f'$type = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{type_payload}")); '
        "Add-Type -TypeDefinition $type; "
        f'$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{path_payload}")); '
        "[FaceManagerExplorerReveal]::Open($path)"
    )
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded_script,
    ]


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
        subprocess.run(
            _build_powershell_reveal_command(windows_path),
            check=True,
            capture_output=True,
            text=True,
        )
        return

    if sys.platform == "win32":
        windows_path = ntpath.normpath(normalized_path.replace("/", "\\"))
        _reveal_with_windows_shell(windows_path)
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", normalized_path])
        return

    containing_directory = os.path.dirname(os.path.abspath(normalized_path))
    subprocess.Popen(["xdg-open", containing_directory])
