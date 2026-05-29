# Nojoin - Product Requirements Document (PRD)

## 1. Introduction

**Application Name:** Nojoin
**Purpose:** Nojoin is a distributed, containerized meeting intelligence platform. The system enables users to record meeting audio from a supported browser, process the data centrally on a GPU-enabled server, and access transcripts, diarization, and AI-generated insights via a web interface.

**Core Philosophy:**

- **Centralized Intelligence:** Computationally intensive tasks (Whisper/Pyannote) are executed on a dedicated server.
- **Ubiquitous Access:** Meeting management and viewing are accessible from any device with a web browser.
- **Configurable Privacy:** The self-hosted architecture ensures audio and transcripts remain within the user's control. Users can run Nojoin in a pure 'Private' mode using a local Ollama instance for AI features. Utilizing remote LLM features (OpenAI, Anthropic, Gemini) will send transcripts to external providers.

---

## 2. System Architecture

The application comprises three distinct subsystems:

### 2.1 The Server (Dockerized)

The core processing unit is hosted on a machine with NVIDIA GPU capabilities.

- **Registry:** Images are hosted on the GitHub Container Registry (GHCR).
- **API Service:** A FastAPI-based REST API manages data and client communication. It runs in a lightweight container (Python Slim) without heavy machine learning dependencies.
- **Worker Service:** A Celery-based background worker handles resource-intensive tasks (transcription, diarization) using the NVIDIA Runtime. It runs in a container with PyTorch and CUDA support.
- **Database:** PostgreSQL serves as the single source of truth for all metadata, transcripts, and speaker profiles.
- **Broker:** Redis manages the task queue and caching.
- **Setup Wizard:** Collects critical user settings (LLM Provider, API Keys, HuggingFace Token) during the creation of the first admin account.
  - **Optional AI Configuration:** AI provider setup is optional. If no provider is configured, recordings still process through transcription and diarisation, but automatic AI enhancement is skipped until configuration is added later.
  - **Auto-Fill:** Settings can be pre-configured via environment variables (defined in `.env`) to simplify the setup process for valid keys.
- **Database Initialization:** Automatically handles schema creation and migrations on startup.
- **Storage:** Docker Volumes provide persistent storage for raw audio and model artifacts.
- **Reverse Proxy:** Nginx handles SSL termination and routing (Port 14443).

### 2.2 The Web Client (Next.js)

The primary user interface for interacting with the system.

- **Framework:** Next.js (React) with TypeScript.
- **Styling:** Tailwind CSS for a fully responsive design, including mobile-optimized views.
- **Functionality:** Dashboard, playback, transcript editing, speaker management, system configuration, and a dedicated live capture / processing workspace for in-flight meetings.
- **Dashboard Iteration Two:** The root route now serves as an operational dashboard with `Meet Now` capture controls, recent-meeting access, an interactive calendar shell, an inline personal Task List, and clear routing into the tasks and recordings workspaces.
  - **Calendar Shell:** Users can browse prior and future months, jump back to today with a dedicated `Today` action, and switch between Month and Agenda modes. When calendar integrations are connected, month dots and agenda markers use per-calendar colours so different sources remain visually distinct. Until a real calendar integration is connected, the dashboard shows honest empty states rather than mock events.
  - **Task List:** Personal tasks are grouped into a Task List on the dashboard for fast capture and day-to-day follow-up.
  - **Task Capture:** The Task List is grouped directly beneath `Meet Now` on larger screens and supports inline entry with `Enter` to save, `Escape` to cancel, double-click title editing on tasks, optional date-time deadlines, archive, delete, and a live time-remaining badge beside each active deadline control. Archived tasks are hidden from the dashboard immediately.
  - **Tasks Workspace:** The `/tasks` route provides a fuller management surface with Open, Completed, and Archived views. Task cards support titles, body text, deadlines, permanent delete, reversible archive, direct recording links, and the same tag hierarchy used for recordings.
