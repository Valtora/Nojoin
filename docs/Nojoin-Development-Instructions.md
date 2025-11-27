# Nojoin Development Instructions

This guide covers the complete setup and workflow for developing Nojoin in a **Hybrid Environment** (WSL2 for the "Brain", Windows for the "Ears").

## 1. Project Setup

### 1.1 Infrastructure (Docker)
Start the database and broker services.
```bash
docker-compose up -d db redis
```

### 1.2 Backend (WSL2)
Set up the Python environment and database.
```bash
# Setup Virtual Environment
python3 -m venv .venv
source .venv/bin/activate

# Install Dependencies
pip install -r requirements.txt
sudo apt-get install -y ffmpeg

# Run Migrations
alembic upgrade head
```

### 1.3 Frontend (WSL2)
Install Node.js dependencies.
```bash
cd frontend
npm install
```

### 1.4 Companion App (Windows)
Build the Rust client.
```powershell
cd companion
cargo build
```

## 2. First Run (Setup Wizard)
Once the services are running (see Section 3), you must initialize the system:
1.  Open `http://localhost:14141` in your browser.
2.  You will be redirected to the **Setup Wizard** (`/setup`).
3.  Create your initial Admin account.

## 3. Development Workflow (The "Daily Drive")
You need **3 Terminals in WSL2** and **1 Terminal in Windows**.

### Terminal 1: API (WSL2)
```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0
```
*   **Health**: `http://localhost:8000/health`
*   **Docs**: `http://localhost:8000/docs`

### Terminal 2: Worker (WSL2)
```bash
source .venv/bin/activate
celery -A backend.celery_app.celery_app worker --pool=solo --loglevel=info
```

### Terminal 3: Frontend (WSL2)
```bash
cd frontend
npm run dev
```
*   **App**: `http://localhost:14141`

### Terminal 4: Companion (Windows PowerShell)
**CRITICAL**: Must run in Windows to access system audio.
```powershell
cd companion
$env:CARGO_INCREMENTAL=0
cargo run
```

## 4. Component Guides

### 4.1 Database (Alembic)
*   **Apply Migrations**: `alembic upgrade head`
*   **Create Migration**: `alembic revision --autogenerate -m "message"`

### 4.2 Frontend
*   **State**: Zustand (`src/lib/store.ts`)
*   **Styling**: Tailwind CSS
