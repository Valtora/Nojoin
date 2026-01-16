# Nojoin AI Agent Instructions

## Project Context

Nojoin is a distributed meeting intelligence platform. The system records system audio via a local Rust companion app, processes the data on a GPU-enabled Docker backend, and presents insights via a Next.js web interface.

**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Privacy First (Self-hosted).

## Architecture & Patterns

### Backend (FastAPI + Celery)

- **Service Boundary**: The `backend/` directory handles API requests and offloads heavy processing to Celery workers via Redis.
  - **Rule**: API endpoints must NEVER run heavy inference (Whisper, Pyannote, LLMs) synchronously. Heavy tasks must be dispatched to Celery.
- **Data Access**: `SQLModel` is used for ORM. Models are located in `backend/models/`.
- **Dependency Injection**: `backend.api.deps` must be used for DB sessions (`SessionDep`) and current user (`CurrentUser`).
- **Heavy Processing**:
  - **Location**: `backend/worker/tasks.py`.
  - **Constraint**: Heavy libraries (torch, whisper, pyannote) must be imported **inside** the task function to keep the API lightweight and ensure fast startup times.
  - **Pipeline**: Validation -> VAD (Silero) -> Proxy Creation (Alignment) -> Transcribe (Whisper) -> Diarize (Pyannote) -> Merge -> Speaker Resolution (Manual/Merge Check -> LLM) -> Voiceprint Extraction -> Title Inference -> Notes Generation.
  - **PyTorch 2.6+ & Safe Globals**: The project uses PyTorch 2.6+, which defaults `weights_only=True` in `torch.load` for security.
    - **Issue**: This blocks loading of custom classes (like `pyannote.audio.core.task.Specifications` and `torch.torch_version.TorchVersion`) from model checkpoints.
    - **Solution**: These classes must be explicitly added to the safe globals list using `torch.serialization.add_safe_globals([...])` **before** loading the model. This is handled at the module level in `embedding_core.py` and `diarize.py`.
- **Configuration**: `backend.utils.config_manager` is used to handle system and user-specific settings.

### Frontend (Next.js + Zustand)

- **State Management**: **Zustand** (`src/lib/store.ts`) is used for global UI state (navigation, selection, filters). Prop drilling should be avoided.
- **API Layer**: All API calls MUST go through `src/lib/api.ts`. This module handles JWT authentication and interceptors.
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
  - Segments are uploaded sequentially to `/recordings/{id}/segment`.
  - **Retries**: Implemented in `src-tauri/src/uploader.rs` with exponential backoff.
- **UI**: The system tray is managed by Tauri.
- **Configuration** (`config.json`):
  - `api_host`: Backend API hostname/IP (default: "localhost").
  - `api_port`: Backend API port (default: 14443).
  - `local_port`: Local server port (default: 12345).
  - `api_token`: JWT token obtained via web-based authorization.
- **Authorization**:
  - The web app sends the token and current host/port to the `/auth` endpoint.
  - The app automatically updates the configuration and connects.
  - Manual configuration is available via System Tray > Settings.
- **Installer**: Built via Tauri Bundler for Windows. Installs to `%LOCALAPPDATA%\Nojoin`.

## Critical Workflows

### Commands

- **Start Infrastructure**:
  - **NVIDIA GPU (Default)**: `docker-compose up -d`
  - **CPU**: `docker compose up -d` (Ensure the `deploy` section in `docker-compose.yml` is commented out)
  - **Remote Access**: Ensure `.env` is configured with `NEXT_PUBLIC_API_URL` (including `/api` suffix) and `ALLOWED_ORIGINS`.
- **Migrations**:
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
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
   - **Trigger**: The push of the `v*` tag automatically triggers the pipeline.
   - **Step 1: Docker Build**: Builds and pushes API, Worker, and Frontend images to GHCR with tags `latest` and `v0.6.0`.
   - **Step 2: Companion Build**:
     - **Auto-Sync**: The CI pipeline automatically syncs the version from the Git Tag to all companion app files (`package.json`, `Cargo.toml`, `tauri.conf.json`). **Manual version updates in these files are NOT required.**
     - **Build**: Compiles the Windows installer (`.exe`) and Portable build.
     - **Release**: Uploads these artifacts to the GitHub Release created by the tag.

**Important**:

- **Versioning**: Strict Semantic Versioning (`vX.Y.Z`).
- **Source of Truth**: The Git Tag (and `docs/VERSION`) is the single source of truth. The companion app files are transiently updated during the build process.
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
  - **Command:** `docker compose build frontend` OR if working locally `cd frontend && npm run build`.
  - **Why:** Next.js dev mode is lenient; production builds are strict.
- **Type Safety:**
  - **Rule:** When adding new data fields (e.g., to Settings or Models), update the TypeScript interfaces in `frontend/src/types/index.ts` **FIRST**.
  - **Rule:** Do not use `any` unless absolutely necessary to bypass library bugs.
- **Import Verification:**
  - **Rule:** When refactoring or moving code, verify that all imports in dependent files are updated. Use `grep_search` to find usages of moved symbols.
- **Client-Side Safety:**
  - **Rule:** Never assume `API_BASE_URL` is absolute. Always use safe URL construction (e.g., `new URL(path, window.location.origin)`) or manually check and prepend origin to handle both relative (production) and absolute (dev) paths.

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
6. **TESTING**: The user performs manual testing.
7. **COMPLETION**: Update the docs as needed.

### Constraints

- **NO GIT COMMANDS**: Never push/pull automatically. Provide text for messages.
- **NO EMOJIS**: Keep output strict and professional.
- **TONE**: Objective, results-oriented. No fluff.
- **COMMENTS**: Add comments to code where the code is non-obvious. Comments should be brief, professional, and to the point with absolutely zero 'developer thought' or 'developer intent' or 'developer reasoning' style comments.
