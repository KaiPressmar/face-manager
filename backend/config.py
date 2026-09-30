import os
import sys
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


def get_project_root() -> Path:
    """Return the source tree root or the PyInstaller bundle root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def get_data_root() -> Path:
    """Return the writable application data directory."""
    override = os.environ.get("FACE_MANAGER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            local_app_data = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            )
            return local_app_data / "FaceManager"
        return Path.home() / ".face-manager"

    return get_project_root() / "backend" / "db"


def get_frontend_dist_dir() -> Path:
    """Return the built frontend directory when present."""
    override = os.environ.get("FACE_MANAGER_FRONTEND_DIST")
    if override:
        return Path(override).expanduser().resolve()
    return get_project_root() / "frontend" / "dist"


def get_changelog_path() -> Path:
    """Return the bundled user-facing changelog."""
    return get_project_root() / "CHANGELOG.md"


def get_build_variant() -> str:
    """Return the installer flavor embedded by the Windows release build."""
    override = os.environ.get("FACE_MANAGER_BUILD_VARIANT", "").strip().lower()
    if override in {"cpu", "gpu"}:
        return override
    variant_path = get_project_root() / "BUILD_VARIANT"
    if variant_path.is_file():
        variant = variant_path.read_text(encoding="utf-8").strip().lower()
        if variant in {"cpu", "gpu"}:
            return variant
    return "cpu"


def get_log_dir() -> Path:
    """Return the directory used for persistent application logs."""
    return get_data_root() / "logs"


def get_error_log_path() -> Path:
    """Return the primary persistent error log path."""
    return get_log_dir() / "error.log"


DB_PATH = str(get_data_root() / "database.sqlite")
APP_VERSION = (get_project_root() / "VERSION").read_text().strip()
