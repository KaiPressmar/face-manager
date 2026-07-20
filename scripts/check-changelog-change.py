#!/usr/bin/env python3

"""Enforce explicit PR classification and user-facing changelog coverage."""

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath


EXACT_CLASSIFICATION_PATHS = {
    "CHANGELOG.md",
    "VERSION",
    "frontend/package.json",
    "frontend/package-lock.json",
    ".github/workflows/release.yml",
    "scripts/changelog.py",
    "scripts/release-version.sh",
}

USER_VISIBLE_MARKER = "User-visible change"
INTERNAL_ONLY_MARKER = "Internal-only change"


def checked(body: str, marker: str) -> bool:
    """Return whether the exact PR classification checkbox is selected."""
    pattern = rf"^\s*-\s*\[[xX]\]\s*\*\*{re.escape(marker)}\*\*"
    return re.search(pattern, body, flags=re.MULTILINE) is not None


def classify_pr_body(body: str) -> str:
    """Return the one selected changelog classification or raise clearly."""
    user_visible = checked(body, USER_VISIBLE_MARKER)
    internal_only = checked(body, INTERNAL_ONLY_MARKER)
    if user_visible == internal_only:
        raise ValueError(
            "Check exactly one PR classification: "
            f"'{USER_VISIBLE_MARKER}' or '{INTERNAL_ONLY_MARKER}'."
        )
    return "user-visible" if user_visible else "internal-only"


def read_pr_body(event_path: Path) -> str:
    """Read the pull request body from a GitHub Actions event payload."""
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request") or {}
    body = pull_request.get("body")
    return body if isinstance(body, str) else ""


def requires_classification(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    if normalized in EXACT_CLASSIFICATION_PATHS:
        return True
    if normalized.startswith("frontend/src/"):
        return True
    if normalized.startswith("packaging/"):
        return True
    if normalized.startswith("backend/") and not normalized.startswith(
        "backend/tests/"
    ):
        return True
    return False


def validate_coverage(
    classification: str,
    changed_paths: set[str],
    relevant_paths: list[str],
) -> None:
    """Require curated release notes only for user-visible changes."""
    if classification != "user-visible" or "CHANGELOG.md" in changed_paths:
        return
    examples = "\n  - ".join(relevant_paths[:10])
    raise ValueError(
        "This PR is classified as user-visible but does not update "
        "CHANGELOG.md:\n  - " + examples
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base commit SHA")
    parser.add_argument("--head", default="HEAD", help="Head commit SHA")
    parser.add_argument(
        "--event-path",
        type=Path,
        help="GitHub event JSON used to read the PR classification",
    )
    args = parser.parse_args()

    result = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base}...{args.head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    relevant_paths = sorted(
        path for path in changed_paths if requires_classification(path)
    )
    if not relevant_paths:
        print("Pull request has no application or release-workflow changes to classify.")
        return

    if args.event_path is None:
        if "CHANGELOG.md" not in changed_paths:
            raise SystemExit(
                "Application changes need either a CHANGELOG.md update or a PR "
                "classification supplied with --event-path."
            )
        print("Changelog coverage passed; PR classification was not available.")
        return

    try:
        classification = classify_pr_body(read_pr_body(args.event_path))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    try:
        validate_coverage(classification, changed_paths, relevant_paths)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if classification == "internal-only":
        print(
            "Internal-only PR classification accepted; "
            "no in-app release note is required."
        )
    else:
        print("User-visible PR classification and changelog coverage passed.")


if __name__ == "__main__":
    main()
