# MCP Connector Guide

Nojoin ships a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server so AI assistants such as Claude can work with your meeting library — recordings, transcripts, meeting notes, attached documents, speakers, tags, and your People library — directly from your own deployment.

The connector is a full agentic interface with three access tiers. **Read** tools (`mcp:read`) cover the whole library, including semantic search across every meeting and document. **Write** tools (`mcp:write`) cover recoverable changes: organising recordings (rename, tag, archive, bin, restore), managing tasks, correcting transcripts, regenerating notes, attaching text documents, appending user notes, and maintaining People records. Everything an assistant changes with write access stays recoverable by you: archived and binned items restore, transcript edits are tracked in the edit log, and the assistant never authors AI-note content directly. **Permanent deletion** exists only behind the separate `mcp:destroy` scope, which is never granted by default: the consent page offers it as an explicit opt-in tick-box. Because Nojoin exposes clean primitives over its own data, an assistant that is also connected to a CRM (HubSpot, Airtable, or a pasted list) can sync people in either direction without Nojoin needing any CRM-specific integration.

## Requirements

- A Nojoin deployment reachable over HTTPS at a stable public URL (the same trusted origin used for browser capture and calendar OAuth). Assistants running in the cloud, such as claude.ai and Claude Cowork, must be able to reach the URL from the internet.
- A normal Nojoin user account. Each connection is scoped to the user who authorises it.

The connector is enabled by default and needs no additional configuration, environment variables, or API keys. Nojoin acts as its own OAuth 2.1 authorization server: clients discover the endpoints from the server URL, register themselves automatically, and send your browser to Nojoin's own sign-in and consent page.

## Disabling the Connector

Operators who do not want the connector surface at all can set `MCP_ENABLED=false` in `.env` and restart the stack. This removes the `/mcp` endpoint, the OAuth discovery documents, and the authorisation endpoints — all of them respond `404` — without affecting any other Nojoin functionality. Existing grants stop working immediately because the token endpoint is gone.

## Connect Claude (claude.ai, Claude Desktop, Cowork)

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. Enter a name (for example `Nojoin`) and the MCP server URL:

   ```text
   https://your-nojoin-domain/mcp
   ```

3. Leave the OAuth Client ID and Client Secret fields empty — Claude registers itself with your Nojoin instance automatically.
4. Click **Add**, then **Connect**. Your browser opens Nojoin's authorisation page: sign in with your Nojoin credentials if needed, review the requested access — including the additive write capabilities (People, speaker names, and notes) — and click **Allow access**.

The connector then appears in Claude's tool list. Connectors added to a claude.ai account are also available in Claude Desktop and Cowork on the same account.

## Connect Claude Code

```bash
claude mcp add nojoin --transport http https://your-nojoin-domain/mcp
```

Claude Code discovers the OAuth flow automatically and opens a browser window for the same sign-in and consent step. No token pasting is required.

## Available Tools

| Tool | Scope | Description |
| --- | --- | --- |
| `list_recordings` | `mcp:read` | List and search recordings with free-text and date filters; covers archived and soft-deleted meetings by default. Each result reports processing state (`status`, `transcript_status`, `notes_status`), `updated_at`, the canonical `transcript_revision` cursor, and, for searches, a best-effort `match_field` hint. |
| `get_transcript` | `mcp:read` | Full speaker-attributed transcript of a recording, formatted for reading. |
| `get_transcript_utterances` | `mcp:read` | The canonical transcript as structured utterances: stable ids, millisecond timestamps, per-utterance state and edit provenance, and a revision cursor with tombstones for incremental sync. |
| `search_context` | `mcp:read` | Semantic search across every transcript and attached document, with recording, timestamp, and page provenance on each hit. |
| `get_meeting_notes` | `mcp:read` | AI-generated meeting notes plus your own manual notes. |
| `get_documents` | `mcp:read` | The documents attached to a recording, with their extracted text. |
| `get_speakers` | `mcp:read` | The speakers in a recording, with links to their People records. |
| `list_tags` | `mcp:read` | Your tag list, usable as search terms. |
| `list_people` | `mcp:read` | Your People library: names, contact details, notes, and tags. |
| `get_person` | `mcp:read` | One person's profile plus the meetings they appear in. |
| `list_calendar_events` | `mcp:read` | Your synced calendar events, for linking recordings to meetings. |
| `list_tasks` | `mcp:read` | Your tasks from the Task workspace. |
| `rename_recording` | `mcp:write` | Rename a recording. |
| `tag_recording` / `untag_recording` | `mcp:write` | Add or remove a tag on a recording, creating the tag if needed. |
| `archive_recording` / `restore_recording` | `mcp:write` | Archive a recording, or bring it back from the archive or bin. |
| `trash_recording` | `mcp:write` | Move a recording to the bin (soft delete, reversible). |
| `reprocess_recording` | `mcp:write` | Re-run the processing pipeline for a recording. |
| `regenerate_notes` | `mcp:write` | Re-run Nojoin's notes pipeline; the assistant never writes note content itself. |
| `attach_document` | `mcp:write` | Attach assistant-authored text as a markdown document; binary uploads stay in the web app. |
| `correct_utterance_text` / `correct_utterance_speaker` | `mcp:write` | Correct a transcript utterance; edits are tracked in the event log with source `mcp` and lock against reprocess overwrite, like web edits. |
| `unlock_utterance` | `mcp:write` | Release an utterance's manual-edit locks so reprocessing may overwrite it again; the clearing is itself an audited event. |
| `link_calendar_event` | `mcp:write` | Link a recording to a calendar event, or unlink it. |
| `create_task` / `update_task` / `delete_task` | `mcp:write` | Create, edit, complete, archive, or delete tasks, with links to recordings and tags. |
| `import_people` | `mcp:write` | Create or update People records, matching existing people by name. |
| `set_speaker_name` | `mcp:write` | Name a meeting's speaker and link them to a person. |
| `append_meeting_notes` | `mcp:write` | Append text to a meeting's user notes. |
| `destroy_recording` | `mcp:destroy` | Permanently delete a recording and its audio. Irreversible, refused unless the recording is already in the bin (trash first, then destroy), and only available when the user ticked the deletion opt-in at consent time. |

