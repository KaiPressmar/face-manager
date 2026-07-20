#!/usr/bin/env python3

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.changelog import (
    ChangelogError,
    finalize_unreleased,
    find_release,
    load_changelog,
    render_document,
    render_release_notes,
    validate_changelog,
)


CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"


def write_output(content: str, output: Path | None) -> None:
    if output is None:
        print(content, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate, finalize, and extract Face Manager release notes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--require-unreleased", action="store_true")
    check_parser.add_argument("--next-version")

    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--version", required=True)
    release_parser.add_argument("--date", default=date.today().isoformat())

    notes_parser = subparsers.add_parser("notes")
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--output", type=Path)

    args = parser.parse_args()

    try:
        document = load_changelog(CHANGELOG_PATH)
        if args.command == "check":
            validate_changelog(
                document,
                require_unreleased=args.require_unreleased,
                next_version=args.next_version,
            )
            print("Changelog format is valid.")
        elif args.command == "release":
            finalized = finalize_unreleased(document, args.version, args.date)
            CHANGELOG_PATH.write_text(render_document(finalized), encoding="utf-8")
            print(f"Changelog finalized for v{args.version}.")
        else:
            release = find_release(document, args.version)
            if release is None or not release["sections"]:
                raise ChangelogError(
                    f"No user-facing changelog section found for v{args.version}."
                )
            write_output(render_release_notes(release), args.output)
    except (ChangelogError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
