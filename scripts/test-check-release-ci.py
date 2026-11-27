#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "check_release_ci", Path(__file__).with_name("check-release-ci.py")
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

REPOSITORY = "owner/repository"
SHA = "a" * 40
BASE = "b" * 40
SOURCE = "c" * 40
TREE = "d" * 40
PR = 147
PASS = {"status": "completed", "conclusion": "success"}
CHECKED_PATHS = ("diff", "--name-only", BASE, "HEAD", "--", verifier.WORKFLOW, verifier.VERIFIER)


class ReleaseCIProofTest(unittest.TestCase):
    def setUp(self):
        self.git = {
            ("rev-parse", "HEAD"): SHA,
            ("show", "-s", "--format=%P", "HEAD"): f"{BASE} {SOURCE}",
            CHECKED_PATHS: "",
            ("rev-parse", "HEAD^{tree}"): TREE,
        }
        self.pulls = [{
            "number": PR,
            "merged_at": "2026-11-27T09:00:00Z",
            "merge_commit_sha": SHA,
            "head": {"ref": "develop", "repo": {"full_name": REPOSITORY}, "sha": SOURCE},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }]
        self.run = {
            **PASS, "id": 123, "run_number": 5, "run_attempt": 2,
            "event": "pull_request", "head_branch": "develop", "head_sha": SOURCE,
            "head_repository": {"full_name": REPOSITORY}, "pull_requests": [{"number": PR}],
        }
        self.runs = [self.run]
        self.jobs = [{**PASS, "name": name, "steps": []} for name in verifier.REQUIRED_JOBS]
        self.jobs[0]["steps"] = [{**PASS, "name": f"Tested tree {TREE}"}]
        self.jobs[1]["steps"] = [{**PASS, "name": name} for name in verifier.BROWSER_STEPS]

    def check_api(self, repository, path, **parameters):
        self.assertEqual(repository, REPOSITORY)
        if path == f"commits/{SHA}/pulls":
            return self.pulls
        if path == "actions/workflows/ci.yml/runs":
            self.assertEqual(
                parameters, {"branch": "develop", "event": "pull_request", "head_sha": SOURCE}
            )
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        self.assertEqual(path, "actions/runs/123/attempts/2/jobs")
        return {"total_count": len(self.jobs), "jobs": self.jobs}

    def result(self, api=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "github-output")
            env = {
                "GH_REPO": REPOSITORY,
                "GH_TOKEN": "synthetic",
                "GITHUB_SHA": SHA,
                "GITHUB_OUTPUT": str(output),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(verifier, "git", side_effect=lambda *arguments: self.git[arguments]),
                patch.object(verifier, "api", side_effect=api or self.check_api),
                contextlib.redirect_stdout(io.StringIO()) as log,
            ):
                verifier.main()
            return output.read_text(encoding="utf-8"), log.getvalue()

    def assert_fallback(self, reason, api=None):
        output, log = self.result(api)
        self.assertEqual(output, "reuse=false\n")
        self.assertIn(reason, log)

    def test_reuses_ci_for_exact_tree_and_latest_attempt(self):
        output, log = self.result()
        self.assertEqual(output, "reuse=true\n")
        self.assertIn("CI run 123 attempt 2 passed for PR #147", log)

    def test_commit_must_match_checkout_with_two_parents(self):
        self.git[("rev-parse", "HEAD")] = BASE
        self.assert_fallback("checkout does not match")
        self.git[("rev-parse", "HEAD")] = SHA
        self.git[("show", "-s", "--format=%P", "HEAD")] = BASE
        self.assert_fallback("not a two-parent merge commit")

    def test_workflow_or_verifier_changes_require_full_ci(self):
        for changed_path in (verifier.WORKFLOW, verifier.VERIFIER):
            with self.subTest(changed_path=changed_path):
                self.git[CHECKED_PATHS] = changed_path
                self.assert_fallback("workflow or verifier changed")

    def test_cannot_reuse_for_wrong_pr_or_head(self):
        self.pulls[0]["head"]["sha"] = BASE
        self.assert_fallback("no unambiguous merged develop-to-main pull request")
        self.pulls[0]["head"]["sha"] = SOURCE
        self.run["pull_requests"] = [{"number": PR + 1}]
        self.assert_fallback("no CI run is linked")
        self.run["pull_requests"] = []
        self.assert_fallback("did not link a candidate")
        self.run["pull_requests"] = [{"number": PR}]
        self.run["head_sha"] = BASE
        self.assert_fallback("latest linked CI run does not prove")

    def test_newer_or_retried_ci_failure_cannot_use_old_success(self):
        failed = {**self.run, "id": 124, "run_number": 6, "run_attempt": 2, "conclusion": "failure"}
        self.runs.append(failed)
        self.assert_fallback("latest linked CI run does not prove")
        failed.update(status="in_progress", conclusion=None)
        self.assert_fallback("latest linked CI run does not prove")
        self.runs.remove(failed)
        self.run["conclusion"] = "failure"
        self.assert_fallback("latest linked CI run does not prove")

    def test_checkout_tree_must_match_main(self):
        self.jobs[0]["steps"][0]["name"] = f"Tested tree {SOURCE}"
        self.assert_fallback("does not prove the exact main tree")

    def test_latest_attempt_must_pass_all_jobs_and_both_smokes(self):
        windows = self.jobs.pop()
        self.assert_fallback("Preview cache (Windows) job")
        self.jobs.append(windows)
        self.jobs.append(windows.copy())
        self.assert_fallback("Preview cache (Windows) job")
        self.jobs.pop()
        self.jobs[1]["steps"][1]["conclusion"] = "skipped"
        self.assert_fallback("Verify browser journey step")

    def test_api_timeout_requires_full_ci(self):
        with patch.object(verifier.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("gh", 8)):
            self.assert_fallback("could not verify the GitHub Actions records", api=verifier.api)


if __name__ == "__main__":
    unittest.main()
