# Repository Instructions for Codex Agents

## Separate release notes from developer notes

Before finishing any implementation, review the complete local diff and classify
the pull request as **user-visible**, **internal-only**, or **mixed**. The
`CHANGELOG.md` is displayed inside Face Manager and therefore contains only
outcomes a person using the released application can notice.

- **User-visible:** Add or refine an item under `## [Unreleased]`. This includes
  features, changed workflows, visible performance or reliability improvements,
  and bugs whose before/after behavior a user can observe.
- **Internal-only:** Do not add a changelog item. Refactors, tests, CI, developer
  tooling, comments, internal logging, dependency maintenance, and release
  mechanics belong in the pull request's **Developer notes**.
- **Mixed:** Add only the user-visible outcome to the changelog. Put technical
  implementation, migrations, safeguards, tests, and maintainability work in
  **Developer notes**.
- A technical change belongs in the changelog only when it changes the delivered
  user experience. Describe that experience generically; never expose the
  implementation merely to make the change sound release-relevant.

For every changelog item:

- Write concise German for end users without technical background.
- Use only `Neu`, `Verbessert`, or `Behoben`.
- Describe outcomes and user value, not files, APIs, database tables, libraries,
  algorithms, implementation details, or internal refactors.
- Combine related work into a small number of high-level bullets. Do not create
  a commit log.
- Update an existing bullet when it already covers the outcome instead of adding
  a duplicate.
- If a user cannot understand what changed without developer context, omit it.
- Never edit an already released section and never move `Unreleased` manually.
  `scripts/release-version.sh` finalizes it during release preparation.
- Do not bump `VERSION` during ordinary implementation work.
- Do not maintain GitHub Release notes separately or generate an automatic
  commit list. The release workflow renders and synchronizes them from the
  matching finalized `CHANGELOG.md` section.

In the pull request template, check exactly one change classification. For a
mixed pull request choose **User-visible change**, maintain the curated
changelog, and record the internal portion under **Developer notes**.

Run `python3 scripts/changelog.py check` after editing the changelog and run
`./scripts/check-all.sh` before handing off completed implementation work.

The full development, changelog, and release process is documented in
`CONTRIBUTING.md`.

## Skills

Two first-party skills encode the commit and release runbooks. Follow them
instead of improvising the git, pull request, or release steps:

- **release-commit** (`.agents/skills/release-commit/SKILL.md`) — commit and push
  local changes: classify the diff, update the German `CHANGELOG.md` `Unreleased`
  section, write a Conventional Commit, run `./scripts/check-all.sh`, and open
  (auto-merge) a pull request into `develop`.
- **release-cut** (`.agents/skills/release-cut/SKILL.md`) — cut the next version:
  run `scripts/release-version.sh`, land the release-prep pull request into
  `develop`, then the release pull request into `main`, and let CI tag and
  publish. Never tag or create the GitHub release by hand.
