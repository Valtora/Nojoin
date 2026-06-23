# Nojoin Development Setup

This guide covers local development prerequisites and the main commands used when working on Nojoin from source.

## Core Tooling

### General

- Git
- Docker

### Backend

- Python 3.12
- FFmpeg
- PostgreSQL development headers
- Compiler tools

Linux examples:

```bash
sudo apt install ffmpeg libpq-dev build-essential
```

Windows:

- Install FFmpeg and add it to `PATH`.
- Install the Microsoft Visual C++ Build Tools.

### Frontend

- Node.js 20 or newer
- npm

### Browser Capture

- Chrome on Windows, Linux, or macOS, or another supported desktop Chromium browser, for manual shared-audio live-capture validation
- Chrome on Android or iOS for manual microphone-only mobile capture validation
- Browser microphone permission for local smoke tests
- PipeWire screen capture support when validating Linux shared-screen or system audio behaviour

## Fresh Checkout Setup

Host-run validation expects the project virtual environment plus frontend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/local.txt

cd frontend
npm install
```

If you are working on a narrower area, you can install a smaller Python dependency set:

- backend tests only: `python -m pip install -r requirements/test.txt`
- API-only or worker-only runtime work: use the matching file under `requirements/`

## Required Pull Request Checks

The `CI` workflow enforces these required checks on pull requests and on pushes to `main`:

- `Backend tests`
- `Frontend lint`
- `Frontend unit tests`
- `Frontend build`
- `Docs validation`
- `Alembic validation`

Local equivalents:

```bash
source .venv/bin/activate
pytest

cd frontend
npm run lint
npm run test
npm run build

cd ..
python3 scripts/validate_docs.py
python3 scripts/validate_alembic.py
```

## Verification By Change Scope

- Backend or worker code: run `pytest`.
- Frontend code: run `npm run lint`, `npm run test`, and `npm run build`.
- Browser capture changes: run the frontend checks and perform manual smoke coverage for share picker behaviour, selected microphone behaviour, waveform/live state, pause/resume, stop/finalize, discard, and unsupported-browser messaging.
- Documentation-only changes: run `python3 scripts/validate_docs.py`.
- Alembic migration changes: run `python3 scripts/validate_alembic.py` and keep exactly one checked-in head revision.
- Deployment or release workflow changes: run the backend, frontend, docs, and Alembic validation set together before opening the pull request.
- Security-sensitive changes: rerun the relevant backend and frontend checks for the affected auth/session path and update `docs/SECURITY.md` in the same pull request when behaviour changes.
- Recording context-menu changes: update both `frontend/src/components/RecordingCard.tsx` and `frontend/src/components/Sidebar.tsx`, then run the full frontend lint, test, and build set.

## Compose Files

- `docker-compose.example.yml`: Deployment template using published images.
- `docker-compose.yml`: Local working copy created from the template.

The repository does not ship a dedicated Docker Compose development override.
If you need Docker-specific development customisations, make them in your local `docker-compose.yml`.

## Containerised Source Stack

The clearest Docker-based development workflow is to run a remote-development-style stack locally from your ignored `docker-compose.yml`.

In that mode:

- all local service containers use the `nojoin-dev-*` naming convention
- `https://localhost:14443` is served through Nginx
- the frontend comes from the `frontend` container
- `docker compose up -d --build frontend` changes what localhost serves

1. Create your local files from the templates:

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   cp .env.example .env
   ```

2. Update `.env` for local development. Keep `.env.example` unchanged because it remains the copy-paste template for non-development deployments. Set `FIRST_RUN_PASSWORD`. If you want the dedicated local development database name used by the compose template at the end of this document, set `POSTGRES_DB=nojoin_dev` in your local `.env` instead of changing the default `nojoin` value in `.env.example`.
3. Replace your local ignored `docker-compose.yml` with the `Localhost Dev Compose Template` appended at the end of this document.
4. Start or rebuild the stack:

   ```bash
   docker compose up -d --build
   ```

5. Open `https://localhost:14443`.

