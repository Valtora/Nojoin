# Nojoin AI Agent Instructions

## Project Context
Nojoin is a distributed meeting intelligence platform. It records system audio via a local Rust companion app (Windows), processes it on a GPU-enabled Docker backend (WSL2/Linux), and presents insights via a Next.js web interface.

**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Privacy First (Self-hosted).

## Repository Structure

```
/
├── backend/                 # FastAPI + Celery Python backend
│   ├── api/                # REST API endpoints
│   │   ├── deps.py        # Dependency injection (SessionDep, CurrentUser)
│   │   └── routes/        # Route handlers by domain
│   ├── models/            # SQLModel ORM models
│   ├── worker/            # Celery tasks for heavy processing
│   ├── processing/        # Audio processing utilities
│   ├── utils/             # Shared utilities (config_manager, etc.)
│   └── alembic/           # Database migrations
├── frontend/               # Next.js web application
│   └── src/
│       ├── app/           # App Router pages
│       ├── components/    # React components
│       ├── lib/           # Utilities (api.ts, store.ts)
│       └── types/         # TypeScript interfaces
├── companion/              # Rust system tray application
│   └── src/
│       ├── main.rs        # Application entry point
│       └── uploader.rs    # Audio upload with retry logic
├── docker/                 # Docker build contexts
├── docs/                   # Documentation (PRD, SECURITY, TODO)
└── nginx/                  # Reverse proxy configuration
```

## Build, Test, and Lint Commands

### Frontend (Next.js)
```bash
cd frontend
npm install          # Install dependencies
npm run dev          # Development server (port 14141)
npm run build        # Production build
npm run lint         # ESLint check
```

### Backend (Python/FastAPI)
```bash
# Run inside Docker or with virtual environment
pip install -r requirements.txt
uvicorn backend.main:app --reload     # Development server (port 8000)
```

### Companion (Rust)
```bash
cd companion
cargo build          # Debug build
cargo build --release  # Release build
cargo run            # Run development build
cargo clippy         # Lint check
cargo fmt            # Format code
```

### Infrastructure
```bash
docker-compose up -d              # Start all services
docker-compose logs -f api        # View API logs
docker-compose logs -f worker     # View worker logs
docker-compose down               # Stop all services
```

### Database Migrations
```bash
alembic upgrade head              # Apply migrations
alembic downgrade -1              # Rollback one migration
alembic revision --autogenerate -m "description"  # Create migration
```

## Architecture & Patterns

### Backend (FastAPI + Celery)
- **Service Boundary**: `backend/` handles API requests and offloads heavy processing to Celery workers via Redis.
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

## Code Style & Conventions

### Python (Backend)
- **Type Hints**: Mandatory for all function arguments and return values.
- **Imports**: Group standard lib, third-party, and local imports (in that order).
- **Error Handling**: Use `HTTPException` in API endpoints.
- **Naming**: snake_case for functions/variables, PascalCase for classes.

### TypeScript (Frontend)
- **Interfaces**: Define shared types in `src/types/index.ts`.
- **Strict Mode**: Avoid `any` types.
- **Components**: Use functional components with hooks.
- **Naming**: camelCase for functions/variables, PascalCase for components/types.

### Rust (Companion)
- **Error Handling**: Use `anyhow::Result` for application code.
- **Async**: Use `tokio` for I/O bound tasks.
- **Naming**: snake_case for functions/variables, PascalCase for types/structs.

## Security Considerations
- **Authentication**: JWT-based auth handled in `backend/api/deps.py`.
- **Secrets**: Never commit secrets. Use environment variables and `.env` files.
- **Input Validation**: Validate all user inputs in API endpoints.
- **File Uploads**: Audio files are validated before processing.
- **Self-hosted**: All data stays on user's infrastructure.

## Development Workflow

### Environment Setup
1. Copy `.env.example` to `.env` and configure
2. Start infrastructure: `docker-compose up -d`
3. Access web UI at `http://localhost:14141`
4. Access API docs at `http://localhost:8000/docs`

### Making Changes
1. **Backend API**: Add routes in `backend/api/routes/`, models in `backend/models/`
2. **Frontend**: Add pages in `frontend/src/app/`, components in `frontend/src/components/`
3. **Database**: Create migrations with Alembic after model changes
4. **Companion**: Changes require rebuilding the Rust binary

### Hybrid Development (WSL2 + Windows)
- **Backend/Frontend**: Run in WSL2/Linux (Docker).
- **Companion**: Run in Windows (Native) to access WASAPI loopback.

## Agent Guidelines

### When Adding Features
1. Check existing patterns in similar code
2. Follow the established directory structure
3. Use dependency injection for database access
4. Add proper type hints/annotations
5. Handle errors appropriately for the component

### When Fixing Bugs
1. Understand the data flow through the system
2. Check both API and worker logs for errors
3. Consider the async nature of processing tasks

### File Placement
- **New API endpoint**: `backend/api/routes/<domain>.py`
- **New model**: `backend/models/<domain>.py`
- **New React component**: `frontend/src/components/<ComponentName>.tsx`
- **New page**: `frontend/src/app/<route>/page.tsx`
- **New TypeScript type**: `frontend/src/types/index.ts`
- **API integration**: `frontend/src/lib/api.ts`
