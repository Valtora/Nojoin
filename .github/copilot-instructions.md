# Nojoin AI Agent Instructions

## 🔍 Project Overview
Nojoin is a distributed meeting intelligence platform with a **FastAPI backend**, **Next.js frontend**, and **Rust companion app**.
- **Core Philosophy:** Local-first processing (Whisper/Pyannote) on a GPU server, accessed via web.
- **Architecture:** Monorepo. Docker for infra (Postgres, Redis). Local processes for dev.

## 🏗️ Architecture & Patterns

### Backend (`backend/`)
- **Framework:** FastAPI with `SQLModel` (SQLAlchemy + Pydantic).
- **Async/Sync Split:**
  - **API Endpoints:** Use `async` functions and `AsyncSession` (`backend/api/deps.py`).
  - **Celery Tasks:** Use **synchronous** sessions via `DatabaseTask` base class (`backend/worker/tasks.py`).
- **Processing Pipeline:** `VAD -> Transcribe (Whisper) -> Diarize (Pyannote)`. Logic in `backend/processing/`.
- **Database:** PostgreSQL. Tables created on startup via `lifespan` in `main.py`.
- **Auth:** OAuth2 with JWT. First user created automatically on startup.

### Frontend (`frontend/`)
- **Framework:** Next.js (App Router) with TypeScript and Tailwind CSS.
- **API Client:** `axios` instance in `src/lib/api.ts`.
  - **Auth:** Interceptors inject `Bearer` token from `localStorage`.
  - **Base URL:** Currently hardcoded to `http://localhost:8000/api/v1`.
- **State:** React hooks. No global state library (Redux/Zustand) observed yet; relies on component state and API refetching.

### Companion App (`companion/`)
- **Language:** Rust.
- **Role:** System tray app for audio capture.
- **Communication:** Uploads audio segments to Backend API.

## 🛠️ Development Workflow
- **Primary Script:** `dev.ps1` (PowerShell).
  - Checks Docker.
  - Starts Infra (Postgres/Redis) via `docker-compose`.
  - Starts API, Worker, Frontend, and Companion in separate windows.
- **Ports:**
  - API: `8000`
  - Frontend: `14141`
  - Companion: `12345`
  - Postgres: `5432`
  - Redis: `6379`

## 🚨 Critical Guidelines
1.  **Testing:** The user performs manual testing. **Do not write automated tests** unless explicitly requested.
2.  **Database Changes:** When modifying models (`backend/models/`), ensure `SQLModel.metadata.create_all` in `main.py` will pick them up.
3.  **Celery Tasks:** Always inherit from `DatabaseTask` when needing DB access to ensure proper session cleanup.
4.  **Frontend API:** Update `src/types/index.ts` when backend models change.
5.  **Environment:** Assume Windows/PowerShell environment for all shell commands.

## 📂 Key Files
- `dev.ps1`: **READ THIS** to understand how the app starts.
- `backend/main.py`: App entry & lifespan logic.
- `backend/worker/tasks.py`: Background processing logic.
- `frontend/src/lib/api.ts`: Frontend API communication layer.
- `docs/PRD.md`: Detailed feature specifications.
