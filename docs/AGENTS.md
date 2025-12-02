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

### Companion App (Rust)
- **Concurrency**:
  - **Audio Thread**: Captures audio using `cpal`. Communicates via `crossbeam_channel`.
  - **Server/Upload Thread**: Uses `tokio` runtime.
- **Upload Strategy**:
  - Segments are uploaded sequentially to `/recordings/{id}/segment`.
  - **Retries**: Implemented in `src/uploader.rs` with exponential backoff.
- **UI**: System tray only (`tray-icon`, `tao`).
- **Configuration** (`config.json`):
  - `api_port`: Backend API port (default: 14443). Hostname is always `localhost`.
  - `local_port`: Local server port (default: 12345).
  - `api_token`: JWT token obtained via web-based authorization.
- **Authorization**: Web app sends token to `/auth` endpoint. No manual config needed.
- **Installer**: NSIS-based (`companion/installer/`). Installs to `%LOCALAPPDATA%\Nojoin`.

## Critical Workflows

### Hybrid Development (WSL2 + Windows)
- **Backend/Frontend**: Run in WSL2/Linux (Docker).
- **Companion**: Run in Windows (Native) to access WASAPI loopback.

### Commands
- **Start Infrastructure**: `docker-compose up -d`
- **Migrations**:
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
- **Companion (Windows)**:
  - Development: `cd companion && cargo run`
  - Release Build: `cd companion && cargo build --release`
- **Companion Installer (Windows)**:
  - Requires: NSIS installed (`choco install nsis` or https://nsis.sourceforge.io)
  - Build: `cd companion && .\installer\build.ps1 -Release`
  - Output: `companion/dist/Nojoin-Companion-Setup.exe`

### Companion Release Workflow

**The companion app uses a separate tag pattern** (`companion-v*`) to avoid rebuilds on every main app release.

1. **Update Version Numbers** (both files must match):
   - `companion/Cargo.toml`: `version = "X.Y.Z"`
   - `companion/installer/installer.nsi`: `!define PRODUCT_VERSION "X.Y.Z"`
2. **Commit and Push**: Push changes to `main` branch.
3. **Create Companion Tag**: Use `companion-v` prefix:
   ```bash
   git tag companion-v0.2.0
   git push origin companion-v0.2.0
   ```
4. **Create GitHub Release**: Create a release for the `companion-v*` tag on GitHub.
5. **CI/CD Builds Automatically**: GitHub Actions builds all platform installers:
   - Windows: NSIS installer (`Nojoin-Companion-Setup.exe`)
   - macOS: Universal DMG (`Nojoin-Companion-Setup.dmg`)
   - Linux: DEB package (`Nojoin-Companion-Setup.deb`)
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
