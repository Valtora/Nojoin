# Support

How to get help with Nojoin, and what to realistically expect from the maintainers.

## Before You Ask

- Read the [documentation index](../docs/README.md); most setup, deployment, capture, and usage questions are answered there.
- Search [existing issues](https://github.com/Valtora/Nojoin/issues) to avoid duplicates.
- Reproduce the problem on the latest released version (see the **Supported Versions** note below).

## Where to Go

- **Bugs and regressions:** open a bug report.
- **Browser-capture or platform problems:** open a platform compatibility report.
- **Feature ideas:** open a feature request.
- **Security vulnerabilities:** do not open a public issue. Use GitHub Private Vulnerability Reporting as described in the [security policy](../docs/SECURITY.md).
- **Code of Conduct concerns:** follow the private reporting process in the [Code of Conduct](../docs/CODE_OF_CONDUCT.md).

## Supported Versions

Nojoin is in active development and should be considered pre-release. Only the latest released version is supported, matching the [security policy](../docs/SECURITY.md). Please reproduce and report issues against the latest version.

## Maintainer Response and Triage

Nojoin is maintained on a best-effort basis by a small team. To set honest expectations:

- **Security reports:** initial acknowledgement within 48 hours, per the [security policy](../docs/SECURITY.md).
- **Bug and platform reports:** triaged and labelled as maintainer time permits. Clear reproduction steps, version, deployment mode, and redacted logs speed this up considerably.
- **Feature requests:** read and considered, but acceptance and scheduling are not guaranteed.
- **Pull requests:** reviewed when maintainer time allows. PRs that pass all required checks and update the affected documentation are the easiest and fastest to merge.

Outside the security acknowledgement window, there is no guaranteed response-time commitment. Well-scoped, reproducible reports and self-contained pull requests receive attention soonest.

## Triage Cadence and Labels

Nojoin is single-maintainer, so triage is a regular self-applied pass rather than a rota. The cadence is deliberately modest so it can be met honestly:

- **Weekly:** sweep new issues and pull requests. Apply a severity, scope, platform (where relevant), and release-impact label, and remove `needs-triage`. Security reports are handled out-of-band on the 48-hour acknowledgement commitment in the [security policy](../docs/SECURITY.md), not on this weekly cadence.
- **Per release:** before tagging, review open `severity:critical` and `severity:high` issues and any `release:breaking` or `release:migration-required` items so release notes and the upgrade guidance are accurate.
- **Quarterly:** the repository-quality re-audit (see [repo-maintenance.md](../docs/repo-maintenance.md)) also reviews stale and unlabelled issues.

New issues opened from the templates should arrive with `needs-triage`; the weekly sweep replaces it with the substantive labels.

### Label Taxonomy

The canonical label set is defined in [labels.yml](labels.yml), grouped as:

- **Severity:** `severity:critical`, `severity:high`, `severity:medium`, `severity:low`.
- **Scope:** `scope:backend`, `scope:worker`, `scope:frontend`, `scope:capture`, `scope:migration`, `scope:deployment`, `scope:docs`, `scope:security` (mirrors [CODEOWNERS](CODEOWNERS)).
- **Platform:** `platform:windows`, `platform:linux`, `platform:macos`, `platform:android`, `platform:ios`, plus the `platform-issue` triage label.
- **Release impact:** `release:breaking`, `release:migration-required`, `release:safe`.
- **Workflow:** `needs-triage`, `flaky`, `slow-test`, `dependencies`, `audit`.

### Applying the Labels (maintainer-action-pending)

Labels exist only in GitHub repository settings, so `labels.yml` must be applied with the GitHub CLI. The commands below mirror `labels.yml`; run them once from an authenticated `gh` (no other tooling required). `gh label create --force` creates the label or updates it in place, so the set is safe to re-run whenever `labels.yml` changes:

```bash
gh label create "severity:critical" --color b60205 --description "Data loss, security exposure, or the product is unusable." --force
gh label create "severity:high"     --color d93f0b --description "Major function broken with no easy workaround." --force
gh label create "severity:medium"   --color fbca04 --description "Important but with a workaround, or affects a subset of users." --force
gh label create "severity:low"      --color 0e8a16 --description "Minor issue, cosmetic, or low-impact edge case." --force
gh label create "scope:backend"     --color 1d76db --description "Backend API and shared backend code." --force
gh label create "scope:worker"      --color 1d76db --description "Celery worker, ML, and the processing pipeline." --force
gh label create "scope:frontend"    --color 1d76db --description "Web frontend (excluding browser capture)." --force
gh label create "scope:capture"     --color 1d76db --description "Browser capture under frontend/src/lib/capture." --force
gh label create "scope:migration"   --color 5319e7 --description "Alembic migrations and database schema changes." --force
gh label create "scope:deployment"  --color 5319e7 --description "Docker, compose, CI, and the release pipeline." --force
gh label create "scope:docs"        --color 0075ca --description "Documentation only." --force
gh label create "scope:security"    --color b60205 --description "Auth, session, token, or encryption boundaries." --force
gh label create "platform:windows"  --color c5def5 --description "Specific to Windows." --force
gh label create "platform:linux"    --color c5def5 --description "Specific to Linux." --force
gh label create "platform:macos"    --color c5def5 --description "Specific to macOS." --force
gh label create "platform:android"  --color c5def5 --description "Specific to Chrome on Android (microphone-only capture)." --force
gh label create "platform:ios"      --color c5def5 --description "Specific to Chrome on iOS (microphone-only capture)." --force
gh label create "platform-issue"    --color d4c5f9 --description "Browser-capture or platform compatibility report (triage queue)." --force
gh label create "release:breaking"  --color b60205 --description "Backwards-incompatible change for operators or API clients." --force
gh label create "release:migration-required" --color d93f0b --description "Requires a database migration on upgrade." --force
gh label create "release:safe"      --color 0e8a16 --description "Drop-in change with no upgrade action required." --force
gh label create "needs-triage"      --color ededed --description "Awaiting maintainer triage and labelling." --force
gh label create "flaky"             --color e99695 --description "Intermittently failing test (see DEVELOPMENT.md test reliability)." --force
gh label create "slow-test"         --color fef2c0 --description "Test flagged as slow by pytest --durations." --force
gh label create "dependencies"      --color 0366d6 --description "Dependency update (used by Dependabot pull requests)." --force
gh label create "audit"             --color 0052cc --description "Periodic repository-quality re-audit (GOV-008)." --force
```

This step is a one-time (then on-change) maintainer action and is not enforced from the repository tree. `.github/labels.yml` remains the canonical definition; keep these commands in step with it when the taxonomy changes.
