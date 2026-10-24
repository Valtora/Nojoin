# Nojoin Development Setup

Prerequisites, local checks, CI, code conventions and the release workflow for working on Nojoin from source.

## Core Tooling

### General

- Git
- Docker

### Backend

- Python 3.12
- FFmpeg
- PostgreSQL development headers
- Compiler tools

On Debian or Ubuntu:

```bash
sudo apt install ffmpeg libpq-dev build-essential
```

On Windows, install FFmpeg on `PATH` and the Microsoft Visual C++ Build Tools.

### Frontend

- Node.js 26, as CI and `frontend/Dockerfile` use. `npm run test` needs at least 22.22.2, 24.15 or 26, the versions jsdom (the Vitest DOM environment) supports.
- npm

### Browser Capture

For manual capture checks:

- Chrome or another supported desktop Chromium browser on Windows, Linux or macOS, for shared-audio capture
- Chrome on Android or iOS, for microphone-only capture
- Microphone permission in the browser
- PipeWire screen capture, for shared-screen or system audio on Linux

## Fresh Checkout Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/dev.txt
python -m pip install --no-deps -r requirements/worker-no-deps.txt
pre-commit install   # optional: Ruff, whitespace and the doc and Alembic validators on commit

cd frontend
npm install
```

`requirements/dev.txt` is the test stack (with PyPI's torch, which runs on the CPU) plus the lint, format and type tooling. On a GPU host that runs the live processing stack, install `requirements/local.txt` instead. `requirements/backend.txt` and `requirements/worker.txt` are the API and worker runtime sets. Whichever of these includes `worker.txt`, follow it with the `--no-deps` line, which installs fastembed without the dependencies it declares ([fastembed's Pillow cap](#fastembeds-pillow-cap)).

## Required Pull Request Checks

The `CI` workflow in [ci.yml](../.github/workflows/ci.yml) runs on pull requests and on pushes to `main`. A `Detect changes` job classifies the diff with `dorny/paths-filter`, and each heavy job runs only when its paths changed:

| Check | Runs when these changed |
| --- | --- |
| `Backend tests` | `backend/**`, `requirements/**`, `pyproject.toml`, `frontend/src/lib/processingTuning.json`, `scripts/dev/**` |
| `Migrations (Postgres)` | `backend/alembic/**`, `backend/models/**`, `scripts/check_migrations.py` |
| `Python quality` (Ruff lint and format check, file-size and blind-except gates, mypy) | the `Backend tests` paths, or `scripts/**` |
| `Frontend lint` (ESLint and the contrast gate) | `frontend/**` |
| `Frontend unit tests` | `frontend/**` |
| `Frontend build` | `frontend/**` |
| `Site build` (Astro build and the contrast gate) | `site/**`, `frontend/src/app/tokens.css`, `frontend/scripts/check-contrast.mjs` |
| `Telemetry worker` (tsc and Vitest) | `telemetry/**` |
| `Demo server` (pytest) | `demo/**` |
| `Docs validation`, `Alembic validation`, `Whitespace check`, `Held pin drift check` | always run |
| `Dependency vulnerability scan` (Trivy) | always runs, never fails |

A change to a deployment path (`docker/**`, `docker-compose*.yml`, `nginx/**` or `.github/workflows/ci.yml`) also runs the first seven jobs. Other workflow files run only the always-on jobs.

The one required status check is `CI gate`. It depends on every job except the dependency scan, fails if any of them failed or was cancelled, and counts a skipped job as a pass, so it reports on every pull request, documentation-only ones included.

Local equivalents:

```bash
source .venv/bin/activate
python scripts/check.py           # Ruff lint and format check, whitespace, file size, blind-except reasons, held pins, mypy, doc and Alembic validators, pytest
python scripts/check.py --fix     # apply Ruff fixes and formatting first
python scripts/check.py lint typecheck   # a subset, by name

cd frontend
npm run lint
npm run check:contrast
npm run test
npm run build

# Only when telemetry/ changed
cd ../telemetry
npm ci
npm run typecheck
npm test
```

The check names are `lint`, `format`, `whitespace`, `filesize`, `blindexcept`, `heldpins`, `typecheck`, `docs`, `alembic` and `tests`. The demo server's tests are in [demo/README.md](../demo/README.md#tests).

### Branch Protection

Branch protection lives in GitHub's settings, outside the repository. To restore it on `main`, apply this with an authenticated `gh`:

```bash
gh api -X PUT repos/Valtora/Nojoin/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "CI gate"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
JSON
```

`PUT` replaces the whole protection object, so a field left out is switched off.

None of these settings can lock out a sole maintainer. GitHub never lets an author approve their own pull request, so `required_approving_review_count` stays `0` and `require_code_owner_reviews` stays `false` while there is one maintainer; [CODEOWNERS](../.github/CODEOWNERS) still requests a review on every pull request. `enforce_admins` holds the owner to `CI gate` too, and the owner can still change protection under **Settings > Branches**. When a second maintainer joins, set `require_code_owner_reviews` to `true` and `required_approving_review_count` to `1`.

Require `CI gate` and nothing else. The individual CI jobs are skipped when their paths are untouched, and the jobs in [release.yml](../.github/workflows/release.yml) run only on a tag push or `workflow_dispatch`, so requiring any of them would leave every pull request waiting on a check that never reports. Branch protection does not govern tags in any case: the release gates itself, as [Gated Tag Publication](#gated-tag-publication) describes.

## Verification By Change Scope

- Backend or worker code: `pytest`, or `python scripts/check.py` for every Python gate.
- Bulk database reads or writes whose size follows the data: also `pytest` with `NOJOIN_TEST_POSTGRES_URL` set. See [Bind Parameter Limits](#bind-parameter-limits).
- Alembic migrations: `python3 scripts/validate_alembic.py`, which needs exactly one head, and `python3 scripts/check_migrations.py` against a throwaway Postgres. See [Running Migrations Against Postgres](#running-migrations-against-postgres).
- Frontend code: `npm run lint`, `npm run check:contrast`, `npm run test` and `npm run build`.
- Design tokens (`frontend/src/app/tokens.css`, `frontend/src/app/palettes.css`, `site/src/styles/site-tokens.css`): `npm run check:contrast`, which fails on any declared pairing below its threshold in any theme or palette. A new token drawn on or behind another needs its pairing added to `frontend/scripts/check-contrast.mjs`. [DESIGN.md](DESIGN.md#accessibility) has the thresholds.
- Marketing site (`site/`): `cd site && npm ci && npm run build` on Node 26. Use `npm ci`, never `npm install`, because an older npm rewrites `site/package-lock.json` in a way CI rejects. [SITE.md](SITE.md) has the preview and review runbook.
- Browser capture: the frontend checks plus the manual checks in [Browser Capture Development](#browser-capture-development).
- Recording actions: change [useRecordingActions.ts](../frontend/src/components/recordings/_hooks/useRecordingActions.ts), not a surface, then run the frontend checks.
- Security-sensitive changes: rerun the checks for the affected auth or session path, and update [SECURITY.md](SECURITY.md) in the same pull request when behaviour changes.
- Documentation only: `python3 scripts/validate_docs.py`.
- Deployment or release workflow: the backend, frontend, docs and Alembic checks together.

## Containerised Source Stack

The repository ships no development compose override. To run the whole stack from source, put the [Localhost Dev Compose Template](#localhost-dev-compose-template) at the end of this document in your ignored `docker-compose.yml`. It builds the application images locally, names every container `nojoin-dev-*`, keeps PostgreSQL, Redis, Nginx and the socket proxy on their upstream images, and serves `https://localhost:14443` from the `frontend` container through the `nginx-dev` proxy.

1. Run `cp .env.example .env` and set `FIRST_RUN_PASSWORD` in `.env`. To use the template's `nojoin_dev` database name, also set `POSTGRES_DB=nojoin_dev` there. Leave `.env.example` unchanged.
2. Copy the template into `docker-compose.yml`. Its `nginx-dev` service also joins an external `proxy_net` network for an edge proxy, so either create that network once with `docker network create proxy_net`, or remove `proxy_net` from `nginx-dev` and from the top-level `networks`.
3. Without an NVIDIA GPU, remove the `deploy` block from `worker-gpu` ([CPU-Only Deployment](DEPLOYMENT.md#cpu-only-deployment)).
4. Start the stack:

   ```bash
   docker compose up -d --build
   ```

5. Open `https://localhost:14443`.

### Incremental Rebuild Loop

```bash
docker compose up -d --build api
docker compose up -d --build worker-gpu worker-cpu worker-io worker-parse
docker compose up -d --build frontend
```

- `api`: after API or shared backend changes.
- The four worker lanes: after worker code, dependency or image changes. Name all four. `worker-gpu` and `worker-cpu` run the shared worker image, `worker-io` layers its own image on top of it, and `worker-parse` runs the `worker-io` image.
- `frontend`: after frontend changes you want to see through Nginx.

`frontend` waits for a healthy `api`, and `nginx-dev` for a healthy `api` and `frontend`. Starting a service does not start the services that depend on it, so to bring `https://localhost:14443` back from a stopped stack, name the proxy as well:

```bash
docker compose up -d --build api frontend nginx-dev
```

To discard cached layers, or when the application services drift out of step, rebuild from clean:

```bash
docker compose down
docker compose build --no-cache api worker-gpu worker-cpu worker-io frontend
docker compose up -d --force-recreate
```

`worker-parse` is absent from the build list because it has no build of its own; `--force-recreate` gives it the fresh `worker-io` image.

### Optional Backend Source-Mount Patch

To run Python changes without rebuilding the API and worker images, patch your `docker-compose.yml` as below. The source mount goes once into the shared `x-worker-runtime` anchor, so every lane picks it up, and each lane's command is wrapped in `watchmedo`, which restarts the worker when a `.py` file under `backend/` changes. The API loads a change on `docker compose restart api`.

```yaml
x-worker-runtime: &worker-runtime
  volumes:
    - .:/app
    - ./data:/app/data
    - model_cache:/home/appuser/.cache
    - /sys/class/drm:/sys/class/drm:ro

services:
  api:
    command: uvicorn backend.main:app --host 0.0.0.0 --port 8000
    volumes:
      - .:/app
      - ./data:/app/data
      - model_cache:/shared_model_cache:ro

  worker-gpu:
    command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker -Q gpu --pool=solo --concurrency=1 --loglevel=info

  worker-cpu:
    command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker -Q cpu --pool=prefork --concurrency=3 --loglevel=info

  worker-io:
    command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker -Q io -B -s /app/data/celerybeat-schedule --pool=prefork --concurrency=4 --loglevel=info

  worker-parse:
    command: watchmedo auto-restart --directory=./backend --pattern=*.py --recursive -- celery -A backend.celery_app.celery_app worker -Q parse --max-tasks-per-child=25 --pool=prefork --concurrency=2 --loglevel=info
```

Keep each lane's `-Q`, `--pool`, `--concurrency` and `--max-tasks-per-child` as the template has them, and `-B` on `worker-io` alone. A lane without `-Q` drains the GPU queue, where unrouted tasks land, and a second `-B` fires every periodic job twice. The pool is part of the dispatch model, and the parse lane's recycle limit overrides a global default of 500. See [Worker Concurrency Lanes](DEPLOYMENT.md#worker-concurrency-lanes) and [Worker Child Recycling](DEPLOYMENT.md#worker-child-recycling).

The patch changes nothing for the frontend: rebuilding `frontend` is still how `https://localhost:14443` updates.

### Optional Host-Run Frontend Workflow

For the fastest UI loop, run Next.js on the host. This is a separate mode: Nginx must proxy to the host process instead of the `frontend` container, which takes a local patch to your `docker-compose.yml` and to the tracked `nginx/nginx.conf` (do not commit it). Rebuilding the `frontend` container then changes nothing at `https://localhost:14443`, and you keep the host process running yourself.

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=/api npm run dev -- --hostname 0.0.0.0
```

`npm run dev` serves on port 14141. Development mode is more forgiving than a production build, so run `npm run lint` and `npm run build` before you push.

To run other code on the host against containerised services, start only the services you need, such as `db` and `redis`. The template publishes neither PostgreSQL nor Redis, so add `ports` entries for them in your `docker-compose.yml`.

### Throwaway Stacks and the Fake LLM Server

A stack started for a browser or end-to-end test puts every container on one internal Docker network: Postgres, Redis, the API, the workers, Nginx and the browser that drives it. An internal network reaches its own containers and nothing else. Publish no port, and never use `--network host`, which reaches every service on the host and on its LAN. Give the stack its own data directory, and name each container with one prefix and its role (`agent-e2e-pg`, `agent-e2e-api`, `agent-e2e-fakellm`), so that one filter finds the whole stack when it comes down.

Notes, Meeting Chat, Meeting Edge, AI analytics and document image parsing call [scripts/dev/fake_llm_server.py](../scripts/dev/fake_llm_server.py) on that network in place of a model. It answers the Ollama API calls Nojoin makes (`GET /api/tags`, `POST /api/show` and `POST /api/chat`, streamed or not) with canned answers that are the same on every run, and logs each request's task and size but never its text. It lists two models: `fake-llm` accepts images and `fake-text` refuses them, which exercises the downgrade to a structural parse. Its context window behaves as Ollama's does, at four bytes a token, so `OLLAMA_CONTEXT_WINDOW=4096` sends a long meeting through the notes-in-parts path.

The server needs only Python's standard library, and runs in the backend test image, `nojoin-dev-tests:local` (the worker image with the test and lint requirements added):

```bash
docker network create --internal agent-e2e-net
docker run -d --rm --name agent-e2e-fakellm --network agent-e2e-net \
  --memory 256m --memory-swap 256m --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD/scripts/dev:/fake:ro" --entrypoint python \
  nojoin-dev-tests:local /fake/fake_llm_server.py --host 0.0.0.0 --port 11434
```

Point the stack's API and workers at it in their environment, then choose `fake-llm` as the model in the setup wizard or **Settings > AI providers**:

```bash
LLM_PROVIDER=ollama
OLLAMA_API_URL=http://agent-e2e-fakellm:11434
```

A URL set in the environment is the install's configured Ollama URL, which the URL check accepts on a private network. `docker logs agent-e2e-fakellm` lists every call the stack made. To tear down, stop `agent-e2e-fakellm` and the stack's other containers, then run `docker network rm agent-e2e-net`.

## Backend Development Notes

```bash
alembic upgrade head                            # apply migrations
alembic revision --autogenerate -m "message"    # create a revision
python -m backend.startup_canonical_cutover     # run the legacy recording sweep alone
```

- Never delete or rename a committed Alembic revision.
- The API image's entrypoint runs Alembic (`backend.startup_migrations`), then the canonical cutover sweep for pending legacy recordings, then the server. `NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER=1` skips the sweep for local debugging, and `NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE` sets its batch size (default `100`).
- The dev template sets `NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS=true` on `api`. A database stamped with a revision that is missing from your checkout is then restamped to the current head before `alembic upgrade head`. Use it only on disposable databases; on a persistent one, repair the graph or the stamp by hand.

### Running Migrations Against Postgres

`validate_alembic.py` checks only the revision graph. `check_migrations.py` runs the chain on a real Postgres: base to head on an empty database; then it seeds awkward values, downgrades the newest migration and applies it again with that data present; then it asserts the database is at head.

```bash
docker run -d --name nojoin-migcheck -e POSTGRES_PASSWORD=test \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=nojoin_migration_check \
  -p 55432:5432 pgvector/pgvector:pg18-trixie

DATABASE_URL=postgresql://postgres:test@localhost:55432/nojoin_migration_check \
  python3 scripts/check_migrations.py

docker rm -f nojoin-migcheck
```

The seed puts JSON `null` in `jsonb` columns, which the manual speaker merge writes when it clears a voiceprint. Postgres may evaluate the two sides of `AND` in either order, so `jsonb_typeof(col) = 'array' AND jsonb_array_length(col) > 0` can still abort on those rows. Use a plain comparison such as `col <> '[]'::jsonb`.

### Bind Parameter Limits

PostgreSQL accepts at most 65535 bind parameters in one statement. asyncpg, the API's driver, stops at 32767, and psycopg 3, the workers' driver, at 65535. SQLAlchemy checks neither, so a multi-row insert or a large `IN` clause builds and then fails at execution once the data is large enough, and a statement between the two limits works in a worker and fails in the API.

When the number of rows or items comes from the data, including a list in a request body, batch it with `bind_batches` from [backend/utils/db_batching.py](../backend/utils/db_batching.py), whose `MAX_BIND_PARAMS` (30000) sits under both limits. Pass `params_per_item` for a multi-row insert, where each row binds every column; the default of 1 suits an `IN` clause.

The suite runs on SQLite, which accepts far more parameters, so a SQLite test passes on a statement Postgres rejects. Tests that need the real limit take the `postgres_test_url` fixture, which skips unless `NOJOIN_TEST_POSTGRES_URL` names a server. CI sets it for `Backend tests`. Locally:

```bash
docker run -d --rm --name nojoin-test-pg \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=nojoin_test \
  -p 55432:5432 pgvector/pgvector:pg18-trixie

NOJOIN_TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:55432/nojoin_test pytest
```

### Test Reliability

- **Durations.** `pyproject.toml` sets `--durations=15`, so every `pytest` run lists the fifteen slowest tests. If a test you touched is near the top, trim its fixtures or narrow it. A slow test worth keeping gets a tracking issue with the `slow-test` label.
- **Flaky tests.** Report a test that passes and fails with no code change as described in [CONTRIBUTING.md](../.github/CONTRIBUTING.md#reporting-issues); triage adds the `flaky` label. Fix it by removing the nondeterminism (timing, ordering, shared state, network), not by re-running it. A skip or `xfail` needs a linked issue and a stated condition for removing it.
- **No Redis.** `backend/tests/conftest.py` fails any test that opens a Redis connection, printing `Test opened a real Redis connection` and the call site. Take the `stub_celery_dispatch` fixture for a Celery dispatch, `stub_meeting_edge_dispatch` for transcript mutations, or stub the client the code uses. Stub an attribute on a shared object (`celery_app.send_task`) or a function in its defining module; a stub on a package that re-exports a helper misses callers that imported the name earlier.
- **No outside network.** `conftest.py` fails any test that resolves or connects to a host other than loopback, a Unix socket or the host in `NOJOIN_TEST_POSTGRES_URL`, printing `Test reached outside the suite's network`, the host and the call site. A lookup counts as much as a connection, so a URL check that resolves `ollama.example.com` trips it too. Stub the client, or `socket.getaddrinfo` for a URL check as [test_ollama_ssrf_policy.py](../backend/tests/test_ollama_ssrf_policy.py) does. The guard sees Python's socket calls only: a connection made from C (libpq, behind psycopg) shows at its name lookup, and a subprocess not at all. Run the suite on a network with no way out, `--network none` or an internal Docker network beside a Postgres container, to cover those. A test that needs a model talks to the [fake LLM server](#throwaway-stacks-and-the-fake-llm-server) on loopback, as [test_fake_llm_server.py](../backend/tests/test_fake_llm_server.py) does.
- **No model downloads.** `conftest.py` sets `HF_HUB_OFFLINE=1`, so huggingface_hub, and fastembed through it, fail at once instead of downloading a model, and the suite passes the same with or without network. It also fails any test that loads the real text embedding model, printing `Test loaded the real text embedding model` and the call site, because every caller of the embedding service swallows the error. Take the `stub_text_embedding` fixture, which returns one fixed vector per text. A test that means to use the real model from the model cache takes `@pytest.mark.real_text_embedding`.
- **Model registry.** `conftest.py` imports `backend.models.registry`, so a test module behaves the same alone as in the full suite. `test_api_mapper_configuration.py` checks that each real entry point registers every model.
- **Postgres setup.** A test that needs the models' tables on Postgres calls `fresh_schema` from [postgres_setup.py](../backend/tests/postgres_setup.py) in a schema of its own. The schema is built once per session and emptied for each test after that, and built again if a test changed its objects. A migration test brings its empty scratch database to the revision before the one it tests with `migrate_empty_database`, which runs Alembic once per revision in a session and copies the result.
- **Image boundaries.** CI installs the API and worker requirements together, so a test passes on an import that one of the images cannot make. `test_api_torch_boundary.py` imports API modules with torch blocked, and `test_worker_import_boundary.py` imports worker modules with FastAPI and the API's other packages blocked ([Worker imports](#audio-processing--ml-operations)).
- **ffmpeg.** Tests that run real media through ffmpeg skip when `shutil.which("ffmpeg")` finds nothing. CI installs Ubuntu's ffmpeg 6.1, the series the worker image ships; the API image ships 7.1. A test that depends on one version's behaviour should say which. To simulate a host without ffmpeg, point `PATH` at an empty directory with `monkeypatch.setenv`.

## Browser Capture Development

Capture code lives in `frontend/src/lib/capture/`. After a capture change, check whichever of these apply:

- Supported-browser gating for desktop Chromium on Windows, Linux and macOS, and the unsupported-browser message on Firefox, Safari and mobile browsers other than Chrome.
- Mobile Chrome: microphone-only start, waveform, pause and resume, stop and finalise, and the notice that shared app, tab or system audio is not captured.
- The share picker for a tab, a window and a screen; shared-audio track detection and the missing-audio message.
- Microphone permission, the selected device, and the per-source gain controls in **Settings > Recording**.
- Segment creation, sequential upload, FLAC decode on upload, live transcript dispatch, pause and resume, stop and finalise, and discard.
- An upload backlog: block segment uploads in the browser's developer tools, close the tab, reopen it, and check that **Stop and process** uploads what the closed tab recorded before finalising.
- The paused-recording lock after a refresh or a closed tab (a real unload, not in-app navigation).
- A tab closed while signed out, or with the pause request blocked: the recording is offered as interrupted two minutes after its last upload, in a fresh tab and without a reload, and a capture still running in another tab is never offered.
- A pause that does not reach the server: block `/pause` in the developer tools and select **Pause**. The controls read **Paused** with a warning, the elapsed time stops, and once `/pause` is unblocked the pause reaches the server within 30 seconds. **Resume** then carries on with the same capture.
- An outage longer than the upload retries: take the capturing tab offline for about 30 seconds. It settles in paused with **Start Meeting** unavailable, the pause reaches the server once the tab is back online, and **Resume** carries on with the same capture and no gap or repeat in the segment numbers.
- A takeover: start a capture, block its uploads until the recording is offered as interrupted (two minutes), resume it in another browser, then unblock the first tab. The first tab stops with the message that the recording continued elsewhere and offers the audio it had not uploaded as a download, and the finished recording holds the first tab's uploaded audio followed by the second browser's, with no segment replaced.
- A discard elsewhere: pause a capture, choose **Discard recording** for it in another browser's resume-or-discard dialog, then select **Resume** in the first tab. It stops with the message that the recording was discarded and releases the microphone and shared tab. With a second account signed in from another tab of the first browser instead, the first tab keeps recording and asks for its owner's sign-in.
- A stop elsewhere: pause a capture, choose **Stop and process** for it in another browser's resume-or-discard dialog, then select **Resume** in the first tab. It stops with the message that the recording was already stopped, releases the microphone and shared tab, and shows no recording controls. Any audio that tab had not uploaded is then gone from its IndexedDB queue (`nojoin-capture`) and offered as a download, and the downloaded FLAC plays back that stretch.
- Focus moving to another tab, window or application, which must not pause capture.

```bash
cd frontend
npm run test -- src/lib/capture
npm run build
```

On the containerised stack, rebuild with `docker compose up -d --build frontend`. Read [CAPTURE.md](CAPTURE.md) before changing support copy, browser compatibility or troubleshooting guidance.

### Spellcheck Dictionaries

Dictionaries live in `frontend/public/dictionaries/<locale>/` as `index.aff.gz` and `index.dic.gz`. To add or update one, compress the raw files with `gzip -k index.aff index.dic` and commit only the `.gz` files.

## Backend Coding Conventions

### Language & Formatting

- **Formatting and imports**: Ruff formats and sorts imports. Run `ruff format .` and `ruff check --fix .`, or `python scripts/check.py --fix`, or let the pre-commit hook do it.
- **Type hints**: annotate new and changed public and cross-module code. mypy checks only the modules in `[tool.mypy].files` in `pyproject.toml`; add a module there once it is annotated.
- **Error handling**: raise `HTTPException` for HTTP-level errors in endpoints, and catch specific exceptions where recovery differs. A deliberate `except Exception` at a recovery boundary must log actionable context or re-raise with `raise ... from`, and carry `# noqa: BLE001 -- boundary: <reason>`. `scripts/check_blind_excepts.py` fails a bare `# noqa: BLE001`, a blanket `# noqa`, and a missing, one-word or placeholder reason. With no reason worth writing, catch a narrower exception.
- **Logging**: on frequently executed paths, pass `%`-style arguments (`logger.info("processed %s", count)`) instead of an f-string, so a disabled level skips the formatting.
- **Prompts**: build LLM prompts from `(heading, body)` blocks with `backend.utils.prompt_blocks.render_prompt_blocks`. Never use `str.format` or `%` substitution in a prompt: interpolated text is often user-authored, and a stray brace would fail at generation time.

### Complexity & Size Thresholds

- **Complexity**: Ruff `C901` caps McCabe complexity at 10, and `PLR0912`, `PLR0913` and `PLR0915` cap branches, arguments and statements at Ruff's Pylint defaults.
- **File size**: backend Python source may run to 1000 lines (`scripts/check_file_size.py`). Tests (`backend/tests/`, `test_*.py`, `*_test.py`) and Alembic revisions are exempt.

Code that predates the gates is grandfathered through per-file ignores under the complexity baseline in `[tool.ruff.lint.per-file-ignores]` and recorded line counts in `GRANDFATHERED` in `scripts/check_file_size.py`. A grandfathered file fails if it grows past its count. When one shrinks, lower its count, drop the rules it no longer trips, and remove its entries once it is within the limits. Never add an entry.

### Comments

These apply to all tracked source, frontend included.

- **Explain intent**: document an invariant, constraint, risk or compatibility requirement. Do not narrate what the code says.
- **State the rule**: remove musings such as "we can commit here" or "maybe skipped?" and questions inside authorisation rules. Express real uncertainty as an invariant, a fallback policy, an issue reference or a testable assumption, and lock a security or authorisation rule with a test.
- **Keep contract comments**: comments on live and final pipeline alignment, security and authorisation boundaries, migration and compatibility requirements, browser-capture contracts, and non-obvious build or runtime constraints stay. Make them more precise rather than deleting them.

### Data Access & Dependency Injection

- **ORM**: SQLModel, with every model in [backend/models/](../backend/models/).
- **Datetimes**: the schema stores naive UTC. Produce values with `backend.utils.time.utc_now`, and give every `datetime` field on a table model an explicit naive type, `sa_type=DateTime` or `sa_column=Column(DateTime, ...)`. From sqlmodel 0.0.45 a bare `datetime` maps to the timezone-aware `UTCDateTime`, which disagrees with the migrations and raises `ValueError` on the first naive value. [test_model_datetime_columns.py](../backend/tests/test_model_datetime_columns.py) fails on such a column.
- **Dependency injection**: take the database session with `Depends(get_db)` and the user with `Depends(get_current_user)` from `backend.api.deps`, or `get_current_admin_user` (owner, admin or superuser) and `get_current_active_superuser` (superuser only) for privileged routes.

### System Configuration

- **Configuration store**: `backend.utils.config_manager` handles system and user settings in `data/config.json`. Do not add another store.
- **Config in workers**: each worker process holds its own copy of `config.json`, which the API writes. A `task_prerun` hook in [backend/celery_app.py](../backend/celery_app.py) reloads it before every task, at the cost of one `stat` when the file is unchanged, so a task never calls `config_manager.reload()` itself. Saves write a temporary file and rename it over `config.json`, so a reader never sees a partial file.

### Audio Processing & ML Operations

- **Heavy imports**: import ML libraries such as `torch`, `whisper` and `pyannote` inside Celery task functions under [backend/worker/tasks/](../backend/worker/tasks/), never on API startup paths.
- **Worker imports**: the worker images install `requirements/worker.txt`, which has no FastAPI. A module a worker can import, by any import a task makes, must not import FastAPI or another package only `requirements/backend.txt` installs, inside a function or out. Raise a domain error and turn it into the `HTTPException` in the endpoint. CI installs both sets, so [test_worker_import_boundary.py](../backend/tests/test_worker_import_boundary.py) walks every module a worker can reach and imports them with those packages blocked. A new pin in `backend.txt` alone fails it until its modules join `BACKEND_ONLY_MODULES`.
- **Task dispatch from async code**: never call `celery_app.send_task`, `.delay()` or `.apply_async()` inside an `async def`. Publishing is a blocking socket call, and an unreachable Redis would stall the event loop and every request on it. Use `dispatch_task` from [backend/core/task_dispatch.py](../backend/core/task_dispatch.py), or `dispatch_task_best_effort` where a dispatch failure must not fail the caller. A test fails on inline dispatch inside an `async def`, but it cannot see a synchronous helper that dispatches and is called from a coroutine, so check that case by reading. Worker code dispatches inline, which is safe only under the `solo` and `prefork` pools set in Compose ([Worker Concurrency Lanes](DEPLOYMENT.md#worker-concurrency-lanes)).
- **ASR engines**: transcription dispatches to an engine under [backend/processing/engines/](../backend/processing/engines/) chosen by the `transcription_backend` setting (`whisper`, `parakeet` or `canary`). Every engine implements `TranscriptionEngine`; Parakeet and Canary share the `OnnxAsrEngine` base class.
- **Window manifests**: read `RecordingAudioWindowManifest.asr_status` for live ASR coverage, not the legacy `status` projection, and never treat `live_processed` as diarisation-complete. Nothing writes the diarisation fields any more; [ARCHITECTURE.md](ARCHITECTURE.md) describes both lanes.
- **Phantom speaker filter**: [phantom_filter.py](../backend/processing/phantom_filter.py) drops or reassigns segments caused by notification sounds or background noise. Its thresholds default from [processing_tuning.py](../backend/processing/processing_tuning.py), and a user's settings can override them.
- **Speaker matching thresholds**: import the named constants from [embedding.py](../backend/processing/embedding.py), such as `IDENTIFICATION_THRESHOLD`, `AUTO_UPDATE_THRESHOLD`, `MARGIN_OF_VICTORY`, `DRIFT_GUARD_THRESHOLD` and `SCAN_MATCH_THRESHOLD`. Never hardcode a threshold elsewhere.
- **PyTorch safe globals**: `torch.load` defaults to `weights_only=True`, which refuses the pyannote classes in model checkpoints. Register them with `torch.serialization.add_safe_globals` before loading a model, as `embedding_core.py` and `diarize.py` do.

## Frontend Coding Conventions

### Architecture & UI Guidelines

- **State**: Zustand. [frontend/src/lib/store.ts](../frontend/src/lib/store.ts) holds global UI state (navigation, selection, filters), with feature stores beside it in `frontend/src/lib/`. Avoid prop drilling.
- **API calls**: all go through [frontend/src/lib/api/](../frontend/src/lib/api/), a barrel that re-exports the typed per-resource clients.
- **Auth**: the browser uses HttpOnly session cookies from `/api/v1/login/session`. Bearer tokens from `/api/v1/login/access-token` are for non-browser clients; never put one in a URL or anywhere else the browser exposes.
- **Framework**: the Next.js App Router ([frontend/src/app/](../frontend/src/app/)) and Tailwind CSS, with functional components in [frontend/src/components/](../frontend/src/components/).
- **TypeScript**: avoid `any`. Update the interfaces in `frontend/src/types/index.ts` first when adding settings or model fields.
- **Rich text**: create every Tiptap editor with `injectCSS: false`. The CSP's nonce-based `style-src` refuses the `<style>` tag Tiptap would otherwise inject, so ProseMirror's base rules are in `globals.css`. `tiptapStyles.test.ts` fails on an editor without the option, or when a Tiptap upgrade changes those rules.
- **Test environment**: Vitest runs each test file under jsdom unless the file's first line is `// @vitest-environment node`, which skips about half a second of jsdom setup per file. Add it only when neither the test nor anything it imports reads a browser global such as `window`, `document`, `navigator` or `Blob`.

### Charts

A series that is a person takes that person's meeting colour: the one `buildMeetingSpeakerColors` in [recordingSpeakerUtils.ts](../frontend/src/lib/recordingSpeakerUtils.ts) assigns and the user can change, as drawn in the transcript and the speaker panel. Charts read it through the `--speaker-*` tokens (the seventeen palette keys the panel's dots use), resolved once per surface with `buildSpeakerColors`. Never use a Tailwind colour class in a chart.

A series with no assigned colour takes `--chart-1` to `--chart-8`, and anything past the eighth takes `--chart-other`. Three rules hold for both families:

- **Fixed slots**: a slot follows the entity and never cycles. A filter that changes how many series are shown must not repaint the rest, and a ninth series folds into `--chart-other` instead of repeating slot 1.
- **Themes**: each theme's chart slots are a separate set, validated against that theme's surfaces for colour-vision and normal-vision separation, so changing one means re-validating both. The `--speaker-*` tokens have one value in both themes, like the panel's dots.
- **Labels**: several steps sit below 3:1 on the card, so their pairings in [check-contrast.mjs](../frontend/scripts/check-contrast.mjs) are held to `HAIRLINE`, not `AA_NON_TEXT`. That is acceptable only while every series is labelled directly and its figures also appear as text. A chart without labels must raise those pairings, and several steps would then fail.

PDF and Word exports draw the same charts on the server in `backend/services/exports/`. Their formatting, delivery readings and attribution wording port `formatDuration.ts`, `deliveryInsights.ts` and `AttributionWarning.tsx`, and `backend/tests/test_export_documents.py` pins the parity, so change both sides together.

### UI Duplication Rules

- **Recording actions**: rename, reprocess, archive, discard and the rest are defined once, in [useRecordingActions.ts](../frontend/src/components/recordings/_hooks/useRecordingActions.ts). The recordings rail ([Sidebar.tsx](../frontend/src/components/Sidebar.tsx)) and the live view ([RecordingStatusDisplay.tsx](../frontend/src/components/RecordingStatusDisplay.tsx)) consume that hook, the recording page takes its audio export from it, and `sharedRecordingActions.test.ts` fails if a surface defines its own.

## Release Workflow and Version Detection

### Unified Release Process

One tag, `vX.Y.Z`, releases the API, worker, worker-io and frontend images together. [scripts/release.py](../scripts/release.py) builds, gates and pushes the images on the maintainer's release host; [release.yml](../.github/workflows/release.yml) then verifies and signs them on GitHub.

**Maintainer steps:**

1. Merge the release's work into `main`.
2. In a pull request, bump [docs/VERSION](VERSION) and write the notes in [.github/release-notes-template.md](../.github/release-notes-template.md). Merge it.
3. On the release host, run the script with the new version:

   ```bash
   python3 scripts/release.py 0.6.0
   ```

   It pushes the tag, watches the workflow run and prints the release URL. `--no-wait` pushes the tag without watching.
4. Refine the notes in the GitHub Releases UI if the release needs specific guidance under Migration, Rollback, Known Issues or Browser-Capture Compatibility.
5. Check the public demo refreshed. `demo/refresh.sh` runs hourly on the release host, rebuilds `demo.nojoin.co.uk` for a new release and sends a notification either way. A failed refresh leaves the previous demo running. See [demo/README.md](../demo/README.md).

**What the script does**, stopping at the first failure:

1. Checks that `docs/VERSION` at `origin/main` is the new version and newer than the previous tag, that the tag is not on `origin`, and that every first-parent commit since the previous tag has a green `CI gate`, waiting up to 20 minutes for CI still running. CI on `main` runs only the jobs whose paths a push touched, so a version bump's own run covers little.
2. Builds the four images from a clean worktree of that commit, with no layer cache, in a `nojoin-release` BuildKit builder created for the run, using [docker/release.hcl](../docker/release.hcl). worker-io layers onto the worker built in the same run.
3. Runs the [Release Gates](#release-gates).
4. Builds again from cache with provenance and SBOM attestations, pushes the immutable version and `sha-<commit>` tags, and checks that each pushed image config is the one it scanned.
5. Pushes the tag.

**What [release.yml](../.github/workflows/release.yml) does on the tag push:**

1. Checks the tag is strict `vX.Y.Z` and matches `docs/VERSION`.
2. Checks every image is on GHCR under both immutable tags, carries provenance and an SBOM, and has a revision label naming the tag's commit. It checks all four before signing any.
3. Signs each image by digest with cosign.
4. Points the rolling `major.minor` and `latest` tags at the signed digests.
5. Publishes the GitHub Release ([Automated Release Notes](#automated-release-notes)).

A tag pushed by hand, without the script, fails at step 2 because its images are not on the registry. A manual `workflow_dispatch` run re-verifies and re-signs an existing tag's images and republishes its notes, for instance after a transient failure: set `release_ref` to the tag, and `publish_latest=true` if `latest` should move.

**Rehearsing a release.** `--dry-run` runs the checks, the build and every gate, and pushes nothing. It reports the version, tag and CI checks instead of stopping on them, and `--ref` builds any commit, so a branch can be rehearsed before it merges:

```bash
python3 scripts/release.py 0.6.0 --dry-run --ref HEAD
```

The scan downloads a fresh vulnerability database on every run, so a release that passed last week can fail today with no code change. Dry-run before bumping the version when the base images or the unpinned SDKs may have moved.

**Release host requirements.** Docker with a current Buildx (the script uses bake target contexts and the `fs.write` entitlement), [Trivy](https://trivy.dev) on `PATH`, `gh` authenticated for this repository, Python 3.12 or later, and about 25 GB free on the disk holding `~/.cache` and Docker's data. Each run creates the `nojoin-release` builder on the BuildKit image pinned in [docker/Dockerfile.buildkit](../docker/Dockerfile.buildkit), first removing any builder an earlier run left behind. The builder runs a privileged container, so it exists only during the build: the script removes it after the push build, or on a dry run after the images load for the smoke, and also when a run fails or is interrupted. To install Trivy into `~/.local/bin` without sudo:

```bash
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b "$HOME/.local/bin"
```

Pushing needs `docker login ghcr.io` with a classic personal access token that has the `write:packages` scope and nothing else. Give it an expiry: it can publish images under the project's name.

```bash
docker login ghcr.io -u <github-user> --password-stdin
```

Logs, build output and the worktree go under `~/.cache/nojoin-release/<version>/`. A successful run removes them; a failed run keeps them for inspection.

### Runtime Version Detection

The API takes its version from `NOJOIN_SERVER_VERSION`, then `/app/.build-version` (written when the image is built), then `docs/VERSION` in a source checkout. The latest published version comes from the GitHub Releases API, with GHCR tags and the raw `docs/VERSION` on `main` as fallbacks.

## Supply-Chain and Release Hardening

Keep these controls intact when changing CI, the release workflow, the release script or a Dockerfile.

### Pinned Actions and Base Images

- Pin every third-party action in [.github/workflows/](../.github/workflows/) to a full commit SHA with a trailing `# vX.Y.Z` comment. Never use a floating tag such as `@v5`, which can be repointed after review.
- Pin every Dockerfile base image by `@sha256:` digest, keeping the readable tag beside it. This includes the release BuildKit image in [docker/Dockerfile.buildkit](../docker/Dockerfile.buildkit), a file nothing builds that exists so Dependabot updates the pin.
- When you upgrade an action or image by hand, change the SHA or digest and its version together.

### Dependency-Update Policy

[.github/dependabot.yml](../.github/dependabot.yml) opens grouped pull requests weekly, with at most five open per ecosystem:

| Ecosystem | Directories | Groups |
| --- | --- | --- |
| GitHub Actions | `/` | `github-actions` |
| pip | `/requirements` | `python-dependencies` |
| npm | `/frontend` | `npm-production`, `npm-development` |
| Docker | `/docker`, `/frontend` | `docker-base-images` |

Dependabot moves a pinned SHA or digest and its version comment in the same pull request. Never replace a pin with a floating tag to simplify an update. Each `ignore:` entry in the file holds a package below a version, with the reason beside it; remove the entry when its blocker clears.

The maintainer ([CODEOWNERS](../.github/CODEOWNERS)) reviews update pull requests like any other change. A green CI run and a read of the changelog for breaking changes are the bar for a routine update; groups that touch runtime dependencies (`npm-production`, `python-dependencies`, base images) get a closer look. Security updates come first and merge once CI is green, and one is often what clears a release blocked by the image scan. A fixable CRITICAL or HIGH finding that cannot be taken yet can be accepted for a limited time with a dated, justified entry in [.github/trivyignore](../.github/trivyignore); [SECURITY.md](SECURITY.md#vulnerability-scanning-and-severity-policy) sets the policy. Findings with no upstream fix never block the release.

### Uncapped AI SDKs

`openai`, `anthropic` and `google-genai` are declared with `>=` only, so each image build installs the newest release and a major version arrives with no pull request. Three tests in [backend/tests/test_llm_services_unified.py](../backend/tests/test_llm_services_unified.py) guard the calls: `test_anthropic_kwargs_are_accepted_by_the_installed_sdk`, `test_openai_kwargs_are_accepted_by_the_installed_sdk` and `test_gemini_kwargs_are_accepted_by_the_installed_sdk` bind the keyword arguments the backend sends against the installed SDK's signatures, and the Gemini test builds its request config from the installed `google.genai.types`, which reject unknown fields. A parameter a release removes fails CI. When that happens, update the call sites; do not cap the SDK.

### Held Pins and Unfixable Advisories

Some versions are declared in several files that must agree, and a pin sometimes has to stay below the latest release because a companion package has not shipped a matching build. [scripts/check_held_pins.py](../scripts/check_held_pins.py) covers both. Its `--offline` half runs in CI and in `scripts/check.py`.

#### The torch stack

`torch` is declared in [requirements/test.txt](../requirements/test.txt) for CI, in [requirements/local.txt](../requirements/local.txt) for a GPU host, and through the `pytorch/pytorch` base image in [docker/Dockerfile.worker](../docker/Dockerfile.worker). `MATCHED_STACKS` records the version, and the drift check fails on any declaration that disagrees. The companions move by hand:

- **torchvision** (local.txt only) pins torch exactly, so it moves with every torch bump.
- **torchaudio** stays at 2.11.0. It is in maintenance mode, builds against torch's stable ABI, and supports every later torch.
- **torchcodec** wheels on PyPI target PyPI torch's CUDA, which cannot load on a base with another CUDA. Dockerfile.worker installs the CPU build of the version worker.txt resolved (it only decodes audio), and the build fails if it does not import.
- **onnxruntime-gpu** is pinned in Dockerfile.worker and has to match the base image's CUDA major: its PyPI builds target CUDA 12 up to 1.26.0 and CUDA 13 from 1.27.0.

The base image also fixes the worker's Python minor. The API image, CI, mypy and the Python prerequisite in this guide and in CONTRIBUTING.md must match it: `EXPECTED_PYTHON` and `PYTHON_DECLARATIONS` make the `--offline` run fail on drift, and a Dependabot `ignore:` entry keeps the API base on 3.12. The image tag names the torch version, not Python's, so run `python -V` in a new base before adopting it. The 2.14.1 image ships 3.12.

The pre-commit hooks must run the release CI pins. `HOOKED_TOOLS` makes the `--offline` run fail when the Ruff hook's `rev` in `.pre-commit-config.yaml` differs from the `ruff` pin in `requirements/lint.txt`. Dependabot bumps the pin and not the hook, so move the `rev` in the same pull request.

To move torch, change test.txt, local.txt, both `FROM` lines in Dockerfile.worker (tag and digest) and `MATCHED_STACKS` together, and move torchvision to its pair. Keep the base on the CUDA build PyPI's torch uses (CUDA 13.0 for 2.14.1), so that local.txt installs the same build without an index URL; pip ignores an `--index-url` written on a requirement line. Then run diarisation, Whisper, an ONNX engine and text embeddings on a GPU in the rebuilt worker image, because CI has no GPU and installs torch from PyPI. Dependabot raises the pip and Docker halves as separate pull requests, each of which fails the drift check alone, so combine them.

#### fastembed's Pillow cap

fastembed 0.9.0, the newest release, declares `pillow<13.0`. Pillow 13.0.0 is the first release with the fixes for 20 advisories, two of them critical (#526), and the cap serves fastembed's image models, which Nojoin never loads: it uses fastembed only for `TextEmbedding`. So fastembed is the one package in [requirements/worker-no-deps.txt](../requirements/worker-no-deps.txt), and every surface installs that file with `pip install --no-deps` after `worker.txt`: Dockerfile.worker, CI's `Backend tests` job and the [Fresh Checkout Setup](#fresh-checkout-setup). `worker.txt` pins everything else fastembed declares, the CPU `onnxruntime` included, which Dockerfile.worker then swaps for `onnxruntime-gpu`.

pip no longer checks fastembed's requirements, so [backend/tests/test_no_deps_requirements.py](../backend/tests/test_no_deps_requirements.py) does. It fails when a requirement is not pinned in `worker.txt` or `base.txt`, or is pinned outside the range fastembed accepts. The `huggingface-hub` hold in [dependabot.yml](../.github/dependabot.yml) now rests on that check. The test also fails once fastembed accepts the pinned Pillow, normally on Dependabot's fastembed bump. Then move fastembed back into `worker.txt`, delete `worker-no-deps.txt` and its install lines (this guide, CONTRIBUTING.md, CI, Dockerfile.worker and the comments in the requirements files), and delete the test. The worker image build also imports fastembed, and the import loads every dependency it declares.

#### Holding a pin

`HOLDS` is empty: no pin is held under an open advisory. Dependabot's `ignore:` stops version-update pull requests but not security alerts, and an advisory raises a fresh alert each time its range is revised, once per manifest that resolves the package (three for a package in test.txt and local.txt, which [requirements/dev.txt](../requirements/dev.txt) includes). To hold a pin:

1. **Record it in code.** Add a `HOLDS` entry with the blocking package and the version that resolves the advisory. The held package also needs a `MATCHED_STACKS` entry, since the drift check compares the pin with the files through that entry; the script fails a hold without one. Add the matching `ignore:` entry to [dependabot.yml](../.github/dependabot.yml). If the hold constrains anything else, as a held base image fixes Python, encode that constraint too.
2. **Add a Dependabot rule** under **Settings > Code security > Dependabot rules > New rule** (web UI only; there is no API). Set *Target alerts* to `package:<name>`, `ecosystem:<ecosystem>` and `severity:low`, tick **Dismiss alerts**, and choose **Indefinitely**. The rule dismisses again on every revision and every new manifest, and moderate and higher alerts still reach a person.
   - Choose *Indefinitely*, not *Until patch is available*: Dependabot only checks that a patched version exists, not that this graph can install it.
   - Filter on package and severity, not `ghsa_id`, so the rule covers every advisory on the package.
   - Add no scope filter. Dependabot reports these manifests as `runtime`, so a `development` filter, or the preset *Dismiss low impact issues for development-scoped dependencies*, matches nothing.
   - Keep the ecosystem filter: npm has a package called `torch`.
   - *Open a pull request to resolve alerts* cannot be unticked; the `ignore:` entry stops that pull request anyway.
3. **Dismiss the alerts already open**:

   ```bash
   gh api --method PATCH repos/Valtora/Nojoin/dependabot/alerts/<number> \
     -f state=dismissed \
     -f dismissed_reason=tolerable_risk \
     -f dismissed_comment="<why the risk is tolerable and what blocks the fix>"
   ```

   Valid reasons are `fix_started`, `inaccurate`, `no_bandwidth`, `not_used` and `tolerable_risk`, and the comment is capped at 280 characters. Prefer `tolerable_risk` to `not_used` unless you have confirmed that no dependency reaches the vulnerable code.
4. **Watch for the hold clearing.** [held-pin-watch.yml](../.github/workflows/held-pin-watch.yml) runs `scripts/check_held_pins.py` against PyPI monthly and opens one `held-pin` issue when the blocker catches up. It sees only the blocker's version number, and a blocker can drop the coupling without a release, so re-read its compatibility notes whenever an alert on the held package is revised.
5. **Lift it** by moving the whole stack, then deleting the `HOLDS` entry, the `ignore:` entry and the Dependabot rule.

If a held advisory ever rises above the risk the project accepts, drop the blocking dependency rather than widen the hold.

### Image Provenance, SBOM, and Signing

`release.py` pushes every image with BuildKit provenance and SBOM attestations (`--provenance=mode=max --sbom=true`), stored in the image index rather than as cosign attestations. The `sign-images` job in [release.yml](../.github/workflows/release.yml) signs each image with cosign 3 keyless (OIDC) signing, which needs `id-token: write` and binds the signature to the workflow's identity; cosign 2 cannot find these signatures. Operator verification is in [DEPLOYMENT.md](DEPLOYMENT.md#verifying-an-image-before-deploying).

The provenance carries no GitHub builder ID, so the workflow checks each image's `org.opencontainers.image.revision` label against the tag's commit before signing. The script builds from a clean `git worktree`, so nothing untracked reaches an image, and sets `SOURCE_DATE_EPOCH` to the commit time, so every bake of the commit yields the same image config and each pushed config digest must equal the one scanned and smoke tested.

### Gated Tag Publication

Nothing reaches the registry until every gate has passed on the host. In release.yml, `publish-mutable-tags` needs `sign-images` and `publish-release-notes` needs `publish-mutable-tags`, so the rolling `latest` and `major.minor` tags move only after all four images are verified and signed. A failure after the push, in signing for instance, leaves the immutable tags with a failed run but never moves `latest`. Keep this ordering when editing the script or the workflow.

### Release Gates

[scripts/release.py](../scripts/release.py) runs these on the images it has just built, before it pushes anything.

- **Vulnerability and secret scan.** Trivy scans each image's OCI layout with `--severity CRITICAL,HIGH --ignore-unfixed --exit-code 1`, the exceptions in [.github/trivyignore](../.github/trivyignore), and the secret-scanner allow rules in [.github/trivy-secret.yaml](../.github/trivy-secret.yaml).
- **Health and non-root smoke.** The same images, loaded into Docker, boot from [docker-compose.example.yml](../docker-compose.example.yml) with Postgres, Redis and the socket proxy under their own Compose project, `nojoin-release-smoke`. `api` and `frontend` must reach `healthy`, and each long-running process must run as a non-root uid: the api's PID 1, the frontend, and `id -u` through the worker and worker-io entrypoints, which drop from root to `appuser` with `gosu`. The worker needs a GPU to boot, so it gets only the uid check.
- **Subscription CLI advisories.** Trivy cannot see inside the Codex CLI, a stripped native binary. [docker/Dockerfile.worker-io](../docker/Dockerfile.worker-io) verifies both subscription CLIs with `npm audit signatures` and records their versions in `/usr/local/share/nojoin/cli-versions.json`, and the script looks each version up in the GitHub advisory database. The CLIs are unpinned, so a hit means rebuilding once the fixed release is on npm.

Some findings need an image-level fix rather than a requirements bump:

- **Worker base Python.** The base's own interpreter keeps older copies of packages such as `pillow` and `urllib3` on disk. [docker/Dockerfile.worker](../docker/Dockerfile.worker) upgrades them with `/usr/bin/python3 -m pip install --break-system-packages`, in step with the requirements pins, then removes that system pip. `certifi` has no requirements pin and the worker's venv imports the system copy, so the same step takes its newest release above a floor that keeps the last advisory fix.
- **Base OS packages.** The distribution often ships a fix before the base image is republished, and the PyTorch base is never republished for one. [docker/Dockerfile.api](../docker/Dockerfile.api) and Dockerfile.worker run `apt-get upgrade -y` in both stages. The builder stages upgrade because pip verifies PyPI against the OS trust store, so a CA the distribution has distrusted stays trusted for the install until the upgrade. Upgrading named packages instead missed `ca-certificates` in the worker (#585). Still merge Dependabot's digest bumps. `linux-libc-dev` findings concern kernel headers, not the host kernel, so a dated trivyignore entry is acceptable where no fixed build exists.
- **A wrong advisory range.** Confirm the fix in the package source, pin every package that ships the affected code, add a dated trivyignore entry citing the evidence, and dismiss the Dependabot alert as `inaccurate`. Entries match on the CVE id alone, so note in the entry that it would also hide a real regression if the pin were lowered.
- **Build tooling.** Remove tools the runtime never runs, as the frontend runner drops `npm` and the worker uninstalls `uv`. Remove a Python tool with pip rather than by deleting its binaries: Trivy reads the package's `dist-info`, which a deleted binary leaves behind.
- **The API image's pip.** Dockerfile.api installs `backend.txt` with the hash-pinned pip in [requirements/pip.txt](../requirements/pip.txt), not with the older pip and vendored `certifi` the base ships. It then uninstalls that pip and the base image's own, and the runtime has none. Dependabot moves the pin and its hashes.
- **A distrusted root CA.** The scan reports some trust stores as outdated packages and cannot see others, so check every bundle an image carries: the OS bundle (`/etc/ssl/certs/ca-certificates.crt`, which the LLM SDKs read on Linux through `httpx2`), `certifi`, any pip's vendored `certifi` (Ubuntu's pip reads the OS bundle instead), grpcio's `roots.pem`, Node's built-in store (`require('tls').rootCertificates`) and the native subscription CLIs. The OS bundle carries no names, so grep each bundle for a fragment of the root's base64 PEM, not for its name.
- **Secrets in the build context.** [.dockerignore](../.dockerignore) excludes key and certificate files, with a `**/` prefix so nested paths match. Release builds come from a clean worktree; a local build sees every untracked file the ignore list misses, so the secret scanner can flag a local build and not a release.
- **Secret-scanner false positives.** Add a narrow path allow rule to [.github/trivy-secret.yaml](../.github/trivy-secret.yaml) with a comment saying why the match is not a credential. A rule id in `.github/trivyignore` would switch the rule off everywhere. Never quote the matched text in a file the images copy, which includes everything under `.github/`.

### Automated Release Notes

After the rolling tags move, `publish-release-notes` finds the previous tag with `git describe`, renders that range with `scripts/render_release_notes.py`, looks up the published digests, and fills [.github/release-notes-template.md](../.github/release-notes-template.md), replacing `{{VERSION}}`, `{{IMAGE_DIGESTS}}` and `{{CHANGELOG}}`. The changelog goes under **Changes**, after the hand-written sections (Highlights, Upgrade, Migration, Rollback, Known Issues and Browser-Capture Compatibility), with commits grouped by Conventional Commit type and the minor ones folded into a collapsed block. Rewrite the notes around the three placeholders, not over them: `scripts/validate_docs.py`, which the `Docs validation` job runs on every pull request, fails when one is missing. `make_latest` follows `publish_latest`.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)

## Localhost Dev Compose Template

Copy this into your ignored `docker-compose.yml`. Its worker lanes, shared environment and API environment must match `docker-compose.example.yml`; `backend/tests/test_dev_compose_template.py` fails if they drift.

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
  # Shared, not api-only: the worker lanes resolve the public origin too, for
  # calendar webhook URLs and the telemetry local-origin flag.
  WEB_APP_URL: ${WEB_APP_URL:-https://localhost:14443}
  # Local development stacks should not contribute to the public telemetry
  # dataset; unset it here if you deliberately want to exercise the ping.
  NOJOIN_TELEMETRY_ENABLED: ${NOJOIN_TELEMETRY_ENABLED:-false}
  NOJOIN_TELEMETRY_ENDPOINT: ${NOJOIN_TELEMETRY_ENDPOINT:-}
  # Exports have to be readable by whoever serves them: a worker writes the
  # archive and the API streams it back, so both sides must resolve the same
  # directory. Setting it on one service only breaks the download.
  BACKUP_EXPORT_DIR: ${BACKUP_EXPORT_DIR:-}
  NOJOIN_UMASK: ${NOJOIN_UMASK:-}
  NOJOIN_STORAGE_HYGIENE: ${NOJOIN_STORAGE_HYGIENE:-}
  # Left empty: config_manager reads an empty value as gemini, and the setup
  # wizard can only report that no provider was chosen if the variable reaches
  # it empty.
  LLM_PROVIDER: ${LLM_PROVIDER:-}
  GEMINI_API_KEY: ${GEMINI_API_KEY:-}
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
  OLLAMA_API_URL: ${OLLAMA_API_URL:-http://host.docker.internal:11434}
  # No default here on purpose. config_manager owns the application default and
  # skips an empty value, so an unset variable falls through to it; repeating a
  # number here would create a second default to keep in step.
  OLLAMA_CONTEXT_WINDOW: ${OLLAMA_CONTEXT_WINDOW:-}
  SECONDARY_LLM_PROVIDER: ${SECONDARY_LLM_PROVIDER:-}
  SECONDARY_GEMINI_API_KEY: ${SECONDARY_GEMINI_API_KEY:-}
  SECONDARY_OPENAI_API_KEY: ${SECONDARY_OPENAI_API_KEY:-}
  SECONDARY_ANTHROPIC_API_KEY: ${SECONDARY_ANTHROPIC_API_KEY:-}
  SECONDARY_OLLAMA_API_URL: ${SECONDARY_OLLAMA_API_URL:-http://host.docker.internal:11434}
  SECONDARY_OLLAMA_CONTEXT_WINDOW: ${SECONDARY_OLLAMA_CONTEXT_WINDOW:-}
  DATA_ENCRYPTION_KEY: ${DATA_ENCRYPTION_KEY:-}
  GOOGLE_OAUTH_CLIENT_ID: ${GOOGLE_OAUTH_CLIENT_ID:-}
  GOOGLE_OAUTH_CLIENT_SECRET: ${GOOGLE_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_CLIENT_ID: ${MICROSOFT_OAUTH_CLIENT_ID:-}
  MICROSOFT_OAUTH_CLIENT_SECRET: ${MICROSOFT_OAUTH_CLIENT_SECRET:-}
  MICROSOFT_OAUTH_TENANT_ID: ${MICROSOFT_OAUTH_TENANT_ID:-common}

# Runtime settings shared by every worker lane. Deliberately carries no image:
# or build:, so the one lane that runs a different image (worker-parse) cannot
# inherit the wrong one. A YAML merge key replaces a whole key; it never merges
# the values under it.
x-worker-runtime: &worker-runtime
  volumes:
    - ./data:/app/data
    - model_cache:/home/appuser/.cache
    - /sys/class/drm:/sys/class/drm:ro
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

# The lanes that run the shared worker image built from Dockerfile.worker.
# worker-io overrides the image and build below; worker-parse must not use this
# anchor at all, because it runs the worker-io image and would otherwise tag a
# plain worker build as nojoin-dev-worker-io:local.
x-worker-base: &worker-base
  <<: *worker-runtime
  build:
    context: .
    dockerfile: docker/Dockerfile.worker
  image: nojoin-dev-worker:local
  pull_policy: never

x-worker-environment: &worker-environment
  <<: *shared-app-environment
  NOJOIN_CODEX_PATH: ${NOJOIN_CODEX_PATH:-}
  XDG_CACHE_HOME: /home/appuser/.cache
  HF_HOME: /home/appuser/.cache/huggingface
  OMP_NUM_THREADS: 2
  MKL_NUM_THREADS: 2
  OPENBLAS_NUM_THREADS: 2
  VECLIB_MAXIMUM_THREADS: 2
  NUMEXPR_NUM_THREADS: 2

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
      - model_cache:/shared_model_cache:ro
    environment:
      <<: *shared-app-environment
      DOCKER_HOST: tcp://socket-proxy:2375
      MCP_ENABLED: ${MCP_ENABLED:-true}
      MCP_ANONYMOUS_DISCOVERY: ${MCP_ANONYMOUS_DISCOVERY:-true}
      NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS: ${NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS:-true}
      FIRST_RUN_PASSWORD: ${FIRST_RUN_PASSWORD:?Set FIRST_RUN_PASSWORD in .env}
      XDG_CACHE_HOME: /shared_model_cache
      HF_HOME: /shared_model_cache/huggingface
      # Trust proxies by IP or CIDR, never by container name, whenever the edge
      # proxy runs on a network the API is not itself attached to: the API
      # resolves these entries at startup and drops any name it cannot resolve,
      # logging only a count, so every external client shares one rate-limit bucket.
      NOJOIN_TRUSTED_PROXIES: ${NOJOIN_TRUSTED_PROXIES:-127.0.0.1,::1,nginx}
      NOJOIN_STALL_WATCHDOG_ENABLED: ${NOJOIN_STALL_WATCHDOG_ENABLED:-}
      NOJOIN_STALL_WATCHDOG_INTERVAL_SECONDS: ${NOJOIN_STALL_WATCHDOG_INTERVAL_SECONDS:-}
      NOJOIN_STALL_WATCHDOG_LAG_THRESHOLD_SECONDS: ${NOJOIN_STALL_WATCHDOG_LAG_THRESHOLD_SECONDS:-}
      NOJOIN_STALL_WATCHDOG_PRESSURE_THRESHOLD: ${NOJOIN_STALL_WATCHDOG_PRESSURE_THRESHOLD:-}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      socket-proxy:
        condition: service_started
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import json, sys, urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/api/health', headers={'X-Forwarded-Proto': 'https'}); data = json.load(urllib.request.urlopen(req, timeout=3)); sys.exit(0 if data.get('status') == 'ok' else 1)\"",
        ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  # Four worker lanes, matching production. Each drains one Celery queue (see
  # backend/celery_app.py TASK_ROUTES), so local work is routed exactly as it is
  # live. Worker Concurrency Lanes in docs/DEPLOYMENT.md explains the split.
  #
  # GPU lane: finalize, live ASR, embeddings. Single-slot (--pool=solo
  # --concurrency=1). This is the ONLY worker with GPU access; remove the deploy
  # block on a CPU-only host.
  worker-gpu:
    <<: *worker-base
    container_name: nojoin-dev-worker-gpu
    environment:
      <<: *worker-environment
      NVIDIA_VISIBLE_DEVICES: ${NVIDIA_VISIBLE_DEVICES:-all}
      NVIDIA_DRIVER_CAPABILITIES: ${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}
      WHISPER_ENABLE_WORD_TIMESTAMPS: ${WHISPER_ENABLE_WORD_TIMESTAMPS:-true}
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    command:
      [
        "celery", "-A", "backend.celery_app.celery_app", "worker",
        "-Q", "gpu", "--pool=solo", "--concurrency=1", "--loglevel=info",
      ]

  # CPU lane: proxy generation, backups, and the ffmpeg transcode of segments
  # from clients older than FLAC capture. No GPU.
  worker-cpu:
    <<: *worker-base
    container_name: nojoin-dev-worker-cpu
    environment: *worker-environment
    command:
      [
        "celery", "-A", "backend.celery_app.celery_app", "worker",
        "-Q", "cpu", "--pool=prefork", "--concurrency=3", "--loglevel=info",
      ]

  # IO/LLM lane: Meeting Edge, notes, chat embeddings, calendar sync, cleanup.
  # Owns Celery Beat (-B) so the periodic jobs fire exactly once across the
  # stack. No other lane may carry -B.
  worker-io:
    <<: *worker-base
    container_name: nojoin-dev-worker-io
    # CLI OAuth AI mode needs Node plus the subscription CLIs, layered on the
    # shared worker image by docker/Dockerfile.worker-io.
    image: nojoin-dev-worker-io:local
    build:
      context: .
      dockerfile: docker/Dockerfile.worker-io
      # Build the shared worker base FIRST, then layer worker-io on it. Without
      # this the two builds run in parallel and worker-io can ship stale code on
      # the previous base.
      additional_contexts:
        worker_base: "service:worker-gpu"
      args:
        WORKER_BASE_IMAGE: worker_base
    environment: *worker-environment
    command:
      [
        "celery", "-A", "backend.celery_app.celery_app", "worker",
        "-Q", "io", "-B", "-s", "/app/data/celerybeat-schedule",
        "--pool=prefork", "--concurrency=4", "--loglevel=info",
      ]

  # Document parsing, isolated from every other lane. A parse has no page cap,
  # so one large upload can hold a worker slot for a long time; on the io lane
  # that would degrade Meeting Edge during a live meeting. Reuses the worker-io
  # IMAGE (no extra build) because visual parsing may route through a
  # subscription CLI whose binaries ship only there.
  worker-parse:
    # *worker-runtime, NOT *worker-base: this lane runs the worker-io image, so
    # inheriting the base build would build Dockerfile.worker and tag the result
    # nojoin-dev-worker-io:local, racing worker-io for that tag.
    <<: *worker-runtime
    container_name: nojoin-dev-worker-parse
    image: nojoin-dev-worker-io:local
    pull_policy: never
    depends_on:
      worker-io:
        condition: service_started
    environment: *worker-environment
    command:
      [
        "celery", "-A", "backend.celery_app.celery_app", "worker",
        "-Q", "parse", "--max-tasks-per-child=25",
        "--pool=prefork", "--concurrency=2", "--loglevel=info",
      ]

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
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:14141/"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 15s
    restart: unless-stopped
    networks:
      - nojoin_net
    logging: *default-logging

  nginx-dev:
    container_name: nojoin-dev-nginx
    image: nginx:alpine
    ports:
      - "${NOJOIN_BIND_ADDRESS:-127.0.0.1}:14141:80"
      - "${NOJOIN_BIND_ADDRESS:-127.0.0.1}:14443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx:/etc/nginx/certs
      - ./docker/init-ssl.sh:/docker-entrypoint.d/99-init-ssl.sh
    depends_on:
      frontend:
        condition: service_healthy
      api:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "curl -k -f -s -o /dev/null https://127.0.0.1/api/health && curl -k -f -s -o /dev/null https://127.0.0.1/",
        ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped
    networks:
      nojoin_net:
      proxy_net:
        aliases:
          - nojoin-dev-nginx
    logging: *default-logging

volumes:
  postgres_data:
  model_cache:
  redis_data:

networks:
  nojoin_net:
    driver: bridge
  proxy_net:
    external: true
```
