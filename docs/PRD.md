# Nojoin - Product Requirements Document (PRD)

## 1. Introduction

**Application Name:** Nojoin
**Purpose:** Nojoin is a distributed, containerized meeting intelligence platform. It enables users to record system audio from any client device, process it centrally on a powerful GPU-enabled server, and access transcripts, diarization, and AI-generated insights via a modern web interface.

**Core Philosophy:**
*   **Centralized Intelligence:** Heavy lifting (Whisper/Pyannote) happens on a dedicated server.
*   **Ubiquitous Access:** Manage and view meetings from any device with a browser.
*   **Privacy First:** Self-hosted architecture ensures audio and transcripts never leave the user's control unless explicitly configured for external LLM services.

---

## 2. System Architecture

The application is composed of three distinct subsystems:

### 2.1 The Server (Dockerized)
The core processing unit hosted on a machine with NVIDIA GPU capabilities.
*   **Registry:** Images are hosted on GitHub Container Registry (GHCR).
*   **API Service:** FastAPI-based REST API for data management and client communication. Runs in a lightweight container (Python Slim) without heavy ML dependencies.
*   **Worker Service:** Celery-based background worker handling resource-intensive tasks (transcription, diarization) using the NVIDIA Runtime. Runs in a heavy container with PyTorch and CUDA support.
*   **Database:** PostgreSQL serving as the single source of truth for all metadata, transcripts, and speaker profiles.
*   **Broker:** Redis for task queue management and caching.
*   **Storage:** Docker Volumes for persistent storage of raw audio and model artifacts.
*   **Reverse Proxy:** Nginx handling SSL termination and routing (Port 14443).

### 2.2 The Web Client (Next.js)
The primary user interface for interacting with the system.
*   **Framework:** Next.js (React) with TypeScript.
*   **Styling:** Tailwind CSS for a responsive, modern design.
*   **Functionality:** Dashboard, playback, transcript editing, speaker management, and system configuration.
*   **Interactive Tour:** A guided tour for first-time users using `driver.js`.
    *   **Dashboard Tour:** Highlights key features like navigation, recording, importing, and companion app setup.
    *   **Transcript Tour:** A detailed walkthrough of the transcript view, triggered when viewing a recording for the first time.
    *   **Demo Data:** A "Welcome to Nojoin" demo recording is automatically seeded for new installations to facilitate the transcript tour.
*   **Companion Status:** Visual indicator (warning bubble) when the Companion App is not detected.
*   **Download Companion Button:** An orange "Download Companion" button appears in the navigation when the Companion App is unreachable. Dynamically links to the correct installer for the user's OS (Windows/macOS/Linux) from GitHub Releases.

### 2.3 The Companion App (Tauri + Rust)
A lightweight, cross-platform system tray application responsible for audio capture.
*   **Directory:** `companion/`
*   **Framework:** Tauri v1.5.
*   **Language:** Rust (Backend) + HTML/JS (Frontend - currently minimal).
*   **Platforms:** Windows, macOS, Linux (First-class support for all).
*   **Role:** Acts as a local server. Captures system audio (loopback) and microphone input upon receiving commands from the Web Client.
*   **UI:** Minimalist system tray menu for status indication, updates, help, and exit. Managed via Tauri.
*   **Local Server:** Always runs on `localhost:12345`. Remote access must be configured via a user-managed reverse proxy.
*   **Distribution:** Installer binaries (MSI, EXE, DMG, DEB) are built via Tauri Bundler and hosted on GitHub Releases.
*   **Auto-Update:** The app uses Tauri's built-in updater to check for new versions on GitHub.

### 2.4 Security
*   **SSL/TLS:** All communication between components (Frontend, Backend, Companion) is encrypted via HTTPS using Nginx as a reverse proxy.
*   **Automatic SSL Generation:** The system automatically generates self-signed SSL certificates on startup if they are missing, ensuring immediate secure access for local deployments.
*   **HTTPS Enforcement:** HTTP requests to port 14141 are automatically redirected to HTTPS on port 14443. The frontend is only accessible through the Nginx reverse proxy, preventing unencrypted access.
*   **Authentication:** JWT-based authentication for API access.
*   **JWT Secret Key:** A secure SECRET_KEY for signing JWT tokens is automatically generated on first startup and persisted to `data/.secret_key`. This ensures tokens remain valid across container restarts. Advanced deployments can override this by setting the `SECRET_KEY` environment variable.
*   **Authorization:** Role-based access control (Owner/Admin/User) and strict ownership checks ensure users can only access their own data.
*   **CORS & Remote Access:**
    *   **CORS:** Restricted to allowed origins. Configurable via `ALLOWED_ORIGINS` environment variable to support LAN and remote access.
    *   **Remote Access:** Supports deployment behind reverse proxies (e.g., Cloudflare Tunnels, Caddy) by configuring `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS`.

### 2.5 User Management & Invitations
*   **Role-Based Access Control (RBAC):**
    *   **Owner:** Full system access, including server settings and user management.
    *   **Admin:** Can manage users and create invitations, but cannot modify critical server settings.
    *   **User:** Standard access to personal recordings and settings.
