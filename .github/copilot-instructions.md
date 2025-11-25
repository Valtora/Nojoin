# Nojoin AI Agent Instructions

## 🧠 Project Overview
Nojoin is a distributed meeting intelligence platform. It records system audio via a local Rust companion app, processes it on a GPU-enabled Docker backend (Whisper/Pyannote), and presents insights via a Next.js web interface.

## 🏗️ Architecture & Data Flow
- **Backend (`backend/`)**: FastAPI service + Celery worker.
  - **API**: Handles metadata, user auth, and serves audio.
  - **Worker**: Performs VAD, Transcription (Whisper), and Diarization (Pyannote).
  - **DB**: PostgreSQL (SQLModel).
  - **Broker**: Redis.
- **Frontend (`frontend/`)**: Next.js (App Router) + Tailwind CSS.
- **Companion (`companion/`)**: Rust system tray app. Captures audio (cpal) and uploads to backend.
- **Infrastructure**: Docker Compose orchestrates all services.

## 💻 Development Workflow
- **Start Stack**: `docker-compose up -d` (starts DB, Redis, API, Worker, Frontend).
- **Backend Dev**:
  - Run locally: `uvicorn backend.main:app --reload` (requires local DB/Redis).
  - Worker: `celery -A backend.celery_app.celery_app worker --pool=solo` (Windows) or `prefork` (Linux).
- **Frontend Dev**: `cd frontend && npm run dev`.
- **Companion Dev**: `cd companion && cargo run`.

## 🐍 Backend (Python/FastAPI)
- **Models**: Defined in `backend/models/`. Use `SQLModel`.
- **Migrations**: Currently using `SQLModel.metadata.create_all` in `lifespan` (no Alembic yet).
- **Tasks**: Heavy processing logic is in `backend/worker/tasks.py`.
  - **Pipeline**: VAD -> Preprocess (16k mono) -> Whisper -> Pyannote -> Alignment.
- **Dependency Injection**: Use `backend.api.deps` for DB sessions and current user.
- **Pattern**: Service layer pattern is partially implemented; complex logic often resides in `backend/processing/`.

## ⚛️ Frontend (Next.js/TypeScript)
- **API Client**: `src/lib/api.ts` uses Axios with interceptors for JWT auth.
- **State**: React Query (implied) or local state.
- **Components**: `src/components/` contains functional components.
- **Routing**: App Router (`src/app/`).
- **Styling**: Tailwind CSS.

## 🦀 Companion App (Rust)
- **Concurrency**: Uses `crossbeam_channel` for audio thread <-> main thread communication.
- **Tray**: `tray-icon` and `tao` for system tray management.
- **Audio**: `cpal` for capture.
- **Server**: Local HTTP server (Tokio) to receive commands from the Frontend.

## ⚠️ Critical Implementation Details
- **GPU Support**: The worker container requires NVIDIA Container Toolkit.
- **Audio Format**: Internal processing standard is **16kHz Mono WAV**.
- **Auth**: JWT-based. Default admin user created on startup.
- **File Paths**: Docker volumes map `./data` to `/app/data`. Ensure paths are consistent across host/container.

## 🧪 Testing
- **Backend**: `pytest` (if available).
- **Frontend**: Manual testing via browser.
- **Companion**: Manual testing via system tray and audio capture verification.
