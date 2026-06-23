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

Minimum pull request verification is:

- Backend/API/worker changes: `source .venv/bin/activate && pytest`
- Python lint, format, and type checks: `python scripts/check.py` (runs Ruff,
  the formatter check, mypy, the doc and Alembic validators, and pytest; pass
  `--fix` to auto-fix lint and formatting first)
- Frontend changes: `cd frontend && npm run lint && npm run test && npm run build`
- Documentation changes: `python3 scripts/validate_docs.py`
- Alembic migration changes: `python3 scripts/validate_alembic.py`

The pull request workflow requires these checks to pass on `main`:

- `Backend tests`
- `Python quality` (Ruff lint, Ruff format check, and mypy on enforced boundaries)
- `Frontend lint`
- `Frontend unit tests`
- `Frontend build`
- `Docs validation`
- `Alembic validation`

Run the checks for every area you touched before opening a pull request. Capture-related changes also require manual browser smoke testing for start, pause/resume, stop/finalize, discard, unsupported-browser messaging, and selected-microphone behavior. Migration changes must keep a single checked-in Alembic head and must not delete or rename committed revision files.

Additional scope rules:

- Recording context-menu changes must keep `frontend/src/components/RecordingCard.tsx` and `frontend/src/components/Sidebar.tsx` in sync.
- Security-sensitive changes must preserve the documented auth and token boundaries in `docs/SECURITY.md` and update that guide in the same pull request when behavior changes.
- API changes must keep backend response schemas (`backend/models/*_public.py` and related Pydantic models) and the corresponding frontend interfaces in `frontend/src/types/index.ts` synchronized in the same pull request.

## Code of Conduct

Please note that this project is released with a [Code of Conduct](docs/CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.

Thank you for helping make Nojoin better!
