# Nojoin - Product Requirements Document (PRD)

## 1. Introduction

**Application Name:** Nojoin
**Purpose:** Nojoin is a distributed, containerized meeting intelligence platform. The system enables users to record system audio from client devices, process the data centrally on a GPU-enabled server, and access transcripts, diarization, and AI-generated insights via a web interface.

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
  - **Auto-Fill:** Settings can be pre-configured via environment variables (defined in `.env`) to simplify the setup process for valid keys.
- **Database Initialization:** Automatically handles schema creation and migrations on startup.
- **Storage:** Docker Volumes provide persistent storage for raw audio and model artifacts.
- **Reverse Proxy:** Nginx handles SSL termination and routing (Port 14443).

### 2.2 The Web Client (Next.js)

The primary user interface for interacting with the system.

- **Framework:** Next.js (React) with TypeScript.
- **Styling:** Tailwind CSS for a fully responsive design, including mobile-optimized views.
- **Functionality:** Dashboard, playback, transcript editing, speaker management, system configuration, and a dedicated live capture / processing workspace for in-flight meetings.
- **Dashboard Iteration Two:** The root route now serves as an operational dashboard with `Meet Now` capture controls, recent-meeting access, an interactive calendar shell, an inline personal Task List, and clear routing into the recordings workspace.
  - **Calendar Shell:** Users can browse prior and future months, jump back to today with a dedicated `Today` action, and switch between Month and Agenda modes. When calendar integrations are connected, month dots and agenda markers use per-calendar colours so different sources remain visually distinct. Until a real calendar integration is connected, the dashboard shows honest empty states rather than mock events.
  - **Task List:** Personal tasks are grouped into a Task List on the dashboard.
  - **Task Capture:** The Task List is grouped directly beneath `Meet Now` on larger screens and supports inline entry with `Enter` to save, `Escape` to cancel, double-click title editing on tasks, optional date-time deadlines, and a live time-remaining badge beside each active deadline control.
- **Workspace Split:** The recordings library now lives under `/recordings`, separating home-level navigation from recordings filtering state and making later dashboard expansion substantially cleaner.
- **Live Capture Workspace:** Recordings that are still uploading, queued, or processing render a dedicated status view instead of the normal transcript layout.
  - **Waveform Monitoring:** While the Companion is recording, the page shows live system-audio and microphone level bars sourced from the local Companion service.
  - **Processing Notes:** Users can capture manual notes while a meeting is recording or processing. The notes panel remains visible until meeting-note generation begins, at which point editing is temporarily locked.
  - **ETA Messaging:** When enough prior processing samples exist for that installation, the UI shows an estimated time remaining. Otherwise it shows a learning message rather than a fabricated estimate.
  - **Shared Visual System:** The dashboard and in-flight meeting workspace share the same ambient layout treatment so the user experience remains coherent across active and idle states.
- **Interactive Tour:** A guided tour for first-time users is implemented using `driver.js`.
  - **Dashboard Tour:** Highlights key features such as navigation, recording, importing, and companion app setup.
  - **Transcript Tour:** A detailed walkthrough of the transcript view, triggered when viewing a recording for the first time.
  - **Demo Data:** A "Welcome to Nojoin" demo recording is automatically seeded for new installations to facilitate the transcript tour.
- **Companion Status:** A visual indicator (warning bubble) is displayed when the Companion App is not detected.
- **Download Companion Button:** An orange "Download Companion" button appears in the navigation when the Companion App is unreachable. It links to the Windows installer from GitHub Releases.

### 2.3 The Companion App (Tauri + Rust)

A lightweight system tray application responsible for audio capture on Windows.

