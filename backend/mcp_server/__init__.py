"""Nojoin MCP connector: a read-only Model Context Protocol server.

Mounted at ``/mcp`` inside the API process. Authentication is OAuth 2.1
bearer tokens minted by :mod:`backend.api.services.oauth_service`; the
tools expose transcripts, meeting notes, and recording metadata scoped to
the authenticated user.
"""

from backend.mcp_server.server import (
    NormaliseMcpMountPathMiddleware,
    build_mcp_asgi_app,
    mcp_session_manager_context,
)

__all__ = [
    "NormaliseMcpMountPathMiddleware",
    "build_mcp_asgi_app",
    "mcp_session_manager_context",
]
