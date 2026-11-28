# MCP Connector Guide

Nojoin has a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so AI assistants such as Claude can work with your meeting library on your own deployment: recordings, transcripts, meeting notes, attached documents, speakers, tags, tasks and your People library.

Read tools (`mcp:read`) cover the whole library, including semantic search and measured speaking analytics, and the user's synced calendars. Write tools (`mcp:write`) change the library, where nothing is deleted permanently (the strongest deletion is moving a recording to the bin, and only the web app can empty the bin), and, on calendars Nojoin can write to, the user's real calendar: [Calendars](#calendars) says what that means, since a cancelled event is removed at the provider and a write can email other people when asked. Because the tools are general primitives, an assistant that is also connected to a CRM (HubSpot, Airtable, or a pasted list) can sync people in either direction without Nojoin needing a CRM integration of its own.

## Requirements

- A deployment reachable over HTTPS at a stable public URL, the same trusted origin used for browser capture and calendar OAuth ([DEPLOYMENT.md](DEPLOYMENT.md#remote-access-and-trusted-public-origin)). Cloud assistants such as claude.ai and Claude Cowork must reach it from the internet.
- A normal Nojoin user account. Each connection acts as the user who authorised it.

The connector is on by default and needs no keys or extra configuration. Nojoin is its own OAuth 2.1 authorisation server: a client discovers the endpoints from the server URL, registers itself, and sends your browser to Nojoin's sign-in and consent page.

## Disabling the Connector

Set `MCP_ENABLED=false` in `.env` and restart Nojoin. The `/mcp` endpoint, the OAuth discovery documents and the authorisation endpoints then answer `404`, and existing connections stop working. Nothing else in Nojoin changes.

## Anonymous Discovery

By default every unauthenticated request to `/mcp` gets `401` with a `WWW-Authenticate` challenge pointing at Nojoin's OAuth discovery documents. That tells a client each user must sign in before any tool can be used: Claude's **Add custom connector** dialog marks **Sign in now** as **Detected**, and Claude Code opens the sign-in itself.

Codex Desktop cannot start OAuth from that `401` on its own, so mark the server for OAuth in Codex as [Connect ChatGPT or Codex](#connect-chatgpt-or-codex-desktop-app) shows. Where that is not possible, set `MCP_ANONYMOUS_DISCOVERY=true` in `.env` and restart. `/mcp` then answers `initialize`, `ping` and the tool listing without a token, and an unauthenticated tool call gets an in-band authentication challenge naming the scope it needs. All this exposes is the generic tool metadata listed under [Available Tools](#available-tools). No tool runs, no user data is reachable, every other unauthenticated request still gets `401`, and the anonymous surface is rate limited per client IP. Claude no longer detects that sign-in is required, though, so anyone adding the connector to Claude must choose **Sign in now** themselves.

## Connect Claude (claude.ai, Claude Desktop, Cowork)

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. Enter a name such as `Nojoin` and the URL `https://your-nojoin-domain/mcp`, then select **Continue** if the dialog shows it.
3. Under **Authentication**, keep **Sign in now**, and under **OAuth client**, keep **Register automatically**. Claude marks both **Detected**. Leave any client ID and secret fields empty, since Claude registers itself with Nojoin. Nojoin does not support **Use Claude's published identity**.
4. Select **Add**, then **Connect**. On Nojoin's consent page, sign in if asked, review the access (it includes the write tools), and select **Allow**. If your account must change its password first, Nojoin takes you to do that and then back to the consent page. The change signs out every session, so sign in there with the new password. If you have not yet accepted Nojoin's terms of use, the consent page shows them first and carries on once you accept.

A connector added to a claude.ai account also works in Claude Desktop and Cowork on the same account.

## Connect Claude Code

```bash
claude mcp add nojoin --transport http https://your-nojoin-domain/mcp
```

Claude Code finds the OAuth flow itself and opens a browser for the same sign-in and consent.

## Connect ChatGPT or Codex (desktop app)

OpenAI renamed the Codex desktop app to ChatGPT, but its settings, CLI and file paths still say Codex, so the steps below do too.

1. Open **Settings → MCP servers** and select **Add server**.
2. Enter a name such as `nojoin`, choose **Streamable HTTP** (STDIO is for servers Codex runs locally), and enter `https://your-nojoin-domain/mcp`. Leave the bearer-token and header fields empty, then save.
3. Mark the server for OAuth, because Codex does not start sign-in from Nojoin's `401` by itself. Either run `codex mcp login nojoin` with the Codex CLI, or add `auth = "oauth"` to the server's entry in `~/.codex/config.toml`, which the desktop app, CLI and IDE extension share:

   ```toml
   [mcp_servers.nojoin]
   url = "https://your-nojoin-domain/mcp"
   auth = "oauth"
   ```

4. Select **Restart**, then **Authenticate** in the server list, and complete the same sign-in and consent as for Claude. `codex mcp login` opens that sign-in itself.

If neither works for your Codex build, an administrator can turn on [anonymous discovery](#anonymous-discovery). Codex then probes the server at startup and offers **Authenticate**, and until you authenticate its tool calls return an authentication challenge.

The consent page heading, "Connect … to Nojoin", shows the `client_name` the app registered with, exactly as the app sent it. The app still registers as `Codex`, so its consent page and its entry in **Settings → Integrations → Connected apps** say Codex. Any client can register under any name, so check the "You will be returned to" host on the consent page before approving.

### If Authenticate never appears: stale stored credentials

Codex offers **Authenticate** only when it holds no OAuth tokens for the server. If it holds dead ones, it reports itself as signed in, keeps retrying a refresh that cannot succeed, and tasks fail with `MCP server 'nojoin' was not ready for this step`. This typically follows Nojoin revoking a grant, after a password change or a session revoke, or for refresh-token reuse, which Codex can trigger because its desktop app, CLI and IDE extension can each keep their own copy of the credentials.

Remove the stored credentials so Codex starts again:

- With the Codex CLI installed: run `codex mcp logout nojoin`, then restart the desktop app.
- Desktop only: quit Codex, then delete the server's entry under the **Codex MCP Credentials** service in the OS keychain (on Windows, Credential Manager → Windows Credentials, secret name starting `MCP_OAUTH`), or delete `~/.codex/.credentials.json` if it exists. That file holds every MCP server's credentials but not the ChatGPT sign-in. Restart the app.
- Quickest: delete the server in Codex and add the same URL under a different name. Credentials are keyed by name, so **Authenticate** appears after a restart.

If **Authenticate** still never appears from a clean state, check that the server is marked for OAuth (step 3 above) and restart, or run `codex mcp login nojoin`.

Some Codex Desktop builds (seen on 0.125.0-alpha.3) complete sign-in but never load the server's tools, reporting `auth_status` as `unsupported`. The same token works against the server directly, so update Codex rather than reconfiguring Nojoin.

## Available Tools

Thirty-nine tools: sixteen read and twenty-three write. Opposites share a row, so the table has fewer rows than tools. Every tool acts for the account that authorised the connection, with the same access it has in the web app: its own meetings, and the meetings shared with it ([USAGE.md](USAGE.md#sharing-a-meeting)). On a meeting shared to view, the read tools work and the write tools are refused. On one shared to edit, the tools that change the transcript, speakers, notes, documents or name work as well, and the actions only an owner may take are refused. Tags, People and tasks stay the account's own.

Two write tools spend your AI provider quota, which undoing their result does not refund: `regenerate_notes`, and the costlier `analyse_meeting`, whose description tells an assistant to ask before running it. On a meeting someone shared with you, both run on your own AI settings and quota, not the owner's.

| Tool | Scope | Description |
| --- | --- | --- |
| `list_recordings` | `mcp:read` | List and search recordings by text and date, including archived and binned ones and those shared with you. Each result reports `access` (`owner`, `edit` or `view`), `owner` on a shared meeting, processing state (`status`, `transcript_status`, `notes_status`), `updated_at`, the `transcript_revision` cursor and, for searches, a best-effort `match_field` hint. |
| `get_transcript` | `mcp:read` | The speaker-attributed transcript, formatted for reading. |
| `get_transcript_utterances` | `mcp:read` | The transcript as structured utterances: stable ids, millisecond timestamps, edit provenance, and a revision cursor with tombstones for incremental sync. |
| `search_context` | `mcp:read` | Semantic search across every transcript, set of AI notes and attached document, with the recording and a timestamp or page on each hit. A file on one of your notes is searched only while its **Use in notes** is on. |
| `get_meeting_notes` | `mcp:read` | The AI meeting notes plus your own notes, in timeline order, each with its time in the recording where it has one, and the file it holds, if any, as `attachment`. |
| `get_documents` | `mcp:read` | The documents attached to a recording, with their extracted text, and the files on your notes, each with its `note_id` and `use_in_notes`. A file on a note has text only while `use_in_notes` is on, and never when `readable` is false. |
| `get_meeting_analytics` | `mcp:read` | Speaking dynamics for a processed meeting, matching its Analytics tab: talk-time share, turns, response behaviour at speaker changes, a talk-share timeline, silence, and measured overlapping speech (there are no interruption counts). Adds measured delivery (pace, pitch, loudness, pausing, and each linked person's usual figures) where it has been generated, and `ai_analysis` (topics, sentiment read from the words, questions and answers, decision ownership, each claim with timestamped quotes) where the meeting has been analysed. Everything except `ai_analysis` is measured, not inferred by a model, and delivery describes how someone spoke, not their mood. `attribution_warnings` flags unreliable speaker attribution. |
| `get_speakers` | `mcp:read` | The speakers in a recording, with links to their People records. `name_pending` marks a suggested name nobody has confirmed yet, which the app shows as `Alice?`. A voice match scoring 0.85 or more shows as a plain name in the app, so it reads `false` here although nobody has confirmed it. |
| `list_tags` | `mcp:read` | Your tags, usable as search terms. |
| `list_people` | `mcp:read` | Your People library: names, contact details, notes and tags. |
| `get_person` | `mcp:read` | One person, with the meetings they appear in, newest first. These are the meetings their **Person Details** counts: deleted meetings and any where all of their lines were moved to other speakers are left out. |
| `list_calendars` | `mcp:read` | Your calendars from every connected account, with provider, selection, time zone, sync status, the synced range, and whether an assistant can write to each and why not. |
| `list_calendar_events` | `mcp:read` | Events from your selected calendars that overlap a range, all-day events included, newest first or oldest first, paged with `skip`. Each has offset-aware times in its own zone, the organiser, attendees with their answers, holds, recurrence and a `revision`. The first eight fields are what the tool always returned. |
| `get_calendar_event` | `mcp:read` | One event in full, with the recordings linked to it. `refresh` reads it from the provider first. |
| `get_free_busy` | `mcp:read` | Busy and free time across your selected calendars over at most 62 days, with `complete` false when a calendar may be out of date. |
| `list_tasks` | `mcp:read` | Your tasks. |
| `rename_recording` | `mcp:write` | Rename a recording. The name counts as the user's, so a generated title never replaces it. |
| `tag_recording` / `untag_recording` | `mcp:write` | Add a tag to a recording, creating the tag if needed, or remove one. |
| `archive_recording` / `restore_recording` | `mcp:write` | Archive a recording, or bring it back from the archive or the bin. |
| `trash_recording` | `mcp:write` | Move a recording to the bin. |
| `reprocess_recording` | `mcp:write` | Re-run processing. The transcript is rebuilt, so every utterance id changes and earlier corrections are replaced. `max_speakers` sets the recording's speaker limit and `clear_max_speakers` removes it; omitting both keeps the current one. Refused while the recording is being captured, paused, queued or processing. |
| `regenerate_notes` | `mcp:write` | Re-run Nojoin's notes pipeline. The assistant never writes the notes itself. Refused while the recording is being captured or paused. |
| `analyse_meeting` | `mcp:write` | Run the AI analysis of a meeting, then read it with `get_meeting_analytics`. Claims without a verified quote from the transcript are discarded. |
| `attach_document` | `mcp:write` | Attach text the assistant wrote as a markdown document. Binary files can only be uploaded in the web app. |
| `correct_utterance_text` / `correct_utterance_speaker` | `mcp:write` | Correct a line's text or speaker. The edit is logged with source `mcp`, locked like a web edit, and labelled `AI corrected text` or `AI corrected speaker` in the app. A speaker correction applies to every line by that speaker unless limited to one. |
| `unlock_utterance` | `mcp:write` | Release a line's edit locks. The release is logged. |
| `link_calendar_event` | `mcp:write` | Link a recording to a calendar event, or unlink it. |
| `create_calendar_event` | `mcp:write` | Create an event, a recurring series or a hold on a writable calendar. Emails nobody unless `notify_attendees` is true. |
| `update_calendar_event` | `mcp:write` | Change an event you organise, one occurrence or a whole series, or confirm a hold. Emails nobody unless `notify_attendees` is true. |
| `cancel_calendar_event` | `mcp:write` | Remove an event you organise at the provider, or release a hold. Emails nobody unless `notify_attendees` is true. |
| `respond_to_calendar_invitation` | `mcp:write` | Accept, decline or tentatively accept someone else's invitation. Emails the organiser unless `notify_organiser` is false. |
| `create_task` / `update_task` | `mcp:write` | Create, edit, complete, archive or restore tasks, with links to recordings and tags. Deleting a task is web-app only. |
| `import_people` | `mcp:write` | Create or update People records, matching existing people by exact name. Never touches voiceprints or removes data. |
| `set_speaker_name` | `mcp:write` | Name a meeting's speaker, linking them to the person of that exact name if one exists. The name reaches the transcript and the AI notes, and the speaker panel marks it `AI named`. |
| `append_meeting_notes` | `mcp:write` | Add a note after the end of a meeting's transcript, or, with `at_seconds`, at that moment of the recording, where the transcript shows it after the line spoken then. `at_seconds` is in seconds, as `get_transcript_utterances`' `start` gives them (or a `[MM:SS]` stamp from `get_transcript`, converted to seconds). A time past the end of a finished recording is refused. |

A connection keeps the scopes it was granted. One authorised before `mcp:write` existed stays read-only, and its write tools reply with an instruction to reconnect. Remove and re-add the connector to grant the wider scope.

## Calendars

The calendar tools read Nojoin's synced copy of your selected calendars. Where `list_calendars` shows a calendar as `writable`, the write tools change it at the provider. Proton Calendar is the first writable provider ([CALENDAR.md](CALENDAR.md#proton-calendar-unofficial)); Google and Outlook calendars are read-only to assistants (`provider_read_only`). `mcp:write` alone authorises calendar writes, and each tool's description tells the assistant to ask you first.

- **Who is told.** Create, update and cancel email nobody unless `notify_attendees` is true: attendees are only written onto the event. Answering an invitation is the exception: the reply is emailed to the organiser unless `notify_organiser` is false, and a Proton organiser sees the answer in their own calendar either way. Mail goes from your own address and sits in your Sent folder. Every result, dry runs included, carries `notifications`: who was emailed (or would be), who was not and why, and who sees the change without mail. If you later edit an event in your provider's own app, that app may offer to email its attendees.
- **Holds.** `status` "tentative" makes a hold, a provisional booking that blocks time in `get_free_busy`. Proton shows it with a `Hold: ` title prefix. Nobody is invited to a hold until it is confirmed (`update_calendar_event` with `status` "confirmed" and `notify_attendees` true).
- **Times.** A date-time with an offset is exact. One without an offset needs `time_zone`, an IANA name such as `Europe/London`, which is also the zone the event keeps, so a weekly 10:00 stays at 10:00 when the clocks change. A time that does not exist when the clocks go forward is refused, and one that happens twice when they go back needs its offset. All-day events take dates, with an exclusive end. In `list_calendar_events` a bare date is that whole day in your Nojoin time zone.
- **Recurring events.** Each occurrence is its own event `id`. Writes to one need `scope`: "occurrence" or "series". A series cannot be moved to another date, and changing its time or rule resets occurrences edited on their own. Answering one occurrence of a recurring invitation is not supported yet.
- **Safety.** `dry_run` checks everything and changes nothing. `idempotency_key` on a create makes a retry return the first event instead of creating another. Pass an event's `revision` as `expected_revision` to refuse a change if the event was edited since it was read.
- **Errors.** A refusal is a JSON object, `{"error": {"code", "message", "retryable", "field", "details"}}`. The codes are `invalid_argument`, `invalid_time`, `ambiguous_time`, `scope_required`, `unsupported`, `not_found`, `not_writable` (with `details.reason`), `not_organiser`, `not_attendee`, `conflict`, `idempotency_conflict`, `write_scope_missing`, `auth_expired`, `rate_limited`, `busy`, `provider_unavailable` and `provider_rejected`.
- **Change log.** Every calendar write past its argument checks is recorded under **Settings > Integrations > Changes made by assistants**: when, which app, what changed, who was emailed and the outcome. A cancelled event's last state is kept there so it can be recreated by hand. The log is kept 12 months and goes into backups.
- **Proton invitations.** An invitation from outside Proton that you have never opened sits in your Proton mailbox, not your calendar. Nojoin does not read mail, so it cannot see or answer such an invitation.

### Keeping an External Copy in Sync

`get_transcript_utterances` is for tools that keep their own copy of transcripts, such as a sidecar or knowledge base. Conversational assistants should use `get_transcript`.

1. Poll `list_recordings` and compare each recording's `transcript_revision` and `notes_status` with the values you stored. A recording is fully processed once `status` is `PROCESSED` and `notes_status` is `completed`.
2. Call `get_transcript_utterances` with your stored cursor as `after_revision` to receive only the changed utterances plus tombstones. Omit it for a full snapshot.
3. Page with `limit` (1 to 500, default 100) and `offset`. `total_utterances` is the full count, `next_offset` is null on the last page, and `tombstones` and `speakers` are complete on every page. If `revision` changes between pages, the transcript moved mid-read: restart from offset 0.

Treat the cursor as opaque and the response as additive, since new fields may appear. Reprocessing replaces every utterance id, but the cursor keeps increasing.

## Managing and Revoking Access

- **Settings → Integrations → Connected apps** lists each connection with its scope, creation time and last use, and revokes it. Revoking stops refreshes at once, and the current access token expires within an hour.
- Changing your password disconnects every app at once. So does an administrator resetting your password, deactivating your account or revoking your sessions, and so does revoking your own through `POST /api/v1/users/me/sessions/revoke-all`. Each app then needs connecting again, with a fresh consent, and Codex may need its old credentials cleared first ([stale stored credentials](#if-authenticate-never-appears-stale-stored-credentials)).

## How Authorisation Works

- **Discovery**: `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` (RFC 9728, RFC 8414).
- **Registration**: `POST /api/v1/oauth/register` (RFC 7591 Dynamic Client Registration). Only public PKCE clients are accepted, redirect URIs must be HTTPS or loopback HTTP, and registration is rate limited.
- **Consent**: the page at `/oauth/authorize` uses your normal Nojoin session and origin checks.
- **Tokens**: one-hour access tokens accepted only by `/mcp`, with the scopes `mcp:read` and `mcp:write`. Refresh tokens rotate on every use and expire after 180 days unused. [SECURITY.md](SECURITY.md#mcp-connector-access) covers codes, scopes and revocation in full.
- **Proxy**: `/mcp` and `/.well-known/oauth-*` must reach the API service. The bundled Nginx does this; see [DEPLOYMENT.md](DEPLOYMENT.md#reverse-proxy-requirements) if you run your own edge proxy.

## Troubleshooting

- **Claude cannot reach the server**: from outside your network, `https://your-domain/.well-known/oauth-protected-resource/mcp` should return JSON. If it returns the Nojoin web app, your edge proxy is not sending `/.well-known/oauth-*` and `/mcp` to the API service.
- **Claude added the connector without asking you to sign in**: it was added with **No sign-in**, so its tools cannot reach your library. Claude cannot change a connector's authentication once it is added, so remove the connector and add it again with **Sign in now** and **Register automatically**.
- **The consent page says the authorisation request is invalid**: the client's registration is gone, for example after a database restore, since backups do not carry registrations. Remove and re-add the connector.
- **A tool returned unexpected or empty results**: the API logs one line per tool call with the tool name, the user id, a redacted argument summary, the result shape and the duration, for example `mcp tool list_recordings ok user=1 limit=20 -> list:12 (18ms)`. Rejected calls log at `WARNING` and failures at `ERROR` with a traceback. Follow them with `docker compose logs -f api`. A refused call gives the assistant Nojoin's reason, the message the web app shows for the same refusal, such as `Recording not found`, and never the traceback. Free text and contact details appear only as lengths and counts, so the log holds no meeting content.
