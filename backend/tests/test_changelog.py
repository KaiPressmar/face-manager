import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import app as app_module
from backend.changelog import (
    ChangelogError,
    finalize_unreleased,
    find_release,
    load_changelog,
    parse_changelog,
    render_document,
    render_release_notes,
    validate_changelog,
)


SAMPLE_CHANGELOG = """# Änderungsprotokoll

## [Unreleased]

### Neu

- Eine sichtbare Neuerung.

### Verbessert

### Behoben
"""


class ChangelogTest(unittest.TestCase):
    def test_finalize_moves_unreleased_items_to_version(self):
        document = parse_changelog(SAMPLE_CHANGELOG)

        finalized = finalize_unreleased(document, "1.2.0", "2026-07-20")
        rendered = render_document(finalized)
        reparsed = parse_changelog(rendered)
        validate_changelog(reparsed)

        release = find_release(reparsed, "1.2.0")
        self.assertEqual(release["date"], "2026-07-20")
        self.assertEqual(
            release["sections"],
            [{"title": "Neu", "items": ["Eine sichtbare Neuerung."]}],
        )
        self.assertEqual(find_release(reparsed, "Unreleased")["sections"], [])

    def test_release_notes_are_high_level_markdown(self):
        finalized = finalize_unreleased(
            parse_changelog(SAMPLE_CHANGELOG), "1.2.0", "2026-07-20"
        )
        release = find_release(finalized, "1.2.0")

        self.assertEqual(
            render_release_notes(release),
            "# Face Manager v1.2.0\n\n## Neu\n\n- Eine sichtbare Neuerung.\n",
        )

    def test_release_requires_user_facing_entry(self):
        empty = SAMPLE_CHANGELOG.replace("- Eine sichtbare Neuerung.\n", "")

        with self.assertRaises(ChangelogError):
            finalize_unreleased(parse_changelog(empty), "1.2.0", "2026-07-20")

    def test_unknown_category_is_rejected(self):
        invalid = SAMPLE_CHANGELOG.replace("### Neu", "### Intern")

        with self.assertRaises(ChangelogError):
            validate_changelog(parse_changelog(invalid))

    def test_load_changelog_from_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "CHANGELOG.md"
            path.write_text(SAMPLE_CHANGELOG, encoding="utf-8")

            document = load_changelog(path)

        self.assertEqual(document["releases"][0]["version"], "Unreleased")

    def test_current_changelog_api_returns_running_version_notes(self):
        finalized = finalize_unreleased(
            parse_changelog(SAMPLE_CHANGELOG), "1.2.0", "2026-07-20"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "CHANGELOG.md"
            path.write_text(render_document(finalized), encoding="utf-8")
            with patch.object(app_module, "APP_VERSION", "1.2.0"), patch.object(
                app_module, "get_changelog_path", return_value=path
            ), patch.object(
                app_module, "get_last_seen_changelog_version", return_value=None
            ):
                response = app_module.api_current_changelog()

        self.assertEqual(response["version"], "1.2.0")
        self.assertFalse(response["seen"])
        self.assertEqual(response["sections"][0]["title"], "Neu")

    def test_acknowledging_current_changelog_persists_running_version(self):
        with patch.object(app_module, "APP_VERSION", "1.2.0"), patch.object(
            app_module, "set_last_seen_changelog_version", return_value="1.2.0"
        ) as persist:
            response = app_module.api_acknowledge_current_changelog()

        persist.assert_called_once_with("1.2.0")
        self.assertEqual(response, {"version": "1.2.0", "seen": True})


if __name__ == "__main__":
    unittest.main()