- **Workspace Split:** The recordings library now lives under `/recordings`, separating home-level navigation from recordings filtering state and making later dashboard expansion substantially cleaner.
- **Live Capture Workspace:** Recordings that are still uploading, queued, or processing render a dedicated status view instead of the normal transcript layout.
  - **Waveform Monitoring:** While browser capture is active, the page shows a unified live audio activity waveform sourced from shared audio and microphone analyser taps.
  - **Meeting Edge Guidance:** The live workspace includes a Meeting Edge card that turns the recent transcript into a small set of tactful clarifying questions, overlooked points, and short concept explanations.
  - **Guidance Steering:** Users can provide a separate short Meeting Edge focus prompt during the meeting to steer the live guidance without editing their broader manual notes.
  - **Processing Notes:** Users can capture manual notes while a meeting is recording or processing. The notes panel remains visible until meeting-note generation begins, at which point editing is temporarily locked.
  - **ETA Messaging:** When enough prior processing samples exist for that installation, the UI shows an estimated time remaining. Otherwise it shows a learning message rather than a fabricated estimate.
  - **Shared Visual System:** The dashboard and in-flight meeting workspace share the same ambient layout treatment so the user experience remains coherent across active and idle states.
- **Interactive Tour:** A guided tour for first-time users is implemented using `driver.js`.
  - **Dashboard Tour:** Highlights key features such as navigation, recording, importing, and capture settings.
  - **Transcript Tour:** A detailed walkthrough of the transcript view, triggered when viewing a recording for the first time.
  - **Demo Data:** A "Welcome to Nojoin" demo recording is automatically seeded for new installations to facilitate the transcript tour.
- **Capture Status:** The frontend displays browser support, permission state, missing shared-audio guidance, upload state, paused-recording recovery, and finalization progress across Settings, dashboard capture controls, alerts, and live recording surfaces.

### 2.3 Browser Capture

Live recording is owned by the web client through browser capture APIs.

- **Supported capture browsers:** Chrome, Edge, Brave, Arc, and other Chromium-family browsers on Windows and Linux for shared-audio capture, plus Chrome on Android and iOS for microphone-only capture.
- **Unsupported capture browsers:** Firefox, Safari, other mobile browsers, and Chromium browsers on macOS. These environments can still review, play back, edit, search, and administer recordings.
- **Shared audio source:** `getDisplayMedia` captures tab, window, or screen audio when the user enables the browser's audio-sharing option.
- **Microphone source:** `getUserMedia` captures the local microphone.
- **Mixing:** The browser combines shared audio and microphone audio with Web Audio gain controls and analyser taps on desktop; mobile Chrome records microphone-only audio.
- **Transport:** The browser uploads short WebM/Opus, Ogg/Opus, or MP4 audio segments during live recording. The worker transcodes each segment to 16 kHz, two-channel WAV before live transcription and final concatenation. Channel 0 carries shared/system audio when available and channel 1 carries microphone audio.
- **Lifecycle:** Refreshing, closing, or navigating away from the Nojoin tab moves the recording to `PAUSED`. Uploaded segments remain available, the in-memory tail is dropped, and the user must resume or discard before starting another capture.
- **Settings:** Capture settings cover microphone selection and per-source gain. Settings are browser-local for the initial cutover.
- **Documentation:** [CAPTURE.md](CAPTURE.md) is the canonical browser capture guide.

| Browser / OS | Live capture |
| --- | --- |
| Chrome / Windows | Shared audio + microphone |
| Edge / Windows | Shared audio + microphone |
| Brave / Windows | Shared audio + microphone |
| Arc / Windows | Shared audio + microphone |
| Chromium-family / Linux with PipeWire | Shared audio + microphone |
| Chrome / Android | Microphone-only |
| Chrome / iOS | Microphone-only |
| Firefox / any OS | Unsupported notice |
| Safari / any OS | Unsupported notice |
| Other mobile browsers | Unsupported notice |
| Chromium-family / macOS | Unsupported notice |