All tools operate only on data owned by the account that authorised the connection. Everything under `mcp:write` is recoverable: archived and binned recordings restore, transcript corrections are attributed and tracked in the edit log, notes regeneration re-runs Nojoin's own pipeline rather than accepting assistant-authored notes, and People imports never touch voiceprints or remove existing data. The one irreversible verb, `destroy_recording`, sits alone behind `mcp:destroy` and additionally refuses any recording not already in the bin, so destruction always takes two deliberate steps with different verbs — the same bin-first rule the web app has always enforced in its UI, now guaranteed server-side for every surface.

Connections keep the scopes they were granted. Grants that predate `mcp:write` stay read-only, and grants without the deletion opt-in cannot destroy anything: the affected tools respond with an instruction to reconnect rather than failing opaquely. Remove and re-add the connector to consent to a wider scope, ticking "Also allow permanent deletion" on the consent page if the assistant should be able to destroy recordings.

### Keeping an External Copy in Sync

`get_transcript_utterances` exists for tools that maintain their own copy of transcript data, such as a read-only sidecar or knowledge base, rather than for conversational assistants, which should prefer `get_transcript`. Poll `list_recordings` and compare each recording's `transcript_revision` (and `notes_status`) with the last value you stored, then call `get_transcript_utterances` with your stored cursor as `after_revision` to receive only changed utterances plus tombstones. A recording is fully processed once `status` is `PROCESSED` and `notes_status` is `completed`. Treat the cursor as opaque and the response fields as an additive contract: new fields may appear over time, and reprocessing a recording can replace most utterance ids while the cursor keeps increasing.

Responses are paged, because a full snapshot of a long meeting easily exceeds an assistant's tool-output budget: `utterances` carries at most `limit` entries (1-500, default 100) starting at `offset`, `total_utterances` is the full count, and `next_offset` is the next page's offset, or null on the last page. `tombstones` and `speakers` are complete on every page. Pages are only consistent within a single `revision`: if `revision` changes between pages, the transcript moved mid-read, so restart from offset 0. Deltas are usually a single page; the calls that need paging are the first snapshot and the delta after a reprocess.

## Managing and Revoking Access

- **Settings → Integrations → Connected apps** lists every active connection with its scope, creation time, and last use, and offers per-connection revocation.
- Changing your password (or an admin resetting it, or `revoke all sessions`) immediately invalidates all connector access tokens, in the same way browser sessions are invalidated.
- Revoking a connection invalidates its refresh tokens; the current access token expires within an hour.

## How Authorisation Works

For operators who want the detail:

- Discovery documents are served at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` (RFC 9728 / RFC 8414).
- Clients self-register at `POST /api/v1/oauth/register` (RFC 7591 Dynamic Client Registration). Only public clients with PKCE are accepted; registration is rate limited.
- The authorisation page at `/oauth/authorize` uses your normal Nojoin session and origin protections. Codes are single-use, PKCE-bound (S256), and expire after 60 seconds.
- Access tokens are one-hour JWTs signed by the standard Nojoin keyring, valid **only** for the `/mcp` endpoint — they cannot call the general API. New grants carry the `mcp:read` and `mcp:write` scopes by default; `mcp:destroy` is added only when the user ticks the permanent-deletion opt-in on the consent page (or a client requests it by name). Refresh tokens rotate on every use; reuse of a rotated token revokes the whole grant.
- The reverse proxy must forward `/mcp` and `/.well-known/oauth-*` to the API service. The bundled Nginx configuration does this out of the box; see [DEPLOYMENT.md](DEPLOYMENT.md) if you front Nojoin with your own edge proxy.

## Troubleshooting

- **Claude reports it cannot reach the server**: confirm `https://your-domain/.well-known/oauth-protected-resource/mcp` returns JSON from outside your network. If it returns the Nojoin web app instead, your edge proxy is not routing `/.well-known/oauth-*` and `/mcp` to the API service.
- **Authorisation page shows "This authorization request is invalid"**: the client's registration may have been removed (for example after a database restore). Remove and re-add the connector so the client re-registers.
- **Connector stopped working after a password change**: that is intentional containment. Reconnect from the assistant to authorise again.
- **A tool returned unexpected or empty results**: the API service logs one line per MCP tool call — the tool name, the acting user id, a redacted argument summary, the result shape, and the duration (for example `mcp tool list_recordings ok user=1 limit=20 -> list:12 (18ms)`). Rejected calls log at `WARNING` and unexpected failures at `ERROR` with a traceback. Follow them with `docker compose logs -f api` (or your deployment's log viewer). Argument summaries never include note bodies, search text, or personal contact details — only lengths and counts — so the log stays useful without recording meeting content.
