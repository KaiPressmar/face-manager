from pathlib import Path
from runpy import run_path

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
build_variant_path = project_root / "build" / "BUILD_VARIANT"
build_variant_path.parent.mkdir(parents=True, exist_ok=True)
build_variant_path.write_text("windows\n", encoding="ascii")

packaging_dir = project_root / "packaging" / "windows"
cuda_runtime = run_path(str(packaging_dir / "cuda_runtime.py"))
openvino_runtime = run_path(str(packaging_dir / "openvino_runtime.py"))
openvino_runtime["validate_onnxruntime_installation"]()
openvino_binaries, openvino_data = openvino_runtime["collect_openvino_files"]()


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
datas += copy_metadata("onnxruntime-gpu")
datas += copy_metadata(openvino_runtime["OPENVINO_DISTRIBUTION"])
datas += openvino_data
for distribution_name, _ in cuda_runtime["CUDA_COMPONENTS"].values():
    datas += copy_metadata(distribution_name)

binaries = []
binaries += collect_dynamic_libs("onnxruntime")
binaries += cuda_runtime["collect_cuda_binaries"]()
binaries += openvino_binaries

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("pythonnet")
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "onnxruntime_ep_openvino",
]

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