The appended template builds the Nojoin application services locally, keeps PostgreSQL, Redis, Nginx, and the Docker socket proxy on their normal upstream images.

### Incremental Rebuild Loop

Use the normal container rebuild loop when you are staying in the containerised localhost mode:

```bash
docker compose up -d --build api
docker compose up -d --build worker
docker compose up -d --build frontend
```

Practical use:

- Run `docker compose up -d --build api` after API changes or shared backend changes.
- Run `docker compose up -d --build worker` after worker code, dependency, or worker-image changes.
- Run `docker compose up -d --build frontend` after frontend changes that you want to verify through Nginx.

The compose files now gate `frontend` on a healthy `api`, and gate `nginx` (or `nginx-dev` in development) on healthy `api` plus `frontend`, so the proxy waits for both application tiers before becoming ready.

Docker Compose still does not auto-start an omitted dependent service from a stopped stack. If the Nginx proxy service is not already running and you want `https://localhost:14443` to come back as part of a targeted start, include it explicitly.

For development environments using `docker-compose.yaml`:

```bash
docker compose up -d --build api frontend nginx-dev
```

For production/release environments using the template configuration (`docker-compose.example.yml`):

```bash
docker compose up -d --build api frontend nginx
```

If you need to discard cached layers or the application services drift out of sync, use a clean rebuild:

```bash
docker compose down
docker compose build --no-cache api worker frontend
docker compose up -d --force-recreate
```

### Optional Backend Source-Mount Patch

If you want the API and worker to reflect Python changes without rebuilding those two images every time, patch your local ignored `docker-compose.yml` like this:

```yaml
services:
  api:
    command: uvicorn backend.main:app --host 0.0.0.0 --port 8000
    volumes:
      - .:/app
      - ./data:/app/data
      - ./data/recordings:/app/recordings
      - model_cache:/shared_model_cache:ro
      - backup_temp:/tmp

  worker:
    command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker --loglevel=info --pool=solo
    volumes:
      - .:/app
      - ./data:/app/data
      - model_cache:/home/appuser/.cache
      - /sys/class/drm:/sys/class/drm:ro
      - backup_temp:/tmp
```

That patch is optional. It changes the backend feedback loop only. It does not change the frontend contract. If Nginx still proxies the `frontend` container, rebuilding `frontend` remains the way to update `https://localhost:14443`.

### Optional Host-Run Frontend Workflow

If you want the fastest UI feedback loop, you can instead run Next.js on the host. Treat that as a different local mode, not as a small patch on top of the containerised template above.

In host-run frontend mode:

- rebuilding the `frontend` container no longer changes what `https://localhost:14443` serves
- you must keep the host Next.js process running yourself
- you should patch both your local `docker-compose.yml` and your local `nginx/nginx.conf` together so Nginx proxies to the host frontend instead of the `frontend` container

Run the host frontend like this:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=/api npm run dev -- --hostname 0.0.0.0 -p 14141
```

After frontend changes, run production build and lint checks since development mode is more forgiving:

```bash
cd frontend
npm run lint
npm run build
```

If you only need supporting services while running code on the host, start the specific services you need. Examples include `db` and `redis`.

If you do not have an NVIDIA GPU, use CPU-only mode as described in [DEPLOYMENT.md](DEPLOYMENT.md) before starting the stack.

## Backend Development Notes

- The compose template does not publish PostgreSQL or Redis to the host by default.
- If you want host-based tooling or host-run services to talk to containerised PostgreSQL or Redis, add the required `ports` entries in your local `docker-compose.yml`.
- Heavy ML libraries must stay inside worker task functions, not API startup paths.

Useful migration and testing commands:

```bash
# Run database migrations
alembic upgrade head

# Create a new migration revision
alembic revision --autogenerate -m "message"

# Sweep legacy recordings (manual run)
python -m backend.startup_canonical_cutover

