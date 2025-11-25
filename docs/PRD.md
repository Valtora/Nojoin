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
*   **API Service:** FastAPI-based REST API for data management and client communication.
*   **Worker Service:** Celery-based background worker handling resource-intensive tasks (transcription, diarization) using the NVIDIA Runtime.
*   **Database:** PostgreSQL serving as the single source of truth for all metadata, transcripts, and speaker profiles.
*   **Broker:** Redis for task queue management and caching.
*   **Storage:** Docker Volumes for persistent storage of raw audio and model artifacts.

### 2.2 The Web Client (Next.js)
The primary user interface for interacting with the system.
*   **Framework:** Next.js (React) with TypeScript.
*   **Styling:** Tailwind CSS for a responsive, modern design.
*   **Functionality:** Dashboard, playback, transcript editing, speaker management, and system configuration.
*   **Companion Status:** Visual indicator (warning bubble) when the Companion App is not detected.

### 2.3 The Companion App (Rust)
A lightweight, cross-platform system tray application responsible for audio capture.
*   **Directory:** `companion/`
*   **Language:** Rust.
*   **Platforms:** Windows, macOS, Linux (First-class support for all).
*   **Role:** Acts as a local server. Captures system audio (loopback) and microphone input upon receiving commands from the Web Client.
*   **UI:** Minimalist system tray menu for status indication, updates, help, and exit. **No recording controls in the tray.**

---

## 3. Core Features

### 3.1 Audio Recording (Companion App)
*   **Headless Operation:** The app runs silently in the background.
*   **Local Control Server:** Exposes a local HTTP/WebSocket server (e.g., on `localhost:12345`) to receive commands (`start`, `stop`, `pause`, `resume`) from the Web Client.
*   **System Tray Resident:**
    *   **Status:** Visual indication of state (Idle, Recording, Paused, Error).
    *   **Menu:** "Check for Updates", "Help", "Exit".
*   **Dual-Channel Capture:** Simultaneously records system output (what you hear) and microphone input (what you say).
*   **Pause & Resume:** Supports pausing via Web Client commands.
*   **Smart Uploads:**
    *   Audio is sent to the server.
    *   If pauses occur, the audio may be sent as multiple segments.
    *   **Server-Side Concatenation:** The server immediately concatenates these segments upon receipt/completion.
*   **Visual Feedback:** Tray icon changes color/shape based on status. Native system notifications for status changes (Recording Started, Stopped, Paused) and errors.
*   **Resilience:**
    *   **Auto-Reconnect:** Retry logic for audio uploads with exponential backoff.
    *   **Config Loading:** robust search for `config.json` in executable directory.

### 3.2 Transcription & Diarization (Server-Side)
*   **Async Processing:** Audio processing is decoupled from the UI. Users can close the browser or companion app immediately after recording; the server handles the rest.
*   **Engine:** OpenAI Whisper (Local) for transcription.
*   **Diarization:** Pyannote (Local) for speaker separation.
*   **Pipeline:**
    1.  **Preprocessing:** Convert to mono 16kHz WAV, VAD filtering (Silero).
    2.  **Transcription:** Whisper (Turbo/Large models supported).
    3.  **Diarization:** Pyannote processing.
    4.  **Alignment:** Merging transcript segments with speaker timestamps.
*   **Progress Tracking:** Real-time status updates (Queued, Processing, Completed, Failed) pushed to the Web Client.
*   **Export:**
    *   Export transcripts to `.txt` format via the Web Client.

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

### 3.4 Meeting Intelligence
*   **LLM-Powered Notes:**
    *   Generate summaries, action items, and key takeaways using configured LLM providers (OpenAI, Anthropic, or Local LLMs via API).
    *   Notes are stored in the database and rendered as rich Markdown in the Web UI.
*   **Chat Q&A:**
    *   "Chat with your meeting" feature allowing users to ask questions about specific recordings.
    *   Uses transcript context to provide accurate answers.

### 3.5 Search & Organization
*   **Tagging System:**
    *   Global tag management.
    *   Apply tags to recordings for categorization (e.g., "Daily Standup", "Client X").
*   **Advanced Search:**
    *   Full-text search across meeting titles, notes, and transcript content.
    *   Filter by Date Range, Tags, and Speakers.
    *   Fuzzy matching for typo tolerance.

### 3.6 Web Playback & Transcript Interface
*   **Layout:**
    *   **Four-Pane Dashboard:**
        *   **Left Sidebar:** Scrollable list of recordings (Meeting Cards) with status indicators. Always visible.
        *   **Center Panel:** Main content area displaying the Meeting Title, Audio Player, and Transcript/Notes.
        *   **Speaker Panel:** A dedicated column between the Transcript and Chat panels listing all identified speakers. Includes a "Play" button to preview the speaker's voice snippet.
        *   **Right Panel:** Collapsible utility panel for "Chat with Meeting", Settings, and Import Audio.
*   **Modern Player:** HTML5-based audio player with waveform visualization.
*   **Synced Transcript:** Clicking a transcript segment seeks the audio to that timestamp. Current text highlights during playback.
*   **Edit Mode:** Allow users to correct transcript text and speaker assignment directly in the browser.
*   **Responsive Design:** Fully functional on desktop, tablet, and mobile browsers.

### 3.7 Settings & Configuration
*   **Server Settings:** Manage API keys, model selection (Whisper size), and storage paths via the Web UI.
*   **User Preferences:** Theme selection (Dark/Light), default playback speed.

---

## 4. Technical Requirements

### 4.1 Server Stack
*   **Language:** Python 3.11+
*   **Framework:** FastAPI
*   **Task Queue:** Celery
*   **Database:** PostgreSQL 16 (accessed via SQLModel/SQLAlchemy)
*   **Broker:** Redis
*   **Container Runtime:** Docker (with NVIDIA Container Toolkit support)

### 4.2 Web Client Stack
*   **Framework:** Next.js (React)
*   **Language:** TypeScript
*   **Styling:** Tailwind CSS
*   **State Management:** React Query / Zustand

### 4.3 Companion App Stack
*   **Language:** Rust
*   **Audio:** cpal (Cross-Platform Audio Library)
*   **Async Runtime:** Tokio
*   **HTTP Client:** Reqwest
*   **GUI/Tray:** Native Windows/macOS/Linux tray integration

### 4.4 Deployment
*   **Docker Compose:** Primary deployment method orchestrating API, Worker, DB, Redis, and Web Frontend containers.
*   **Versioning:** Semantic versioning applied to Docker images.

### 4.5 Containerization Standards
*   **Base Images:** Utilize optimized, pre-built images (e.g., `pytorch/pytorch` with CUDA runtime) to minimize build time and image size. Avoid building heavy dependencies (like CUDA/PyTorch) from scratch.
*   **Context Management:** Maintain a strict `.dockerignore` file to exclude build artifacts (Rust `target/`, Node `node_modules/`), version control history (`.git`), and local environment files from the build context.
*   **Dependency Optimization:** Filter `requirements.txt` during the build process to prevent redundant installation of packages already present in the base image (e.g., `torch`, `torchaudio`).
*   **Layer Efficiency:** Combine `RUN` instructions where possible and clean up package manager caches (`apt-get clean`, `rm -rf /var/lib/apt/lists/*`) in the same layer to reduce image size.
