#!/usr/bin/env python3

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(*arguments: str) -> None:
    subprocess.run(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        version_info_path = temporary_path / "windows-version-info.txt"
        inventory_path = temporary_path / "dependency-inventory.json"

        run(
            "packaging/windows/generate-version-info.py",
            "--version",
            version,
            "--output",
            str(version_info_path),
        )
        run(
            "scripts/inventory-dependencies.py",
            "--project-root",
            str(PROJECT_ROOT),
            "--output",
            str(inventory_path),
        )

        version_info = version_info_path.read_text(encoding="utf-8")
        if f"StringStruct('ProductVersion', '{version}')" not in version_info:
            raise RuntimeError("Generated Windows metadata does not match VERSION")

        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        if inventory["project_version"] != version:
            raise RuntimeError("Dependency inventory does not match VERSION")
        if not inventory["backend_requirements"]:
            raise RuntimeError("Dependency inventory has no backend requirements")
        if not inventory["frontend_packages"]:
            raise RuntimeError("Dependency inventory has no frontend packages")

    spec = (PROJECT_ROOT / "packaging/windows/face-manager.spec").read_text(
        encoding="utf-8"
    )
    if "upx=True" in spec or spec.count("upx=False") != 2:
        raise RuntimeError("UPX must remain disabled for Windows release artifacts")
    if "version=str(version_info_path)" not in spec:
        raise RuntimeError("PyInstaller is not configured with Windows version metadata")
    if 'project_root / "CHANGELOG.md"' not in spec:
        raise RuntimeError("PyInstaller is not configured to bundle CHANGELOG.md")
    if (
        'build_variant_path.write_text("windows\\n", encoding="ascii")' not in spec
        or '(str(build_variant_path), ".")' not in spec
    ):
        raise RuntimeError("PyInstaller is not configured to bundle the Windows updater marker")

    build_script = (
        PROJECT_ROOT / "packaging/windows/build-release.ps1"
    ).read_text(encoding="utf-8")
    if "python -m venv $buildVenv" not in build_script:
        raise RuntimeError("Windows release builds must install dependencies in an isolated environment")
    for script in ("cuda_runtime.py", "openvino_runtime.py"):
        if f"& $buildPython packaging/windows/{script}" not in build_script:
            raise RuntimeError(f"Windows release builds do not run {script}")
    if (
        "$installerProductVersion = $installerVersionInfo.ProductVersion.Trim()"
        not in build_script
        or "if ($installerProductVersion -ne $version)" not in build_script
    ):
        raise RuntimeError(
            "Windows installer validation does not normalize version metadata"
        )
    if "function Invoke-PipInstallWithRetry" not in build_script:
        raise RuntimeError("Windows release dependency downloads are not retried")

    installer = (PROJECT_ROOT / "packaging/windows/FaceManager.iss").read_text(
        encoding="utf-8"
    )
    required_installer_metadata = (
        "AppPublisherURL=",
        "AppSupportURL=",
        "AppUpdatesURL=",
        "OutputBaseFilename=FaceManager-Setup-{#AppVersion}",
        "VersionInfoProductName=",
        "VersionInfoProductVersion=",
    )
    missing = [entry for entry in required_installer_metadata if entry not in installer]
    if missing:
        raise RuntimeError(f"Installer metadata is incomplete: {', '.join(missing)}")
    versioned_icon = 'IconFilename: "{app}\\FaceManager-{#AppVersion}.ico"'
    if installer.count(versioned_icon) != 2:
        raise RuntimeError(
            "Windows shortcuts must use the versioned icon path to avoid stale icons"
        )
    if 'DestName: "FaceManager-{#AppVersion}.ico"' not in installer:
        raise RuntimeError("Windows installer does not install the versioned shortcut icon")
    if "ChangesAssociations=yes" not in installer:
        raise RuntimeError("Windows installer does not notify the Shell about icon updates")

    release_workflow = (PROJECT_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    windows_job = re.search(r"(?ms)^  windows-installer:\n(.*?)(?=^  [\w-]+:|\Z)", release_workflow)
    if windows_job is None:
        raise RuntimeError("Windows installer job is missing")
    windows_job = windows_job.group(1)
    if "backend/requirements-desktop-gpu.txt" not in windows_job:
        raise RuntimeError("Windows GPU dependencies are missing from the pip cache key")
    if (
        "matrix:" in windows_job
        or "INSTALLER_SUFFIX" in windows_job
        or '"dist/FaceManager-Setup-$env:VERSION.exe.sha256"' not in windows_job
    ):
        raise RuntimeError("Windows release builds must publish one installer")

    print("Packaging metadata and dependency inventory checks passed.")


if __name__ == "__main__":
    main()
