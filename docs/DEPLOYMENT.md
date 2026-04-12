# Nojoin Deployment & Configuration Guide

## Deployment

### Hardware Requirements

- **Recommended:** Linux or Windows system with NVIDIA GPU (CUDA 12.x support).
- **Minimum:** 8GB VRAM for optimal performance (Whisper Turbo + Pyannote).
- **macOS Hosting:** Hosting the **backend** on macOS via Docker is **not recommended**. Docker on macOS cannot pass through the Apple Silicon GPU (Metal) to containers. This forces the system to run in CPU-only mode, which is significantly slower for transcription and diarization.
  - _Note:_ The **Companion App** is currently **Windows only**. macOS and Linux are **not** supported.

### Server Stack

- **Language:** Python 3.11+ (FastAPI)
- **Task Queue:** Celery with Redis
- **Database:** PostgreSQL 18
- **Container Runtime:** Docker (with NVIDIA Container Toolkit support)

### Deployment Method

- **Docker Compose:** The primary deployment method orchestrating API, Worker, DB, Redis, and Web Frontend containers.
- **Container Registry:** Images are automatically built and pushed to GHCR (`ghcr.io/valtora/nojoin-*`).
  - **Pull-First:** `docker compose up -d` pulls the latest pre-built images by default.
  - **Build-Local:** `docker compose build && docker compose up -d --wait` provides a more reliable local-source startup path than a single detached `up --build` invocation.
- **Hardware Support:**
  - **NVIDIA GPU (Default):** The `docker-compose.example.yml` (copied to `docker-compose.yml`) is configured for GPU inference by default.
  - **CPU-Only (Optional):** CPU support is enabled by commenting out the `deploy` section in `docker-compose.yml` (after copying from example).

### Containerization Standards

- **Registry:** GitHub Container Registry (GHCR).
- **CI/CD:** The unified GitHub Actions workflow (`release.yml`) builds and pushes Docker images AND compiles the Companion App binaries on every release tag (`v*`). Version numbers are automatically synchronized across all components.
- **Base Images:** Optimized, pre-built images (e.g., `pytorch/pytorch`) are used.
- **Context Management:** `.dockerignore` excludes build artifacts.
- **Dependency Optimization:** `requirements.txt` is filtered during build to prevent redundant installations.

## Configuration Management

### Unified Strategy

Configuration is split between system-wide infrastructure settings and user-specific preferences.

- **System Config:** Stored in `data/config.json` (Server) and `config.json` (Companion). Includes infrastructure URLs, device paths, and hardware settings.
- **User Settings:** Stored in the PostgreSQL database per user. Includes UI themes, API keys, model preferences, and AI settings.

### Initial Setup

- **Setup Wizard:** Collects critical user settings (LLM Provider, API Keys, HuggingFace Token) during the creation of the first admin account.
  - Can be pre-filled via Environment Variables (e.g., `HF_TOKEN`, `GEMINI_API_KEY`) to streamline deployment.
  - **Privacy Note:** To deploy Nojoin in a pure 'Private' mode where no data leaves your server, explicitly configure it to use Ollama as the LLM provider. Using other remote providers (OpenAI, Anthropic, Gemini) will send meeting transcripts to these external services for processing.
- **Database Initialization:** Automatically handles schema creation and migrations on startup.

### Companion Config

- **Localhost Enforcement:** Defaults to `localhost` but supports configurable `api_host`.
- **Auto-Configuration:** The "Connect to Companion" flow in the Web Client automatically configures the Companion App.
- **Manual Configuration:** The "Settings" window in the System Tray allows manual entry of API Host and Port.
- **Config Preservation:** The Windows installer preserves `config.json` during updates.

### Security

