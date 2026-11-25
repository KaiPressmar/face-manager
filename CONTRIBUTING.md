# Contributing

This document is the source of truth for the development and release workflow.
The [CI workflow](.github/workflows/ci.yml), [main-branch policy](.github/workflows/main-branch-policy.yml),
and [release workflow](.github/workflows/release.yml) are the executable source
of truth if this document and automation ever disagree.

## Workflow Overview

```text
work branch --------> PR -----> develop
release-prep branch -> PR -----> develop
                                  |
                                  v
                          release PR to main
                                  |
                             merge commit
                                  |
                                  v
                                main
                                  |
                           successful CI
                                  |
                                  v
                  tag + release + desktop artifacts
```

## Branch Model

| Branch | Purpose | Accepted changes |
| --- | --- | --- |
| `develop` | Integration branch for the next release | Feature, fix, documentation, dependency, and release-preparation changes through pull requests |
| `main` | Exact source of released versions | Release pull requests whose source branch is `develop` |
| `feature/*`, `fix/*`, `docs/*`, `chore/*` | Focused work | Changes intended for a pull request into `develop` |

Do not push feature work directly to `develop` or `main`. Do not open a pull
request to `main` from any branch other than `develop`; the **Main Branch
Policy** workflow rejects it.

Suggested branch names:

```text
feature/folder-metadata
fix/import-progress
docs/setup-notes
chore/dependency-updates
```

## Development Workflow

### 1. Start from the current integration branch

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/my-change
```

Choose the prefix that matches the work. Keep unrelated changes in separate
branches and pull requests.

### 2. Prepare and run the application

Install or reconcile the Ubuntu/WSL2 development environment:

```bash
./scripts/setup-dev.sh
```

The script is safe to rerun. See the setup and manual development sections in
`README.md` for CPU/GPU selection, terminal commands, and VS Code launch tasks.

Start the backend with `./scripts/dev-backend.sh` and the frontend with
`npm --prefix frontend run dev`. The backend watcher intentionally reloads only
runtime Python sources; tests, the virtual environment, generated data, and
frontend changes do not restart it. Vite handles frontend source changes with
HMR, proxies `/api` to port 8000, and fails clearly if its fixed port 5173 is
already occupied.

### 3. Validate the change

Run the complete local check before pushing:

```bash
./scripts/check-all.sh
```

That command runs, in order:

1. Semantic-version consistency checks.
2. Changelog format checks.
3. Packaging metadata and dependency-inventory checks.
4. Python dependency validation, compilation, and the backend unit tests.
5. Frontend behavior tests, TypeScript checking, and a production Vite build.

Backend tests use an isolated temporary data directory, so validation does not
initialize or lock the library used by a running development server.

Also test user-visible behavior manually where applicable. Update documentation
in the same pull request when commands, configuration, UI behavior, or release
outputs change.

The manually triggered **Windows and Ubuntu SMB interoperability proof** drives
separate Windows UNC and Ubuntu 26.04 CIFS clients against a temporary Windows
SMB share on one host. To run it directly, use elevated PowerShell on a Windows
test machine with the SMB Server service running and an Ubuntu WSL2 distribution
in NAT mode with `cifs-utils`, `iproute2`, and `python3` installed.

```powershell
./scripts/test-wsl-smb.ps1 -Distribution Ubuntu-26.04
```

### Release notes and developer notes

Before opening or updating a pull request, review the complete change from an
end-user perspective. Classify the PR using exactly one checkbox in the pull
request template:

| Classification | Changelog | Pull request |
| --- | --- | --- |
| User-visible | Required under `Unreleased` | Summarize user impact and record technical details under **Developer notes** |
| Internal-only | No release-note item | Explain purpose, implementation, risk, and validation under **Developer notes** |
| Mixed | Required only for the visible outcome; select **User-visible change** | Keep all internal work under **Developer notes** |

A change is user-visible when someone using the released application can notice
a meaningful before/after difference: a capability, workflow, corrected bug,
visible performance improvement, reliability improvement, compatibility change,
or security behavior. A change is internal-only when its value requires
developer context, such as a refactor, test, CI change, developer tooling,
comment, internal log, dependency refresh without changed behavior, or release
automation.

For every user-visible outcome, add or refine an entry under `## [Unreleased]`
in `CHANGELOG.md`:

- Write concise German for people using the application, not for developers.
- Use `Neu` for new capabilities, `Verbessert` for better existing behavior, and
  `Behoben` for corrected user-facing problems.
- Describe the outcome rather than files, endpoints, database changes, classes,
  algorithms, dependencies, tests, or internal architecture.
- Combine related changes and update existing bullets instead of producing a
  chronological commit list.
- Do not mention internal work generically just to include it. If users cannot
  understand the benefit without implementation context, leave it out.
