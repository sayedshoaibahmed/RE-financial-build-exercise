"""
config.py - Load and validate configuration from environment.

SECURITY NOTES:
- Config is loaded from the file named by env var QBO_ENV_FILE (default: .env in project root).
- Secrets (client_id, client_secret) are kept only in memory; never logged or returned as strings.
- This module REFUSES to run if QBO_ENVIRONMENT != "sandbox".
- The OAuth scope is HARDCODED as com.intuit.quickbooks.accounting only; it is not configurable.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from dotenv import dotenv_values

log = logging.getLogger(__name__)

# Hardcoded scope - not configurable per requirements.
QBO_SCOPE = "com.intuit.quickbooks.accounting"

# QBO accounting API version pinned - minorversion 75 is the minimum as of Aug 2025.
# Source: Intuit deprecation notice https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/account
QBO_MINOR_VERSION = "75"

# Intuit token endpoint (sandbox and production share the same endpoint).
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"

# QBO base URL for sandbox.
QBO_SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com/v3/company"
QBO_PRODUCTION_BASE = "https://quickbooks.api.intuit.com/v3/company"


class _SecretStr:
    """Wraps a secret so repr/str never reveal the value."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<SecretStr ***>"

    def __str__(self) -> str:
        return "***"


class Config:
    """Validated, immutable configuration object.

    Raises RuntimeError if QBO_ENVIRONMENT is not 'sandbox'.
    """

    def __init__(self) -> None:
        env_file_path = os.environ.get("QBO_ENV_FILE", ".env")
        raw = dotenv_values(env_file_path)

        self.client_id = _SecretStr(self._require(raw, "QBO_CLIENT_ID"))
        self.client_secret = _SecretStr(self._require(raw, "QBO_CLIENT_SECRET"))
        self.redirect_uri: str = self._require(raw, "QBO_REDIRECT_URI")
        self.expected_realm_id: str = self._require(raw, "QBO_EXPECTED_REALM_ID")

        environment = self._require(raw, "QBO_ENVIRONMENT")
        if environment != "sandbox":
            raise RuntimeError(
                "QBO_ENVIRONMENT must be 'sandbox'. "
                "This server refuses to connect to a non-sandbox environment."
            )
        self.environment: str = environment

        # Scope is hardcoded; not read from env.
        self.scope: str = QBO_SCOPE

    @staticmethod
    def _require(raw: dict, key: str) -> str:
        val = raw.get(key, "").strip()
        if not val:
            raise RuntimeError(f"Required env var {key!r} is missing or empty.")
        return val

    @property
    def base_url(self) -> str:
        # Always sandbox (production check above guarantees this).
        return QBO_SANDBOX_BASE

    def __repr__(self) -> str:
        return (
            f"Config(environment={self.environment!r}, "
            f"redirect_uri={self.redirect_uri!r}, "
            f"client_id=***, client_secret=***)"
        )