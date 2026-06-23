# Nojoin Repository Maintenance Plan

This document tracks the repository-level work required to make Nojoin easier to review, contribute to, release, and maintain at a high open-source standard.

It covers engineering quality controls, source hygiene, maintainability, contributor experience, documentation, and release governance. Product feature work and future desktop-transition planning are outside this tracker's scope.

## How To Use This Tracker

- `[ ]` means the item has not been completed.
- `[x]` means the item is complete and has been verified.
- Complete higher-priority sections before broad refactoring or cosmetic cleanup.
- Do not mark an item complete solely because code was changed. Run the stated checks and record the evidence in the implementing pull request or commit.
- Update counts in the re-audit section as work lands. Preserve the original baseline below for comparison.

## Audit Baseline

Baseline captured on 2026-06-22:

- Backend: `609 passed` in `48.67s`.
- Frontend production build: passed.
- Frontend tests: `94 passed`, `1 failed` across 25 test files.
- Frontend lint: `329 problems` (`152 errors`, `177 warnings`).
- Frontend source: 159 `no-explicit-any` suppression comments and 153 `catch (...: any)` clauses.
- Python source: 265 broad `except Exception` handlers, including 201 explicit `BLE001` suppressions.
- Python annotations: 339 functions without return annotations and 239 functions with at least one unannotated argument, excluding tests and migrations.
- Documentation: 14 machine-local `file:///` links and one missing local document link.
- Automation: release publishing exists, but there is no pull-request CI workflow.
- Largest audited functions include a 1,044-line backup restore function, a 913-line processing task, and a 777-line live-transcription task.

## Phase 0: Restore A Green, Gated Main Branch

These items are release blockers. Complete them before treating `main` as production-ready.

### Continuous Integration

- [x] **CI-001:** Add a pull-request and `main` branch CI workflow under `.github/workflows/`.
- [x] **CI-002:** Add a backend test job that installs explicit test dependencies and runs the full `pytest` suite.
- [x] **CI-003:** Add a frontend lint job that runs `npm run lint` and fails on errors.
- [x] **CI-004:** Add a frontend unit-test job that runs `npm run test`.
- [x] **CI-005:** Add a frontend production-build job that runs `npm run build`.
- [x] **CI-006:** Add a documentation job that detects missing local Markdown links and machine-local `file:///` links.
- [x] **CI-007:** Add an Alembic validation job that verifies the checked-in migration graph has the expected head and no missing revisions.
- [x] **CI-008:** Configure required status checks and branch protection for `main` after the workflow is stable.
- [x] **CI-009:** Document the required CI checks and expected local equivalents in `CONTRIBUTING.md` and `docs/DEVELOPMENT.md`.

### Current Frontend Failures

- [x] **FE-001:** Fix all current `@typescript-eslint/no-explicit-any` errors instead of suppressing them.
- [x] **FE-002:** Remove or correctly place the unused `eslint-disable-next-line` directives that currently produce duplicate lint findings.
- [x] **FE-003:** Replace `catch (...: any)` with `unknown` and shared, typed error-extraction helpers.
- [x] **FE-004:** Resolve current unused imports, unused state, and unused helper warnings.
- [x] **FE-005:** Review every `react-hooks/exhaustive-deps` warning and either correct the dependency model or document a narrow, justified suppression.
- [x] **FE-006:** Fix `Sidebar.test.tsx` by rendering the component with its required viewport-density context or a representative test wrapper.
- [x] **FE-007:** Confirm `npm run lint`, `npm run test`, and `npm run build` all pass from a clean checkout.

### Release Gating

- [x] **REL-001:** Make release publishing depend on the same green test, lint, build, migration, and documentation checks required for pull requests.
- [x] **REL-002:** Prevent `workflow_dispatch` runs from publishing `latest` unless the run is explicitly authorized from the intended release ref.
- [x] **REL-003:** Validate release tags as strict `vX.Y.Z` SemVer values rather than accepting every `v*` tag.
- [x] **REL-004:** Verify the release tag matches `docs/VERSION` before publishing images.
- [x] **REL-005:** Fail the release when any image build, test, or provenance step fails.

