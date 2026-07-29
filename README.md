<!-- markdownlint-disable -->
<div align="center">
    <h1><img src="assets/images/nojoin-mark.svg" alt="Nojoin Logo" height="45" width="45" style="vertical-align: middle; horizontal-align: middle;"/> <span style="color: #F36012;"><br>Nojoin</span></h1>
        <p>
           <strong>Self-Hosted Meeting Transcription and Notes</strong>
            </p>
    <p>
        <img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg" alt="License">
        <img src="https://badgen.net/github/release/Valtora/Nojoin" alt="Latest Version">
        <img src="https://github.com/Valtora/Nojoin/actions/workflows/ci.yml/badge.svg" alt="CI">
        <img src="https://github.com/Valtora/Nojoin/actions/workflows/release.yml/badge.svg" alt="Release">
    </p>
</div>

<img width="1638" height="1237" alt="dark" src="https://github.com/user-attachments/assets/b7fd2d7a-a67a-4394-bcb3-fd63afb928dc" />

---

<img width="1638" height="1237" alt="light" src="https://github.com/user-attachments/assets/ffdd8c20-5a0f-4f30-8cb7-57291883498e" />

---

## What Is Nojoin?

Nojoin is a self-hosted meeting transcription and notes solution.

It captures audio through a supported browser, processes recordings on your own server, and gives you transcripts, speaker separation, meeting notes, search, meeting chat, and a web-based dashboard for day-to-day work.

Nojoin is built for people who want the usefulness of meeting assistants without inviting bots into meetings or defaulting to a SaaS platform for storage and processing.

## Why Nojoin?

- No meeting bot awkwardly joins your calls
- Works across Google Meet, Microsoft Teams, Zoom, etc. without any integration or meeting links
- Built-in transcription and speaker attribution.
- Optional fully local AI with Ollama
- `Meeting Edge` live meeting guidance with questions, missed points, and concept help
- AI-generated meeting notes
- AI-powered Meeting Chat grounded in the transcript, notes, and uploaded documents
- Global speaker library with voiceprints and speaker matching
- Google and Outlook calendar sync with meeting context in the dashboard
- Search across recordings, notes, and documents
- Dedicated Task workspace

## Supported Browsers and OSes

- Chrome for Windows, Linux, and macOS (shared-audio recording)
- Chromium-family browsers on Windows and Linux (shared-audio recording)
- Chromium-family browsers on macOS (best-effort shared-audio recording)
- Chrome (mobile) for Android and iOS (microphone-only recording)

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

3. Set required environment variables in `.env`.
   - `FIRST_RUN_PASSWORD`
   - `DATA_ENCRYPTION_KEY` (for any persistent deployment)
   - And other API keys as needed for your desired features.

4. Start Nojoin.

   ```bash
   docker compose up -d
   ```

5. Open the setup wizard.

   ```text
   https://localhost:14443/setup
   ```

6. Unlock the wizard with your `FIRST_RUN_PASSWORD` and complete the first-run setup. The sign-in page does not link to setup; the wizard is only reachable at `/setup`.

7. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio recording, in another Chromium-family browser on Windows or Linux, or in Chrome on Android/iOS for microphone-only recording, then start a short test meeting. Other Chromium-family browsers on macOS are best-effort. See [docs/CAPTURE.md](docs/CAPTURE.md) for browser capture guidance.

Notes:

- An NVIDIA GPU is strongly recommended for faster processing.
- CPU-only mode is supported but it is much slower.
- Browser capture supports shared-audio recording on Chrome for Windows, Linux, and macOS, Chromium-family browsers on Windows and Linux, and best-effort macOS Chromium-family browsers, plus microphone-only recording on Chrome for Android and iOS.
- Set `DATA_ENCRYPTION_KEY` once and keep it stable to avoid losing access to previously encrypted calendar credentials after restores or host changes.
- To reach Nojoin from another device, a phone, a VPN, or a tailnet, read [Remote Access and Trusted Public Origin](docs/DEPLOYMENT.md#remote-access-and-trusted-public-origin). Live capture requires an HTTPS origin, so remote access means putting a reverse proxy in front of Nojoin rather than only changing a port.
- For calendar OAuth, updates, and backup guidance, use the documentation below.

## Documentation

- [Documentation Index](docs/README.md)
- [Getting Started](docs/GETTING_STARTED.md)
- [Deployment & Configuration](docs/DEPLOYMENT.md)
- [Browser Capture Guide](docs/CAPTURE.md)
- [User Guide](docs/USAGE.md)
- [Calendar Guide](docs/CALENDAR.md)
- [MCP Connector Guide](docs/MCP.md)
- [Administration Guide](docs/ADMIN.md)
- [Backup & Restore](docs/BACKUP_RESTORE.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Security Policy](docs/SECURITY.md)
- [Anonymous Usage Data](docs/TELEMETRY.md)
- [Legal Disclaimer](docs/LEGAL.md)
- [Screenshots](docs/SCREENSHOTS.md)

## Project Status

Nojoin is under active development. Back up your instance regularly before upgrading.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and contribution expectations.

## Anonymous Usage Data

Nojoin sends one anonymous ping a day so it is possible to know how many deployments exist, which versions are in use, and which features are worth investing in. It contains a random install ID, the version, user and recording counts, and which features are switched on. It contains **none of your meeting data** — no audio, transcripts, notes, names, hostnames, or keys — and it is never sold.

It is on by default and can be turned off in **Settings > Privacy**, by setting `NOJOIN_TELEMETRY_ENABLED=false` in `.env`, or by blocking `telemetry.nojoin.co.uk`. On an existing installation nothing is sent until an administrator has seen the notice explaining it.

The receiving service is open source in [telemetry/](telemetry/), so what is stored can be checked rather than taken on trust. See [docs/TELEMETRY.md](docs/TELEMETRY.md) for the full disclosure.

## Security

If you discover a vulnerability, follow the [security policy](docs/SECURITY.md).

## Licence

Nojoin is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

## Legal

Recording laws vary by jurisdiction. Review the [legal disclaimer](docs/LEGAL.md) before using Nojoin.

## Support

If Nojoin is useful, please consider [buying me a coffee](https://ko-fi.com/valtorra).
