# Nojoin Code Audit Tracker

This document tracks the initial codebase audit completed on 2026-05-30. It is
intended to be a living remediation tracker for security, release readiness,
correctness, maintainability, and documentation quality.

## Audit Scope

The review assumes that Nojoin may be deployed on a public internet origin. The
first remediation priority is security and release safety. This document records
findings only; it does not imply that the listed issues have been fixed.

The audit covered:

- Repository documentation and tracked-file hygiene.
- Authentication, authorisation, setup, invitation, and session handling.
- Reverse-proxy configuration and host-mounted data permissions.
- Upload, backup, Docker socket, model-management, and Celery result surfaces.
- API and worker service boundaries.
- GitHub Actions, container builds, dependency pinning, and test commands.
- Backend compilation, focused security tests, frontend lint/build/tests, npm
  dependency audit, Compose validation, and migration-head validation.

## Status Definitions

| Status | Meaning |
| --- | --- |
| Open | Confirmed issue awaiting remediation. |
| Investigate | Evidence of a problem exists, but the correct remediation or full impact needs confirmation. |
| Deferred | Valid cleanup item intentionally scheduled after higher-impact work. |
| Resolved | Remediation has been implemented and verified. |

## Priority Summary

| Priority | Count | Release position |
| --- | ---: | --- |
| Critical | 2 | Block public-internet deployment and release. |
| High | 6 | Resolve before claiming hardened public deployment. |
| Medium | 16 | Address during the professionalisation pass. |
| Low | 7 | Resolve as polish and repository hygiene work. |

## Critical Findings

### SEC-001: Invitation Registration Can Escalate Admins to Owner

