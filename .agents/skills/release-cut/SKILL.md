---
name: release-cut
description: >
  Cut the next Face Manager version end to end. Runs the release-preparation
  runbook: bumps VERSION via scripts/release-version.sh, finalizes the German
  CHANGELOG Unreleased section into a dated release, opens and auto-merges the
  release-prep PR into develop, then the release PR from develop into main, and
  reports the release that CI publishes. Use when the user says "cut a release",
  "create the next release", "release a new version", "/release-cut". Does not tag
  or create the GitHub release itself — CI does that.
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
gh pr create --base develop --title "Release v$(cat VERSION)" --body "<template: Internal-only / release mechanics>"
gh pr merge --squash --auto
```

Wait for CI to pass and the PR to actually merge before the next hop
(`gh pr checks --watch`, `gh run watch`).

### 6. Release PR from develop into main (merge commit, auto-merge, wait for CI)

```bash
git switch develop && git pull --ff-only origin develop
gh pr create --base main --head develop --title "Release v$(cat VERSION)" --body "Release vX.Y.Z"
gh pr merge --merge --auto
```

The base is `main`, the head is `develop`. The Main Branch Policy workflow only
allows PRs into `main` from `develop`. Wait for CI to pass and the merge to land.

### 7. Let CI publish; then report

Do **not** tag or create the release. Monitor the automated publish and report the
result:

```bash
gh run watch          # the "Publish Release" workflow_run
gh release view "v$(cat VERSION)" --web
```

Report the release URL and the uploaded installers
(`FaceManager-Setup-X.Y.Z.exe`, `FaceManager-Setup-GPU-X.Y.Z.exe`, and their
`.sha256`). If CI failed before publishing, report that instead — never fill the
gap by tagging or creating the release manually.

### 8. Sync local branches after the release

Bring both long-lived branches up to date so the next cycle starts clean:

```bash
git fetch --all --tags --prune
git switch main   && git pull --ff-only origin main
git switch develop && git pull --ff-only origin develop
git branch -d chore/release-preparation   # squash-merged, now redundant
```

`develop` and `main` should now agree on the released version. If `develop` has
already moved ahead, rebase or fast-forward any in-flight work branch onto the
updated `develop` before continuing.

## Boundaries

Turns merged `develop` work into a published release. It does not author feature
work or changelog content — that happens earlier via **release-commit**.
