# Nojoin Deployment & Configuration Guide

## Deployment

### Server Stack
*   **Language:** Python 3.11+ (FastAPI)
*   **Task Queue:** Celery with Redis
*   **Database:** PostgreSQL 16
*   **Container Runtime:** Docker (with NVIDIA Container Toolkit support)

### Deployment Method
*   **Docker Compose:** Primary deployment method orchestrating API, Worker, DB, Redis, and Web Frontend containers.
*   **Container Registry:** Images are automatically built and pushed to GHCR (`ghcr.io/valtora/nojoin-*`).
    *   **Pull-First:** `docker compose up -d` pulls the latest pre-built images by default.
    *   **Build-Local:** `docker compose up -d --build` forces a local build from source.
*   **Hardware Support:**
    *   **NVIDIA GPU (Default):** The `docker-compose.yml` is configured for GPU inference by default.
    *   **CPU-Only (Optional):** CPU support is enabled by commenting out the `deploy` section in `docker-compose.yml`.

### Containerization Standards
*   **Registry:** GitHub Container Registry (GHCR).
*   **CI/CD:** GitHub Actions workflow (`docker-publish.yml`) builds and pushes images on push to `main` and release tags.
*   **Base Images:** Optimized, pre-built images (e.g., `pytorch/pytorch`) are used.
*   **Context Management:** `.dockerignore` excludes build artifacts.
*   **Dependency Optimization:** `requirements.txt` is filtered during build to prevent redundant installations.

## Configuration Management

### Unified Strategy
Configuration is split between system-wide infrastructure settings and user-specific preferences.

*   **System Config:** Stored in `data/config.json` (Server) and `config.json` (Companion). Includes infrastructure URLs, device paths, and hardware settings.
*   **User Settings:** Stored in the PostgreSQL database per user. Includes UI themes, API keys, model preferences, and AI settings.

### Initial Setup
*   **Setup Wizard:** Collects critical user settings (LLM Provider, API Keys, HuggingFace Token) during the creation of the first admin account.
*   **Database Initialization:** Automatically handles schema creation and migrations on startup.

### Companion Config
*   **Localhost Enforcement:** Defaults to `localhost` but supports configurable `api_host`.
*   **Auto-Configuration:** The "Connect to Companion" flow in the Web Client automatically configures the Companion App.
*   **Manual Configuration:** "Settings" window in System Tray allows manual entry of API Host and Port.
*   **Config Preservation:** The Windows installer preserves `config.json` during updates.

### Security
*   **SSL/TLS:** All communication is encrypted via HTTPS using Nginx.
*   **Automatic SSL Generation:** Self-signed certificates are generated on startup if missing.
*   **HTTPS Enforcement:** HTTP requests to port 14141 are redirected to HTTPS on port 14443.
*   **Authentication:** JWT-based authentication.
*   **JWT Secret Key:** Automatically generated on first startup and persisted to `data/.secret_key`. Can be overridden by `SECRET_KEY` environment variable.
*   **CORS & Remote Access:**
    *   **CORS:** Configurable via `ALLOWED_ORIGINS` environment variable.
    *   **Remote Access:** Supports deployment behind reverse proxies by configuring `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS`.
