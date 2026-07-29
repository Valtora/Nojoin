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

8. Open `https://localhost:14443/setup` and unlock the first-run wizard with your `FIRST_RUN_PASSWORD`.
9. Use Chrome on Windows, Linux, or macOS for shared-audio live recording, another Chromium-family browser on Windows or Linux, or Chrome on Android/iOS for microphone-only live recording. Other Chromium-family browsers on macOS are best-effort. Other browsers can still review and administer Nojoin.

Nojoin refuses first initialisation if `FIRST_RUN_PASSWORD` is missing.
If you add or change it, redeploy the stack before using the setup wizard.
The sign-in page does not link to the setup wizard, and every anonymous setup
denial returns the same generic response regardless of cause, so the service
never discloses whether it has been initialised. The specific denial reason
(wrong password, unset `FIRST_RUN_PASSWORD`, or already-initialised system)
appears in the API logs, and the API startup log prints the `/setup` address
while the system is uninitialised. Setup requests are rate limited per client
address.
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

## Data Directory Ownership

The `api` and `worker-*` containers run their long-lived process as UID 1000
(`appuser`), but their entrypoints enter as root first for one purpose: Docker
creates a missing bind-mount source on the host as `root:root`, which an
unprivileged process cannot then write into. The entrypoint corrects ownership
and immediately drops to `appuser` via `gosu`. The release smoke test asserts
that the running process is non-root.

Ownership is checked per directory, not just on the data root. Every
write-critical path (`recordings`, `recordings/temp`, `recordings/failed`,
`logs`, `documents`, `temp_uploads`, `temp_restores`, `restore_staging`,
`cli-oauth`) is created if missing and repaired independently, so a child
directory that was recreated as root under an already-correct parent is fixed
rather than skipped. When the data root itself is mis-owned, which is the first
boot on a fresh `data/`, a single recursive pass covers the whole tree; on later
restarts a large recordings library is never walked.

Ownership repair is best-effort by design. Some bind-mount backends, notably a
Windows-drive mount under Docker Desktop, either reject `chown` or ignore it. In
that case the container still starts and reports the problem rather than
crash-looping:

- The API logs an explicit error at startup naming the unwritable path.
- **Settings > System** shows the **Storage** readiness card as an error, and the
  pipeline summary is reported as blocked.
- Uploads and imports fail with an actionable 503 rather than a bare 500.

If you see that state, correct the ownership of the host directory bound to
`/app/data` so it is owned by UID 1000, then restart the API and worker
services.

Only `./data` is bound into the API container. Earlier versions also declared a
second bind of `./data/recordings` at `/app/recordings`; nothing read it, and
because Docker materialises each declared bind source independently as root, it
could recreate the recordings directory root-owned under an appuser-owned
parent. If you carry a Compose override derived from an older example file,
remove that line.

## Temporary Files and the Data Directory

No service mounts a shared volume over `/tmp`. Each container keeps a private
`/tmp` that is discarded when the container is recreated, which is what stops
scratch files accumulating for the life of a deployment.

Only one file has to pass between containers: a finished backup archive, built by
a worker and served by the API. It travels through `data/backups` on the `./data`
bind mount that every service already has, so the sharing uses the mount that
already exists rather than a second one. Uploaded archives and restore staging
work the same way, through `data/temp_uploads` and `data/restore_staging`.
`BACKUP_EXPORT_DIR` moves exports elsewhere if you want them on a separate disk;
the API and the workers must agree on the value.

Everything else a container writes to `/tmp` is genuinely local to that
container: ffmpeg intermediates, Python temporary files, and the scratch the
finalise pipeline removes when it completes. The pipeline clears its own
intermediates, and the daily cleanup task reclaims any left by a worker that was
killed before it could.

### Reclaiming the Old Shared /tmp Volume

Deployments created before this change mounted a `backup_temp` named volume at
`/tmp` in the `api` and all three `worker-*` services. That made `/tmp` both
permanent and shared, so every temporary file any of those four services leaked
accumulated there indefinitely, and only the export subdirectory was ever swept.