# Run backend tests (ensure the virtual environment is active first)
source .venv/bin/activate
pytest

# Validate docs and Alembic graph before opening a pull request
python3 scripts/validate_docs.py
python3 scripts/validate_alembic.py
```

Development guardrails:

- Do not delete or rename committed Alembic revision files once a database may have applied them.
- Container startup now runs two backend migration stages in order: Alembic first, then the startup canonical cutover sweep for pending legacy recordings.
- `python -m backend.startup_canonical_cutover` is the backend-only local entry point for the legacy recording sweep. Use it when you need to exercise or debug the container-level cutover without booting the full API service.
- `NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER=1` is available for local debugging only when you need to bypass that sweep temporarily.
- `NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE` controls the batch size used by the startup cutover loop. The default is `100`.
- The localhost dev compose template at the end of this document sets `NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS=true` on the API service. If a local dev database is stamped to a revision that no longer exists in your checkout, startup will restamp it to the current checked-in head before running `alembic upgrade head`.
- Keep that auto-repair flag limited to disposable local databases. For persistent deployments, fix the migration graph or reconcile the database revision manually instead of auto-stamping.

## Browser Capture Development

Browser capture code lives under `frontend/src/lib/capture/` and is exercised by the recording page and capture settings surfaces.

When changing capture behaviour, validate the relevant parts of this path:

- Supported-browser gating for desktop Chromium on Windows, Linux, and macOS.
- Unsupported-browser messaging for Firefox, Safari, and mobile browsers other than Chrome.
- Mobile Chrome microphone-only start, waveform, pause/resume, stop/finalize, and clear copy that shared app/tab/system audio is not captured.
- Browser share picker flow for tab, window, and screen sharing.
- Shared-audio track detection and missing-audio messaging.
- Microphone permission and selected-device behaviour.
- Per-source gain controls in **Settings > Capture**.
- Segment creation, sequential upload, worker transcode, live transcript dispatch, stop/finalize, pause/resume, and discard.
- The paused-recording lock after refresh or close (actual tab unload, not in-app navigation).
- Focus changes to another tab, window, or application; these should not pause capture.

Useful focused checks:

```bash
cd frontend
npm run test -- --run src/lib/capture
npm run build
```

If you are validating through the containerised localhost stack, rebuild the frontend container after frontend changes:

```bash
docker compose up -d --build frontend
```

Read [CAPTURE.md](CAPTURE.md) before changing support copy, browser compatibility behaviour, or troubleshooting guidance.

### Spellcheck Dictionaries

Spellcheck dictionaries are stored under `frontend/public/dictionaries/` in gzip-compressed format (`index.aff.gz` and `index.dic.gz`) to optimize repository size and container image build footprint.

If you add a new language or update an existing dictionary:
1. Obtain the raw `.aff` and `.dic` files.
2. Compress them using gzip:
   ```bash
   gzip -k index.aff
   gzip -k index.dic
   ```
3. Commit only the compressed `.gz` files under `frontend/public/dictionaries/<locale>/`. Do not track the raw uncompressed files.

## Backend Coding Conventions

### Language & Formatting
- **Formatting & Imports**: Code is formatted and import-sorted by Ruff. Run `ruff format .` and `ruff check --fix .`, or let the pre-commit hook do it. Do not hand-format.
- **Type Hints**: New and changed public/cross-module code should be fully annotated. Type checking is enforced incrementally by mypy on the boundary listed under `[tool.mypy].files` in `pyproject.toml` (configuration, core security/persistence contracts, and API-facing schema/model modules). Add modules to that list as they are annotated; annotate public functions and cross-module interfaces before internal helpers.
- **Error Handling**: Use `HTTPException` in API endpoints to raise HTTP-level issues. Catch specific exception types where recovery differs; a deliberately broad `except Exception` at a recovery boundary must log actionable context (or re-raise with `raise ... from`) and carry a justification comment in the form `# noqa: BLE001 -- boundary: <reason>`.
- **Logging**: Use lazy `%`-style formatting (`logger.info("processed %s", count)`), not eager f-strings, in frequently executed paths so the interpolation is skipped when the level is disabled.

