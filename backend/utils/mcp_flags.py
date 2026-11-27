"""Environment switches for the MCP connector surface."""

import os

MCP_ENABLED_ENV_KEY = "MCP_ENABLED"
_FALSY_FLAG_VALUES = {"0", "false", "no", "off"}


def is_mcp_enabled() -> bool:
    """Master switch for the MCP connector surface. Defaults to enabled.

    When disabled, the OAuth authorization-server endpoints, the discovery
    documents, and the /mcp endpoint all respond 404. Changing the value
    requires an API container restart, like other environment settings.
    """
    raw = os.environ.get(MCP_ENABLED_ENV_KEY)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in _FALSY_FLAG_VALUES


MCP_ANONYMOUS_DISCOVERY_ENV_KEY = "MCP_ANONYMOUS_DISCOVERY"


def is_mcp_anonymous_discovery_enabled() -> bool:
    """Compatibility switch for MCP clients that cannot start OAuth from a 401.

    When enabled (the default), the /mcp endpoint answers a fixed allowlist
    of protocol-bootstrap JSON-RPC methods anonymously: the handshake and
    tool listing succeed without a token, and tool calls return an in-band
    authentication challenge instead of an HTTP error. Every actual
    operation still requires an authenticated user. When disabled, every
    unauthenticated request gets the strict 401 challenge. Has no effect
    while MCP_ENABLED is false, which removes the surface entirely.
    """
    raw = os.environ.get(MCP_ANONYMOUS_DISCOVERY_ENV_KEY)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in _FALSY_FLAG_VALUES
