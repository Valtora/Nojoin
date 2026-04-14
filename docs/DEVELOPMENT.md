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

## Compose Files

- `docker-compose.example.yml`: Deployment template using published images.
- `docker-compose.yml`: Local working copy created from the template.

The repository does not ship a dedicated Docker Compose development override.
If you need Docker-specific development customisations, make them in your local `docker-compose.yml`.

## Containerised Source Stack

1. Create your local files from the templates:

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   cp .env.example .env
   ```

2. Set `FIRST_RUN_PASSWORD` in `.env`.
3. Start the standard stack:

   ```bash
   docker compose up -d
   ```

4. Open `https://localhost:14443`.

The compose template runs the published images.
If you need source changes reflected inside containers, add local build or bind-mount changes in your ignored `docker-compose.yml`.

If you only need supporting services while running code on the host, start the specific services you need.
Examples include `db` and `redis`.

If you do not have an NVIDIA GPU, use CPU-only mode as described in [DEPLOYMENT.md](DEPLOYMENT.md) before starting the stack.

## Backend Development Notes

- The compose template does not publish PostgreSQL or Redis to the host by default.
- If you want host-based tooling or host-run services to talk to containerised PostgreSQL or Redis, add the required `ports` entries in your local `docker-compose.yml`.
- Heavy ML libraries must stay inside worker task functions, not API startup paths.

Useful migration commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
```

## Frontend Development

For the best feedback loop, run the frontend on the host:

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