After updating your Compose file, the volume is no longer referenced. Nothing
durable is stored in it, so remove it to reclaim the space:

```bash
docker compose down
docker volume rm nojoin_backup_temp
docker compose up -d
```

Check what you are about to reclaim first if you want to see the scale of it:

```bash
docker run --rm -v nojoin_backup_temp:/old alpine du -sh /old
```

An export that was in flight when you upgrade is lost. Exports are transient by
design, with a 24-hour lifetime, so take the backup again if you needed it.

## Worker Container Startup

The worker container starts Celery without preloading inference models. Nojoin
keeps GPU memory idle at startup, then queues worker-side model preparation for
the configured Whisper model, Pyannote diarisation, and voice embeddings. The
worker validates those assets on CPU where possible, caches them on disk, and
releases model objects and CUDA memory before returning to idle.

One worker lane (`worker-io`) runs the embedded Celery Beat scheduler (`celery
worker -B`) that drives Nojoin's periodic jobs: calendar sync every 15 minutes,
calendar push-channel renewal every 30 minutes, and temporary-recording cleanup
daily. Beat runs on that single lane only, so it cannot double-schedule, and the
beat schedule state lives on the persistent data volume so the cadence survives
restarts. Optional calendar live sync (push notifications) additionally requires
the instance to be reachable from the public internet over HTTPS at
`WEB_APP_URL`; see [CALENDAR.md](CALENDAR.md).

Changing the transcription model later does not download anything on its own.
Preparation runs on the GPU lane, so an unannounced download would queue in
front of live work; instead **Settings > AI providers** asks whether to fetch a newly
selected model now, and **Model dependencies** offers a `Download` action plus
live progress for anything still missing. A model that is never prepared is
fetched on first use, which delays live transcription and Meeting Edge until it
is ready. Only one preparation runs at a time; a second request is refused with
409 while one is in flight. Deleting a cached model also runs on a worker, not
in the API: the API mounts the model volume read-only (`model_cache:ro`), so it
has no write access to that cache by design. Both actions therefore need a
running worker. Live and final processing still load inference models only for
active work. After each
worker task, Nojoin releases model caches and clears CUDA memory when
`keep_models_loaded` is unset or false — except while a recording is actively
uploading (live capture), where the live ASR model is kept resident so
consecutive segments are not forced to reload it (a reload costs several seconds
per segment). When capture goes idle the caches are released as normal, and a
recording finalise clears cached models before loading its heavier diarisation
stack so the two never exceed VRAM. Set `keep_models_loaded=true` only if you
deliberately prefer warmer repeated processing over idle VRAM across the board.

### Worker Concurrency Lanes

To stop a long recording finalise from blocking every other user's live
transcription, notes, chat, and calendar sync, worker tasks are split across
three Celery queues, each drained by its own container:

- `worker-gpu` — GPU-bound inference (recording finalise, live ASR, speaker
  embeddings). Single-slot (`--pool=solo --concurrency=1`): with one GPU, heavy
  work is deliberately serialised to protect VRAM. This is the only worker
  granted GPU access.
- `worker-cpu` — ffmpeg segment transcode, proxy generation, and backups
  (`--pool=prefork --concurrency=3`). No GPU.
- `worker-io` — network-bound work: Meeting Edge, notes, chat embeddings,
  calendar sync, and cleanup (`--pool=prefork --concurrency=4`). No GPU. Also
  runs Celery Beat.

Task-to-queue routing is defined in `backend/celery_app.py` (`TASK_ROUTES`);
anything unrouted falls back to the GPU lane. Tune the `--concurrency` values in
`docker-compose.yml` to your host: the CPU and IO lanes are cheap (no model
memory), while the GPU lane should stay at `--concurrency=1` unless you have
multiple GPUs, or a single card large enough to hold two concurrent pipelines.

With three worker containers plus the API, several worker processes each keep a
small database connection pool. The default PostgreSQL `max_connections` (100)
comfortably covers the reference `3` / `4` lane sizing; if you scale the lanes
much wider, raise `max_connections` to match.

