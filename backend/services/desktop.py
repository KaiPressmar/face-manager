import os
import subprocess
import sys


def _is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as version_file:
            return "microsoft" in version_file.read().lower()
    except OSError:
        return False


def open_file_location(path: str):
    if _is_wsl():
        result = subprocess.run(
            ["wslpath", "-w", path],
            check=True,
            capture_output=True,
            text=True,
        )
        windows_path = result.stdout.strip()
        subprocess.Popen(["explorer.exe", f"/select,{windows_path}"])
        return

    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
        return

    subprocess.Popen(["xdg-open", os.path.dirname(path)])
