# Nojoin Deployment & Configuration Guide

This guide is for operators deploying and running Nojoin.

If you just want the fastest path to a working instance, start with [GETTING_STARTED.md](GETTING_STARTED.md) and return here when you need deeper hosting, networking, or upgrade guidance.

## Recommended Hardware

- **Recommended:** Linux or Windows with an NVIDIA GPU and CUDA 12.x support.
- **Practical minimum:** 8 GB VRAM for Whisper Turbo and Pyannote.
- **macOS hosting:** Not recommended for the backend because Docker on macOS cannot expose Apple Silicon GPU acceleration to the containers.
- **Capture browser:** Chrome, Edge, Brave, Arc, or another Chromium-family browser on Windows or Linux for shared-audio live recording, or Chrome on Android/iOS for microphone-only live recording.

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
5. Adjust `WEB_APP_URL` if the deployment is not local-only.
6. Review `docker-compose.yml` and apply any private or machine-specific changes.
7. Start the stack:

   ```bash
   docker compose up -d
   ```

8. Open `https://localhost:14443`.
9. Use a supported Chromium browser on Windows or Linux for shared-audio live recording, or Chrome on Android/iOS for microphone-only live recording. Other browsers can still review and administer Nojoin.

Nojoin refuses first initialisation if `FIRST_RUN_PASSWORD` is missing.
If you add or change it, redeploy the stack before using the setup wizard.
If `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, `REDIS_PASSWORD`, or the
tracked PostgreSQL password placeholder are left at their example values,
Nojoin now emits startup log warnings and an authenticated frontend warning
toast. Those warnings are advisory only; operators are still responsible for
replacing the placeholder secrets in `.env`.

The compose template is already configured for GPU inference.

The compose files now health-gate the web stack so `frontend` waits for a healthy `api`, and `nginx` waits for healthy `api` plus `frontend` before it is considered ready.

When doing targeted starts from a fully stopped stack, remember that Docker Compose does not auto-start an omitted dependent service. If you want the proxy back as part of a partial startup, include `nginx` explicitly:

```bash
docker compose up -d api frontend nginx
```

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
Nojoin auto-generates and persists its JWT signing keyring under `data/.secret_keys.json` in the default deployment, migrating any legacy `data/.secret_key` file on startup, so no `.env` setting is required for that.
Nojoin can also auto-generate `data/.data_encryption_key`, but operators should treat that as a fallback rather than the primary persistence strategy.

### Always Set

- `FIRST_RUN_PASSWORD`: Required bootstrap password for the first successful Nojoin initialisation.
- `DATA_ENCRYPTION_KEY`: Stable installation-wide encryption seed used for calendar OAuth client secrets and user calendar tokens. Set this once and keep it unchanged for the lifetime of the deployment.
- `POSTGRES_PASSWORD`: Replace the tracked example value before any deployment that persists data or is reachable by other users or hosts.
- `REDIS_PASSWORD`: Replace the tracked example value before any deployment that persists data or is reachable by other users or hosts.

### Change for Remote or Reverse-Proxy Deployments

- `WEB_APP_URL`: Exact public browser origin used for invitation links, calendar OAuth callbacks, other public URLs, and the backend CORS allowlist.

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
- Keep the browser origin, reverse proxy origin, and OAuth callback origin aligned.
- The backend automatically includes `WEB_APP_URL` in its browser CORS allowlist.

For publicly reachable deployments, use a VPN or a secure reverse proxy rather than exposing the service casually.
For internet-exposed deployments, treat `FIRST_RUN_PASSWORD` as a deployment secret and avoid logging request headers that could capture it during the setup flow.

## Reverse Proxy Requirements

When fronting Nojoin with Nginx, Caddy, Traefik, or another reverse proxy:

1. Proxy to the HTTPS endpoint, not the plain HTTP port.
2. By default that means the host-facing port `14443`.
3. Disable upstream certificate verification because Nojoin uses a self-signed internal certificate by default.
4. Keep `WEB_APP_URL` aligned with the public origin.
5. Keep the public HTTPS origin stable so browser capture, session cookies, invitation links, and OAuth callbacks all target the same Nojoin site.

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

- When performing major upgrades, check release notes for breaking changes.
- The browser-capture cutover retires the Windows desktop helper. Users start live recordings directly from the Nojoin web app in a supported browser.
- Existing recordings remain viewable and process through the same backend pipeline. Existing native-helper installs are obsolete and should be removed from user machines.
- Current pipeline-cutover releases run a blocking backend-only canonical transcript migration during container startup after Alembic completes. Expect the API container to take longer to become ready on the first boot after upgrade if the database still contains pre-cutover recordings.
- During that startup cutover, existing recordings are classified entirely on the backend. Successfully migrated legacy meetings remain viewable, while legacy meetings that cannot be canonicalized safely are marked for explicit reprocess instead of being edited in place.
- The supported rollback model for this cutover is code rollback only. Canonical rows created during startup migration are additive and are not converted back into legacy-only transcript state.
- The live pipeline lane-state migration adds ASR and diarisation fields to `recording_audio_window_manifests` and backfills them from legacy window status plus completed diarisation window results. No operator action is required beyond allowing Alembic to run during the normal container startup, but take a database backup before upgrade and avoid downgrading after the migration unless you are prepared to restore from backup.

### Live Pipeline Readiness Notes

- Browser live capture now depends on the canonical 16 kHz, two-channel browser segment WAVs produced by the worker. Channel 0 is shared/system audio when available and channel 1 is microphone audio.
- Segment sequences start at `0`. Operators investigating upload or finalization failures should check for missing sequence numbers before assuming ASR or diarisation failure.
- Recording detail pages expose only high-level progress, waveform state, and Meeting Edge guidance during live capture.
- Final processing reuses live transcript and source-channel speaker evidence only after stable-id or clear overlap alignment. Ambiguous live/final spans are intentionally left to final ASR and diarisation output.
- A practical smoke after upgrade is: start a browser recording in supported desktop Chromium, share a meeting tab with audio, speak through the microphone, observe waveform and Meeting Edge or processing-state updates, pause, resume, finalize, then verify final transcript and speaker continuity. For mobile capture changes, also smoke Chrome on Android or iOS microphone-only recording with the tab open and the phone awake.

### Canonical Cutover Notes

- The container entrypoint now runs Alembic first and then runs a second backend-only startup cutover pass before the API process starts serving traffic.
- The startup cutover acquires a database-level lock so only one upgraded instance performs the legacy-recording sweep at a time.
- Historical meetings from before the unified pipeline are supported for viewing and explicit reprocess. They are not guaranteed to preserve transcript-edit or speaker-edit parity without reprocessing.
- For local recovery or debugging only, `NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER=1` skips the second startup cutover pass. Do not rely on that flag as a normal production rollout strategy.
- `NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE` can reduce or increase the number of pending legacy recordings processed per sweep iteration during startup. The default is `100`.

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
- The application surfaces release metadata primarily from GitHub Releases.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [CALENDAR.md](CALENDAR.md)
- [ADMIN.md](ADMIN.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
