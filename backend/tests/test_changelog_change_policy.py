import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check-changelog-change.py"
SPEC = importlib.util.spec_from_file_location("check_changelog_change", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class ChangelogChangePolicyTest(unittest.TestCase):
    def test_user_visible_classification(self):
        body = "- [x] **User-visible change** — visible\n- [ ] **Internal-only change** — internal"

        self.assertEqual(POLICY.classify_pr_body(body), "user-visible")

    def test_internal_only_classification(self):
        body = "- [ ] **User-visible change** — visible\n- [X] **Internal-only change** — internal"

        self.assertEqual(POLICY.classify_pr_body(body), "internal-only")

    def test_missing_classification_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            POLICY.classify_pr_body("")

    def test_conflicting_classification_is_rejected(self):
        body = "- [x] **User-visible change**\n- [x] **Internal-only change**"

        with self.assertRaisesRegex(ValueError, "exactly one"):
            POLICY.classify_pr_body(body)

    def test_source_paths_require_classification(self):
        self.assertTrue(POLICY.requires_classification("CHANGELOG.md"))
        self.assertTrue(POLICY.requires_classification("frontend/src/App.tsx"))
        self.assertTrue(POLICY.requires_classification("backend/app.py"))
        self.assertFalse(POLICY.requires_classification("backend/tests/test_app.py"))
        self.assertFalse(POLICY.requires_classification("CONTRIBUTING.md"))

    def test_user_visible_source_change_requires_changelog(self):
        with self.assertRaisesRegex(ValueError, "does not update CHANGELOG.md"):
            POLICY.validate_coverage(
                "user-visible",
                {"frontend/src/App.tsx"},
                ["frontend/src/App.tsx"],
            )

    def test_internal_only_source_change_does_not_require_changelog(self):
        POLICY.validate_coverage(
            "internal-only",
            {"frontend/src/App.tsx"},
            ["frontend/src/App.tsx"],
        )


if __name__ == "__main__":
    unittest.main()
