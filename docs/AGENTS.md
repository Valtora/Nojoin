# Nojoin AI Agent Instructions

## Start Here

- Read [../README.md](../README.md) for product scope and major entry points.
- Read [DEVELOPMENT.md](DEVELOPMENT.md) before running local commands or changing build tooling.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing component boundaries or request flows.
- Read [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, GPU or CPU mode, `.env` setup, and remote access configuration.
- Read [CALENDAR.md](CALENDAR.md) before touching calendar OAuth or sync behavior.
- Read [SECURITY.md](SECURITY.md) before changing auth, tokens, encryption, or exposure of sensitive data.

## Project Context

Nojoin is a distributed meeting intelligence platform. The system records system audio via a local Rust companion app, processes the data on a GPU-enabled Docker backend, and presents insights via a Next.js web interface.

**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Configurable Privacy (Self-hosted with optional local-only AI).

## Architecture & Patterns

### Backend (FastAPI + Celery)

- **Service Boundary**: The `backend/` directory handles API requests and offloads heavy processing to Celery workers via Redis.
  - **Rule**: API endpoints must NEVER run heavy inference (Whisper, Pyannote, LLMs) synchronously. Heavy tasks must be dispatched to Celery.
- **Data Access**: `SQLModel` is used for ORM. Models are located in `backend/models/`.
- **Dependency Injection**: `backend.api.deps` must be used for DB sessions (`SessionDep`) and current user (`CurrentUser`).
- **Heavy Processing**:
  - **Location**: `backend/worker/tasks.py`.
  - **Constraint**: Heavy libraries (torch, whisper, pyannote) must be imported **inside** the task function to keep the API lightweight and ensure fast startup times.
  - **Pipeline**: Validation -> VAD (Silero) -> Proxy Creation (Alignment) -> Transcribe (Whisper) -> Diarize (Pyannote) -> Phantom Speaker Filter -> Merge -> Voiceprint Extraction -> Deterministic Speaker Resolution (manual names, merge handling, global matches) -> Automatic Meeting Intelligence (one provider call for unresolved speaker suggestions, title, and Markdown notes when AI is configured).
  - **Manual AI Flows**: Automatic AI enhancement is provider-gated and no longer uses separate per-feature toggles. Manual `Generate Notes` remains notes-only, and manual `Retry Speaker Inference` remains speaker-only.
  - **Transcription Engine**: The Transcribe step dispatches to a pluggable engine (`backend/processing/engines/`), selected by the `transcription_backend` config key. Whisper is the default; Parakeet (onnx-asr) is selectable.
  - **Phantom Speaker Filter**: Post-diarization stage (`backend/processing/phantom_filter.py`) that detects and reassigns segments caused by non-speech sounds (notifications, background noise). Uses heuristic detection (duration/segment count) followed by embedding-based validation. Thresholds are defined as named constants in `phantom_filter.py` (`PHANTOM_MAX_DURATION_S`, `PHANTOM_MAX_SEGMENTS`, `PHANTOM_EMBEDDING_FLOOR`, `PHANTOM_MERGE_THRESHOLD`).
  - **Speaker Identification Constants**: All speaker matching thresholds are centralised in `backend/processing/embedding.py`. Do not hardcode threshold values elsewhere; import and reference the named constants (`IDENTIFICATION_THRESHOLD`, `AUTO_UPDATE_THRESHOLD`, `MARGIN_OF_VICTORY`, `DRIFT_GUARD_THRESHOLD`, `SCAN_MATCH_THRESHOLD`, `UI_SHOW_MATCH_THRESHOLD`, `UI_STRONG_MATCH_THRESHOLD`).
  - **PyTorch 2.6+ & Safe Globals**: The project uses PyTorch 2.6+, which defaults `weights_only=True` in `torch.load` for security.
    - **Issue**: This blocks loading of custom classes (like `pyannote.audio.core.task.Specifications` and `torch.torch_version.TorchVersion`) from model checkpoints.
    - **Solution**: These classes must be explicitly added to the safe globals list using `torch.serialization.add_safe_globals([...])` **before** loading the model. This is handled at the module level in `embedding_core.py` and `diarize.py`.
  - **Configuration**: `backend.utils.config_manager` is used to handle system and user-specific settings persisted in `data/config.json`. Do not add parallel ad hoc config storage.

### Frontend (Next.js + Zustand)

- **State Management**: **Zustand** (`src/lib/store.ts`) is used for global UI state (navigation, selection, filters). Prop drilling should be avoided.
- **API Layer**: All API calls MUST go through `src/lib/api.ts`.
  - Browser authentication uses the Secure HttpOnly session cookie issued by `/api/v1/login/session`.
  - Explicit Bearer tokens from `/api/v1/login/access-token` are reserved for non-browser API clients.
  - Companion pairing uses a manual code-based flow, establishing a single-backend association and receiving a revocable companion credential plus local control secret. The browser never receives a reusable Companion bearer token.
  - `/api/v1/recordings/init` returns a short-lived upload token bound to the newly created recording. The Companion must use that token for segment uploads, client-status updates, finalisation, and discard flows.
  - `force_password_change` is enforced server-side. Flagged users may only fetch `/api/v1/users/me`, update `/api/v1/users/me/password`, or log out until they rotate their password.
  - Never put bearer tokens into URL query strings or other browser-visible locations.
- **Routing**: The App Router (`src/app/`) is utilized.
- **Styling**: Tailwind CSS is the standard styling framework.
- **Components**: Functional components in `src/components/` are preferred.

### Companion App (Tauri + Rust)

- **Structure**:
  - `src-tauri/`: Rust backend code.
  - `src/`: Frontend assets (currently minimal).
- **Platform Support**: Windows only. macOS and Linux support is not currently available (contributions are welcome).
- **Concurrency**:
  - **Audio Thread**: Captures audio using `cpal` and communicates via `crossbeam_channel`.
  - **Server/Upload Thread**: Uses the `tokio` runtime.
- **Upload Strategy**:
  - Segments are numbered sequentially but uploaded concurrently (racing upload tasks plus retries) to `/recordings/{id}/segment`. The backend re-imposes order via a sequence-gated buffer in the live transcription task.
  - **Retries**: Implemented in `src-tauri/src/uploader.rs` with exponential backoff.
- **UI**: The system tray is managed by Tauri.
- **Configuration** (`config.json`):
  - **Location**: `%APPDATA%\Nojoin Companion` (Windows).
  - `api_host`: Backend API hostname/IP (default: "localhost").
  - `api_port`: Backend API port (default: 14443).
  - `local_port`: Local server port (default: 12345).
  - `api_token`: JWT token obtained via web-based authorization.
- **Authorization**:
  - The Companion app initiates pairing manually, displaying a single-use code.
  - The web app sends the code and bootstrap Companion token to the Companion's pairing endpoint.
  - The Companion local API has two classes of routes: the short-lived pairing route, and the authenticated steady-state routes that require a short-lived local control token and strict Host validation. Anonymous detection is explicitly blocked.
  - Each `/recordings/init` response provides the per-recording upload token used for segment upload, status changes, finalisation, and discard.
  - Manual configuration is available via System Tray > Settings.
- **Installer**: Built via Tauri Bundler for Windows. Installs to `%LOCALAPPDATA%\Nojoin`.

## Critical Workflows

### Commands

- **Start Infrastructure**:
  - **Operator deployment**: copy the compose and env templates to local files, then run `docker compose up -d`
  - **CPU**: `docker compose up -d` after removing the `deploy` section from `docker-compose.yml`
  - **Local source development**: use the host and local-compose workflows described in `docs/DEVELOPMENT.md`
  - **Remote Access**: Ensure `.env` is configured with the correct `WEB_APP_URL`.
- **Migrations**:
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
- **Backend Tests**:
  - Run: `pytest backend`
- **Frontend**:
  - Development: `cd frontend && npm install && npm run dev`
  - Verification: `cd frontend && npm run build`
  - Lint: `cd frontend && npm run lint`
- **Companion (Windows)**:
  - Development: `cd companion && npm run tauri dev`
  - Release Build: `cd companion && npm run tauri build`
  - **Note**: Build from a Windows environment. WSL2 may have UNC path issues.
  - **Environment Variables**: Ensure `TAURI_PRIVATE_KEY` and `TAURI_KEY_PASSWORD` (if applicable) are set in the Windows environment variables or PowerShell session before building.
- **Companion Installer (Windows)**:
  - Build: `cd companion && npm run tauri build`
  - Output: `companion/src-tauri/target/release/bundle/nsis/Nojoin Setup X.Y.Z.exe`

### Release Workflow (Unified Lock-step)

The project uses a **Lock-step Versioning** strategy where a single Git Tag (`vX.Y.Z`) triggers a unified release for both the Server (Docker) and the Companion App (Windows Installer).

1. **Update Version**: Update `docs/VERSION` to the new version (e.g., `0.6.0`).
2. **Commit and Tag**:
   - Commit the change.
   - Create a tag matching the version: `git tag v0.6.0`
   - Push the tag: `git push origin v0.6.0`

3. **CI/CD Pipeline** (`.github/workflows/release.yml`):
  **Trigger**: The push of the `v*` tag automatically triggers the pipeline.

  **Step 1: Docker Build**: Builds and pushes API, Worker, and Frontend images to GHCR with tags `latest` and `v0.6.0`. The API image also embeds the resolved server version for runtime display in Settings.

  **Step 2: Companion Build**: The CI pipeline automatically syncs the version from the Git Tag to all companion app files (`package.json`, `Cargo.toml`, `tauri.conf.json`). **Manual version updates in these files are NOT required.** It then compiles the Windows installer (`.exe`) and Portable build, and uploads those artifacts to the GitHub Release created by the tag.

**Important**:

- **Versioning**: Strict Semantic Versioning (`vX.Y.Z`).
- **Source of Truth**: The Git Tag is the single source of truth for published releases. Local source builds use `docs/VERSION`. The API image embeds the resolved server version at build time, and the companion app files are transiently updated during the build process.
  - **Version Detection**: The API resolves the running version from build metadata embedded into the image (`NOJOIN_SERVER_VERSION` and `/app/.build-version`), falling back to bundled or local `docs/VERSION` in development and test contexts. User-facing release metadata is resolved from GitHub Releases first, with GHCR tags and the GitHub raw `docs/VERSION` file only used as version fallbacks if release metadata is unavailable.
- **Platform**: Only Windows builds are currently supported for the Companion App.

## Code Style & Conventions

### Python (Backend)

- **Type Hints**: Mandatory for all function arguments and return values.
- **Imports**: Group standard lib, third-party, and local imports.
- **Error Handling**: Use `HTTPException` in API endpoints.

### TypeScript (Frontend)

- **Interfaces**: Define shared types in `src/types/index.ts`.
- **Strict Mode**: No `any`.

### Rust (Companion)

- **Error Handling**: Use `anyhow::Result` for application code.
- **Async**: Use `tokio` for I/O bound tasks.

## Quality Assurance & Build Safety

- **Frontend Verification:**
  - **Rule:** After ANY change to frontend code (`frontend/src/**/*`), a build check MUST be run to catch type errors that dev mode misses.
  - **Command:** `cd frontend && npm run build`.
  - **Why:** Next.js dev mode is lenient; production builds are strict.
- **Type Safety:**
  - **Rule:** When adding new data fields (e.g., to Settings or Models), update the TypeScript interfaces in `frontend/src/types/index.ts` **FIRST**.
  - **Rule:** Do not use `any` unless absolutely necessary to bypass library bugs.
- **Import Verification:**
  - **Rule:** When refactoring or moving code, verify that all imports in dependent files are updated. Use `grep_search` to find usages of moved symbols.
- **Client-Side Safety:**
  - **Rule:** Never assume `API_BASE_URL` is absolute. Always use safe URL construction (e.g., `new URL(path, window.location.origin)`) or manually check and prepend origin to handle both relative (production) and absolute (dev) paths.

## Related Docs

- [USAGE.md](USAGE.md): End-user workflows and UI behavior.
- [ADMIN.md](ADMIN.md): Roles, invitations, password rotation, and admin operations.
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md): Backup contents, restore behavior, and sensitivity model.
- [PRD.md](PRD.md): Product intent and longer-term scope.
- [README.md](README.md): Documentation index by task.

