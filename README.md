# 🎙️ Nojoin

## ⚖️ Legal Disclaimer

**Important:** You are responsible for complying with all applicable laws in your jurisdiction regarding the recording of conversations. Many jurisdictions require the consent of all parties before a conversation can be recorded. By using Nojoin, you acknowledge that you will use this software in a lawful manner. The developers of Nojoin assume no liability for any unlawful use of this application.

## 🚀 Overview

**Nojoin** is a distributed, containerized meeting intelligence platform. It enables users to record system audio from any client device, process it centrally on a powerful GPU-enabled server, and access transcripts, diarization, and AI-generated insights via a modern web interface.

**Core Philosophy:**
*   **Centralized Intelligence:** Heavy lifting (Whisper/Pyannote) happens on a dedicated server.
*   **Ubiquitous Access:** Manage and view meetings from any desktop device with a browser. (The companion app is currently desktop-only, mobile support will be added in future releases.)
*   **Privacy First:** Self-hosted architecture ensures audio and transcripts never leave your control unless explicitly configured for external LLM services.

## ✨ Features

*   **Distributed Architecture:**
    *   **Server:** Dockerized backend handling heavy AI processing (Whisper, Pyannote).
    *   **Web Client:** Modern Next.js interface for managing meetings from anywhere.
    *   **Companion App:** Lightweight Rust system tray app for capturing audio on client machines.
*   **Advanced Audio Processing:**
    *   **Local-First Transcription:** Uses OpenAI's Whisper (Turbo/Large models) for accurate, private transcription.
    *   **Speaker Diarization:** Automatically identifies distinct speakers using Pyannote.
    *   **Dual-Channel Recording:** Captures both system audio (what you hear) and microphone input (what you say).
*   **Meeting Intelligence:**
    *   **LLM-Powered Notes:** Generate summaries, action items, and key takeaways using OpenAI, Anthropic, or Google Gemini.
    *   **Chat Q&A:** "Chat with your meeting" to ask specific questions about the content.
*   **Organization & Search:**
    *   **Global Speaker Library:** Centralized management of speaker identities across all recordings.
    *   **Full-Text Search:** Instantly find content across transcripts, titles, and notes.
    *   **Tagging:** Organize meetings with custom tags.

## ⚙️ System Architecture

Nojoin is composed of three distinct subsystems:

1.  **The Server (Dockerized):**
    *   Hosted on a machine with NVIDIA GPU capabilities.
    *   Runs the API (FastAPI), Worker (Celery), Database (PostgreSQL), and Broker (Redis).
    *   Handles all heavy lifting: VAD, Transcription, and Diarization.

2.  **The Web Client (Next.js):**
    *   The primary interface for users.
    *   Provides a dashboard for playback, transcript editing, and system configuration.

3.  **The Companion App (Rust):**
    *   Runs on client machines (Windows, macOS, Linux).
    *   Sits in the system tray and handles audio capture.
    *   Uploads audio to the server for processing.

## 🛠️ Installation & Setup

### Prerequisites
*   **Docker Desktop**
*   **NVIDIA GPU** (Optional, but highly recommended for faster processing).

### Quick Start (Docker Compose)

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Valtora/Nojoin
    cd Nojoin
    ```

2.  **Start the Stack:**

    **Option A: NVIDIA GPU (Default)**
    Requires an NVIDIA GPU. Much faster processing.
    ```bash
    docker compose up -d
    ```

    **Option B: CPU**
    Works on all systems. Slower processing speeds.
    ```bash
    docker compose -f docker-compose.cpu.yml up -d
    ```
    *Note: The first run may take several minutes as it needs to download large Docker images.*

3.  **Access the Application:**
    *   **Web Interface:** Open `https://localhost:14443`
        *   *Note: You will see a "Not Secure" warning because of the self-signed certificate.*

### Running the Companion App
1.  Go to the [Releases](https://github.com/Valtora/Nojoin/releases) page.
2.  Download the executable for your operating system (Windows, macOS, or Linux).
3.  Run the application. It will appear in your system tray.
4.  The web app also has a 'Download Companion App' button that will take you to the above link.

*Note: For developers, you can still build from source by navigating to the `companion` directory and running `cargo run --release`.*

## 📦 Editions

*   **Community Edition:** This is the free and open-source version of Nojoin, designed for self-hosting and community support. It includes all core features for recording, processing, and analyzing meetings.
*   **Enterprise Edition:** (Coming Soon) A paid version designed for larger organizations. It will include additional deployment options, advanced administration features, and dedicated support.

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.