## Phase 1: Establish Enforceable Development Standards

### Python Tooling

- [x] **PY-001:** Align Ruff's `target-version` with the production Python 3.12 runtime.
- [x] **PY-002:** Add an explicit development or test requirements file that pins `pytest`, required pytest plugins, Ruff, and any other repository tooling.
- [x] **PY-003:** Document creation of `.venv`, dependency installation, activation, linting, formatting, migration checks, and testing from a fresh checkout.
- [x] **PY-004:** Enable Ruff rules for undefined names, unused imports, unused variables, and redefinitions.
- [x] **PY-005:** Add deterministic Python formatting and import ordering, then apply it in a dedicated mechanical change.
- [x] **PY-006:** Add a type-checking strategy for stable backend boundaries, beginning with API schemas, configuration, and shared processing contracts.
- [x] **PY-007:** Add pre-commit or an equivalent single local command that runs the same fast checks used by CI.
- [x] **PY-008:** Ensure the documented local setup can run Ruff and pytest without relying on undeclared transitive dependencies.

### Exception Handling And Logging

- [x] **PY-009:** Inventory the 265 broad `except Exception` handlers and classify each as required boundary handling, retry handling, or overly broad handling.
- [x] **PY-010:** Remove unjustified `BLE001` suppressions and catch narrower exception types where recovery behavior differs.
- [x] **PY-011:** Require broad boundary catches to log actionable context, preserve exception chaining where appropriate, and avoid exposing secrets.
- [x] **PY-012:** Standardize lazy logger formatting instead of eager f-string formatting in frequently executed paths.
- [x] **PY-013:** Add focused tests around exception paths changed during cleanup.

### Type And Interface Discipline

- [x] **PY-014:** Reconcile the documented mandatory type-hint policy with the current backend and define an incremental enforcement boundary.
- [x] **PY-015:** Add missing annotations to public functions and cross-module interfaces before internal helpers.
- [x] **FE-008:** Keep backend response schemas and frontend interfaces synchronized for every API change.
- [x] **FE-009:** Add reusable type guards for Axios errors, API error payloads, and unknown runtime data.

## Phase 2: Clean Up Source Comments And Mechanical Debt

### Comment Policy

- [x] **SRC-001:** Document that comments should explain invariants, constraints, risk, compatibility requirements, or non-obvious intent rather than narrating syntax.
- [x] **SRC-002:** Remove indecisive developer-thought comments such as "we can commit here", "usually", "assume consistency", and questions embedded in authorization rules.
- [x] **SRC-003:** Rewrite necessary uncertainty as an explicit invariant, fallback policy, issue reference, or testable assumption.
- [x] **SRC-004:** Preserve comments that document live/final pipeline alignment, security boundaries, migration compatibility, and browser-capture contracts.

### Targeted Comment Cleanup

- [x] **SRC-005:** Clean up commit narration and stale guidance in `backend/seed_demo.py`.
- [x] **SRC-006:** Replace ambiguous owner-role comments in `backend/api/v1/endpoints/users.py` with explicit authorization policy and tests.
- [x] **SRC-007:** Simplify narrated implementation comments in `backend/utils/transcript_utils.py` while retaining split/merge invariants.
- [x] **SRC-008:** Replace speculative restore comments in `backend/core/backup_manager.py` with verified transaction and identity rules.
- [x] **SRC-009:** Remove stale legacy narration from `frontend/src/lib/api.ts` once compatibility behavior is confirmed or retired.
- [x] **SRC-010:** Remove obsolete example usage, removed-function markers, and duplicate section labels from `backend/utils/config_manager.py`.
- [x] **SRC-011:** Remove commented-out imports, state declarations, filters, logger configuration, and destructive database calls unless they are retained as documented operational examples.
- [x] **SRC-012:** Trim redundant Dockerfile narration that merely restates the following instruction.

### Formatting And Dead Code

