#!/usr/bin/env python3
"""Bundle the same backend, frontend, version and changelog as the desktop app."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "android/app/build/generated"


def prepare():
    subprocess.run(["npm", "--prefix", str(ROOT / "frontend"), "run", "build"], check=True)
    backend = GENERATED / "python/backend"
    if backend.exists():
        shutil.rmtree(backend)
    # Select source files explicitly, never bundle a developer's database or photos.
    for source in (ROOT / "backend").rglob("*.py"):
        relative = source.relative_to(ROOT / "backend")
        if any(part in {".venv", "tests", "__pycache__"} for part in relative.parts):
            continue
        destination = backend / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    runtime = GENERATED / "runtimeAssets/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("VERSION", "CHANGELOG.md"):
        shutil.copyfile(ROOT / name, runtime / name)
    frontend = runtime / "frontend"
    if frontend.exists():
        shutil.rmtree(frontend)
    shutil.copytree(ROOT / "frontend/dist", frontend)
    test_sources = GENERATED / "testPython/backend/tests"
    if test_sources.exists():
        shutil.rmtree(test_sources)
    shutil.copytree(ROOT / "backend/tests", test_sources,
                    ignore=shutil.ignore_patterns("__pycache__"))
    print("Staged the shared desktop backend and frontend for Android")


if __name__ == "__main__":
    prepare()
