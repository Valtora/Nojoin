# MCP Connector Guide

Nojoin has a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so AI assistants such as Claude can work with your meeting library on your own deployment: recordings, transcripts, meeting notes, attached documents, speakers, tags, tasks and your People library.

Read tools (`mcp:read`) cover the whole library, including semantic search and measured speaking analytics. Write tools (`mcp:write`) make recoverable changes only. Nothing in the connector deletes permanently: the strongest deletion is moving a recording to the bin, and only the web app can empty the bin. Because the tools are general primitives, an assistant that is also connected to a CRM (HubSpot, Airtable, or a pasted list) can sync people in either direction without Nojoin needing a CRM integration of its own.

## Requirements

- A deployment reachable over HTTPS at a stable public URL, the same trusted origin used for browser capture and calendar OAuth ([DEPLOYMENT.md](DEPLOYMENT.md#remote-access-and-trusted-public-origin)). Cloud assistants such as claude.ai and Claude Cowork must reach it from the internet.
- A normal Nojoin user account. Each connection acts as the user who authorised it.

The connector is on by default and needs no keys or extra configuration. Nojoin is its own OAuth 2.1 authorisation server: a client discovers the endpoints from the server URL, registers itself, and sends your browser to Nojoin's sign-in and consent page.

## Disabling the Connector

Set `MCP_ENABLED=false` in `.env` and restart Nojoin. The `/mcp` endpoint, the OAuth discovery documents and the authorisation endpoints then answer `404`, and existing connections stop working. Nothing else in Nojoin changes.

## Anonymous Discovery

Some clients, Codex Desktop among them, cannot start OAuth when the protocol handshake answers `401`. By default `/mcp` therefore answers `initialize`, `ping` and the tool listing without a token, and an unauthenticated tool call gets an in-band authentication challenge naming the scope it needs. All this exposes is the generic tool metadata listed under [Available Tools](#available-tools). No tool runs, no user data is reachable, every other unauthenticated request still gets `401`, and the anonymous surface is rate limited per client IP.

Set `MCP_ANONYMOUS_DISCOVERY=false` and restart to answer `401` to every unauthenticated request. Clients must then start OAuth from the `401` itself, which Claude can do and Codex Desktop cannot.

## Connect Claude (claude.ai, Claude Desktop, Cowork)

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. Enter a name such as `Nojoin` and the URL `https://your-nojoin-domain/mcp`.
3. Leave the OAuth Client ID and Client Secret empty. Claude registers itself with Nojoin.
4. Select **Add**, then **Connect**. On Nojoin's consent page, sign in if asked, review the access (it includes the write tools), and select **Allow**. If your account must change its password first, Nojoin takes you to do that and then back to the consent page. The change signs out every session, so sign in there with the new password.

A connector added to a claude.ai account also works in Claude Desktop and Cowork on the same account.

## Connect Claude Code

```bash
claude mcp add nojoin --transport http https://your-nojoin-domain/mcp
```

Claude Code finds the OAuth flow itself and opens a browser for the same sign-in and consent.

## Connect ChatGPT or Codex (desktop app)

OpenAI renamed the Codex desktop app to ChatGPT, but its settings, CLI and file paths still say Codex, so the steps below do too.

1. Open **Settings → MCP servers** and select **Add server**.
2. Enter a name such as `nojoin`, choose **Streamable HTTP** (STDIO is for servers Codex runs locally), and enter `https://your-nojoin-domain/mcp`. Leave the bearer-token and header fields empty.
3. Save, then select **Restart**. Codex probes the server at startup through [anonymous discovery](#anonymous-discovery).
4. Select **Authenticate** in the server list and complete the same sign-in and consent as for Claude. Until then, tool calls return an authentication challenge.

The consent page heading, "Connect … to Nojoin", shows the `client_name` the app registered with, exactly as the app sent it. The app still registers as `Codex`, so its consent page and its entry in **Settings → Integrations → Connected apps** say Codex. Any client can register under any name, so check the "You will be returned to" host on the consent page before approving.

### If Authenticate never appears: stale stored credentials

Codex offers **Authenticate** only when it holds no OAuth tokens for the server. If it holds dead ones, it reports itself as signed in, keeps retrying a refresh that cannot succeed, and tasks fail with `MCP server 'nojoin' was not ready for this step`. This typically follows Nojoin revoking a grant, after a password change or a session revoke, or for refresh-token reuse, which Codex can trigger because its desktop app, CLI and IDE extension can each keep their own copy of the credentials.

Remove the stored credentials so Codex starts again:

- With the Codex CLI installed: run `codex mcp logout nojoin`, then restart the desktop app.
- Desktop only: quit Codex, then delete the server's entry under the **Codex MCP Credentials** service in the OS keychain (on Windows, Credential Manager → Windows Credentials, secret name starting `MCP_OAUTH`), or delete `~/.codex/.credentials.json` if it exists. That file holds every MCP server's credentials but not the ChatGPT sign-in. Restart the app.
- Quickest: delete the server in Codex and add the same URL under a different name. Credentials are keyed by name, so **Authenticate** appears after a restart.

If **Authenticate** still never appears from a clean state, mark the server for OAuth in `~/.codex/config.toml` (shared by the desktop app, CLI and IDE extension) and restart, or run `codex mcp login nojoin`:

```toml
[mcp_servers.nojoin]
url = "https://your-nojoin-domain/mcp"
auth = "oauth"
```

Some Codex Desktop builds (seen on 0.125.0-alpha.3) complete sign-in but never load the server's tools, reporting `auth_status` as `unsupported`. The same token works against the server directly, so update Codex rather than reconfiguring Nojoin.

## Available Tools

Thirty-two tools: thirteen read and nineteen write. Opposites share a row, so the table has fewer rows than tools. Every tool acts only on data owned by the account that authorised the connection.

Two write tools spend your AI provider quota, which undoing their result does not refund: `regenerate_notes`, and the costlier `analyse_meeting`, whose description tells an assistant to ask before running it.

| Tool | Scope | Description |
| --- | --- | --- |
| `list_recordings` | `mcp:read` | List and search recordings by text and date, including archived and binned ones. Each result reports processing state (`status`, `transcript_status`, `notes_status`), `updated_at`, the `transcript_revision` cursor and, for searches, a best-effort `match_field` hint. |
| `get_transcript` | `mcp:read` | The speaker-attributed transcript, formatted for reading. |
| `get_transcript_utterances` | `mcp:read` | The transcript as structured utterances: stable ids, millisecond timestamps, edit provenance, and a revision cursor with tombstones for incremental sync. |
| `search_context` | `mcp:read` | Semantic search across every transcript, set of AI notes and attached document, with the recording and a timestamp or page on each hit. A file on one of your notes is searched only while its **Use in notes** is on. |
| `get_meeting_notes` | `mcp:read` | The AI meeting notes plus your own notes, in timeline order, each with its time in the recording where it has one, and the file it holds, if any, as `attachment`. |
| `get_documents` | `mcp:read` | The documents attached to a recording, with their extracted text, and the files on your notes, each with its `note_id` and `use_in_notes`. A file on a note has text only while `use_in_notes` is on, and never when `readable` is false. |
| `get_meeting_analytics` | `mcp:read` | Speaking dynamics for a processed meeting, matching its Analytics tab: talk-time share, turns, response behaviour at speaker changes, a talk-share timeline, silence, and measured overlapping speech (there are no interruption counts). Adds measured delivery (pace, pitch, loudness, pausing, and each linked person's usual figures) where it has been generated, and `ai_analysis` (topics, sentiment read from the words, questions and answers, decision ownership, each claim with timestamped quotes) where the meeting has been analysed. Everything except `ai_analysis` is measured, not inferred by a model, and delivery describes how someone spoke, not their mood. `attribution_warnings` flags unreliable speaker attribution. |
| `get_speakers` | `mcp:read` | The speakers in a recording, with links to their People records. `name_pending` marks a suggested name nobody has confirmed yet, which the app shows as `Alice?`. A voice match scoring 0.85 or more shows as a plain name in the app, so it reads `false` here although nobody has confirmed it. |
| `list_tags` | `mcp:read` | Your tags, usable as search terms. |
| `list_people` | `mcp:read` | Your People library: names, contact details, notes and tags. |
| `get_person` | `mcp:read` | One person, with the meetings they appear in. |
| `list_calendar_events` | `mcp:read` | Your synced calendar events, for linking recordings to meetings. |
| `list_tasks` | `mcp:read` | Your tasks. |
| `rename_recording` | `mcp:write` | Rename a recording. |
| `tag_recording` / `untag_recording` | `mcp:write` | Add a tag to a recording, creating the tag if needed, or remove one. |
| `archive_recording` / `restore_recording` | `mcp:write` | Archive a recording, or bring it back from the archive or the bin. |
| `trash_recording` | `mcp:write` | Move a recording to the bin. |
| `reprocess_recording` | `mcp:write` | Re-run processing. The transcript is rebuilt, so every utterance id changes and earlier corrections are replaced. Refused while the recording is being captured, paused, queued or processing. |
| `regenerate_notes` | `mcp:write` | Re-run Nojoin's notes pipeline. The assistant never writes the notes itself. Refused while the recording is being captured or paused. |
| `analyse_meeting` | `mcp:write` | Run the AI analysis of a meeting, then read it with `get_meeting_analytics`. Claims without a verified quote from the transcript are discarded. |
| `attach_document` | `mcp:write` | Attach text the assistant wrote as a markdown document. Binary files can only be uploaded in the web app. |
| `correct_utterance_text` / `correct_utterance_speaker` | `mcp:write` | Correct a line's text or speaker. The edit is logged with source `mcp`, locked like a web edit, and labelled `AI corrected text` or `AI corrected speaker` in the app. A speaker correction applies to every line by that speaker unless limited to one. |
| `unlock_utterance` | `mcp:write` | Release a line's edit locks. The release is logged. |
| `link_calendar_event` | `mcp:write` | Link a recording to a calendar event, or unlink it. |
| `create_task` / `update_task` | `mcp:write` | Create, edit, complete, archive or restore tasks, with links to recordings and tags. Deleting a task is web-app only. |
| `import_people` | `mcp:write` | Create or update People records, matching existing people by exact name. Never touches voiceprints or removes data. |
| `set_speaker_name` | `mcp:write` | Name a meeting's speaker, linking them to the person of that exact name if one exists. The name reaches the transcript and the AI notes, and the speaker panel marks it `AI named`. |
| `append_meeting_notes` | `mcp:write` | Add a note after the end of a meeting's transcript, or, with `at_seconds`, at that moment of the recording, where the transcript shows it after the line spoken then. `at_seconds` is in seconds, as `get_transcript_utterances`' `start` gives them (or a `[MM:SS]` stamp from `get_transcript`, converted to seconds). A time past the end of a finished recording is refused. |

A connection keeps the scopes it was granted. One authorised before `mcp:write` existed stays read-only, and its write tools reply with an instruction to reconnect. Remove and re-add the connector to grant the wider scope.

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
- **The consent page says the authorisation request is invalid**: the client's registration is gone, for example after a database restore, since backups do not carry registrations. Remove and re-add the connector.
- **A tool returned unexpected or empty results**: the API logs one line per tool call with the tool name, the user id, a redacted argument summary, the result shape and the duration, for example `mcp tool list_recordings ok user=1 limit=20 -> list:12 (18ms)`. Rejected calls log at `WARNING` and failures at `ERROR` with a traceback. Follow them with `docker compose logs -f api`. Free text and contact details appear only as lengths and counts, so the log holds no meeting content.
