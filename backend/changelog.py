"""Parse and publish the user-facing release changelog."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any


SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING_PATTERN = re.compile(
    r"^## \[(?P<version>Unreleased|\d+\.\d+\.\d+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?$"
)
CATEGORY_HEADING_PATTERN = re.compile(r"^### (?P<title>.+)$")
CHANGELOG_CATEGORIES = ("Neu", "Verbessert", "Behoben")


class ChangelogError(ValueError):
    """Raised when CHANGELOG.md does not follow the repository format."""


def parse_changelog(text: str) -> dict[str, Any]:
    """Parse the constrained Markdown format used for releases."""
    preamble: list[str] = []
    releases: list[dict[str, Any]] = []
    current_release: dict[str, Any] | None = None
    current_section: dict[str, Any] | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        release_match = RELEASE_HEADING_PATTERN.fullmatch(line)
        if release_match:
            current_release = {
                "version": release_match.group("version"),
                "date": release_match.group("date"),
                "sections": [],
            }
            releases.append(current_release)
            current_section = None
            continue

        category_match = CATEGORY_HEADING_PATTERN.fullmatch(line)
        if category_match:
            if current_release is None:
                raise ChangelogError(
                    f"Category before first release heading on line {line_number}."
                )
            current_section = {
                "title": category_match.group("title").strip(),
                "items": [],
            }
            current_release["sections"].append(current_section)
            continue

        if current_release is None:
            preamble.append(line)
            continue

        if not line:
            continue
        if line.startswith("- "):
            if current_section is None:
                raise ChangelogError(
                    f"Changelog item without category on line {line_number}."
                )
            item = line[2:].strip()
            if not item:
                raise ChangelogError(f"Empty changelog item on line {line_number}.")
            current_section["items"].append(item)
            continue

        raise ChangelogError(
            f"Unsupported changelog content on line {line_number}: {line!r}"
        )

    return {"preamble": preamble, "releases": releases}


def validate_changelog(
    document: dict[str, Any],
    *,
    require_unreleased: bool = False,
    next_version: str | None = None,
) -> None:
    """Validate ordering, categories, dates, and optional release readiness."""
    releases = document["releases"]
    if not releases or releases[0]["version"] != "Unreleased":
        raise ChangelogError("The first changelog section must be [Unreleased].")

    seen_versions: set[str] = set()
    for index, release in enumerate(releases):
        version = release["version"]
        if version in seen_versions:
            raise ChangelogError(f"Duplicate changelog version: {version}.")
        seen_versions.add(version)

        if version == "Unreleased":
            if index != 0:
                raise ChangelogError("[Unreleased] must be the first release section.")
            if release["date"] is not None:
                raise ChangelogError("[Unreleased] must not have a release date.")
        else:
            if not SEMVER_PATTERN.fullmatch(version):
                raise ChangelogError(f"Invalid changelog version: {version}.")
            if release["date"] is None:
                raise ChangelogError(f"Released version {version} needs an ISO date.")
            try:
                date.fromisoformat(release["date"])
            except ValueError as exc:
                raise ChangelogError(
                    f"Released version {version} has an invalid date."
                ) from exc

        section_titles = [section["title"] for section in release["sections"]]
        if len(section_titles) != len(set(section_titles)):
            raise ChangelogError(f"Version {version} contains duplicate categories.")
        unknown_categories = [
            title for title in section_titles if title not in CHANGELOG_CATEGORIES
        ]
        if unknown_categories:
            raise ChangelogError(
                f"Version {version} contains unsupported categories: "
                f"{', '.join(unknown_categories)}."
            )

    unreleased_items = sum(
        len(section["items"]) for section in releases[0]["sections"]
    )
    if require_unreleased and unreleased_items == 0:
        raise ChangelogError("[Unreleased] must contain at least one user-facing item.")

    if next_version:
        if not SEMVER_PATTERN.fullmatch(next_version):
            raise ChangelogError(f"Invalid next version: {next_version}.")
        if next_version in seen_versions:
            raise ChangelogError(
                f"Version {next_version} already exists in the changelog."
            )


def load_changelog(path: Path) -> dict[str, Any]:
    document = parse_changelog(path.read_text(encoding="utf-8"))
    validate_changelog(document)
    return document


def find_release(document: dict[str, Any], version: str) -> dict[str, Any] | None:
    for release in document["releases"]:
        if release["version"] == version:
            return {
                "version": version,
                "date": release["date"],
                "sections": [
                    {
                        "title": section["title"],
                        "items": list(section["items"]),
                    }
                    for section in release["sections"]
                    if section["items"]
                ],
            }
    return None


def _release_view(release: dict[str, Any]) -> dict[str, Any]:
    """Return a release with only its non-empty categories, ready for the UI."""
    return {
        "version": release["version"],
        "date": release["date"],
        "sections": [
            {
                "title": section["title"],
                "items": list(section["items"]),
            }
            for section in release["sections"]
            if section["items"]
        ],
    }


def released_versions(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every released version with content, newest first.

    The ``Unreleased`` section and any release without user-facing items are
    omitted so the full changelog only shows finished, meaningful entries.
    """
    views = []
    for release in document["releases"]:
        if release["version"] == "Unreleased":
            continue
        view = _release_view(release)
        if view["sections"]:
            views.append(view)
    return views


def render_document(document: dict[str, Any]) -> str:
    lines = list(document["preamble"])
    while lines and not lines[-1]:
        lines.pop()

    for release in document["releases"]:
        heading = f"## [{release['version']}]"
        if release["date"]:
            heading += f" - {release['date']}"
        lines.extend(["", heading])
        for section in release["sections"]:
            lines.extend(["", f"### {section['title']}"])
            if section["items"]:
                lines.append("")
                lines.extend(f"- {item}" for item in section["items"])

    return "\n".join(lines).rstrip() + "\n"


def finalize_unreleased(
    document: dict[str, Any], version: str, release_date: str
) -> dict[str, Any]:
    validate_changelog(
        document,
        require_unreleased=True,
        next_version=version,
    )
    try:
        date.fromisoformat(release_date)
    except ValueError as exc:
        raise ChangelogError(
            f"Release date must use the ISO format YYYY-MM-DD: {release_date}."
        ) from exc
    unreleased = document["releases"][0]
    released_sections = [
        {
            "title": section["title"],
            "items": list(section["items"]),
        }
        for section in unreleased["sections"]
        if section["items"]
    ]
    next_unreleased = {
        "version": "Unreleased",
        "date": None,
        "sections": [
            {"title": title, "items": []} for title in CHANGELOG_CATEGORIES
        ],
    }
    released = {
        "version": version,
        "date": release_date,
        "sections": released_sections,
    }
    return {
        "preamble": list(document["preamble"]),
        "releases": [next_unreleased, released, *document["releases"][1:]],
    }


def render_release_notes(release: dict[str, Any]) -> str:
    lines = [f"# Face Manager v{release['version']}"]
    for section in release["sections"]:
        if not section["items"]:
            continue
        lines.extend(["", f"## {section['title']}", ""])
        lines.extend(f"- {item}" for item in section["items"])
    return "\n".join(lines).rstrip() + "\n"
