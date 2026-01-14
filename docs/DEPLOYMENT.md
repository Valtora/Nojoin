# Nojoin Deployment & Configuration Guide

## Deployment

### Hardware Requirements

- **Recommended:** Linux or Windows system with NVIDIA GPU (CUDA 12.x support).
- **Minimum:** 8GB VRAM for optimal performance (Whisper Turbo + Pyannote).
- **macOS Hosting:** Hosting the **backend** on macOS via Docker is **not recommended**. Docker on macOS cannot pass through the Apple Silicon GPU (Metal) to containers. This forces the system to run in CPU-only mode, which is significantly slower for transcription and diarization.
  - _Note:_ The **Companion App** is currently **Windows only**. macOS and Linux are **not** supported.

### Server Stack

- **Language:** Python 3.11+ (FastAPI)
- **Task Queue:** Celery with Redis
- **Database:** PostgreSQL 18
- **Container Runtime:** Docker (with NVIDIA Container Toolkit support)

### Deployment Method

- **Docker Compose:** The primary deployment method orchestrating API, Worker, DB, Redis, and Web Frontend containers.
- **Container Registry:** Images are automatically built and pushed to GHCR (`ghcr.io/valtora/nojoin-*`).
  - **Pull-First:** `docker compose up -d` pulls the latest pre-built images by default.
  - **Build-Local:** `docker compose up -d --build` forces a local build from source.
- **Hardware Support:**
  - **NVIDIA GPU (Default):** The `docker-compose.example.yml` (copied to `docker-compose.yml`) is configured for GPU inference by default.
  - **CPU-Only (Optional):** CPU support is enabled by commenting out the `deploy` section in `docker-compose.yml` (after copying from example).

### Containerization Standards

- **Registry:** GitHub Container Registry (GHCR).
- **CI/CD:** The GitHub Actions workflow (`docker-publish.yml`) builds and pushes images on push to `main` and release tags.
- **Base Images:** Optimized, pre-built images (e.g., `pytorch/pytorch`) are used.
- **Context Management:** `.dockerignore` excludes build artifacts.
- **Dependency Optimization:** `requirements.txt` is filtered during build to prevent redundant installations.

## Configuration Management

### Unified Strategy

Configuration is split between system-wide infrastructure settings and user-specific preferences.

- **System Config:** Stored in `data/config.json` (Server) and `config.json` (Companion). Includes infrastructure URLs, device paths, and hardware settings.
- **User Settings:** Stored in the PostgreSQL database per user. Includes UI themes, API keys, model preferences, and AI settings.

### Initial Setup

- **Setup Wizard:** Collects critical user settings (LLM Provider, API Keys, HuggingFace Token) during the creation of the first admin account.
- **Database Initialization:** Automatically handles schema creation and migrations on startup.

### Companion Config

- **Localhost Enforcement:** Defaults to `localhost` but supports configurable `api_host`.
- **Auto-Configuration:** The "Connect to Companion" flow in the Web Client automatically configures the Companion App.
- **Manual Configuration:** The "Settings" window in the System Tray allows manual entry of API Host and Port.
- **Config Preservation:** The Windows installer preserves `config.json` during updates.

### Security

- **SSL/TLS:** All communication is encrypted via HTTPS using Nginx.
- **Automatic SSL Generation:** Self-signed certificates are generated on startup if missing.
- **HTTPS Enforcement:** HTTP requests to port 14141 are redirected to HTTPS on port 14443.
- **Authentication:** JWT-based authentication.
- **JWT Secret Key:** Automatically generated on first startup and persisted to `data/.secret_key`. Can be overridden by the `SECRET_KEY` environment variable.
- **CORS & Remote Access:**
  - **CORS:** Configurable via the `ALLOWED_ORIGINS` environment variable.
  - **Remote Access:** Supports deployment behind reverse proxies by configuring `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS`.

### Environment Variables

The following environment variables can be used to pre-configure the system (e.g. in `.env` or `docker-compose.yml`), useful for automated deployments.

| Variable | Description |
| t --- | --- |
| `HF_TOKEN` | Hugging Face User Access Token (Read) |
| `LLM_PROVIDER` | Default LLM Provider (`gemini`, `openai`, `anthropic`, `ollama`) |
| `GEMINI_API_KEY` | Google Gemini API Key |
| `OPENAI_API_KEY` | OpenAI API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `OLLAMA_API_URL` | Ollama API URL (default: `http://host.docker.internal:11434`) |

## Troubleshooting

### Companion App Issues

The Companion App currently supports Windows only. If issues are encountered:

1. Ensure the latest version is installed from the [Releases](https://github.com/Valtora/Nojoin/releases) page.
2. Check the logs in the application directory.
3. Report issues on GitHub.

**For macOS and Linux users:** Contributors are being sought to help build companion apps for these platforms. Please see the [Contributing Guide](../CONTRIBUTING.md) for more information.
