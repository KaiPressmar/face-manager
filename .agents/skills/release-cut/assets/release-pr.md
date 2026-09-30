## Summary

Publish the combined, reviewed Face Manager release from develop to main.

## Change Classification

- [x] **User-visible change** — users can notice a feature, fix, workflow, performance, reliability, compatibility, or security difference
- [ ] **Internal-only change** — refactoring, tests, CI, tooling, documentation, dependencies, or release mechanics without a changed user experience

## User Impact

Users receive the improvements described in the finalized release section of `CHANGELOG.md`.

## Developer Notes

- This develop-to-main pull request is the single release boundary.
- CI creates the annotated tag, release notes, checksums, provenance attestations, and Windows installers after merge.

## Validation

- [x] `./scripts/check-all.sh` passes locally
- [x] User-facing behavior was tested where applicable
- [x] Documentation was updated where applicable
- [x] User-visible outcomes are covered by the finalized changelog section
- [x] `VERSION` was changed only when preparing a release
- [x] The release follows the checklist in `CONTRIBUTING.md`
