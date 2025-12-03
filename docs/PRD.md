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

### 2.4 Configuration Management
*   **Unified Strategy:** Configuration is split between system-wide infrastructure settings and user-specific preferences.
*   **System Config:** Stored in `data/config.json` (Server) and `config.json` (Companion). Includes infrastructure URLs, device paths, and hardware settings.
*   **User Settings:** Stored in the PostgreSQL database per user. Includes UI themes, API keys, model preferences, and AI settings.
*   **Initial Setup:** The Setup Wizard collects critical user settings (LLM Provider, API Keys, HuggingFace Token) during the creation of the first admin account, ensuring immediate system readiness without manual restarts.
*   **Database Initialization:** The system automatically handles database schema creation and migrations on startup, ensuring the database is always in a consistent state.
*   **Security:** Sensitive user data (API keys) is stored in the database, not in flat files.
*   **Companion Config:**
    *   **Localhost Enforcement:** The Companion App defaults to `localhost` but supports a configurable `api_host` for remote deployments.
    *   **Auto-Configuration:** The "Connect to Companion" flow in the Web Client automatically configures the Companion App with the correct Server IP and Port.
    *   **Manual Configuration:** A "Settings" window (accessible via System Tray) allows manual entry of the API Host and Port if auto-configuration is not possible.
    *   **Legacy Migration:** Old config formats (using `api_url`) are automatically migrated to the new host/port-based format.
    *   **Config Preservation:** The Windows installer preserves `config.json` during updates.

### 2.5 Security
*   **SSL/TLS:** All communication between components (Frontend, Backend, Companion) is encrypted via HTTPS using Nginx as a reverse proxy.
*   **Automatic SSL Generation:** The system automatically generates self-signed SSL certificates on startup if they are missing, ensuring immediate secure access for local deployments.
*   **HTTPS Enforcement:** HTTP requests to port 14141 are automatically redirected to HTTPS on port 14443. The frontend is only accessible through the Nginx reverse proxy, preventing unencrypted access.
*   **Authentication:** JWT-based authentication for API access.
*   **JWT Secret Key:** A secure SECRET_KEY for signing JWT tokens is automatically generated on first startup and persisted to `data/.secret_key`. This ensures tokens remain valid across container restarts. Advanced deployments can override this by setting the `SECRET_KEY` environment variable.
*   **Authorization:** Strict ownership checks ensure users can only access their own data.
*   **CORS:** Restricted to allowed origins.

---

## 3. Core Features

### 3.1 Audio Recording (Companion App)
*   **Headless Operation:** The app runs silently in the background.
*   **Local Control Server:** Exposes a local HTTP/WebSocket server (e.g., on `localhost:12345`) to receive commands (`start`, `stop`, `pause`, `resume`) from the Web Client.
*   **System Tray Resident:**
    *   **Status:** Visual indication of state (Idle, Recording, Paused, Error).
    *   **Menu:** "Open Nojoin", "Check for Updates", "Help", "About", "Restart", "Exit".
*   **Dual-Channel Capture:** Simultaneously records system output (what you hear) and microphone input (what you say).
*   **Pause & Resume:** Supports pausing via Web Client commands.
*   **Smart Uploads:**
    *   Audio is sent to the server.
    *   If pauses occur, the audio may be sent as multiple segments.
    *   **Server-Side Concatenation:** The server immediately concatenates these segments upon receipt/completion.
*   **Visual Feedback:** Tray icon changes color/shape based on status. Native system notifications for status changes (Recording Started, Stopped, Paused) and errors.
*   **Audio Monitoring:**
    *   **Peak Hold Logic:** The Companion App tracks the maximum audio level (peak) between polling intervals. This ensures that even brief speech is detected by the Web Client's health check, preventing false "No Audio" warnings during quiet moments.
*   **Resilience:**
    *   **Auto-Reconnect:** Retry logic for audio uploads with exponential backoff.
    *   **Config Loading:** robust search for `config.json` in executable directory.

### 3.1.1 Import Recordings
*   **Functionality:** Users can import existing audio files (e.g., from Zoom, Teams, Google Meet) directly via the Web Client.
*   **Supported Formats:** WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, Opus.
*   **Metadata:** Users can specify a custom meeting name and the original recording date/time during import.
*   **Processing:** Imported files enter the same processing pipeline (Transcription -> Diarization -> Alignment) as live recordings.

