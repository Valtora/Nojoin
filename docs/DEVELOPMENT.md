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

### Browser Capture

- A supported Chromium browser on Windows or Linux for manual shared-audio live-capture validation
- Chrome on Android or iOS for manual microphone-only mobile capture validation
- Browser microphone permission for local smoke tests
- PipeWire screen capture support when validating Linux shared-screen or system audio behavior

## Compose Files

- `docker-compose.example.yml`: Deployment template using published images.
- `docker-compose.yml`: Local working copy created from the template.

The repository does not ship a dedicated Docker Compose development override.
If you need Docker-specific development customisations, make them in your local `docker-compose.yml`.

## Containerised Source Stack

The clearest Docker-based development workflow is to run a remote-development-style stack locally from your ignored `docker-compose.yml`.

In that mode:

- all local service containers use the `nojoin-dev-*` naming convention
- `https://localhost:14443` is served through Nginx
- the frontend comes from the `frontend` container
- `docker compose up -d --build frontend` changes what localhost serves

1. Create your local files from the templates:

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   cp .env.example .env
   ```

2. Update `.env` for local development. Keep `.env.example` unchanged because it remains the copy-paste template for non-development deployments. Set `FIRST_RUN_PASSWORD`. If you want the dedicated local development database name used by the compose template at the end of this document, set `POSTGRES_DB=nojoin_dev` in your local `.env` instead of changing the default `nojoin` value in `.env.example`.
3. Replace your local ignored `docker-compose.yml` with the `Localhost Dev Compose Template` appended at the end of this document.
4. Start or rebuild the stack:

   ```bash
   docker compose up -d --build
   ```

5. Open `https://localhost:14443`.

The appended template builds the Nojoin application services locally, keeps PostgreSQL, Redis, Nginx, and the Docker socket proxy on their normal upstream images.

### Incremental Rebuild Loop

Use the normal container rebuild loop when you are staying in the containerised localhost mode:

```bash
docker compose up -d --build api
docker compose up -d --build worker
docker compose up -d --build frontend
```

Practical use:

- Run `docker compose up -d --build api` after API changes or shared backend changes.
- Run `docker compose up -d --build worker` after worker code, dependency, or worker-image changes.
- Run `docker compose up -d --build frontend` after frontend changes that you want to verify through Nginx.

If you need to discard cached layers or the application services drift out of sync, use a clean rebuild:

```bash
docker compose down
docker compose build --no-cache api worker frontend
docker compose up -d --force-recreate
```

### Optional Backend Source-Mount Patch

If you want the API and worker to reflect Python changes without rebuilding those two images every time, patch your local ignored `docker-compose.yml` like this:

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
```

That patch is optional. It changes the backend feedback loop only. It does not change the frontend contract. If Nginx still proxies the `frontend` container, rebuilding `frontend` remains the way to update `https://localhost:14443`.

### Optional Host-Run Frontend Workflow

If you want the fastest UI feedback loop, you can instead run Next.js on the host. Treat that as a different local mode, not as a small patch on top of the containerised template above.

In host-run frontend mode:

- rebuilding the `frontend` container no longer changes what `https://localhost:14443` serves
- you must keep the host Next.js process running yourself
- you should patch both your local `docker-compose.yml` and your local `nginx/nginx.conf` together so Nginx proxies to the host frontend instead of the `frontend` container

Run the host frontend like this:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=/api npm run dev -- --hostname 0.0.0.0 -p 14141
```

After frontend changes, still run a production build check because development mode is more forgiving:

```bash
cd frontend
npm run build
```

If you only need supporting services while running code on the host, start the specific services you need. Examples include `db` and `redis`.

If you do not have an NVIDIA GPU, use CPU-only mode as described in [DEPLOYMENT.md](DEPLOYMENT.md) before starting the stack.

## Backend Development Notes

- The compose template does not publish PostgreSQL or Redis to the host by default.
- If you want host-based tooling or host-run services to talk to containerised PostgreSQL or Redis, add the required `ports` entries in your local `docker-compose.yml`.
- Heavy ML libraries must stay inside worker task functions, not API startup paths.

Useful migration commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
python -m backend.startup_canonical_cutover
```

Development guardrails:

- Do not delete or rename committed Alembic revision files once a database may have applied them.
- Container startup now runs two backend migration stages in order: Alembic first, then the startup canonical cutover sweep for pending legacy recordings.
- `python -m backend.startup_canonical_cutover` is the backend-only local entry point for the legacy recording sweep. Use it when you need to exercise or debug the container-level cutover without booting the full API service.
- `NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER=1` is available for local debugging only when you need to bypass that sweep temporarily.
- `NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE` controls the batch size used by the startup cutover loop. The default is `100`.
- The localhost dev compose template at the end of this document sets `NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS=true` on the API service. If a local dev database is stamped to a revision that no longer exists in your checkout, startup will restamp it to the current checked-in head before running `alembic upgrade head`.
- Keep that auto-repair flag limited to disposable local databases. For persistent deployments, fix the migration graph or reconcile the database revision manually instead of auto-stamping.

## Browser Capture Development

Browser capture code lives under `frontend/src/lib/capture/` and is exercised by the recording page and capture settings surfaces.

When changing capture behavior, validate the relevant parts of this path:

