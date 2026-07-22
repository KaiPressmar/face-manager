---
name: release-commit
description: >
  Commit and push local changes the Face Manager way, then open (and auto-merge)
  a pull request into develop. Classifies the diff (user-visible / internal-only /
  mixed), routes user outcomes to CHANGELOG.md Unreleased in German and technical
  detail to the PR Developer Notes, writes a Conventional Commit, runs the
  validation gate, and fills the PR template. Use when the user says "commit and
  push", "commit my changes", "open a PR", "ship this work", "/release-commit".
  Not for cutting a version — use release-cut for that.
---

Drive the everyday change → PR → develop flow exactly as `CONTRIBUTING.md`
prescribes, so users get clean release notes and developers keep the detail.

## Golden rules (never break)

- Never push directly to `develop` or `main`. Everything lands via a PR into
  `develop`.
- Never bump `VERSION` or edit `frontend/package.json` / `-lock.json` here — that
  is `release-cut` only.
- Never edit a released `CHANGELOG.md` section. Only touch `## [Unreleased]`.
- Never `git tag` or `gh release create` — publishing is automated by CI.
- The in-app changelog is for users; the PR is for developers. Keep them separate.

## Steps

### 1. Inspect and classify the full diff

```bash
git status
git diff            # unstaged
git diff --staged   # staged
```

Classify the change as one of (`CONTRIBUTING.md` → "Release notes and developer
notes"):

- **User-visible** — a released-app user can notice a before/after difference: a
  capability, workflow, corrected bug, visible performance/reliability change,
  compatibility, or security behavior. → changelog required.
- **Internal-only** — value needs developer context: refactor, tests, CI,
  tooling, comments, internal logging, dependency refresh without behavior change,
  release automation, docs. → no changelog entry.
- **Mixed** — has both. → changelog for the visible outcome only; classify the PR
  as **User-visible**.

### 2. Route user outcomes to the changelog (German)

For user-visible / mixed work, add or refine consolidated bullets under
`## [Unreleased]` in `CHANGELOG.md`, in the correct category:

- `Neu` — new capability
- `Verbessert` — better existing behavior
- `Behoben` — corrected user-facing problem

**Write for a non-technical user. The entry must make it obvious what changes for
them.** Someone who has never seen the code should read the bullet and immediately
understand what is now different when they use the app.

- **German, everyday words.** No technical terms — no `Endpoint`, `Cache`, `SSE`,
  `Backend`, `Queue`, `Datenbank`, `API`, class/file names, or feature-flag names.
  If a normal user wouldn't say the word, don't use it.
- **Describe what the user notices, from their side.** State the concrete benefit
  or the fixed annoyance — "Importe laufen jetzt spürbar schneller", not "Import
  optimiert". Prefer the user's action or result over the mechanism.
- **One outcome per bullet, short and plain.** A single readable sentence. No
  "why" or implementation detail. Combine related changes and refine existing
  bullets instead of appending a commit log.
- **If you can't explain the benefit without technical context, it's internal —
  leave it out** (it belongs in the PR Developer Notes, not the changelog).
- Read each bullet back as if you were a first-time user: "What is different for
  me now?" If that isn't clear, rewrite it.

```
Too technical:  "Added SSE endpoint and thumbnail warmup queue."
Still unclear:  "Vorschau-Handling verbessert."
Clear for users: "Importe und Vorschaubilder aktualisieren ihren Status jetzt live im Hintergrund."

Too technical:  "Fixed null-pointer in face-cluster merge."
Clear for users: "Personen werden beim Zusammenführen nicht mehr versehentlich doppelt angezeigt."
```

Validate the changelog on its own before the full gate:

```bash
python3 scripts/changelog.py check
```

### 3. Ensure a conforming work branch

Work branches are `feature/*`, `fix/*`, `docs/*`, or `chore/*`. If on `develop`,
`main`, or a non-conforming branch (e.g. `agent/*`), move the work onto a
conforming branch cut from an up-to-date `develop`:

```bash
git fetch origin
git switch -c fix/<short-topic> origin/develop   # pick the right prefix
```

Confirm the branch name with the user if the prefix is ambiguous.

### 4. Commit (Conventional Commits)

Stage intentionally and commit. Follow the `caveman-commit` conventions:
`<type>(<scope>): <imperative summary>`, types `feat|fix|refactor|perf|docs|test|
chore|build|ci|style|revert`, imperative, lowercase after the colon, no trailing
period, subject ≤50 (hard cap 72). Add a body only for a non-obvious *why*,
breaking changes, or migrations. Reference issues at the end (`Closes #42`).

### 5. Run the full validation gate

```bash
./scripts/check-all.sh
```

Fix anything it reports before continuing.

### 6. Push and open the PR into develop

```bash
git push -u origin HEAD
gh pr create --base develop --title "<conventional-commit-style title>" --body "<template>"
```

Fill the PR template (`.github/PULL_REQUEST_TEMPLATE.md`):

- **Change Classification** — check exactly one box. Mixed ⇒ **User-visible
  change**.
- **User Impact** — observable before/after, or `None` for internal-only.
- **Developer Notes** — all implementation, architecture, migrations, deps, risk,
  tests, CI, and maintenance detail. This is where developer-level information
  lives — never in the changelog.
- **Validation** — tick the boxes that hold (`check-all.sh` passed, changelog
  updated for user-visible outcomes, `VERSION` unchanged, etc.).

### 7. Auto-merge

```bash
gh pr merge --squash --auto
```

Report the PR URL and its merge state. PRs into `develop` are squash-merged.

### 8. Sync local branches after the merge

Once the PR actually merges, bring the local repo up to date so the next task
starts clean:

```bash
gh pr checks --watch          # wait for the merge to land
git switch develop
git pull --ff-only origin develop
git branch -d <work-branch>   # the squash-merged branch is now redundant
```

If you keep working on a follow-up branch, cut it (or rebase it) from the freshly
updated `develop` so it never lags behind.

## Boundaries

Handles commit + PR into `develop` only. It does not release. When the user wants
to cut a version, hand off to **release-cut**.