### 2.4 Security

- **SSL/TLS:** Browser and backend traffic is encrypted via HTTPS using Nginx as a reverse proxy.
- **Automatic SSL Generation:** The system automatically generates self-signed SSL certificates on startup if they are missing, ensuring immediate secure access for local deployments.
- **HTTPS Enforcement:** HTTP requests to port 14141 are automatically redirected to HTTPS on port 14443. The frontend is only accessible through the Nginx reverse proxy, preventing unencrypted access.
- **Authentication:** JWT-based authentication is used for API access.
- **Browser Sessions:** The Web Client authenticates with Secure HttpOnly cookies issued by the session login flow. These cookies are used for normal browser traffic, including authenticated WebSocket connections.
- **Bearer Tokens:** Explicit Bearer tokens are reserved for non-browser API clients. Browser recording operations use the authenticated session cookie and strict ownership checks.
- **Password Rotation Enforcement:** Users created manually by an Admin or Owner, and users whose password is reset by a superuser, must change their password before they can access other authenticated features. While the flag is set, only the self-profile, self-password update, and logout routes remain available.
- **JWT Signing Keys:** A secure JWT signing keyring is automatically generated on first startup and persisted to `data/.secret_keys.json`. Legacy `data/.secret_key` files are migrated automatically so tokens remain valid across container restarts without requiring manual configuration.
- **Authorization:** Role-based access control (Owner/Admin/User), privilege guardrails around Owner and superuser creation, and strict ownership checks ensure users can only access their own data.
- **Public Auth Throttling:** Login, invitation validation, and invitation-backed registration are rate limited to reduce brute-force and enumeration attacks.
- **Input Validation:** Strict validation and sanitization of all user inputs, including configuration settings, to prevent injection attacks and ensure data integrity.
- **File & Storage Security:** Path traversal protection on all file uploads, temporary directory generation, and backup extraction (Zero-tolerance for Zip Slip vulnerabilities).
- **Model Security:** Safe deserialization of Machine Learning models enforcing PyTorch `weights_only=True` with explicitly whitelisted global unpicklers.
- **Browser Capture Permissions:** Screen, tab, system-audio, and microphone capture are gated by browser permission prompts. Nojoin cannot silently capture those sources without the user selecting a source in the browser picker.
- **Trusted Public Origin:** Invitation links, OAuth callbacks, browser CORS, and unsafe cookie-authenticated requests use the configured public web origin rather than trusting request Host headers.
- **CORS & Remote Access:**
  - **CORS:** Restricted to local development origins plus the configured `WEB_APP_URL` public origin.
  - **Remote Access:** Supports deployment behind reverse proxies (e.g., Cloudflare Tunnels, Caddy) by configuring `NEXT_PUBLIC_API_URL` and `WEB_APP_URL`.

### 2.5 User Management & Invitations

- **Role-Based Access Control (RBAC):**
  - **Owner:** Full system access, including server settings and user management.
  - **Admin:** Can manage users and create invitations, but cannot modify critical server settings.
  - **User:** Standard access to personal recordings and settings.
- **Invitation System:**
  - **Invite Links:** Admins can generate unique, time-limited invite links.
  - **Management:** Dedicated UI for tracking and revoking active invitations.
  - **Registration:** Public registration page gated by valid invite codes.
- **Manual User Provisioning:** Users created directly by an Admin or Owner receive a temporary password and are required to choose a new one on first sign-in.
- **Privilege Guardrails:** Only Owners can create Owner-role accounts, and only existing superusers can grant superuser privileges or force a password reset on an existing user.

### 2.6 Accessibility & Design System

