"""
server.py - MCP server exposing four read-only QuickBooks Online tools.

Tools:
  - get_company_info_tool
  - get_profit_and_loss_tool
  - get_balance_sheet_tool
  - list_accounts_tool

All tools are READ-ONLY. No write tools exist. The accounting API scope
(com.intuit.quickbooks.accounting) permits writes; read-only is enforced
by the qbo_client module (GET-only, path allowlist), not by Intuit.

Startup check: the stored token realm_id must match QBO_EXPECTED_REALM_ID.
If it does not, the server raises and refuses to start.

On auth errors (InvalidGrantError, 401): tools return a structured error
telling the user to run 'qbo-mcp-auth login'. No silent retries on auth errors.
"""
from __future__ import annotations

import logging

from mcp.server import MCPServer

from qbo_mcp.auth.flow import InvalidGrantError
from qbo_mcp.config import Config
from qbo_mcp.qbo_client import (
    get_balance_sheet,
    get_company_info,
    get_profit_and_loss,
    list_accounts,
)
from qbo_mcp.startup_check import _check_realm_at_startup  # noqa: F401 (re-exported for tests)

log = logging.getLogger(__name__)

mcp = MCPServer("qbo-mcp")


def _handle_auth_error(exc: Exception) -> dict:
    """Return a structured error dict on auth failure."""
    return {
        "error": "authentication_required",
        "message": str(exc),
        "action": "Run 'qbo-mcp-auth login' to re-authenticate.",
    }


@mcp.tool()
def get_company_info_tool() -> dict:
    """Fetch basic company information from QuickBooks Online (read-only)."""
    config = Config()
    try:
        return get_company_info(config)
    except (InvalidGrantError, RuntimeError) as exc:
        if "Unauthorized" in str(exc) or "login" in str(exc).lower():
            return _handle_auth_error(exc)
        raise


@mcp.tool()
def get_profit_and_loss_tool(
    start_date: str = "",
    end_date: str = "",
) -> dict:
    """
    Fetch the Profit and Loss report from QuickBooks Online (read-only).

    Args:
        start_date: Report start date in YYYY-MM-DD format (optional).
        end_date:   Report end date in YYYY-MM-DD format (optional).
    """
    config = Config()
    try:
        return get_profit_and_loss(
            config,
            start_date=start_date or None,
            end_date=end_date or None,
        )
    except (InvalidGrantError, RuntimeError) as exc:
        if "Unauthorized" in str(exc) or "login" in str(exc).lower():
            return _handle_auth_error(exc)
        raise


@mcp.tool()
def get_balance_sheet_tool(as_of_date: str = "") -> dict:
    """
    Fetch the Balance Sheet report from QuickBooks Online (read-only).

    Args:
        as_of_date: Report end date in YYYY-MM-DD format (optional).
                    Must be a real calendar date; date macro strings are NOT accepted.
    """
    config = Config()
    try:
        return get_balance_sheet(config, as_of_date=as_of_date or None)
    except (InvalidGrantError, RuntimeError) as exc:
        if "Unauthorized" in str(exc) or "login" in str(exc).lower():
            return _handle_auth_error(exc)
        raise


@mcp.tool()
def list_accounts_tool(
    start_position: int = 1,
    max_results: int = 100,
) -> dict:
    """
    List accounts from QuickBooks Online (read-only).

    The query is fixed as 'SELECT * FROM Account'. Only paging parameters
    may be supplied.

    Args:
        start_position: First record to return (1-10000, default 1).
        max_results:    Maximum records to return (1-1000, default 100).
    """
    config = Config()
    try:
        return list_accounts(config, start_position=start_position, max_results=max_results)
    except (InvalidGrantError, RuntimeError) as exc:
        if "Unauthorized" in str(exc) or "login" in str(exc).lower():
            return _handle_auth_error(exc)
        raise




def main() -> None:
    """Entry point for the MCP server (stdio transport)."""
    logging.basicConfig(level=logging.INFO)
    config = Config()
    _check_realm_at_startup(config)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()