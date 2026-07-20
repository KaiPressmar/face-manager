#!/usr/bin/env python3

import json
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
    if 'build_variant_path = project_root / "build" / "BUILD_VARIANT"' not in spec:
        raise RuntimeError("PyInstaller is not configured to bundle the build variant")

    build_script = (
        PROJECT_ROOT / "packaging/windows/build-release.ps1"
    ).read_text(encoding="utf-8")
    if 'Join-Path $buildDir "BUILD_VARIANT"' not in build_script:
        raise RuntimeError("Windows release builds do not emit the updater variant")
    if "$env:FACE_MANAGER_BUILD_VARIANT = $Variant" not in build_script:
        raise RuntimeError("Windows release builds do not pass the updater variant")

    installer = (PROJECT_ROOT / "packaging/windows/FaceManager.iss").read_text(
        encoding="utf-8"
    )
    required_installer_metadata = (
        "AppPublisherURL=",
        "AppSupportURL=",
        "AppUpdatesURL=",
        "VersionInfoProductName=",
        "VersionInfoProductVersion=",
    )
    missing = [entry for entry in required_installer_metadata if entry not in installer]
    if missing:
        raise RuntimeError(f"Installer metadata is incomplete: {', '.join(missing)}")

    print("Packaging metadata and dependency inventory checks passed.")


if __name__ == "__main__":
    main()
