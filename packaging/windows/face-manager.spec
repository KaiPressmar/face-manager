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


def optional_collect(package_name):
    try:
        return (
            collect_data_files(package_name),
            collect_dynamic_libs(package_name),
            copy_metadata(package_name),
        )
    except Exception:
        return ([], [], [])

datas = [
    (str(project_root / "VERSION"), "."),
    (str(frontend_dist), "frontend/dist"),
]
datas += collect_data_files("insightface")
datas += collect_data_files("cv2")
datas += copy_metadata("insightface")
datas += copy_metadata("onnxruntime")
datas += copy_metadata("pywebview")

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

for package_name in ("nvidia_cuda_runtime_cu12", "nvidia_cudnn_cu12"):
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
    ["backend/desktop_main.py"],
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
    upx=True,
    console=False,
    icon=str(icon_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FaceManager",
)
