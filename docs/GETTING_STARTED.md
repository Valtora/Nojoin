# Nojoin Getting Started

This guide is the shortest path from a fresh checkout to a working Nojoin installation and your first processed meeting.

For deeper hosting and configuration detail, continue to [DEPLOYMENT.md](DEPLOYMENT.md) after this guide.
For detailed Companion install, pairing, reconnect, switching deployments, tray, and troubleshooting guidance, continue to [COMPANION.md](COMPANION.md).

## Before You Begin

- Docker must be installed.
- An NVIDIA GPU is strongly recommended for faster transcription and diarisation, but CPU-only mode is supported.
- The Companion app currently supports Windows only.

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

During setup you will usually want to provide:

- A Hugging Face token for diarisation.
- Your preferred LLM provider.
- Optional API keys for OpenAI, Anthropic, Gemini, or a local Ollama endpoint.

You can also pre-fill much of this through environment variables. See [DEPLOYMENT.md](DEPLOYMENT.md#configure-env).

## 4. Install and Pair the Companion App

1. Download the latest Windows Companion build from the GitHub Releases page.
2. Run the installer or portable build, then launch the Companion.
3. In Nojoin, open `Settings -> Companion App` and choose `Pair This Device`.
4. Let the browser open the local Companion through `nojoin://pair`, then approve the OS-native prompt on this device.
5. Confirm the Companion page shows `Connected` before starting a live recording.

If this machine is already paired to a different backend later, start a fresh pairing request from the target Nojoin site instead. The current backend stays active until the new pairing succeeds.

The browser and Companion must run on the same machine. The Nojoin backend can be remote as long as both can reach the same HTTPS origin. For reconnect, tray guidance, or troubleshooting, use [COMPANION.md](COMPANION.md).

## 5. Make Your First Recording

1. Open the dashboard.
2. Use **Meet Now** to start a recording.
3. Stop the recording when finished.
4. Open the recording in the `/recordings` workspace.
5. Wait for transcription, diarisation, title inference, and note generation to complete.

## 6. Recommended Next Steps

- Read [COMPANION.md](COMPANION.md) for the full Companion install, pairing, reconnect, switching, tray, and troubleshooting guide.
- Read [USAGE.md](USAGE.md) for the dashboard, recordings workspace, notes, search, and speaker workflows.
- Read [CALENDAR.md](CALENDAR.md) if you want Google or Outlook calendar integration.
- Read [ADMIN.md](ADMIN.md) if you will manage users, invitations, or system settings.
- Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on backups in production.
- Read [DEPLOYMENT.md](DEPLOYMENT.md) before exposing Nojoin over a LAN, VPN, or public domain.