- [x] **SRC-013:** Remove trailing whitespace across tracked source and documentation in one reviewable mechanical change.
- [x] **SRC-014:** Normalize indentation, blank-line spacing, quote style, and line wrapping through configured formatters.
- [x] **SRC-015:** Remove unused imports, unused state, abandoned helpers, and dead compatibility branches verified as unreachable.
- [x] **SRC-016:** Add checks that prevent trailing whitespace and formatter drift from returning.

## Phase 3: Reduce Maintainability Hotspots

Large refactors must preserve behavior and begin with characterization tests.

### Backend Decomposition

- [x] **BE-001:** Add characterization tests around backup conflict modes, identity remapping, file extraction, transaction boundaries, and proxy regeneration before decomposing restore logic.
- [x] **BE-002:** Split `BackupManager._restore_backup_sync` into cohesive validation, preflight, extraction, table-restore, identity-remap, and finalization services.
- [ ] **BE-003:** Add stage-level characterization tests before decomposing `process_recording_task`.
- [ ] **BE-004:** Split the processing task into explicit orchestration stages with typed inputs, outputs, and failure semantics while keeping heavy inference in Celery workers.
- [ ] **BE-005:** Decompose `transcribe_segment_live_task` around sequence gating, audio buffering, ASR, persistence, diarisation dispatch, and best-effort failure handling.
- [ ] **BE-006:** Decompose the largest canonical-pipeline reconciliation functions without weakening stable-id alignment or manual-edit authority.
- [ ] **BE-007:** Split oversized API endpoint modules by resource or responsibility while preserving route contracts.
- [ ] **BE-008:** Define and enforce review thresholds for new modules and functions that grow beyond an agreed size or complexity.

### Frontend Decomposition

- [x] **FE-010:** Split `frontend/src/lib/api.ts` into typed resource clients while preserving a single public API layer.
- [ ] **FE-011:** Decompose the recording detail page into data orchestration, live-state, transcript, notes, documents, and action modules.
- [ ] **FE-012:** Decompose oversized dashboard, navigation, sidebar, transcript, speaker, and settings components into focused components and hooks.
- [ ] **FE-013:** Introduce shared test renderers that provide navigation, notification, viewport-density, and other required application contexts.
- [ ] **FE-014:** Add focused component tests before moving behavior out of large components.
- [ ] **FE-015:** Keep recording actions synchronized between `RecordingCard.tsx` and `Sidebar.tsx` until a shared action model removes the duplication safely.

## Phase 4: Improve Documentation And Contributor Experience

### Documentation Integrity

- [x] **DOC-001:** Publish this maintenance tracker and link it from the documentation index.
- [x] **DOC-002:** Replace every machine-local `file:///home/...` documentation link with a repository-relative link.
- [x] **DOC-003:** Add automated local-link checking for `README.md`, `CONTRIBUTING.md`, and `docs/*.md`.
- [ ] **DOC-004:** Consolidate the root and `docs/` legal disclaimers into one canonical policy and update all links.
- [ ] **DOC-005:** Correct spelling, grammar, punctuation, capitalization, and British/American terminology inconsistencies across public documentation. British English spelling and conventions takes priority and precedence.
- [ ] **DOC-006:** Replace stale historical implementation notes in operator documentation with current behavior plus versioned migration notes where still required.
- [ ] **DOC-007:** Add headings, captions, and context to the screenshots guide.
- [ ] **DOC-008:** Host critical logos and documentation images in repository-controlled assets where practical.
- [ ] **DOC-009:** Add a documentation ownership and review rule requiring behavior, setup, deployment, and support changes to update the relevant guide in the same pull request.

### Contributor Onboarding