- **Directory:** `companion/`
- **Framework:** Tauri v2.
- **Language:** Rust (Backend) + HTML/JS (Frontend).
- **Platforms:** Windows (macOS and Linux support is not currently available).
- **Role:** Acts as a local server. Captures system audio (loopback) and microphone input upon receiving commands from the Web Client.
- **Live Metering Endpoint:** Exposes a non-destructive `GET /levels/live` endpoint for the Web Client so the recording page can poll live audio levels without consuming the destructive peak counters used elsewhere.
- **Pairing Model:** The Web Client authorises the Companion using a dedicated bootstrap token for pairing and recording initialisation. The browser session itself remains in a Secure HttpOnly cookie and is never re-used directly by the desktop app.
- **Per-Recording Upload Tokens:** Each recording initialisation returns a short-lived upload token bound to that recording ID. Segment upload, client-status updates, finalisation, and discard all use that narrower token.
- **UI:** Minimalist system tray menu for status indication, updates, help, and exit. Managed via Tauri.
- **Local Server:** Runs on `localhost:12345`. Remote access requires configuration via a user-managed reverse proxy.
- **Distribution:** The Windows installer (NSIS) is built via the unified CI/CD pipeline (`release.yml`) and hosted on GitHub Releases alongside the server Docker images, ensuring strict version parity.
- **Auto-Update:** The app uses the built-in Tauri updater to check for new versions on GitHub matched to the server version.

### 2.4 Security

- **SSL/TLS:** All communication between components (Frontend, Backend, Companion) is encrypted via HTTPS using Nginx as a reverse proxy.
- **Automatic SSL Generation:** The system automatically generates self-signed SSL certificates on startup if they are missing, ensuring immediate secure access for local deployments.
- **HTTPS Enforcement:** HTTP requests to port 14141 are automatically redirected to HTTPS on port 14443. The frontend is only accessible through the Nginx reverse proxy, preventing unencrypted access.
- **Authentication:** JWT-based authentication is used for API access.
- **Browser Sessions:** The Web Client authenticates with Secure HttpOnly cookies issued by the session login flow. These cookies are used for normal browser traffic, including authenticated WebSocket connections.
- **Bearer Tokens:** Explicit Bearer tokens are reserved for non-browser API clients. Companion pairing receives a bootstrap token, and each recording initialisation returns a short-lived recording token bound to that recording ID for upload, status, finalisation, and discard operations.
- **Password Rotation Enforcement:** Users created manually by an Admin or Owner, and users whose password is reset by a superuser, must change their password before they can access other authenticated features. While the flag is set, only the self-profile, self-password update, and logout routes remain available.
- **JWT Secret Key:** A secure JWT signing key is automatically generated on first startup and persisted to `data/.secret_key`. This ensures tokens remain valid across container restarts without requiring manual configuration.
- **Authorization:** Role-based access control (Owner/Admin/User), privilege guardrails around Owner and superuser creation, and strict ownership checks ensure users can only access their own data.
- **Public Auth Throttling:** Login, invitation validation, and invitation-backed registration are rate limited to reduce brute-force and enumeration attacks.
- **Input Validation:** Strict validation and sanitization of all user inputs, including configuration settings, to prevent injection attacks and ensure data integrity.
- **File & Storage Security:** Path traversal protection on all file uploads, temporary directory generation, and backup extraction (Zero-tolerance for Zip Slip vulnerabilities).
- **Model Security:** Safe deserialization of Machine Learning models enforcing PyTorch `weights_only=True` with explicitly whitelisted global unpicklers.
- **Companion IPC Security:** Strict Origin validation to prevent Cross-Site Request Forgery (CSRF) and unauthorized local scripts from interfacing with the local Companion server.
- **Trusted Public Origin:** Invitation links and TLS fingerprint resolution use the configured public web origin and allowed-origin fallback, rather than trusting request Host headers.
- **CORS & Remote Access:**
  - **CORS:** Restricted to allowed origins. Configurable via the `ALLOWED_ORIGINS` environment variable to support LAN and remote access.
  - **Remote Access:** Supports deployment behind reverse proxies (e.g., Cloudflare Tunnels, Caddy) by configuring `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS`.

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
  - **Dashboard State:** Task List items (`user_tasks`), people records, voiceprint embeddings, and the persisted calendar sync tables used by the dashboard calendar and agenda views.
  - **Calendar Integration:** Installation calendar provider configuration plus connected-account tokens, selected calendars, colour overrides, sync cursors, and cached events.
  - **Audio:** Original audio files compressed to Opus format to save space.
  - **Config:** System configuration with sensitive application keys still redacted.
- **Security:**
  - **Redaction:** LLM, Hugging Face, and similar application API keys remain redacted from the backup, and password material is never restorable.
  - **Sensitive Archive:** Calendar provider credentials and connected-calendar OAuth tokens are intentionally preserved so calendar integrations can be restored on a new installation. Backup archives must therefore be handled as secret material.
  - **Ownership:** Backups include user mapping to ensure correct ownership upon restoration.