### Complexity & Size Thresholds
New and changed code is gated on function complexity and module size to keep maintainability hotspots from spreading:

- **Function complexity**: Ruff `C901` enforces a McCabe complexity ceiling of 10 (`[tool.ruff.lint.mccabe] max-complexity = 10`).
- **Function size & arguments**: Ruff `PLR0915` (too-many-statements), `PLR0912` (too-many-branches), and `PLR0913` (too-many-arguments) at Ruff's Pylint-default limits.
- **File size**: Backend Python source files (excluding `backend/tests/`, `test_*.py`/`*_test.py`, and Alembic migrations under `backend/alembic/versions/`) must be **at most 1000 lines**. This is enforced by `scripts/check_file_size.py`, which runs in `scripts/check.py` and CI.

Existing violators predate the gate and are **grandfathered**: the complexity/size Ruff rules are ignored per-file under the `# BE-008 complexity baseline` block in `[tool.ruff.lint.per-file-ignores]`, and over-length files are listed with their current line count in the `GRANDFATHERED` allowlist in `scripts/check_file_size.py`. The policy is **shrink, not grow**: a grandfathered file fails the size check if it grows beyond its recorded count, and you should remove its baseline entries as it drops back under the limits. **Do not add new grandfather entries** — new files and new code in non-grandfathered files must comply with the thresholds above.

### Comments
These rules apply to all tracked source (backend, worker, and frontend), not just Python.

- **Explain intent, not syntax**: A comment should document an invariant, constraint, risk, compatibility requirement, or other non-obvious intent. Do not narrate what the code already says line by line.
- **No indecisive developer-thought comments**: Remove musings such as "we can commit here", "usually", "assume consistency", "maybe skipped?", or questions embedded in authorisation rules. If the code's behaviour is a deliberate rule, state the rule.
- **Rewrite uncertainty as a contract**: If something is genuinely uncertain or conditional, express it as an explicit invariant, a documented fallback policy, a tracked issue reference, or a testable assumption — not as a hedge. Where the rule is security- or authorisation-sensitive, lock it with a test rather than a comment.
- **Preserve load-bearing comments**: Keep comments that document live/final pipeline alignment, security and authorisation boundaries, migration and backward-compatibility requirements, browser-capture contracts, and non-obvious build or runtime constraints. When in doubt, make such a comment more precise rather than deleting it.

### Local Checks From A Fresh Checkout
Reproduce the CI Python checks locally with a single command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements/dev.txt   # tests + lint/format/type tooling (CPU)
pre-commit install                              # optional: run lint/format on commit

