---
name: release-cut
description: >
  Cut the next Face Manager version end to end. Runs the release-preparation
  runbook: bumps VERSION via scripts/release-version.sh, finalizes the German
  CHANGELOG Unreleased section into a dated release, opens and auto-merges the
  release-prep PR into develop, then the release PR from develop into main,
  monitors publishing, recovers transient artifact failures, and reports the
  release that CI publishes. Use when the user says "cut a release", "create the
  next release", "release a new version", "/release-cut". Does not tag or create
  the GitHub release itself — CI does that.
---

Drive the release runbook from `CONTRIBUTING.md`. Land committed work first with
**release-commit**; this skill only turns already-merged `develop` work into a
published version.

## Golden rules (never break)

- Never push directly to `develop` or `main`. Both hops go through PRs.
- Never `git tag` or `gh release create`. On a merge to `main`, CI runs, and on
  success `.github/workflows/release.yml` creates the annotated tag `vX.Y.Z`, the
  GitHub Release (notes rendered from `CHANGELOG.md`), and the Windows installers.
  Doing any of this by hand corrupts the release.
- Let `scripts/release-version.sh` do all version/changelog edits — do not hand-edit
  `VERSION`, `frontend/package.json`, `frontend/package-lock.json`, or move the
  `Unreleased` section yourself.
- Squash the release-prep PR into `develop`; use a **merge commit** for the release
  PR into `main` (preserves the release boundary).

## Steps

### 1. Preconditions

```bash
git switch develop && git pull --ff-only origin develop
git status                       # tree clean
python3 scripts/changelog.py check
```

`## [Unreleased]` must contain at least one item. If it is empty, **abort** and
tell the user there is nothing user-facing to release — land user-visible work via
release-commit first. (`release-version.sh` will otherwise fail its
`--require-unreleased` guard.)

### 2. Choose the version

Use an explicit `X.Y.Z` if the user gave one. Otherwise derive `major` / `minor` /
`patch` from the nature of the `Unreleased` items and **confirm with the user**
before proceeding. The target must be strictly newer than the current `VERSION`.

### 3. Prepare on a release branch

```bash
git switch -c chore/release-preparation
./scripts/release-version.sh <major|minor|patch|X.Y.Z>
git diff -- VERSION frontend/package.json frontend/package-lock.json CHANGELOG.md
```

Confirm the diff touches exactly those four files: `VERSION`, both frontend
manifests, and the finalized `CHANGELOG.md` (`Unreleased` → `## [X.Y.Z] - DATE`,
with a fresh empty `Unreleased`).

This is the last chance to polish wording before it ships. Read every bullet in
the new `## [X.Y.Z]` section as a non-technical user would: each one must plainly
say what changes for them, in everyday German, with no technical terms. Refine any
unclear bullet now, while it is part of this unmerged release prep — do **not**
touch sections from earlier, already-released versions.

### 4. Validate

```bash
./scripts/check-all.sh
```

### 5. Release-prep PR into develop (squash, auto-merge, wait for CI)

```bash
git add VERSION frontend/package.json frontend/package-lock.json CHANGELOG.md
git commit -m "Release v$(cat VERSION)"
git push -u origin HEAD
gh pr create --base develop --title "Release v$(cat VERSION)" \
  --body-file .agents/skills/release-cut/assets/release-prep-pr.md
gh pr merge --squash --auto
```

Capture the PR number. Wait for CI, then verify `state=MERGED` and a non-null
`mergedAt` before the next hop. Passing checks alone do not prove that auto-merge
has landed:

```bash
gh pr checks <pr-number> --watch
gh pr view <pr-number> --json state,mergedAt,mergeCommit,url
```

### 6. Release PR from develop into main (merge commit, auto-merge, wait for CI)

```bash
git switch develop && git pull --ff-only origin develop
gh pr create --base main --head develop --title "Release v$(cat VERSION)" \
  --body-file .agents/skills/release-cut/assets/release-pr.md
gh pr merge --merge --auto
```

The base is `main`, the head is `develop`. The Main Branch Policy workflow only
allows PRs into `main` from `develop`. The body asset deliberately selects the
user-visible classification required by the changelog coverage gate; do not
replace it with a one-line body.

Because each previous release adds a merge commit only to `main`, GitHub can mark
the new release PR as `BEHIND`. Inspect `mergeStateStatus` immediately after PR
creation. If it is `BEHIND`, use GitHub's update-branch operation once:

```bash
gh pr view <pr-number> --json mergeStateStatus
gh pr update-branch <pr-number>
```

This merges the previous release boundary into `develop` without a direct push
and starts a fresh PR event with the correct body. Then wait for all current CI
checks and the auto-merge to land. Do not rerun a failed old PR event after
editing its body; reruns retain the original event payload.

### 7. Let CI publish; then report

Do **not** tag or create the release. Capture the release PR's merge commit, find
the main CI run for that commit, and wait for it to succeed. Then find the
subsequent `Publish Release` workflow run triggered by `workflow_run`.

Prefer low-noise JSON status queries every 30–60 seconds over an unbounded
`gh run watch`: the latter repeats the full job tree and can flood the working
context during long Windows builds.

```bash
gh run view <run-id> --json status,conclusion,jobs \
  --jq '{status,conclusion,jobs:[.jobs[]|{name,status,conclusion}]}'
gh release view "v$(cat VERSION)" --json url,tagName,targetCommitish,assets
```

The publish job creates the GitHub Release before the CPU and GPU bundles finish.
An initially empty `assets` array is therefore expected, not a failure. Keep
monitoring until the complete publish workflow finishes.

If an installer job fails, inspect its failed log before changing code. Retry the
failed jobs once only when the log proves a transient infrastructure problem such
as an incomplete package download or runner timeout:

```bash
gh run view <run-id> --log-failed
gh run rerun <run-id> --failed
gh run watch <run-id> --exit-status
```

For deterministic build, test, signing, or upload failures, fix the cause through
the normal PR flow and cut a newer version; never mutate an existing tag.

After a successful workflow, require exactly the four expected deliverables:
`FaceManager-Setup-X.Y.Z.exe`, `FaceManager-Setup-X.Y.Z.exe.sha256`,
`FaceManager-Setup-GPU-X.Y.Z.exe`, and its `.sha256`. Verify that each asset has a
non-zero size, URL, and digest; report both installer sizes and SHA-256 digests.
If CI failed before publishing, report that instead — never fill the gap by
tagging or creating the release manually.

### 8. Sync local branches after the release

Bring both long-lived branches up to date so the next cycle starts clean:

```bash
git fetch --all --tags --prune
git switch main   && git pull --ff-only origin main
git switch develop && git pull --ff-only origin develop
git status --short --branch
git diff --exit-code origin/main origin/develop
git rev-list --left-right --count origin/main...origin/develop
```

The two remote branches should have identical file content immediately after the
release, although `main` normally has one additional release merge commit. Do not
mistake that expected graph difference for content drift. Delete a leftover local
release-prep branch only after its PR is confirmed merged and the worktree is
clean; a squash-merged branch may require `git branch -D` because its original
commit is intentionally not an ancestor of `develop`.

If `develop` has already moved ahead, report that fact instead of forcing content
equality, and base subsequent work on the updated `develop`.

## Boundaries

Turns merged `develop` work into a published release. It does not author feature
work or changelog content — that happens earlier via **release-commit**.
