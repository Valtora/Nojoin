# Nojoin Deployment & Configuration Guide

This guide is for operators deploying and running Nojoin.

If you just want the fastest path to a working instance, start with [GETTING_STARTED.md](GETTING_STARTED.md) and return here when you need deeper hosting, networking, or upgrade guidance.

## Recommended Hardware

- **Recommended:** Linux or Windows with an NVIDIA GPU and CUDA 12.x support.
- **Practical minimum:** 8 GB VRAM for Whisper Turbo and Pyannote.
- **macOS hosting:** Not recommended for the backend because Docker on macOS cannot expose Apple Silicon GPU acceleration to the containers.
- **Capture browser:** Chrome on Windows, Linux, or macOS for shared-audio live recording; Edge, Brave, Arc, or another Chromium-family browser on Windows or Linux; or Chrome on Android/iOS for microphone-only live recording. Other Chromium-family browsers on macOS are best-effort.

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
9. Use Chrome on Windows, Linux, or macOS for shared-audio live recording, another Chromium-family browser on Windows or Linux, or Chrome on Android/iOS for microphone-only live recording. Other Chromium-family browsers on macOS are best-effort. Other browsers can still review and administer Nojoin.

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

## Worker Container Startup

The worker container starts Celery without preloading inference models. Nojoin
keeps GPU memory idle at startup, then queues worker-side model preparation for
the configured Whisper model, Pyannote diarisation, and voice embeddings. The
worker validates those assets on CPU where possible, caches them on disk, and
releases model objects and CUDA memory before returning to idle.

If an administrator switches transcription to Parakeet or Canary, Nojoin queues
preparation for the selected ONNX ASR model after the setting is saved. Live and
final processing still load inference models only for active work. After each
worker task, Nojoin releases model caches and clears CUDA memory when
`keep_models_loaded` is unset or false. Set `keep_models_loaded=true` only if you
deliberately prefer warmer repeated processing over idle VRAM.

### GPU Acceleration

The worker image installs Triton in its virtual environment so Whisper word-level timestamps use GPU-accelerated kernels. Without Triton, `whisper/timing.py` falls back to slower CPU-based implementations for word alignment.

Text embedding (used during AI-generated meeting intelligence) uses the ONNX Runtime CUDA execution provider when available, with an automatic CPU fallback.

The Parakeet and Canary ASR engines also use ONNX Runtime CUDA. Some ONNX graph operations are inherently CPU-pinned; the resulting memcpy overhead is expected and does not indicate a configuration problem.

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
- `NOJOIN_TRUSTED_PROXIES`: Comma-separated list of trusted proxy IP addresses, CIDR blocks, or hostnames. Defaults to `127.0.0.1,::1,nginx` to cover local loopback access and the default Docker Nginx proxy container name. If deploying behind an external load balancer or edge proxy (e.g. Cloudflare, AWS ALB), add its IP/CIDR to ensure that rate-limiting resolves client IPs correctly and safely.

### Common Optional Values

