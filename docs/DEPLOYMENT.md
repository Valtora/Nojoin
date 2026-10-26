# Nojoin Deployment & Configuration Guide

This is the operator reference for running Nojoin. For a first install, follow [GETTING_STARTED.md](GETTING_STARTED.md) and come back here for detail.

## Recommended Hardware

- **Recommended:** Linux or Windows with an NVIDIA GPU from the Turing generation or later (GTX 16 or RTX 20 series and newer) and NVIDIA driver 580 or newer. See [GPU Requirements](#gpu-requirements).
- **Practical minimum:** 8 GB VRAM for Whisper Turbo and Pyannote. Without a GPU, Nojoin runs on the CPU, more slowly ([CPU-Only Deployment](#cpu-only-deployment)).
- **macOS hosting:** Not recommended, because Docker on macOS cannot give containers Apple Silicon GPU acceleration.
- **Capture browsers:** See [CAPTURE.md](CAPTURE.md#supported-environments).

## Core Requirements

- Docker Desktop or Docker Engine with Compose.
- Disk space for recordings, derived files and models.
- For a GPU on Linux, the NVIDIA driver and the NVIDIA Container Toolkit.

## Quick Deployment

`docker-compose.example.yml` is the deployment template and pulls the published GHCR images. `.env.example` is the configuration template. Copy them to `docker-compose.yml` and `.env` as [GETTING_STARTED.md](GETTING_STARTED.md) describes, then before the first start:

1. Set `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD` in `.env` ([Always Set](#always-set)).
2. Set `WEB_APP_URL` if anyone will reach Nojoin from another machine ([Remote Access](#remote-access-and-trusted-public-origin)).
3. Keep private or machine-specific changes in `docker-compose.yml`. The repository ships no other compose override.
4. Run `docker compose up -d` and open `https://localhost:14443/setup`.

Compose stops with `Set FIRST_RUN_PASSWORD in .env` while that variable is unset or empty. After changing it, run `docker compose up -d` again so the `api` container receives it. [SECURITY.md](SECURITY.md#first-run-bootstrap-protection) describes how setup reports a denial.

If `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` or `REDIS_PASSWORD` still holds its example value, the api and workers log a warning at startup and the web app shows one to signed-in users. Nothing is blocked.

`frontend` waits for a healthy `api`, and `nginx` waits for both. Compose does not start a dependent service you leave out, so name `nginx` in a partial start:

```bash
docker compose up -d api frontend nginx
```

To work from source instead, see [DEVELOPMENT.md](DEVELOPMENT.md).

## GPU Support

The compose template is set up for GPU inference.

### GPU Requirements

The worker image is built on CUDA 13.0, which needs:

- **NVIDIA driver 580 or newer** on the host, Linux or Windows.
- **A Turing-generation GPU or later** (compute capability 7.5 or higher): GTX 16 and RTX 20 series and newer, including RTX 50 series. Maxwell, Pascal and Volta cards (GTX 900 and 10 series, TITAN V, Tesla P4, P40, P100 and V100) are not supported.

Check both with:

```bash
nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv
```

On Linux, `worker-gpu` checks both when it starts. If the driver is too old or the card predates Turing, it logs a `[gpu-preflight]` line saying which, hides that GPU and processes on the CPU.

### Linux

1. Install the proprietary NVIDIA driver and check that `nvidia-smi` lists the GPU.
2. Install the NVIDIA Container Toolkit.
3. Configure Docker for the NVIDIA runtime:

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
   ```

`.env.example` sets `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,utility` for `worker-gpu`.

### Windows

Use Docker Desktop with the WSL 2 backend and NVIDIA Windows driver 580 or newer.

### GPU Acceleration

`worker-gpu` is the only container with GPU access. Whisper and Pyannote run on PyTorch, and the image includes Triton so Whisper's word timestamps run on the GPU. Parakeet and Canary run on ONNX Runtime's CUDA provider. The text embedding model behind search and meeting chat runs on `worker-io` and `worker-parse`, which have no GPU.

| Engine | Weights on a GPU | Weights on a CPU | Download |
| --- | --- | --- | --- |
| Parakeet | fp32 | fp32 | About 2.5 GB |
| Canary | fp32, about 5.4 GB of VRAM | int8 | About 4 GB for fp32, 1 GB for int8 |

A GPU never loads int8: ONNX Runtime has no CUDA kernels for most quantised operations, so an int8 graph runs mostly on the CPU even with the CUDA provider loaded. Parakeet's int8 weights drop stretches of clear speech, so Parakeet loads fp32 on a CPU as well. Preparation fetches the precision the lane will load and validates the model on the CPU, so it briefly needs that much host memory but no VRAM.

Both engines transcribe in windows of 15 to 30 seconds that close at a pause, on every host, because longer windows lost speech. A window with no signal, such as a stretch VAD muted, is skipped. A GPU out-of-memory failure in final processing is retried once with GPU memory freed. Diarisation runs first and its pipeline is released before ASR loads, and the ASR models are released before voiceprint extraction: ONNX Runtime's memory arena never shrinks, so on a small card a stage that follows ASR without the release fails with CUDA out of memory.

#### Diagnosing a CPU Fallback

If ONNX Runtime cannot load its CUDA provider, it builds the session on the CPU and reports success. The symptom is slow transcription, with `worker-gpu` using several cores while `nvidia-smi` shows 0% GPU utilisation. PyTorch loads CUDA separately, so `torch.cuda.is_available()`, VAD logging `Model loaded successfully on cuda` and a working `nvidia-smi` in the container say nothing about ONNX Runtime. Nor does `onnxruntime.get_available_providers()`, which lists the providers the build includes even on a host with no GPU.

Check what each model got:

```bash
docker compose logs worker-gpu | grep -iE "execution provider|are on CPU"
```

`is using the ONNX Runtime CUDA execution provider` means the model is on the GPU. A warning ending `despite a GPU being present` means its sessions fell back to the CPU, usually because a shared library failed to load, logged nearby as:

```
Failed to load library libonnxruntime_providers_cuda.so with error: libcudnn_*.so.9: cannot open shared object file
```

The image registers cuDNN with `ldconfig` at build time. Confirm the loader sees it:

```bash
docker compose exec worker-gpu ldconfig -p | grep libcudnn_adv
```

An empty result means the base image moved cuDNN and the image needs rebuilding against the new path. A handful of memcpy nodes in ONNX Runtime's log is normal. Hundreds or thousands mean the graph is running on the CPU.

## Data Directory Ownership

The `api` and `worker-*` containers run as UID 1000 (`appuser`). Their entrypoints start as root only to fix ownership of the `./data` bind mount, because Docker creates a missing bind-mount source as `root:root`, then drop to `appuser` through `gosu`. Each directory Nojoin writes to under `data/` is created and checked on its own. A mis-owned data root, as on the first boot of a fresh `data/`, gets one recursive pass.

Some bind-mount backends, such as a Windows drive under Docker Desktop, reject or ignore `chown`. The containers still start and report the problem:

- The API logs an error naming the unwritable path.
- **Settings > System and logs** shows the **Storage** check as **Not writable**.
- Uploads and imports fail with a 503.

Make the host directory bound to `/app/data` owned by UID 1000, then restart the api and worker services.

## Temporary Files and the Data Directory

Each container keeps a private `/tmp`, discarded when the container is recreated. The pipeline removes its own scratch files and periodic cleanup tasks remove any a killed worker left behind.

A finished backup archive, built by a worker and served by the API, is the one file that passes between containers. It goes through `data/backups` on the `./data` mount every service has, and stays until a newer one replaces it ([BACKUP_RESTORE.md](BACKUP_RESTORE.md#the-saved-backup)). Uploaded archives and restore staging use `data/temp_uploads` and `data/restore_staging`. `BACKUP_EXPORT_DIR` moves exports elsewhere. The api and every worker must see the same path, so a directory outside `/app/data` has to be mounted into all of them.

## Storage Hygiene

A file in `data/documents/` or `data/recordings/` can outlive every row that names it: a process killed between a delete's commit and its unlink, a failed unlink, or a write whose row never committed. Once a day, `cleanup_temp_recordings` on `worker-io` logs a line counting, for documents, audio, proxies, partial uploads (`.upload-tmp`, `.part`, and a proxy conversion's `.tmp`) and unrecognised files, the files that no row names and their bytes, and the rows whose file is missing. It also lists up to 50 of the files a removal would take. Rows are never changed, and a failure here is logged without failing the rest of the cleanup.

`NOJOIN_STORAGE_HYGIENE=delete` makes the daily run remove those files, logging each one with its size, modification time and category. To see the report and the files, or to remove them now:

```sh
docker compose exec --user appuser worker-io python -m backend.storage_hygiene           # report only
docker compose exec --user appuser worker-io python -m backend.storage_hygiene --delete  # remove them
```

`exec` skips the entrypoint's drop to `appuser`, so `--user appuser` runs the command with the application's own permissions. Stored paths are relative to `/app`, where `exec` starts.

The CLI lists each flagged file with its size above the table, so you can check them before `--delete`: up to 100, or all of them with `--show 0`. A run that removes files, daily or from the CLI, logs each one it removes.

Only names Nojoin writes are ever removed: a lowercase uuid4 with or without an extension, `<name>__<uuid4>` from a restore, `demo_welcome_<id>`, `.restored` audio, and the `.part`, `.upload-tmp` or `.<8 hex>.tmp` a killed write leaves beside one of those. Any other file in these directories is counted as unrecognised and left alone; even so, keep nothing of your own in `data/documents/` or `data/recordings/`. Every run also keeps:

- anything modified in the last 24 hours, and any file that changed between the listing and its unlink;
- any file a row names, soft-deleted recordings included: by its device and inode, or by its file name in any case, wherever the stored path points;
- every file sharing its base name with a recording still uploading or paused, such as the capture master that finalize writes before its row names it;
- `temp/` and `failed/`, which the existing 24-hour sweep owns, and dotfiles and symlinks.

The rows are read once more just before the first file is removed. The circuit breaker removes nothing when any of these holds:

- the `recordings` table is empty while the directories hold files;
- a directory is the data directory, contains it, or holds `config.json`;
- more than half of one column's stored paths (audio, proxies or documents) name missing files, as when `data/` belongs to another database. Recordings still uploading or paused, or in error or cancelled, are not counted, because those can lack their file by design;
- more than half of the paths stored relative to the working directory name missing files, because the run is not where the app runs;
- more than half of a directory's recognised files older than 24 hours are named by no row. The database may not belong to this data directory, and no option lifts this. It is logged as an error when the run would remove files, and as a warning otherwise;
- the run would remove more than 10% of one directory's files, or of its bytes. On a small install one large orphan can trip the bytes share alone. `--allow-bulk <directory> --expect <count>` lifts this rule for that directory only, and only when `<count>` is the "files to remove" count the dry run printed for that directory. One directory over the limit stops the whole run, so give a pair for each;
- the restore lock is set, or Redis cannot be reached to read it. The lock is read before the listing, before the first removal and again every 25 flagged files. It expires after 6 hours, so a restore still running after that is not seen;
- in a run that would remove files and that every rule above lets through, the clock of a directory it would remove files from is more than an hour behind the worker's, or the run cannot check it. The 24-hour grace compares each file's modification time with the worker's clock, but on a network data directory that time may come from another clock: an NFS server stamps it itself (nfs(5), "File timestamp maintenance"), and the Linux SMB client may write its own back when a file is closed. A server clock a day behind would make a file written a moment ago look old. So, after reading the restore lock once more, the run writes a probe, `.nojoin-hygiene-clock`, in each such directory, stamped the same way as Nojoin's own files, reads its modification time back and removes it. On a full volume the empty probe's creation time is used. A directory where the probe cannot be created trips the rule too, and so does one where a probe is already there: another deleting run is under way, or an earlier one could not remove its probe, which you then delete by hand. This is logged as an error: sync the server's clock (NTP), or make the directory writable for `appuser`. A directory with nothing to remove is not probed, a clock running ahead only keeps files longer, and a report-only run, or one another rule stops, writes nothing.

Storage hygiene can only compare the files with the rows of the database it reads, and files that database doesn't know about are removed while they stay under these limits. Keep `NOJOIN_STORAGE_HYGIENE` at `flag` after restoring the database from an older dump, and never share a data directory between installs.

A trip is logged as a warning naming its rules, or as an error where noted above. The CLI exits 0 when the run finished and nothing was refused, 1 when the breaker tripped, 2 on a usage error, 3 when a file could not be removed, and 4 when the run failed with an error.

## Worker Container Startup

At startup the API queues model preparation for Pyannote diarisation, voice embeddings and the install's transcription engine (**Settings > Transcription**). Whisper is prepared only when the engine is Whisper. Preparation validates each model, caches it on disk and releases it, so the GPU stays idle until there is work.

Models live in the `model_cache` volume (`XDG_CACHE_HOME` and `HF_HOME` on the workers), so they survive container recreation. The text embedding model is cached in `$XDG_CACHE_HOME/fastembed`. Set `FASTEMBED_CACHE_PATH` on the workers to keep it elsewhere.

Changing the transcription model downloads nothing by itself. **Settings > Transcription** offers to fetch the new model, and **Model downloads** under **Settings > AI providers** has a **Download** action with progress. A model that was never prepared is fetched on first use, which holds up live transcription and Meeting Edge. One preparation runs at a time, and a second request gets a 409. Deleting a cached model runs on a worker too, because the API mounts `model_cache` read-only, so both actions need running workers.

After each task, workers release cached models and CUDA memory. The exception is while a recording is uploading live, when the live ASR model stays loaded between segments. Final processing releases any live model before it loads diarisation.

`worker-io` runs Celery Beat (`-B`) for the periodic jobs, among them calendar sync, cleanup, search index backfill and telemetry. Its schedule file is `data/celerybeat-schedule`, so the cadence survives restarts. Give `-B` to one lane only, or every periodic job runs twice. Calendar live sync also needs the instance reachable from the internet over HTTPS at `WEB_APP_URL` ([CALENDAR.md](CALENDAR.md)).

### Worker Concurrency Lanes

Worker tasks are split across four Celery queues, each drained by its own container, so a long recording finalise cannot block live transcription, notes, chat or calendar sync:

| Service | Queue | Shipped pool | Work |
| --- | --- | --- | --- |
| `worker-gpu` | `gpu` | `--pool=solo --concurrency=1` | Final processing, live ASR, speaker embeddings, overlap analytics, model preparation. The only lane with GPU access. |
| `worker-cpu` | `cpu` | `--pool=prefork --concurrency=3` | ffmpeg transcodes, playback proxies, imported audio, delivery analytics, backups. |
| `worker-io` | `io` | `--pool=prefork --concurrency=4`, plus Celery Beat | Meeting Edge, notes, chat, transcript indexing, calendar sync, restores, cleanup, model deletion, telemetry. |
| `worker-parse` | `parse` | `--pool=prefork --concurrency=2 --max-tasks-per-child=25` | Document parsing and search index rebuilds. |

`worker-parse` runs the `worker-io` image, because visual parsing can use a subscription CLI that ships only there. It is a separate lane because a parse has no page cap, and one large upload on the IO lane could slow Meeting Edge during a live meeting. Routing is `TASK_ROUTES` in `backend/celery_app.py`, and an unrouted task goes to the GPU lane.

Tune `--concurrency` in `docker-compose.yml` to your host. Keep the GPU lane at 1 unless you have several GPUs or one card that can hold two pipelines. PostgreSQL's default `max_connections` (100) covers the shipped sizing, so raise it if you widen the lanes a lot.

Keep `--pool` at `solo` or `prefork`. Worker code queues follow-on tasks with a blocking call, which is safe only while each task has an operating-system process to itself. A pool that runs several tasks in one process, such as `gevent`, `eventlet` or `threads`, lets one blocked dispatch stall the others, and no test catches the change.

#### Worker Child Recycling

Prefork children are replaced after 500 tasks (`worker_max_tasks_per_child` in `backend/celery_app.py`). A lane's `--max-tasks-per-child` flag overrides that, as `worker-parse` does with 25, because one document can leave a child far heavier than any other task. The setting has no effect on `worker-gpu`, whose solo pool has no children, and does not touch Celery Beat.

A recycle never interrupts or loses a task, and the new child re-imports what its first task needs. Recycling bounds how far an undetected leak can grow. It does not lower steady-state memory. Raise the limit for a lane whose tasks are slow to warm up, and lower it for one whose tasks are memory-hungry.

## CPU-Only Deployment

1. Remove the `deploy` block under `worker-gpu` in `docker-compose.yml`.
2. Start the stack with `docker compose up -d`.

All four lanes then run on the CPU. Processing is slower but everything works.

### Limiting the Workers' CPUs

ONNX Runtime runs Parakeet and Canary (on `worker-gpu`, in a CPU install) and the text embedding model (on `worker-io` and `worker-parse`, in every install). By default it starts one thread per host core, pinned to that core, and it cannot see a container's CPU limit. A worker capped with `cpus:` or `cpuset:` would then run more threads than it has CPUs, which made transcription and embedding 1.5 to 3.6 times slower in testing, and with a cpuset the log fills with `pthread_setaffinity_np failed`.

Nojoin sizes the pool to the limit instead: the CPUs in the worker's affinity mask (`cpuset:`), capped by any cgroup v2 quota (`cpus:`) on the worker or a parent group, rounded up, so `cpus: 1.5` gives two threads. The worker logs `Sizing ONNX Runtime thread pools to N CPU(s)` when it does this. A worker with no limit keeps ONNX Runtime's default. A cgroup v1 host has no quota to read, so limit the worker with `cpuset:` there. Each prefork child on `worker-io` and `worker-parse` loads its own copy of the embedding model and takes the full count.

To set the count yourself, set `ORT_INTRA_OP_NUM_THREADS` in the service's `environment:` in `docker-compose.yml`. `0` restores ONNX Runtime's default. `worker-io` and `worker-parse` use the shared `*worker-environment` block unchanged, so give the one you change its own block that merges it, as `worker-gpu` does. `OMP_NUM_THREADS` has no effect on ONNX Runtime, which is built without OpenMP.

PyTorch does read `OMP_NUM_THREADS`. On a CPU install it runs diarisation and voice embeddings on `worker-gpu`, and the shipped file sets the variable to 2 on every worker. Diarisation speed scales with it, so to use a larger CPU allowance, set `OMP_NUM_THREADS` to match in `worker-gpu`'s own `environment:` block.

## Configure .env

`.env`, copied from `.env.example`, is the operator configuration. Compose derives the internal PostgreSQL, Redis and Celery URLs itself. A variable reaches a container only if that container's compose service declares it under `environment:`. The shipped template declares every variable in this section unless a table says otherwise. If you maintain your own compose file, a variable set in `.env` but not declared is discarded without a warning and the application default applies.

Nojoin generates its JWT signing keyring at `data/.secret_keys.json` ([JWT Signing Key Rotation](SECURITY.md#jwt-signing-key-rotation)). If that file is empty or unreadable, the api still starts but nobody can sign in, and the api log names the file. Nojoin can also generate `data/.data_encryption_key`, but treat that as a fallback and set `DATA_ENCRYPTION_KEY`.

Restart after changing `.env` (`docker compose up -d`). The LLM provider, Ollama URL and Ollama context window variables take precedence over the same settings in `data/config.json`.

Put each comment on a line of its own. Compose strips a comment that follows a value, so `KEY=value # note` sets `value`, but with nothing before the `#` it keeps the comment: `KEY= # note` sets the variable to `# note`. The `.env.example` of earlier releases had up to nine lines of that shape, and for those nine variables Nojoin reads a value that starts with `#` as empty, and the api and each worker log a warning naming them when they start ([Version Notes](#unreleased-changes-on-main)). Any other variable takes the comment as its value.

### Always Set

| Variable | Purpose |
| --- | --- |
| `FIRST_RUN_PASSWORD` | Password for the setup wizard at `/setup` on an uninitialised system. Unused once setup is done. |
| `DATA_ENCRYPTION_KEY` | Encrypts calendar OAuth client secrets, users' calendar tokens and subscription (CLI OAuth) credentials. See [DATA_ENCRYPTION_KEY Guidance](#data_encryption_key-guidance). |
| `POSTGRES_PASSWORD` | Database password. Set it before the first start: PostgreSQL keeps the password its data directory was initialised with. |
| `REDIS_PASSWORD` | Redis password. |

`POSTGRES_USER` and `POSTGRES_DB` (`postgres` and `nojoin`) can also be changed before the first start.

### Change for Remote or Reverse-Proxy Deployments

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEB_APP_URL` | `https://localhost:14443` | The exact public browser origin. Used for invitation links, calendar OAuth callbacks and webhooks, the CORS allowlist and the trusted host list. |
| `NOJOIN_TRUSTED_PROXIES` | `127.0.0.1,::1,nginx` | IPs, CIDR blocks or hostnames of proxies whose `X-Forwarded-For` is trusted when identifying clients for rate limiting. Add your edge proxy ([Trusted Proxy IPs](#trusted-proxy-ips-for-rate-limiting)). |
| `NOJOIN_BIND_ADDRESS` | `127.0.0.1` | Host address the bundled proxy publishes ports `14141` and `14443` on ([Loopback Port Binding](#loopback-port-binding)). |

### Common Optional Values

AI providers:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `gemini` | The install's default provider: `gemini`, `openai`, `anthropic` or `ollama`. Empty means `gemini`. Shown read-only in **Settings > AI providers**. |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | | Keys for the hosted providers. |
| `OLLAMA_API_URL` | `http://host.docker.internal:11434` | Ollama endpoint. |
| `OLLAMA_CONTEXT_WINDOW` | `32768` | Ollama `num_ctx` for full-meeting prompts. See below. |
| `SECONDARY_LLM_PROVIDER` | Empty | Fallback provider used when the primary fails. Same values as `LLM_PROVIDER`. Empty turns the fallback off. |
| `SECONDARY_GEMINI_API_KEY`, `SECONDARY_OPENAI_API_KEY`, `SECONDARY_ANTHROPIC_API_KEY`, `SECONDARY_OLLAMA_API_URL`, `SECONDARY_OLLAMA_CONTEXT_WINDOW` | As the primary | The same settings for the fallback provider. |
| `HF_TOKEN` | Empty | Only needed to refresh the bundled Pyannote assets from Hugging Face. |

`OLLAMA_CONTEXT_WINDOW` covers about a two-hour meeting (25,000 to 30,000 tokens) at its default. Size it against VRAM, not the model's advertised maximum: the KV cache costs roughly 190 KiB per token on a 14B model, so `32768` needs about 6 GB on top of the weights and `131072` about 24 GB. Ollama refuses to load a model whose cache does not fit. Nojoin treats the value as a ceiling and lowers it to the model's trained context length when that is smaller. The window holds prompt and answer together, and a truncated result is never saved. [Long Meetings And Ollama](USAGE.md#long-meetings-and-ollama) describes what happens to a longer meeting; with Ollama as the primary, a hosted fallback provider receives such a meeting whole. This needs Ollama 0.12.6 or later. On older servers truncation is detected only after the call, so a streamed chat answer is withdrawn after it was shown.

Calendar ([CALENDAR.md](CALENDAR.md)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | | Google calendar OAuth client. |
| `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET` | | Microsoft calendar OAuth client. |
| `MICROSOFT_OAUTH_TENANT_ID` | `common` | Microsoft tenant. Use `common` only when the app registration supports that sign-in model. |

MCP and telemetry:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_ENABLED` | `true` | `false` turns off the built-in [MCP connector](MCP.md): `/mcp`, its OAuth discovery documents and the connector authorisation endpoints all return 404. |
| `MCP_ANONYMOUS_DISCOVERY` | `true` | Answers the MCP handshake and tool listing without authentication, so clients that cannot start OAuth from a `401` (Codex Desktop) can connect. Only generic tool metadata is exposed, and no tool runs without a signed-in user. `false` requires authentication for every request. |
| `NOJOIN_TELEMETRY_ENABLED` | Empty | Empty sends the daily install heartbeat and leaves usage diagnostics to **Settings > Privacy**. `false` stops both, and `true` opts in to both. Either value makes the Settings toggle read-only and suppresses the one-time admin notice. The heartbeat goes out within fifteen minutes of starting, so set `false` before the first start if nothing may ever be sent. See [TELEMETRY.md](TELEMETRY.md). |
| `NOJOIN_TELEMETRY_ENDPOINT` | Empty | Test override for the diagnostics ingest URL. The heartbeat goes to `heartbeat` beside it. Not in `.env.example`. |

Operations:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_TIMEZONE` | `UTC` | Timezone for users who have not saved their own. |
| `BACKUP_EXPORT_DIR` | `data/backups` | Where exported backup archives are written ([Temporary Files](#temporary-files-and-the-data-directory)). |
| `NOJOIN_UMASK` | `0077` | Umask for the application processes. The default gives owner-only files (`0600`) and directories (`0700`). |
| `NOJOIN_STORAGE_HYGIENE` | `flag` | What the daily cleanup does with document, audio and proxy files that no row names: `flag` logs them, `delete` removes them ([Storage Hygiene](#storage-hygiene)). |
| `NOJOIN_STALL_WATCHDOG_ENABLED` | `true` | Logs API stalls and host pressure ([Stall Detection](#stall-detection)). |
| `WHISPER_ENABLE_WORD_TIMESTAMPS` | `true` | Whisper word timestamps, which let the merge assign speakers word by word. |
| `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES` | `all`, `compute,utility` | GPU passthrough for `worker-gpu`. |
| `NOJOIN_CODEX_PATH` | `/usr/local/bin/codex` | Codex CLI path on the workers ([CLI OAuth](#cli-oauth-worker-io-image)). Not in `.env.example`. |

Not in `.env.example` and not passed by the shipped compose file. Set them in the service's `environment:` block:

| Variable | Service | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | `api` | Replaces the JWT keyring with one static key ([SECURITY.md](SECURITY.md#jwt-signing-key-rotation)). |
| `ORT_INTRA_OP_NUM_THREADS` | Workers | ONNX Runtime thread count ([Limiting the Workers' CPUs](#limiting-the-workers-cpus)). |
| `FASTEMBED_CACHE_PATH` | `worker-io`, `worker-parse` | Text embedding model cache. |
| `NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER`, `NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE` | `api` | Debugging only ([DEVELOPMENT.md](DEVELOPMENT.md)). |

### DATA_ENCRYPTION_KEY Guidance

- Set it before anyone connects a calendar or a subscription, or an admin stores calendar provider secrets.
- On a deployment that already has `data/.data_encryption_key`, copy that file's value into `DATA_ENCRYPTION_KEY` before restarting.
- Keep it unchanged across restarts, upgrades, image changes and host migrations, and store it with your other secrets.
- Changing it makes stored calendar credentials and subscription tokens unreadable.

### Custom Frontend Build Value

`NEXT_PUBLIC_API_URL` is a frontend build argument. Set it only when building your own frontend image whose API is not at the same-origin `/api` path. The app's Content-Security-Policy then allows that URL's origin for API requests, recording playback and the images of notes' files.

## Stall Detection

The API watches its own process for stalls and logs them as pipeline metrics. A stall during a live recording loses that stretch of audio, because the browser keeps counting time while nothing reaches the server. [ARCHITECTURE.md](ARCHITECTURE.md#stall-detection) explains how it works.

Two events appear in `docker compose logs api`:

- **`event_loop_stalled`** carries `lag_s`, the seconds by which a fixed sleep overshot, and `pressure_avg10` where the kernel provides it. More than a second or two means requests were queueing behind something. A high `memory_some` or `io_some` alongside it means the host was the cause, not the API.
- **`host_pressure_high`** means the host spent a large share of the last ten seconds with tasks blocked, without the API stalling. Treat it as an early warning that the host is under-resourced. It is logged at most once a minute.

Both are quiet on a healthy deployment. If either appears during meetings, check host memory and swap first. Pressure figures need `/proc/pressure`, which only Linux kernels built with PSI provide. Without it, lag is still detected but not attributed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `NOJOIN_STALL_WATCHDOG_ENABLED` | `true` | `false` turns stall detection off. |
| `NOJOIN_STALL_WATCHDOG_INTERVAL_SECONDS` | `5` | How often event-loop lag is measured. |
| `NOJOIN_STALL_WATCHDOG_LAG_THRESHOLD_SECONDS` | `1` | Overshoot above which a stall is reported. Below a second, ordinary scheduling produces noise. |
| `NOJOIN_STALL_WATCHDOG_PRESSURE_THRESHOLD` | `20` | Percentage of the last ten seconds spent blocked above which host pressure is reported on its own. |

Set any of them in `.env`. Only `NOJOIN_STALL_WATCHDOG_ENABLED` is listed in `.env.example`.

## Configuration Model

User settings live in the database. Install-wide settings an administrator changes in the UI, such as AI models, the transcription engine and model, the install glossary, the default notes structure and export branding, are written to `data/config.json`, so `data/` must be writable by UID 1000. If it is not, saving them fails with an error. Workers re-read `config.json` before every task, so a hand edit reaches the next task. Restart the api to be sure it sees the edit too.

First-run setup can pre-fill values from environment variables, and that prefill is also locked behind `FIRST_RUN_PASSWORD`. Setup writes the wizard's Whisper model size to `config.json`, except that the wizard default (`turbo`) does not replace a different size already in a pre-seeded file.

### Processing Tuning Defaults

Speech and speaker tuning is per user (**Settings > Recording > Advanced**, **Speech and speaker tuning**), and each value inherits by default. Set an installation default for any key as a flat entry in `data/config.json`. It applies to every user who has not set their own:

| Key | Shipped default | Accepted range |
| --- | --- | --- |
| `vad_threshold` | 0.5 | 0.15 to 0.90 |
| `asr_word_length_cap_s` | 0.4 | 0.2 to 0.45 |
| `phantom_max_duration_s` | 3.0 | 0 to 10 |
| `phantom_max_segments` | 3 | 0 to 20 (whole number) |
| `phantom_embedding_floor` | 0.35 | 0 to 0.95, below `phantom_merge_threshold` |
| `phantom_merge_threshold` | 0.60 | 0.05 to 1.0 |
| `word_flip_max_duration_s` | 0.45 | 0 to 2.0 |
| `word_flip_max_gap_s` | 0.25 | 0 to 1.0 |

`asr_word_length_cap_s` is the longest a Parakeet word can last. Its maximum is the default `word_flip_max_duration_s`, so at default smoothing every capped word is short enough to smooth. It replaces the retired `asr_word_end_padding_s` ([Version Notes](#version-notes)).

The legacy `vad_parameters.threshold` in `config.json` still applies, below both a user's `vad_threshold` and a flat one. The worker logs and ignores a value outside its range, or a phantom floor that is not below the phantom merge threshold, and uses the shipped default instead (for the VAD threshold, `vad_parameters.threshold`), so a typo never fails a recording.

A user's own value replaces the flat key entirely, so an unusable per-user value (edited by hand, restored from an older backup, or stored before a range narrowed) falls back to the shipped default, not the installation's. The settings page shows it as empty and the user's next save clears it. The page shows only a user's own values, with the shipped default as the placeholder. A user's save of either phantom similarity is checked against the pair processing will use, counting an empty half as the installation's value. A conflict between the installation's own two values blocks no one's save.

### Live Speaker Labels

`live_speaker_labels` in `data/config.json` turns on provisional speaker labels in the live transcript ([USAGE.md](USAGE.md#live-transcription), [ARCHITECTURE.md](ARCHITECTURE.md#live-transcription-lane)). There is no settings page for it.

- `off`, the shipped default: nothing runs and no model loads.
- `auto`: labels run on `worker-gpu` when it is on CUDA (`processing_device` is `auto` or a `cuda` device, and a GPU is present). On a CPU-only host they stay off, because the model pass alone needs about a third of real time on 16 CPU threads.

Any other value is off. Workers re-read `config.json` before each task, so a change reaches the next live segment, and labels turned on mid-meeting number the speakers from that point.

On a 12 GB RTX 4070 Ti, labels take about 32 ms of the GPU worker's time per 2 s of audio (p95 about 90 ms, when a re-cluster runs) and add about 450 MiB of VRAM to a capture, since the live lane keeps the segmentation and speaker embedding models loaded beside the transcription model. The first segment of a capture also loads them, about 2 s. On the shipped single GPU lane a final pass releases every model before it starts and after it ends, so it never runs beside them: with Parakeet, the lane's peak rose from 3,890 to 4,140 MiB, set by a capture rather than the final pass. With two GPU lanes on one card, a final pass and a labelled capture running at once peaked at about 8,030 MiB. The engine's state sits in the recording's `temp/<id>/live/` and is deleted at finalise: a file rewritten every 2 s, about 0.35 MB plus 2 KB per speaker (0.5 MB after two and a half hours), and an append-only bank of 1 KB per speaker turn (2.4 MB). Each save syncs both to disk, about 60 ms on a RAID array. A re-cluster handles at most 1,000 recent turns plus 10 older ones per speaker, so its cost does not grow with the meeting (about 0.3 s every 10 s with 40 speakers, against 2.6 s at 8,000 turns unbounded).

### Screen Study

`speaker_frame_sampling` in `data/config.json` allows the screen study ([CAPTURE.md](CAPTURE.md#screen-study)): frames of the shared screen, kept on the server for an offline measurement of speaker labels, only for meetings started with the box ticked. There is no settings page for it.

- `off`, the shipped default: nobody is offered the study, and every one of its endpoints answers 404.
- `owner`: the installation's first Owner account, the one created at first-run setup, sees **Screen study** under **Start Meeting**. No other account does.

`speaker_frame_rate_hz` sets how often the browser looks at the shared screen: `1` (the default) or `2` times per second of audio. Any other value of either key reads as its default. The api re-reads `config.json` on each study request, so an edit reaches the next request without a restart. A page already open offers the box once it is reloaded.

To allow it, add the key to `data/config.json`, keeping the file valid JSON and owned by UID 1000:

```json
"speaker_frame_sampling": "owner"
```

Frames live in `data/speaker_frames/<recording public id>/` as `<sequence>-<offset_ms>.jpg`, mode 0600, with an `index.jsonl` beside them: `sequence` is the audio chunk a frame was taken in and `offset_ms` the offset into it, so `recording_audio_chunks.absolute_start_ms + offset_ms` puts the frame on the transcript's clock. Each index line also holds the frame's size and SHA-256, `tick` (the capture's audio tick it was sampled on, from 0), `lost_before` (ticks since the previous stored frame that were not sampled, or whose frame was dropped or failed to upload, so a still screen is not mistaken for a gap), `frame_ts_us` (the video frame's own timestamp) and `grab_delay_ms` (from the audio tick to the grab). `platform_hint` is always `null` for now. `data/speaker_frame_state/` records when each recording stopped and whether its frames were deleted, and holds a lock file per recording. A frame is kept only when the screen changes, or every 15 seconds while it does not, so storage depends on the meeting. Once all frames together reach 20 GB, or storing one would leave less free disk space than uploads keep (2 GB by default, `DOCUMENT_DISK_HEADROOM_BYTES`), no more are kept. An hourly task on `worker-io` deletes frames 14 days after their recording stops, and keeps doing so with the key set to `off`.

The bundled proxy leaves the study's per-recording requests out of its access log, as the api does. A reverse proxy of your own logs them like any other request unless you exclude `/api/v1/recordings/<id>/speaker-frames`.

To end a study, set the key to `off`, then delete the frames and check that none are left:

```bash
docker compose exec --user appuser api python -m backend.utils.speaker_frames purge --all
docker compose exec --user appuser api python -m backend.utils.speaker_frames audit
docker compose exec --user appuser api python -m backend.utils.speaker_frames audit --scan /app/data
```

`purge` (or `purge <public id>` for one recording) first writes the SHA-256 of every frame it deletes to a new list in `data/speaker_frame_hashes/`. `audit` lists the frame directories with their counts and sizes, the state files and the hash lists, and never any frame content. `--scan` also hashes every JPEG under a directory, the members of ZIP files such as backups included, names each one that matches a saved hash, and then exits with status 1. Delete `data/speaker_frame_hashes/` once you are satisfied.

## CLI OAuth (worker-io image)

The per-user CLI OAuth AI mode routes a user's inference through their own Claude or ChatGPT subscription. It needs Node.js, the Claude Code CLI and the OpenAI Codex CLI, which ship only in the `worker-io` image (`docker/Dockerfile.worker-io`, built on the shared worker image). The shipped compose file runs `worker-io` and `worker-parse` on that image, including for the first-run wizard's AI step, and `worker-gpu` and `worker-cpu` on the base image. If your compose file runs `worker-io` on the base image, users can still connect a Claude subscription, but inference through it fails and falls back to the server's providers. The stored credential is encrypted with `DATA_ENCRYPTION_KEY`, and no other variable is needed.

The Codex CLI is a stripped native binary the image scanner cannot inspect, so its version is checked against the GitHub advisory database instead ([SECURITY.md](SECURITY.md#cli-oauth-subscription-access)). If you need an image whose contents were all scanned, leave the ChatGPT route unused, or run `worker-io` on the base image and do without subscription inference.

## Remote Access and Trusted Public Origin

Read this to reach Nojoin from another device: a computer on the LAN, a laptop over a VPN or tailnet, or a phone. [Reverse Proxy Requirements](#reverse-proxy-requirements) covers the proxy itself.

- Set `WEB_APP_URL` to the exact origin users will visit and restart the stack. The API adds that origin to its CORS allowlist.
- Keep the browser origin, the proxy's public origin and the OAuth callback origin the same.
- Serve the remote origin over HTTPS, which live capture requires.
- Prefer a VPN or tailnet to exposing Nojoin on the internet. If you do expose it, treat `FIRST_RUN_PASSWORD` as a secret and do not log request headers during setup.

### HTTPS Is Required for Live Capture

Browsers expose the microphone and screen-sharing APIs only to a secure context: an HTTPS origin or `localhost`. On a plain HTTP origin anywhere else, `navigator.mediaDevices` is absent and Nojoin reports the browser as unsupported for capture. Reviewing recordings, playback, transcript editing, search and administration still work. Starting a live recording does not.

Any remote arrangement must therefore terminate TLS in front of Nojoin. The bundled proxy's HTTP port, `14141`, only redirects to HTTPS on the standard port 443. The API enforces HTTPS as well: a GET, HEAD or OPTIONS request it cannot recognise as proxied HTTPS is redirected to `WEB_APP_URL`, and any other method is rejected with `400 Plain HTTP requests are not allowed. Use HTTPS.`

## Reverse Proxy Requirements

These apply to Nginx, Caddy, Traefik, Tailscale Serve or any other proxy in front of Nojoin.

### Loopback Port Binding

The bundled proxy publishes ports `14141` and `14443` on `127.0.0.1` by default (`NOJOIN_BIND_ADDRESS`). An edge proxy on the same host can reach it, and nothing on the network can bypass that edge proxy's authentication, rate limiting or filtering. Without an edge proxy, set `NOJOIN_BIND_ADDRESS=0.0.0.0` (or one host address) in `.env` and restart to let other hosts reach the bundled proxy directly, and restrict the ports with host firewall rules such as `ufw` or `iptables`.

### Forwarding Checklist

1. Proxy to the bundled proxy's HTTPS port, `14443` by default, not the HTTP port.
2. Skip upstream certificate verification, because the internal certificate is self-signed.
3. Set `WEB_APP_URL` to the public origin.
4. Forward the public hostname: the upstream `Host` and `X-Forwarded-Host` must match the hostname in `WEB_APP_URL`.
5. Send `X-Forwarded-Proto: https`.
6. Serve the whole site through that one origin, including `/mcp` and `/.well-known/oauth-*` for the [MCP connector](MCP.md). The bundled proxy already routes those paths to the API.
7. Stream responses without buffering, forward WebSocket upgrades, and allow long-lived connections and large request bodies ([below](#streaming-websocket-and-upload-forwarding)).
8. Add the edge proxy to `NOJOIN_TRUSTED_PROXIES` ([below](#trusted-proxy-ips-for-rate-limiting)).
9. If the proxy keeps an access log, mask the one-time codes some request URLs carry ([SECURITY.md](SECURITY.md#operator-rules)). Nginx's default log records them, and Caddy logs nothing unless a `log` directive is set.

### Why the Host and Proto Headers Matter

The API is reached over plain HTTP inside the Docker network, so it treats a request as HTTPS only when all of these hold:

- The immediate peer is a private, loopback or link-local address, as the bundled proxy is.
- `X-Forwarded-Proto` is `https`.
- The `Host` hostname matches the hostname in `WEB_APP_URL`.
- The `X-Forwarded-Host` hostname matches the hostname in `WEB_APP_URL`.

Getting this wrong fails in one of two ways:

- **`400 Invalid host header`** means the `Host` hostname is not trusted. Only `localhost`, `127.0.0.1`, `::1` and the hostname in `WEB_APP_URL` are. The usual cause is an edge proxy forwarding an internal host such as `nojoin-nginx:443`, or `WEB_APP_URL` not updated to the public hostname and the stack not restarted.
- **A redirect loop on page loads, with `400 Plain HTTP requests are not allowed. Use HTTPS.` on saves,** means the host is trusted but another condition fails, most often a rewritten `Host` or a missing `X-Forwarded-Proto`. Reads are redirected to `WEB_APP_URL`, which the proxy rewrites the same way again, and writes are rejected. Check these headers first.

`/health` and `/api/health` are exempt, so health checks work over plain HTTP.

### HSTS and Other Security Headers

The bundled proxy sends `Strict-Transport-Security: max-age=31536000` on every HTTPS response, without `includeSubDomains`, so it covers Nojoin's own hostname only. Browsers ignore it over the self-signed internal certificate and apply it once an edge proxy with a trusted certificate passes it on. Caddy and Nginx pass upstream response headers through, so the examples below need nothing extra, and the same goes for the CSP and other security headers listed in [SECURITY.md](SECURITY.md#browser-session-request-protection).

Do not add a second `Strict-Transport-Security` header at the edge, because a browser reads only the first. To set `includeSubDomains` or preload for your whole domain, set it at the edge and remove the upstream one with `header_down -Strict-Transport-Security` in Caddy or `proxy_hide_header Strict-Transport-Security;` in Nginx.

### Streaming, WebSocket, and Upload Forwarding

Browser capture uploads its segments as ordinary HTTP requests. Three things need proxy support:

- **Server-sent events.** Meeting chat answers stream as `text/event-stream`. A buffering proxy holds the whole answer until it ends, so replies arrive all at once or time out. Turn off response buffering and allow read timeouts of several minutes. The bundled proxy uses `proxy_buffering off` and 300 second timeouts.
- **WebSocket upgrades.** Only `/api/v1/system/logs/live`, the live log viewer in **Settings > System and logs**, uses a WebSocket. Without upgrades, that panel fails to connect and nothing else is affected.
- **Request body size.** The bundled proxy accepts bodies up to 500 MB. A smaller limit at the edge rejects uploads with `413`. Nginx defaults to 1 MB, below the 15 MB live capture segment limit and far below document and backup uploads.

Caddy streams responses, forwards upgrades and has no body limit by default. Nginx needs all three configured.

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

Put the `map` block in the `http` context, outside `server`. It sends `Connection: upgrade` only on WebSocket requests.

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

Tailscale Serve publishes Nojoin to your tailnet over HTTPS with a Tailscale-issued certificate for the node's MagicDNS name, without opening router ports. It suits a GPU workstation at home that you reach from a laptop elsewhere. Serve runs on the host in front of the stack like any edge proxy, and Nojoin does not manage it.

- **Same tailnet only.** The accessing device must be signed in to your tailnet. A device that cannot run Tailscale cannot reach Serve. Funnel, which publishes to the whole internet, is not recommended: for public access, follow [Remote Access and Trusted Public Origin](#remote-access-and-trusted-public-origin) as for any internet-exposed deployment.
- **Leave `NOJOIN_BIND_ADDRESS` at `127.0.0.1`.** Serve runs on the host, so it reaches the loopback port and the bundled proxy stays off the LAN.
- **Target `https+insecure://` on port `14443`.** That form accepts the self-signed internal certificate (checklist items 1 and 2).
- **Set `WEB_APP_URL` to `https://<node>.<tailnet>.ts.net`** and restart the stack. Until then the API answers `400 Invalid host header`.
- **No header changes.** Serve preserves `Host` and sets `X-Forwarded-Host`, `X-Forwarded-Proto: https` and `X-Forwarded-For`, and the bundled proxy passes `Host` through and handles streaming, upgrades and body size.
- **Trust the Docker bridge gateway** for rate limiting ([Host-Run Edge Proxies](#host-run-edge-proxies-tailscale-serve-and-similar)).
- **Tailnet identity is not sign-on.** Nojoin ignores Serve's `Tailscale-User-*` headers and supports no proxy-header authentication, so users sign in to Nojoin as usual.

The tailnet certificate is browser-trusted, so the origin is a secure context and live capture works from the remote device. Serve needs HTTPS certificates enabled for the tailnet in the Tailscale admin console.

### Trusted Proxy IPs for Rate Limiting

Nojoin finds each client's address by walking the `X-Forwarded-For` chain back through the proxies in `NOJOIN_TRUSTED_PROXIES` ([Change for Remote or Reverse-Proxy Deployments](#change-for-remote-or-reverse-proxy-deployments)). By default only the bundled proxy is trusted.

A hostname entry works only if the API container can resolve it, and Docker resolves container names only on networks the API is attached to. Trust an edge proxy on a different Docker network by its IP or subnet. For example, for Caddy on a shared `proxy_net` in `172.18.0.0/16`:

```dotenv
NOJOIN_TRUSTED_PROXIES=127.0.0.1,::1,nginx,172.18.0.0/16
```

An entry that does not resolve, such as a bare `caddy` from another network, is dropped. The walk then stops at the proxy's own address, and every remote client shares one bucket for the per-IP limits on sign-in, invitations, `/setup` and MCP registration. If an entry still does not resolve two minutes after the API starts, the API logs a warning with the number of such entries. The wait is for the bundled proxy, which starts only once the API and the web app report healthy, so `nginx` cannot resolve while the API is starting.

#### Host-Run Edge Proxies (Tailscale Serve and Similar)

A proxy running on the host rather than in a container, such as Tailscale Serve, a system Nginx or Caddy, or an SSH tunnel, reaches the stack through the published loopback port. Docker's port publishing rewrites the source address, so the bundled proxy usually records the **Docker bridge gateway** of Nojoin's network as the peer, and every remote client again shares one rate-limit bucket.

Look up the gateway address:

```bash
docker inspect nojoin-nginx \
  --format '{{ range $name, $net := .NetworkSettings.Networks }}{{ $name }} {{ $net.Gateway }}{{ printf "\n" }}{{ end }}'
```

On a stock deployment that prints one line, for Nojoin's network. Trust that address, or its subnet:

```dotenv
NOJOIN_TRUSTED_PROXIES=127.0.0.1,::1,nginx,172.19.0.1
```

The subnet form (`172.19.0.0/16`) survives a new gateway address if the network is recreated, and the range holds only Nojoin's containers and the gateway. The bundled proxy's access log shows the address it records for a remote request:

```bash
docker compose logs --tail 20 nginx
```

Leaving this unset is not a security problem. Per-IP limits then count every remote user as one client, which a small private tailnet may accept.

## Image Trust and Supply Chain

Nojoin publishes `ghcr.io/valtora/nojoin-api`, `nojoin-worker`, `nojoin-worker-io` and `nojoin-frontend`. Each release is tagged with its version (`2.10.0`) and commit (`sha-<7 characters>`), and the rolling `major.minor` (`2.10`) and `latest` tags then move to it.

- **Pinned inputs:** Base images, including the BuildKit image that builds the release, are pinned by `@sha256:` digest, and the release workflow's actions by commit SHA. Dependabot proposes updates weekly. Each passes CI before merging, and reaches published images only in a new release.
- **Checks before publishing:** Every image passes a Trivy vulnerability scan, the api and frontend images boot and pass their healthchecks, and every image's long-running process is checked to run as a non-root user, before anything is pushed.
- **Signed images:** Every image is signed with keyless [cosign](https://github.com/sigstore/cosign), bound to the release workflow's GitHub identity. The workflow signs an image only after checking that its revision label names the release tag's commit.
- **Provenance and SBOM:** Every image carries BuildKit provenance and SBOM attestations. Images are built on the maintainer's release host, so the provenance records the source commit, build arguments and pinned bases, with no GitHub runner identity.

[ARCHITECTURE.md](ARCHITECTURE.md#release-model) describes the release pipeline.

### Verifying an Image Before Deploying

Verify the cosign signature, replacing the tag as needed. Use cosign 3.0 or later: cosign 2 reports `no signatures found` for these images.

```bash
cosign verify ghcr.io/valtora/nojoin-api:latest \
  --certificate-identity-regexp "^https://github.com/Valtora/Nojoin/.github/workflows/release.yml@.*$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The provenance and SBOM are BuildKit attestations in the image index, not cosign attestations, so read them with `docker buildx imagetools` rather than `cosign verify-attestation`:

```bash
docker buildx imagetools inspect ghcr.io/valtora/nojoin-api:latest --format '{{ json .Provenance }}'
docker buildx imagetools inspect ghcr.io/valtora/nojoin-api:latest --format '{{ json .SBOM }}'
```

Pin a deployment to the digest you verified (`ghcr.io/valtora/nojoin-api@sha256:...`) to run exactly that image.

## Updating a Deployment

Stop the stack before upgrading, so no worker from the old release runs against the migrated database:

```bash
docker compose down
git pull
docker compose pull
docker compose up -d
```

`git pull` updates the bundled proxy's configuration in `nginx/`, which the images do not carry. It leaves your `docker-compose.yml` and `.env` alone, since they are your own untracked copies, so compare them with `docker-compose.example.yml` and `.env.example` when the release notes say a template changed. If you have edited a tracked file such as `nginx/nginx.conf`, `git pull` stops and names it: commit or stash the edit first.

The api runs database migrations when it starts. Check the release notes for breaking changes and [Upgrading and Migration](#upgrading-and-migration) for notes on your version, and make a short test recording afterwards.

If your `docker-compose.yml` has its own `build:` directives, run `docker compose build` in place of `pull`. `worker-io` is built `FROM` the shared worker image through a named build context, `worker_base`, so Compose builds the base first and rebuilds `worker-io` when it changes. The example file points that context at the published `nojoin-worker:latest`. A full source build points it at `service:worker-gpu` and gives the worker services `build:` stanzas.

**Settings > Updates** shows the installed and latest published versions ([ADMIN.md](ADMIN.md#updates)). The installed version comes from build metadata in the API image, and a local source build falls back to `docs/VERSION`.

## Upgrading and Migration

### File Permissions

- **TLS key:** `nginx/cert.key` is created with mode `600`. A key generated by an older release may be `644`. Restrict it with `chmod 600 nginx/cert.key`.
- **Data files:** Recordings, keys, logs, documents and configuration default to owner-only permissions, and a startup pass secures existing files under the data directory, skipping symbolic links. To match on the host, run `chmod -R 700 ./data`. If a host integration needs group or world read access, set `NOJOIN_UMASK`, for example `0022` or `0002`.

### Version Notes

These run on the first start after the upgrade and need no action unless a note says so.

#### Unreleased Changes on Main

- **Screen study (#653).** Two new keys in `data/config.json`, `speaker_frame_sampling` (`off` by default) and `speaker_frame_rate_hz` (`1`), and an hourly `sweep_speaker_frames_task` on `worker-io` ([Screen Study](#screen-study)). Nothing changes until the first key is set to `owner`. `nginx/access-log.conf` leaves the study's frame requests out of the bundled proxy's access log; it reaches an existing install through the `git pull` in [Updating a Deployment](#updating-a-deployment) and a restart of `nginx`.
- **Companion pairing tables on a new install (#724).** Alembic revision `4a8e2f5b6c71` now drops `companion_pairings` and `companion_pairing_requests` without `NOJOIN_ALLOW_COMPANION_DROP` when both are empty, as they are on every new install, and no longer logs `Skipping companion table drop` there. An install that has already run the revision keeps the tables it had, which nothing reads.
- **Files on notes (#653).** Alembic revision `c4d81e6a2f90` adds `documents.note_id`, unique, a foreign key to `timeline_notes` with `ON DELETE CASCADE`, and `documents.use_in_notes`, `false` by default. Every existing row keeps `note_id` empty, so attached documents behave as before. A downgrade drops both columns, and files on notes stay on their meetings as ordinary documents, read by every notes run from then on. A newer worker fails on the missing columns until the API has migrated, so upgrade the API and the workers together.
- **Speaker names with sources (#487).** Alembic revision `0f3b502561a6` adds the table `speaker_name_events` and the nullable column `recording_speakers.name_source`, with no backfill: names given before the upgrade show as they did, with no "?", and their links to People still teach voiceprints. A downgrade drops both, and backups leave the table out ([BACKUP_RESTORE.md](BACKUP_RESTORE.md#what-is-not-included)). Every user starts with `voiceprint_learning` at `confirmed` and `confirm_suggested_names` off, so from the first run after the upgrade, a suggested name waits for **Confirm** and only confirmed names update a person's voiceprint ([USAGE.md](USAGE.md#speaker-management)). A newer worker fails on the missing column until the API has migrated, so upgrade the API and the workers together.
- **Language detection notice (#671).** Alembic revision `b13e4912f23a` adds the nullable column `transcripts.language_detection`, with no backfill, so recordings processed earlier never show the notice. A downgrade drops it. With Whisper and VAD on, every run now detects a language, also when one is set: one more `detect_language` call per recording, about 0.14 s on a GPU and a few seconds on a CPU. As with the next note, upgrade the API and the workers together.
- **A language per recording (#671).** Alembic revision `76243f67da02` adds the nullable column `recordings.transcription_language`, with no backfill: every existing recording follows its owner's transcription language as before. A downgrade drops it. A newer worker fails on the missing column until the API has migrated, so upgrade the API and the workers together.
- **Each tag held once on a meeting (#592).** Alembic revision `fb5a849e7f5b` deletes every repeated link between a meeting and a recording tag, keeping the oldest, then adds the unique constraint `unique_recording_tag` on `recording_tags (recording_id, tag_id)`. The api log gives the number of links deleted. A downgrade drops the constraint and leaves the deleted links deleted.
- **Each tag name held once per user (#578).** Alembic revision `edb5059e5d68` merges each user's repeated recording tag or people tag names, compared exactly, into the oldest tag of that name. The other copies' meetings, tasks, people and sub-tags move to it and the copies are deleted. The oldest tag keeps its colour and its place, unless it sits beneath one of the copies, when it takes that copy's place. The revision then adds a unique index on `(user_id, name)` to `tags` and `p_tags`. The api log lists the merged ids, never names. A downgrade drops the indexes and leaves the tags merged.
- **Live speaker labels (#487).** A new `live_speaker_labels` key in `data/config.json`, `off` by default ([Live Speaker Labels](#live-speaker-labels)). Nothing changes until it is set to `auto`. `requirements/worker.txt` now pins scipy, which the worker image already carried.
- **Your notes became timeline notes (#494).** Alembic revision `63005cac6492` turns each non-blank `transcripts.user_notes` into a head note authored by the recording's owner and drops the column. A downgrade folds every note back into that column in timeline order, losing each note's kind, time, quote and author. The `/transcripts/{recording_id}/user-notes` routes are removed; clients use `/transcripts/{recording_id}/timeline-notes` ([Timeline Notes](ARCHITECTURE.md#timeline-notes)). MCP `get_meeting_notes` returns a `timeline_notes` list in place of `user_notes`, and `append_meeting_notes` adds one tail note per call and returns it as `note`. Backups are written in format 3, which earlier releases refuse; restoring an older archive turns its notes into a head note, as the migration does. Stop the stack before upgrading, as in [Updating a Deployment](#updating-a-deployment): a worker from an earlier release still reads the dropped column.
- **Bundled proxy sends HSTS (#493).** `nginx/nginx.conf` now sends `Strict-Transport-Security` on every HTTPS response ([HSTS and Other Security Headers](#hsts-and-other-security-headers)). On app pages it no longer adds a CSP and other security headers alongside the web app's own, so each header arrives once. The effective policy is unchanged. The new configuration reaches an existing install only through the `git pull` step in [Updating a Deployment](#updating-a-deployment).
- **Parakeet word length capped, word end padding retired (#473).** A Parakeet word now ends at the earliest of the next word's start, its window's end and its start plus `asr_word_length_cap_s` (default 0.4 s, range 0.2 to 0.45). The retired `asr_word_end_padding_s` (default 0.2) set how long a word lasted before a long pause. It is not converted, because the same number means something else as a cap. A stored value is ignored, dropped from a user's settings on their next save, and dropped from `config.json` when an administrator next saves an install-wide setting. An older settings page that still sends it is ignored. Anyone who had set it gets the new default until they set the cap. Reprocess a recording to apply the change to it.
- **Voiceprints measured on extraction (#498).** Migration `7c3e5a91d2f4` adds the nullable column `recording_speakers.embedding_speech_seconds`, with no backfill; a downgrade drops it. The voiceprint dialog's extract actions, recalibration and person split now call `extract_measured_embedding_task`, which an older worker fails with `NotRegistered`, and a newer worker fails on the missing column until the API has migrated, so upgrade the API and the workers together. Voiceprints extracted earlier have no speech figure and work as before until they are extracted again ([USAGE.md](USAGE.md#when-a-voiceprint-is-not-saved)).
- **Drift guard refusals remembered (#499).** Migration `7ff0bbdadad8` adds the table `recording_speaker_drift_refusals`, one row for each person the guard refused a recording speaker for, so a rebuild of a person's voiceprint leaves out a speaker the guard refused for them. It starts empty, since earlier refusals were never recorded. Deleting the speaker or the person deletes its rows, backups carry them, and a downgrade drops the table. As with the note above, a newer worker fails on the missing table until the API has migrated.
- **`LLM_PROVIDER` reaches the containers empty (#477).** `docker-compose.example.yml` passes `LLM_PROVIDER` as `${LLM_PROVIDER:-}` in place of `${LLM_PROVIDER:-gemini}`, so the setup wizard can report that no AI provider is configured. Nojoin reads an empty value as `gemini`, ahead of any provider in `data/config.json`, as the old default did, so the api and the workers keep the same provider with either line and an existing `docker-compose.yml` needs no change. `.env.example` also moves the comment off its `LLM_PROVIDER=` line, because Compose read `LLM_PROVIDER= #One of: gemini, openai, anthropic, ollama` as the provider's name, and every call to the default provider failed. A `.env` that still has that line now selects `gemini`, as the next note describes.
- **Comments in `.env` no longer reach the containers as values (#520).** Compose reads `KEY= # note` as the value `# note`, and `.env.example` had eight more lines of that shape: `NOJOIN_TELEMETRY_ENABLED`, `BACKUP_EXPORT_DIR`, `NOJOIN_UMASK`, `NOJOIN_STALL_WATCHDOG_ENABLED`, `HF_TOKEN`, `OLLAMA_CONTEXT_WINDOW`, `SECONDARY_LLM_PROVIDER` and `SECONDARY_OLLAMA_CONTEXT_WINDOW`. The template now keeps every comment on its own line. A `.env` copied from an earlier one keeps the old lines, so Nojoin reads a value of any of these variables or `LLM_PROVIDER` that starts with `#` as empty, and the api and each worker log `Reading <variables> as empty` once as they start. Before this, `BACKUP_EXPORT_DIR` sent exports to a directory inside the worker container, which the API could not serve and which was lost when the container was recreated, so export again any archive you still need. `HF_TOKEN` also showed as set in **Settings**, and `SECONDARY_LLM_PROVIDER` named no provider, so the fallback never ran. To clear the warning, move each comment in `.env` to its own line or delete it, then run `docker compose up -d`.
- **Stall detection settings reach the API (#476).** `docker-compose.example.yml` now passes the four `NOJOIN_STALL_WATCHDOG_*` variables to `api`. A `docker-compose.yml` copied from an earlier example does not, so a value set in `.env` has no effect until you copy those four lines into its `api` service's `environment:` block ([Stall Detection](#stall-detection)).
- **Storage hygiene (#516).** The daily cleanup now logs the document, audio and proxy files that no row names, and removes them only when `NOJOIN_STORAGE_HYGIENE` is `delete` ([Storage Hygiene](#storage-hygiene)). A `docker-compose.yml` copied from an earlier example does not pass the variable, so a value set in `.env` has no effect until you add `NOJOIN_STORAGE_HYGIENE: ${NOJOIN_STORAGE_HYGIENE:-}` to its `x-shared-app-environment` block. The CLI needs no change.

#### Upgrading From Before 2.10.0

- **ASR window result ledger removed.** Alembic revision `3b804f4435a1` drops the `recording_asr_window_results` table, an unread log of transcription attempts that no transcript, utterance, speaker or backup refers to. A downgrade recreates it empty. The `enable_asr_window_result_ledger` setting and the `ENABLE_ASR_WINDOW_RESULT_LEDGER` variable are ignored. Stop the stack before upgrading, as in [Updating a Deployment](#updating-a-deployment): a worker from an earlier release still running after the migration fails every final pass.
- **Parakeet and Canary windows shortened, GPU transcription window retired (#445).** Both engines transcribe in windows of at most 30 seconds on every host, and Parakeet loads fp32 on a CPU too ([GPU Acceleration](#gpu-acceleration)). A CPU host running Parakeet downloads the fp32 weights, about 2.5 GB, on the first start. The int8 files, about 0.7 GB, stay beside them; deleting Parakeet in **Model downloads** under **Settings > AI providers** removes both, and preparing it again fetches only fp32. The `gpu_asr_window_s` setting is retired: a stored value is ignored and dropped from `config.json` on the next save. Reprocess a recording to transcribe it with the shorter windows.
- **fp32 ASR weights downloaded again (#448).** huggingface_hub 1.32 and later can store a model's fp32 graph and its external weights in different directories, which ONNX Runtime 1.31 (Nojoin 2.8.0 and later) refuses to load. The worker now sets `HF_HUB_DISABLE_SHARED_BLOBS=1`. A copy already in the shared store shows as not downloaded, and startup preparation deletes it and downloads it again: about 2.5 GB for Parakeet and 4 GB for Canary. A repo directory that is a link elsewhere is not deleted. The worker log says so, and you remove it by hand.

#### Upgrading From Before 2.6.0

- **Transcription engine made install-wide.** The engine (`transcription_backend`) and its model (`whisper_model_size`, `parakeet_model`, `canary_model`) live in `data/config.json` and apply to every user. On the first start, the API copies the owner's choice into `config.json` and removes it from the owner's account. An owner value equal to the shipped default does not replace a different value already in `config.json`, and choices stored on other accounts are ignored. The api log names every value carried, kept or rejected. If `config.json` cannot be written, the next start tries again.

#### Upgrading From Before 2.3.0

- **Search index rebuilt (destructive).** The search and meeting chat embedding model changed from `all-MiniLM-L6-v2` to `jina-embeddings-v2-small-en`. The migration widens `context_chunks.embedding` from 384 to 512 dimensions and deletes every row, so search and meeting chat return nothing until the index is rebuilt. Take a database backup first. Downgrading is equally destructive.

  A periodic task re-indexes transcripts a few at a time. To rebuild transcripts and documents sooner, run this until it reports zero. Each call queues up to 50 recordings and skips any already current:

  ```bash
  docker compose exec worker-parse \
    celery -A backend.celery_app.celery_app call \
    backend.worker.tasks.rebuild_text_embeddings_task
  ```

  Re-indexing runs locally and costs nothing. Documents parsed before 2.3.0 have no stored pages, so they are re-parsed structurally, never visually, and spend no AI provider quota. Use **Parse again** on a document to run visual analysis on it. The new model, about 120 MB, downloads the first time a worker embeds anything, so on an air-gapped host the rebuild fails until the model cache holds it.

#### Upgrading From 1.x

- **Shared /tmp volume.** Deployments created before 2.0.0 mounted a `backup_temp` volume at `/tmp` in the api and workers. Nothing uses it now and it holds nothing durable, so remove it. An export in flight during the upgrade is lost.

  ```bash
  docker compose down
  docker volume rm nojoin_backup_temp
  docker compose up -d
  ```

- **Recordings bind mount.** Remove any `./data/recordings:/app/recordings` bind left in a compose file derived from an older example. Nothing reads it, and Docker can recreate the recordings directory owned by root through it.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [CAPTURE.md](CAPTURE.md)
- [CALENDAR.md](CALENDAR.md)
- [ADMIN.md](ADMIN.md)
- [SECURITY.md](SECURITY.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
