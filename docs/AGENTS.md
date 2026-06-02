# Nojoin AI Agent Instructions

## Start Here

- Read [../README.md](../README.md) for product scope and major entry points.
- Read [DEVELOPMENT.md](DEVELOPMENT.md) before running local commands or changing build tooling.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing component boundaries or request flows.
- Read [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, GPU or CPU mode, `.env` setup, and remote access configuration.
- Read [CALENDAR.md](CALENDAR.md) before touching calendar OAuth or sync behavior.
- Read [SECURITY.md](SECURITY.md) before changing auth, tokens, encryption, or exposure of sensitive data.

## Project Context

Nojoin is a distributed meeting intelligence platform. The system records live meeting audio directly from supported browsers, processes the data on a GPU-enabled Docker backend, and presents insights via a Next.js web interface.

**Core Philosophy**: Centralized Intelligence (GPU server), Ubiquitous Access (Web), Configurable Privacy (Self-hosted with optional local-only AI).

## Architecture & Patterns

### Backend (FastAPI + Celery)

- **Service Boundary**: The `backend/` directory handles API requests and offloads heavy processing to Celery workers via Redis.
  - **Rule**: API endpoints must NEVER run heavy inference (Whisper, Pyannote, LLMs) synchronously. Heavy tasks must be dispatched to Celery.
- **Data Access**: `SQLModel` is used for ORM. Models are located in `backend/models/`.
- **Dependency Injection**: `backend.api.deps` must be used for DB sessions (`SessionDep`) and current user (`CurrentUser`).
- **Heavy Processing**:
  - **Location**: `backend/worker/tasks.py`.
  - **Constraint**: Heavy libraries (torch, whisper, pyannote) must be imported **inside** the task function to keep the API lightweight and ensure fast startup times.
  - **Pipeline**: Validation -> VAD (Silero) -> Proxy Creation (Alignment) -> Transcribe (Whisper) -> Diarize (Pyannote) -> Phantom Speaker Filter -> Merge -> Voiceprint Extraction -> Deterministic Speaker Resolution (manual names, merge handling, global matches) -> Rolling Diarization Window Reconciliation (replays live-lane windows to apply speaker boundary corrections) -> Frame-level Segmentation Refinement (re-splits boundary-flagged utterances using per-frame `pyannote/segmentation-3.0` probabilities; see `backend/processing/segmentation_refinement.py`) -> Automatic Meeting Intelligence (one provider call for unresolved speaker suggestions, title, and Markdown notes when AI is configured).
  - **Manual AI Flows**: Automatic AI enhancement is provider-gated and no longer uses separate per-feature toggles. Manual `Generate Notes` remains notes-only, and manual `Retry Speaker Inference` remains speaker-only.
  - **Meeting Edge**: Live guidance is a separate worker path from end-of-processing meeting intelligence. It consumes recent live transcript context plus optional user focus text and user notes, expects a strict JSON contract, and may use a provider-specific Meeting Edge live model that falls back to the provider's main model when unset.
  - **Transcription Engine**: The Transcribe step dispatches to a pluggable engine (`backend/processing/engines/`), selected by the `transcription_backend` config key. The normal live and final recording flow uses the same selected engine so live transcription can be reused during final processing. Whisper is the default; Parakeet and Canary (both onnx-asr, sharing the `OnnxAsrEngine` base) are selectable. Parakeet is much faster on supported NVIDIA systems, but trades off some accuracy and language coverage compared with Whisper. Different-engine transcription belongs to explicit manual reprocessing after Settings are changed.
  - **Live Transcription Latency**: Browser capture uploads short WebM/Opus, Ogg/Opus, or MP4 audio segments that the worker transcodes to WAV for the live lane. The backend live lane sequence-gates those uploads, carries trailing speech across segment boundaries, and force-emits continuous speech after about 8 seconds. The recording page should show the in-flight Meeting Edge/status workspace immediately for active recordings; provisional live transcript text is intentionally not rendered there anymore.
  - **Live Window State**: `RecordingAudioWindowManifest.status` is a legacy compatibility projection. Use `asr_status` for live/catch-up ASR coverage and `diarization_status`, `diarization_config_hash`, and `diarization_window_result_id` for rolling or catch-up speaker-window coverage. Do not treat legacy `live_processed` as diarization-complete.
  - **Live Speaker Assignment**: The live lane uses online voice embeddings to keep stable `LIVE_XX` speaker labels. Short or embedding-less regions should fall back to the most recent stable live label rather than creating a new speaker per fragment. Embedding extraction uses a centred window trimmed away from segment edges to reduce noise-pickup bias. Manual speaker edits and live text edits are authoritative and must survive final processing.
  - **Final Live Reuse**: Live/final transcript reuse must align by stable utterance identifiers or clear one-to-one time overlap. Never use equal array length or array index position as proof that a live segment maps to a final segment. Preserve ambiguous live evidence as metadata and keep final ASR/diarization output.
  - **Phantom Speaker Filter**: Post-diarization stage (`backend/processing/phantom_filter.py`) that detects and reassigns segments caused by non-speech sounds (notifications, background noise). Uses heuristic detection (duration/segment count) followed by embedding-based validation. Thresholds are defined as named constants in `phantom_filter.py` (`PHANTOM_MAX_DURATION_S`, `PHANTOM_MAX_SEGMENTS`, `PHANTOM_EMBEDDING_FLOOR`, `PHANTOM_MERGE_THRESHOLD`).
  - **Speaker Identification Constants**: All speaker matching thresholds are centralised in `backend/processing/embedding.py`. Do not hardcode threshold values elsewhere; import and reference the named constants (`IDENTIFICATION_THRESHOLD`, `AUTO_UPDATE_THRESHOLD`, `MARGIN_OF_VICTORY`, `DRIFT_GUARD_THRESHOLD`, `SCAN_MATCH_THRESHOLD`, `UI_SHOW_MATCH_THRESHOLD`, `UI_STRONG_MATCH_THRESHOLD`).
  - **PyTorch 2.6+ & Safe Globals**: The project uses PyTorch 2.6+, which defaults `weights_only=True` in `torch.load` for security.
    - **Issue**: This blocks loading of custom classes (like `pyannote.audio.core.task.Specifications` and `torch.torch_version.TorchVersion`) from model checkpoints.
    - **Solution**: These classes must be explicitly added to the safe globals list using `torch.serialization.add_safe_globals([...])` **before** loading the model. This is handled at the module level in `embedding_core.py` and `diarize.py`.
  - **Configuration**: `backend.utils.config_manager` is used to handle system and user-specific settings persisted in `data/config.json`. Do not add parallel ad hoc config storage.

### Frontend (Next.js + Zustand)

- **State Management**: **Zustand** (`src/lib/store.ts`) is used for global UI state (navigation, selection, filters). Prop drilling should be avoided.
- **API Layer**: All API calls MUST go through `src/lib/api.ts`.
  - Browser authentication uses the Secure HttpOnly session cookie issued by `/api/v1/login/session`.
  - Explicit Bearer tokens from `/api/v1/login/access-token` are reserved for non-browser API clients.
  - Browser recording operations use the authenticated session cookie with trusted-origin and ownership checks.
  - `/api/v1/recordings/init` creates an uploading recording for the current user. Browser segment upload, pause, resume, discard, and finalisation must preserve monotonic 0-based segment sequencing and paused-recording lock behavior.
  - `force_password_change` is enforced server-side. Flagged users may only fetch `/api/v1/users/me`, update `/api/v1/users/me/password`, or log out until they rotate their password.
  - Never put bearer tokens into URL query strings or other browser-visible locations.
- **Routing**: The App Router (`src/app/`) is utilized.
- **Styling**: Tailwind CSS is the standard styling framework.
- **Components**: Functional components in `src/components/` are preferred.

### Browser Capture

- **Structure**: Browser capture modules live under `frontend/src/lib/capture/`.
- **Platform Support**: Chrome, Edge, Brave, Arc, and other Chromium-family browsers on Windows and Linux support shared-audio live capture. Chrome on Android and iOS supports microphone-only live capture. Firefox, Safari, other mobile browsers, and Chromium browsers on macOS are not supported for live capture.
- **Capture Strategy**:
  - `getDisplayMedia` captures the user-selected tab, window, or screen and its shared audio track when the browser grants one on desktop.
  - `getUserMedia` captures the local microphone. On mobile Chrome, this is the only live capture source.
  - Web Audio mixes shared audio and microphone audio, applies gain, and feeds analyser state for the live waveform. Mobile Chrome records microphone-only audio.
  - MediaRecorder creates short WebM/Opus, Ogg/Opus, or MP4 audio segments that upload sequentially to `/recordings/{id}/segment`.
  - The worker transcodes each browser segment to canonical 16 kHz, two-channel WAV before live transcription and final concatenation. Channel 0 is shared/system audio when available and channel 1 is microphone audio; ASR/VAD may consume a mono derivative from those preserved channels, but the browser-live asset itself is not mono.
- **Lifecycle**:
  - Refreshing, closing, or navigating away from the Nojoin tab during capture marks the recording `PAUSED`.
  - A paused recording blocks new capture until the user resumes or discards it.
  - Switching to another browser tab, window, or application must not pause capture.
  - Retired native-helper routes should remain terminal and should not issue credentials or accept uploads.

## Critical Workflows

### Commands

- **Start Infrastructure**:
  - **Operator deployment**: copy the compose and env templates to local files, then run `docker compose up -d`
  - **CPU**: `docker compose up -d` after removing the `deploy` section from `docker-compose.yml`
  - **Local source development**: use the host and local-compose workflows described in `docs/DEVELOPMENT.md`
  - **Remote Access**: Ensure `.env` is configured with the correct `WEB_APP_URL`.
- **Migrations**:
  - Apply: `alembic upgrade head`
  - Create: `alembic revision --autogenerate -m "message"`
- **Backend Tests**:
  - Ensure the virtual environment is active (e.g., `source .venv/bin/activate`)
  - Run: `pytest` or `pytest backend`
- **Frontend**:
  - Development: `cd frontend && npm install && npm run dev`
  - Verification: `cd frontend && npm run build`
  - Lint: `cd frontend && npm run lint`
- **Browser Capture Verification**:
  - Unit tests: `cd frontend && npm run test -- --run src/lib/capture`
  - Manual smoke: start Nojoin in a supported desktop Chromium browser, share a meeting tab with audio enabled, verify waveform and Meeting Edge or processing-state updates, pause/resume, stop/finalize, and unsupported-browser messaging where practical. For mobile capture changes, also smoke Chrome on Android or iOS microphone-only recording with the tab open and the phone awake.

### Release Workflow (Unified Lock-step)

The project uses a single Git Tag (`vX.Y.Z`) to trigger the server and frontend release pipeline.

1. **Update Version**: Update `docs/VERSION` to the new version (e.g., `0.6.0`).
2. **Commit and Tag**:
   - Commit the change.
   - Create a tag matching the version: `git tag v0.6.0`
   - Push the tag: `git push origin v0.6.0`

3. **CI/CD Pipeline** (`.github/workflows/release.yml`):
  **Trigger**: The push of the `v*` tag automatically triggers the pipeline.

  **Step 1: Docker Build**: Builds and pushes API, Worker, and Frontend images to GHCR with tags `latest` and `v0.6.0`. The API image also embeds the resolved server version for runtime display in Settings.

  **Step 2: Release Metadata**: Publish or update the GitHub Release for the same tag so Settings can surface release notes. Browser capture compatibility belongs in those release notes when capture behavior changes. The current workflow does not create the GitHub Release automatically.

**Important**:

- **Versioning**: Strict Semantic Versioning (`vX.Y.Z`).
- **Source of Truth**: The Git Tag is the single source of truth for published releases. Local source builds use `docs/VERSION`. The API image embeds the resolved server version at build time.
  - **Version Detection**: The API resolves the running version from build metadata embedded into the image (`NOJOIN_SERVER_VERSION` and `/app/.build-version`), falling back to bundled or local `docs/VERSION` in development and test contexts. User-facing release metadata is resolved from GitHub Releases first, with GHCR tags and the GitHub raw `docs/VERSION` file only used as version fallbacks if release metadata is unavailable.

## Code Style & Conventions

### Python (Backend)

- **Type Hints**: Mandatory for all function arguments and return values.
- **Imports**: Group standard lib, third-party, and local imports.
- **Error Handling**: Use `HTTPException` in API endpoints.

### TypeScript (Frontend)

- **Interfaces**: Define shared types in `src/types/index.ts`.
- **Strict Mode**: No `any`.

### Browser Capture (Frontend)

- Keep capture lifecycle, recorder, upload, and status behavior covered by focused Vitest tests.
- Use browser feature detection for capture support instead of user-agent-only checks wherever possible.
- Preserve the unsupported-browser review/admin path when changing capture gating.

## Quality Assurance & Build Safety

- **Frontend Verification:**
  - **Rule:** After ANY change to frontend code (`frontend/src/**/*`), a build check MUST be run to catch type errors that dev mode misses.
  - **Command:** `cd frontend && npm run build`.
  - **Why:** Next.js dev mode is lenient; production builds are strict.
- **Type Safety:**
  - **Rule:** When adding new data fields (e.g., to Settings or Models), update the TypeScript interfaces in `frontend/src/types/index.ts` **FIRST**.
  - **Rule:** Do not use `any` unless absolutely necessary to bypass library bugs.
- **Import Verification:**
  - **Rule:** When refactoring or moving code, verify that all imports in dependent files are updated. Use `grep_search` to find usages of moved symbols.
- **Client-Side Safety:**
  - **Rule:** Never assume `API_BASE_URL` is absolute. Always use safe URL construction (e.g., `new URL(path, window.location.origin)`) or manually check and prepend origin to handle both relative (production) and absolute (dev) paths.

## Related Docs

- [USAGE.md](USAGE.md): End-user workflows and UI behavior.
- [ADMIN.md](ADMIN.md): Roles, invitations, password rotation, and admin operations.
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md): Backup contents, restore behavior, and sensitivity model.
- [CAPTURE.md](CAPTURE.md): Browser capture setup, support matrix, and troubleshooting.
- [PRD.md](PRD.md): Product intent and longer-term scope.
- [README.md](README.md): Documentation index by task.