- **Standards:** Adheres to WCAG 2.1 AA standards for contrast and accessibility.
- **Color Palette:**
  - **Primary Action:** Orange-600 (`#ea580c`) for buttons and active states to ensure sufficient contrast against white backgrounds.
  - **Hover States:** Orange-700 (`#c2410c`) for interactive feedback.
  - **Borders:** Gray-300 (`#d1d5db`) for light mode and Gray-600 (`#4b5563`) for dark mode to ensure visibility of UI boundaries.
  - **Backgrounds:** Gray-100 (`#f3f4f6`) for secondary backgrounds in light mode to differentiate from white containers.
- **Theme:** Fully responsive Light and Dark modes with semantic color mapping.

### 2.7 Backup System

- **Strategy:** Zip-based export containing:
  - **Database:** JSON dumps of all tables.
  - **Dashboard State:** Task List and Tasks workspace items (`user_tasks` plus shared task tag and recording links), people records, voiceprint embeddings, and the persisted calendar sync tables used by the dashboard calendar and agenda views.
  - **Calendar Integration:** Installation calendar provider configuration plus connected-account tokens, selected calendars, colour overrides, sync cursors, and cached events.
  - **Audio:** Original audio files compressed to Opus format to save space.
  - **Config:** System configuration with sensitive application keys still redacted.
- **Security:**
  - **Redaction:** LLM, Hugging Face, and similar application API keys remain redacted from the backup, and password material is never restorable.
  - **Sensitive Archive:** Calendar provider credentials and connected-calendar OAuth tokens are intentionally preserved so calendar integrations can be restored on a new installation. Backup archives must therefore be handled as secret material.
  - **Ownership:** Backups include user mapping to ensure correct ownership upon restoration.
- **Flexibility:**
  - **Smart Deduplication:** Restored recordings are matched by durable identifiers (`meeting_uid`, `public_id`) with a legacy fallback to the recording audio path to prevent duplicate restores.
  - **Additive Restore:** Can merge data into an existing installation.
  - **Conflict Resolution:** Options to skip (safe merge) or overwrite conflicting data.

---

## 3. Core Features

> **Note:** For detailed usage instructions and feature descriptions, please refer to [USAGE.md](USAGE.md).

The system provides the following core capabilities:

- **Audio Recording:** Browser-native live capture for shared tab/window/screen audio plus microphone input on supported desktop Chromium browsers, and microphone-only recording on Chrome mobile.
  - **Live Waveform Visibility:** While a meeting is actively recording, the Web Client shows live audio activity derived from the browser analyser taps.
  - **Pause and Resume:** Paused recordings retain uploaded segments and block new capture until resumed or discarded.
- **Import:** Support for importing existing audio files.
  - **No Upload Limits:** Large files are automatically split into 10MB chunks during upload to bypass proxy limits and ensure reliability. There are no artificial file size caps.
- **Transcription & Diarization:** Async processing using Whisper (Transcription) and Pyannote (Diarization).
  - **Transcription Engine Choices:** Whisper remains the broad-coverage default. Parakeet provides much faster transcription on supported NVIDIA systems, with a tradeoff of slightly lower accuracy and fewer supported languages. Normal live and final transcription use the same selected engine so live transcript work can be reused; different-engine transcription is handled through manual reprocessing.
  - **Live Pipeline Coverage:** Browser-live ASR coverage and rolling speaker-window diarization coverage are tracked independently, so live transcript availability does not imply every speaker-window pass has completed.
  - **Live/Final Reuse:** Final processing may reuse live text and speaker decisions only after stable-id or clear overlap alignment. Manual transcript and speaker edits remain authoritative.
  - **Phantom Speaker Filter:** Post-diarization stage that detects and reassigns segments caused by non-speech audio (notification sounds, background noise) to prevent phantom "UNKNOWN" speaker assignments. Uses heuristic detection validated by embedding similarity analysis.