- Never edit a released version section. Corrections to released behavior belong
  in the next `Unreleased` section.

Example:

```text
Avoid:  "Added SSE endpoint and thumbnail warmup queue."
Prefer: "Importe und Vorschaubilder aktualisieren ihren Status jetzt live im Hintergrund."

Internal-only example — PR developer notes, no changelog item:
"Refactored event subscriptions and added reconnect regression tests."

Mixed example:
- Changelog: "Aktivitäten aktualisieren sich nach einer unterbrochenen Verbindung wieder automatisch."
- Developer notes: "Centralized SSE reconnect handling and added lifecycle tests."
```

Validate the format directly when working only on release notes:

```bash
python3 scripts/changelog.py check
```

For pull requests, CI compares the branch with its base and reads the selected
classification from the PR body. User-visible application changes must update
`CHANGELOG.md`. Internal-only changes may omit it, preventing developer work from
creating noisy in-app release notes. Missing or contradictory classifications
fail **Metadata and scripts**.

### 4. Open a pull request into `develop`

```bash
git push -u origin HEAD
```

Open the pull request with `develop` as its base branch and complete the pull
request template. `VERSION` must remain unchanged in ordinary development pull
requests.

The **CI** workflow runs for pull requests and pushes to `develop` and `main`:

| Check | What it validates |
| --- | --- |
| `Metadata and scripts` | Shell syntax, version and changelog consistency, generated Windows metadata, UPX policy, and dependency inventory generation |
| `Backend` | Python dependencies, module compilation, and backend unit tests |
| `Frontend` | Locked npm installation, TypeScript checking, and production build |

Newer runs on the same branch cancel older in-progress CI runs. A pull request
is ready to merge only when all required checks pass and review is complete.

Use a squash merge for ordinary pull requests into `develop` so each focused
change has one integration commit.

## Release Workflow

### Release invariants

- Every released source commit has one unique semantic version.
- Every release pull request comes from `develop` and targets `main`.
- `main` receives released code only; do not merge an unversioned documentation
  or maintenance change directly into it.
- Never move an existing version tag or publish a different commit using an
  existing version number. If released code changes, create at least a patch
  release.
- The current Windows artifacts are not Authenticode-signed. Checksums and
  GitHub attestations prove integrity and build origin but do not suppress
  Windows SmartScreen warnings.

### 1. Prepare the version through `develop`

Wait until all intended changes are merged into `develop`, then update it:

```bash
git switch develop
git pull --ff-only origin develop
```

Choose the next semantic version. The helper accepts `major`, `minor`, `patch`,
or an explicit `X.Y.Z` value:

- `patch`: backward-compatible fixes or maintenance.
- `minor`: backward-compatible user-facing functionality.
- `major`: incompatible behavior, data, or workflow changes.

```bash
git switch -c chore/release-preparation
./scripts/release-version.sh patch
# or: ./scripts/release-version.sh 1.2.0
```

The helper updates the three canonical version files, finalizes the changelog,
and does not commit or tag anything:

- `VERSION`
- `frontend/package.json`
- `frontend/package-lock.json`
- `CHANGELOG.md`: moves non-empty `Unreleased` categories into a dated
  `vX.Y.Z` section and creates a new empty `Unreleased` template.

Release preparation fails if `Unreleased` has no user-facing item or if the
target version is not newer than the current version or already exists in the
changelog.

Review and validate the release preparation:

```bash
git diff -- VERSION frontend/package.json frontend/package-lock.json CHANGELOG.md
./scripts/check-all.sh
```

Commit and push the version bump on the preparation branch:

```bash
git add VERSION frontend/package.json frontend/package-lock.json CHANGELOG.md
git commit -m "Release v$(cat VERSION)"
git push -u origin HEAD
```

Open a pull request from the preparation branch into `develop`, let CI pass,
and squash-merge it. This keeps the configured pull-request requirement on
`develop` intact. Then update the local integration branch:

```bash
git switch develop
git pull --ff-only origin develop
```

### 2. Open and merge the release pull request

Open exactly one pull request with:

- Base branch: `main`
- Source branch: `develop`
- Title: `Release vX.Y.Z`

Confirm that the pull request contains all commits intended for the release,
the three canonical version files and dated changelog section contain the
selected version, and all CI plus **Require develop as source** checks pass.

Merge the release pull request with a merge commit. This preserves the release
boundary and records the complete `develop` history in `main`.

### 3. Automated publication after the merge

A push to `main` runs **CI** first. The **Publish Release** workflow is triggered
for the exact tested commit through `workflow_run`, but its jobs proceed only
when that CI run succeeds.

The publication then performs these steps:

1. Check out the exact successful `main` commit and validate its version.
2. Extract the curated `vX.Y.Z` section from `CHANGELOG.md`.
3. Create and push the annotated `vX.Y.Z` tag if it does not exist.
4. Create a GitHub Release draft from the curated notes or refresh the draft
   for an unchanged retry.
5. Build and verify the Windows installer and Ubuntu 26.04 amd64 package on
   their target runners. The Windows job tests upgrades from published CPU, GPU,
   and combined installers.
6. Upload the desktop installers, the byte-identical Windows GPU compatibility
   copy, and their SHA-256 files to the draft. Public builds provide GitHub
   provenance attestations for the installers.
7. After all packaging jobs succeed, verify the draft's tag, all six assets, and
   the uploaded Windows installer digests and checksum contents. Publish the
   draft and mark it as the latest release.

Users continue to see the previous latest release until these checks succeed.

GitHub Release notes are never maintained as a separate changelog. The workflow
always renders them from the matching released section in `CHANGELOG.md`. On a
retry for the same unchanged release commit, it synchronizes the draft title
and notes before continuing with the artifacts.

### Published artifacts

| Artifact | Purpose |
| --- | --- |
| `FaceManager-Setup-X.Y.Z.exe` | Shared Windows x64 installer for NVIDIA, Intel GPU and CPU |
| `FaceManager-Setup-X.Y.Z.exe.sha256` | Windows installer checksum |
| `FaceManager-Setup-GPU-X.Y.Z.exe` | Byte-identical compatibility copy for legacy Windows GPU updaters |
| `FaceManager-Setup-GPU-X.Y.Z.exe.sha256` | Windows compatibility checksum with the legacy filename |
| `face-manager_X.Y.Z_amd64.deb` | Ubuntu 26.04 amd64 package for NVIDIA, Intel GPU and CPU |
| `face-manager_X.Y.Z_amd64.deb.sha256` | Ubuntu package checksum |

For public repositories, provenance can also be verified with GitHub CLI:

```bash
gh attestation verify FaceManager-Setup-X.Y.Z.exe \
  --repo KaiPressmar/face-manager
```

The adjacent checksum can be verified from Linux/WSL2 with:

```bash
sha256sum --check FaceManager-Setup-X.Y.Z.exe.sha256
```

The dependency inventory generated under `build/` is a local review aid. It is
not packaged into the application or uploaded to the GitHub Release.

The same curated version section is bundled with the desktop application. On
the first start after an update, the UI shows it once as **Neu in Face Manager**.
After dismissal, users can reopen it by clicking the version number beside the
application name in the sidebar.

The application also checks the public GitHub latest-release endpoint at startup
and then no more than once per hour unless the user triggers a manual check.
Release assets must retain the filenames above and their matching `.sha256`
files. The updater chooses the asset for the installed platform and verifies
its SHA-256 before allowing installation. Legacy Windows GPU updaters use the
compatibility filename. Keep that file and its own checksum on each latest stable
release while those updaters are supported. The shared Windows installer is also
available directly from GitHub Releases. Ubuntu package users can open the
release page and install the downloaded `.deb` with APT. Keep the explicit
installation confirmation for unsigned Windows installers. Update-check settings
and skipped versions are persisted locally.

### Failures and retries

- If CI on `main` fails, publication does not proceed. Fix the issue through
  `develop` and a new release pull request. The version may be reused only if no
  tag or GitHub Release was created for it.
- If a packaging or publication job fails for an unchanged release commit,
  rerun the failed GitHub Actions jobs. The workflow reuses the existing tag
  and draft, and replaces matching draft assets on retry.
- If fixing the failure changes application source after a tag or GitHub Release
  exists, bump at least the patch version and publish a new release. Do not
  replace an existing version with binaries from another commit.

## Manual desktop release builds

Follow [Build desktop installers](README.md#build-desktop-installers) for the
Windows x64 and Ubuntu amd64 build commands. For an existing Windows signing
setup, `-RequireSigned` verifies that both executables have valid Authenticode
signatures. The automated workflow currently produces unsigned Windows builds.

## Recommended GitHub Protection

Keep `main` as the repository default branch so visitors see the released
version by default. Open development pull requests against `develop`.
Repository administrators can run `./scripts/configure-github-repo.sh` with a
GitHub CLI credential that can edit repository settings to set the description,
latest-release link and topics.
Configure branch protection under **Settings > Rules > Rulesets**.

### `develop`

- Require a pull request before merging.
- Require `Backend`, `Frontend`, and `Metadata and scripts`.
- Require branches to be up to date before merging.
- Block force pushes and deletion.

### `main`

- Require a pull request before merging.
- Require the three CI checks and `Require develop as source`.
- Require branches to be up to date before merging.
- Block force pushes and deletion.
- Use merge commits for release pull requests.
