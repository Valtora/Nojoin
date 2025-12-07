<div align="center">
    <h1><img src="https://iili.io/fueA2wB.png" alt="Nojoin Logo" height="45" width="45" style="vertical-align: middle; margin-right: 10px;"/>Nojoin</h1>
      <p>
          <strong>Self-Hosted Meeting Intelligence. Privacy First.</strong>
            </p>

<!-- Screenshot 1 -->
![Nojoin Dashboard](https://iili.io/fuHyWPt.png)

---

## Why Nojoin?

Most meeting assistants require you to invite them to join your meetings or upload sensitive business conversations to the cloud. Nojoin is different.

- 🔒 **100% Private:** Audio and transcripts never leave your server (unless you want them to for LLM processing).
- 🚀 **Unlimited:** No monthly limits on recording minutes.
- 🧠 **Smart:** Uses OpenAI Whisper (Turbo) for transcription and Pyannote for speaker identification.
- 💬 **Interactive:** Chat with your meetings using ChatGPT, Claude, Gemini, or Ollama.
- ✨ **Best of All:** Nojoin doesn't need to join awkwardly on your meetings.

## ⚡ Quick Start

1.  **Clone:** `git clone https://github.com/Valtora/Nojoin && cd Nojoin`
2.  **Launch:** `docker compose up -d`
3.  **Use:** Open `https://localhost:14443` (Accept self-signed cert warning)
4.  **Configure:** Follow the first-run wizard to set up API keys and preferences.
5.  **Companion App:** Download, install, and connect the companion app on client machines to start recording audio.

    *See [Installation & Setup](#installation--setup) for CPU-only mode and configuration details.*

## ✨ Features

*   **Distributed Architecture:**
    *   **Server:** Dockerized backend handling heavy AI processing (Whisper, Pyannote).
    *   **Web Client:** Modern Next.js interface for managing meetings from anywhere.
    *   **Companion App:** Lightweight Rust system tray app for capturing audio on client machines.
*   **Advanced Audio Processing:**
    *   **Local-First Transcription:** Uses OpenAI's Whisper (Turbo) for accurate, private transcription.
    *   **Speaker Diarization:** Automatically identifies distinct speakers using Pyannote Community 1.
    *   **Dual-Channel Recording:** Captures both system audio (what you hear) and microphone input (what you say).
*   **Meeting Intelligence:**
    *   **LLM-Powered Notes:** Generate summaries, action items, and key takeaways using OpenAI, Anthropic, Google Gemini, or Ollama.
    *   **Chat Q&A:** "Chat with your meeting" to ask specific questions about the content or make edits to notes.
*   **Organization & Search:**
    *   **Global Speaker Library:** Centralized management of speaker identities across all recordings.
    *   **Full-Text Search:** Instantly find content across transcripts, titles, and notes.
    *   **Tagging:** Organize meetings with custom tags.

## 🏗️ System Architecture

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

## 🔑 API Keys & Configuration

Nojoin requires certain API keys to function fully. The first-run wizard will request these keys but they can also be entered in the **Settings** -> **AI Services** page of the web interface after installation.

### Hugging Face Token (Required for Diarization)
To enable speaker diarization (identifying who is speaking), you need a Hugging Face token.
1.  Create an account on [Hugging Face](https://huggingface.co/).
2.  Generate an Access Token (Read permissions).
3.  Accept the user conditions for the following models:
    *   [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
4.  Enter this token in the Nojoin **Settings > AI Settings**.

### LLM Providers (Optional)
To use the "Chat with Meeting" and "Generate Notes" features, you need an API key from one of the supported providers:
*   **OpenAI**
*   **Anthropic**
*   **Google Gemini**
*   **Ollama** (Local LLMs - no API key required, but requires setup)

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
    
    Open `docker-compose.yml` and comment out the `deploy` section under the `worker` service.
    
    Then run:
    ```bash
    docker compose up -d
    ```
    *Note: The first run may take several minutes as it needs to download large Docker images.*

3.  **Access the Application:**
    *   **Web Interface:** Open `https://localhost:14443`
        *   *Note: You will see a "Not Secure" warning because of the self-signed certificate.*
    *   **Remote Access:** To access from another machine or domain, configure `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS` in your `.env` file. See `.env.example` for details.

### Using Local LLMs (Ollama)
If you are running Ollama on the same machine as the Nojoin Docker containers, you must use the special Docker host address:
*   **API URL:** `http://host.docker.internal:11434`
*   Do **not** use `localhost` or `127.0.0.1` as the container cannot see the host's localhost.

### Running the Companion App

1.  Go to the [Releases](https://github.com/Valtora/Nojoin/releases) page.
2.  Download the executable for your operating system (Windows, macOS, or Linux).
3.  Run the application. It will appear in your system tray.
4.  The web app also has a 'Download Companion App' button that will take you to the above link.

*Note: For developers, you can still build from source by navigating to the `companion` directory and running `cargo run --release`.*

> **🧪 Call for Testing:** The Companion App has been tested on Windows. We are looking for community feedback on **macOS** and **Linux** stability. Please report any OS-specific issues on GitHub.

## 📦 Editions

*   **Community Edition:** This is the free and open-source version of Nojoin, designed for self-hosting and community support. It includes all core features for recording, processing, and analyzing meetings.
*   **Enterprise Edition:** (Coming Soon) A paid version designed for larger organizations. It will include additional deployment options, advanced administration features, and dedicated support.

## ⚖️ Legal

Please review our [Legal Disclaimer](LEGAL.md) regarding the recording of conversations and compliance with local laws.

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.
