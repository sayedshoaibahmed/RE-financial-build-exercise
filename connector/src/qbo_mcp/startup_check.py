"""
startup_check.py - Realm ID verification run before the MCP server starts.

Isolated from server.py so tests can import _check_realm_at_startup
without triggering the MCPServer / pywin32 import chain.
"""
from __future__ import annotations

import logging

from qbo_mcp.config import Config
from qbo_mcp.token_store import load_tokens

log = logging.getLogger(__name__)


def _check_realm_at_startup(config: Config) -> None:
    """
    Verify the stored token's realm ID matches QBO_EXPECTED_REALM_ID.

    Raises RuntimeError and aborts startup on mismatch.
    Called once at server startup.
    """
    td = load_tokens()
    if td is None:
        # No tokens yet - server can start but tools will fail until login.
        log.warning("No tokens found. Run 'qbo-mcp-auth login' before using tools.")
        return
    if td.realm_id != config.expected_realm_id:
        raise RuntimeError(
            f"Realm ID mismatch at startup: stored={td.realm_id!r}, "
            f"expected={config.expected_realm_id!r}. "
            "Run 'qbo-mcp-auth login' to re-authenticate."
        )