- **Processing Telemetry & ETA:** New processing runs persist `processing_started_at` and `processing_completed_at` on the recording and use those samples to estimate remaining processing time for future meetings.
  - **Learning Threshold:** ETA is only shown once at least three prior processed meetings with timing data exist.
  - **Scope of History:** ETA calculations use only recordings that explicitly captured processing timings. Older recordings are excluded unless they are reprocessed.
- **Speaker Management:** Global speaker library with high-accuracy voiceprint identification (utilizing multi-segment averaging, margin-of-victory thresholds, and embedding drift guards).
  - **Outlier Filtering:** Segment embeddings are statistically filtered before averaging to remove mis-diarised segments that would corrupt the voiceprint.
  - **Confidence-Gated Updates:** Auto-updates to global voiceprints only occur when match confidence exceeds the auto-update threshold, preventing gradual embedding degradation from borderline matches.
  - **Drift Guard:** Embedding merges are rejected when the incoming embedding is too dissimilar to the current voiceprint, protecting against false-positive pollution.
  - **Recalibrate Voiceprint:** Manual flow to select "Gold Standard" audio samples to redefine a speaker's voiceprint.
  - **Voiceprint Locking:** Prevent automated updates to manually verified voiceprints.
- **Meeting Intelligence:** Provider-gated automatic AI enhancement via one LLM call that can return unresolved speaker suggestions, a meeting title, and Markdown meeting notes, plus separate Chat Q&A.
  - **Manual AI Actions:** `Generate Notes` remains a notes-only manual action, and `Retry Speaker Inference` remains a speaker-only manual action.
  - **Meeting Edge Live Guidance:** A separate low-latency Meeting Edge flow uses recent live transcript context to generate real-time questions, missed points, and concept help during in-flight meetings.
  - **Separate Live Model Slot:** Each provider can optionally configure a dedicated Meeting Edge model; when unset, Meeting Edge falls back to that provider's main model.
  - **User-Authored Notes:** Users can write manual notes during recording or processing. These notes are injected into speaker-name inference and meeting-note generation as supporting context.
  - **Final Note Attribution:** Final notes append a deterministic `User Notes` section in which each manual note is labelled as user-authored.
- **Search Capabilities:**
  - **Global Search:** Backend-driven SQL pattern matching for finding recordings by title or content.
  - **Transcript Search:** Client-side fuzzy search for locating specific text within a transcript.
  - **Organization:** Hierarchical tagging system with expand/collapse functionality and custom creation modal.
- **Web Playback:** Modern HTML5 player with synced transcript and edit mode.
  - **Context Menus:** Right-click context menus on recording lists provide quick access to actions like Rename, Retry Processing, Show Recording Info, Archive, and Delete across both the main recording cards and the sidebar list view.
- **Dashboard Workspace:** The dashboard now combines `Meet Now`, an interactive calendar shell with recorded meeting history, and personal task capture into a single operational home surface.
  - **Calendar Modes:** Month navigation, a `Today` reset action, and an Agenda toggle are live. Without a connected calendar source, both modes remain empty-state views.
  - **Calendar Colour Mapping:** Connected calendar sources can be given distinct colours in Personal settings. The dashboard month dots and agenda cards reuse those per-calendar colours instead of collapsing every event into a single accent colour.
  - **Task Flow:** Tasks are created inline in the dashboard Task List. Users can rename an existing task by double-clicking its title, then save with `Enter` or an outside click, or cancel with `Escape`. Optional deadlines store both date and time, while active tasks show a live time-remaining badge that prefers days first and then rounded-down whole hours once the remaining time drops below one day. Users can archive dashboard tasks to hide them without deletion.
  - **Tasks Page:** The main navigation exposes **Tasks** under **Dashboard** and above **Recordings**. This page supports richer task cards with title, body, shared tags, linked recordings, deadlines, completion state, reversible archive, and permanent delete.
  - **Planned Expansion:** Future iterations are expected to connect external calendar data and derive richer agenda/task automation from meeting outcomes and action items.
