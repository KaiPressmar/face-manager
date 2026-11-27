#!/usr/bin/env python3

"""Check whether the release PR already passed CI for the exact main tree."""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode


WORKFLOW = ".github/workflows/ci.yml"
VERIFIER = "scripts/check-release-ci.py"
REQUIRED_JOBS = (
    "Metadata and scripts",
    "Backend",
    "Frontend",
    "Preview cache (Windows)",
)
TREE_STEP_PREFIX = "Tested tree "
BROWSER_STEPS = ("Verify browser workflows", "Verify browser journey")
PAGE_SIZE = 100


class ProofUnavailable(Exception):
    pass


def git(*arguments):
    return subprocess.check_output(
        ["git", *arguments], text=True, stderr=subprocess.DEVNULL, timeout=5
    ).strip()


def api(repository, path, **parameters):
    query = urlencode({"per_page": PAGE_SIZE, **parameters})
    try:
        response = subprocess.check_output(
            ["gh", "api", f"repos/{repository}/{path}?{query}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"},
        )
        return json.loads(response)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProofUnavailable("could not verify the GitHub Actions records") from error


def successful(record):
    return record["status"] == "completed" and record["conclusion"] == "success"


def matching_release_pr(repository, sha, source):
    pulls = api(repository, f"commits/{sha}/pulls")
    if not isinstance(pulls, list) or len(pulls) >= PAGE_SIZE:
        raise ProofUnavailable("the merged pull request list is incomplete")
    matches = [
        pull for pull in pulls
        if pull["merged_at"]
        and pull["merge_commit_sha"] == sha
        and pull["head"]["sha"] == source
        and pull["head"]["ref"] == "develop"
        and pull["base"]["ref"] == "main"
        and (pull["head"]["repo"] or {}).get("full_name") == repository
        and (pull["base"]["repo"] or {}).get("full_name") == repository
    ]
    if len(matches) != 1:
        raise ProofUnavailable("no unambiguous merged develop-to-main pull request")
    return matches[0]["number"]


def latest_pr_ci(repository, source, pull_number):
    response = api(
        repository, "actions/workflows/ci.yml/runs",
        branch="develop", event="pull_request", head_sha=source,
    )
    runs = response["workflow_runs"]
    if response["total_count"] != len(runs):
        raise ProofUnavailable("the matching CI run list is incomplete")
    numbers = [run["run_number"] for run in runs]
    if (
        any(type(number) is not int or number < 1 for number in numbers)
        or len(set(numbers)) != len(runs)
    ):
        raise ProofUnavailable("CI run ordering is ambiguous")

    for run in sorted(runs, key=lambda run: run["run_number"], reverse=True):
        if not run["pull_requests"]:
            raise ProofUnavailable("GitHub did not link a candidate CI run to a pull request")
        if not any(pull["number"] == pull_number for pull in run["pull_requests"]):
            continue
        if (
            run["event"] != "pull_request"
            or run["head_branch"] != "develop"
            or run["head_sha"] != source
            or run["head_repository"]["full_name"] != repository
            or not successful(run)
        ):
            raise ProofUnavailable("the latest linked CI run does not prove successful PR CI")
        return run
    raise ProofUnavailable("no CI run is linked to the merged pull request and source commit")


def verify_jobs(repository, run, tree):
    run_id = run["id"]
    attempt = run["run_attempt"]
    if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 1:
        raise ProofUnavailable("the CI run or attempt ID is invalid")
    response = api(repository, f"actions/runs/{run_id}/attempts/{attempt}/jobs")
    jobs = response["jobs"]
    if response["total_count"] != len(jobs):
        raise ProofUnavailable("the CI job list is incomplete")

    verified = {}
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job["name"] == name]
        if len(matches) != 1 or not successful(matches[0]):
            raise ProofUnavailable(f"the latest attempt did not pass exactly one {name} job")
        verified[name] = matches[0]

    tree_steps = [
        step for step in verified["Metadata and scripts"]["steps"]
        if step["name"].startswith(TREE_STEP_PREFIX)
    ]
    if (
        len(tree_steps) != 1
        or tree_steps[0]["name"] != f"{TREE_STEP_PREFIX}{tree}"
        or not successful(tree_steps[0])
    ):
        raise ProofUnavailable("the CI checkout step does not prove the exact main tree")

    for name in BROWSER_STEPS:
        matches = [step for step in verified["Backend"]["steps"] if step["name"] == name]
        if len(matches) != 1 or not successful(matches[0]):
            raise ProofUnavailable(f"the latest attempt did not pass exactly one {name} step")


def release_ci_proof(repository, sha):
    if git("rev-parse", "HEAD") != sha:
        raise ProofUnavailable("the checkout does not match the main push SHA")
    parents = git("show", "-s", "--format=%P", "HEAD").split()
    if len(parents) != 2:
        raise ProofUnavailable("the main push is not a two-parent merge commit")
    first_parent, source = parents
    if git("diff", "--name-only", first_parent, "HEAD", "--", WORKFLOW, VERIFIER):
        raise ProofUnavailable("the CI workflow or verifier changed in this merge")
    tree = git("rev-parse", "HEAD^{tree}")

    pull_number = matching_release_pr(repository, sha, source)
    run = latest_pr_ci(repository, source, pull_number)
    verify_jobs(repository, run, tree)
    return f"CI run {run['id']} attempt {run['run_attempt']} passed for PR #{pull_number} and tree {tree}"


def main():
    reuse = False
    try:
        repository = os.environ.get("GH_REPO")
        sha = os.environ.get("GITHUB_SHA")
        if not repository or not sha or not os.environ.get("GH_TOKEN"):
            raise ProofUnavailable("the GitHub Actions context is incomplete")
        proof = release_ci_proof(repository, sha)
        print(f"Reusing release PR CI. {proof}.")
        reuse = True
    except ProofUnavailable as error:
        print(f"Running full CI because {error}.")
    except (AttributeError, KeyError, TypeError, ValueError):
        print("Running full CI because the GitHub Actions records were malformed.")
    except (OSError, subprocess.SubprocessError):
        print("Running full CI because the checkout could not be inspected.")

    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"reuse={str(reuse).lower()}\n")


if __name__ == "__main__":
    main()
