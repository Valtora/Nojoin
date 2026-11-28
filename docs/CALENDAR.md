# Nojoin Calendar Guide

Nojoin shows Google Calendar, Microsoft Outlook and Proton Calendar events on the dashboard. For Google and Outlook an Owner or Admin registers an OAuth app for the installation once; each user then connects their own calendar accounts, and never enters client IDs, secrets or tenant IDs. Proton needs no app registration: each user signs in with their own Proton account, through an unofficial route ([Proton Calendar](#proton-calendar-unofficial)).

## Before You Begin

- Set `WEB_APP_URL` to the exact origin users open Nojoin at, ideally a stable HTTPS one. Nojoin builds the OAuth redirect URI and the webhook addresses from it, so behind a reverse proxy it must match the public origin.
- Set a stable `DATA_ENCRYPTION_KEY` before anyone stores provider secrets or connects a calendar. It encrypts both.

[DEPLOYMENT.md](DEPLOYMENT.md) covers both variables.

## Admin Configuration Options

Save the app credentials in either of two places:

1. **Settings > Integrations**, under **Advanced**, in **Google calendar sign-in** or **Microsoft calendar sign-in** (preferred). Each card shows the exact **Redirect URI** to register with the provider. Until a provider is set up, the **Calendars** card offers administrators an **Add app credentials** link to these cards.
2. Environment variables, followed by a restart: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET` and `MICROSOFT_OAUTH_TENANT_ID` (default `common`).

Values saved in Settings take precedence over the environment variables.

## Google OAuth Setup

1. In Google Cloud Console, create or select a project.
2. Enable the **Google Calendar API**.
3. Configure the OAuth consent screen with these scopes:
   - `openid`
   - `email`
   - `profile`
   - `https://www.googleapis.com/auth/calendar.readonly`
4. While the app is in testing mode, add every Google account that will connect as a test user.
5. Create an OAuth client ID of type **Web application** with this redirect URI:

   ```text
   <public-origin>/api/v1/calendar/oauth/google/callback
   ```

6. Enter the client ID and client secret in Nojoin.
7. For [Live Sync](#enabling-live-sync) only: verify the Nojoin domain under the project's **Domain verification** settings, so Google will deliver push notifications.

## Microsoft OAuth Setup

The supported account type decides who can sign in:

- **Any Entra ID Tenant + Personal Microsoft Accounts** allows both Microsoft 365 work or school accounts and personal Outlook.com or Hotmail accounts. Use tenant `common` in Nojoin.
- **Accounts in this organisational directory only** restricts sign-in to one work or school tenant. Use that tenant's ID in Nojoin instead of `common`.

### Registration Steps

1. In the Microsoft Entra admin centre, go to **Identity > Applications > App registrations** and create a new registration.
2. Choose the supported account type for your users.
3. Add this redirect URI as a **Web** platform redirect:

   ```text
   <public-origin>/api/v1/calendar/oauth/microsoft/callback
   ```

4. Copy the **Application (client) ID**, and for a single-tenant app the **Directory (tenant) ID**.
5. Under **API permissions**, add these delegated permissions (no application permissions):
   - `openid`
   - `profile`
   - `email`
   - `offline_access`
   - `User.Read`
   - `Calendars.Read`
6. Under **Certificates & secrets**, create a client secret and copy its **Value** straight away. Do not use the **Secret ID**.
7. Enter the client ID, secret value and tenant in Nojoin.

Live sync needs no extra Microsoft setup: `Calendars.Read` covers change-notification subscriptions.

## Connecting a Calendar

1. Open **Settings > Integrations** and go to **Calendars**.
2. Select **Connect** beside **Google Calendar** or **Outlook Calendar**, then sign in and approve access at the provider.
3. Back in Nojoin, your primary calendar is selected and synced. Tick any other calendars you want, and change a calendar's colour if you like.

Each connected account has **Sync now** and **Disconnect** buttons, and shows its status: **Up to date**, **Syncing**, **Needs attention** or **Reconnect required**.

If the connection fails after you approve access, the **Calendars** card says why, with the provider's own reason, and no account is added. [Connecting Fails After Sign-In](#connecting-fails-after-sign-in) lists the common causes.

To check a new setup, connect an account as a normal user, select a calendar, and confirm the dashboard shows its events.

## Dashboard and Linked Recordings

Selected calendars appear on the dashboard month grid and agenda, each in its own colour, alongside unlinked Nojoin recordings in orange. [USAGE.md](USAGE.md#calendar-surface) describes the dashboard.

When a recording finishes processing, Nojoin links it to the calendar event it clearly overlaps, if exactly one does. To link or change an event yourself, use **Link calendar event** in the recording's header, which lists events within two hours of the recording. A linked recording gives notes and Meeting Edge the event's description and attendees, and appears on the dashboard as its event.

## Sync Behaviour

Nojoin keeps events from 12 months back to 12 months ahead. Every 15 minutes the worker syncs each connected account with at least one selected calendar, fetching only what changed since the last run. When the provider's change cursor cannot be used, for example once a month when the 12-month range moves on, Nojoin fetches the calendar in full and updates each stored event in place, so linked recordings stay linked. An account shows **Reconnect required** when the provider refuses Nojoin's stored sign-in, for example because access was revoked, the password changed or the sign-in expired. Nojoin then skips the account, live sync included, until you reconnect it: select **Connect** again and sign in to the same account.

Deleting an event at the provider, removing a calendar from the account, or disconnecting the account removes those events from Nojoin and unlinks their recordings. An event that has only fallen out of the 12-month range is kept for as long as a recording links to it.

### Enabling Live Sync

Live sync is optional. With it on, the provider sends changes to Nojoin by webhook within seconds. The 15-minute sync keeps running either way.

An Owner or Admin turns it on per provider with the **Live sync** switch in **Settings > Integrations**, under **Advanced**. It requires:

- Nojoin reachable from the public internet over HTTPS at exactly `WEB_APP_URL`. Localhost and LAN-only installations cannot receive webhooks.
- For Google, the webhook domain verified in the Google Cloud project.
- A reverse proxy or tunnel that forwards `POST` requests to these paths and preserves the `Host` header:
  - `<public-origin>/api/v1/calendar/webhooks/google`
  - `<public-origin>/api/v1/calendar/webhooks/microsoft`

An account with live sync running shows a **Live sync** badge in **Settings > Integrations > Calendars**. If Nojoin cannot set up a subscription, it logs the provider's reason and carries on with the 15-minute sync. It tries again after 30 minutes when the provider could not be reached, had a server error or asked Nojoin to slow down, and after 6 hours when the provider refused the request.

## Proton Calendar (Unofficial)

> **Proton Calendar is connected through an unofficial route.** Proton offers no public calendar API. Nojoin talks to Proton's private API, the one Proton's own apps use, following the protocol that the open-source projects carbonate and proton-cal document. Before you connect, know that:
>
> - It can stop working without notice. Proton does not support this use and may change its API at any time.
> - Proton's terms (clause 2.10) allow automated access only while the traffic "remains indistinguishable from the standard client behavior of human users", and let Proton block access at its discretion. Using this connection could lead Proton to restrict your account.
> - To read and write your encrypted calendar, Nojoin keeps a Proton session and the key passphrase derived from your password. That passphrase opens every key on your Proton account, so it could also decrypt your Proton Mail and the rest of your account's encrypted data. Nojoin stores it encrypted with `DATA_ENCRYPTION_KEY` and uses it only for your calendars and, when you or an assistant ask, to send invitations. Anyone who obtains both the database and that key could read your mail.
> - Nojoin stores the events it syncs (titles, descriptions, attendees) in its database without Proton's encryption, as it does for Google and Outlook calendars.
> - Your password, mailbox password and two-factor code are never stored.
> - Disconnecting ends the session at Proton and deletes everything Nojoin kept for it. Changing your Proton password also cuts Nojoin off until you reconnect.

Every user can connect Proton; there is no setting to turn it on. The connection runs through the `nojoin-proton` helper that ships in the `api` and `worker-io` images, so there is nothing for an operator to install.

### Connecting Proton

1. Open **Settings > Integrations > Calendars** and select **Connect** beside **Proton Calendar**.
2. Read the disclosure and tick **I understand**.
3. Enter your Proton address or username and your password.
4. If Proton wants to check that you are a person, select **Open Proton's check**, complete it in the new tab, then select **I've finished the check**.
5. If your account has two-factor sign-in, enter the code from your authenticator app. An account whose only second factor is a security key cannot connect; add an authenticator app in Proton's settings first.
6. If your account uses a separate mailbox password, enter it.

7. Nojoin shows **Connected** with your account, and a progress bar while it fetches your events for the first time, which can take a minute or two. Select **Continue in background** to close the dialog: the sync carries on, and the account shows **Syncing** with the same progress in **Calendars** until it finishes. If it fails, the account shows **Needs attention** with the reason.

Your default Proton calendar is selected and synced. Subscribed and holiday calendars are listed read-only.

### What Proton Sync Does

Proton calendars sync on the same 15-minute cycle as the others: a full fetch over the 25-month range, then only what changed. Requests go one at a time to keep the load small. Recurring events are expanded in their own time zone, so a weekly 10:00 meeting stays at 10:00 when the clocks change. Proton's conference link becomes the event's meeting link.

Assistants can create, change and cancel events on writable Proton calendars, make holds and answer invitations ([MCP.md](MCP.md#calendars)). A hold is marked with a hidden property Proton's apps keep, and shown in Proton with a `Hold: ` title prefix that Nojoin adds and removes.

Limits: descriptions of at most 3,000 characters, at most 100 attendees, and recurrence rules Proton's apps accept (daily, weekly, monthly or yearly, at most 49 occurrences by count, ending no later than 2037). An invitation from outside Proton that you have not opened in Proton Mail is not in your calendar yet, so Nojoin cannot see it.

### Reconnecting and Disconnecting

An account shows **Reconnect required** when Proton refuses the stored session: after a Proton password change, a session ended in Proton's session list, or a restore from a backup, which never carries Proton sign-ins. Select **Reconnect** on the account and sign in again; its calendars, events and linked recordings are kept. **Disconnect** ends the session at Proton, then removes the account from Nojoin.

### Proton Troubleshooting

- **Proton asked Nojoin to slow down.** Proton rate-limited the account. The next sync tries again; nothing needs doing.
- **Proton asked for a check Nojoin cannot show.** Sign in to Proton's web app from the same network once, then connect again.
- **Proton did not accept that username or password** after several tries. Wait ten minutes: Nojoin limits attempts, since repeated wrong passwords can lock a Proton account.

## Assistants and Your Calendars

Assistants connected through the MCP connector read your selected calendars: event times in each event's own time zone, the organiser, attendees and their answers, holds and recurrence ([MCP.md](MCP.md#calendars)). Only selected calendars are searched. **Settings > Integrations > Changes made by assistants** lists every change an assistant made to your calendars, which app made it, who was emailed and the outcome, for 12 months.

## Troubleshooting

### Redirect Origin Is Wrong

Confirm that `WEB_APP_URL` matches the origin users open, and that the provider registration uses the redirect URI shown in **Settings > Integrations**.

### `AADSTS50194` with Microsoft

The Microsoft app is single-tenant while Nojoin uses `common`. Either change the registration to **Any Entra ID Tenant + Personal Microsoft Accounts**, or set Nojoin's tenant to the explicit tenant ID.

### Microsoft Sign-In Works but Calendar Sync Still Fails

If the app should accept personal or mixed account types but Nojoin points at a specific tenant ID, sign-in can succeed while the first calendar read fails. Set the tenant back to `common`, save, then disconnect and reconnect the Outlook calendar.

### `invalid_client` During Token Exchange

Nojoin holds the **Secret ID** instead of the secret **Value**. Enter the **Value**.

### Admin Approval Message in Microsoft

The tenant blocks user consent for the delegated permissions. A tenant administrator must grant consent.

### Connecting Fails After Sign-In

The **Calendars** card shows what the provider said, and the API log (`docker logs nojoin-api`) records the step that failed with the HTTP status and the provider's error code and message, for example `Connecting a google calendar failed at the calendar list step: HTTP 403 forbidden: ...`. The log never holds the account's tokens or address. Common reasons:

- **`notACalendarUser`, or `forbidden` from Google for a Google Workspace account.** Calendar is turned off for that user, or the Workspace administrator restricts third-party apps' access to the Calendar API. In the Google Admin console, turn Calendar on for the user under **Apps > Google Workspace > Calendar**, or mark Nojoin's OAuth app as trusted under **Security > Access and data control > API controls**.
- **`admin_policy_enforced` from Google.** The Workspace administrator blocks the app. Trust it under **API controls** as above.
- **`accessNotConfigured` from Google.** The Google Calendar API is not enabled in the Google Cloud project behind the client ID. Enable it, as in [Google OAuth Setup](#google-oauth-setup).
- **`ErrorAccessDenied` from Microsoft.** The tenant blocks the app or has not consented to its permissions. See [Admin Approval Message in Microsoft](#admin-approval-message-in-microsoft).
- **`MailboxNotEnabledForRESTAPI` from Microsoft.** The account has no Exchange Online mailbox, for example because it is unlicensed or its mailbox is hosted on-premises, so it has no calendar Nojoin can read.
- **`invalid_client`.** The provider refused the app credentials. For Microsoft, see [`invalid_client` During Token Exchange](#invalid_client-during-token-exchange).

### Google Testing Mode Blocks a User

While the Google app is in testing mode, only listed test users can connect. Add the account as a test user or publish the consent screen.

Testing mode also ends each sign-in after seven days, so the account then shows **Reconnect required**. Publishing the consent screen stops this.

### Calendars Disconnect After the Encryption Key Changes

If the database outlives its encryption key, for example because `DATA_ENCRYPTION_KEY` changed or was never set and the data directory was replaced, Nojoin can no longer read the stored secrets. It removes the saved provider credentials, falling back to the environment variables, and removes each user's connection, so users must connect their calendars again. A Nojoin backup restores onto a different key without this problem ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)).

### Live Sync Not Activating

If an account never shows the **Live sync** badge, check each requirement under [Enabling Live Sync](#enabling-live-sync), starting with the provider's **Live sync** switch. Calendars still refresh every 15 minutes.

A CDN or web application firewall in front of Nojoin must let Microsoft and Google `POST` to the two webhook paths. Microsoft checks the address while creating a subscription and refuses it when that request is blocked. Cloudflare's Bot Fight Mode is a common cause: it challenges these requests, and the log shows Microsoft's `Subscription validation request failed` with status `Forbidden`. No rule can exempt a path from Bot Fight Mode, so either turn it off, move to Super Bot Fight Mode (Pro plan and above) with a custom rule that skips it for the two paths, or leave live sync off.

The `worker-io` log records each failed attempt with the HTTP status and the provider's own error code and message.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [ADMIN.md](ADMIN.md)
- [MCP.md](MCP.md)
- [SECURITY.md](SECURITY.md)
