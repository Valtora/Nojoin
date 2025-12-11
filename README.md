<div align="center">
    <h1><img src="https://iili.io/fueA2wB.png" alt="Nojoin Logo" height="45" width="45" style="vertical-align: middle; horizontal-align: middle;"/> <span style="color: #F36012;"><br>Nojoin</span></h1>
        <p>
           <strong>A Self-Hosted Meeting Assistant</strong>
            </p>
    <p>
        <img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg" alt="License">
        <img src="https://img.shields.io/github/v/release/Valtora/Nojoin" alt="Release">
        <img src="https://github.com/Valtora/Nojoin/actions/workflows/companion-tauri.yml/badge.svg" alt="Companion Build">
        <img src="https://github.com/Valtora/Nojoin/actions/workflows/docker-publish.yml/badge.svg" alt="Docker Build">
    </p>
</div>

<!-- Screenshot 1 -->
![Nojoin Dashboard](https://iili.io/fuHyWPt.png)

---
## Why Nojoin?

Most meeting assistants require you to invite them to join your meetings or upload sensitive business conversations to the cloud. Nojoin is different.

- 🔒 **100% Private:** Audio and transcripts never leave your server (unless you want them to for LLM processing).
- 🚀 **Unlimited:** No monthly limits on recording minutes.
- 🧠 **Smart:** Uses OpenAI Whisper (Turbo) for transcription and Pyannote for speaker identification.
- 💬 **Interactive:** Chat with your meetings using ChatGPT, Claude, Gemini, or Ollama.
- ✨ **Best of All:** Nojoin doesn't need to awkwardly join in on your meetings.

## 📚 Table of Contents
- [Why Nojoin?](#why-nojoin)
- [Quick Start](#-quick-start)
- [Hardware Requirements](#%EF%B8%8F-hardware-requirements)
- [Features](#-features)
- [System Architecture](#-system-architecture)
- [API Keys & Configuration](#-api-keys--configuration)
- [User Management](#-user-management)
- [Installation & Setup](#%EF%B8%8F-installation--setup)
- [Troubleshooting](#-troubleshooting)
- [Roadmap](#%EF%B8%8F-roadmap)
- [Contributing](#-contributing)
- [Editions](#-editions)
- [Legal](#%EF%B8%8F-legal)

## ⚡ Quick Start

1.  **Clone:** `git clone https://github.com/Valtora/Nojoin && cd Nojoin`
2.  **Launch:** `docker compose up -d` (Pulls pre-built images from GHCR)
3.  **Use:** Open `https://localhost:14443` (Accept self-signed cert warning)
4.  **Configure:** Follow the first-run wizard to set up API keys and preferences.
5.  **Companion App:** Go to the [Releases](https://github.com/Valtora/Nojoin/releases) page download, install, and connect the companion app on client machines to start recording audio.

    - See [Installation & Setup](#installation--setup) for CPU-only mode and configuration details.

## 🖥️ Hardware Requirements

*   **Backend Server:**
    *   **Recommended:** Windows 11 (with WSL2) or Linux system with a compatible NVIDIA GPU (CUDA 12.x support).
    *   **Minimum:** 8GB VRAM for optimal performance (Whisper Turbo + Pyannote).
    *   **macOS Hosting:** Hosting the **backend** on macOS via Docker is **not recommended**.
        - Docker on macOS cannot pass through the Apple Silicon GPU (Metal) to containers. This forces the system to run in CPU-only mode, which is significantly slower for transcription and diarization.
*   **Companion App:**
    *   Fully supported on Windows, macOS, and Linux.

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
*   **User Management & Security:**
    *   **Role-Based Access:** Owner, Admin, and User roles with granular permissions.
    *   **Invitation System:** Secure registration via invite links with expiration and usage limits.
    *   **User Data:** Complete data cleanup on user deletion (files, database records, and logs).

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
    *   **macOS Support:** Uses native ScreenCaptureKit for system audio capture (requires macOS 12.3+).

## 🔑 API Keys & Configuration

Nojoin requires certain API keys to function fully. The first-run wizard will request these keys but they can also be entered in the **Settings** -> **AI Services** page of the web interface after installation.

### Hugging Face Token (Required for Diarization)
To enable speaker diarization (identifying who is speaking), you need a Hugging Face token.

**Privacy Note:** This token is **only** used to download the model weights from Hugging Face. All audio processing and diarization happens locally on your server. No audio data is sent to Hugging Face.

1.  Create an account on [Hugging Face](https://huggingface.co/).
2.  Generate an Access Token. You can use a token with fine-grained permissions:
    *   Select "Read access to contents of selected repos".
    *   Select the following repositories:
        *   `pyannote/speaker-diarization-community-1`
        *   `pyannote/wespeaker-voxceleb-resnet34-LM`
3.  Accept the user conditions for the following models:
    *   [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
    *   [`pyannote/wespeaker-voxceleb-resnet34-LM`](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM)
4.  Enter this token in the Nojoin **Settings > AI Settings**.

## LLM Providers (optional but recommended)
**To use the meeting note generation, speaker/title inference, and meeting chat features, you need an API key from one of the supported providers.**

**Privacy Note:** If you use cloud-based LLM providers (OpenAI, Anthropic, Google Gemini), your meeting transcripts and notes will be sent to their API for processing. To keep everything 100% local, use **Ollama**.

*   **OpenAI**
*   **Anthropic**
*   **Google Gemini**
*   **Ollama** (Local LLMs - no API key required, but requires setup)

## 👥 User Management

Nojoin includes a robust user management system designed for self-hosted environments.

### Roles
*   **Owner:** The first user created is automatically assigned the Owner role. Has full system access and cannot be deleted.
*   **Admin:** Can manage users, create/revoke invites, and view system settings.
*   **User:** Standard access to record and manage their own meetings.

### Invitation System
Registration is restricted to invited users only.
1.  **Create Invite:** Admins generate invite links with specific roles (Admin/User), expiration dates, and usage limits.
2.  **Revoke:** Invites can be revoked at any time to prevent further signups while keeping a record of the code.
3.  **Delete:** Revoked invites can be permanently deleted from the system.

### Data Cleanup
When a user is deleted, Nojoin performs a **hard delete** of all associated data to ensure privacy:
*   **Files:** Audio recordings and proxy files are physically removed from the disk.
*   **Database:** User account, recordings, transcripts, chat history, and speaker profiles are cascaded and removed.
*   **Anonymization:** Invitations created by the deleted user are preserved but anonymized (orphaned) to maintain invite code validity for other users.

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
    *Note: This pulls pre-built images from GitHub Container Registry. To build from source, use `docker compose up -d --build`.*

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
    *   **Remote Access:** The Docker images are pre-configured to work on any domain. Simply access the server via its IP or domain name (e.g., `https://192.168.1.50:14443`).
        *   *Note: You may need to add your domain to `ALLOWED_ORIGINS` in `.env` if you encounter CORS issues.*

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

## ❓ Troubleshooting

<details>
<summary><strong>Transcription is slow / GPU not being used</strong></summary>

Ensure you have passed the GPU to the container correctly.
1. Check if `nvidia-smi` works on your host.
2. Verify the `docker-compose.yml` has the `deploy: resources: reservations: devices` section uncommented.
3. If on Windows, ensure you have the latest WSL2 drivers.
</details>

<details>
<summary><strong>Cannot access Nojoin from another computer</strong></summary>

1. Check your firewall settings on the host machine (port 14443).
2. Ensure `ALLOWED_ORIGINS` in your `.env` file includes your server's IP or domain (e.g., `https://192.168.1.50:14443`).
</details>

<details>
<summary><strong>Ollama connection failed</strong></summary>

If running Ollama on the host, use `http://host.docker.internal:11434` instead of `localhost`. Docker containers cannot see `localhost` of the host directly.
</details>

## 🗺️ Roadmap

- [x] **Windows & Linux Support** (Stable)
- [ ] **macOS Support** (In Progress - Target: Jan 2026)
    - Full stability for macOS companion app.
    - Native ScreenCaptureKit integration.
- [ ] **Real-time Transcription** (Target: April 2026)
    - Live transcript generation during recording.
    - Instant feedback loop.
    - Real-time suggestions and notes.

## 🤝 Contributing

We welcome contributions from the community! Whether it's fixing bugs, improving documentation, or adding new features, your help is appreciated.

Please see our [Contributing Guide](CONTRIBUTING.md) for details on how to get started.

## 📦 Editions

*   **Community Edition:** This is the free and open-source version of Nojoin, designed for self-hosting and community support. It includes all core features for recording, processing, and analyzing meetings.

## ⚖️ Legal

**License**

Nojoin is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.
This ensures that the software remains free and open-source, and that any modifications made to the code (even if run as a service) must be shared back with the community.

Please review our [Legal Disclaimer](LEGAL.md) regarding the recording of conversations and compliance with local laws.

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.
