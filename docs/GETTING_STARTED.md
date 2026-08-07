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

## 2. Open the Setup Wizard

Open:

```text
https://localhost:14443/setup
```

Nojoin uses a self-signed certificate by default, so your browser will show a certificate warning on first access.

The sign-in page deliberately does not link to the setup wizard, and the server never reveals to visitors whether it has been initialised. On a fresh deployment, browse to `/setup` directly; the API startup log also prints this address until the system is initialised.

## 3. Complete the First-Run Wizard

Unlock the wizard with the `FIRST_RUN_PASSWORD` value from your `.env`. Every unlock failure shows the same generic denial — if you are certain the password is correct, confirm `FIRST_RUN_PASSWORD` is set and the stack was restarted after setting it, and check the API logs for the specific reason.

The wizard runs in five steps after the unlock gate.

1. **Terms.** The legal disclaimer, plus an **anonymous usage data** checkbox, ticked by default. Leaving it ticked sends one anonymous ping every six hours with counts and configuration settings — never your recordings, transcripts, notes, names, or keys. Unticking it means nothing is ever sent. You can change this later in **Settings > Privacy**; see [TELEMETRY.md](TELEMETRY.md).
2. **Transcription.** Choose the Whisper model. Turbo (the default) suits a server with an NVIDIA GPU; Small or Base is far faster on a CPU-only deployment, and you can change it later in **Settings > AI**. This step also reports whether speaker diarisation can run, by detecting either an `HF_TOKEN` in the environment or Pyannote assets already present on the server.
3. **Account.** Create the Owner account. Submitting this step creates the account, signs you in, and queues preparation of the transcription and speaker models in the background, so the download runs while you finish the remaining steps. Everything after this point is authenticated, and the wizard cannot be stepped back past it.
4. **AI.** Choose how AI runs. See below.
5. **Finish.** A summary of what was configured, a check that this browser and origin can actually record, and the remaining model-preparation progress.

### Choosing an AI route

Nojoin supports three routes, and the wizard offers all three rather than assuming a server-side API key:

- **This server's AI provider.** Uses whatever provider credential is present in the server's `.env` (`LLM_PROVIDER` plus the matching key, or `OLLAMA_API_URL` for a local Ollama). The wizard validates the credential, lists the provider's models, and lets you pick the default. Shared by every account on the server.
- **Your own Claude or ChatGPT subscription.** Connect a Claude Pro/Max or ChatGPT Plus/Pro plan directly in the wizard and route AI through it, with no API key anywhere. This is a per-user choice: every other account on the server picks its own. See [Your own subscription (CLI OAuth)](USAGE.md#your-own-subscription-cli-oauth).
- **Decide later.** A supported configuration, not a failure. Recording, transcription, speaker separation and the speaker library, search, tags, tasks, documents, calendar sync, and the sample meeting with its notes all work with no AI provider at all. Generated notes and titles, meeting chat, automatic speaker inference, and Meeting Edge wait until you configure AI, and can then be run against meetings recorded before that.

You can change the route at any time in **Settings > Your AI**. If you add or correct a provider key in `.env` while the wizard is open, restart the stack and use **Check config again**: keep the tab open and the wizard keeps your progress.

You can also pre-fill much of this through environment variables. See [DEPLOYMENT.md](DEPLOYMENT.md#configure-env).

## 4. Prepare Browser Capture

1. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio capture, in another supported Chromium-family browser on Windows or Linux, or in Chrome on Android/iOS for microphone-only capture. Other Chromium-family browsers on macOS are best-effort.
2. Open your meeting in a browser tab when possible. Tab sharing is the most reliable way to capture meeting audio.
3. Open **Settings > Recording** if you need to choose a microphone or adjust shared-audio and microphone gain.
4. Keep the Nojoin tab open during live recording.

On desktop, the browser will ask what tab, window, or screen to share when you start recording. Turn on the browser's audio-sharing or system-audio option in that picker when it is offered so remote participants are captured. If you close the picker with **Cancel**, Nojoin simply returns to the pre-start state. On mobile Chrome, Nojoin records only the phone microphone, so keep the phone close enough to hear the meeting and keep the tab open.

See [CAPTURE.md](CAPTURE.md) for browser-specific guidance, Linux PipeWire notes, pause/resume semantics, and troubleshooting.

## 5. Make Your First Recording

1. Open the dashboard.
2. Use the **Meet Now** card and click **Start Meeting**.
3. On desktop, select the meeting tab, window, or screen in the browser share picker.
4. On desktop, enable the browser's audio-sharing or system-audio option in the picker when it is offered, then allow microphone access if prompted. On mobile Chrome, allow microphone access and keep the phone awake.
5. Speak briefly and confirm the live waveform responds. If AI is configured, Meeting Edge guidance should appear after enough speech accumulates.
6. Stop the recording when finished.
7. Open the recording in the `/recordings` workspace.
8. Wait for transcription and diarisation to complete.
9. If AI is configured, Nojoin then runs one automatic meeting-intelligence pass that can name unresolved speakers, set a meeting title, and write Markdown meeting notes. Inferred speaker names are applied automatically; you can rename any speaker afterwards.
10. If AI is not configured, the meeting still completes normally and remains available for transcript review. Set a route up in **Settings > Your AI** — either your own Claude or ChatGPT subscription, or a provider key in the server's `.env` — before using Generate Notes, meeting chat, or Retry Speaker Inference. Meetings recorded beforehand can be enhanced afterwards.

## 6. Recommended Next Steps

- Read [CAPTURE.md](CAPTURE.md) for browser capture setup, resume/discard behaviour, and troubleshooting.
- Read [USAGE.md](USAGE.md) for the dashboard, recordings workspace, notes, search, and speaker workflows.
- Read [CALENDAR.md](CALENDAR.md) if you want Google or Outlook calendar integration.
- Read [ADMIN.md](ADMIN.md) if you will manage users, invitations, or system settings.
- Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on backups in production.
- Read [Remote Access and Trusted Public Origin](DEPLOYMENT.md#remote-access-and-trusted-public-origin) before reaching Nojoin from another device over a LAN, VPN, tailnet, or public domain. Live capture needs an HTTPS origin, so a remote address requires a reverse proxy rather than only a port change.