python scripts/check.py            # ruff lint, format check, trailing-whitespace, file-size, mypy, doc/alembic validators, pytest
python scripts/check.py --fix      # auto-fix lint + formatting first
python scripts/check.py lint mypy  # run a subset
```

Use `requirements/local.txt` instead of `dev.txt` for a full GPU host with the live processing stack.

### Data Access & Dependency Injection
- **Data Access**: `SQLModel` is used for ORM. All model files are located in [backend/models/](../backend/models/).
- **Dependency Injection**: Use `backend.api.deps` for DB sessions (`SessionDep`) and retrieving the current user (`CurrentUser`).

### System Configuration
- **Configuration Management**: `backend.utils.config_manager` is the central utility handling system and user settings persisted in `data/config.json`. Do not implement parallel ad-hoc configuration storage mechanisms.

### Audio Processing & ML Operations
- **Library Imports**: ML libraries (such as `torch`, `whisper`, `pyannote`) are computationally heavy and must be imported **inside** Celery task functions (under [backend/worker/tasks/](../backend/worker/tasks/)) to ensure quick backend API startup.
- **Pluggable ASR Engines**: The ASR Transcribe step dispatches to a pluggable engine under [backend/processing/engines/](../backend/processing/engines/), determined by the `transcription_backend` configuration key. Pluggable engines (e.g. Whisper, or onnx-asr engines like Parakeet and Canary) share the `OnnxAsrEngine` base class. Note that Parakeet offers faster transcription on compatible NVIDIA systems but trades off accuracy and language coverage compared to Whisper.
- **Diarisation manifests**: `RecordingAudioWindowManifest.status` is a legacy compatibility projection. Use `asr_status` for live/catch-up ASR coverage and `diarization_status`, `diarization_config_hash`, and `diarization_window_result_id` for rolling or catch-up speaker-window coverage. Never treat legacy `live_processed` as diarisation-complete.
- **Phantom Speaker Filter**: The post-diarisation stage [backend/processing/phantom_filter.py](../backend/processing/phantom_filter.py) filters and reassigns speech segments caused by non-speech notification sounds or background noise. It uses heuristic duration/segment count checks followed by embedding-based validation. Constant thresholds (`PHANTOM_MAX_DURATION_S`, `PHANTOM_MAX_SEGMENTS`, `PHANTOM_EMBEDDING_FLOOR`, and `PHANTOM_MERGE_THRESHOLD`) are defined in that file.
- **Speaker Identification Constants**: All speaker matching thresholds are centralized in [backend/processing/embedding.py](../backend/processing/embedding.py). Do not hardcode threshold values elsewhere; import and reference the named constants (such as `IDENTIFICATION_THRESHOLD`, `AUTO_UPDATE_THRESHOLD`, `MARGIN_OF_VICTORY`, `DRIFT_GUARD_THRESHOLD`, `SCAN_MATCH_THRESHOLD`, `UI_SHOW_MATCH_THRESHOLD`, and `UI_STRONG_MATCH_THRESHOLD`).
- **PyTorch 2.6+ safe globals**: PyTorch 2.6+ defaults to `weights_only=True` in `torch.load` for security, blocking loading of custom classes (like `pyannote.audio.core.task.Specifications` and `torch.torch_version.TorchVersion`) from model checkpoints. These classes must be explicitly registered prior to loading models via `torch.serialization.add_safe_globals([...])` at the module level in `embedding_core.py` and `diarize.py`.

## Frontend Coding Conventions

### Architecture & UI Guidelines
- **State Management**: **Zustand** (defined in [frontend/src/lib/store.ts](../frontend/src/lib/store.ts)) is the global UI state manager (handling navigation, selection, and filters). Avoid prop drilling wherever possible.
- **API Client Layer**: All API communication must go through the [frontend/src/lib/api/](../frontend/src/lib/api/) layer, a barrel that re-exports the typed per-resource clients.
- **Security & Tokens**:
  - Browser auth uses HttpOnly session cookies issued by `/api/v1/login/session`.
  - Bearer tokens from `/api/v1/login/access-token` are reserved for non-browser API clients.
  - Never expose bearer tokens in URL query strings or other browser-visible areas.
- **Routing & Framework**: Use the Next.js App Router ([frontend/src/app/](../frontend/src/app/)) and Tailwind CSS for styling. Prefer functional components inside [frontend/src/components/](../frontend/src/components/).
- **Strict TypeScript**: Avoid the use of `any` types. Ensure TS interfaces in `frontend/src/types/index.ts` are updated first when adding settings or model fields.

### UI Duplication Rules
- **Context Menus warning**: When modifying the context menu options for recording cards/rows, you **must** update both of the following files to prevent UI divergence:
  - [frontend/src/components/RecordingCard.tsx](../frontend/src/components/RecordingCard.tsx) (handles the recording grid view).
  - [frontend/src/components/Sidebar.tsx](../frontend/src/components/Sidebar.tsx) (handles the sidebar recording list view).

## Release Workflow and Version Detection

### Unified Release Process

Nojoin uses a single Git tag (`vX.Y.Z`) to trigger the API, Worker, and Frontend release builds in lock-step. The maintainer steps are unchanged by the supply-chain hardening; what changed is that more happens automatically after the tag is pushed, and the release can now be blocked by a gate.

**Maintainer steps (manual):**

1. **Merge and sync**: Merge the work for the release into `main` and ensure your local `main` is up to date.
2. **Update version**: Update [VERSION](VERSION) to the new version string (e.g. `0.6.0`). The tag must match this value exactly or the release fails fast.
3. **Commit and tag**: Commit the version bump, then create and push the tag:
   ```bash
   git add docs/VERSION
   git commit -m "chore: bump version to 0.6.0"
   git tag v0.6.0
   git push origin v0.6.0
   ```
4. **Refine release notes (after the run succeeds)**: The pipeline creates the GitHub Release automatically (see below). Edit its editorial sections — Migration, Rollback, Known Issues, Browser-Capture Compatibility — in the GitHub Releases interface where the release needs specific guidance. You no longer author release notes from scratch.

**What the pipeline does automatically (on tag push):** The push of a strict `vX.Y.Z` tag triggers [.github/workflows/release.yml](../.github/workflows/release.yml), which runs in this order:

1. Re-runs the full backend, frontend, docs, and Alembic validation set and verifies `docs/VERSION` matches the tag.
2. Builds each image and publishes only the immutable `version` and commit-`sha` tags, with provenance and SBOM attestations.
3. Scans each image with Trivy and **fails the release on fixable CRITICAL/HIGH vulnerabilities** (see [Image Provenance, SBOM, and Signing](#image-provenance-sbom-and-signing) and the severity policy in [SECURITY.md](SECURITY.md)).
4. Signs each image with cosign, then runs the non-root and health smoke.
5. Publishes the rolling `latest` and `major.minor` tags only after all the above pass.
6. Generates and publishes the GitHub Release notes from the exact previous-tag-to-this-tag range (see [Automated Release Notes](#automated-release-notes-rel-013-rel-014)).

Because of step 3, a tag push no longer guarantees published images: if scanning finds a fixable CRITICAL/HIGH vulnerability the run fails and the rolling tags are not moved. The usual fix is to merge the relevant Dependabot base-image or dependency update (or, for a justified unfixable case, add a documented, dated entry to [.trivyignore](../.trivyignore)) and cut the tag again.

Manual `workflow_dispatch` runs must target an existing release tag through `release_ref`; they only publish `latest` when `publish_latest=true` is set explicitly.

### Runtime Version Detection
The backend API resolves the running server version from image build metadata (checking `NOJOIN_SERVER_VERSION` environment variable and `/app/.build-version` file), falling back to local `docs/VERSION` in development/testing. User-facing release metadata is resolved from the GitHub Releases API first, with GHCR tags and raw `docs/VERSION` file used as fallbacks.

## Supply-Chain and Release Hardening

The release pipeline is hardened to make published images reproducible, traceable, and verifiable. Contributors changing CI, the release workflow, or the Dockerfiles must keep the controls below intact.

### Pinned Actions and Base Images

- Every third-party GitHub Action in [.github/workflows/](../.github/workflows/) is pinned to a full commit SHA with a trailing `# vX.Y.Z` comment. Do not reintroduce floating tags such as `@v5`; a mutable tag can be repointed at malicious code after review.
- Every container base image in the Dockerfiles is pinned by `@sha256:` digest with the human-readable tag kept as a comment. The digest is the immutable identity of the image; the tag alone is mutable.
- When you intentionally upgrade an action or base image, update both the SHA/digest and the version comment in the same change.

