# Nojoin Calendar Guide

Nojoin supports Google Calendar and Microsoft Outlook calendar integration for dashboard month and agenda views.

This guide covers both sides of the feature:

- Admin and owner setup of the installation OAuth credentials.
- End-user connection of their own calendar accounts.
- Dashboard behaviour, colours, links, and troubleshooting.

## Overview

Calendar integration has two distinct layers:

1. The installation must be configured with Google and/or Microsoft OAuth credentials.
2. Individual users then connect their own accounts and choose which calendars to sync.

Users do not enter OAuth client IDs, client secrets, or tenant IDs themselves.

## Before You Begin

- Use a stable HTTPS browser origin whenever possible.
- Set `WEB_APP_URL` to the exact public browser origin used to access Nojoin.
- Set a stable `DATA_ENCRYPTION_KEY` before storing provider secrets or connecting user calendars.
- If you deploy behind a reverse proxy, make sure the browser origin and provider callback origin are identical.

For the full hosting context, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Admin Configuration Options

You can provide the installation OAuth credentials in either of these ways:

1. Preferred: sign in as an Owner or Admin and save them in **Settings > Administration > Calendar providers**.
2. Alternative: set the matching environment variables and restart the stack.

Supported environment variables:

- `WEB_APP_URL`
- `DATA_ENCRYPTION_KEY`
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `MICROSOFT_OAUTH_CLIENT_ID`
- `MICROSOFT_OAUTH_CLIENT_SECRET`
- `MICROSOFT_OAUTH_TENANT_ID`

`DATA_ENCRYPTION_KEY` is not an OAuth credential, but it is what keeps stored calendar provider secrets and user calendar tokens readable across upgrades and infrastructure changes. Earlier `v0.8.1` deployments could rely only on the generated key file, which was an operational oversight for installations where the app data directory and database might be managed separately.

## End-User Connection Flow

Once the installation credentials exist:

1. Open **Settings > Personal** and go to **Calendar Connections**.
2. Choose **Connect Gmail Calendar** or **Connect Outlook Calendar**.
3. Complete the provider sign-in and consent flow.
4. Return to Nojoin and choose which calendars to sync.
5. Optionally apply per-calendar colour overrides.

Connected calendars then feed the dashboard month view, agenda view, and next-event surface.

## Dashboard Behaviour

When calendar sync is configured:

- The dashboard month view shows dots on days with synced events.
- The agenda view focuses on future events rather than historical clutter.
- Selecting a day in month view opens a day agenda, and selecting today shows a live now marker against timed events.
- The `Today` action jumps back to the current month and date context.
- Per-calendar colours can be used so different sources remain visually distinct.
- Event times render in the user's configured Nojoin timezone.
- Meeting links and supported online meeting URLs can be surfaced directly from agenda items.

When no calendar is connected, the dashboard deliberately shows an honest empty state rather than mock data.

## Google OAuth Setup

1. Open Google Cloud Console and create or select the project you want to use.
2. Enable the **Google Calendar API**.
3. Configure the OAuth consent screen.
4. Add the scopes used by Nojoin:
   - `openid`
   - `email`
   - `profile`
   - `https://www.googleapis.com/auth/calendar.readonly`
5. If the app remains in testing mode, add every Google account that needs access as a test user.
6. Create an OAuth client ID of type **Web application**.
7. Add this redirect URI exactly:

   ```text
   <public-origin>/api/v1/calendar/oauth/google/callback
   ```

8. Save the client.
9. Enter the generated client ID and client secret into Nojoin Admin Calendar settings or the matching environment variables.

## Microsoft OAuth Setup

Microsoft setup is slightly more sensitive because tenant choice affects which accounts can sign in.

### Supported Account Type

- In the current Entra registration UI, use **Any Entra ID Tenant + Personal Microsoft Accounts** if you want both Microsoft 365 work or school accounts and personal Outlook.com or Hotmail accounts.
- Older Microsoft documentation may describe this same choice using different wording, but in the current interface the label to look for is **Any Entra ID Tenant + Personal Microsoft Accounts**.
- Use **Accounts in this organisational directory only** only if you deliberately want a single-tenant work or school deployment.

### Registration Steps

1. Open Microsoft Entra admin centre.
2. Go to **Identity > Applications > App registrations**.
3. Create a new registration.
4. Choose the supported account type that matches your intended users. For mixed Microsoft 365 and personal Microsoft account support, select **Any Entra ID Tenant + Personal Microsoft Accounts**.
5. Add this redirect URI as a **Web** platform redirect:

   ```text
   <public-origin>/api/v1/calendar/oauth/microsoft/callback
   ```

6. Copy the **Application (client) ID**.
7. If the app is single-tenant, also copy the **Directory (tenant) ID**.
8. Open **API permissions** and add delegated permissions for:
   - `openid`
   - `profile`
   - `email`
   - `offline_access`
   - `User.Read`
   - `Calendars.Read`
9. Do not add application permissions for this user-consent flow.
10. Open **Certificates & secrets** and create a client secret.
11. Copy the secret **Value** immediately. Do not use the **Secret ID**.
12. Save the values into Nojoin Admin Calendar settings or the matching environment variables.

### Tenant Selection Rules

- Use `common` only when the app registration supports both organisational directories and personal Microsoft accounts.
- If the app is single-tenant, use the explicit tenant ID instead of `common`.
- If the app is meant to support personal Microsoft accounts but Nojoin is pointed at a specific tenant GUID, sign-in may succeed while the first calendar read still fails. In that case, switch back to `common`, save, disconnect the failed connection, and reconnect it.

## Verification Checklist

After setup:

1. Sign in as a normal user.
2. Connect Google or Microsoft from **Settings > Personal > Calendar Connections**.
3. Approve the provider consent flow.
4. Select one or more calendars.
5. Confirm the dashboard shows calendar markers, agenda items, and next-event data.

## Troubleshooting

### Redirect Origin Is Wrong

- Confirm `WEB_APP_URL` matches the exact browser origin users open.
- Confirm the provider registration uses the same callback origin.

### `AADSTS50194` with Microsoft

Your Microsoft app is still single-tenant while Nojoin is using `common`.

Fix one of these:

- Change the app registration to **Any Entra ID Tenant + Personal Microsoft Accounts**.
- Or set Nojoin to the explicit tenant ID instead of `common`.

### Microsoft Sign-In Works but Calendar Sync Still Fails

If you intend to support personal Microsoft accounts or mixed account types, confirm Nojoin is using `common`, then disconnect and reconnect the Outlook calendar connection.

### `invalid_client` During Token Exchange

Make sure Nojoin stores the client secret **Value**, not the **Secret ID**.

### Admin Approval Message in Microsoft

The delegated permissions exist, but the tenant blocks user consent. A tenant administrator must grant consent.

### Google Testing Mode Blocks a User

If the Google app is still in testing mode, that Google account must be listed as a test user until the consent screen is published.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [ADMIN.md](ADMIN.md)
