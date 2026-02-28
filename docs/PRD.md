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
- **Functionality:** Dashboard, playback, transcript editing, speaker management, and system configuration.
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
- **UI:** Minimalist system tray menu for status indication, updates, help, and exit. Managed via Tauri.
- **Local Server:** Runs on `localhost:12345`. Remote access requires configuration via a user-managed reverse proxy.
- **Distribution:** The Windows installer (NSIS) is built via the unified CI/CD pipeline (`release.yml`) and hosted on GitHub Releases alongside the server Docker images, ensuring strict version parity.
- **Auto-Update:** The app uses the built-in Tauri updater to check for new versions on GitHub matched to the server version.

### 2.4 Security

- **SSL/TLS:** All communication between components (Frontend, Backend, Companion) is encrypted via HTTPS using Nginx as a reverse proxy.
- **Automatic SSL Generation:** The system automatically generates self-signed SSL certificates on startup if they are missing, ensuring immediate secure access for local deployments.
- **HTTPS Enforcement:** HTTP requests to port 14141 are automatically redirected to HTTPS on port 14443. The frontend is only accessible through the Nginx reverse proxy, preventing unencrypted access.
- **Authentication:** JWT-based authentication is used for API access.
- **JWT Secret Key:** A secure SECRET_KEY for signing JWT tokens is automatically generated on first startup and persisted to `data/.secret_key`. This ensures tokens remain valid across container restarts. Advanced deployments can override this by setting the `SECRET_KEY` environment variable.
- **Authorization:** Role-based access control (Owner/Admin/User) and strict ownership checks ensure users can only access their own data.
- **Input Validation:** Strict validation and sanitization of all user inputs, including configuration settings, to prevent injection attacks and ensure data integrity.
- **File & Storage Security:** Path traversal protection on all file uploads, temporary directory generation, and backup extraction (Zero-tolerance for Zip Slip vulnerabilities).
- **Model Security:** Safe deserialization of Machine Learning models enforcing PyTorch `weights_only=True` with explicitly whitelisted global unpicklers.
- **Companion IPC Security:** Strict Origin validation to prevent Cross-Site Request Forgery (CSRF) and unauthorized local scripts from interfacing with the local Companion server.
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
  - **Audio:** Original audio files compressed to Opus format to save space.
  - **Config:** System configuration (redacted).
- **Security:**
  - **Redaction:** Sensitive data (API keys, passwords, authentication tokens) is automatically redacted from the backup.
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
- **Import:** Support for importing existing audio files.
  - **No Upload Limits:** Large files are automatically split into 10MB chunks during upload to bypass proxy limits and ensure reliability. There are no artificial file size caps.
- **Transcription & Diarization:** Async processing using Whisper (Transcription) and Pyannote (Diarization).
- **Speaker Management:** Global speaker library with high-accuracy voiceprint identification (utilizing multi-segment averaging and margin-of-victory thresholds).
  - **Recalibrate Voiceprint:** Manual flow to select "Gold Standard" audio samples to redefine a speaker's voiceprint.
  - **Voiceprint Locking:** Prevent automated updates to manually verified voiceprints.
- **Meeting Intelligence:** LLM-powered notes (Summaries, Action Items), Chat Q&A, and automatic meeting title inference.
- **Search Capabilities:**
  - **Global Search:** Backend-driven SQL pattern matching for finding recordings by title or content.
  - **Transcript Search:** Client-side fuzzy search for locating specific text within a transcript.
  - **Organization:** Hierarchical tagging system with expand/collapse functionality and custom creation modal.
- **Web Playback:** Modern HTML5 player with synced transcript and edit mode.
  - **Context Menus:** Right-click context menus on recording lists provide quick access to actions like Rename, Retry Processing, Show Recording Info, Archive, and Delete. This is handled by the Sidebar.tsx file.
- **Settings:** Comprehensive server and user configuration.
- **Backup & Restore:** Full system backup capabilities including database records and audio files (compressed), with selective restoration and data redaction for security.

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