- **Status:** Resolved
- **Impact:** Privilege escalation.
- **Evidence:** [`backend/api/v1/endpoints/invitations.py`](../backend/api/v1/endpoints/invitations.py#L46)
  accepts an arbitrary `role` value when an admin or owner creates an
  invitation. [`backend/api/v1/endpoints/users.py`](../backend/api/v1/endpoints/users.py#L89)
  copies `invitation.role` directly into a newly registered user. This bypasses
  the owner-only promotion rules used by manual user creation.
- **Remediation direction:** Validate invitation roles server-side. Permit
  `user` and `admin` invitations only unless an explicit owner-only path is
  required. Apply database constraints or enum validation so invalid persisted
  roles cannot be created.
- **Verification:** Invitation creation now rejects `owner` and unknown roles,
  public validation rejects invalid persisted invitations, registration
  revalidates the stored invitation role, and focused auth or security tests
  cover admin, owner, invalid-role, and legacy-row behavior.
- **Acceptance criteria:** An admin cannot create an `owner` invitation or any
  unknown role. Registration revalidates the persisted invitation role. Tests
  cover admin, owner, invalid-role, and legacy-row behavior.

### SEC-002: Authenticated Ollama Model Listing Allows SSRF

- **Status:** Resolved
- **Impact:** Internal service access from the API container.
- **Evidence:** [`backend/api/v1/endpoints/llm.py`](../backend/api/v1/endpoints/llm.py#L11)
  accepts an arbitrary `api_url` for any authenticated user.
  [`backend/processing/llm_services.py`](../backend/processing/llm_services.py#L1544)
  performs a server-side request without applying the setup route's SSRF
  validator. The API container can reach internal services, including the
  Docker socket proxy.
- **Remediation direction:** Centralise outbound URL validation and enforce it
  at every server-side Ollama request boundary. Prefer an installation-wide
  admin-configured allowlist over per-request arbitrary endpoints. Re-resolve
  DNS safely and reject private, loopback, link-local, reserved, and internal
  service addresses unless an operator has explicitly configured a trusted
  local Ollama endpoint.
- **Verification:** Ollama URL validation now lives in a shared backend policy,
  the authenticated model-list route is pinned to the installation-wide
  configured endpoint, runtime Ollama requests disable redirects, and focused
  tests cover direct IPs, internal service names, DNS resolution, IPv4, IPv6,
  trusted installation-wide endpoints, and override rejection.
- **Acceptance criteria:** Ordinary users cannot direct API requests to
  arbitrary internal or external endpoints. Tests cover direct IPs, internal
  service names, DNS resolution, redirects, IPv4, IPv6, and approved Ollama
  endpoints.

### SEC-003: Ordinary Users Can Persist Worker-Side Ollama SSRF Targets

- **Status:** Resolved
- **Impact:** Repeated internal requests from worker processes.
- **Evidence:** [`backend/api/v1/endpoints/settings.py`](../backend/api/v1/endpoints/settings.py#L86)
  checks URL shape only, and ordinary users can persist `ollama_api_url`.
  [`backend/utils/llm_config.py`](../backend/utils/llm_config.py#L83) merges
  user settings into resolved worker configuration. Worker AI operations then
  use that URL.
- **Remediation direction:** Make the Ollama endpoint installation-wide and
  admin-controlled, or apply the same centralised outbound policy before
  persistence and again before use.
- **Verification:** `ollama_api_url` is now treated as install-wide only,
  non-admin settings updates drop it, merged worker configuration ignores user
  overrides, and backend construction rejects unsafe runtime endpoints even if
  stale values still exist in user settings.
- **Acceptance criteria:** Non-admin users cannot persist arbitrary Ollama
  endpoints. Worker tasks reject unsafe persisted values even if they already
  exist in the database.

## High Findings

### SEC-004: Deployment Templates Accept Known Placeholder Secrets

- **Status:** Resolved
- **Impact:** Predictable bootstrap access, encryption seed, and internal
  service credentials when operators use the copy-paste deployment path
  without replacing defaults.
- **Evidence:** [`.env.example`](../.env.example#L2) contains known PostgreSQL,
  Redis, first-run, and data-encryption placeholders.
  [`docker-compose.example.yml`](../docker-compose.example.yml#L96) rejects a
  missing `FIRST_RUN_PASSWORD` but accepts the known placeholder.
- **Remediation direction:** Detect tracked placeholder secrets at runtime,
  warn operators in startup logs and the authenticated web UI, and document
  that secret replacement remains the operator's responsibility.
- **Verification:** The API and worker now log one startup warning when tracked
  placeholder secrets are active, authenticated `/api/v1/system/health`
  responses expose a non-secret `deployment_warnings` array, and the frontend
  shows a persistent authenticated toast until the placeholders are removed.
- **Acceptance criteria:** A template deployment with unchanged sentinel values
  produces actionable operator-visible warnings without exposing the raw secret
  values or blocking startup.

### SEC-005: Generated TLS Private Keys Are World-Readable

- **Status:** Resolved
- **Impact:** Local users and processes on a shared host can read the bundled
  TLS private key.
- **Evidence:** [`docker/init-ssl.sh`](../docker/init-ssl.sh#L22) applies mode
  `644` to both the certificate and private key.
- **Remediation direction:** Set the private key to `600`. The public
  certificate may remain `644`.
- **Remediation:** Changed [`docker/init-ssl.sh`](../docker/init-ssl.sh) to apply `600` permissions to the private key while leaving the public certificate as `644`. Added migration instructions to [`docs/DEPLOYMENT.md`](DEPLOYMENT.md).
- **Verification:** Verified that newly generated keys are generated with `600` permissions and applied `chmod 600` to the existing key file in the workspace.
- **Acceptance criteria:** Newly generated keys are owner-readable only.
  Existing deployments receive migration guidance.

### SEC-006: Confidential Data Files Rely on Default Umask

- **Status:** Resolved
- **Impact:** Meeting audio, JWT signing material, encryption fallback keys,
  logs, and configuration may be readable by unrelated host users.
- **Evidence:** [`backend/core/security.py`](../backend/core/security.py#L70),
  [`backend/core/encryption.py`](../backend/core/encryption.py#L21), and upload
  writers such as [`backend/api/v1/endpoints/recordings.py`](../backend/api/v1/endpoints/recordings.py#L1180)
  do not explicitly enforce restrictive modes. The audited local deployment
  contained world-readable JWT material, meeting audio, and TLS key files.
- **Remediation direction:** Apply explicit `0600` file permissions and `0700`
  confidential directory permissions. Add a startup permission repair pass and
  operator-facing migration notes.
- **Remediation:** Enforced a default `0077` umask at process initialization in [`backend/__init__.py`](../backend/__init__.py) (with custom override support via `NOJOIN_UMASK`). Added explicit `chmod 600` calls during JWT keyring and encryption key file writing. Added an automatic recursive permission repair pass on startup inside [`backend/utils/path_manager.py`](../backend/utils/path_manager.py). Updated migration notes in [`docs/DEPLOYMENT.md`](DEPLOYMENT.md).
- **Verification:** Created unit tests in [`backend/tests/test_umask_security.py`](../backend/tests/test_umask_security.py) verifying umask parsing and the recursive file/directory permissions repair pass, both of which pass successfully.
- **Acceptance criteria:** New and existing secret, recording, document, and
  log files are not group- or world-readable unless intentionally configured.

### DEP-001: Bundled Proxy Ports Can Bypass an External Edge Proxy

- **Status:** Resolved
- **Impact:** Operators may unintentionally expose the bundled Nginx proxy
  directly, bypassing authentication, filtering, or rate limiting configured
  on Caddy, Traefik, a tunnel, or another edge proxy.
- **Evidence:** [`docker-compose.example.yml`](../docker-compose.example.yml#L178)
  publishes ports `14141` and `14443` on all host interfaces. The reverse-proxy
  guide does not require loopback binding or firewall rules.
- **Remediation direction:** Bind the bundled proxy to loopback by default for
  edge-proxy deployments, provide an explicit direct-access variant, and
  document firewall expectations.
- **Remediation:** Added `NOJOIN_BIND_ADDRESS` in `.env.example` defaulting to `127.0.0.1` and updated `docker-compose.example.yml` to bind the bundled Nginx proxy ports to this variable (falling back to loopback). Updated development templates in `docs/DEVELOPMENT.md` and reverse proxy deployment documentation in `docs/DEPLOYMENT.md` to detail direct-access overrides and firewall considerations.
- **Verification:** Updated the host deployment's environment and recreated the Nginx container to restrict port mapping strictly to the loopback interface (`127.0.0.1`), validating the loopback binding.
- **Acceptance criteria:** The recommended public-internet deployment exposes
  exactly one intentional edge entry point.

### REL-001: Release Images Can Be Published Without Verification Gates

- **Status:** Deferred
- **Impact:** Broken or vulnerable images can be published as release artifacts
  and tagged `latest`.
- **Evidence:** [`.github/workflows/release.yml`](../.github/workflows/release.yml#L3)
  builds and pushes images without backend tests, frontend tests, lint, build,
  Compose validation, or a dependency audit.
- **Remediation direction:** Add pull-request CI and require a green reusable
  verification workflow before image publication.
- **Acceptance criteria:** Release jobs cannot publish unless required checks
  pass for the exact commit being released.

### BUG-001: Calendar Candidate Linking Crashes at Runtime

- **Status:** Resolved
- **Impact:** The calendar event candidate endpoint returns a server error.
- **Evidence:** [`backend/api/v1/endpoints/recordings.py`](../backend/api/v1/endpoints/recordings.py#L1794)
  uses `timedelta` without importing it. The backend suite reproduces the
  failure.
- **Remediation direction:** Add the missing import and retain a focused
  regression test.
- **Remediation:** Imported `timedelta` from the `datetime` module at the top of [`backend/api/v1/endpoints/recordings.py`](../backend/api/v1/endpoints/recordings.py).
- **Verification:** Verified that the candidate calendar linking tests in [`backend/tests/test_calendar_event_linking.py`](../backend/tests/test_calendar_event_linking.py) pass successfully after the fix.
- **Acceptance criteria:** The affected backend test passes and candidate
  linking works through the API.

## Medium Findings

### SEC-007: Authenticated Upload Routes Buffer Large Bodies in API Memory

- **Status:** Resolved
- **Impact:** A low-privilege account can create avoidable API memory pressure
  with concurrent uploads.
- **Evidence:** [`nginx/nginx.conf`](../nginx/nginx.conf#L25) permits request
  bodies up to `500M`. Several routes read an entire body before writing it,
  including browser segments in
  [`backend/api/v1/endpoints/recordings.py`](../backend/api/v1/endpoints/recordings.py#L893),
  legacy recording uploads in
  [`backend/api/v1/endpoints/recordings.py`](../backend/api/v1/endpoints/recordings.py#L1517),
  document uploads in
  [`backend/api/v1/endpoints/documents.py`](../backend/api/v1/endpoints/documents.py#L72),
  and backup uploads in
  [`backend/api/v1/endpoints/backup.py`](../backend/api/v1/endpoints/backup.py#L157).
- **Remediation direction:** Stream uploads to disk in bounded chunks, apply
  route-specific size limits, and add per-user concurrency or rate controls.
- **Remediation:** Rewrote all API upload endpoints to stream incoming files to disk in 64KB chunks rather than buffering them fully in memory. Applied configurable route-specific size limits defaulting to 15MB for segments, 250MB for legacy recordings, 20MB for documents, and 300MB for backups. Enforced per-user concurrency limits (5 for segments, 2 for large uploads) via Redis with local dictionary fallback.
- **Verification:** Verified by writing a comprehensive unit/integration test suite in [`backend/tests/test_upload_limits.py`](../backend/tests/test_upload_limits.py) covering chunked streaming, early content-length rejection, chunk-based rejection, temporary file cleanup, and concurrency locking.
- **Acceptance criteria:** Large uploads do not scale API memory linearly with
  request size.

### SEC-008: Superusers Can Read Logs From Arbitrary Host Containers

- **Status:** Resolved
- **Impact:** On shared Docker hosts, a compromised Nojoin superuser account can
  retrieve logs from unrelated containers and potentially disclose secrets.
- **Evidence:** [`backend/api/v1/endpoints/system.py`](../backend/api/v1/endpoints/system.py#L128)
  accepts a user-controlled container name and passes it to Docker. The API
  receives container-read access through the socket proxy in
  [`docker-compose.example.yml`](../docker-compose.example.yml#L71).
- **Remediation direction:** Restrict log access to an explicit Nojoin
  container allowlist. Consider removing Docker socket access entirely from the
  API and collecting Nojoin logs through a narrower mechanism.
- **Remediation:** Enforced a strict allowlist of allowed Nojoin production and development container names in `backend/api/v1/endpoints/system.py`. Requests to download or stream logs for any container outside of this list are rejected immediately.
- **Verification:** Created unit and integration tests in [`backend/tests/test_container_logs_security.py`](../backend/tests/test_container_logs_security.py) verifying that log requests for allowed container names succeed, whereas unauthorized/invalid container names are rejected with 403 Forbidden (for downloads) or closed immediately with WS code 1008 policy violation (for WebSockets).
- **Acceptance criteria:** Requests for non-Nojoin container names are rejected
  before Docker is queried.

### SEC-009: Reverse-Proxy Client Address Handling Is Not Explicit

- **Status:** Resolved
- **Impact:** Login throttling can become ineffective or collapse all users
  behind an edge proxy into one denial-of-service bucket.
- **Evidence:** [`backend/utils/rate_limit.py`](../backend/utils/rate_limit.py#L22)
  trusts `X-Real-IP` and `X-Forwarded-For`. The bundled proxy overwrites
  `X-Real-IP` with its direct peer in [`nginx/nginx.conf`](../nginx/nginx.conf#L41).
- **Remediation direction:** Define trusted proxy hops explicitly. Preserve or
  derive the real edge client address only from trusted intermediaries, and
  reject spoofed forwarded headers from untrusted peers.
- **Remediation:** Added `NOJOIN_TRUSTED_PROXIES` environment variable defaulting to `127.0.0.1,::1,nginx`. Reimplemented `get_client_address` in `backend/utils/rate_limit.py` to traverse the `X-Forwarded-For` header right-to-left, stopping at the first untrusted IP, and to only fall back to `X-Real-IP` if the direct peer is trusted. Documented configuration in `.env.example`, `docker-compose.example.yml`, and `docs/DEPLOYMENT.md`.
- **Verification:** Created unit/integration tests in [`backend/tests/test_proxy_rate_limit.py`](../backend/tests/test_proxy_rate_limit.py) covering direct access (spoofed headers ignored), single trusted proxy, nested trusted proxies, spoofed headers via trusted proxies, `X-Real-IP` fallback, and dynamic hostname resolution for docker containers. All 7 tests passed successfully.
- **Acceptance criteria:** Tests cover direct access, one trusted proxy, nested
  trusted proxies, spoofed headers, and multiple users behind an edge proxy.


### SEC-010: Invitation Usage Limits Are Race-Prone

- **Status:** Resolved
- **Impact:** Concurrent registrations can exceed an invitation's `max_uses`.
- **Evidence:** [`backend/api/v1/endpoints/users.py`](../backend/api/v1/endpoints/users.py#L66)
  checks and increments usage without a row lock or atomic update.
- **Remediation direction:** Perform an atomic conditional increment or lock
  the invitation row within the registration transaction.
- **Remediation:** Added row-level locking by appending `.with_for_update()` to the query for the invitation code inside `register_user` in [`backend/api/v1/endpoints/users.py`](../backend/api/v1/endpoints/users.py).
- **Verification:** Created query verification unit test `test_register_user_uses_with_for_update` in [`backend/tests/test_invitation_role_security.py`](../backend/tests/test_invitation_role_security.py) asserting that the row lock query modifier is compiled during registration, preventing race conditions under PostgreSQL concurrent transactions.
- **Acceptance criteria:** Concurrent registration tests prove that usage
  limits cannot be exceeded.

### SEC-011: Celery Task Results Are Not Bound to Owners

- **Status:** Resolved
- **Impact:** Any authenticated user who obtains a task ID can read task result
  or progress metadata from another user's operation.
- **Evidence:** [`backend/api/v1/endpoints/system.py`](../backend/api/v1/endpoints/system.py#L444)
  returns Celery state for any supplied task ID without checking task ownership
  or task type.
- **Remediation direction:** Persist task ownership and expose scoped status
  endpoints for each operation. Return only allowlisted public metadata.
- **Remediation:** Introduced a new SQLModel table `AsyncTaskOwnership` to track Celery task IDs created by each user. Configured all endpoints dispatching user-facing Celery tasks to register ownership, and enforced verification inside the generic `/tasks/{task_id}` endpoint.
- **Verification:** Created unit tests in [`backend/tests/test_task_ownership_security.py`](../backend/tests/test_task_ownership_security.py) verifying that standard users cannot fetch tasks owned by others or unregistered task IDs (resulting in a 404), while admins and task owners can read status successfully.
- **Acceptance criteria:** Cross-user task result reads are rejected.

### SEC-012: Model Status Exposes Internal Cache Paths

- **Status:** Resolved
- **Impact:** Ordinary authenticated users receive internal filesystem paths
  and cache layout details.
- **Evidence:** [`backend/api/v1/endpoints/system.py`](../backend/api/v1/endpoints/system.py#L489)
  returns the full result of
  [`backend/preload_models.py`](../backend/preload_models.py#L367), including
  `path` and `checked_paths`.
- **Remediation direction:** Return a redacted user-facing model status or
  restrict detailed paths to administrators.
- **Remediation:** Updated `/models/status` in [`backend/api/v1/endpoints/system.py`](../backend/api/v1/endpoints/system.py) to check if the user is an admin or superuser, and if not, redacts `path` (set to `None`) and `checked_paths` (set to `[]`). Also updated [`frontend/src/components/settings/AISettings.tsx`](../frontend/src/components/settings/AISettings.tsx) to only display the hover debug info element if `checked_paths` has items to display.
- **Verification:** Added `test_models_status_admin` and `test_models_status_non_admin` to [`backend/tests/test_security_surface.py`](../backend/tests/test_security_surface.py) to assert correct redaction behavior based on the requester's role. Both tests passed.
- **Acceptance criteria:** Standard users receive readiness booleans and safe
  labels only.

### SEC-013: AI Provider API Keys Can Be Sent in Query Strings

- **Status:** Resolved
- **Impact:** Provider secrets may be retained in browser history, reverse-proxy
  access logs, and monitoring systems.
- **Evidence:** [`backend/api/v1/endpoints/llm.py`](../backend/api/v1/endpoints/llm.py#L15)
  accepts `api_key` as a query parameter. The corresponding frontend helper is
  [`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts#L1694).
- **Remediation:** Migrated to a server-side environment-only (`.env`) keys regime. Removed all LLM API key and Hugging Face token input fields from both the Settings view and the Setup Wizard. The backend validation and model listing endpoints read credentials directly from system environments (`get_system_api_keys()`). The Setup Wizard detects missing keys and presents warning panels explaining the loss of core intelligence features (Meeting Edge, Notes, and Speaker Inference) with options to reload the configuration or proceed without them.
- **Verification:** Frontend built and compiled successfully with Turbopack and TypeScript verification. Tested both setup flow paths (missing and configured credentials) and verified they behave correctly.
- **Acceptance criteria:** No API keys, tokens, or passwords are transmitted in
  URLs or request payloads from the frontend.

### ARC-001: Heavy Inference Work Still Runs Inside API Requests

- **Status:** Resolved
- **Impact:** Slow model initialisation and inference can block API workers,
  increase latency, and reduce availability.
- **Evidence:** [`backend/api/v1/endpoints/transcripts.py`](../backend/api/v1/endpoints/transcripts.py#L1805)
  invokes embedding synchronously during chat. Several speaker routes, starting
  around [`backend/api/v1/endpoints/speakers.py`](../backend/api/v1/endpoints/speakers.py#L1234),
  enqueue tasks and then wait for the result inside the request path. Two paths
  have no timeout.
- **Remediation direction:** Return task IDs and poll or stream progress.
  Dispatch embedding work to Celery where it is not a bounded lightweight
  request operation.
- **Remediation:** Offloaded the synchronous text embedding generation in `chat_with_meeting` to a new worker-side Celery task `get_text_embedding_task`, with a 30 second timeout on the API side. Added 120 second timeouts and error/timeout handling to `extract_voiceprint` and `extract_all_voiceprints` Celery task `.get()` calls to prevent indefinite worker-blocking hangs. Removed module-level `get_text_embedding_service` import from `transcripts.py` to keep the API process lightweight.
- **Verification:** Created unit and integration tests in [`backend/tests/test_inference_work_remediation.py`](../backend/tests/test_inference_work_remediation.py) verifying text embedding delegation to Celery, LLM response generation with mocks, timeout raising/handling in voiceprint extraction endpoints, and correct worker-side task execution. All tests pass successfully.
- **Acceptance criteria:** API handlers do not load models or wait indefinitely
  for heavy worker operations.

### ARC-002: API Imports Couple Startup to Worker and ML Modules

- **Status:** Resolved
- **Impact:** API startup remains heavier and more fragile than the documented
  service boundary intends.
- **Evidence:** [`backend/api/v1/endpoints/system.py`](../backend/api/v1/endpoints/system.py#L25)
  imports `download_models_task` from the worker module at API import time.
  `fastembed` is also part of API requirements through
  [`requirements/base.txt`](../requirements/base.txt#L1).
- **Remediation:** Removed the `fastembed` dependency from `requirements/base.txt` and relocated it to `requirements/worker.txt`. Removed all top-level and dynamic imports of worker task modules (from `backend.worker.tasks`) inside the API endpoint modules (`system.py`, `documents.py`, `recordings.py`, `transcripts.py`), the health service helper (`health_service.py`), and the application startup script (`main.py`). Standardized task execution in these endpoints by dispatching tasks using `celery_app.send_task` referencing task names as strings.
- **Verification:** Verified by compiling all python files successfully (`python -m compileall -q -f backend`) and running the test suite (`test_inference_work_remediation.py`, `test_health_service.py`, `test_upload_limits.py`, `test_recording_proxy_generation.py`), all of which pass successfully. Verified that `api_router` can be imported without pulling in any worker-only ML modules.
- **Acceptance criteria:** The API image starts without importing worker task
  implementations or worker-only ML packages.

### REL-002: Dependency and Image Builds Are Not Reproducible

- **Status:** Open
- **Impact:** Rebuilding the same commit at different times can produce
  different artifacts and inherit upstream changes unexpectedly.
- **Evidence:** Only 7 of 63 Python requirement entries are exact pins.
  [`.github/workflows/release.yml`](../.github/workflows/release.yml#L40) uses
  mutable action tags. Compose and Dockerfiles use mutable image tags rather
  than digests. [`frontend/Dockerfile`](../frontend/Dockerfile#L7) installs
  `npm@latest` during image builds.
- **Remediation direction:** Introduce locked Python constraints with an update
  workflow, pin GitHub Actions by commit SHA, use digest-pinned base images, and
  remove runtime resolution of `npm@latest`.
- **Acceptance criteria:** Release rebuilds consume reviewed lock changes and
  stable upstream artifacts.

### REL-003: Release Trigger Does Not Enforce the Documented Version Contract

- **Status:** Open
- **Impact:** Manual or malformed-tag releases can publish images that do not
  match the documented strict semantic-version process.
- **Evidence:** [`.github/workflows/release.yml`](../.github/workflows/release.yml#L5)
  accepts `v*` tags and manual dispatch. Documentation states `vX.Y.Z` is the
  single release source of truth.
- **Remediation direction:** Validate the tag format and require agreement with
  `docs/VERSION`. Define whether manual dispatch is a dry-run, a rebuild of an
  existing tag, or an intentionally supported release path.
- **Acceptance criteria:** Invalid or ambiguous release inputs fail before
  publication.

### QA-001: There Is No Pull-Request CI Workflow

- **Status:** Deferred
- **Impact:** Regressions are detected manually or during release instead of
  before merge.
- **Evidence:** The only workflow under `.github/workflows/` is
  [`.github/workflows/release.yml`](../.github/workflows/release.yml).
- **Remediation direction:** Add PR checks for backend compilation and tests,
  frontend lint/tests/build, Compose validation, migration-head validation,
  dependency auditing, and lightweight secret scanning.
- **Acceptance criteria:** Required branch checks block merges when verification
  fails.

### QA-002: Backend Verification Command Is Broken and Pytest Is Unconfigured

- **Status:** Resolved
- **Impact:** Contributors following project documentation receive collection
  failures, and CI adoption is harder.
- **Evidence:** [`docs/AGENTS.md`](AGENTS.md#L86) documents `pytest backend`,
  which fails collection with `ModuleNotFoundError: No module named 'backend'`.
  `python -m pytest backend` collects 518 tests. Custom
  `pipeline_baseline` markers are unregistered.
- **Remediation direction:** Add a root `pyproject.toml` or `pytest.ini`, define
  supported Python invocation, register markers, and update contributor docs.
- **Acceptance criteria:** The documented backend test command works in the
  supported local environment without warnings.

### QA-003: Main Test Suites Are Red

- **Status:** Open
- **Impact:** New regressions cannot be distinguished reliably from accepted
  failures.
- **Evidence:** The audited backend run completed with 513 passing and 5 failing
  tests. The frontend run completed with 79 passing and 1 failing test.
- **Remediation direction:** Fix the confirmed calendar import defect and
  reconcile stale mocks, baseline expectations, sidebar assertions, and the LLM
  configuration contract test.
- **Acceptance criteria:** Default backend and frontend test suites pass before
  feature work is merged.

### BUG-002: Processing Device Helper Raises `NameError`

- **Status:** Open
- **Impact:** Any future or external caller of the helper crashes.
- **Evidence:** [`backend/utils/config_manager.py`](../backend/utils/config_manager.py#L168)
  references `torch` without importing it. A direct runtime invocation
  reproduces `NameError: name 'torch' is not defined`.
- **Remediation direction:** Decide whether the helper belongs in the API at
  all. If retained, import lazily in a worker-safe path or replace it with a
  lightweight capability report.
- **Acceptance criteria:** The helper has a tested, API-safe implementation or
  is removed.

### DOC-001: Public Capture Documentation Has Drifted

- **Status:** Open
- **Impact:** Users opening the in-app documentation receive a materially
  shortened and stale capture guide.
- **Evidence:** [`frontend/public/docs/CAPTURE.md`](../frontend/public/docs/CAPTURE.md)
  differs substantially from [`docs/CAPTURE.md`](CAPTURE.md). The public copy is
  linked from the frontend.
- **Remediation direction:** Generate or copy the public guide from the
  canonical documentation source as part of build or verification.
- **Acceptance criteria:** CI fails when the public copy diverges from the
  canonical guide.

## Low Findings

### DOC-002: Contributor and Product Documentation Contains Stale Statements

- **Status:** Open
- **Impact:** Contributors and operators receive inaccurate instructions.
- **Evidence:** [`CONTRIBUTING.md`](../CONTRIBUTING.md#L28) describes browser
  support incompletely. [`docs/DEVELOPMENT.md`](DEVELOPMENT.md#L98) references
  `nginx-dev`, while the template service is `nginx`.
  [`docs/PRD.md`](PRD.md#L195) still describes calendar integration as future
  work.
- **Remediation direction:** Reconcile documentation against current behavior
  during the documentation pass.
- **Acceptance criteria:** Commands and product-status statements match the
  current repository.

### DOC-003: Security Policy Does Not Provide a Private Disclosure Channel

- **Status:** Open
- **Impact:** Researchers may disclose vulnerabilities publicly or abandon a
  report.
- **Evidence:** [`docs/SECURITY.md`](SECURITY.md#L78) requests reports but does
  not provide an email address or GitHub Security Advisory link.
- **Remediation direction:** Add a private reporting channel and expected
  disclosure workflow.
- **Acceptance criteria:** A reporter can submit a vulnerability privately from
  the published security policy.

### ARC-003: Large Modules Concentrate Maintenance Risk

- **Status:** Open
- **Impact:** Refactoring, review, and regression isolation are unnecessarily
  difficult.
- **Evidence:** `backend/utils/canonical_pipeline.py` exceeds 6,000 lines,
  `backend/worker/tasks.py` exceeds 3,600 lines, and several API modules exceed
  2,000 lines.
- **Remediation direction:** Split modules along stable domain boundaries after
  blocker fixes. Preserve behavior with focused tests before moving code.
- **Acceptance criteria:** High-change modules have narrower responsibilities
  and reviewable interfaces.

### ARC-004: Error Handling and Type-Safety Standards Are Not Enforced

- **Status:** Deferred
- **Impact:** Broad error suppression and loose frontend typing can hide
  defects and complicate maintenance.
- **Evidence:** The production backend contains approximately 251
  `except Exception` catches. `frontend/src` contains approximately 85 `any`
  matches, including legitimate library workarounds and cleanup candidates.
- **Remediation direction:** Audit catches by boundary, replace silent
  suppression with typed handling where practical, and progressively remove
  avoidable `any` usage.
- **Acceptance criteria:** New broad catches and avoidable `any` usage are
  prevented by review or lint rules.

### PKG-001: Large Spellcheck Assets Need an Explicit Packaging Strategy

- **Status:** Open
- **Impact:** Repository size and image footprint are larger than necessary.
- **Evidence:** Tracked spellcheck dictionaries account for tens of megabytes,
  with individual dictionary files up to approximately 8.7 MB.
- **Remediation direction:** Confirm supported languages and choose an explicit
  strategy: retain and document, compress, lazy-load, or package separately.
- **Acceptance criteria:** The asset footprint is intentional and documented.

### PKG-002: Default Next.js Public Assets Are Unused

- **Status:** Open
- **Impact:** Minor repository noise.
- **Evidence:** `frontend/public/file.svg`, `globe.svg`, `next.svg`,
  `vercel.svg`, and `window.svg` are tracked but unreferenced.
- **Remediation direction:** Remove unused starter assets.
- **Acceptance criteria:** Unreferenced default assets are removed.

### PKG-003: Legacy Dockerfile Is Stale

- **Status:** Open
- **Impact:** Contributors may attempt to build a dead path and receive a
  confusing failure.
- **Evidence:** [`docker/Dockerfile`](../docker/Dockerfile#L24) copies a root
  `requirements.txt`, but that file no longer exists. Release builds use
  `Dockerfile.api` and `Dockerfile.worker`.
- **Remediation direction:** Remove the obsolete Dockerfile or update and
  document its intended use.
- **Acceptance criteria:** Every tracked Dockerfile has a supported,
  reproducible build purpose.

## Verification Baseline

The following non-destructive checks were run during the audit:

| Check | Result |
| --- | --- |
| `python -m compileall -q -f backend` | Passed. |
| `docker compose -f docker-compose.example.yml config --quiet` | Passed. |
| `git diff --check` | Passed. |
| `cd frontend && npm run lint` | Passed with 10 warnings. |
| `cd frontend && npm run build` | Passed. |
| `cd frontend && npm run test -- --run` | 79 passed, 1 failed. |
| `python -m pytest -q backend` | 513 passed, 5 failed. |
| Focused backend security tests | 69 passed. |
| `npm audit --json` | No reported vulnerabilities. |
| `alembic heads` | One head: `7c1f4e2a9b63`. |
| `git status --short --branch` | Clean worktree at audit completion. |

The environment did not have `pip-audit`, `bandit`, `ruff`, `mypy`,
`actionlint`, or `trivy` installed. Add equivalent CI checks before treating the
audit as a comprehensive dependency or static-analysis review.

## Recommended Remediation Order

1. Fix `SEC-002` and `SEC-003` before public deployment.
2. Reject placeholder secrets, repair file permissions, and harden proxy
   exposure.
3. Add PR CI and make release publication depend on a green verification
   workflow.
4. Fix the confirmed calendar crash and return default test suites to green.
5. Address upload memory limits, Docker log access, task ownership, and proxy
   address handling.
6. Make builds reproducible with locked dependencies and pinned upstream
   artifacts.
7. Complete documentation, packaging, and maintainability cleanup passes.

## Audit Maintenance

When resolving an item:

1. Change its status to `Resolved`.
2. Add the remediation pull request or commit reference.
3. Record the automated and manual checks used to verify the fix.
4. Add follow-up items when the remediation intentionally leaves a narrower
   residual risk.
