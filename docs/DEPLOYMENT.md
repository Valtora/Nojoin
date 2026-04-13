# Nojoin Deployment & Configuration Guide

This guide is for operators deploying and running Nojoin.

If you just want the fastest path to a working instance, start with [GETTING_STARTED.md](GETTING_STARTED.md) and return here when you need deeper hosting, networking, or upgrade guidance.

## Recommended Hardware

- **Recommended:** Linux or Windows with an NVIDIA GPU and CUDA 12.x support.
- **Practical minimum:** 8 GB VRAM for Whisper Turbo and Pyannote.
- **macOS hosting:** Not recommended for the backend because Docker on macOS cannot expose Apple Silicon GPU acceleration to the containers.
- **Companion app:** Currently Windows only.

## Core Requirements

- Docker Desktop or Docker Engine.
- Enough local storage for recordings, derived assets, and models.
- If using a GPU on Linux, NVIDIA drivers and the NVIDIA Container Toolkit.

## Quick Deployment

1. Clone the repository.
2. Copy `docker-compose.example.yml` to `docker-compose.yml`.
3. Optionally create or adjust `.env`.
4. Start the stack:

   ```bash
   docker compose up -d
   ```

5. Open `https://localhost:14443`.

The default `docker-compose.example.yml` is already configured for GPU inference.

If you want to build from local source instead of pulling the published images:

```bash
docker compose build && docker compose up -d --wait
```

## GPU Support

### Linux

1. Install the proprietary NVIDIA drivers.
2. Verify GPU visibility with:

   ```bash
   nvidia-smi
   ```

3. Install the NVIDIA Container Toolkit.
4. Configure Docker for NVIDIA runtime support:

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
   ```

### Windows

- Use Docker Desktop with the WSL 2 backend.
- Install up-to-date NVIDIA Windows drivers.

## CPU-Only Deployment

If you do not have a compatible NVIDIA GPU:

1. Open `docker-compose.yml`.
2. Comment out the `deploy` section under the `worker` service.
3. Start the stack normally with `docker compose up -d`.

Processing will be slower, but the application remains usable.

## Environment Variables

These variables are the main deployment knobs to know about.

- `NEXT_PUBLIC_API_URL`: Public API base URL used by the frontend. Include the `/api` suffix.
- `ALLOWED_ORIGINS`: Comma-separated list of trusted browser origins.
- `WEB_APP_URL`: Exact public browser origin used for invitation links, OAuth callbacks, and other public URLs.
- `REDIS_PASSWORD`: Password for the internal Redis service.
- `HF_TOKEN`: Hugging Face token used to download diarisation models.
- `DEFAULT_TIMEZONE`: Default installation timezone before a user saves their own timezone.
- `LLM_PROVIDER`: Default LLM provider such as `gemini`, `openai`, `anthropic`, or `ollama`.
- `GEMINI_API_KEY`: Gemini API key.
- `OPENAI_API_KEY`: OpenAI API key.
- `ANTHROPIC_API_KEY`: Anthropic API key.
- `OLLAMA_API_URL`: Local or remote Ollama endpoint.
- `GOOGLE_OAUTH_CLIENT_ID`: Google calendar OAuth client ID.
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google calendar OAuth client secret.
- `MICROSOFT_OAUTH_CLIENT_ID`: Microsoft calendar OAuth client ID.
- `MICROSOFT_OAUTH_CLIENT_SECRET`: Microsoft calendar OAuth client secret.
- `MICROSOFT_OAUTH_TENANT_ID`: Microsoft tenant ID. Use `common` only when the app registration supports the intended sign-in model.

For calendar-specific registration detail, read [CALENDAR.md](CALENDAR.md).

## Configuration Model

Nojoin splits configuration between:

- **System configuration**: installation-wide infrastructure and service settings.
- **User settings**: per-user preferences stored in the database.

The first-run setup wizard can pre-fill many values from environment variables to speed up deployment.

## Remote Access and Trusted Public Origin

If you expose Nojoin beyond localhost:

- Set `WEB_APP_URL` to the exact browser origin users will visit.
- Include that same origin in `ALLOWED_ORIGINS`.
- Keep the browser origin, reverse proxy origin, and OAuth callback origin aligned.

For publicly reachable deployments, use a VPN or a secure reverse proxy rather than exposing the service casually.

## Reverse Proxy Requirements

When fronting Nojoin with Nginx, Caddy, Traefik, or another reverse proxy:

1. Proxy to the HTTPS endpoint, not the plain HTTP port.
2. By default that means the host-facing port `14443`.
3. Disable upstream certificate verification because Nojoin uses a self-signed internal certificate by default.
4. Keep `WEB_APP_URL` and `ALLOWED_ORIGINS` aligned with the public origin.

### Caddy Example

```caddy
nojoin.yourdomain.com {
    reverse_proxy localhost:14443 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
```

### Nginx Example

```nginx
location / {
    proxy_pass https://localhost:14443;
    proxy_ssl_verify off;
    proxy_set_header Host $host;
}
```

## Migrations

Useful Alembic commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
```

## Updating a Deployment

### Pull-First Installations

```bash
docker compose down
docker compose pull
docker compose up -d
```

### Local-Source Installations

```bash
docker compose down
docker compose build
docker compose up -d --wait
```

Nojoin also exposes installed and latest published version information in **Settings > Updates**.

## Release Model

Nojoin uses a unified lock-step release model:

- A `vX.Y.Z` tag drives the published release.
- Docker images are published to GHCR.
- Windows Companion binaries are published alongside the server release.
- The application surfaces release metadata primarily from GitHub Releases.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [CALENDAR.md](CALENDAR.md)
- [ADMIN.md](ADMIN.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
