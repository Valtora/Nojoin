# Nojoin Development Instructions

This guide covers the complete setup and workflow for developing Nojoin in a **Hybrid Environment** (WSL2 for the "Brain", Windows for the "Ears").

## 1. Project Setup

### 1.1 Infrastructure (Docker)
Start the entire stack (Database, Redis, API, Worker, Frontend, Nginx).
```bash
docker-compose up -d --build
```

### 1.2 Companion App (Windows)
Build the Rust client.
```powershell
cd companion
cargo build
```

## 2. First Run (Setup Wizard)
Once the services are running, you must initialize the system:
1.  Open `https://localhost:14443` in your browser.
    *   **Note:** You will see a security warning because we are using a self-signed certificate for development. Please accept/proceed (e.g., "Advanced" -> "Proceed to localhost").
2.  You will be redirected to the **Setup Wizard** (`/setup`).
3.  Create your initial Admin account.

## 3. Development Workflow (The "Daily Drive")
You need **1 Terminal in WSL2** and **1 Terminal in Windows**.

### Terminal 1: Infrastructure Logs (WSL2)
View logs for the Backend, Worker, and Frontend (running in Docker).
```bash
docker-compose logs -f
```
*   **App**: `https://localhost:14443`
*   **API Docs**: `https://localhost:14443/api/v1/docs`
*   **Health**: `https://localhost:14443/api/health`

### Terminal 2: Companion (Windows PowerShell)
**CRITICAL**: Must run in Windows to access system audio.
```powershell
cd companion
$env:CARGO_INCREMENTAL=0
cargo run
```

## 4. Component Guides

### 4.1 Database (Alembic)
*   **Apply Migrations**: `alembic upgrade head`

### 4.2 Worker Development
The worker container is configured with `watchmedo` to automatically restart when you modify Python files in `backend/`.
*   **Code Changes**: Edit files in `backend/` -> Worker restarts automatically.
*   **Dependency Changes**: If you edit `requirements.txt`, you must rebuild:
    ```bash
    docker-compose up -d --build worker
    ```
*   **Create Migration**: `alembic revision --autogenerate -m "message"`

### 4.2 Frontend
*   **State**: Zustand (`src/lib/store.ts`)
*   **Styling**: Tailwind CSS
