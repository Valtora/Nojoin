# Contributing to Nojoin

Thank you for your interest in Nojoin!

Nojoin is free and open-source software licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

## Licensing of Contributions

No additional contributor agreement is required. By submitting a Pull Request, you agree that your contribution will be licensed under the **GNU Affero General Public License v3.0 (AGPLv3)** for inclusion in Nojoin.

## How to Contribute

We **actively welcome** code contributions, bug fixes, and feature enhancements from the community.

### Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally.
3. Create a new **branch** for your feature or fix (`git checkout -b feature/amazing-feature`).
4. Commit your changes following [Conventional Commits](https://www.conventionalcommits.org/).
5. **Push** to your branch.
6. Open a **Pull Request** against the `main` branch.

### Ways to Help

- **Code Contributions:** Submit PRs for bug fixes or new features.
- **Bug Reports:** If you encounter any issues, please [open an issue](https://github.com/Valtora/Nojoin/issues) with detailed reproduction steps.
- **Platform Testing:** We specifically need help verifying browser capture on supported Chromium-family browsers across Windows and Linux, plus microphone-only capture on Chrome on Android and iOS. We also need verification of unsupported-browser messaging on Firefox, Safari, non-Chrome mobile browsers, and Chromium browsers on macOS.
- **Documentation:** Improvements to the docs are always welcome.

## Development Setup

Please refer to [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for local development setup and core source-build commands.

Local contributor prerequisites:

- Python 3.12
- Node.js 20 or newer
- npm
- Docker for the containerised stack and deployment-path verification

Typical host setup from a fresh checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# Full GPU host (live processing stack): use local.txt.
# Tests + linting + type-checking only (CPU, no CUDA): use dev.txt instead.
python -m pip install -r requirements/local.txt

cd frontend
npm install
```

Install the git pre-commit hook so linting and formatting run automatically:

```bash
pre-commit install
```

### Lightweight Contributor Paths

You do not need the full GPU/ML stack for every change. The heavy inference dependencies (CUDA, Whisper, Pyannote) are only required to run the live processing pipeline locally. Match the setup to the surface you are changing.

- **Documentation-only changes:** no Python virtual environment or Node toolchain is required. Edit the Markdown, then validate links with `python3 scripts/validate_docs.py`. That script has no third-party dependencies.
- **Frontend-only changes:** install Node.js 20 or newer and the frontend dependencies only. You can develop and fully verify frontend work without CUDA or the ML requirements.

  ```bash
  cd frontend
  npm install
  npm run lint && npm run test && npm run build
  ```

- **Backend lint, test, and type-check without a GPU:** create the virtual environment and install `requirements/dev.txt` (CPU-only) instead of `requirements/local.txt`, then run `python scripts/check.py`. The full `requirements/local.txt` is only needed to exercise live transcription and diarisation on a GPU host.

Minimum pull request verification is:

- Backend/API/worker changes: `source .venv/bin/activate && pytest`
- Python lint, format, and type checks: `python scripts/check.py` (runs Ruff,
  the formatter check, mypy, the doc and Alembic validators, and pytest; pass
  `--fix` to auto-fix lint and formatting first)
- Frontend changes: `cd frontend && npm run lint && npm run test && npm run build`
- Documentation changes: `python3 scripts/validate_docs.py`
- Alembic migration changes: `python3 scripts/validate_alembic.py`

The pull request workflow runs these checks, and the aggregate `CI gate` must be green to merge. The expensive jobs run only when their area changed; the cheap validators always run:

- `Backend tests` and `Python quality` (Ruff lint, Ruff format check, and mypy on enforced boundaries) — on backend or deployment changes.
- `Frontend lint`, `Frontend unit tests`, and `Frontend build` — on frontend or deployment changes.
- `Docs validation` and `Alembic validation` — always.

See the [Merge Requirements](#merge-requirements) section for how `CI gate` aggregates these and the exact path rules.

Run the checks for every area you touched before opening a pull request. Capture-related changes also require manual browser smoke testing for start, pause/resume, stop/finalize, discard, unsupported-browser messaging, and selected-microphone behaviour. Migration changes must keep a single checked-in Alembic head and must not delete or rename committed revision files.

Additional scope rules:

- Recording context-menu changes must keep `frontend/src/components/RecordingCard.tsx` and `frontend/src/components/Sidebar.tsx` in sync.
- Security-sensitive changes must preserve the documented auth and token boundaries in `docs/SECURITY.md` and update that guide in the same pull request when behaviour changes.
- API changes must keep backend response schemas (`backend/models/*_public.py` and related Pydantic models) and the corresponding frontend interfaces in `frontend/src/types/index.ts` synchronised in the same pull request.

## Documentation Ownership

Documentation is owned alongside the code it describes. Any change to behaviour, setup, deployment, or support must update the relevant guide in the same pull request:

- Product or UI behaviour: `docs/USAGE.md`, and `docs/SCREENSHOTS.md` if a captured screen changes.
- Browser capture behaviour: `docs/CAPTURE.md`.
- Local setup or development workflow: `docs/DEVELOPMENT.md` and this file.
- Deployment, configuration, or upgrade behaviour: `docs/DEPLOYMENT.md`.
- Auth, token, or encryption behaviour: `docs/SECURITY.md`.
- Architecture or pipeline contracts: `docs/ARCHITECTURE.md`.
- Support, reporting, or supported-version expectations: `.github/SUPPORT.md` and `docs/SECURITY.md`.

Reviewers should treat a behaviour or operational change with no corresponding documentation update as incomplete.

## Reporting Issues

Use the GitHub issue templates and choose the closest match. Route sensitive reports privately as noted below.

- **Bugs:** open a bug report and include your Nojoin version, deployment mode, browser, capture mode, and redacted logs.
- **Platform or browser-capture failures:** open a platform compatibility report with your operating system, browser, and versions. These are triaged under the `platform-issue` label.
- **Flaky tests:** open a bug report titled `[flaky]`. Include the test name, the CI job or local command, how often it fails, and a link to a failing run if available. Note whether it reproduces locally or only in CI.
- **Dependency issues:** open a bug report describing the dependency, the pinned and installed versions, the platform (CPU or CUDA, operating system, Python or Node version), and the exact install or runtime error. State whether it affects `requirements/local.txt`, `requirements/dev.txt`, or the frontend lockfile.
- **Security vulnerabilities:** do not open a public issue. Use GitHub Private Vulnerability Reporting as described in the [security policy](docs/SECURITY.md).

## Repository Governance

Nojoin is currently maintained by a single maintainer. The governance below is designed around that reality: code ownership, merge discipline, and the re-audit cadence are self-applied rather than relying on a second human reviewer. The structure is forward-compatible — when a co-maintainer joins, the same rules gain a genuine second-reviewer gate without restructuring.

### Code Ownership

Review responsibility is recorded in [.github/CODEOWNERS](.github/CODEOWNERS). GitHub auto-requests a review from the listed owner for every pull request that touches a matched path. The current map is:

| Area | Paths | Owner |
| --- | --- | --- |
| Backend / API | `backend/api/**` | `@Valtora` |
| Worker / ML and processing | `backend/worker/**`, `backend/processing/**` | `@Valtora` |
| Migrations | `backend/alembic/**` | `@Valtora` |
| Frontend / capture | `frontend/src/**`, `frontend/src/lib/capture/**` | `@Valtora` |
| Security | `docs/SECURITY.md`, `backend/core/security.py`, `backend/core/encryption.py`, `backend/api/deps.py`, `backend/api/v1/endpoints/login.py` | `@Valtora` |
| Deployment / CI | `docker/**`, `docker-compose*.yml`, `.github/workflows/**` | `@Valtora` |
| Documentation | `docs/**`, `*.md` | `@Valtora` |

### Merge Requirements

Every pull request to `main` must have the required `CI gate` status check green before merge. `CI gate` is an aggregate that passes only when every applicable CI job passed. To save CI minutes, the expensive jobs run only when their area changed and are skipped (counted as a pass) otherwise:

- `Backend tests` and `Python quality` (Ruff lint, Ruff format check, mypy) — run when `backend/**`, `requirements/**`, `pyproject.toml`, or `scripts/**` changed.
- `Frontend lint`, `Frontend unit tests`, and `Frontend build` — run when `frontend/**` changed.
- A **deployment** change (`docker/**`, `docker-compose*.yml`, `nginx/**`, or `.github/workflows/**`) runs **both** the backend and frontend suites, since it can affect the built images or pipeline even without code changes. This is consistent with the deployment/release sensitive-scope rule below.
- `Docs validation` and `Alembic validation` — always run (cheap).

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#required-pull-request-checks) for the exact path rules and how `CI gate` works.

Sensitive scopes carry an additional review obligation that the maintainer (or, in future, a designated CODEOWNER) must satisfy before merging:

- **Migrations (`backend/alembic/**`):** keep exactly one checked-in Alembic head and never delete or rename a committed revision. Confirm `Alembic validation` is green.
- **Security (`docs/SECURITY.md`, auth/session/token/encryption code):** preserve the documented auth and token boundaries in [docs/SECURITY.md](docs/SECURITY.md) and update that guide in the same pull request when behaviour changes.
- **Capture (`frontend/src/lib/capture/**`):** complete the manual browser smoke coverage listed under [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and keep `RecordingCard.tsx` and `Sidebar.tsx` context menus in sync.
- **Deployment / release (`docker/**`, `docker-compose*.yml`, `.github/workflows/**`):** keep the pinned action SHAs, pinned base-image digests, and the gated/signed release ordering intact, and run the full backend, frontend, docs, and Alembic validation set together.

### Branch Protection (maintainer-action-pending)

Branch protection and required-reviewer enforcement cannot be applied from the repository tree; they are GitHub repository settings. The settings to apply on `main` are designed so the sole maintainer can always merge their own green pull request and are never an irreversible lockout:

- Require a pull request before merging, with **0** required approvals while the project is single-maintainer. GitHub forbids approving your own pull request, so a non-zero count would make every merge impossible for a sole author; raise it to **1** and enable **Require review from Code Owners** only when a second maintainer joins.
- Require the single `CI gate` status check to pass before merging, with branches required to be up to date. It is enforced for admins too, so nothing merges red. (Only `CI gate` is required, not the individual jobs, because those are skipped by the path filter when irrelevant and a skipped job would otherwise leave a required check pending forever.)
- Require linear history and disallow force pushes and deletions.

The exact `gh` command, the reasoning behind each value, and the guarantee that this cannot lock out a sole maintainer are recorded in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#branch-protection-maintainer-action). The release jobs are deliberately **not** part of this list: they only run on tag pushes, never on pull requests, so requiring them on `main` would block every merge. The release pipeline self-gates through its own job dependency graph instead, as documented in [ADR-0001](docs/adr/0001-gated-signed-release-model.md).

### Architecture Decision Records

Changes that alter trust boundaries, persistence, capture, processing, or deployment contracts require an Architecture Decision Record. See [docs/adr/README.md](docs/adr/README.md) for when an ADR is required and how to add one.

### Periodic Re-Audit

The repository-quality bar is maintained, not set once. Three passes keep it current:

- **Quarterly (automated reminder):** `.github/workflows/repo-audit-reminder.yml` runs on the first day of each quarter and opens an `audit`-labelled issue if one is not already open. That issue carries the re-audit checklist; the maintainer works through it, records the measured evidence in the issue, and opens follow-up issues for any regression.
- **Per release:** before tagging, confirm the release-blocking quality checks still hold and that open `severity:critical`/`severity:high` and `release:*` issues are reflected in the release notes.
- **On significant change:** when a change alters a core contract (trust boundary, persistence, capture, processing, or deployment), the accompanying ADR is the record — see [docs/adr/README.md](docs/adr/README.md).

The re-audit procedure is to run the required checks from a clean environment, capture the actual numbers (test counts and durations, lint results, link-check output) in the audit issue, and resolve regressions with tracked follow-up issues rather than silently closing the audit.

### Other Governance Policies

To avoid duplication, the remaining policies live in their canonical homes:

- **Triage cadence and labels:** [.github/SUPPORT.md](.github/SUPPORT.md) and [.github/labels.yml](.github/labels.yml).
- **Dependency-update policy:** [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#dependency-update-policy).
- **Test reliability (duration and flakiness):** [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#test-reliability) and the flaky-test reporting path under [Reporting Issues](#reporting-issues).

## Code of Conduct

Please note that this project is released with a [Code of Conduct](docs/CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.

Thank you for helping make Nojoin better!
