# 🎙️ Nojoin

## ⚖️ Legal Disclaimer

**Important:** You are responsible for complying with all applicable laws in your jurisdiction regarding the recording of conversations. Many jurisdictions require the consent of all parties before a conversation can be recorded. By using Nojoin, you acknowledge that you will use this software in a lawful manner. The developers of Nojoin assume no liability for any unlawful use of this application.

<img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/636ff7be-afc0-43b1-ab80-a3c0efa8eff8" />

## 🚀 Overview

**Nojoin** is a distributed, containerized meeting intelligence platform. It enables users to record system audio from any client device, process it centrally on a powerful GPU-enabled server, and access transcripts, diarization, and AI-generated insights via a modern web interface.

**Core Philosophy:**
*   **Centralized Intelligence:** Heavy lifting (Whisper/Pyannote) happens on a dedicated server.
*   **Ubiquitous Access:** Manage and view meetings from any device with a browser.
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
*   **Docker Desktop** (with NVIDIA Container Toolkit support for GPU acceleration).
*   **NVIDIA GPU** (Highly recommended for reasonable processing times).

### Quick Start (Docker Compose)

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Valtora/Nojoin
    cd Nojoin
    ```

2.  **Start the Stack:**
    ```bash
    docker-compose up -d
    ```
    This will spin up the Database, Redis, API, Worker, and Frontend.

3.  **Access the Application:**
    *   **Web Interface:** Open `http://localhost:14141` (or configured port).
    *   **API Docs:** Open `http://localhost:8000/docs`.

### Running the Companion App
Navigate to the `companion` directory and run the Rust application:
```bash
cd companion
cargo run --release
```
*Note: You will need the Rust toolchain installed.*

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.