- **SSL/TLS:** All communication is encrypted via HTTPS using Nginx.
- **Automatic SSL Generation:** Self-signed certificates are generated on startup if missing.
- **HTTPS Enforcement:** HTTP requests to port 14141 are redirected to HTTPS on port 14443.
- **Authentication:** Browser sessions use Secure HttpOnly cookies. Explicit Bearer tokens are reserved for API clients and scoped companion recording flows.
- **Public Auth Throttling:** The API rate limits login, invitation validation, and public registration requests to reduce brute-force abuse.
- **JWT Secret Key:** Automatically generated on first startup and persisted to `data/.secret_key`. Can be overridden by the `SECRET_KEY` environment variable.
- **Internal Services Security:** Redis is protected by a password (configured via `REDIS_PASSWORD`) and its port is restricted to the internal Docker network. Host connections (if enabled) are bound exclusively to localhost to prevent external unauthorized access.
- **CORS, Security & Remote Access:**
  - **CORS & Trusted Public Origin:** Configurable via the `ALLOWED_ORIGINS` environment variable. Together with `web_app_url`, this defines the trusted public origin used for generated invitation links and TLS fingerprint resolution instead of request Host headers.
  - **Remote Access:** Supports deployment behind reverse proxies by configuring `NEXT_PUBLIC_API_URL` and `ALLOWED_ORIGINS`. For deployments exposed over a publically accessible URL, it is strongly recommended to use a VPN or a secure reverse proxy to mitigate potential security risks.

### Environment Variables

The following environment variables can be used to pre-configure the system (e.g. in `.env` or `docker-compose.yml`), useful for automated deployments.

- `REDIS_PASSWORD`: Password used to secure the Redis instance and authenticate clients.
- `HF_TOKEN`: Hugging Face User Access Token (Read).
- `LLM_PROVIDER`: Default LLM Provider (`gemini`, `openai`, `anthropic`, `ollama`).
- `GEMINI_API_KEY`: Google Gemini API Key.
- `OPENAI_API_KEY`: OpenAI API Key.
- `ANTHROPIC_API_KEY`: Anthropic API Key.
- `OLLAMA_API_URL`: Ollama API URL (default: `http://host.docker.internal:11434`).
- `GOOGLE_OAUTH_CLIENT_ID`: Google OAuth client ID for Calendar sign-in.
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google OAuth client secret for Calendar sign-in.
- `MICROSOFT_OAUTH_CLIENT_ID`: Microsoft OAuth client ID for Calendar sign-in.
- `MICROSOFT_OAUTH_CLIENT_SECRET`: Microsoft OAuth client secret for Calendar sign-in.
- `MICROSOFT_OAUTH_TENANT_ID`: Microsoft tenant ID. Use `common` only when the app registration is multi-tenant.

## Calendar OAuth Setup

Nojoin uses the providers' own OAuth consent screens for end-user calendar access. Users click `Connect Gmail Calendar` or `Connect Outlook Calendar`, are redirected to Google or Microsoft, sign in, grant permission, and are then sent back to Nojoin already connected.

The only manual credential entry is the one-time installation registration of the OAuth application itself. You can provide those credentials in one of two ways:

1. Preferred: open `Settings > Admin > Calendar` in Nojoin and save the provider client ID and client secret there.
2. Alternative: define the OAuth variables in `.env` and restart the stack so the API and worker containers receive them.

### Step 1: Decide the Public Origin

Pick the exact browser origin your users will use to access Nojoin. This must match the provider registration exactly.

- Local example: `https://localhost:14443`
- Public example behind a reverse proxy: `https://nojoin.example.com`

If you are not using the default local setup, ensure `ALLOWED_ORIGINS` includes that exact origin. If you have also set `web_app_url` in Nojoin system configuration, keep it identical to the public origin you register with the provider.

### Step 2: Register the Google OAuth App

1. Open Google Cloud Console.
1. Create or select a project for Nojoin.
1. Enable the Google Calendar API for that project.
1. Open `APIs & Services > OAuth consent screen`.
1. Choose the appropriate user type and configure the app name, support email, and developer contact email.
1. Add the scopes needed for Nojoin's read-only calendar flow: `openid`, `email`, `profile`, and `https://www.googleapis.com/auth/calendar.readonly`.
1. If the app is still in testing mode, add the Gmail accounts you want to use as test users.
1. Open `APIs & Services > Credentials` and create an `OAuth client ID`.
1. Choose `Web application`.
1. Add this authorised redirect URI: `<public-origin>/api/v1/calendar/oauth/google/callback`.
1. Save the generated client ID and client secret into Nojoin Admin Calendar settings or into `.env`.

### Step 3: Register the Microsoft OAuth App

Important licensing note:

- Basic app registration for the Microsoft identity platform is not an Entra P1/P2-only feature.
- Microsoft’s current registration quickstart requires an Azure account with an active subscription. A free Azure account is sufficient for this use case.
- You still need a tenant to hold the app registration. If the Entra admin centre tells you tenant creation is restricted for your current account, Microsoft’s guidance is to sign up for a free Azure account and use the default tenant created there.

1. Open Microsoft Entra admin centre.
1. Go to `Identity > Applications > App registrations` and create a new registration for Nojoin.
1. Choose the supported account type based on the sign-in scope you want: `Accounts in any organisational directory and personal Microsoft accounts` if Nojoin should support both Outlook.com and Microsoft 365 accounts, or `Accounts in this organisational directory only` if access should stay limited to your own tenant.
1. In the registration form, add the redirect URI as a `Web` platform redirect URI: `<public-origin>/api/v1/calendar/oauth/microsoft/callback`.
1. Finish creating the app, then open its `Overview` page. Copy the `Application (client) ID` into Nojoin's `Application (client) ID` field. If you chose a single-tenant app, also note the tenant ID shown in Entra and use that exact tenant ID in Nojoin instead of `common`.
1. Open `Certificates & secrets` and create a new client secret. Copy the secret `Value` immediately because Microsoft only shows it once, paste that value into Nojoin's `Client Secret Value` field, and ignore the separate `Secret ID` field in Entra because it is metadata rather than the secret to store in Nojoin.
1. Open `API permissions` and add delegated Microsoft Graph permissions for `openid`, `profile`, `email`, `offline_access`, `User.Read`, and `Calendars.Read`.
1. Review consent policy for your tenant. If your organisation blocks end-user consent, users will see an admin approval message during the Outlook connect flow. In that case, a tenant administrator must grant consent in Entra before standard users can connect their calendars.
1. Save the Microsoft values into Nojoin Admin Calendar settings or `.env`: `Application (client) ID`, `Client Secret Value`, and `Tenant ID or common`.

Tenant selection rules in Nojoin:

- Use `common` only when the app registration allows both organisational directories and personal Microsoft accounts.
- If the app remains single-tenant, `common` will fail with `AADSTS50194`.
- Use your explicit tenant ID when the app is intentionally single-tenant or when you only want users from one directory to connect.

If you only have a personal Outlook/Hotmail account today and no tenant you can administer, the practical route is:

1. Create a free Azure account.
2. Use the default tenant that Azure creates for you.
3. Register the Nojoin app in that tenant.
4. Choose the supported account type that includes both organisational directories and personal Microsoft accounts.
5. Use tenant `common` in Nojoin if you want both Outlook.com and Microsoft 365 accounts to work.

If you prefer to keep the app single-tenant, enter your directory tenant ID in Nojoin instead of `common`. That will allow work/school accounts from that tenant only, and personal Microsoft accounts will not work.

### Step 4: Configure Nojoin

Use one of these paths:

1. Admin UI: sign in as an Owner or Admin, open `Settings > Admin > Calendar`, enter the Google client ID and secret, enter the Microsoft `Application (client) ID`, `Client Secret Value`, and `Tenant ID or common`, verify the Microsoft field labels match the labels shown in Entra, and save.
1. Environment variables: add the OAuth variables to `.env`, then restart the stack, for example with `docker compose up -d --build api worker frontend`.

### Step 5: Verify the End-User Flow

1. Sign in as a normal user.
2. Open `Settings > Account`.
3. Click `Connect Gmail Calendar` or `Connect Outlook Calendar`.
4. Confirm you are redirected to Google or Microsoft.
5. Sign in, click through the consent screen, and allow read-only calendar access.
6. Confirm you are returned to Nojoin and the account appears under Calendar Connections.
7. Select one or more calendars for sync.
8. Confirm the dashboard calendar shows orange day dots, agenda entries, and the `Next event in ...` helper text.

### Notes

- For the smoothest provider OAuth experience, use a stable HTTPS origin. A public domain with a trusted certificate is preferred for long-term deployment.
- The end-user flow is fully automated after registration. Users do not enter client IDs, client secrets, or tenant IDs.
- Google testing-mode apps require every test account to be explicitly added until you publish the consent screen.
