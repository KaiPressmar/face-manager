# Repository Instructions for Codex and Claude Agents

## Agent orchestration (Claude and Codex)

The main agent owns requirements, decisions, integration, and the complete final
diff. Keep a compact task ledger with each worker's owner, scope, model/effort,
status, evidence, and blockers, plus key decisions and dependencies; update it
as assignments finish or change. Default to delegating substantive
investigation, implementation, testing, and review; keep the main session focused
on orchestration. Do trivial work inline when delegation costs more than it saves,
or work locally when delegation is unavailable. Respect
the active runtime's tools, limits, and instructions.

- Assign one writer per file. Start with one or two workers; add independent,
  useful parallel work only within runtime limits. Workers must not delegate
  again without a specific assignment from the main agent.
- Workers must not mutate git state (commit, branch, merge, push, or PR). Avoid
  duplicate investigation and full test runs. Workers may run focused checks
  for their own changes and report blockers promptly. The main agent integrates
  work, checks worker evidence against the code, reviews the complete diff, and
  owns the required final validation and handoff; it may assign one worker to
  run validation, then assess that worker's evidence before handoff.
- Give each worker a self-contained, minimal task: objective, relevant paths,
  applicable repository and skill constraints, acceptance checks, and an output
  budget (about 200 words by default). Do not rely on a fresh worker inheriting
  these instructions. Fork only the context needed. Request concise evidence
  with paths, checks, risks, and unresolved uncertainty; do not ask workers to
  hide doubt.
- Search with `rg` and read relevant slices; avoid whole-repository dumps. Batch
  independent reads, retain long logs outside the main context, and return only
  decisive evidence. Load skills on demand; do not repeatedly reread instructions.
- Reuse a worker for a related task at the same model tier; otherwise create a
  fresh, scoped handoff. Use skill-provided presets only when available. Cost
  routing is a heuristic, not a guarantee of optimality or savings:

  | Work | Model tier | Reasoning |
  | --- | --- | --- |
  | Routine lookup or mechanical edit | Lightweight | Low |
  | Scoped implementation, debugging, or review | Balanced | Medium |
  | Ambiguous architecture, concurrency, or security | Frontier | High |

Escalate the approach or model when concrete failure or uncertainty justifies
it; do not repeat cheap guesses. Continue until the task is done or a concrete
blocker remains. Use maximum or ultra reasoning only for a specific hard problem.
Before selecting a model, inspect the models and controls exposed by the live
tool. If a model or effort override is unsupported, report the limit rather
than claiming it was applied.

For Codex, current-session examples are `gpt-6-luna` at low/medium for routine
work, `gpt-6-sol` at medium for normal scoped work, and `gpt-6-astra` at high
for difficult work. These are illustrative, not pricing guarantees or a fixed
model catalog. Choose a task-appropriate model and effort explicitly when
supported. When `fork_turns` is exposed, prefer `"none"` or the smallest
relevant integer for a self-contained assignment. Full-history forks inherit the
parent's settings and may not accept overrides; follow the live tool contract.
Recheck capabilities when they change:
[Codex subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).

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