- **Flexibility:**
  - **Smart Deduplication:** Audio files are identified by hash to prevent duplication.
  - **Additive Restore:** Can merge data into an existing installation.
  - **Conflict Resolution:** Options to skip, overwrite, or create copies of conflicting data.

---

## 3. Core Features

> **Note:** For detailed usage instructions and feature descriptions, please refer to [USAGE.md](USAGE.md).

The system provides the following core capabilities:

- **Audio Recording:** Headless system tray app for dual-channel capture (System + Mic).
  - **Live Waveform Visibility:** While a meeting is actively recording, the Web Client shows calibrated live level bars for both channels.
- **Import:** Support for importing existing audio files.
  - **No Upload Limits:** Large files are automatically split into 10MB chunks during upload to bypass proxy limits and ensure reliability. There are no artificial file size caps.
- **Transcription & Diarization:** Async processing using Whisper (Transcription) and Pyannote (Diarization).
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
- **Meeting Intelligence:** LLM-powered notes (Summaries, Action Items), Chat Q&A, and automatic meeting title inference.
  - **User-Authored Notes:** Users can write manual notes during recording or processing. These notes are injected into speaker-name inference and meeting-note generation as supporting context.
  - **Final Note Attribution:** Final notes append a deterministic `User Notes` section in which each manual note is labelled as user-authored.
- **Search Capabilities:**
  - **Global Search:** Backend-driven SQL pattern matching for finding recordings by title or content.
  - **Transcript Search:** Client-side fuzzy search for locating specific text within a transcript.
  - **Organization:** Hierarchical tagging system with expand/collapse functionality and custom creation modal.
- **Web Playback:** Modern HTML5 player with synced transcript and edit mode.
  - **Context Menus:** Right-click context menus on recording lists provide quick access to actions like Rename, Retry Processing, Show Recording Info, Archive, and Delete. This is handled by the Sidebar.tsx file.
- **Dashboard Workspace:** The dashboard now combines `Meet Now`, recent meetings, an interactive calendar shell, and personal task capture into a single operational home surface.
  - **Calendar Modes:** Month navigation, a `Today` reset action, and an Agenda toggle are live. Without a connected calendar source, both modes remain empty-state views.
  - **Calendar Colour Mapping:** Connected calendar sources can be given distinct colours in Account settings. The dashboard month dots and agenda cards reuse those per-calendar colours instead of collapsing every event into a single accent colour.
  - **Task Flow:** Tasks are created inline in the dashboard Task List. Users can rename an existing task by double-clicking its title, then save with `Enter` or an outside click, or cancel with `Escape`. Optional deadlines store both date and time, while active tasks show a live time-remaining badge that prefers days first and then rounded-down whole hours once the remaining time drops below one day.
  - **Planned Expansion:** Future iterations are expected to connect external calendar data and derive richer agenda/task automation from meeting outcomes and action items.
- **Settings:** Comprehensive server and user configuration.
- **Updates & Releases:** Built-in Settings page for installed version visibility from the current API build, release history, release notes, and companion installer links sourced from GitHub Releases.
- **Backup & Restore:** Full system backup capabilities including database records, Task List items, people voiceprints, calendar integrations, and compressed audio, with selective restoration and targeted redaction for non-restorable application keys.

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

### 4.3 Companion App Stack

- **Framework:** Tauri v2
- **Language:** Rust (Core Logic)
- **Audio:** cpal (Windows)
- **Async Runtime:** Tokio
- **HTTP Client:** Reqwest
- **GUI/Tray:** Tauri System Tray
- **Platforms:** Windows only (macOS and Linux support pending contributions)
- **Installer:** Tauri Bundler (NSIS for Windows) with:
  - Installation to `%LOCALAPPDATA%\Nojoin`
  - Start Menu and Desktop shortcuts
  - Run on Startup option (via `tauri-plugin-autostart`)
  - Automatic termination of running instances during update
  - Config file preservation during updates
- **Signing:**
  - Updates are signed using the built-in Tauri mechanism (Minisign).
  - Private keys are stored in GitHub Secrets (`TAURI_PRIVATE_KEY`, `TAURI_KEY_PASSWORD`) for CI/CD.
  - Local builds require these keys to be present in environment variables.

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
