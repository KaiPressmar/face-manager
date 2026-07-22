## Summary

Prepare the next Face Manager release by updating canonical version metadata and finalizing the curated German changelog.

## Change Classification

- [ ] **User-visible change** — users can notice a feature, fix, workflow, performance, reliability, compatibility, or security difference
- [x] **Internal-only change** — refactoring, tests, CI, tooling, documentation, dependencies, or release mechanics without a changed user experience

## User Impact

None. This pull request only prepares release metadata for changes already reviewed and merged into develop.

## Developer Notes

- Updates VERSION and frontend package metadata.
- Finalizes the existing Unreleased changelog entries for the target release.

## Validation

- [x] `./scripts/check-all.sh` passes locally
- [x] User-facing behavior was tested where applicable
- [x] Documentation was updated where applicable
- [x] User-visible outcomes are covered by the finalized changelog section
- [x] `VERSION` was changed only when preparing a release
- [x] The release follows the checklist in `CONTRIBUTING.md`