## Working Style

- Prefer small, targeted changes that match existing patterns in the touched area.
- Link to the relevant docs instead of copying large procedural sections into new files.
- If a task touches auth, calendar, processing, or release behavior, read the relevant doc before editing.

## Agent Interaction Rules

### The Workflow Loop

1. **REQUIREMENT**: The user states a feature. You then read the AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md files for context.
2. **PLANNING**: Produce a detailed plan. Consider signal propagation and dependencies.
3. **APPROVAL**: Wait for user confirmation.
4. **IMPLEMENTATION**: Generate robust code. Do not delete existing functionality unless planned.
5. **UI DUPLICATION**: When modifying the **Context Menu** for recordings, remember that there are TWO places to update:
   - `frontend/src/components/RecordingCard.tsx`: The main grid view.
   - `frontend/src/components/Sidebar.tsx`: The sidebar list view.
   - **Failure to update both will result in inconsistent behavior.**
6. **TESTING**: The user performs manual testing but the agent should run the relevant host-side build checks and rebuild any locally customised container services when needed to catch build-time errors and ensure changes are reflected in the environment. The agent should also provide detailed instructions for testing the new feature, including any necessary setup steps, expected outcomes, and edge cases to consider.
7. **COMPLETION**: Update the docs as needed.

### Constraints

- **NO GIT COMMANDS**: Never push/pull automatically. Provide text for messages.
- **NO EMOJIS**: Keep output strict and professional.
- **TONE**: Objective, results-oriented. No fluff.
- **COMMENTS**: Add comments to code where the code is non-obvious. Comments should be brief, professional, and to the point with absolutely zero 'developer thought' or 'developer intent' or 'developer reasoning' style comments.
