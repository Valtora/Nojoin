# Nojoin Getting Started

This guide is the shortest path from a fresh checkout to a working Nojoin installation and your first processed meeting.

For deeper hosting and configuration detail, continue to [DEPLOYMENT.md](DEPLOYMENT.md) after this guide.

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

2. Copy the example compose file.

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   ```

3. Start Nojoin.

   ```bash
   docker compose up -d
   ```

If you want to build locally instead of pulling the published images, use:

```bash
docker compose build && docker compose up -d --wait
```

If you do not have an NVIDIA GPU, see [DEPLOYMENT.md](DEPLOYMENT.md) for CPU-only instructions.

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

You can also pre-fill much of this through environment variables. See [DEPLOYMENT.md](DEPLOYMENT.md#environment-variables).

## 4. Install and Connect the Companion App

1. Download the latest Windows Companion build from the GitHub Releases page.
2. Run the installer or portable build.
3. Open Nojoin and use the Companion connection flow.
4. Confirm the app shows as connected before starting a live recording.

The web app can also surface Companion download links from the Updates page and when the Companion is unreachable.

## 5. Make Your First Recording

1. Open the dashboard.
2. Use **Meet Now** to start a recording.
3. Stop the recording when finished.
4. Open the recording in the `/recordings` workspace.
5. Wait for transcription, diarisation, title inference, and note generation to complete.

## 6. Recommended Next Steps

- Read [USAGE.md](USAGE.md) for the dashboard, recordings workspace, notes, search, and speaker workflows.
- Read [CALENDAR.md](CALENDAR.md) if you want Google or Outlook calendar integration.
- Read [ADMIN.md](ADMIN.md) if you will manage users, invitations, or system settings.
- Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on backups in production.
- Read [DEPLOYMENT.md](DEPLOYMENT.md) before exposing Nojoin over a LAN, VPN, or public domain.
