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

### Build Nojoin Images Locally

If you want the Docker stack to run your checked-out source instead of the published GHCR images, change the `api`, `worker`, and `frontend` services in your local `docker-compose.yml` from `image:` entries to local `build:` entries.

One working pattern is:

```yaml
services:
   api:
      build:
         context: .
         dockerfile: docker/Dockerfile.api
      image: nojoin-api:local
      pull_policy: never

   worker:
      build:
         context: .
         dockerfile: docker/Dockerfile.worker
      image: nojoin-worker:local
      pull_policy: never

   frontend:
      build:
         context: ./frontend
         dockerfile: Dockerfile
         args:
            NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
      image: nojoin-frontend:local
      pull_policy: never
```

This leaves PostgreSQL, Redis, Nginx, and the Docker socket proxy on their normal upstream images while forcing the Nojoin application services to build from the current checkout.

If you want the earlier rebuild-friendly development loop back in your local ignored `docker-compose.yml`, patch the `api` and `worker` services with a source mount and keep the frontend on a same-origin build value:

```yaml
services:
   api:
      command: uvicorn backend.main:app --host 0.0.0.0 --port 8000
      volumes:
         - .:/app
         - ./data:/app/data
         - ./data/recordings:/app/recordings
         - model_cache:/shared_model_cache:ro
         - backup_temp:/tmp

   worker:
      command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker --loglevel=info --pool=solo
      volumes:
         - .:/app
         - ./data:/app/data
         - model_cache:/home/appuser/.cache
         - /sys/class/drm:/sys/class/drm:ro
         - backup_temp:/tmp

   frontend:
      build:
         args:
            NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
      environment:
         NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
```

Apply that only to your local `docker-compose.yml`, not to the tracked `docker-compose.example.yml`.
The bind mounts and worker auto-restart command are development conveniences, while the tracked template remains the operator deployment file.
The frontend does not need its own source-mount patch for this rebuild loop. As long as the local `frontend` service keeps its `build:` block and same-origin `NEXT_PUBLIC_API_URL` value, `docker compose up -d --build frontend` remains a normal part of the workflow.

With that local patch in place, the usual incremental loop is:

```bash
docker compose up -d --build api
docker compose up -d --build worker
docker compose up -d --build frontend
```

Practical use:

- Run `docker compose up -d --build api` after API changes or shared backend changes when you want FastAPI restarted against the current checkout.
- The worker sees the mounted source tree and `watchmedo` restarts Celery automatically for Python edits under `backend/`, so you usually only rebuild `worker` after dependency, Dockerfile, or worker-image changes.
- Run `docker compose up -d --build frontend` after frontend changes that you want to verify through Nginx. This rebuild path works without the API and worker bind-mount changes because the frontend already builds from `./frontend`.

If you need to discard cached layers or the application services drift out of sync, use a clean rebuild:

```bash
docker compose down
docker compose build --no-cache api worker frontend
docker compose up -d --force-recreate
```

If you are keeping only the local `build:` entries and not the source-mount patch above, the normal incremental workflow remains:

```bash
docker compose up -d --build
```

The frontend is served from a built container image rather than a source mount. After frontend-only changes, rebuild that service before testing through Nginx:

```bash
docker compose up -d --build frontend
```

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

Development guardrails:

- Do not delete or rename committed Alembic revision files once a database may have applied them.
- The tracked development compose stack sets `NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS=true` on the API service. If a local dev database is stamped to a revision that no longer exists in your checkout, startup will restamp it to the current checked-in head before running `alembic upgrade head`.
- Keep that auto-repair flag limited to disposable local databases. For persistent deployments, fix the migration graph or reconcile the database revision manually instead of auto-stamping.

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
