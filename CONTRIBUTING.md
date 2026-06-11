# Contributing

## Branch Model

- `main` contains released code only.
- `develop` is the integration branch for the next release.
- Create feature branches from `develop`.
- Open feature, fix, documentation, and dependency pull requests into
  `develop`.
- Open one release pull request from `develop` into `main`.
- Do not push feature work directly to `main`.

Suggested feature branch names:

```text
feature/folder-metadata
fix/import-progress
docs/setup-notes
chore/dependency-updates
```

## Start Work

Update the integration branch and create a focused branch:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/my-change
```

Install or reconcile the local environment:

```bash
./scripts/setup-dev.sh
```

Run the application through the VS Code **Full Stack: Debug** launch
configuration or through the documented terminal commands.

## Validate Changes

Run the same checks used by GitHub Actions:

```bash
./scripts/check-all.sh
```

Then push the feature branch and open a pull request targeting `develop`.

## Prepare a Release

Once `develop` contains the desired release:

1. Switch to `develop` and pull the latest changes.
2. Bump the semantic version.
3. Run all checks.
4. Commit and push the version change.
5. Open a pull request from `develop` to `main`.

```bash
git switch develop
git pull --ff-only origin develop
./scripts/release-version.sh minor
./scripts/check-all.sh
git add VERSION frontend/package.json frontend/package-lock.json
git commit -m "Release v$(cat VERSION)"
git push origin develop
```

After the release PR merges, the **Publish Release** GitHub workflow creates
an annotated `vX.Y.Z` tag and a GitHub Release with generated notes.

## Recommended GitHub Protection

Configure branch protection in the GitHub repository settings:

### `develop`

- Require a pull request before merging
- Require the `Backend`, `Frontend`, and `Metadata and scripts` CI checks
- Require branches to be up to date before merging
- Block force pushes and deletion

### `main`

- Require a pull request before merging
- Require the three CI checks and `Require develop as source`
- Require branches to be up to date before merging
- Block force pushes and deletion

Use squash merges for feature PRs into `develop`. Use a merge commit for the
release PR from `develop` to `main` so the release boundary remains visible.
