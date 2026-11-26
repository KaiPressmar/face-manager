import sqlite3
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
    released_versions,
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


MULTI_VERSION_CHANGELOG = """# Änderungsprotokoll

## [Unreleased]

### Neu

## [1.2.0] - 2026-07-20

### Neu

- Ganz neue Funktion.

## [1.1.0] - 2026-06-10

### Verbessert

- Schnellere Ansicht.

## [1.0.0] - 2026-05-01

### Neu

- Erste Veröffentlichung.
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

    def test_released_versions_skips_unreleased_and_empty(self):
        document = parse_changelog(MULTI_VERSION_CHANGELOG)

        versions = released_versions(document)

        self.assertEqual(
            [release["version"] for release in versions],
            ["1.2.0", "1.1.0", "1.0.0"],
        )
        self.assertTrue(all(release["sections"] for release in versions))

    def _current_changelog(self, changelog, version, last_seen, *, has_photos=False):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "CHANGELOG.md"
            path.write_text(changelog, encoding="utf-8")
            database = path.with_suffix(".sqlite")
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE image (id INTEGER PRIMARY KEY)")
                if has_photos:
                    connection.execute("INSERT INTO image (id) VALUES (1)")
                connection.commit()
            finally:
                connection.close()
            with patch.object(app_module, "APP_VERSION", version), patch.object(
                app_module, "get_changelog_path", return_value=path
            ), patch.object(
                app_module,
                "get_last_seen_changelog_version",
                return_value=last_seen,
            ), patch.object(
                app_module,
                "get_conn",
                side_effect=lambda: sqlite3.connect(database),
            ):
                return app_module.api_current_changelog()

    def test_current_changelog_fresh_install_shows_only_running_version(self):
        response = self._current_changelog(MULTI_VERSION_CHANGELOG, "1.2.0", None)

        self.assertFalse(response["seen"])
        self.assertTrue(response["fresh_install"])
        self.assertEqual(
            [release["version"] for release in response["versions"]],
            ["1.2.0"],
        )

    def test_current_changelog_returns_every_skipped_version(self):
        response = self._current_changelog(MULTI_VERSION_CHANGELOG, "1.2.0", "1.0.0")

        self.assertFalse(response["seen"])
        self.assertFalse(response["fresh_install"])
        self.assertEqual(
            [release["version"] for release in response["versions"]],
            ["1.2.0", "1.1.0"],
        )

    def test_current_changelog_marks_seen_when_up_to_date(self):
        response = self._current_changelog(MULTI_VERSION_CHANGELOG, "1.2.0", "1.2.0")

        self.assertTrue(response["seen"])

    def test_current_changelog_retains_upgrade_notice_for_older_nonempty_library(self):
        response = self._current_changelog(
            MULTI_VERSION_CHANGELOG, "1.2.0", None, has_photos=True,
        )
        self.assertFalse(response["seen"])
        self.assertFalse(response["fresh_install"])

    def test_full_changelog_api_returns_all_released_versions(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "CHANGELOG.md"
            path.write_text(MULTI_VERSION_CHANGELOG, encoding="utf-8")
            with patch.object(app_module, "get_changelog_path", return_value=path):
                response = app_module.api_full_changelog()

        self.assertEqual(
            [release["version"] for release in response["versions"]],
            ["1.2.0", "1.1.0", "1.0.0"],
        )

    def test_acknowledging_current_changelog_persists_running_version(self):
        with patch.object(app_module, "APP_VERSION", "1.2.0"), patch.object(
            app_module, "set_last_seen_changelog_version", return_value="1.2.0"
        ) as persist:
            response = app_module.api_acknowledge_current_changelog()

        persist.assert_called_once_with("1.2.0")
        self.assertEqual(response, {"version": "1.2.0", "seen": True})

    def test_full_changelog_file_failure_is_retryable(self):
        with patch.object(app_module, "load_changelog", side_effect=OSError("temporarily unavailable")):
            with self.assertRaises(app_module.HTTPException) as raised:
                app_module.api_full_changelog()
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
