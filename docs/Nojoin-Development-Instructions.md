# Nojoin Development Instructions

This guide covers the complete setup and workflow for developing Nojoin. It is designed for a **Hybrid Environment** (WSL2 for the "Brain", Windows for the "Ears").

---

## 1. Prerequisites & Hardware
*   **OS**: Windows 11 with WSL2 (Ubuntu 24.04 recommended).
*   **GPU**: NVIDIA RTX Series (Required for local Whisper/Pyannote).
*   **Tools**:
    *   Docker Desktop (WSL2 Backend enabled).
    *   Python 3.10+.
    *   Node.js 18+ (LTS).
    *   Rust (latest stable).
    *   NVIDIA CUDA Toolkit 12.x.

---

## 2. Environment Setup

### 2.1 Infrastructure (Docker)
We run the database (PostgreSQL) and broker (Redis) in Docker containers. The application code runs locally for hot-reloading.

```bash
# Start DB and Redis
docker-compose up -d db redis
```

### 2.2 Backend (Python/FastAPI)
Runs in WSL2 to leverage Linux-optimized AI libraries and Docker integration.

**Setup:**
```bash
# 1. Create & Activate Virtual Environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3. Install PyTorch & CUDA (Critical for GPU)
# Note: We use the cu126 index for compatibility with CUDA 12.x
pip install torch torchvision torchaudio torchcodec openai-whisper pyannote.audio --index-url https://download.pytorch.org/whl/cu126

# 4. Install System Dependencies
sudo apt-get update && sudo apt-get install -y ffmpeg
```

### 2.3 Frontend (Next.js)
Runs in WSL2.

**Setup:**
```bash
cd frontend
npm install
```

### 2.4 Companion App (Rust)
Runs in **Windows PowerShell** to access WASAPI (Loopback Audio).

**Setup:**
1.  Install Rust on Windows: [rustup.rs](https://rustup.rs/)
2.  Ensure `cpal` dependencies are met (usually standard on Windows).

---

## 3. Development Workflow (The "Daily Drive")

To start a full development session, you will need **3 Terminals in WSL2** and **1 Terminal in Windows**.

### Terminal 1: API (WSL2)
Serves the REST API and manages the database connection.
```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0
```
*   **Health Check**: `http://localhost:8000/health`
*   **Docs**: `http://localhost:8000/docs`

### Terminal 2: Worker (WSL2)
Handles heavy AI tasks (Whisper, Pyannote).
```bash
source .venv/bin/activate
# 'solo' pool is often more stable for GPU tasks in dev
celery -A backend.celery_app.celery_app worker --pool=solo --loglevel=info
```

### Terminal 3: Frontend (WSL2)
The web interface.
```bash
cd frontend
npm run dev
```
*   **URL**: `http://localhost:14141`

### Terminal 4: Companion (Windows PowerShell)
Captures audio and sends it to the API.
**CRITICAL**: Must run in Windows, not WSL.
```powershell
cd companion
$env:CARGO_INCREMENTAL=0 # Optional: Fixes some file locking issues
cargo run
```
*   **Status**: Check the System Tray icon.

---

## 4. Component Guides

### 4.1 Database Management (Alembic)
We use Alembic for migrations. Tables are **not** auto-created.

**Common Commands:**
```bash
# Apply all pending migrations (Run this on fresh setup)
alembic upgrade head

# Create a new migration (After modifying models in backend/models/)
alembic revision --autogenerate -m "Description of change"
```

**Troubleshooting**:
*   If you see `relation "users" does not exist`, run `alembic upgrade head`.
*   To reset the DB: `docker-compose down -v`, then `up -d`, then `alembic upgrade head`, then `python -m backend.create_first_user`.

### 4.2 Frontend Development
*   **State Management**: Zustand (`src/lib/store.ts`, `src/lib/notificationStore.ts`).
*   **Styling**: Tailwind CSS.
*   **Best Practices**:
    *   **SSR Safety**: Wrap browser-only logic (like `window` access) in `useEffect` or `mounted` checks.
    *   **Package Install**: Always run `npm install` inside the `frontend/` directory.

### 4.3 Documentation & Changelog
We maintain a changelog to track the project's history. We follow a "Release on Push" workflow.

**Workflow:**
1.  **Development**: Implement features and fixes. Add entries to `docs/CHANGELOG.md` under `[Unreleased]` as you go.
2.  **Completion**: Once the work is completed for a session and ready to push:
    *   Rename the `[Unreleased]` header to the new version number and date (e.g., `## [0.0.8.4] - 2025-11-27`).
    *   Create a new empty `## [Unreleased]` section at the top.
    *   Follow the [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
3.  **Commit**: Include the updated `docs/CHANGELOG.md` in the git commit.

---

## 5. Troubleshooting

### Audio Not Detected
*   Ensure Companion App is running in **Windows**.
*   Check `http://localhost:12345/status`.
*   Verify "Stereo Mix" or Loopback device is active in Windows Sound Settings.

### GPU Not Used
*   Run `nvidia-smi` in WSL2 to verify visibility.
*   Ensure `torch.cuda.is_available()` returns `True` in the backend.

