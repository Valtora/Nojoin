<!-- markdownlint-disable -->
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
![Nojoin Demo](docs/images/nojoin-demo.gif)

---

## ❔ Why Nojoin?

Most meeting assistants require users to invite bots to join meetings or upload sensitive business conversations to the cloud. Nojoin offers a different approach.

- **Privacy First:** Audio and transcripts remain on the user's server (unless explicitly configured for external LLM processing).
- **Unlimited:** No monthly limits on recording minutes.
- **Smart:** Utilizes OpenAI Whisper (Turbo) for transcription and Pyannote for speaker identification.
- **Interactive:** Enables chat with meetings using ChatGPT, Claude, Gemini, or Ollama.
- **Non-Intrusive:** Nojoin does not require joining meetings as a participant.

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

1. **Clone:** `git clone https://github.com/Valtora/Nojoin && cd Nojoin`
2. **Launch:** `docker compose up -d` (Pulls pre-built images from GHCR)
3. **Use:** Open `https://localhost:14443` (Accept self-signed cert warning)
4. **Configure:** Follow the first-run wizard to set up API keys and preferences.
5. **Companion App:** Navigate to the [Releases](https://github.com/Valtora/Nojoin/releases) page to download, install, and connect the companion app on client machines to start recording audio.

   - See [Installation & Setup](#%EF%B8%8F-installation--setup) for CPU-only mode and configuration details.

## 🖥️ Hardware Requirements

- **Backend Server:**
  - **Recommended:** Windows 11 (with WSL2) or Linux system with a compatible NVIDIA GPU (CUDA 12.x support).
  - **Minimum:** 8GB VRAM for optimal performance (Whisper Turbo + Pyannote).
  - **macOS Hosting:** Hosting the **backend** on macOS via Docker is **not recommended**.
    - Docker on macOS cannot pass through the Apple Silicon GPU (Metal) to containers. This forces the system to run in CPU-only mode, which is significantly slower for transcription and diarization.
- **Companion App:**
  - Currently supported on **Windows only**.
  - macOS and Linux companion apps are not yet available. Contributors are welcome to help build support for these platforms!

## ✨ Features

- **Distributed Architecture:**
  - **Server:** Dockerized backend handling heavy AI processing (Whisper, Pyannote).
  - **Web Client:** Modern Next.js interface for managing meetings from anywhere.
  - **Companion App:** Lightweight Rust system tray app for capturing audio on client machines.
- **Advanced Audio Processing:**
  - **Local-First Transcription:** Uses OpenAI's Whisper (default Turbo) for accurate, private transcription.
  - **Speaker Diarization:** Automatically identifies distinct speakers using Pyannote.
  - **System Audio Capture:** Captures both system audio out and microphone input.
- **Meeting Intelligence:**
  - **LLM-Powered Notes:** Generate summaries, action items, and key takeaways using OpenAI, Anthropic, Google Gemini, or Ollama.
  - **Chat Q&A:** "Chat with your meeting" to ask specific questions about the content or make edits to notes.
  - **Documents:** Upload documents to be processed by the LLM.
  - **Cross-Meeting Context:** Select tags to include meetings, notes, and documents from across all meetings with the same tag(s).
- **Organization & Search:**
  - **Global Speaker Library:** Centralized management of speaker identities across all recordings.
  - **Full-Text Search:** Instantly find content across transcripts, titles, and notes.
  - **Tagging:** Organize meetings with custom tags.
- **User Management & Security:**
  - **Role-Based Access:** Owner, Admin, and User roles with granular permissions.
  - **Invitation System:** Secure registration via invite links with expiration and usage limits.
  - **User Data:** Complete data cleanup on user deletion (files, database records, and logs).

## 🏗️ System Architecture

Nojoin is composed of three distinct subsystems:

1. **The Server (Dockerized):**
   - Hosted on a machine with NVIDIA GPU capabilities.
   - Runs the API (FastAPI), Worker (Celery), Database (PostgreSQL), and Broker (Redis).
   - Handles all heavy lifting: VAD, Transcription, and Diarization.

2. **The Web Client (Next.js):**
   - The primary interface for users.
   - Provides a dashboard for playback, transcript editing, and system configuration.

3. **The Companion App (Rust):**
   - Runs on Windows client machines.
   - Sits in the system tray and handles audio capture.
   - Uploads audio to the server for processing.
   - **Platform Support:** Currently Windows-only. Community contributions are welcome for macOS and Linux support!

## 🔑 API Keys & Configuration

Nojoin requires certain API keys to function fully. The first-run wizard will request these keys, but they can also be entered in the **Settings** -> **AI Services** page of the web interface after installation.

### Hugging Face Token (Required for Diarization)

To enable speaker diarization (identifying who is speaking), a Hugging Face token is required.

**Privacy Note:** This token is **only** used to download the model weights from Hugging Face. All audio processing and diarization happens locally on the server. No audio data is sent to Hugging Face.

1. Create an account on [Hugging Face](https://huggingface.co/).
2. Generate an Access Token. A token with fine-grained permissions can be used:
   - Select "Read access to contents of selected repos".
   - Select the following repositories:
     - `pyannote/speaker-diarization-community-1`
     - `pyannote/wespeaker-voxceleb-resnet34-LM`
3. Accept the user conditions for the following models:
   - [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
   - [`pyannote/wespeaker-voxceleb-resnet34-LM`](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM)
4. Enter this token in the Nojoin **Settings > AI Settings**.

## LLM Providers (optional but recommended)

**To use the meeting note generation, speaker/title inference, and meeting chat features, an API key from one of the supported providers is required.**

**Privacy Note:** If cloud-based LLM providers (OpenAI, Anthropic, Google Gemini) are used, meeting transcripts and notes will be sent to their API for processing. To keep everything 100% local, use **Ollama**.

- **OpenAI**
- **Anthropic**
- **Google Gemini**
- **Ollama** (Local LLMs - no API key required, but requires setup)

## 👥 User Management

Nojoin includes a robust user management system designed for self-hosted environments.

### Roles

- **Owner:** The first user created is automatically assigned the Owner role. Has full system access and cannot be deleted.
- **Admin:** Can manage users, create/revoke invites, and view system settings.
- **User:** Standard access to record and manage their own meetings.

### Invitation System

Registration is restricted to invited users only.

1. **Create Invite:** Admins generate invite links with specific roles (Admin/User), expiration dates, and usage limits.
2. **Revoke:** Invites can be revoked at any time to prevent further signups while keeping a record of the code.
3. **Delete:** Revoked invites can be permanently deleted from the system.

### Data Cleanup

When a user is deleted, Nojoin performs a **hard delete** of all associated data to ensure privacy:

- **Files:** Audio recordings and proxy files are physically removed from the disk.
- **Database:** User account, recordings, transcripts, chat history, and speaker profiles are cascaded and removed.
- **Anonymization:** Invitations created by the deleted user are preserved but anonymized (orphaned) to maintain invite code validity for other users.

## 🛠️ Installation & Setup

### Prerequisites

#### For Hosting (Docker)

- **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux)
- **NVIDIA GPU** (Optional, but highly recommended for faster processing).
  - Requires **NVIDIA Container Toolkit** to be installed on Linux.
  - Compute Capability 6.1+ (Pascal) recommended.

#### For Local Development

If you plan to develop or build Nojoin from source, you will need the following tools installed:

**General:**
- **Git**
- **Docker** (Required for running Database and Redis services)

**Backend (Python):**
- **Python 3.11**
- **FFmpeg** (Required for audio processing)
  - Linux: `sudo apt install ffmpeg`
  - Windows: Download and add to PATH.
- **PostgreSQL Development Headers**
  - Linux: `sudo apt install libpq-dev`
- **Compiler Tools**
  - Linux: `sudo apt install build-essential`
  - Windows: Microsoft Visual C++ Build Tools

**Frontend (Node.js):**
- **Node.js v20+** (LTS recommended)
- **npm** (comes with Node.js) or **pnpm**

**Companion App (Rust):**
- **Rust** (Latest Stable)
- **CMake** (Used by some Rust build scripts)
- **Platform-specific dependencies:**
  - **Linux:** `sudo apt install libwebkit2gtk-4.0-dev build-essential curl wget file libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`
  - **Windows:** Microsoft Visual C++ Build Tools

### Quick Start (Docker Compose)

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Valtora/Nojoin
   cd Nojoin
   ```

2. **Start the Stack:**

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

3. **Access the Application:**

   - **Web Interface:** Open `https://localhost:14443`
     - *Note: A "Not Secure" warning will appear because of the self-signed certificate.*
   - **Remote Access:** The Docker images are pre-configured to work on any domain. Simply access the server via its IP or domain name (e.g., `https://192.168.1.50:14443`).
     - *Note: The domain may need to be added to `ALLOWED_ORIGINS` in `.env` if CORS issues are encountered.*

### Using Local LLMs (Ollama)

If running Ollama on the same machine as the Nojoin Docker containers, the special Docker host address must be used:

- **API URL:** `http://host.docker.internal:11434`
- Do **not** use `localhost` or `127.0.0.1` as the container cannot see the host's localhost.

### Running the Companion App

1. Go to the [Releases](https://github.com/Valtora/Nojoin/releases) page.
2. Download the Windows installer (`Nojoin_X.Y.Z_windows.exe`). There is also a portable binary available if preferred.
3. Run the installer. The application will appear in the system tray.
4. The web app also has a 'Download Companion App' button that will direct to the releases page.

*Note: For developers, the app can be built from source by navigating to the `companion` directory and running `cargo build --release` on Windows.*

> **🤝 Contributions Welcomed:** Contributions are welcomed to help build macOS and Linux versions of the companion app. The Windows version uses standard Rust audio libraries (cpal) that have cross-platform support for Linux so I will focus on this first.
> MacOS is trickier due to the need to use ScreenCaptureKit for system audio capture. If interested in contributing, please check the [Contributing Guide](CONTRIBUTING.md) or open an issue to discuss.

## ❓ Troubleshooting

<details>
<summary><strong>Transcription is slow / GPU not being used</strong></summary>

Ensure the GPU has been passed to the container correctly.

1. Check if `nvidia-smi` works on the host.
2. Verify the `docker-compose.yml` has the `deploy: resources: reservations: devices` section uncommented.
3. If on Windows, ensure the latest WSL2 drivers are installed.

</details>

<details>
<summary><strong>Cannot access Nojoin from another computer</strong></summary>

1. Check firewall settings on the host machine (port 14443).
2. Ensure `ALLOWED_ORIGINS` in the `.env` file includes the server's IP or domain (e.g., `https://example.yourdomain.com:14443`).

</details>

<details>
<summary><strong>Ollama connection failed</strong></summary>

If running Ollama on the host, use `http://host.docker.internal:11434` instead of `localhost`. Docker containers cannot see `localhost` of the host directly.

</details>

## 🗺️ Roadmap

- [x] **Windows Support** (Stable)
- [ ] **macOS & Linux Support** (Contributions Welcome)
  - Community-driven development for companion app.
    - Windows implementation can serve as a reference.
- [ ] **Real-time Transcription** (Target: Q2 2026)
  - Live transcript generation during recording.
  - Instant feedback loop.
  - Real-time suggestions and notes.

## 🤝 Contributing

Contributions from the community are welcome! Whether it's fixing bugs, improving documentation, or adding new features, help is appreciated.

**Important:** To ensure Nojoin can remain sustainable and offer a commercial SaaS version, by submitting a Pull Request, you agree to the **Contributor License Agreement (CLA)**. This grants Valtora the right to use your contributions in the commercial version of the platform while ensuring the Community Edition remains open source forever.

Please see the [Contributing Guide](CONTRIBUTING.md) for details on how to get started.

## 📦 Editions

- **Community Edition:** The source code in this repository. Free, open-source (AGPLv3), and self-hosted. Ideal for individuals, developers, and privacy-focused users who want full control over their data.

- **Nojoin Cloud:** A fully managed, hosted version of Nojoin allowing users to take advantage of powerful GPUs and pay a monthly subscription based on tiered usage. *(Coming Late 2026)*

## 📃 Licence

The **Community Edition** of Nojoin is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.
This ensures that the software remains free and open-source, and that any modifications made to the code (even if run as a service) must be shared back with the community.

The **Nojoin Cloud (SaaS)** edition is a proprietary, closed-source service provided by Valtora.

### Third-Party Components

This project utilizes the following third-party models and libraries:

- **Pyannote Audio** models (`pyannote/speaker-diarization-community-1` and `pyannote/wespeaker-voxceleb-resnet34-LM`) by [Pyannote](https://www.pyannote.ai/). Licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
- **OpenAI Whisper** by OpenAI. Licensed under the [MIT License](https://github.com/openai/whisper/blob/main/LICENSE).

## ⚖️ Legal

Please review the [Legal Disclaimer](LEGAL.md) regarding the recording of conversations and compliance with local laws.

## ☕ Buy Me a Coffee

If Nojoin is useful, please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support Nojoin.
