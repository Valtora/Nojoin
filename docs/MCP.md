# MCP Connector Guide

Nojoin ships a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server so AI assistants such as Claude can read your meeting library — recordings, transcripts, meeting notes, and tags — directly from your own deployment.

The connector is **read-only**. Connected assistants cannot create, edit, or delete anything in Nojoin.

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
4. Click **Add**, then **Connect**. Your browser opens Nojoin's authorisation page: sign in with your Nojoin credentials if needed, review the requested access, and click **Allow access**.

The connector then appears in Claude's tool list. Connectors added to a claude.ai account are also available in Claude Desktop and Cowork on the same account.

## Connect Claude Code

```bash
claude mcp add nojoin --transport http https://your-nojoin-domain/mcp
```

Claude Code discovers the OAuth flow automatically and opens a browser window for the same sign-in and consent step. No token pasting is required.

## Available Tools

| Tool | Description |
| --- | --- |
| `list_recordings` | List recordings with free-text search and date filters. |
| `get_transcript` | Full speaker-attributed transcript of a recording. |
| `get_meeting_notes` | AI-generated meeting notes plus your own manual notes. |
| `list_tags` | Your tag list, usable as search terms. |

All tools operate only on recordings owned by the account that authorised the connection.

## Managing and Revoking Access

- **Settings → Personal → Connected Apps** lists every active connection with its scope, creation time, and last use, and offers per-connection revocation.
- Changing your password (or an admin resetting it, or `revoke all sessions`) immediately invalidates all connector access tokens, in the same way browser sessions are invalidated.
- Revoking a connection invalidates its refresh tokens; the current access token expires within an hour.

## How Authorisation Works

For operators who want the detail:

- Discovery documents are served at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` (RFC 9728 / RFC 8414).
- Clients self-register at `POST /api/v1/oauth/register` (RFC 7591 Dynamic Client Registration). Only public clients with PKCE are accepted; registration is rate limited.
- The authorisation page at `/oauth/authorize` uses your normal Nojoin session and origin protections. Codes are single-use, PKCE-bound (S256), and expire after 60 seconds.
- Access tokens are one-hour JWTs signed by the standard Nojoin keyring, valid **only** for the `/mcp` endpoint — they cannot call the general API. Refresh tokens rotate on every use; reuse of a rotated token revokes the whole grant.
- The reverse proxy must forward `/mcp` and `/.well-known/oauth-*` to the API service. The bundled Nginx configuration does this out of the box; see [DEPLOYMENT.md](DEPLOYMENT.md) if you front Nojoin with your own edge proxy.

## Troubleshooting

- **Claude reports it cannot reach the server**: confirm `https://your-domain/.well-known/oauth-protected-resource/mcp` returns JSON from outside your network. If it returns the Nojoin web app instead, your edge proxy is not routing `/.well-known/oauth-*` and `/mcp` to the API service.
- **Authorisation page shows "This authorization request is invalid"**: the client's registration may have been removed (for example after a database restore). Remove and re-add the connector so the client re-registers.
- **Connector stopped working after a password change**: that is intentional containment. Reconnect from the assistant to authorise again.
