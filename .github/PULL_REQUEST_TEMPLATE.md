# Pull Request

## Description

What changed and why. List any new dependencies.

Fixes # (issue)

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behaviour)
- [ ] Documentation update

## Checks run

Tick the checks you ran for the areas you touched (`docs/DEVELOPMENT.md`, "Verification By Change Scope").

- [ ] Python: `python scripts/check.py` (Ruff, format, whitespace and other gates, mypy, doc and Alembic validators, pytest)
- [ ] Frontend: `cd frontend && npm run lint && npm run check:contrast && npm run test && npm run build`
- [ ] Site: `cd site && npm ci && npm run build`
- [ ] Docs only: `python3 scripts/validate_docs.py`

## Migration impact

- [ ] No database migration.
- [ ] Adds an Alembic migration: one checked-in head, no committed revision deleted or renamed, upgrade and downgrade behaviour described above, and `python3 scripts/check_migrations.py` run against a throwaway Postgres.

## Documentation impact

- [ ] No documentation change required.
- [ ] Updated the guide that owns each changed behaviour, setup, deployment or support topic (`.github/CONTRIBUTING.md`, "Documentation Ownership").

## Security impact

- [ ] No security-sensitive change.
- [ ] Touches auth, tokens, encryption, capture ownership or exposure; `docs/SECURITY.md` boundaries preserved and updated where behaviour changed.

## Manual verification

What you tested by hand. Capture changes need a browser smoke test of start, pause and resume, stop and finalise, discard, unsupported-browser messaging and the selected microphone.

## Screenshots (if relevant)

Drag images into the box, with sensitive content redacted.