Change `--concurrency` freely, but treat `--pool` as load-bearing. `solo` and
`prefork` both give a task an operating-system process to itself, which is what
makes it safe for worker code to queue follow-on Celery work with a blocking
call: it can stall only the task making it. A pool that shares one process
between concurrent tasks, such as `gevent`, `eventlet` or `threads`, breaks that
and reintroduces the head-of-line blocking described in
[ADR-0007](adr/0007-bounded-fail-fast-task-dispatch.md), which documents the
reasoning and what would need to change first.

### GPU Acceleration

The worker image installs Triton in its virtual environment so Whisper word-level timestamps use GPU-accelerated kernels. Without Triton, `whisper/timing.py` falls back to slower CPU-based implementations for word alignment.

Text embedding (used during AI-generated meeting intelligence) uses the ONNX Runtime CUDA execution provider when available, with an automatic CPU fallback.

The Parakeet and Canary ASR engines also use ONNX Runtime CUDA. Some ONNX graph operations are inherently CPU-pinned; the resulting memcpy overhead is expected and does not indicate a configuration problem.

#### Diagnosing a silent CPU fallback

ONNX Runtime treats its provider list as a preference, not a contract. If the CUDA execution provider cannot be loaded, the session is built on CPU and reported as successful, so the only symptom is that transcription runs far slower while the host CPU saturates. `worker-gpu` pegging several cores with `nvidia-smi` showing 0% GPU utilisation is the signature.

Note that PyTorch and ONNX Runtime resolve their CUDA libraries independently, so they can disagree. `torch.cuda.is_available()` returning `True`, VAD logging `Model loaded successfully on cuda`, and `nvidia-smi` working inside the container all confirm the container's GPU passthrough is intact. None of them say anything about ONNX Runtime.

To check the real state:

```bash
docker compose logs worker-gpu | grep -i "execution provider"
```

The worker logs which provider each ONNX model actually got after loading. A warning naming `fell back to CPU` means the CUDA provider was dropped. The underlying cause is usually a missing shared library, logged nearby as:

```
Failed to load library libonnxruntime_providers_cuda.so with error: libcudnn_*.so.9: cannot open shared object file
```

`onnxruntime.get_available_providers()` is not a valid check here. It lists the providers the build was compiled with, so it reports `CUDAExecutionProvider` even on a host with no GPU.

If a library is missing, confirm the loader can see cuDNN inside the container. The worker image registers the cuDNN wheel's directory with `ldconfig` at build time for exactly this reason:

```bash
docker compose exec worker-gpu ldconfig -p | grep libcudnn_adv
```

An empty result means the base image moved cuDNN and the image needs rebuilding against the corrected path.

## CPU-Only Deployment

If you do not have a compatible NVIDIA GPU:

1. Open `docker-compose.yml`.
2. Remove the `deploy` section under the `worker-gpu` service.
3. Start the stack normally with `docker compose up -d`.

Processing will be slower, but the application remains usable. All three worker
lanes then run on CPU.

## Configure .env

Create `.env` from `.env.example` and treat it as the canonical operator configuration file.
The compose stack derives internal service URLs for PostgreSQL, Redis, and Celery automatically, so those values are intentionally not part of `.env.example`.
Keep any secrets, private mounts, or machine-specific overrides in your local `docker-compose.yml`, not in the tracked template.
Nojoin auto-generates and persists its JWT signing keyring under `data/.secret_keys.json` in the default deployment, migrating any legacy `data/.secret_key` file on startup, so no `.env` setting is required for that.
Nojoin can also auto-generate `data/.data_encryption_key`, but operators should treat that as a fallback rather than the primary persistence strategy.

### Always Set

- `FIRST_RUN_PASSWORD`: Required bootstrap password for the first successful Nojoin initialisation. It unlocks the setup wizard at `/setup` on an uninitialised system and is not used after initialisation.
- `DATA_ENCRYPTION_KEY`: Stable installation-wide encryption seed used for calendar OAuth client secrets and user calendar tokens. Set this once and keep it unchanged for the lifetime of the deployment.
- `POSTGRES_PASSWORD`: Replace the tracked example value before any deployment that persists data or is reachable by other users or hosts.
- `REDIS_PASSWORD`: Replace the tracked example value before any deployment that persists data or is reachable by other users or hosts.