- Supported-browser gating for Chromium on Windows and Linux.
- Unsupported-browser messaging for Firefox, Safari, mobile browsers other than Chrome, and Chromium browsers on macOS.
- Mobile Chrome microphone-only start, waveform, pause/resume, stop/finalize, and clear copy that shared app/tab/system audio is not captured.
- Browser share picker flow for tab, window, and screen sharing.
- Shared-audio track detection and missing-audio messaging.
- Microphone permission and selected-device behavior.
- Per-source gain controls in **Settings > Capture**.
- Segment creation, sequential upload, worker transcode, live transcript dispatch, stop/finalize, pause/resume, and discard.
- The paused-recording lock after refresh, close, or in-app navigation away from the active recording.
- Focus changes to another tab, window, or application; these should not pause capture.

Useful focused checks:

```bash
cd frontend
npm run test -- --run src/lib/capture
npm run build
```

If you are validating through the containerised localhost stack, rebuild the frontend container after frontend changes:

```bash
docker compose up -d --build frontend
```

Read [CAPTURE.md](CAPTURE.md) before changing support copy, browser compatibility behavior, or troubleshooting guidance.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [AGENTS.md](AGENTS.md)

## Localhost Dev Compose Template

Copy this into your ignored `docker-compose.yml` when you want a containerised localhost development instance that mirrors the remote development deployment naming and rebuild behaviour.

```yaml
name: nojoin-dev

x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

x-shared-app-environment: &shared-app-environment
  DATABASE_URL: postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-postgres}@db:5432/${POSTGRES_DB:-nojoin_dev}
  REDIS_URL: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  CELERY_BROKER_URL: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  CELERY_RESULT_BACKEND: redis://:${REDIS_PASSWORD:-change_to_secure_string}@redis:6379/0
  HF_TOKEN: ${HF_TOKEN:-}
  DEFAULT_TIMEZONE: ${DEFAULT_TIMEZONE:-UTC}
  LLM_PROVIDER: ${LLM_PROVIDER:-gemini}
  GEMINI_API_KEY: ${GEMINI_API_KEY:-}
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
  OLLAMA_API_URL: ${OLLAMA_API_URL:-http://host.docker.internal:11434}
  DATA_ENCRYPTION_KEY: ${DATA_ENCRYPTION_KEY:-}
  GOOGLE_OAUTH_CLIENT_ID: ${GOOGLE_OAUTH_CLIENT_ID:-}
  GOOGLE_OAUTH_CLIENT_SECRET: ${GOOGLE_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_CLIENT_ID: ${MICROSOFT_OAUTH_CLIENT_ID:-}
  MICROSOFT_OAUTH_CLIENT_SECRET: ${MICROSOFT_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_TENANT_ID: ${MICROSOFT_OAUTH_TENANT_ID:-common}

services:
  db:
    container_name: nojoin-dev-db
    image: pgvector/pgvector:pg18-trixie
    volumes:
      - postgres_data:/var/lib/postgresql
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
      POSTGRES_DB: ${POSTGRES_DB:-nojoin_dev}
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-nojoin_dev}",
        ]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  redis:
    container_name: nojoin-dev-redis
    image: redis:alpine
    command: /bin/sh -ec 'printf "requirepass %s\n" "$$REDIS_PASSWORD" > /tmp/redis.conf && exec redis-server /tmp/redis.conf'
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-change_to_secure_string}
      REDISCLI_AUTH: ${REDIS_PASSWORD:-change_to_secure_string}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  socket-proxy:
    container_name: nojoin-dev-socket-proxy
    image: tecnativa/docker-socket-proxy
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      CONTAINERS: "1"
      POST: "0"
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  api:
    container_name: nojoin-dev-api
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    image: nojoin-dev-api:local
    pull_policy: never
    volumes:
      - ./data:/app/data
      - ./data/recordings:/app/recordings
      - model_cache:/shared_model_cache:ro
      - backup_temp:/tmp
    environment:
      <<: *shared-app-environment
      DOCKER_HOST: tcp://socket-proxy:2375
      WEB_APP_URL: ${WEB_APP_URL:-https://localhost:14443}
      NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS: ${NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS:-true}
      FIRST_RUN_PASSWORD: ${FIRST_RUN_PASSWORD:?Set FIRST_RUN_PASSWORD in .env}
      XDG_CACHE_HOME: /shared_model_cache
      HF_HOME: /shared_model_cache/huggingface
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      socket-proxy:
        condition: service_started
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  worker:
    container_name: nojoin-dev-worker
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    image: nojoin-dev-worker:local
    pull_policy: never
    volumes:
      - ./data:/app/data
      - model_cache:/home/appuser/.cache
      - /sys/class/drm:/sys/class/drm:ro
      - backup_temp:/tmp
    environment:
      <<: *shared-app-environment
      NVIDIA_VISIBLE_DEVICES: ${NVIDIA_VISIBLE_DEVICES:-all}
      NVIDIA_DRIVER_CAPABILITIES: ${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}
      WHISPER_ENABLE_WORD_TIMESTAMPS: ${WHISPER_ENABLE_WORD_TIMESTAMPS:-true}
      XDG_CACHE_HOME: /home/appuser/.cache
      HF_HOME: /home/appuser/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  frontend:
    container_name: nojoin-dev-frontend
    build:
      context: ./frontend
      dockerfile: Dockerfile
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}
    image: nojoin-dev-frontend:local
    pull_policy: never
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  nginx:
    container_name: nojoin-dev-nginx
    image: nginx:alpine
    ports:
      - "14141:80"
      - "14443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx:/etc/nginx/certs
      - ./docker/init-ssl.sh:/docker-entrypoint.d/99-init-ssl.sh
    depends_on:
      - frontend
      - api
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

volumes:
  postgres_data:
  model_cache:
  redis_data:
  backup_temp:

networks:
  nojoin_net:
    driver: bridge
```
