## Summary

Describe what changed and why.

## Change Classification

Check exactly one option. For mixed work, select **User-visible change** and put
only the observable outcome in the changelog.

- [ ] **User-visible change** — users can notice a feature, fix, workflow, performance, reliability, compatibility, or security difference
- [ ] **Internal-only change** — refactoring, tests, CI, tooling, documentation, dependencies, or release mechanics without a changed user experience

## User Impact

Describe the observable before/after behavior. Write “None” for internal-only changes.

## Developer Notes

Record implementation details, architecture, migrations, dependencies, risks,
tests, and release or maintenance work here—not in the in-app changelog.

## Target Branch

- Feature, fix, documentation, or dependency PRs target `develop`.
- Only the release PR from `develop` targets `main`.
- Release PRs use a unique version and include only the intended release state.

## Validation

- [ ] `./scripts/check-all.sh` passes locally
- [ ] User-facing behavior was tested where applicable
- [ ] Documentation was updated where applicable
- [ ] User-visible outcomes are covered under `CHANGELOG.md` → `Unreleased`; internal-only details remain in this PR
- [ ] `VERSION` was changed only when preparing a release
- [ ] A release PR follows the release checklist in `CONTRIBUTING.md`

## Screenshots

Add screenshots for visible UI changes, or remove this section.