- [x] **DOC-010:** Expand `CONTRIBUTING.md` with prerequisites, environment setup, test commands, lint commands, commit expectations, and pull-request verification requirements.
- [ ] **DOC-011:** Add a short contributor path that does not require building every GPU/ML dependency for frontend-only or documentation-only changes.
- [x] **DOC-012:** Document which checks are mandatory by change scope: backend, worker, frontend, capture, migration, documentation, and deployment.
- [x] **DOC-013:** Add guidance for database migrations, security-sensitive changes, browser-capture manual testing, and recording context-menu duplication.
- [ ] **DOC-014:** Document how contributors can report flaky tests, platform-specific failures, and dependency issues.

### Community Files

- [ ] **COMM-001:** Add an explicit private reporting contact or process to the Code of Conduct enforcement section.
- [ ] **COMM-002:** Update the platform issue template to match the current Windows, Linux, macOS, Android, and iOS support boundaries.
- [ ] **COMM-003:** Improve the bug template with Nojoin version, deployment mode, browser, capture mode, logs, reproduction data, and privacy-redaction guidance.
- [ ] **COMM-004:** Improve the pull-request template with backend tests, frontend lint/test/build, migration impact, documentation impact, security impact, and manual verification.
- [ ] **COMM-005:** Add issue-template configuration that directs security reports to private vulnerability reporting and support questions to the intended support channel.
- [ ] **COMM-006:** Define maintainer response, triage, and supported-version expectations that can realistically be met.

## Phase 5: Harden Release And Supply-Chain Governance

- [ ] **REL-006:** Pin third-party GitHub Actions to reviewed commit SHAs and use automated updates to keep them current.
- [ ] **REL-007:** Generate build provenance and an SBOM for each published image.
- [ ] **REL-008:** Add container and dependency vulnerability scanning with a documented severity policy.
- [ ] **REL-009:** Publish checksums or signatures appropriate to every release artifact.
- [ ] **REL-010:** Remove `npm install -g npm@latest` from Docker builds and use a deliberate, reproducible npm version.
- [ ] **REL-011:** Pin or otherwise govern mutable container base images and document the update policy.
- [ ] **REL-012:** Verify release images run as non-root and pass health checks before publication.
- [ ] **REL-013:** Automate release-note generation or validation from the exact previous-tag-to-current-tag range.
- [ ] **REL-014:** Publish release notes with upgrade, migration, rollback, known-issue, and browser-capture compatibility sections where applicable.
- [ ] **REL-015:** Add a documented emergency security-release and artifact-revocation procedure.

## Phase 6: Establish Ongoing Repository Governance

- [ ] **GOV-001:** Define code ownership or review responsibility for backend/API, worker/ML, frontend/capture, migrations, security, deployment, and documentation.
- [ ] **GOV-002:** Define merge requirements, including required reviewers and required checks for sensitive areas.
- [ ] **GOV-003:** Add a lightweight architectural decision record process for changes that alter trust boundaries, persistence, capture, processing, or deployment contracts.
- [ ] **GOV-004:** Establish an issue and pull-request triage cadence with labels for severity, scope, platform, and release impact.
- [ ] **GOV-005:** Configure dependency-update policy for Python, npm, Docker, and GitHub Actions.
- [ ] **GOV-006:** Track test duration and flakiness so the suite remains reliable as coverage grows.
- [ ] **GOV-007:** Add visible README badges only for checks that are mandatory, meaningful, and consistently green.
- [ ] **GOV-008:** Schedule a periodic repository-quality re-audit and update this tracker with measured progress.

## Re-Audit And Completion Gate

The repository-maintenance initiative is complete only when all of the following are true:

- [ ] All required pull-request checks pass on `main`.
- [ ] Backend tests pass from a documented clean environment.
- [ ] Frontend lint, unit tests, and production build pass without broad suppressions.
- [ ] Python linting and formatting pass with production-compatible configuration.
- [ ] Documentation has no missing local links or machine-local paths.
- [ ] Release publication is gated, reproducible, and traceable to an exact tag and commit.
- [ ] The largest maintainability hotspots have been decomposed behind regression coverage or have a documented, reviewed justification for remaining intact.
- [ ] Contributor and community documentation accurately describes the supported workflow and reporting channels.
- [ ] A final audit records zero known release-blocking repository-quality findings.
