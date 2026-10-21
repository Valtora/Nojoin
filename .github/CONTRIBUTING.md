# Contributing to Nojoin

Nojoin is free and open-source software licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

## Licensing of Contributions

No contributor agreement is required. By opening a pull request you agree that your contribution is licensed under the AGPLv3 for inclusion in Nojoin.

## How to Contribute

1. Fork the repository and create a branch for your change.
2. Commit using [Conventional Commits](https://www.conventionalcommits.org/).
3. Open a pull request against `main` and complete the template.

Help is especially welcome with:

- **Bug reports** with reproduction steps (see [Reporting Issues](#reporting-issues)).
- **Platform testing:** shared-audio capture in Chrome on Windows, Linux and macOS and in other Chromium-family browsers, microphone-only capture in Chrome on Android and iOS, and the unsupported-browser message in Firefox, Safari and other mobile browsers.
- **Code and documentation** fixes and improvements.

## Development Setup

[docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md) covers setup, dependency sets and checks. Prerequisites:

- Python 3.12
- Node.js 26, with npm, as CI uses ([DEVELOPMENT.md](../docs/DEVELOPMENT.md) gives the minimum for the tests)
- Docker, for the containerised stack

Install only what your change needs:

- **Documentation:** nothing. `python3 scripts/validate_docs.py` uses only the standard library.
- **Frontend:** Node and `npm install` in `frontend/`. No Python or GPU.
- **Backend without a GPU:** a virtual environment with `requirements/dev.txt`, the test stack plus Ruff, mypy and pre-commit, followed by `requirements/worker-no-deps.txt` installed with `--no-deps` ([Fresh Checkout Setup](../docs/DEVELOPMENT.md#fresh-checkout-setup)). `requirements/local.txt` adds pyannote.audio for running the processing pipeline on a GPU host.

Before opening a pull request, run the checks for every area you touched, as listed in [Verification By Change Scope](../docs/DEVELOPMENT.md#verification-by-change-scope). `python scripts/check.py` runs every Python-side check, pytest included. CI runs the same checks and the aggregate `CI gate` must pass to merge ([Required Pull Request Checks](../docs/DEVELOPMENT.md#required-pull-request-checks)).

Two rules apply to every pull request:

- Recording actions go in `frontend/src/components/recordings/_hooks/useRecordingActions.ts`, never in a single surface, so the rail and the live view cannot diverge.
- An API change updates the backend response schemas (`backend/models/*_public.py` and related Pydantic models) and the frontend interfaces in `frontend/src/types/index.ts` in the same pull request.

## Documentation Ownership

A change to behaviour, setup, deployment or support updates the guide that owns the topic in the same pull request. [docs/README.md](../docs/README.md) indexes them. The usual ones:

- Product or UI behaviour: `docs/USAGE.md`, plus the `README.md` screenshots if a captured screen changes. Admin-only settings and operations: `docs/ADMIN.md`.
- Browser capture: `docs/CAPTURE.md`.
- Development workflow: `docs/DEVELOPMENT.md` and this file.
- Deployment, configuration or upgrades: `docs/DEPLOYMENT.md`.
- Auth, tokens or encryption: `docs/SECURITY.md`.
- Architecture or pipeline contracts: `docs/ARCHITECTURE.md`.
- Telemetry payloads: `docs/TELEMETRY.md`.
- Support or supported versions: `.github/SUPPORT.md` and `docs/SECURITY.md`.

Reviewers treat a behaviour or operational change with no documentation update as incomplete.

## Reporting Issues

Use the closest issue template.

- **Bugs:** a bug report with your Nojoin version, deployment mode, browser, capture mode and redacted logs.
- **Platform or browser-capture failures:** a platform compatibility report with your operating system, browser and versions. These arrive labelled `platform-issue`.
- **Flaky tests:** a bug report titled `[flaky]` with the test name, the CI job or local command, how often it fails, whether it reproduces locally or only in CI, and a link to a failing run if you have one.
- **Dependency problems:** a bug report naming the dependency, its pinned and installed versions, the platform (CPU or CUDA, operating system, Python or Node version), the exact install or runtime error, and whether it affects `requirements/local.txt`, `requirements/dev.txt` or the frontend lockfile.
- **Security vulnerabilities:** never a public issue. Use GitHub Private Vulnerability Reporting as described in the [security policy](../docs/SECURITY.md).

## Repository Governance

Nojoin has one maintainer, so code ownership, merge discipline and re-audits are self-applied. When a co-maintainer joins, the same rules become a second-reviewer gate without restructuring.

### Code Ownership

[CODEOWNERS](CODEOWNERS) groups paths by subsystem, and GitHub requests a review from the owner of every changed path. Every subsystem maps to `@Valtora` for now.

### Merge Requirements

Every pull request to `main` needs the `CI gate` status check green. It passes when every applicable job passed, and a job skipped because its area did not change counts as a pass ([Required Pull Request Checks](../docs/DEVELOPMENT.md#required-pull-request-checks)).

Sensitive scopes carry an extra obligation the maintainer, or later a designated code owner, must meet before merging:

- **Migrations (`backend/alembic/**`):** keep exactly one checked-in Alembic head, never delete or rename a committed revision, and confirm `Alembic validation` and `Migrations (Postgres)` are green.
- **Security (`docs/SECURITY.md`, auth, session, token and encryption code):** preserve the auth and token boundaries in [docs/SECURITY.md](../docs/SECURITY.md), and update it in the same pull request when behaviour changes.
- **Capture (`frontend/src/lib/capture/**`):** smoke-test start, pause and resume, stop and finalise, discard, unsupported-browser messaging and the selected microphone in a browser ([Verification By Change Scope](../docs/DEVELOPMENT.md#verification-by-change-scope)).
- **Deployment and release (`docker/**`, `docker-compose*.yml`, `.github/workflows/**`, `scripts/release.py`):** keep pinned action SHAs, pinned base-image digests and the gated, signed release ordering intact, and run the backend, frontend, docs and Alembic checks together.

### Branch Protection

`main` requires a pull request, the `CI gate` check on an up-to-date branch, linear history and resolved review threads, enforced for admins too. It requires no approvals while there is one maintainer, and force pushes and deletion are blocked. [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#branch-protection) has the exact settings and the command that restores them.

### Recording Significant Decisions

When a change alters a core contract, record the reasoning in the commit body and the pull request description. The contracts are:

- **Trust boundaries:** authentication, sessions and tokens, encryption, or what an actor may do.
- **Persistence:** the database schema's shape or guarantees, backup and restore semantics, or migration strategy.
- **Capture:** supported browsers, audio sources, segment upload or live-processing dispatch.
- **Processing:** the transcription and diarisation pipeline, including stage boundaries, engine selection or canonical-pipeline reconciliation.
- **Deployment:** the release model, the image build, sign and scan pipeline, runtime topology, or operator-facing configuration.

Record the context that forced the decision, the alternatives weighed and the trade-offs accepted. A routine fix, refactor, dependency bump or documentation edit needs none of this. Where a decision constrains future work, such as an invariant a reasonable-looking change would break, also state it in the relevant guide under [docs/](../docs/).

### Periodic Re-Audit

[repo-audit-reminder.yml](workflows/repo-audit-reminder.yml) runs at 09:00 UTC on the first day of each quarter and opens an `audit`-labelled issue carrying the re-audit checklist, unless one is already open. Work through it from a clean environment, record the measured numbers (test counts and durations, lint results, link-check output) in the issue, open a follow-up issue for each regression, then close it. The per-release triage pass is in [SUPPORT.md](SUPPORT.md#triage-cadence-and-labels).

### Other Governance Policies

- **Triage cadence and labels:** [SUPPORT.md](SUPPORT.md) and [labels.yml](labels.yml).
- **Dependency updates:** [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#dependency-update-policy).
- **Test reliability:** [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#test-reliability) and the flaky-test path under [Reporting Issues](#reporting-issues).

## Code of Conduct

This project is released with a [Code of Conduct](../docs/CODE_OF_CONDUCT.md). By participating you agree to abide by its terms.
