# MCP Connector Guide

Nojoin ships a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server so AI assistants such as Claude can work with your meeting library — recordings, transcripts, meeting notes, attached documents, speakers, tags, and your People library — directly from your own deployment.

The connector is a full agentic interface with two access tiers. **Read** tools (`mcp:read`) cover the whole library, including semantic search across every meeting and document. **Write** tools (`mcp:write`) cover recoverable changes: organising recordings (rename, tag, archive, bin, restore), managing tasks, correcting transcripts, regenerating notes, attaching text documents, appending user notes, and maintaining People records. Everything an assistant changes stays recoverable by you: archived and binned items restore, transcript edits are tracked in the edit log, and the assistant never authors AI-note content directly. **Permanent deletion is not possible through the connector at all** — the strongest deletion verb is moving a recording to the bin, and emptying the bin exists only in the web app. Because Nojoin exposes clean primitives over its own data, an assistant that is also connected to a CRM (HubSpot, Airtable, or a pasted list) can sync people in either direction without Nojoin needing any CRM-specific integration.

## Requirements

- A Nojoin deployment reachable over HTTPS at a stable public URL (the same trusted origin used for browser capture and calendar OAuth). Assistants running in the cloud, such as claude.ai and Claude Cowork, must be able to reach the URL from the internet.
- A normal Nojoin user account. Each connection is scoped to the user who authorises it.

The connector is enabled by default and needs no additional configuration, environment variables, or API keys. Nojoin acts as its own OAuth 2.1 authorization server: clients discover the endpoints from the server URL, register themselves automatically, and send your browser to Nojoin's own sign-in and consent page.

## Disabling the Connector

Operators who do not want the connector surface at all can set `MCP_ENABLED=false` in `.env` and restart the stack. This removes the `/mcp` endpoint, the OAuth discovery documents, and the authorisation endpoints — all of them respond `404` — without affecting any other Nojoin functionality. Existing grants stop working immediately because the token endpoint is gone.

## Anonymous Discovery

Some MCP clients — Codex Desktop among them — cannot begin OAuth against a server that answers the protocol handshake with `401`. By default Nojoin therefore serves a small anonymous bootstrap surface on `/mcp`: `initialize`, `ping`, and the tool listing succeed without a token, and an unauthenticated tool call returns an in-band authentication challenge naming the scope it needs instead of executing. What this exposes is only the connector's generic tool metadata — names, descriptions, schemas, and scopes, identical on every deployment and mirrored in the tool table below. No tool runs, no user data is reachable, every request outside that fixed allowlist still gets the strict `401`, and the anonymous surface is rate limited per client IP.

Operators who prefer the strict pre-authentication behaviour can set `MCP_ANONYMOUS_DISCOVERY=false` in `.env` and restart; MCP clients must then be able to start OAuth from the `401` challenge itself (Claude can, Codex Desktop currently cannot). `MCP_ENABLED=false` removes the whole surface regardless.

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

## Connect Codex (desktop app)

