# Nojoin Getting Started

The shortest path from a fresh checkout to your first processed meeting. [DEPLOYMENT.md](DEPLOYMENT.md) has the reference detail for hosting and configuration.

## Before You Begin

- Docker with Compose.
- An NVIDIA GPU is strongly recommended for transcription and speaker separation: GTX 16 or RTX 20 series or newer, on driver 580 or newer ([GPU requirements](DEPLOYMENT.md#gpu-requirements)). CPU-only works, more slowly.
- A browser that can record: Chrome on Windows, Linux or macOS, or another Chromium-family browser on Windows or Linux, for shared audio; Chrome on Android or iOS for the microphone only. [CAPTURE.md](CAPTURE.md#supported-environments) has the full list. Other browsers can review recordings but cannot record.

## 1. Start the Stack

1. Clone the repository and create your deployment files from the tracked examples.

   ```bash
   git clone https://github.com/Valtora/Nojoin
   cd Nojoin
   cp docker-compose.example.yml docker-compose.yml
   cp .env.example .env
   ```

2. In `.env`, replace the example values of `FIRST_RUN_PASSWORD`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD`, and set a long random `DATA_ENCRYPTION_KEY`. Setup refuses the example `FIRST_RUN_PASSWORD`. Set the database password now, because PostgreSQL keeps the one it first started with, and keep the encryption key unchanged from then on: it encrypts stored calendar and AI subscription credentials.
3. Without a compatible NVIDIA GPU, remove the `deploy` section under `worker-gpu` in `docker-compose.yml` ([CPU-Only Deployment](DEPLOYMENT.md#cpu-only-deployment)).
4. Start Nojoin.

   ```bash
   docker compose up -d
   ```

To work on Nojoin's source instead, use [DEVELOPMENT.md](DEVELOPMENT.md).

## 2. Open the Setup Wizard

Open `https://localhost:14443/setup`. Nojoin uses a self-signed certificate by default, so the browser shows a certificate warning on first visit.

The sign-in page does not link to the wizard, so browse to `/setup` directly. Until setup is complete, the API startup log also prints this address.

## 3. Complete the First-Run Wizard

Enter your `FIRST_RUN_PASSWORD` to open the wizard. The example value from `.env.example` is refused with a message of its own: set your own value in `.env`, run `docker compose up -d` and enter the new one. Every other failed attempt shows the same generic message. If you are sure the password is right, check that `FIRST_RUN_PASSWORD` is set and that you restarted the stack after setting it; the API log gives the specific reason.

The wizard then has five steps:

1. **Terms.** Accept the legal disclaimer. **Share anonymous usage diagnostics** is ticked by default: it sends counts and settings every six hours, never your recordings, transcripts, notes, names or keys. Separately, Nojoin sends a daily heartbeat with a random install ID and the version, which has no switch in the app; `NOJOIN_TELEMETRY_ENABLED=false` in `.env` stops both. You can change diagnostics later in **Settings > Privacy**; [TELEMETRY.md](TELEMETRY.md) has the details.
2. **Transcription.** Choose the transcription model. Turbo, the default, suits a server with an NVIDIA GPU; Small or Base is far faster on CPU only. You can change it later in **Settings > Transcription**. The step also reports whether speaker separation can run, which needs either an `HF_TOKEN` or Pyannote assets already on the server.
3. **Account.** Create the Owner account, and choose whether to include a sample meeting. Submitting signs you in and starts downloading the transcription and speaker models in the background; nothing is downloaded before this. You cannot step back past this point, and reloading the page from here on returns you to the step you were on.
4. **AI.** Choose how AI runs (below).
5. **Finish.** A summary, a check that this browser and address can record, and the progress of the model downloads.

### Choosing an AI route

- **This server's AI provider** uses the provider credential in the server's `.env` (`LLM_PROVIDER` and its key, or `OLLAMA_API_URL` for Ollama). With `LLM_PROVIDER` empty, a single key in `.env` selects its own provider. The wizard validates it and lets you pick the default model. Every account on the server shares it.
- **My own Claude or ChatGPT subscription** connects your Claude Pro or Max, or ChatGPT Plus or Pro, plan with no API key. This is per user: each account chooses its own. See [Your own subscription (CLI OAuth)](USAGE.md#your-own-subscription-cli-oauth).
- **Decide later.** Recording, transcription, speaker separation, the speaker library, search, tags, tasks, documents, calendar sync and the sample meeting all work without AI. Generated notes and titles, meeting chat, automatic speaker naming and Meeting Edge wait until you set AI up, and can then run on meetings recorded before that.

You can change the route at any time in **Settings > Your AI**. If you add or fix a provider key in `.env` while the wizard is open, restart the stack and select **Check config again**; the wizard keeps your progress. To pre-fill settings through environment variables, see [Configure .env](DEPLOYMENT.md#configure-env).

## 4. Prepare Browser Capture

Open Nojoin in a browser that can record. To use a microphone other than the system default, choose it in **Settings > Recording**. On desktop, open the meeting in a browser tab if you can: tab sharing is the most reliable way to capture meeting audio. [CAPTURE.md](CAPTURE.md) covers what to share, pause and resume, and capture troubleshooting.

## 5. Make Your First Recording

1. On the dashboard, select **Start Meeting** in the **Meet Now** card.
2. On desktop, pick the meeting tab, window or screen in the browser's share picker, turn on its audio option if offered, and allow microphone access. On a phone, allow microphone access and keep the phone awake. Nojoin then opens the recording's page.
3. Speak briefly and check that the live waveform moves.
4. Stop the recording. The page shows processing progress until transcription and speaker separation finish.

With AI configured, Nojoin then names the speakers it can infer, titles the meeting and writes notes; you can rename any speaker afterwards. Without AI, the meeting still completes and its transcript is ready to review.

## 6. Recommended Next Steps

- [USAGE.md](USAGE.md): the dashboard, recordings, notes, search and speakers.
- [CALENDAR.md](CALENDAR.md): Google or Outlook calendar integration.
- [ADMIN.md](ADMIN.md): users, invitations and system settings.
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md): read before relying on backups.
- [Remote Access and Trusted Public Origin](DEPLOYMENT.md#remote-access-and-trusted-public-origin): read before opening Nojoin from another device. Live capture needs HTTPS, which for a remote address means a reverse proxy.