- `REDIS_PASSWORD`: Password for the internal Redis service.
- `HF_TOKEN`: Optional Hugging Face token used only when you want to refresh the bundled Pyannote diarisation assets from upstream.
- `DEFAULT_TIMEZONE`: Default installation timezone before a user saves their own timezone.
- `LLM_PROVIDER`: Default LLM provider such as `gemini`, `openai`, `anthropic`, or `ollama`.
- `GEMINI_API_KEY`: Gemini API key.
- `OPENAI_API_KEY`: OpenAI API key.
- `ANTHROPIC_API_KEY`: Anthropic API key.
- `OLLAMA_API_URL`: Local or remote Ollama endpoint.
- `OLLAMA_CONTEXT_WINDOW`: Ollama `num_ctx` value used for full-context meeting prompts. Defaults to `131072`; ensure the selected model and hardware can support the requested context.
- `SECONDARY_LLM_PROVIDER`: Secondary LLM provider used when the primary fails. Same values as `LLM_PROVIDER`. Leave empty to disable fallback.
- `SECONDARY_GEMINI_API_KEY`: Gemini API key for the secondary provider.
- `SECONDARY_OPENAI_API_KEY`: OpenAI API key for the secondary provider.
- `SECONDARY_ANTHROPIC_API_KEY`: Anthropic API key for the secondary provider.
- `SECONDARY_OLLAMA_API_URL`: Ollama endpoint for the secondary provider.
- `SECONDARY_OLLAMA_CONTEXT_WINDOW`: Ollama `num_ctx` value for the secondary Ollama provider. Defaults to `131072`.
- `GOOGLE_OAUTH_CLIENT_ID`: Google calendar OAuth client ID.
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google calendar OAuth client secret.
- `MICROSOFT_OAUTH_CLIENT_ID`: Microsoft calendar OAuth client ID.
- `MICROSOFT_OAUTH_CLIENT_SECRET`: Microsoft calendar OAuth client secret.
- `MICROSOFT_OAUTH_TENANT_ID`: Microsoft tenant ID. Use `common` only when the app registration supports the intended sign-in model.
- `NOJOIN_UMASK`: Custom umask for the application processes. Defaults to `0077` (owner-only access: `0600`/`0700` permissions on files/directories).

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

### Loopback Port Binding (DEP-001)

By default, the bundled Nginx proxy publishes ports `14141` and `14443` bound to the loopback interface (`127.0.0.1`) rather than all host interfaces (`0.0.0.0`). This ensures that if you place Nojoin behind an edge reverse proxy (such as Caddy, Traefik, or a tunnel) on the same host, the bundled proxy is not exposed directly to the public internet, preventing bypass of the edge proxy's authentication, rate limiting, or filtering.

* **NOJOIN_BIND_ADDRESS**: Controls the host IP interface the bundled proxy binds to. Defaults to `127.0.0.1`.
* **Direct-Access Deployments**: If you do not use an edge proxy and want the bundled Nginx proxy to be reachable directly from other hosts or the public internet, set `NOJOIN_BIND_ADDRESS=0.0.0.0` in your `.env` file and restart the stack.
* **Firewall Expectations**: If exposing ports directly by setting `NOJOIN_BIND_ADDRESS=0.0.0.0` or a public IP, ensure you have configured appropriate host firewall rules (e.g., `ufw` or `iptables`) to restrict access to authorized IP ranges.

1. Proxy to the HTTPS endpoint, not the plain HTTP port.
2. By default that means the host-facing port `14443`.
3. Disable upstream certificate verification because Nojoin uses a self-signed internal certificate by default.
4. Keep `WEB_APP_URL` aligned with the public origin.
5. Preserve the public browser host when forwarding requests. The upstream `Host` and `X-Forwarded-Host` values should match the hostname in `WEB_APP_URL`.
6. Forward `X-Forwarded-Proto: https` so Nojoin can recognise secure browser requests through the proxy chain.
7. Keep the public HTTPS origin stable so browser capture, session cookies, invitation links, and OAuth callbacks all target the same Nojoin site.

If API requests fail with `400 Invalid host header`, the edge proxy is usually forwarding an internal upstream host such as `nojoin-nginx:443` instead of the public `WEB_APP_URL` host.

### Caddy Example

