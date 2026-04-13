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
- `WEB_APP_URL`: Exact public browser origin used for generated OAuth callbacks, invite links, and other public URLs.
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

If you are not using the default local setup, set `WEB_APP_URL` to that exact origin and ensure `ALLOWED_ORIGINS` also includes it. If you have also set `web_app_url` in Nojoin system configuration, keep it identical to the public origin you register with the provider.

### Step 2: Register the Google OAuth App

1. Open Google Cloud Console and create or select the project you want to use for Nojoin.
1. Open `APIs & Services > Enabled APIs & services`, choose `+ Enable APIs and services`, search for `Google Calendar API`, and enable it.
1. Open `APIs & Services > OAuth consent screen`.
1. Choose the correct audience for your install.
1. Enter the app name, support email, and developer contact email.
1. Add the scopes used by Nojoin's read-only calendar integration: `openid`, `email`, `profile`, and `https://www.googleapis.com/auth/calendar.readonly`.
1. If the app remains in testing mode, add every Google account that should be allowed to connect as a test user.
1. Open `APIs & Services > Credentials` and choose `Create credentials > OAuth client ID`.
1. Select `Web application`.
1. Add this authorised redirect URI exactly: `<public-origin>/api/v1/calendar/oauth/google/callback`.
1. Save the client.
1. Copy the generated `Client ID` and `Client secret` into Nojoin `Settings > Admin > Calendar`, or store them in `.env` as `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.

### Step 3: Register the Microsoft OAuth App

Important licensing note:

- Basic app registration for the Microsoft identity platform is not an Entra P1/P2-only feature.
- Microsoft’s current registration quickstart requires an Azure account with an active subscription. A free Azure account is sufficient for this use case.
- You still need a tenant to hold the app registration. If the Entra admin centre tells you tenant creation is restricted for your current account, Microsoft’s guidance is to sign up for a free Azure account and use the default tenant created there.

1. Open Microsoft Entra admin centre.
1. Go to `Identity > Applications > App registrations` and choose `New registration`.
1. Enter a name such as `Nojoin` or `Nojoin - Localhost`.
1. Choose the supported account type that matches your intended users.
1. Use `Accounts in any organisational directory and personal Microsoft accounts` if you want both Microsoft 365 work/school accounts and personal Outlook.com or Hotmail accounts to be able to connect.
1. Use `Accounts in this organisational directory only` only if you deliberately want a single-tenant work/school-only app.
1. Under `Redirect URI`, choose platform type `Web` and enter `<public-origin>/api/v1/calendar/oauth/microsoft/callback`.
1. Finish creating the app.
1. On the new app's `Overview` page, copy `Application (client) ID` into Nojoin's `Application (client) ID` field.
1. If the app is single-tenant, also copy `Directory (tenant) ID` and use that exact value in Nojoin's `Tenant ID or common` field.
1. If the app is multi-tenant and should support personal Microsoft accounts as well, enter `common` in Nojoin's `Tenant ID or common` field instead of the directory GUID.
1. Open `Authentication`.
1. Confirm the redirect URI is present under the `Web` platform.
1. Confirm the supported account type still matches the sign-in scope you want.
1. Open `API permissions`.
1. Add delegated Microsoft Graph permissions for `openid`, `profile`, `email`, `offline_access`, `User.Read`, and `Calendars.Read`.
1. Do not add application permissions such as application `Calendars.Read` for Nojoin's current user-consent flow. They are not required for this feature and only add confusion.
1. If your tenant restricts user consent, a tenant administrator must grant consent in Entra before normal users can complete the Outlook connect flow.
1. Open `Certificates & secrets` and create a new client secret.
1. Copy the secret `Value` immediately and store that in Nojoin as `Client Secret Value`.
1. Do not copy `Secret ID` into Nojoin. `Secret ID` is only metadata and will not work as the client secret.
1. Save the Microsoft values into Nojoin Admin Calendar settings or `.env`: `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET`, and `MICROSOFT_OAUTH_TENANT_ID`.

Tenant selection rules in Nojoin:

- Use `common` only when the app registration allows both organisational directories and personal Microsoft accounts.
- If the app remains single-tenant, `common` will fail with `AADSTS50194`.
- Use your explicit tenant ID when the app is intentionally single-tenant or when you only want users from one directory to connect.
- If you allow personal Microsoft accounts and leave Nojoin pointed at a specific tenant GUID, Microsoft sign-in may succeed but the first calendar read can still fail. In that case, switch Nojoin back to `common`, save, disconnect the failed Outlook calendar connection, and reconnect it.

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
1. Environment variables: add `WEB_APP_URL` plus the OAuth variables to `.env`, then restart the stack, for example with `docker compose up -d --build api worker frontend`.

If you change the Microsoft tenant mode later, for example from a tenant GUID to `common`, disconnect any previously failed Outlook calendar connections and reconnect them so Nojoin requests a fresh token under the corrected tenant path.

### Step 5: Verify the End-User Flow

1. Sign in as a normal user.
2. Open `Settings > Account`.
3. Click `Connect Gmail Calendar` or `Connect Outlook Calendar`.
4. Confirm you are redirected to Google or Microsoft.
5. Sign in, click through the consent screen, and allow read-only calendar access.
6. Confirm you are returned to Nojoin and the account appears under Calendar Connections.
7. Select one or more calendars for sync.
8. Confirm the dashboard calendar shows orange day dots, agenda entries, the `Next event in ...` helper text, and where present a clickable meeting link or location on agenda cards.

### Microsoft Troubleshooting

- `AADSTS50194` after using `common`: the app registration is still single-tenant. Either switch the app to a multi-tenant account type or enter the exact tenant ID in Nojoin instead of `common`.
- Sign-in succeeds but the Outlook calendar still does not connect and the API logs show `GET /me/calendars` failing: if the app is meant to support personal Microsoft accounts or mixed account types, make sure Nojoin's tenant field is set to `common`, then disconnect and reconnect the Outlook calendar connection.
- Microsoft sign-in page appears but users see an admin approval message: the delegated permissions exist, but tenant policy blocks user consent. Have an Entra administrator grant consent for the app.
- Token exchange fails with `invalid_client`: verify that Nojoin stores the client secret `Value`, not the `Secret ID`.
- Only work or school accounts should connect: keep the app single-tenant and enter that tenant's `Directory (tenant) ID` in Nojoin instead of `common`.

### Notes

- For the smoothest provider OAuth experience, use a stable HTTPS origin. A public domain with a trusted certificate is preferred for long-term deployment.
- The end-user flow is fully automated after registration. Users do not enter client IDs, client secrets, or tenant IDs.
- Google testing-mode apps require every test account to be explicitly added until you publish the consent screen.
