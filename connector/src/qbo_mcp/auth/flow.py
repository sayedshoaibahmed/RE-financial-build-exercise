"""
auth/flow.py - OAuth 2.0 Authorization Code flow for QuickBooks Online.

DESIGN NOTES:
- The auth module POSTs to Intuit's token endpoint (oauth.platform.intuit.com).
  This is required by the OAuth 2.0 spec; it is not a write to the accounting API.
- Intuit's accounting scope (com.intuit.quickbooks.accounting) technically allows
  write operations. Read-only is enforced by OUR accounting client, not by Intuit.
- The server opens a one-shot localhost HTTP server on port 8765 to receive the
  OAuth callback. A random `state` parameter prevents CSRF.
- Timeout: if no callback is received within CALLBACK_TIMEOUT_SECONDS, the server
  closes and the login fails.
- Refresh tokens rotate on every use. The newest token is always saved immediately.
- x-include-refresh-token-hard-expires-in: true is sent on every token request to
  obtain the refresh token hard expiry.
  Source: Intuit docs https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0
"""
from __future__ import annotations

import base64
import http.server
import logging
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from qbo_mcp.config import Config, QBO_AUTH_URL, QBO_TOKEN_URL
from qbo_mcp.token_store import TokenData, load_tokens, save_tokens

log = logging.getLogger(__name__)

CALLBACK_TIMEOUT_SECONDS = 120
CALLBACK_PORT = 8765


class AuthError(Exception):
    """Raised for authentication failures that require user action."""


class InvalidGrantError(AuthError):
    """Raised when the refresh token is invalid or expired (invalid_grant)."""


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    """Build the Basic Authorization header for the token endpoint."""
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {encoded}"


def exchange_code(
    config: Config,
    code: str,
    realm_id: str,
    http_client: Optional[httpx.Client] = None,
) -> TokenData:
    """
    Exchange an authorization code for tokens.

    Posts to Intuit's token endpoint (not to the accounting API).
    Saves the resulting tokens atomically to disk.
    """
    own_client = http_client is None
    client = http_client or httpx.Client()
    try:
        resp = client.post(
            QBO_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(
                    config.client_id.get_secret_value(),
                    config.client_secret.get_secret_value(),
                ),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-include-refresh-token-hard-expires-in": "true",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            timeout=30,
        )
    finally:
        if own_client:
            client.close()

    if resp.status_code != 200:
        # Never log the body; it may contain tokens.
        raise AuthError(f"Token exchange failed: HTTP {resp.status_code}")

    return _parse_and_save_token_response(resp.json(), realm_id)


def refresh_access_token(
    config: Config,
    td: TokenData,
    http_client: Optional[httpx.Client] = None,
) -> TokenData:
    """
    Refresh the access token using the stored refresh token.

    On invalid_grant: raises InvalidGrantError (caller must prompt re-login).
    On network errors: caller handles retry with backoff.
    """
    own_client = http_client is None
    client = http_client or httpx.Client()
    try:
        resp = client.post(
            QBO_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(
                    config.client_id.get_secret_value(),
                    config.client_secret.get_secret_value(),
                ),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-include-refresh-token-hard-expires-in": "true",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": td.refresh_token,
            },
            timeout=30,
        )
    finally:
        if own_client:
            client.close()

    if resp.status_code == 400:
        body = resp.json()
        if body.get("error") == "invalid_grant":
            raise InvalidGrantError(
                "Refresh token is invalid or expired. Run 'qbo-mcp-auth login' to re-authenticate."
            )
        raise AuthError(f"Token refresh failed: HTTP {resp.status_code}")

    if resp.status_code != 200:
        raise AuthError(f"Token refresh failed: HTTP {resp.status_code}")

    return _parse_and_save_token_response(resp.json(), td.realm_id)


def _parse_and_save_token_response(body: dict, realm_id: str) -> TokenData:
    """
    Parse an Intuit token response, compute absolute expiry timestamps, and save.

    Fields used:
    - expires_in: access token lifetime in seconds (typically 3600).
    - x_refresh_token_expires_in: refresh token remaining lifetime in seconds.
    - x_refresh_token_hard_expires_in: hard expiry (if header sent). not tested
      whether this field appears in all account types; we fall back to
      x_refresh_token_expires_in if absent.
    """
    now = time.time()
    access_expires_in = int(body.get("expires_in", 3600))
    refresh_expires_in = int(
        body.get("x_refresh_token_hard_expires_in")
        or body.get("x_refresh_token_expires_in", 8640000)  # 100 days default
    )

    td = TokenData(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        access_token_expires_at=now + access_expires_in,
        refresh_token_expires_at=now + refresh_expires_in,
        realm_id=realm_id,
        last_refreshed_at=now,
    )
    save_tokens(td)
    log.info("Tokens saved (access_token=<REDACTED>, realm_id=%s)", realm_id)
    return td


# ---------------------------------------------------------------------------
# One-shot callback server
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles exactly one OAuth callback GET request."""

    result: Optional[dict] = None
    error: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        error = params.get("error", [None])[0]
        if error:
            _CallbackHandler.error = error
            self._respond("Login failed: " + error)
            return

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        realm_id = params.get("realmId", [None])[0]

        if not code or not state or not realm_id:
            _CallbackHandler.error = "Missing parameters in callback"
            self._respond("Missing parameters in callback.")
            return

        _CallbackHandler.result = {"code": code, "state": state, "realm_id": realm_id}
        self._respond("Authentication successful! You may close this window.")

    def _respond(self, message: str) -> None:
        body = message.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        # Suppress default access log - it could inadvertently log the auth code.
        pass


def login(config: Config) -> TokenData:
    """
    Start the authorization-code flow.

    1. Generate a random state parameter.
    2. Build the Intuit authorization URL.
    3. Open the browser.
    4. Start a one-shot localhost server on port 8765.
    5. Wait for callback (up to CALLBACK_TIMEOUT_SECONDS).
    6. Verify state.
    7. Exchange code for tokens.
    """
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": config.client_id.get_secret_value(),
        "response_type": "code",
        "scope": config.scope,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    auth_url = f"{QBO_AUTH_URL}?{urlencode(params)}"

    _CallbackHandler.result = None
    _CallbackHandler.error = None

    server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    server.timeout = CALLBACK_TIMEOUT_SECONDS

    print(f"Opening browser for QuickBooks authorization...")
    print(f"If it does not open automatically, visit:\n  {auth_url}")
    webbrowser.open(auth_url)

    deadline = time.time() + CALLBACK_TIMEOUT_SECONDS
    while time.time() < deadline:
        server.handle_request()
        if _CallbackHandler.result or _CallbackHandler.error:
            break

    server.server_close()

    if _CallbackHandler.error:
        raise AuthError(f"Authorization failed: {_CallbackHandler.error}")

    if not _CallbackHandler.result:
        raise AuthError("Authorization timed out. Run 'qbo-mcp-auth login' again.")

    cb = _CallbackHandler.result
    if cb["state"] != state:
        raise AuthError("State mismatch - possible CSRF attack. Login aborted.")

    return exchange_code(config, cb["code"], cb["realm_id"])