```caddy
nojoin.yourdomain.com {
    reverse_proxy localhost:14443 {
        header_up Host nojoin.yourdomain.com
        header_up X-Forwarded-Host nojoin.yourdomain.com
        header_up X-Forwarded-Proto https

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
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Image Trust and Supply Chain

Published Nojoin images are built by a hardened, gated release pipeline. Operators with stricter assurance requirements can rely on the following properties.

- **Reproducible bases:** Every image is built from base images pinned by immutable `@sha256:` digest, not mutable tags. The exact GitHub Actions used by the release workflow are pinned to commit SHAs.
- **Update policy:** Pinned actions and base images are kept current automatically by Dependabot on a weekly cadence. Each update passes the full CI gate before it can merge, and a new release must be cut to publish updated images.
- **Signed images:** Every published image is signed with [cosign](https://github.com/sigstore/cosign) using keyless (OIDC) signing. The signature is bound to the release workflow's identity rather than a stored key.
- **Provenance and SBOM:** Every image carries a build-provenance attestation and a Software Bill of Materials (SBOM) attestation describing how it was built and what it contains.
- **Pre-publication verification:** Before the rolling `latest` and `major.minor` tags are published, the api and frontend images are booted with their real dependencies and must pass their production healthchecks, and all images are asserted to run as a non-root user.

### Verifying an Image Before Deploying

Verify the cosign signature (replace the tag as needed):

```bash
cosign verify ghcr.io/valtora/nojoin-api:latest \
  --certificate-identity-regexp "^https://github.com/Valtora/Nojoin/.github/workflows/release.yml@.*$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Inspect the provenance and SBOM attestations:

```bash
# Provenance attestation
cosign verify-attestation --type slsaprovenance ghcr.io/valtora/nojoin-api:latest \
  --certificate-identity-regexp "^https://github.com/Valtora/Nojoin/.*$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# SBOM and image index (digests, platforms, attestations)
docker buildx imagetools inspect ghcr.io/valtora/nojoin-api:latest
```

Pinning a deployment to an exact image digest (`ghcr.io/valtora/nojoin-api@sha256:...`) rather than a rolling tag guarantees you run the precise image you verified.

## Upgrading and Migration

- When performing major upgrades, check release notes for breaking changes.
- **TLS Private Key Permissions (SEC-005):** For security hardening, the TLS private key (`cert.key`) generated in `nginx/` is now set to mode `600` (owner-readable only) instead of `644` (world-readable). For existing deployments, operators should manually restrict the permissions of their existing private key on the host:
  ```bash
  chmod 600 nginx/cert.key
  ```
- **Confidential Data File Permissions (SEC-006):** For security hardening, all confidential application data files (audio recordings, JWT keys, logs, documents, configuration files) now default to owner-only permissions. A recursive startup repair pass automatically secures existing data inside the container-mounted directory. If you are using host-mounted directories and want to align host-level permissions, you can manually restrict them:
  ```bash
  chmod -R 700 ./data
  ```
  If you have special host-integration requirements that require group or world read access, you can configure a custom umask using the `NOJOIN_UMASK` environment variable (e.g. `NOJOIN_UMASK=0022` or `NOJOIN_UMASK=0002`).
### One-Time Migrations From Pre-Browser-Capture Releases

The notes below describe one-time migrations that run automatically when you first upgrade across the relevant cutover. They apply only if your database or installation predates that cutover. On a clean install, or on any installation already past these cutovers, they require no action and can be treated as historical context.

- Browser-capture cutover: the Windows desktop helper has been retired. Users start live recordings directly from the Nojoin web app in a supported browser. Existing recordings remain viewable and process through the same backend pipeline; any remaining native-helper installs are obsolete and should be removed from user machines.
- Canonical-pipeline cutover (first upgrade only): if the database still contains pre-cutover recordings, the first upgrade across this cutover runs a blocking backend-only canonical transcript migration during container startup after Alembic completes. Expect the API container to take longer to become ready on that first boot. During the sweep, existing recordings are classified entirely on the backend: successfully migrated legacy meetings remain viewable, while legacy meetings that cannot be canonicalised safely are marked for explicit reprocess instead of being edited in place. The supported rollback model for this cutover is code rollback only; canonical rows created during the migration are additive and are not converted back into legacy-only transcript state.
- Live-pipeline lane-state migration: this adds ASR and diarisation fields to `recording_audio_window_manifests` and backfills them from legacy window status plus completed diarisation window results. No operator action is required beyond allowing Alembic to run during normal container startup, but take a database backup before upgrade and avoid downgrading after the migration unless you are prepared to restore from backup.

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
