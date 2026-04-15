<!-- markdownlint-disable -->
<div align="center">
    <h1><img src="https://iili.io/fueA2wB.png" alt="Nojoin Logo" height="45" width="45" style="vertical-align: middle; horizontal-align: middle;"/> <span style="color: #F36012;"><br>Nojoin</span></h1>
        <p>
           <strong>A self-hosted meeting intelligence platform</strong>
            </p>
    <p>
        <img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg" alt="License">
        <img src="https://img.shields.io/github/v/release/Valtora/Nojoin" alt="Release">
        <img src="https://github.com/Valtora/Nojoin/actions/workflows/release.yml/badge.svg" alt="Release">
    </p>
</div>

<img width="2548" height="1231" alt="Nojoin screenshot" src="https://github.com/user-attachments/assets/7a0039de-321b-46a3-8d8f-95ebf6b3f1c4" />

<img width="2545" height="1235" alt="sc-dark" src="https://github.com/user-attachments/assets/c5c3e33a-ac36-405e-bf36-64d104a0346e" />

---

## What Is Nojoin?

Nojoin is a self-hosted meeting intelligence platform.

It captures audio through a local Windows Companion app, processes recordings on your own server, and gives you transcripts, speaker separation, meeting notes, search, meeting chat, and a web-based dashboard for day-to-day work.

Nojoin is built for people who want the usefulness of meeting assistants without inviting bots into meetings or defaulting to a SaaS platform for storage and processing.

## Why Nojoin?

- Self-hosted and privacy-first.
- No meeting bot joins your calls.
- Local Whisper transcription and Pyannote diarisation on your own infrastructure.
- Optional cloud LLMs, or fully local AI with Ollama.
- Web dashboard with recordings, calendar context, and a Task List.
- Windows Companion app for system and microphone capture.

## Quick Start

1. Clone the repository.

   ```bash
   git clone https://github.com/Valtora/Nojoin
   cd Nojoin
   ```

2. Create local deployment files from the tracked examples.

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   cp .env.example .env
   ```

3. Set `FIRST_RUN_PASSWORD` in `.env`.
4. Set `DATA_ENCRYPTION_KEY` in `.env` for any persistent deployment.

5. Start Nojoin.

   ```bash
   docker compose up -d
   ```

6. Open the web app.

   ```text
   https://localhost:14443
   ```

7. Complete the first-run wizard.

8. Download and connect the latest Windows Companion build from GitHub Releases.

Notes:

- An NVIDIA GPU is strongly recommended for faster processing.
- CPU-only mode is supported.
- The Companion app currently supports Windows only.
- Set `DATA_ENCRYPTION_KEY` once and keep it stable to avoid losing access to previously encrypted calendar credentials after restores or host changes.
- For remote access, reverse proxy setup, calendar OAuth, updates, and backup guidance, use the documentation below.

## Documentation

- [Documentation Index](docs/README.md)
- [Getting Started](docs/GETTING_STARTED.md)
- [Deployment & Configuration](docs/DEPLOYMENT.md)
- [User Guide](docs/USAGE.md)
- [Calendar Guide](docs/CALENDAR.md)
- [Administration Guide](docs/ADMIN.md)
- [Backup & Restore](docs/BACKUP_RESTORE.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Security Policy](docs/SECURITY.md)
- [Legal Disclaimer](docs/LEGAL.md)

## Project Status

Nojoin is under active development. Back up your instance regularly before upgrading.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and contribution expectations.

## Security

If you discover a vulnerability, follow the [security policy](docs/SECURITY.md).

## Licence

Nojoin is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

## Legal

Recording laws vary by jurisdiction. Review the [legal disclaimer](LEGAL.md) before using Nojoin.

## Support

If Nojoin is useful, please consider [buying me a coffee](https://ko-fi.com/valtorra).