## Working Style

- Prefer small, targeted changes that match existing patterns in the touched area.
- Link to the relevant docs instead of copying large procedural sections into new files.
- If a task touches auth, calendar, processing, or release behavior, read the relevant doc before editing.

## Agent Interaction Rules

### The Workflow Loop

1. **REQUIREMENT**: The user states a feature. You then read the AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md files for context.
2. **PLANNING**: Produce a detailed plan. Consider signal propagation and dependencies.
3. **APPROVAL**: Wait for user confirmation.
4. **IMPLEMENTATION**: Generate robust code. Do not delete existing functionality unless planned.
5. **UI DUPLICATION**: When modifying the **Context Menu** for recordings, remember that there are TWO places to update:
   - `frontend/src/components/RecordingCard.tsx`: The main grid view.
   - `frontend/src/components/Sidebar.tsx`: The sidebar list view.
   - **Failure to update both will result in inconsistent behavior.**
6. **TESTING**: The user performs manual testing but the agent should run the relevant host-side build checks and rebuild any locally customised container services when needed to catch build-time errors and ensure changes are reflected in the environment. The agent should also provide detailed instructions for testing the new feature, including any necessary setup steps, expected outcomes, and edge cases to consider.
7. **COMPLETION**: Update the docs as needed.

### Constraints

- **NO GIT COMMANDS**: Never push/pull automatically. Provide text for messages.
- **NO EMOJIS**: Keep output strict and professional.
- **TONE**: Objective, results-oriented. No fluff.
- **COMMENTS**: Add comments to code where the code is non-obvious. Comments should be brief, professional, and to the point with absolutely zero 'developer thought' or 'developer intent' or 'developer reasoning' style comments.
