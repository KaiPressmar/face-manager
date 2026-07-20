import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


project_root = Path.cwd()
frontend_dist = project_root / "frontend" / "dist"
icon_path = project_root / "packaging" / "windows" / "assets" / "face-manager-icon.ico"
version_info_path = project_root / "build" / "windows-version-info.txt"
build_variant = os.environ.get("FACE_MANAGER_BUILD_VARIANT", "cpu").strip().lower()
if build_variant not in {"cpu", "gpu"}:
    raise ValueError(f"Unsupported FACE_MANAGER_BUILD_VARIANT: {build_variant}")
build_variant_path = project_root / "build" / "BUILD_VARIANT"
build_variant_path.parent.mkdir(parents=True, exist_ok=True)
build_variant_path.write_text(build_variant + "\n", encoding="ascii")


def optional_collect(package_name):
    try:
        return (
            collect_data_files(package_name),
            collect_dynamic_libs(package_name),
            copy_metadata(package_name),
        )
    except Exception:
        return ([], [], [])


def optional_copy_metadata(distribution_name):
    try:
        return copy_metadata(distribution_name)
    except Exception:
        return []

datas = [
    (str(project_root / "VERSION"), "."),
    (str(project_root / "CHANGELOG.md"), "."),
    (str(build_variant_path), "."),
    (str(frontend_dist), "frontend/dist"),
]
datas += collect_data_files("insightface")
datas += collect_data_files("cv2")
datas += copy_metadata("insightface")
datas += copy_metadata("pywebview")
datas += optional_copy_metadata("onnxruntime")
datas += optional_copy_metadata("onnxruntime-gpu")

binaries = []
binaries += collect_dynamic_libs("onnxruntime")

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("pythonnet")
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

for package_name in (
    "nvidia.cuda_runtime",
    "nvidia.cudnn",
    "nvidia.cublas",
    "nvidia.cuda_nvrtc",
):
    package_datas, package_binaries, package_metadata = optional_collect(package_name)
    datas += package_datas
    datas += package_metadata
    binaries += package_binaries

excludes = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
]

a = Analysis(
    [str(project_root / "backend" / "desktop_main.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FaceManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon_path),
    version=str(version_info_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FaceManager",
)
