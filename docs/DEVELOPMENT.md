# Nojoin Development Setup

This guide covers local development prerequisites and the main commands used when working on Nojoin from source.

## Core Tooling

### General

- Git
- Docker

### Backend

- Python 3.11
- FFmpeg
- PostgreSQL development headers
- Compiler tools

Linux examples:

```bash
sudo apt install ffmpeg libpq-dev build-essential
```

Windows:

- Install FFmpeg and add it to `PATH`.
- Install the Microsoft Visual C++ Build Tools.

### Frontend

- Node.js 20 or newer
- npm

### Companion App

- Rust stable
- CMake
- Windows is the supported development platform for the Companion app today

Linux package example for Tauri prerequisites:

```bash
sudo apt install libwebkit2gtk-4.0-dev build-essential curl wget file libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev
```

## Bringing Up Supporting Services

For most local work you will want the Docker stack available.

### Pull-first path

```bash
docker compose up -d
```

### Build-local path

```bash
docker compose build && docker compose up -d --wait
```

If you do not have an NVIDIA GPU, use CPU-only mode as described in [DEPLOYMENT.md](DEPLOYMENT.md).

## Backend Development Notes

- FastAPI serves the API.
- Celery handles heavy background processing.
- PostgreSQL and Redis normally run through Docker Compose during development.
- Heavy ML libraries must stay inside worker task functions, not API startup paths.

Useful migration commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
```

## Frontend Development

Install dependencies and run the dev server:

```bash
cd frontend
npm install
npm run dev
```

After frontend changes, run a production build check because development mode is more forgiving:

```bash
cd frontend
npm run build
```

## Companion Development

The Companion app currently targets Windows.

For development:

```bash
cd companion
npm install
npm run tauri dev
```

For a release build on Windows:

```bash
cd companion
npm run tauri build
```

If you are building signed updates locally, ensure `TAURI_PRIVATE_KEY` and `TAURI_KEY_PASSWORD` are available in your environment.

## Related Docs

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [AGENTS.md](AGENTS.md)
