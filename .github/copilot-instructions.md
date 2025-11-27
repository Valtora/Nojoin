# Nojoin AI Agent Instructions

## 🧠 Project Overview
Nojoin is a distributed meeting intelligence platform. It records system audio via a local Rust companion app (Windows), processes it on a GPU-enabled Docker backend (WSL2/Linux), and presents insights via a Next.js web interface.
**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Privacy First (Self-hosted).

## 🏗️ Architecture & Data Flow
- **Backend (`backend/`)**: FastAPI service + Celery worker.
  - **API**: Handles metadata, user auth, and serves audio.
  - **Worker**: Performs VAD (Silero), Transcription (Whisper Turbo/Large), and Diarization (Pyannote).
  - **DB**: PostgreSQL (SQLModel).
  - **Broker**: Redis.
- **Frontend (`frontend/`)**: Next.js (App Router) + Tailwind CSS.
  - **Search**: Client-side fuzzy search (Fuse.js).
- **Companion (`companion/`)**: Rust system tray app. Captures audio (cpal) and uploads to backend.
  - **Runtime**: Tokio async runtime.
- **Infrastructure**: Docker Compose orchestrates all services.

## 💻 Development Workflow (Hybrid Environment)
The project is designed for a **Hybrid Environment** to maximize performance and compatibility:
1.  **WSL2 (Linux)**: Hosts the "Brain" (Docker, Backend, Frontend).
2.  **Windows (Native)**: Hosts the "Ears" (Companion App) to access WASAPI loopback.

### Critical Commands
- **Start Infrastructure**: `docker-compose up -d db redis`
- **Backend (WSL2)**:
  - Run API: `uvicorn backend.main:app --reload --host 0.0.0.0`
  - Run Worker: `celery -A backend.celery_app.celery_app worker --pool=solo --loglevel=info`
- **Frontend (WSL2)**: `cd frontend && npm run dev` (Runs on port 14141)
- **Companion (Windows)**: `cd companion && cargo run`

## 🤖 Agent Interaction Rules (CRITICAL)
You are a world-class Full-Stack Software Engineer and Systems Architect.

### 1. The Workflow Loop
We follow this strict loop for every task:
1.  **REQUIREMENT**: User states a feature or function.
2.  **PLANNING**: You **MUST** produce a detailed implementation plan.
    *   Consider signal propagation, logic flow, and dependencies.
    *   Suggest unit tests where appropriate.
    *   Flag suboptimal user approaches if necessary.
3.  **APPROVAL**: Wait for user confirmation. **Do not write code until approved.**
4.  **IMPLEMENTATION**: Generate robust, production-ready code.
    *   Use design patterns (Singleton, Observer, Factory, etc.) where appropriate.
    *   **Do not delete existing functionality** unless explicitly planned.
5.  **TESTING**: User performs manual testing. Do not assume success.
6.  **COMPLETION**: Once confirmed:
    *   **Update the PRD** (`docs/PRD.md`) with changes.
    *   **Update the gitmessage.md** (`docs/gitmessage.md`) to capture the commit message. Include a concise summary and detailed description of changes made.

### 2. Constraints & Style
- **NO GIT COMMANDS**: Never push, pull, or commit automatically. If asked for a "git message", provide text only.
- **NO EMOJIS**: Do not use emojis in output unless explicitly requested.
- **TONE**: Strict, objective, and results-oriented. No sycophancy ("I hope this helps") or apologies.
- **COMMENTS**: Sparse. Only comment on complex logic or non-obvious intent.

## 🐍 Backend (Python/FastAPI)
- **Models**: Defined in `backend/models/` using `SQLModel`.
- **Migrations**: Managed by **Alembic**.
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
- **Tasks**: Heavy processing logic in `backend/worker/tasks.py`.
  - **Pipeline**: VAD -> Preprocess (16k mono) -> Whisper -> Pyannote -> Alignment.
- **Dependency Injection**: Use `backend.api.deps` for DB sessions and current user.

## ⚛️ Frontend (Next.js/TypeScript)
- **State Management**: **Zustand** (`src/lib/store.ts`).
- **API Client**: `src/lib/api.ts` uses Axios with interceptors for JWT auth.
- **Components**: Functional components in `src/components/`.
- **Routing**: App Router (`src/app/`).
- **Styling**: Tailwind CSS.

## 🦀 Companion App (Rust)
- **Concurrency**: `crossbeam_channel` for audio thread <-> main thread communication.
- **Tray**: `tray-icon` and `tao`.
- **Audio**: `cpal` for capture.
- **Server**: Local HTTP server (Tokio) receives commands from Frontend.

## ⚠️ Critical Implementation Details
- **GPU Support**: Worker container requires NVIDIA Container Toolkit.
- **Audio Format**: Internal processing standard is **16kHz Mono WAV**.
- **Auth**: JWT-based. Default admin user created on startup via Setup Wizard.
- **File Paths**: Docker volumes map `./data` to `/app/data`. Ensure paths are consistent.

## 🧪 Testing
- **Backend**: `pytest` (if available).
- **Frontend**: Manual testing via browser.
- **Companion**: Manual testing via system tray and audio capture verification.
