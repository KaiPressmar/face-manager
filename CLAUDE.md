# Claude Repository Instructions

Read and follow `AGENTS.md`; its orchestration, changelog, validation, and
release rules apply to Claude too. Read the relevant workflow in
`CONTRIBUTING.md` before implementation or release work.

For Claude subagents, select an available model explicitly for each task or
supported preset: `haiku` for routine lookup or mechanical edits, `sonnet` for
scoped implementation or review, and `opus` for difficult reasoning. Use only
aliases installed in the active runtime. Do not assume a built-in agent such
as Explore is cheap; it may inherit the parent model. Use medium effort for
ordinary Sonnet work and high effort for complex work only when the runtime
exposes those controls. Inspect the effective model and effort in `/tasks` when
available. If selection is unsupported, report that limit rather than imply
an override. Recheck capability changes at
[Claude subagent documentation](https://code.claude.com/docs/en/sub-agents).
