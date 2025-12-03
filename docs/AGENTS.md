# Nojoin AI Agent Instructions

## Project Context
Nojoin is a distributed meeting intelligence platform. It records system audio via a local Rust companion app, processes it on a GPU-enabled Docker backend, and presents insights via a Next.js web interface.

**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Privacy First (Self-hosted).

## Architecture & Patterns

### Backend (FastAPI + Celery)
- **Service Boundary**: `backend/` handles API requests and offloads heavy processing to Celery workers via Redis.
  - **Rule**: API endpoints must NEVER run heavy inference (Whisper, Pyannote, LLMs) synchronously. Always dispatch to Celery.
- **Data Access**: Use `SQLModel` for ORM. Models are in `backend/models/`.
- **Dependency Injection**: ALWAYS use `backend.api.deps` for DB sessions (`SessionDep`) and current user (`CurrentUser`).
- **Heavy Processing**:
  - **Location**: `backend/worker/tasks.py`.
  - **Constraint**: Import heavy libraries (torch, whisper, pyannote) **inside** the task function to keep the API lightweight and fast-starting.
  - **Pipeline**: VAD (Silero) -> Transcribe (Whisper) -> Diarize (Pyannote) -> Alignment.
- **Configuration**: Use `backend.utils.config_manager` to handle system and user-specific settings.

### Frontend (Next.js + Zustand)
- **State Management**: Use **Zustand** (`src/lib/store.ts`) for global UI state (navigation, selection, filters). Avoid prop drilling.
- **API Layer**: All API calls MUST go through `src/lib/api.ts`. This handles JWT auth and interceptors.
- **Routing**: App Router (`src/app/`).
- **Styling**: Tailwind CSS.
- **Components**: Prefer functional components in `src/components/`.

### Companion App (Tauri + Rust)
- **Structure**:
  - `src-tauri/`: Rust backend code.
  - `src/`: Frontend assets (currently minimal).
- **Concurrency**:
  - **Audio Thread**: Captures audio using `cpal`. Communicates via `crossbeam_channel`.
  - **Server/Upload Thread**: Uses `tokio` runtime.
- **Upload Strategy**:
  - Segments are uploaded sequentially to `/recordings/{id}/segment`.
  - **Retries**: Implemented in `src-tauri/src/uploader.rs` with exponential backoff.
- **UI**: System tray managed by Tauri.
- **Configuration** (`config.json`):
  - `api_host`: Backend API hostname/IP (default: "localhost").
  - `api_port`: Backend API port (default: 14443).
  - `local_port`: Local server port (default: 12345).
  - `api_token`: JWT token obtained via web-based authorization.
- **Authorization**: 
  - Web app sends token + current host/port to `/auth` endpoint.
  - App automatically updates config and connects.
  - Manual configuration available via System Tray > Settings.
- **Installer**: Built via Tauri Bundler. Installs to `%LOCALAPPDATA%\Nojoin` on Windows.

## Critical Workflows

### Hybrid Development (WSL2 + Windows)
- **Backend/Frontend**: Run in WSL2/Linux (Docker).
- **Companion**: Run in Windows (Native) to access WASAPI loopback.

### Commands
- **Start Infrastructure**:
  - **NVIDIA GPU (Default)**: `docker-compose up -d`
  - **CPU**: `docker compose -f docker-compose.cpu.yml up -d`
- **Migrations**:
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
- **Companion (Windows)**:
  - Development: `cd companion && npm run tauri dev`
  - Release Build: `cd companion && npm run tauri build`
  - **Note**: When building from WSL2, copy the project to a Windows directory first to avoid UNC path issues with `npm`.
  - **Environment Variables**: Ensure `TAURI_PRIVATE_KEY` and `TAURI_KEY_PASSWORD` (if applicable) are set in your Windows environment variables or PowerShell session before building.
- **Companion Installer (Windows)**:
  - Build: `cd companion && npm run tauri build`
  - Output: `companion/src-tauri/target/release/bundle/nsis/Nojoin-Companion-Setup.exe`

### Companion Release Workflow

**The companion app uses a separate tag pattern** (`companion-v*`) to avoid rebuilds on every main app release.

1. **Update Version Numbers** (both files must match):
   - `companion/package.json`: `"version": "X.Y.Z"`
   - `companion/src-tauri/tauri.conf.json`: `"version": "X.Y.Z"`
   - `companion/src-tauri/Cargo.toml`: `version = "X.Y.Z"`
2. **Commit and Push**: Push changes to `main` branch.
3. **Create Companion Tag**: Use `companion-v` prefix:
   ```bash
   git tag companion-v0.2.0
   git push origin companion-v0.2.0
   ```
   *Note: Creating a tag locally via GUI does not automatically push it. You must explicitly push the tag to trigger the workflow.*
4. **Create GitHub Release**: Create a release for the `companion-v*` tag on GitHub.
5. **CI/CD Builds Automatically**: GitHub Actions builds all platform installers:
   - Windows: Tauri NSIS installer (`.exe`)
   - macOS: Tauri DMG (`.dmg`)
   - Linux: Tauri DEB (`.deb`)
6. **Artifacts Uploaded**: All installers attached to the GitHub Release automatically.

**Important**: Regular `v*` tags do NOT trigger companion builds. Only `companion-v*` tags do.

**Manual CI Trigger**: Run "Build Companion Installers" from GitHub Actions > "Run workflow" for testing.

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

## Agent Interaction Rules

### The Workflow Loop
1.  **REQUIREMENT**: User states a feature.
2.  **PLANNING**: Produce a detailed plan. Consider signal propagation and dependencies.
3.  **APPROVAL**: Wait for user confirmation.
4.  **IMPLEMENTATION**: Generate robust code. Do not delete existing functionality unless planned.
5.  **TESTING**: User performs manual testing.
6.  **COMPLETION**: Update PRD if needed.

### Constraints
- **NO GIT COMMANDS**: Never push/pull automatically. Provide text for messages.
- **NO EMOJIS**: Keep output strict and professional.
- **TONE**: Objective, results-oriented. No fluff.