- **Settings:** Comprehensive server and user configuration.
- **Updates & Releases:** Built-in Settings page for installed version visibility from the current API build, release history, and release notes sourced from GitHub Releases.
- **Backup & Restore:** Full system backup capabilities including database records, Task List and Tasks page items, people voiceprints, calendar integrations, and compressed audio, with selective restoration and targeted redaction for non-restorable application keys.

### 3.1 Processing Lifecycle Details

- **Status Persistence:** When a recording enters the worker pipeline, Nojoin stores a processing start timestamp. On successful completion, including "no speech detected" outcomes, it stores a processing completion timestamp.
- **Retry / Cancel Semantics:** Retrying processing clears prior generated artefacts, resets timing fields, and records a fresh timing sample for the new run. Cancelling or erroring a run clears the completion timestamp so incomplete runs do not pollute ETA history.
- **User Note Preservation:** Retry Processing preserves user-authored notes so operators do not lose manually captured context while rebuilding transcript-derived artefacts.

---

## 4. Technical Requirements

### 4.1 Server Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI
- **Task Queue:** Celery
- **Database:** PostgreSQL 18 (accessed via SQLModel/SQLAlchemy)
- **Migrations:** Alembic
- **Broker:** Redis
- **Container Runtime:** Docker (Linux with NVIDIA Container Toolkit recommended)
  - _Constraint:_ macOS hosting is CPU-only due to Docker limitations.

### 4.2 Web Client Stack

- **Framework:** Next.js (React)
- **Language:** TypeScript
- **Styling:** Tailwind CSS v4
- **State Management:** Zustand
- **Build:** Production optimized (`next build`) with multi-stage Docker build.

### 4.3 Browser Capture Stack

- **Browser APIs:** `getDisplayMedia`, `getUserMedia`, Web Audio, and MediaRecorder. Mobile Chrome uses `getUserMedia` and MediaRecorder without `getDisplayMedia`.
- **Frontend modules:** Capture logic lives under `frontend/src/lib/capture/`.
- **Media transport:** WebM/Opus, Ogg/Opus, or MP4 audio during live recording, transcoded by the worker to 16 kHz, two-channel WAV segments with shared/system audio on channel 0 when available and microphone audio on channel 1.
- **Supported capture platforms:** Chromium-family browsers on Windows and Linux for shared-audio capture; Chrome on Android and iOS for microphone-only capture.
- **Unsupported capture platforms:** Firefox, Safari, other mobile browsers, and Chromium browsers on macOS.
- **Validation:** Manual browser matrix testing is required before release sign-off.

### 4.4 Deployment & Configuration

> **Note:** For detailed deployment and configuration instructions, please refer to [DEPLOYMENT.md](DEPLOYMENT.md).

### 4.5 Containerization Standards

- **Base Images:** Utilize optimized, pre-built images (e.g., `pytorch/pytorch` with CUDA runtime) to minimize build time and image size. Avoid building heavy dependencies (like CUDA/PyTorch) from scratch.
- **Context Management:** Maintain a strict `.dockerignore` file to exclude build artifacts (Rust `target/`, Node `node_modules/`), version control history (`.git`), and local environment files from the build context.
- **Dependency Optimization:** Filter `requirements.txt` during the build process to prevent redundant installation of packages already present in the base image (e.g., `torch`, `torchaudio`).
- **Layer Efficiency:** Combine `RUN` instructions where possible and clean up package manager caches (`apt-get clean`, `rm -rf /var/lib/apt/lists/*`) in the same layer to reduce image size.

### 4.6 Development Standards

- **Type Safety:** All frontend code must pass strict TypeScript validation (`npm run type-check` or `npm run build`) before merging.
- **Interface Consistency:** Data models must be synchronized between Backend (Pydantic/SQLModel) and Frontend (TypeScript Interfaces).
- **Build Verification:** Features are not considered complete until the application builds successfully in the Docker production environment.