### Dependabot

[.github/dependabot.yml](../.github/dependabot.yml) keeps four ecosystems current on a weekly cadence: GitHub Actions, Python (`requirements/`), npm (`frontend/`), and Docker base images. Dependabot rewrites pinned SHAs and digests in place and updates the version comment, so pinning does not cause drift. Review and merge these update pull requests like any other change; they run the full CI suite.

### Image Provenance, SBOM, and Signing

Every published image is signed with cosign keyless (OIDC) signing and carries build-provenance and SBOM attestations (`provenance: mode=max`, `sbom: true` in the build step). The signature is bound to the release workflow identity, so the `server-release` job requires `id-token: write`. Operator verification commands live in [DEPLOYMENT.md](DEPLOYMENT.md#verifying-an-image-before-deploying).

### Gated Tag Publication

The release flow publishes the immutable `version` and commit-`sha` tags during the build, then publishes the rolling `latest` and `major.minor` tags from a separate `publish-mutable-tags` job only after vulnerability scanning, the image health smoke, and signing all pass. This means a build that fails a gate can briefly expose an immutable `vX.Y.Z` tag (with the run visibly failing) but can never advance the `latest` tag that operators pull by default. Keep this ordering intact when editing the release workflow.

### Health and Non-Root Smoke (REL-012)

The `health-smoke` job brings up the freshly built api and frontend images with their real `docker-compose` dependencies (Postgres, Redis, the socket proxy) and waits for the production healthchecks to report `healthy`. It then asserts each running container's uid is non-root. The worker requires a GPU and preloaded models to boot, so its non-root `USER` is asserted from the published image config via `docker buildx imagetools inspect` (which reads the config without pulling the large layers) rather than by booting it. The rolling tags are not published unless this job passes.

### Automated Release Notes (REL-013, REL-014)

After the rolling tags publish, the `publish-release-notes` job creates the GitHub Release. It resolves the exact previous-tag→this-tag commit range with `git describe`, renders the changelog from that range, resolves the published image digests, and fills [.github/release-notes-template.md](../.github/release-notes-template.md). The template carries the required sections — Upgrade, Migration, Rollback, Known Issues, and Browser-Capture Compatibility — with sensible defaults that maintainers refine in the GitHub Releases UI when a release needs specific guidance. `make_latest` follows the same `publish_latest` decision as the image tags.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)

