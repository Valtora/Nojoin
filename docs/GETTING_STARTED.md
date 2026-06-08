# Nojoin Getting Started

This guide is the shortest path from a fresh checkout to a working Nojoin installation and your first processed meeting.

For deeper hosting and configuration detail, continue to [DEPLOYMENT.md](DEPLOYMENT.md) after this guide. For capture details and troubleshooting, continue to [CAPTURE.md](CAPTURE.md).

## Before You Begin

- Docker must be installed.
- An NVIDIA GPU is strongly recommended for faster transcription and diarisation, but CPU-only mode is supported.
- Shared-audio live recording requires Chrome on Windows, Linux, or macOS, or Edge, Brave, Arc, or another Chromium-family browser on Windows or Linux. Other Chromium-family browsers on macOS are best-effort.
- Chrome on Android and iOS can start microphone-only live recordings.
- Firefox, Safari, and other mobile browsers can review existing recordings but cannot start live capture.

## 1. Start the Stack

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
4. Set `DATA_ENCRYPTION_KEY` in `.env` if this will be a persistent installation.

5. Start Nojoin.

   ```bash
   docker compose up -d
   ```

For source development workflows, use [DEVELOPMENT.md](DEVELOPMENT.md).

If you do not have an NVIDIA GPU, see [DEPLOYMENT.md](DEPLOYMENT.md) for CPU-only instructions.

`DATA_ENCRYPTION_KEY` prevents future decryptability issues if the app data directory and database do not move together during restores, host migrations, or partial replacements.

## 2. Open the Web App

Open:

```text
https://localhost:14443
```

Nojoin uses a self-signed certificate by default, so your browser will show a certificate warning on first access.

## 3. Complete the First-Run Wizard

The first user becomes the Owner account.

During setup, the system automatically detects configured API keys and Hugging Face tokens from the environment variables (set in `.env` before starting the stack). The setup wizard will:

- Detect active AI provider credentials and Hugging Face tokens.
- Detect whether the bundled local Pyannote speaker models are already present.
- Let you select the default model for your configured AI provider.
- Warn you if no AI provider credentials are configured, explaining the loss of core intelligence features (Meeting Edge, Notes, and Speaker Inference), and allow you to proceed or reload the configuration.

If you skip AI configuration, Nojoin still records, transcribes, and diarises meetings. The automatic AI enhancement step is simply skipped until you configure a provider later by setting environment variables in `.env` and restarting the server.

You can also pre-fill much of this through environment variables. See [DEPLOYMENT.md](DEPLOYMENT.md#configure-env).

## 4. Prepare Browser Capture

1. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio capture, in another supported Chromium-family browser on Windows or Linux, or in Chrome on Android/iOS for microphone-only capture. Other Chromium-family browsers on macOS are best-effort.
2. Open your meeting in a browser tab when possible. Tab sharing is the most reliable way to capture meeting audio.
3. Open **Settings > Capture** if you need to choose a microphone or adjust shared-audio and microphone gain.
4. Keep the Nojoin tab open during live recording.

On desktop, the browser will ask what tab, window, or screen to share when you start recording. Turn on the browser's audio-sharing option in that picker so remote participants are captured. On mobile Chrome, Nojoin records only the phone microphone, so keep the phone close enough to hear the meeting and keep the tab open.

See [CAPTURE.md](CAPTURE.md) for browser-specific guidance, Linux PipeWire notes, pause/resume semantics, and troubleshooting.

## 5. Make Your First Recording

1. Open the dashboard.
2. Use the **Meet Now** card and click **Start Meeting**.
3. On desktop, select the meeting tab, window, or screen in the browser share picker.
4. On desktop, enable shared audio in the picker and allow microphone access if prompted. On mobile Chrome, allow microphone access and keep the phone awake.
5. Speak briefly and confirm the live waveform responds. If AI is configured, Meeting Edge guidance should appear after enough speech accumulates.
6. Stop the recording when finished.
7. Open the recording in the `/recordings` workspace.
8. Wait for transcription and diarisation to complete.
9. If AI is configured, Nojoin then runs one automatic meeting-intelligence pass that can apply unresolved speaker suggestions, a meeting title, and Markdown meeting notes.
10. If AI is not configured, the meeting still completes normally and remains available for transcript review. You can configure AI later before using Generate Notes, meeting chat, or Retry Speaker Inference.

## 6. Recommended Next Steps

- Read [CAPTURE.md](CAPTURE.md) for browser capture setup, resume/discard behavior, and troubleshooting.
- Read [USAGE.md](USAGE.md) for the dashboard, recordings workspace, notes, search, and speaker workflows.
- Read [CALENDAR.md](CALENDAR.md) if you want Google or Outlook calendar integration.
- Read [ADMIN.md](ADMIN.md) if you will manage users, invitations, or system settings.
- Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on backups in production.
- Read [DEPLOYMENT.md](DEPLOYMENT.md) before exposing Nojoin over a LAN, VPN, or public domain.
