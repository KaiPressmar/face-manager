#!/usr/bin/env python3

"""Create a factual dependency inventory without making license judgments."""

import argparse
import json
from pathlib import Path
from typing import Any


BACKEND_MANIFESTS = (
    "backend/requirements.txt",
    "backend/requirements-desktop.txt",
    "backend/requirements-desktop-gpu.txt",
)


def read_backend_requirements(project_root: Path) -> list[dict[str, str]]:
    requirements: list[dict[str, str]] = []
    for relative_path in BACKEND_MANIFESTS:
        manifest_path = project_root / relative_path
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            requirement = line.strip()
            if not requirement or requirement.startswith("#"):
                continue
            requirements.append(
                {
                    "manifest": relative_path,
                    "requirement": requirement,
                }
            )
    return requirements


def read_frontend_packages(project_root: Path) -> list[dict[str, Any]]:
    lock_path = project_root / "frontend/package-lock.json"
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: list[dict[str, Any]] = []

    for location, metadata in sorted(lock_data.get("packages", {}).items()):
        if not location or "node_modules/" not in location:
            continue

        package: dict[str, Any] = {
            "location": location,
            "name": location.rsplit("node_modules/", 1)[-1],
            "version": metadata.get("version"),
        }
        if "license" in metadata:
            package["declared_license"] = metadata["license"]
        packages.append(package)

    return packages


def build_inventory(project_root: Path) -> dict[str, Any]:
    version = (project_root / "VERSION").read_text(encoding="utf-8").strip()
    return {
        "schema_version": 1,
        "project": "Face Manager",
        "project_version": version,
        "scope": {
            "backend": "Declared requirements; versions are constraints, not a resolved environment.",
            "frontend": "Resolved package metadata reported by frontend/package-lock.json.",
        },
        "notice": (
            "This is a technical inventory, not legal advice or a conclusion about "
            "license compatibility or redistribution rights."
        ),
        "backend_requirements": read_backend_requirements(project_root),
        "frontend_packages": read_frontend_packages(project_root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    inventory = json.dumps(
        build_inventory(args.project_root.resolve()),
        indent=2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{inventory}\n", encoding="utf-8")
    else:
        print(inventory)


if __name__ == "__main__":
    main()
