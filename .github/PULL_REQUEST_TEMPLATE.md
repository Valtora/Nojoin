# Summary

Describe the user-visible or maintainer-visible change in 2-4 sentences. State what changed and why.

## Linked Issues

- Fixes #
- Related #

## Change Type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Documentation
- [ ] CI / build / tooling
- [ ] Breaking change

## Affected Areas

- [ ] Backend / API
- [ ] Worker / processing pipeline
- [ ] Frontend / UI
- [ ] Browser capture
- [ ] Auth / security
- [ ] Calendar / integrations
- [ ] Migrations / persistence
- [ ] Deployment / operations
- [ ] Documentation only

## Local Verification

Mark every command you actually ran. Leave unchecked items unchecked and explain them below.

- [ ] `source .venv/bin/activate && python scripts/check_fast.py`
- [ ] `source .venv/bin/activate && pytest`
- [ ] `cd frontend && npm run lint`
- [ ] `cd frontend && npm run test`
- [ ] `cd frontend && npm run build`
- [ ] `python3 scripts/validate_docs.py`
- [ ] `python3 scripts/validate_alembic.py`
- [ ] `git diff --check`

## Manual Validation

List the manual checks you performed. If none were needed, say `Not needed`.

If browser capture or recording flows changed, explicitly cover:

- share picker behavior
- selected microphone behavior
- waveform / live state
- pause / resume
- stop / finalize
- discard
- unsupported-browser messaging

## Impact And Risk

- Security impact:
  State whether auth, sessions, tokens, secrets, or trust boundaries changed. If yes, say whether `docs/SECURITY.md` was updated.
- Migration impact:
  State whether `backend/alembic/versions/` changed and whether `python3 scripts/validate_alembic.py` passed.
- Deployment impact:
  State whether operators need `.env`, Docker, reverse proxy, model, or rollout changes. If yes, say whether `docs/DEPLOYMENT.md` was updated.
- Documentation impact:
  List the docs updated for this behavior or workflow change. If none were needed, say why.

## Scope-Specific Checks

- [ ] Security-sensitive behavior changes updated `docs/SECURITY.md`
- [ ] Deployment or runtime workflow changes updated `docs/DEPLOYMENT.md`
- [ ] Contributor workflow or validation changes updated `CONTRIBUTING.md` and `docs/DEVELOPMENT.md`

## Pending Before Merge

List anything still pending before merge. If nothing is pending, say `None`.

## Screenshots Or Recordings

Add screenshots, screen recordings, or note `Not applicable`.
