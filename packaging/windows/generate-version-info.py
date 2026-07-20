#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def render_version_info(version: str) -> str:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ValueError(f"Expected a semantic version, got {version!r}")

    major, minor, patch = (int(part) for part in match.groups())
    numeric_version = f"({major}, {minor}, {patch}, 0)"
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric_version},
    prodvers={numeric_version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Face Manager Project'),
          StringStruct('FileDescription', 'Face Manager desktop application'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'FaceManager'),
          StringStruct('OriginalFilename', 'FaceManager.exe'),
          StringStruct('ProductName', 'Face Manager'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the PyInstaller Windows version resource."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_version_info(args.version), encoding="utf-8")


if __name__ == "__main__":
    main()
