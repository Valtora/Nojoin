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
  - **Pipeline**: VAD (Silero) -> Transcribe (Whisper) -> Diarize (Pyannote) -> Alignment.
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

### Companion Release Workflow

**The companion app uses the standard tag pattern** (`v*`) to align with the main app release.

1. **Update Version Numbers** (all three files must match):
   - `companion/package.json`: `"version": "X.Y.Z"`
   - `companion/src-tauri/tauri.conf.json`: `"version": "X.Y.Z"`
   - `companion/src-tauri/Cargo.toml`: `version = "X.Y.Z"`
2. **Commit and Push**: Push changes to the `main` branch.

3. **Create Tag**: Use the `v` prefix:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

   *Note: Creating a tag locally via GUI does not automatically push it. The tag must be explicitly pushed to trigger the workflow.*

4. **Create GitHub Release**: Create a release for the `v*` tag on GitHub.

5. **Trigger CI/CD Manually**: Navigate to GitHub Actions > "Companion App Build & Release" > "Run workflow". Select the branch or tag to build.
   - This builds the Windows installer (`.exe`).

6. **Artifacts Uploaded**: The Windows installer is attached to the GitHub Release automatically.

**Important**:

- **Versioning**: Strict 3-component Semantic Versioning (`X.Y.Z`, e.g., `0.1.6`) must be used. 4-component versions (`0.1.6.1`) are **NOT** supported by Tauri or Windows installers.
- **Triggers**: Tags do NOT trigger companion builds automatically. The workflow must be triggered manually.
- **Platform**: Only Windows builds are currently supported. macOS and Linux builds have been removed pending community contributions.

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

## Agent Interaction Rules

### The Workflow Loop

1. **REQUIREMENT**: The user states a feature. You then read the AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md files for context.
2. **PLANNING**: Produce a detailed plan. Consider signal propagation and dependencies.
3. **APPROVAL**: Wait for user confirmation.
4. **IMPLEMENTATION**: Generate robust code. Do not delete existing functionality unless planned.
5. **TESTING**: The user performs manual testing.
6. **COMPLETION**: Update the docs as needed.

### Constraints

- **NO GIT COMMANDS**: Never push/pull automatically. Provide text for messages.
- **NO EMOJIS**: Keep output strict and professional.
- **TONE**: Objective, results-oriented. No fluff.
