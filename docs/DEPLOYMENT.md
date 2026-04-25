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

## Compose Files

- `docker-compose.example.yml`: Deployment template using the published GHCR images.
- `docker-compose.yml`: Local working copy created from the template.

The repository does not ship a separate Docker Compose development override.

## Quick Deployment

1. Clone the repository.
2. Create your local deployment files:

    ```bash
    cp docker-compose.example.yml docker-compose.yml
    cp .env.example .env
    ```

3. Set `FIRST_RUN_PASSWORD` in `.env`.
4. Set `DATA_ENCRYPTION_KEY` in `.env` before first production use.
5. Adjust `WEB_APP_URL` and `ALLOWED_ORIGINS` if the deployment is not local-only.
6. Review `docker-compose.yml` and apply any private or machine-specific changes.
7. Start the stack:

   ```bash
   docker compose up -d
   ```

8. Open `https://localhost:14443`.

Nojoin refuses first initialisation if `FIRST_RUN_PASSWORD` is missing.
If you add or change it, redeploy the stack before using the setup wizard.

The compose template is already configured for GPU inference.

`DATA_ENCRYPTION_KEY` is strongly recommended for every non-ephemeral deployment. Earlier releases relied on the auto-generated `data/.data_encryption_key` fallback alone, which meant encrypted calendar secrets and tokens could become unreadable if the app data directory was replaced while the database volume was preserved. Setting a stable `DATA_ENCRYPTION_KEY` avoids that class of failure.

If you are developing from local source instead of operating a deployment, read [DEVELOPMENT.md](DEVELOPMENT.md).

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

The default `.env.example` enables `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,utility`.

### Windows

- Use Docker Desktop with the WSL 2 backend.
- Install up-to-date NVIDIA Windows drivers.

## CPU-Only Deployment

If you do not have a compatible NVIDIA GPU:

1. Open `docker-compose.yml`.
2. Remove the `deploy` section under the `worker` service.
3. Start the stack normally with `docker compose up -d`.

Processing will be slower, but the application remains usable.

## Configure .env

Create `.env` from `.env.example` and treat it as the canonical operator configuration file.
The compose stack derives internal service URLs for PostgreSQL, Redis, and Celery automatically, so those values are intentionally not part of `.env.example`.
Keep any secrets, private mounts, or machine-specific overrides in your local `docker-compose.yml`, not in the tracked template.
Nojoin auto-generates and persists its JWT signing key under `data/.secret_key`, so no `.env` setting is required for that.
Nojoin can also auto-generate `data/.data_encryption_key`, but operators should treat that as a fallback rather than the primary persistence strategy.

### Always Set

- `FIRST_RUN_PASSWORD`: Required bootstrap password for the first successful Nojoin initialisation.
- `DATA_ENCRYPTION_KEY`: Stable installation-wide encryption seed used for calendar OAuth client secrets and user calendar tokens. Set this once and keep it unchanged for the lifetime of the deployment.

### Change for Remote or Reverse-Proxy Deployments

- `WEB_APP_URL`: Exact public browser origin used for invitation links, calendar OAuth callbacks, and other public URLs.
- `ALLOWED_ORIGINS`: Comma-separated list of trusted browser origins allowed to call the API.

### Common Optional Values

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

### DATA_ENCRYPTION_KEY Guidance

- Set `DATA_ENCRYPTION_KEY` before users connect calendar accounts or an admin stores calendar provider secrets.
- On an existing deployment that already has `data/.data_encryption_key`, copy that current value into `DATA_ENCRYPTION_KEY` before restarting the stack.
- Keep the value stable across restarts, upgrades, image changes, and host migrations.
- Store it in your secret manager, password vault, or deployment automation alongside other installation secrets.
- Do not rotate it casually. Changing it without first re-encrypting stored secrets will make previously stored calendar credentials unreadable.
- This is being documented explicitly as a hotfix follow-up for an oversight in `v0.8.1`, where relying on the generated key file alone could surprise operators during partial restores or host-level data replacement.

### Custom Frontend Build Value

- `NEXT_PUBLIC_API_URL`: Only set this when building a custom frontend image and the frontend is not using the default same-origin `/api` path.

For calendar-specific registration detail, read [CALENDAR.md](CALENDAR.md).

## Configuration Model

Nojoin splits configuration between:

- **System configuration**: installation-wide infrastructure and service settings.
- **User settings**: per-user preferences stored in the database.

The first-run setup wizard can pre-fill many values from environment variables to speed up deployment.
On uninitialised systems, that prefill flow is itself locked behind `FIRST_RUN_PASSWORD`.

## Remote Access and Trusted Public Origin

If you expose Nojoin beyond localhost:

- Set `WEB_APP_URL` to the exact browser origin users will visit.
- Include that same origin in `ALLOWED_ORIGINS`.
- Keep the browser origin, reverse proxy origin, and OAuth callback origin aligned.

For publicly reachable deployments, use a VPN or a secure reverse proxy rather than exposing the service casually.
For internet-exposed deployments, treat `FIRST_RUN_PASSWORD` as a deployment secret and avoid logging request headers that could capture it during the setup flow.

## Reverse Proxy Requirements

When fronting Nojoin with Nginx, Caddy, Traefik, or another reverse proxy:

1. Proxy to the HTTPS endpoint, not the plain HTTP port.
2. By default that means the host-facing port `14443`.
3. Disable upstream certificate verification because Nojoin uses a self-signed internal certificate by default.
4. Keep `WEB_APP_URL` and `ALLOWED_ORIGINS` aligned with the public origin.
5. If you replace or rotate the public TLS certificate presented to the Companion, users must re-pair the Companion so it can pin the new certificate.

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

## Upgrading and Migration

- Keep server and Companion app versions aligned.
- When performing major upgrades, check release notes for breaking changes.
- **Companion Security Upgrade**: Upgrading to a version that implements the strict one-backend manual pairing model will automatically clear out any legacy connection state in the Companion app. You will need to perform a clean first-pair workflow (Settings > Pair with Nojoin) to continue using the Companion.
- **Companion TOFU TLS Pinning**: The Companion now pins the backend certificate it first sees during pairing. Disconnecting the current backend from Companion Settings clears that saved trust and leaves the app ready for a clean new pairing.

## Database Migrations

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

### Local Custom Builds

```bash
docker compose down
docker compose build
docker compose up -d
```

Use this only if your local `docker-compose.yml` includes custom build directives.

Nojoin also exposes installed and latest published version information in **Settings > Updates**. The installed version is read from build metadata embedded into the API image, with local source builds falling back to `docs/VERSION`.

## Release Model

Nojoin uses a unified lock-step release model:

- A `vX.Y.Z` tag drives the published release.
- Docker images are published to GHCR.
- The API image embeds the resolved server version during the build, so the installed version shown in Settings does not depend on Docker daemon inspection at runtime.
- Windows Companion binaries are published alongside the server release.
- The application surfaces release metadata primarily from GitHub Releases.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [CALENDAR.md](CALENDAR.md)
- [ADMIN.md](ADMIN.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