1. Open **Settings → MCP servers** and select **Add server**.
2. Enter a name (for example `nojoin`), choose **Streamable HTTP** (not STDIO, which is for MCP servers Codex launches locally as a subprocess), and enter the URL `https://your-nojoin-domain/mcp`. Leave any bearer-token and header fields empty — that is not how Nojoin authenticates.
3. Save the server, then select **Restart**. The restart matters: Codex probes the server on startup. The handshake and tool listing succeed anonymously (see [Anonymous Discovery](#anonymous-discovery)), every tool advertises the OAuth scope it needs, and the server flags itself as requiring sign-in.
4. In the server list, select **Authenticate**. A browser window opens for the same Nojoin sign-in and consent step as Claude. Until then, any tool call an assistant attempts returns an authentication challenge rather than data.

**The name on the consent screen comes from the client, not from Nojoin.** Every MCP client registers itself before the first authorisation and sends a `client_name`; Nojoin stores that string and displays it verbatim, so the heading reads whatever the connecting app calls itself. Nothing in Nojoin picks or overrides it, and no Nojoin release can change what a given client reports. Any client that can speak the protocol can register under any name it likes, which is worth knowing when you read a consent screen: **the name tells you what the app claims to be, and the redirect host underneath it tells you where you are actually being sent.** Check both before approving.

In practice that is why connecting the OpenAI desktop app still reads "Connect Codex to Nojoin" after its rename to ChatGPT — the client continues to register as `Codex`. Existing connections keep the name they registered with, and **Settings → Integrations → Connected apps** lists them under it.

### If Authenticate never appears: stale stored credentials

Codex only offers **Authenticate** when it finds no stored OAuth tokens for the server. If it holds tokens from an earlier connection — even dead ones — it reports itself as signed in and silently retries a refresh that can never succeed, and tasks see `MCP server 'nojoin' was not ready for this step`. This happens in particular after Nojoin revokes a grant for refresh-token replay (Codex's desktop app, CLI, and IDE extension share `~/.codex` but can keep divergent credential copies across its keyring and file stores, so one process can replay a token another has already rotated — which Nojoin's OAuth server treats as theft and punishes by revoking the whole grant).

The fix is to remove the stored credentials so Codex re-evaluates the server from scratch:

- Where the Codex CLI is installed: `codex mcp logout nojoin`, then restart the desktop app.
- Desktop-only hosts: quit Codex fully, then delete the server's entry under the **"Codex MCP Credentials"** service in the OS keychain (Windows Credential Manager → Windows Credentials; the secret name starts with `MCP_OAUTH`), or delete the fallback file `~/.codex/.credentials.json` if it exists (this clears stored credentials for every MCP server, though not the ChatGPT sign-in). Restart the app.
- Quickest workaround, no credential surgery: delete the server entry in Codex and re-add the same URL under a **different name**. Stored credentials are keyed by server name, so the new name starts clean and **Authenticate** appears after a restart.

If **Authenticate** still never appears from a genuinely clean state, mark the server for OAuth by hand in `~/.codex/config.toml` (shared by the desktop app, CLI, and IDE extension), restart again, or run `codex mcp login nojoin` in a terminal as a fallback:

```toml
[mcp_servers.nojoin]
url = "https://your-nojoin-domain/mcp"
auth = "oauth"
```

Known upstream issue: some Codex Desktop builds (observed on 0.125.0-alpha.3) complete the OAuth sign-in but never import the server's tools into threads, reporting `auth_status` as `unsupported`. That failure is inside Codex — the same Codex-issued token works against the server directly — so if tools stay absent after a successful sign-in, update Codex rather than reconfiguring Nojoin.

## Available Tools

Thirty tools. Twelve read and eighteen write; the table below pairs a few of them onto one
row where they are opposites of each other, so it has fewer rows than there are tools.

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
| `correct_utterance_text` / `correct_utterance_speaker` | `mcp:write` | Correct a transcript utterance; edits are tracked in the event log with source `mcp` and lock against reprocess overwrite, like web edits. The app labels the result `AI corrected text` or `AI corrected speaker`, so an assistant's correction is visibly distinct from one made in the web app, and stays so across reprocessing. |
| `unlock_utterance` | `mcp:write` | Release an utterance's manual-edit locks so reprocessing may overwrite it again; the clearing is itself an audited event. |
| `link_calendar_event` | `mcp:write` | Link a recording to a calendar event, or unlink it. |
| `create_task` / `update_task` | `mcp:write` | Create, edit, complete, archive, or restore tasks, with links to recordings and tags. Archiving is the strongest removal; task deletion exists only in the web app. |
| `import_people` | `mcp:write` | Create or update People records, matching existing people by name. |
| `set_speaker_name` | `mcp:write` | Name a meeting's speaker and link them to a person. The name propagates to the transcript and the AI meeting notes, and the speaker panel labels it `AI named`; because this relabels every line the speaker holds, it is disclosed once against the speaker rather than on each utterance. |
| `append_meeting_notes` | `mcp:write` | Append text to a meeting's user notes. |

All tools operate only on data owned by the account that authorised the connection. Everything is recoverable: archived and binned recordings restore, transcript corrections are attributed in the app as well as tracked in the edit log, notes regeneration re-runs Nojoin's own pipeline rather than accepting assistant-authored notes, and People imports never touch voiceprints or remove existing data. There is deliberately no destructive tool: an assistant can move a recording to the bin, and only you can empty the bin, from the web app, which itself only offers permanent deletion for recordings already binned.

Connections keep the scopes they were granted. Grants that predate `mcp:write` stay read-only: the write tools respond with an instruction to reconnect rather than failing opaquely, and removing and re-adding the connector consents to the wider scope.

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
- Access tokens are one-hour JWTs signed by the standard Nojoin keyring, valid **only** for the `/mcp` endpoint — they cannot call the general API. Grants carry the `mcp:read` and `mcp:write` scopes; there is no stronger scope, and the retired `mcp:destroy` scope is stripped from any client that still requests it by name. Refresh tokens rotate on every use; reuse of a rotated token revokes the whole grant.
- The reverse proxy must forward `/mcp` and `/.well-known/oauth-*` to the API service. The bundled Nginx configuration does this out of the box; see [DEPLOYMENT.md](DEPLOYMENT.md) if you front Nojoin with your own edge proxy.

## Troubleshooting

- **Claude reports it cannot reach the server**: confirm `https://your-domain/.well-known/oauth-protected-resource/mcp` returns JSON from outside your network. If it returns the Nojoin web app instead, your edge proxy is not routing `/.well-known/oauth-*` and `/mcp` to the API service.
- **Authorisation page shows "This authorization request is invalid"**: the client's registration may have been removed (for example after a database restore). Remove and re-add the connector so the client re-registers.
- **Connector stopped working after a password change**: that is intentional containment. Reconnect from the assistant to authorise again.
- **A tool returned unexpected or empty results**: the API service logs one line per MCP tool call — the tool name, the acting user id, a redacted argument summary, the result shape, and the duration (for example `mcp tool list_recordings ok user=1 limit=20 -> list:12 (18ms)`). Rejected calls log at `WARNING` and unexpected failures at `ERROR` with a traceback. Follow them with `docker compose logs -f api` (or your deployment's log viewer). Argument summaries never include note bodies, search text, or personal contact details — only lengths and counts — so the log stays useful without recording meeting content.