## Localhost Dev Compose Template

Copy this into your ignored `docker-compose.yml` when you want a containerised localhost development instance that mirrors the remote development deployment naming and rebuild behaviour.

```yaml
name: nojoin-dev

x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

x-shared-app-environment: &shared-app-environment
  DATABASE_URL: postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-postgres}@db:5432/${POSTGRES_DB:-nojoin_dev}
  REDIS_URL: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  CELERY_BROKER_URL: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  CELERY_RESULT_BACKEND: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  HF_TOKEN: ${HF_TOKEN:-}
  DEFAULT_TIMEZONE: ${DEFAULT_TIMEZONE:-UTC}
  LLM_PROVIDER: ${LLM_PROVIDER:-gemini}
  GEMINI_API_KEY: ${GEMINI_API_KEY:-}
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
  OLLAMA_API_URL: ${OLLAMA_API_URL:-http://host.docker.internal:11434}
  SECONDARY_LLM_PROVIDER: ${SECONDARY_LLM_PROVIDER:-}
  SECONDARY_GEMINI_API_KEY: ${SECONDARY_GEMINI_API_KEY:-}
  SECONDARY_OPENAI_API_KEY: ${SECONDARY_OPENAI_API_KEY:-}
  SECONDARY_ANTHROPIC_API_KEY: ${SECONDARY_ANTHROPIC_API_KEY:-}
  SECONDARY_OLLAMA_API_URL: ${SECONDARY_OLLAMA_API_URL:-http://host.docker.internal:11434}
  DATA_ENCRYPTION_KEY: ${DATA_ENCRYPTION_KEY:-}
  GOOGLE_OAUTH_CLIENT_ID: ${GOOGLE_OAUTH_CLIENT_ID:-}
  GOOGLE_OAUTH_CLIENT_SECRET: ${GOOGLE_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_CLIENT_ID: ${MICROSOFT_OAUTH_CLIENT_ID:-}
  MICROSOFT_OAUTH_CLIENT_SECRET: ${MICROSOFT_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_TENANT_ID: ${MICROSOFT_OAUTH_TENANT_ID:-common}

services:
  db:
    container_name: nojoin-dev-db
    image: pgvector/pgvector:pg18-trixie
    volumes:
      - postgres_data:/var/lib/postgresql
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
      POSTGRES_DB: ${POSTGRES_DB:-nojoin_dev}
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-nojoin_dev}",
        ]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  redis:
    container_name: nojoin-dev-redis
    image: redis:alpine
    command: /bin/sh -ec 'printf "requirepass %s\n" "$$REDIS_PASSWORD" > /tmp/redis.conf && exec redis-server /tmp/redis.conf'
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-change_to_secure_string}
      REDISCLI_AUTH: ${REDIS_PASSWORD:-change_to_secure_string}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  socket-proxy:
    container_name: nojoin-dev-socket-proxy
    image: tecnativa/docker-socket-proxy
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      CONTAINERS: "1"
      POST: "0"
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  api:
    container_name: nojoin-dev-api
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    image: nojoin-dev-api:local
    pull_policy: never
    volumes:
      - ./data:/app/data
      - ./data/recordings:/app/recordings
      - model_cache:/shared_model_cache:ro
      - backup_temp:/tmp
    environment:
      <<: *shared-app-environment
      DOCKER_HOST: tcp://socket-proxy:2375
      WEB_APP_URL: ${WEB_APP_URL:-https://localhost:14443}
      NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS: ${NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS:-true}
      FIRST_RUN_PASSWORD: ${FIRST_RUN_PASSWORD:?Set FIRST_RUN_PASSWORD in .env}
      XDG_CACHE_HOME: /shared_model_cache
      HF_HOME: /shared_model_cache/huggingface
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      socket-proxy:
        condition: service_started
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import json, sys, urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/api/health', headers={'X-Forwarded-Proto': 'https'}); data = json.load(urllib.request.urlopen(req, timeout=3)); sys.exit(0 if data.get('status') == 'ok' else 1)\"",
        ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  worker:
    container_name: nojoin-dev-worker
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    image: nojoin-dev-worker:local
    pull_policy: never
    volumes:
      - ./data:/app/data
      - model_cache:/home/appuser/.cache
      - /sys/class/drm:/sys/class/drm:ro
      - backup_temp:/tmp
    environment:
      <<: *shared-app-environment
      NVIDIA_VISIBLE_DEVICES: ${NVIDIA_VISIBLE_DEVICES:-all}
      NVIDIA_DRIVER_CAPABILITIES: ${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}
      WHISPER_ENABLE_WORD_TIMESTAMPS: ${WHISPER_ENABLE_WORD_TIMESTAMPS:-true}
      XDG_CACHE_HOME: /home/appuser/.cache
      HF_HOME: /home/appuser/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  frontend:
    container_name: nojoin-dev-frontend
    build:
      context: ./frontend
      dockerfile: Dockerfile
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
    image: nojoin-dev-frontend:local
    pull_policy: never
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:14141/"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 15s
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  nginx-dev:
    container_name: nojoin-dev-nginx
    image: nginx:alpine
    ports:
      - "${NOJOIN_BIND_ADDRESS:-127.0.0.1}:14141:80"
      - "${NOJOIN_BIND_ADDRESS:-127.0.0.1}:14443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx:/etc/nginx/certs
      - ./docker/init-ssl.sh:/docker-entrypoint.d/99-init-ssl.sh
    depends_on:
      frontend:
        condition: service_healthy
      api:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "curl -k -f -s -o /dev/null https://127.0.0.1/api/health && curl -k -f -s -o /dev/null https://127.0.0.1/",
        ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped
    networks:
      nojoin_net:
      proxy_net:
        aliases:
          - nojoin-dev-nginx
    logging: *default-logging

volumes:
  postgres_data:
  model_cache:
  redis_data:
  backup_temp:

networks:
  nojoin_net:
    driver: bridge
  proxy_net:
    external: true
```
