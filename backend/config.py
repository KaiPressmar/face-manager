import os
from pathlib import Path

WSL_DRIVE_MAP = {
    "D:": "/mnt/d",
    "C:": "/mnt/c",
}

def to_wsl_path(win_path: str) -> str:
    """Convert a Windows drive path to its conventional WSL mount path.

    Args:
        win_path: Windows path containing a drive letter.

    Returns:
        Equivalent normalized WSL path.
    """
    win_path = win_path.replace("\\", "/")
    drive, rest = win_path.split(":", 1)
    base = WSL_DRIVE_MAP.get(f"{drive}:", f"/mnt/{drive.lower()}")
    return base + rest

EMBEDDING_DIM = 512
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "database.sqlite")
APP_VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
