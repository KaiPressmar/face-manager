# Claude Repository Instructions

Follow `CONTRIBUTING.md` and the repository-wide instructions in `AGENTS.md`.

For every implementation task, inspect the complete local diff before finishing
and classify it as user-visible, internal-only, or mixed. `CHANGELOG.md` is shown
inside the application: add only behavior, features, fixes, performance, or
reliability improvements that users of the released application can actually
notice. Write those outcomes in concise, high-level German for non-technical
users and consolidate related changes.

Do not add changelog entries for refactors, tests, CI, developer tooling,
comments, internal logging, dependency maintenance, or release mechanics unless
they materially change the delivered user experience. Put those details in the
pull request's **Developer notes**. For mixed work, keep only the user-facing
outcome in the changelog and keep code symbols, files, endpoints, migrations,
libraries, safeguards, and tests in the PR.

Check exactly one classification in the PR template. A mixed PR is classified
as **User-visible change** because it requires a curated changelog item.

Do not modify released changelog sections or bump the application version during
ordinary work. The release helper converts `Unreleased` into the dated version
section. Validate with `python3 scripts/changelog.py check` and then
`./scripts/check-all.sh`.