### 3.2 Transcription & Diarization (Server-Side)
*   **Async Processing:** Audio processing is decoupled from the UI. Users can close the browser or companion app immediately after recording; the server handles the rest.
*   **Engine:** OpenAI Whisper (Local) for transcription.
*   **Diarization:** Pyannote (Local) for speaker separation.
*   **Pipeline:**
        1.  **Validation:** Verify audio file integrity and duration using `ffprobe`.
        2.  **Preprocessing:** Convert to mono 16kHz WAV, VAD filtering (Silero).
        3.  **Transcription:** Whisper (Turbo/Large models supported).
        4.  **Diarization:** Pyannote processing.
        5.  **Alignment:** Merging transcript segments with speaker timestamps.
    *   **Robustness:**
        *   **Validation:** Early rejection of invalid or zero-duration files.
        *   **Silence Detection:** Graceful handling of recordings with no speech (marked as Processed with empty transcript).
        *   **Error Handling:** Granular error states and messages for debugging.
        *   **Cleanup:** Guaranteed removal of temporary files.
        *   **Robustness & Recovery:**
            *   **Automatic Repair:** Corrupted audio files are automatically detected and repaired (re-encoded) using ffmpeg.
            *   **Retry Logic:** Transient errors (network, resource contention) trigger automatic retries with exponential backoff.
            *   **Graceful Failure:** Unrecoverable errors result in a clear "Error" status with detailed logs, preventing pipeline stalls.
*   **Progress Tracking:**
    *   **Granular Status:** Real-time status updates pushed to the Web Client, including:
        *   **Client State:** "Meeting in Progress", "Meeting Paused", "Uploading...".
        *   **Processing Steps:** "Filtering silence...", "Transcribing...", "Determining speakers...", "Learning voiceprints...".
*   **Export:**
    *   Export to `.txt` format via the Web Client with flexible options:
        *   **Transcript Only:** Export the diarized transcript with timestamps
        *   **Notes Only:** Export AI-generated meeting notes
        *   **Both:** Combined export with transcript and notes in a single file
    *   Export modal allows users to choose their preferred export option

### 3.3 Speaker Management
*   **Global Speaker Library:**
    *   Centralized database of known speakers stored in PostgreSQL.
    *   Web interface for managing identities (Name, Description).
*   **Intelligent Linking:**
    *   "Unknown Speakers" in new recordings can be linked to existing Global Speakers via the Web UI.
    *   Renaming a speaker in one meeting offers to update the Global Speaker or create a new one.
    *   **Visual Identification:**
        *   Color-coded speaker chips in the transcript view.
        *   Filter transcripts by specific speakers.
    *   **Error Handling & Recovery:**
        *   **Manual Retry:** Users can manually trigger a retry of the speaker inference process if it fails or yields poor results.
        *   **Async Execution:** Manual retries are processed asynchronously via the worker queue to prevent UI blocking.
*   **Voiceprint Management:**
    *   **Optional Auto-Extraction:** Speaker voiceprints (embeddings) can be automatically extracted during processing. This can be disabled in Settings for faster processing.
    *   **On-Demand Creation:** Users can create voiceprints for individual speakers via the "Create Voiceprint" context menu option in the Speaker Panel.
    *   **Batch Creation:** "Create All Voiceprints" button allows extracting voiceprints for all speakers in a recording at once.
    *   **Voiceprint Modal:** When creating a voiceprint, users are presented with options:
        *   Link to a matched Global Speaker (if similarity is detected).
        *   Create a new Global Speaker with the voiceprint.
        *   Force-link to a different Global Speaker (for training/correction).
        *   Keep the voiceprint local to the recording only.
    *   **Visual Indicator:** Speakers with voiceprints display a fingerprint icon, indicating they can be recognized in future recordings.

### 3.4 User Management
*   **Multi-Tenant System:** Supports multiple users with role-based access control (Admin/User).
*   **Admin Safeguards:**
    *   **Last Admin Protection:** The system prevents the deletion of the last remaining administrator account to ensure system accessibility.
    *   **Deletion Confirmation:** Critical actions like deleting a user require explicit confirmation via a custom modal dialog to prevent accidental data loss.

### 3.5 Meeting Intelligence
*   **LLM-Powered Notes:**
    *   Generate summaries, action items, and key takeaways using configured LLM providers (OpenAI, Anthropic, or Gemini).
    *   Notes are stored in the database and rendered as rich Markdown in the Web UI.
    *   **Comprehensive Meeting Notes:** AI-generated notes include:
        *   **Topics Discussed:** List of major themes covered
        *   **Summary:** Comprehensive overview of the meeting
        *   **Detailed Notes:** Per-topic breakdown including key points, discussions, decisions, rationales, and open questions
        *   **Action Items/Tasks:** Assigned tasks with ownership and due dates
        *   **Miscellaneous:** Additional important information
    *   **Panel Switching:** Users can toggle between Transcript and Notes views using tab navigation
    *   **Context-Aware Tools:** Search, find/replace, undo/redo, and export are available in both views
    *   **Unified Find/Replace:** Changes made via find/replace apply to both transcript and notes to maintain consistency
