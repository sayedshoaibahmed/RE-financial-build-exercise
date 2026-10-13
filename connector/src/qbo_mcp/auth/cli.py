"""
auth/cli.py - CLI entry points: "auth login" and "auth status".

auth status rules:
- Prints: realm ID, environment, access-token validity, refresh-token expiry
  date and days left, last refresh time, warning if < 30 days left or
  > 90 days since last refresh.
- NEVER prints token values (access_token or refresh_token).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from qbo_mcp.config import Config
from qbo_mcp.token_store import load_tokens
from qbo_mcp.auth.flow import login


def cmd_login() -> None:
    """Start the authorization-code flow interactively."""
    config = Config()
    td = login(config)
    print(f"Login successful. Realm ID: {td.realm_id}")


def cmd_status() -> None:
    """Print token status. Never prints token values."""
    td = load_tokens()
    if td is None:
        print("No tokens found. Run 'qbo-mcp-auth login' first.")
        sys.exit(1)

    now = time.time()
    config = Config()

    print(f"Realm ID:             {td.realm_id}")
    print(f"Environment:          {config.environment}")
    print(f"Access token valid:   {td.access_token_valid()}")

    rt_expiry_dt = datetime.fromtimestamp(td.refresh_token_expires_at, tz=timezone.utc)
    days_left = td.days_until_refresh_expiry()
    print(f"Stored expiry (hard cap, as saved): {rt_expiry_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Days until expiry:                  {days_left:.1f}")

    last_dt = datetime.fromtimestamp(td.last_refreshed_at, tz=timezone.utc)
    days_since = td.days_since_last_refresh()
    print(f"Last refreshed:                     {last_dt.strftime('%Y-%m-%d %H:%M UTC')} ({days_since:.1f} days ago)")

    est_deadline_ts = td.last_refreshed_at + 100 * 86400
    est_deadline_dt = datetime.fromtimestamp(est_deadline_ts, tz=timezone.utc)
    days_until_est = int(round((est_deadline_ts - now) / 86400))
    print(
        f"Estimated 100-day non-use deadline: {est_deadline_dt.strftime('%Y-%m-%d')} "
        "(Intuit's documented non-use rule; our estimate, not read from Intuit)"
    )
    print(f"Days until estimated deadline:      {days_until_est}")

    if days_until_est < 30 or td.refresh_token_expired():
        print(f"WARNING: Token reconnect required ({days_until_est} days remaining or stored expiry passed); run qbo-mcp-auth login to reconnect.")


def main() -> None:
    """Main entry point dispatching 'login' or 'status'."""
    if len(sys.argv) < 2:
        print("Usage: qbo-mcp-auth <login|status>")
        sys.exit(1)
    if sys.argv[1] in ("--help", "-h"):
        print("Usage: qbo-mcp-auth <login|status>")
        sys.exit(0)
    if sys.argv[1] not in ("login", "status"):
        print("Usage: qbo-mcp-auth <login|status>")
        sys.exit(1)
    if sys.argv[1] == "login":
        cmd_login()
    else:
        cmd_status()