### Change for Remote or Reverse-Proxy Deployments

- `WEB_APP_URL`: Exact public browser origin used for invitation links, calendar OAuth callbacks, other public URLs, and the backend CORS allowlist.
- `NOJOIN_TRUSTED_PROXIES`: Comma-separated list of trusted proxy IP addresses, CIDR blocks, or hostnames. Defaults to `127.0.0.1,::1,nginx` to cover local loopback access and the default Docker Nginx proxy container name. If deploying behind an external load balancer or edge proxy (e.g. Cloudflare, AWS ALB), add its IP/CIDR to ensure that rate-limiting resolves client IPs correctly and safely. A hostname entry only works if the API container can resolve it: a Docker container name resolves only on networks the API is itself attached to, so an edge proxy running on a **separate** Docker network must be trusted by its **IP or subnet CIDR, not its container name**. See [Trusted Proxy IPs for Rate Limiting (DEP-002)](#trusted-proxy-ips-for-rate-limiting-dep-002).

### Common Optional Values

- `REDIS_PASSWORD`: Password for the internal Redis service.
- `HF_TOKEN`: Optional Hugging Face token used only when you want to refresh the bundled Pyannote diarisation assets from upstream.
- `DEFAULT_TIMEZONE`: Default installation timezone before a user saves their own timezone.
- `MCP_ENABLED`: Master switch for the built-in MCP connector ([MCP.md](MCP.md)). Defaults to `true`; set to `false` to remove the `/mcp` endpoint, the OAuth discovery documents, and the connector authorisation endpoints entirely. Requires an API container restart to change.
- `NOJOIN_TELEMETRY_ENABLED`: Hard switch for anonymous usage data ([TELEMETRY.md](TELEMETRY.md)). Leave unset to manage it from **Settings > Users and access**. Set to `false` to disable it permanently: the value overrides the in-app setting, and the Settings toggle becomes read-only. Set it before first start if telemetry must never be sent from this deployment.
- `NOJOIN_TELEMETRY_ENDPOINT`: Overrides the ingest URL. Intended for testing; there is no reason to change it in a normal deployment.
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

Install-wide settings that an administrator changes in the UI (the AI provider and models, the install
glossary, the install default notes structure) are written to `data/config.json` rather than to the
database, so the mounted `data/` directory must be writable by the API container's user. If it is not,
saving any of those settings now fails with an explicit error instead of appearing to succeed and then
reverting on the next restart. If you see that error, check the ownership of the host directory bound to
`/app/data`.

## CLI OAuth (worker-io image)

The per-user CLI OAuth AI mode (routing inference through a user's own Claude or ChatGPT subscription) needs Node.js plus the Claude Code CLI and the OpenAI Codex CLI, which ship **only** in the `worker-io` image (`docker/Dockerfile.worker-io`, layered on the shared worker image). Point the `worker-io` service at that image via the `image:`/`build:` override in `docker-compose.example.yml`; `worker-gpu` and `worker-cpu` stay on the base image. No new `.env` is required — the encrypted credential reuses `DATA_ENCRYPTION_KEY`. Note the Codex CLI adds a large (~336 MB) native binary to this image only; `NOJOIN_CODEX_PATH` overrides the codex binary path if needed (default `/usr/local/bin/codex`). See [ADR-0002](adr/0002-cli-oauth-subscription-mode.md).

## Remote Access and Trusted Public Origin

This is the section to read if you want to reach Nojoin from a device other than
the machine it runs on: another computer on the LAN, a laptop over a VPN or
tailnet, or a phone. The mechanics of the proxy itself are in
[Reverse Proxy Requirements](#reverse-proxy-requirements); this section covers
what the application expects from the resulting origin.

If you expose Nojoin beyond localhost:

- Set `WEB_APP_URL` to the exact browser origin users will visit.
- Keep the browser origin, reverse proxy origin, and OAuth callback origin aligned.
- The backend automatically includes `WEB_APP_URL` in its browser CORS allowlist.
- Serve the remote origin over HTTPS. This is a hard requirement, not a
  recommendation; see below.

### HTTPS Is Required for Live Capture

Browsers only expose the microphone and screen-sharing APIs to a **secure
context**: an HTTPS origin, or `localhost`. A remote origin served over plain
HTTP is not a secure context, so `navigator.mediaDevices` is absent and Nojoin's
capture feature detection reports the browser as unsupported. Reviewing
recordings, playback, transcript editing, search, and administration all still
work over plain HTTP; **starting a live recording does not**.

Practically, this means any remote-access arrangement must terminate TLS in
front of Nojoin. Reaching the bundled proxy's plain HTTP port (`14141`) from
another device is enough to sign in and browse, and will then fail confusingly
at the point a user tries to record.

The API enforces the same expectation independently: non-HTTPS requests that it
cannot recognise as proxied HTTPS are redirected to `WEB_APP_URL` when safe, and
rejected with `400 Plain HTTP requests are not allowed. Use HTTPS.` otherwise.

For publicly reachable deployments, use a VPN or a secure reverse proxy rather than exposing the service casually.
For internet-exposed deployments, treat `FIRST_RUN_PASSWORD` as a deployment secret and avoid logging request headers that could capture it during the setup flow.

## Reverse Proxy Requirements

When fronting Nojoin with Nginx, Caddy, Traefik, Tailscale Serve, or another
reverse proxy:

### Loopback Port Binding (DEP-001)

By default, the bundled Nginx proxy publishes ports `14141` and `14443` bound to the loopback interface (`127.0.0.1`) rather than all host interfaces (`0.0.0.0`). This ensures that if you place Nojoin behind an edge reverse proxy (such as Caddy, Traefik, or a tunnel) on the same host, the bundled proxy is not exposed directly to the public internet, preventing bypass of the edge proxy's authentication, rate limiting, or filtering.

* **NOJOIN_BIND_ADDRESS**: Controls the host IP interface the bundled proxy binds to. Defaults to `127.0.0.1`.
* **Direct-Access Deployments**: If you do not use an edge proxy and want the bundled Nginx proxy to be reachable directly from other hosts or the public internet, set `NOJOIN_BIND_ADDRESS=0.0.0.0` in your `.env` file and restart the stack.
* **Firewall Expectations**: If exposing ports directly by setting `NOJOIN_BIND_ADDRESS=0.0.0.0` or a public IP, ensure you have configured appropriate host firewall rules (e.g., `ufw` or `iptables`) to restrict access to authorized IP ranges.

### Forwarding Checklist

1. Proxy to the HTTPS endpoint, not the plain HTTP port.
2. By default that means the host-facing port `14443`.
3. Disable upstream certificate verification because Nojoin uses a self-signed internal certificate by default.
4. Keep `WEB_APP_URL` aligned with the public origin.
5. Preserve the public browser host when forwarding requests. The upstream `Host` and `X-Forwarded-Host` values should match the hostname in `WEB_APP_URL`.
6. Forward `X-Forwarded-Proto: https` so Nojoin can recognise secure browser requests through the proxy chain.
7. Keep the public HTTPS origin stable so browser capture, session cookies, invitation links, and OAuth callbacks all target the same Nojoin site.
8. Forward the whole site through one origin, including `/mcp` and `/.well-known/oauth-*`. Those paths serve the built-in MCP connector and its OAuth discovery documents (see [MCP.md](MCP.md)); the bundled Nginx proxy already routes them to the API service, so an edge proxy that forwards everything to port `14443` needs no extra rules.
9. Stream responses rather than buffering them, forward WebSocket upgrades, allow long-lived connections, and allow large request bodies. See [Streaming, WebSocket, and Upload Forwarding](#streaming-websocket-and-upload-forwarding).
10. Add the edge proxy to `NOJOIN_TRUSTED_PROXIES` so per-client rate limiting resolves real client addresses. See [Trusted Proxy IPs for Rate Limiting (DEP-002)](#trusted-proxy-ips-for-rate-limiting-dep-002).

### Why the Host and Proto Headers Are Load-Bearing

Items 4, 5, and 6 are not stylistic. The API process is reached over plain HTTP
inside the Docker network, so it cannot observe TLS directly and instead treats a
request as secure only when **all** of the following hold:

- The immediate peer is a private, loopback, or link-local address, which the
  bundled proxy satisfies.
- `X-Forwarded-Proto` resolves to `https`.
- The `Host` header's hostname matches the hostname in `WEB_APP_URL`.
- The `X-Forwarded-Host` header's hostname matches the hostname in `WEB_APP_URL`.

Two distinct failures follow from getting this wrong, and they look nothing
alike:

- **`400 Invalid host header`** means the `Host` hostname is not in the trusted
  host list at all. Besides the loopback names (`localhost`, `127.0.0.1`, `::1`),
  the only hostname Nojoin trusts is the one in `WEB_APP_URL`. The usual cause is
  an edge proxy forwarding an internal upstream host such as `nojoin-nginx:443`,
  or `WEB_APP_URL` not having been updated to the public hostname and the stack
  restarted.
- **A redirect loop on page loads, plus `400 Plain HTTP requests are not
  allowed. Use HTTPS.` on saves**, means the host is trusted but one of the four
  conditions above fails, most often a rewritten `Host` or a missing
  `X-Forwarded-Proto`. Safe methods are redirected to `WEB_APP_URL`, which the
  proxy then rewrites the same way again; unsafe methods are rejected outright.
  If browsing appears to loop but the API is plainly up, check these headers
  before anything else.

`/health` and `/api/health` are deliberately exempt so container and uptime
checks can reach them over plain HTTP.

### Streaming, WebSocket, and Upload Forwarding

Most of Nojoin is ordinary request/response HTTP, including browser capture:
recording segments are uploaded as individual HTTP requests, not over a socket.
Three behaviours still need explicit proxy support.

- **Server-sent events.** Meeting chat responses stream as `text/event-stream`.
  A proxy that buffers responses will hold the entire answer until the stream
  closes, so replies appear all at once after a long pause or time out. Disable
  response buffering and allow read timeouts of several minutes. The bundled
  proxy uses `proxy_buffering off` with 300 second timeouts.
- **WebSocket upgrades.** Nojoin uses exactly one WebSocket endpoint,
  `/api/v1/system/logs/live`, which backs the live container log viewer in
  **Settings > System and logs** for administrators. Nothing else in the product uses
  WebSockets. If upgrades are not forwarded, that one panel fails to connect and
  the rest of Nojoin is unaffected.
- **Request body size.** The bundled proxy allows request bodies up to 500 MB.
  An edge proxy with a smaller limit will reject uploads with `413`. Nginx in
  particular defaults to 1 MB, which is below the live capture segment limit and
  far below document and backup uploads.

Caddy needs none of this stated explicitly: it streams responses, forwards
upgrades, and does not cap request bodies by default. Nginx needs all three
configured, which is why the example below is longer than the Caddy one.

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

The `map` block belongs in the `http` context, not inside `server`. It is the
standard Nginx idiom for conditional WebSocket upgrades, and avoids sending
`Connection: upgrade` on ordinary requests.

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

```nginx
location / {
    proxy_pass https://localhost:14443;
    proxy_ssl_verify off;

    # Nginx defaults to 1 MB, which is below Nojoin's capture segment size.
    client_max_body_size 500M;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # WebSocket upgrades for the admin live log viewer.
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;

    # Server-sent events: stream chat responses instead of buffering them.
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_connect_timeout 300s;
}
```

### Tailscale Serve

Tailscale Serve publishes Nojoin to your tailnet over HTTPS using a
Tailscale-issued certificate for the node's MagicDNS name. It suits the common
case of a GPU workstation at home that you want to reach from a laptop
elsewhere, without opening any router ports. Nojoin does not bundle or manage
Tailscale; Serve runs on the host, in front of the stack, like any other edge
proxy.

Two constraints decide whether this arrangement is appropriate at all:

- **The accessing device must be signed in to the same tailnet.** Serve does not
  publish anything to the public internet. A borrowed or locked-down device that
  cannot run Tailscale cannot reach a Serve endpoint.
- **Funnel is out of scope and is not recommended.** Funnel exposes the endpoint
  to the whole internet, which removes the network boundary that makes this
  arrangement worth choosing. If you need public access, treat it as a normal
  internet-exposed deployment and apply the guidance in
  [Remote Access and Trusted Public Origin](#remote-access-and-trusted-public-origin).

Against the contract above, a Serve deployment resolves as follows.

- **Leave `NOJOIN_BIND_ADDRESS` at its `127.0.0.1` default.** Serve runs on the
  host, so the loopback binding is already the correct target and the bundled
  proxy stays unreachable from the LAN. This is the one edge-proxy arrangement
  that needs no change to DEP-001.
- **Target the bundled proxy's HTTPS port, `14443`, on loopback.** Serve accepts
  an insecure-HTTPS target form (`https+insecure://`) for exactly this case, so
  it will not reject Nojoin's self-signed internal certificate. This satisfies
  checklist items 1 to 3.
- **Set `WEB_APP_URL` to the full tailnet origin**, `https://<node>.<tailnet>.ts.net`,
  and restart the stack. Until you do, the tailnet hostname is not in the trusted
  host list and the API answers `400 Invalid host header`.
- **Headers need no special handling.** Serve preserves the inbound `Host` and
  sets `X-Forwarded-Host`, `X-Forwarded-Proto: https`, and `X-Forwarded-For`.
  Because Serve connects to the bundled proxy over TLS, that proxy independently
  derives `X-Forwarded-Proto: https` from its own listener and passes the client
  `Host` through unchanged. Checklist items 5 and 6 are therefore met without
  extra configuration, and streaming, WebSocket upgrades, and body size are
  already handled by the bundled proxy.
- **Rate limiting needs one addition.** Because Serve connects from the host
  rather than from a container, the bundled proxy records the Docker bridge
  gateway as the immediate peer. Without trusting it, every tailnet device shares
  one rate-limit bucket. See
  [Host-Run Edge Proxies](#host-run-edge-proxies-tailscale-serve-and-similar).
- **Tailnet identity is not sign-on.** Serve adds `Tailscale-User-*` headers
  describing the tailnet user. Nojoin ignores them and does not support
  proxy-header authentication. Users still sign in to Nojoin normally, and
  tailnet membership is a network boundary rather than an authentication one.

The practical payoff beyond reachability is that the tailnet certificate is a
real, browser-trusted certificate. The origin is a secure context with no
certificate warning, so browser live capture works from the remote device, which
is not true of reaching the stack over plain HTTP.

Serve requires HTTPS certificates to be enabled for your tailnet in the
Tailscale admin console.

### Trusted Proxy IPs for Rate Limiting (DEP-002)

Nojoin derives each client's IP from the `X-Forwarded-For` chain by walking back through the proxies listed in `NOJOIN_TRUSTED_PROXIES` (see [Change for Remote or Reverse-Proxy Deployments](#change-for-remote-or-reverse-proxy-deployments)). Only the bundled Nginx proxy is trusted by default (`127.0.0.1,::1,nginx`).

When your edge proxy runs as a **container on a different Docker network** than `nojoin-api`, the API cannot resolve it by container name - Docker DNS only resolves names on networks the API container is itself attached to. Trust such a proxy by its **IP or subnet CIDR** instead. For example, if Caddy sits on a shared `proxy_net` in the `172.18.0.0/16` range:

```dotenv
NOJOIN_TRUSTED_PROXIES=127.0.0.1,::1,nginx,172.18.0.0/16
```

Using the bare container name (`...,nginx,caddy`) instead silently collapses every remote client into a single shared rate-limit bucket: `caddy` does not resolve from the API's network, so that hop is treated as untrusted and the walk stops on the proxy's own IP. Per-IP throttles (login, invitation, `/setup`, MCP registration) then key on one address for all external users. Nojoin logs a warning at API startup naming any configured trusted-proxy hostname it cannot resolve.

#### Host-Run Edge Proxies (Tailscale Serve and Similar)

An edge proxy that runs directly on the host rather than in a container -
Tailscale Serve, a system Nginx or Caddy service, or an SSH tunnel - reaches the
stack through the published loopback port. Docker's port publishing rewrites the
source address of that connection, so the bundled proxy typically records the
**Docker bridge gateway** of Nojoin's own network as the peer, not the real
client. The result is the same shared rate-limit bucket described above, reached
by a different route.

Read the actual gateway address rather than assuming one; the subnet depends on
what else the Docker daemon has allocated:

```bash
docker inspect nojoin-nginx \
  --format '{{ range $name, $net := .NetworkSettings.Networks }}{{ $name }} {{ $net.Gateway }}{{ printf "\n" }}{{ end }}'
```

On a stock deployment that prints one line, for the Compose-created
`nojoin_net`. Trust that address, or the subnet containing it:

```dotenv
NOJOIN_TRUSTED_PROXIES=127.0.0.1,::1,nginx,172.19.0.1
```

The subnet form (`172.19.0.0/16`) survives a gateway address change if the
network is recreated, at the cost of trusting any address in that range. Both
are acceptable; the range contains only Nojoin's own containers and the gateway.

The definitive check is the bundled proxy's own access log, which records the
address it actually sees for a remote request:

```bash
docker compose logs --tail 20 nginx
```

Leaving this unset is not a security problem. It only means per-IP throttles
count every remote user as one client, which on a small private tailnet may be an
acceptable trade for one less moving part.

## Image Trust and Supply Chain

Published Nojoin images are built by a hardened, gated release pipeline. Operators with stricter assurance requirements can rely on the following properties.

- **Reproducible bases:** Every image is built from base images pinned by immutable `@sha256:` digest, not mutable tags. The exact GitHub Actions used by the release workflow are pinned to commit SHAs.
- **Update policy:** Pinned actions and base images are kept current automatically by Dependabot on a weekly cadence. Each update passes the full CI gate before it can merge, and a new release must be cut to publish updated images.
- **Signed images:** Every published image is signed with [cosign](https://github.com/sigstore/cosign) using keyless (OIDC) signing. The signature is bound to the release workflow's identity rather than a stored key.
- **Provenance and SBOM:** Every image carries a build-provenance attestation and a Software Bill of Materials (SBOM) attestation describing how it was built and what it contains.
- **Pre-publication verification:** Before the rolling `latest` and `major.minor` tags are published, the api and frontend images are booted with their real dependencies and must pass their production healthchecks, and all images are asserted to run as a non-root user at runtime. The api and worker entrypoints enter as root only to repair ownership of the `./data` bind mount (Docker creates a missing bind source as root), then drop to an unprivileged user (uid 1000) via `gosu` before the long-running process starts; the smoke test verifies that dropped runtime uid rather than the image's declared `USER`.

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
- **Confidential Data File Permissions (SEC-006):** For security hardening, all confidential application data files (audio recordings, JWT keys, logs, documents, configuration files) now default to owner-only permissions. A recursive startup repair pass automatically secures existing data inside the container-mounted directory. The pass skips symbolic links, so a link stored under the data directory never has its target re-permissioned elsewhere on the filesystem. If you are using host-mounted directories and want to align host-level permissions, you can manually restrict them:
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

`worker-io` is built `FROM` the shared worker image, so the base must be built
before `worker-io`; otherwise Compose builds them in parallel and `worker-io` can
ship stale code layered on the previous base. The compose files wire this ordering
explicitly: `worker-io`'s build declares the base as a named `additional_contexts`
entry (`worker_base`), so Compose builds the base first and rebuilds `worker-io`
whenever it changes. `docker-compose.example.yml` pins that context to the published
image (`docker-image://…/nojoin-worker:latest`); a full source build points it at the
base service instead (`service:worker-gpu`, which also needs a `build:` stanza on the
worker services). No manual build ordering is required.

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