*   **Chat Q&A:**
    *   "Chat with your meeting" feature allowing users to ask questions about specific recordings.
    *   **Persistent Chat:** Chat history is saved per recording.
    *   **Streaming:** Real-time responses using Server-Sent Events.
    *   **Custom Instructions:** Users can configure system prompts for tone and format.
    *   Uses transcript context to provide accurate answers.

### 3.6 Search & Organization
*   **Tagging System:**
    *   Global tag management.
    *   Apply tags to recordings for categorization (e.g., "Daily Standup", "Client X").
*   **Advanced Search:**
    *   Full-text search across meeting titles, notes, and transcript content.
    *   Filter by Date Range, Tags, and Speakers.
    *   **Fuzzy Search:** Client-side fuzzy matching (using Fuse.js) for recordings and settings, allowing for typo tolerance and semantic-like discovery.

### 3.7 Web Playback & Transcript Interface
*   **Layout:**
    *   **Dashboard Layout:**
        *   **Left Sidebar:** Scrollable list of recordings (Meeting Cards) with status indicators. Always visible.
        *   **Main Content Area:**
            *   **Center Panel:** Tabbed view for Transcript and Meeting Notes.
            *   **Speaker Panel:** Resizable panel to the right of the transcript, listing identified speakers.
            *   **Chat Sidebar:** Collapsible and resizable right-hand sidebar for "Chat with Meeting" functionality.
*   **Modern Player:** HTML5-based audio player with waveform visualization.
*   **Synced Transcript:** Clicking a transcript segment seeks the audio to that timestamp. Current text highlights during playback.
*   **Edit Mode:** Allow users to correct transcript text and speaker assignment directly in the browser.
*   **Responsive Design:** Fully functional on desktop, tablet, and mobile browsers.

### 3.8 Settings & Configuration
*   **Server Settings:** Manage API keys, model selection (Whisper size), and storage paths via the Web UI.
*   **User Preferences:** Theme selection (Dark/Light), default playback speed.

### 3.9 UI/UX Standards
*   **Accessibility:** High contrast ratios for text and interactive elements to ensure readability.
*   **Theme Support:** Full support for both Light and Dark modes with consistent styling and visibility.
*   **Visual Clarity:** Distinct borders and clear separation of UI components.

---

## 4. Technical Requirements

### 4.1 Server Stack
*   **Language:** Python 3.11+
*   **Framework:** FastAPI
*   **Task Queue:** Celery
*   **Database:** PostgreSQL 16 (accessed via SQLModel/SQLAlchemy)
*   **Migrations:** Alembic
*   **Broker:** Redis
*   **Container Runtime:** Docker (with NVIDIA Container Toolkit support)

### 4.2 Web Client Stack
*   **Framework:** Next.js (React)
*   **Language:** TypeScript
*   **Styling:** Tailwind CSS
*   **State Management:** React Query / Zustand

### 4.3 Companion App Stack
*   **Framework:** Tauri v1.5
*   **Language:** Rust (Core Logic)
*   **Audio:** cpal (Cross-Platform Audio Library)
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

### 4.4 Deployment
*   **Docker Compose:** Primary deployment method orchestrating API, Worker, DB, Redis, and Web Frontend containers.
*   **Hardware Support:**
    *   **NVIDIA GPU (Default):** The base `docker-compose.yml` is configured for GPU inference by default.
    *   **CPU-Only (Optional):** CPU support is enabled by using the CPU configuration file: `docker compose -f docker-compose.cpu.yml up -d`.
*   **Versioning:** Semantic versioning applied to Docker images.

### 4.5 Containerization Standards
*   **Base Images:** Utilize optimized, pre-built images (e.g., `pytorch/pytorch` with CUDA runtime) to minimize build time and image size. Avoid building heavy dependencies (like CUDA/PyTorch) from scratch.
*   **Context Management:** Maintain a strict `.dockerignore` file to exclude build artifacts (Rust `target/`, Node `node_modules/`), version control history (`.git`), and local environment files from the build context.
*   **Dependency Optimization:** Filter `requirements.txt` during the build process to prevent redundant installation of packages already present in the base image (e.g., `torch`, `torchaudio`).
*   **Layer Efficiency:** Combine `RUN` instructions where possible and clean up package manager caches (`apt-get clean`, `rm -rf /var/lib/apt/lists/*`) in the same layer to reduce image size.
