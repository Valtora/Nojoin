# Pull Request

## Description

Summarise the change, the motivation, and any context. List any new dependencies.

Fixes # (issue)

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behaviour)
- [ ] Documentation update

## Checks run

Tick the checks you ran for the areas you touched (see `CONTRIBUTING.md`).

- [ ] Backend tests: `source .venv/bin/activate && pytest`
- [ ] Python quality: `python scripts/check.py` (Ruff lint, format check, mypy, doc and Alembic validators)
- [ ] Frontend lint: `cd frontend && npm run lint`
- [ ] Frontend unit tests: `cd frontend && npm run test`
- [ ] Frontend build: `cd frontend && npm run build`
- [ ] Docs validation: `python3 scripts/validate_docs.py`
- [ ] Alembic validation: `python3 scripts/validate_alembic.py`

## Migration impact

- [ ] No database migration in this PR.
- [ ] Adds an Alembic migration. Single checked-in head preserved; no committed revisions deleted or renamed. Upgrade and downgrade behaviour described above.

## Documentation impact

- [ ] No documentation change required.
- [ ] Updated the relevant guide(s) in the same PR (behaviour, setup, deployment, or support changes must update their guide — see CONTRIBUTING.md "Documentation Ownership").

## Security impact

- [ ] No security-sensitive change.
- [ ] Touches auth, tokens, encryption, capture ownership, or exposure. `docs/SECURITY.md` boundaries preserved and updated where behaviour changed.

## Manual verification

Describe what you tested manually. Capture-related changes require browser smoke testing for start, pause/resume, stop/finalize, discard, unsupported-browser messaging, and selected-microphone behaviour. Recording context-menu changes must keep `RecordingCard.tsx` and `Sidebar.tsx` in sync.

## Screenshots (if relevant)

Drag and drop images directly into the markdown box (redact sensitive content).