*   **Invitation System:**
    *   **Invite Links:** Admins can generate unique, time-limited invite links.
    *   **Management:** Dedicated UI for tracking and revoking active invitations.
    *   **Registration:** Public registration page gated by valid invite codes.

### 2.6 Accessibility & Design System
*   **Standards:** Adheres to WCAG 2.1 AA standards for contrast and accessibility.
*   **Color Palette:**
    *   **Primary Action:** Orange-600 (`#ea580c`) for buttons and active states to ensure sufficient contrast against white backgrounds.
    *   **Hover States:** Orange-700 (`#c2410c`) for interactive feedback.
    *   **Borders:** Gray-300 (`#d1d5db`) for light mode and Gray-600 (`#4b5563`) for dark mode to ensure visibility of UI boundaries.
    *   **Backgrounds:** Gray-100 (`#f3f4f6`) for secondary backgrounds in light mode to differentiate from white containers.
*   **Theme:** Fully responsive Light and Dark modes with semantic color mapping.

---

## 3. Core Features

> **Note:** For detailed usage instructions and feature descriptions, please refer to [USAGE.md](USAGE.md).

The system provides the following core capabilities:
*   **Audio Recording:** Headless system tray app for dual-channel capture (System + Mic).
*   **Import:** Support for importing existing audio files.
    *   **Chunked Uploads:** Large files are automatically split into 10MB chunks during upload to bypass proxy limits (e.g., Cloudflare Tunnel 100MB limit) and ensure reliability.
*   **Transcription & Diarization:** Async processing using Whisper (Transcription) and Pyannote (Diarization).
*   **Speaker Management:** Global speaker library with voiceprint identification.
*   **Meeting Intelligence:** LLM-powered notes (Summaries, Action Items), Chat Q&A, and automatic meeting title inference.
*   **Search & Organization:** Tagging, full-text search, and fuzzy search.
*   **Web Playback:** Modern HTML5 player with synced transcript and edit mode.
*   **Settings:** Comprehensive server and user configuration.

---

## 4. Technical Requirements

### 4.1 Server Stack
*   **Language:** Python 3.11+
*   **Framework:** FastAPI
*   **Task Queue:** Celery
*   **Database:** PostgreSQL 16 (accessed via SQLModel/SQLAlchemy)
*   **Migrations:** Alembic
*   **Broker:** Redis
*   **Container Runtime:** Docker (Linux with NVIDIA Container Toolkit recommended)
    *   *Constraint:* macOS hosting is CPU-only due to Docker limitations.

### 4.2 Web Client Stack
*   **Framework:** Next.js (React)
*   **Language:** TypeScript
*   **Styling:** Tailwind CSS v4
*   **State Management:** React Query / Zustand
*   **Build:** Production optimized (`next build`) with multi-stage Docker build.

### 4.3 Companion App Stack
*   **Framework:** Tauri v1.5
*   **Language:** Rust (Core Logic)
*   **Audio:** cpal (Windows/Linux), ScreenCaptureKit (macOS)
*   **Async Runtime:** Tokio
*   **HTTP Client:** Reqwest
*   **GUI/Tray:** Tauri System Tray
*   **Installer:** Tauri Bundler (NSIS for Windows, DMG for macOS, DEB/AppImage for Linux) with:
    *   Installation to `%LOCALAPPDATA%\Nojoin` (Windows)
    *   Start Menu and Desktop shortcuts
    *   Run on Startup option (via `tauri-plugin-autostart`)
    *   Automatic termination of running instances during update
    *   Config file preservation during updates
*   **Signing:**
    *   Updates are signed using Tauri's built-in mechanism (Minisign).
    *   Private keys are stored in GitHub Secrets (`TAURI_PRIVATE_KEY`, `TAURI_KEY_PASSWORD`) for CI/CD.
    *   Local builds require these keys to be present in environment variables.

### 4.4 Deployment & Configuration

> **Note:** For detailed deployment and configuration instructions, please refer to [DEPLOYMENT.md](DEPLOYMENT.md).

### 4.5 Containerization Standards
*   **Base Images:** Utilize optimized, pre-built images (e.g., `pytorch/pytorch` with CUDA runtime) to minimize build time and image size. Avoid building heavy dependencies (like CUDA/PyTorch) from scratch.
*   **Context Management:** Maintain a strict `.dockerignore` file to exclude build artifacts (Rust `target/`, Node `node_modules/`), version control history (`.git`), and local environment files from the build context.
*   **Dependency Optimization:** Filter `requirements.txt` during the build process to prevent redundant installation of packages already present in the base image (e.g., `torch`, `torchaudio`).
*   **Layer Efficiency:** Combine `RUN` instructions where possible and clean up package manager caches (`apt-get clean`, `rm -rf /var/lib/apt/lists/*`) in the same layer to reduce image size.

### 4.6 Development Standards
*   **Type Safety:** All frontend code must pass strict TypeScript validation (`npm run type-check` or `npm run build`) before merging.
*   **Interface Consistency:** Data models must be synchronized between Backend (Pydantic/SQLModel) and Frontend (TypeScript Interfaces).
*   **Build Verification:** Features are not considered complete until the application builds successfully in the Docker production environment.
