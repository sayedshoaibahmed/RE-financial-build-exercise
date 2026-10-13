r"""
token_store.py - Persistent token storage under %LOCALAPPDATA%/qbo-mcp.

SECURITY:
- Tokens are stored in %LOCALAPPDATA%/qbo-mcp, which is per-user by design.
- The folder is restricted to the current user via icacls on first creation.
- Windows does NOT guarantee 0600 semantics; we do NOT claim that.
- Protection is NTFS permissions on a per-user directory.
- Data is NOT encrypted at rest unless BitLocker or Windows DPAPI is used.
  THIS IMPLEMENTATION DOES NOT USE DPAPI. Tokens are stored in plaintext JSON
  under the per-user NTFS-protected folder. See README for the honest assessment.
- Writes are atomic: write to a temp file then os.replace() so two concurrent
  processes cannot corrupt the token file.
- A file lock (filelock) prevents two processes from writing simultaneously.

Token rotation:
- Every refresh response issues a new refresh_token. The old one is invalidated.
- The newest token is ALWAYS saved immediately after a refresh.
- x_refresh_token_expires_in (seconds remaining) is stored as an absolute
  UTC datetime so we can warn when it's near expiry.
- The request header x-include-refresh-token-hard-expires-in: true is sent
  on every token request to obtain x_refresh_token_hard_expires_in.
  Source: Intuit docs https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock

# Token storage directory - per-user Windows profile location.
_TOKEN_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "qbo-mcp"
_TOKEN_FILE = _TOKEN_DIR / "tokens.json"
_LOCK_FILE = _TOKEN_DIR / "tokens.json.lock"


def _ensure_token_dir() -> None:
    """Create the token directory and restrict it to the current user via icacls."""
    if not _TOKEN_DIR.exists():
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        # Restrict to current user only using icacls.
        # icacls removes inherited permissions (inheritance:r) then grants the
        # current user full control. This is the Windows equivalent of chmod 700.
        import subprocess
        username = os.environ.get("USERNAME", "")
        if username:
            subprocess.run(
                [
                    "icacls",
                    str(_TOKEN_DIR),
                    "/inheritance:r",
                    "/grant:r",
                    f"{username}:(OI)(CI)F",
                ],
                check=False,
                capture_output=True,
            )


class TokenData:
    """In-memory representation of the stored OAuth tokens."""

    __slots__ = (
        "access_token",
        "refresh_token",
        "access_token_expires_at",
        "refresh_token_expires_at",
        "realm_id",
        "last_refreshed_at",
    )

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        access_token_expires_at: float,
        refresh_token_expires_at: float,
        realm_id: str,
        last_refreshed_at: float,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.access_token_expires_at = access_token_expires_at
        self.refresh_token_expires_at = refresh_token_expires_at
        self.realm_id = realm_id
        self.last_refreshed_at = last_refreshed_at

    def access_token_valid(self) -> bool:
        # Consider expired 60 s before actual expiry to allow network round-trip.
        return time.time() < self.access_token_expires_at - 60

    def refresh_token_expired(self) -> bool:
        return time.time() >= self.refresh_token_expires_at

    def days_until_refresh_expiry(self) -> float:
        remaining_seconds = self.refresh_token_expires_at - time.time()
        return remaining_seconds / 86400

    def days_since_last_refresh(self) -> float:
        return (time.time() - self.last_refreshed_at) / 86400

    def to_dict(self) -> dict:
        # Tokens written here must NEVER be logged. They are in plaintext JSON.
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_token_expires_at": self.access_token_expires_at,
            "refresh_token_expires_at": self.refresh_token_expires_at,
            "realm_id": self.realm_id,
            "last_refreshed_at": self.last_refreshed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TokenData":
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            access_token_expires_at=float(d["access_token_expires_at"]),
            refresh_token_expires_at=float(d["refresh_token_expires_at"]),
            realm_id=d["realm_id"],
            last_refreshed_at=float(d.get("last_refreshed_at", 0.0)),
        )


_SECRET_PATTERN = re.compile(
    r"(access_token|refresh_token|client_secret|client_id|Bearer)[\":\s]+[A-Za-z0-9._~+/=-]{8,}",
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    """Redact token values and secrets from log strings."""
    return _SECRET_PATTERN.sub(r"\1=<REDACTED>", text)


def load_tokens() -> Optional[TokenData]:
    """Load tokens from disk. Returns None if no token file exists."""
    _ensure_token_dir()
    if not _TOKEN_FILE.exists():
        return None
    with FileLock(str(_LOCK_FILE)):
        with open(_TOKEN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    return TokenData.from_dict(data)


def save_tokens(td: TokenData) -> None:
    """
    Atomically persist tokens to disk under a file lock.

    Atomic write: write to a temp file in the same directory, then
    os.replace() to swap it in. This prevents partial-write corruption.
    Two concurrent callers are serialised by the file lock.
    """
    _ensure_token_dir()
    payload = json.dumps(td.to_dict(), indent=2)
    with FileLock(str(_LOCK_FILE)):
        fd, tmp_path = tempfile.mkstemp(dir=str(_TOKEN_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, str(_TOKEN_FILE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def token_dir() -> Path:
    """Return the token directory path (for tests)."""
    return _TOKEN_DIR


def token_file() -> Path:
    """Return the token file path (for tests)."""
    return _TOKEN_FILE