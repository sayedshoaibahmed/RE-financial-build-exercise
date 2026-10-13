"""
qbo_client.py - HTTP client for the QuickBooks Online Accounting API.

CRITICAL SECURITY DESIGN:
1. GET-ONLY: This function only issues HTTP GET requests. No POST/PUT/DELETE.
   The auth module POSTs to Intuit token endpoint (not the accounting API).
   Intuit accounting scope (com.intuit.quickbooks.accounting) allows writes;
   read-only is enforced HERE by our code, not by Intuit.

2. PATH ALLOWLIST: Only paths in QBO_ALLOWED_PATHS may be requested. Any other
   path raises ValueError immediately, before any network request.

3. RETRY: Retries with exponential backoff on HTTP 429 and 5xx only.
   Auth errors (401, invalid_grant) are NOT retried; the caller receives
   an explicit error telling the user to run 'auth login' again.

4. UNTRUSTED DATA: All response data is sanitised via sanitise.sanitise_dict()
   before being returned to the MCP tool layer.

5. MINOR VERSION: minorversion=75 is appended to every request URL.
   Source: Intuit docs - minorversion 75 is current as of Aug 2025.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from qbo_mcp.auth.flow import InvalidGrantError, refresh_access_token
from qbo_mcp.config import Config, QBO_MINOR_VERSION
from qbo_mcp.sanitise import sanitise_dict, wrap_untrusted_data
from qbo_mcp.token_store import TokenData, load_tokens, save_tokens

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path allowlist - the ONLY paths this client will request.
# ---------------------------------------------------------------------------
QBO_ALLOWED_PATHS = frozenset(
    [
        "companyinfo",
        "reports/ProfitAndLoss",
        "reports/BalanceSheet",
        "query",
    ]
)

# Paging bounds for list_accounts queries.
LIST_ACCOUNTS_MIN_START = 1
LIST_ACCOUNTS_MAX_START = 10_000
LIST_ACCOUNTS_MIN_MAX_RESULTS = 1
LIST_ACCOUNTS_MAX_MAX_RESULTS = 1000

# Fixed base query for list_accounts. The model may only vary paging parameters.
LIST_ACCOUNTS_BASE_QUERY = "SELECT * FROM Account"

# Retry settings (429 and 5xx only).
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


def _check_select_only(query: str) -> None:
    """
    Defence-in-depth: verify the query string starts with SELECT (case-insensitive).

    list_accounts uses a fixed query, so this should never fail in practice.
    It guards against future code paths that might accidentally pass a non-SELECT query.
    """
    stripped = query.strip().upper()
    if not stripped.startswith("SELECT"):
        raise ValueError(
            f"Query must start with SELECT. Got: {query[:50]!r}"
        )


def _check_path_allowed(path: str) -> None:
    """
    Verify the requested path is in QBO_ALLOWED_PATHS.

    Raises ValueError (not a network call) if the path is not on the allowlist.
    """
    if path not in QBO_ALLOWED_PATHS:
        raise ValueError(
            f"Path {path!r} is not in the allowed path list. "
            f"Allowed: {sorted(QBO_ALLOWED_PATHS)}"
        )


def _validate_date(value: Optional[str], field_name: str) -> Optional[str]:
    """
    Validate a date parameter before any HTTP call.

    - Empty string or None means "not provided" -> returns None.
    - Any non-empty value must be EXACTLY a real calendar date in YYYY-MM-DD format.
      datetime.strptime is used so invalid calendar dates (e.g. 2026-13-01) are caught.
    - Anything else (e.g. "Today", "2026-01-01&foo=bar") raises ValueError.

    Raises ValueError BEFORE any network call or token lookup.
    """
    if not value or not value.strip():
        return None
    # Reject if the value has any characters that are not digits or hyphens;
    # this catches injection attempts like "2026-01-01&foo=bar" before strptime.
    stripped = value.strip()
    if len(stripped) != 10 or not all(c.isdigit() or c == "-" for c in stripped):
        raise ValueError(
            f"{field_name!r} must be YYYY-MM-DD (e.g. '2026-01-31'). Got: {stripped!r}"
        )
    try:
        datetime.strptime(stripped, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"{field_name!r} is not a valid calendar date in YYYY-MM-DD format. "
            f"Got: {stripped!r}"
        )
    return stripped


def _validate_date_range(start: Optional[str], end: Optional[str]) -> None:
    """
    For Profit and Loss: if both start_date and end_date are provided,
    start_date must be on or before end_date.

    Raises ValueError BEFORE any network call.
    """
    if start and end:
        if datetime.strptime(start, "%Y-%m-%d") > datetime.strptime(end, "%Y-%m-%d"):
            raise ValueError(
                f"start_date ({start!r}) must be on or before end_date ({end!r})."
            )


def _get_valid_token(config: Config, http_client: Optional[httpx.Client] = None) -> TokenData:
    """
    Return a valid TokenData, refreshing if necessary.

    Raises InvalidGrantError if refresh_token is expired/invalid.
    Raises RuntimeError if no tokens exist.
    """
    td = load_tokens()
    if td is None:
        raise RuntimeError("No tokens found. Run 'qbo-mcp-auth login' first.")

    if td.realm_id != config.expected_realm_id:
        raise RuntimeError(
            f"Realm ID mismatch: stored={td.realm_id!r}, "
            f"expected={config.expected_realm_id!r}. "
            "Run 'qbo-mcp-auth login' to re-authenticate with the correct company."
        )

    if td.refresh_token_expired():
        raise InvalidGrantError(
            "Refresh token has expired. Run 'qbo-mcp-auth login' to re-authenticate."
        )

    if not td.access_token_valid():
        # Refresh the access token. Raises InvalidGrantError on invalid_grant.
        td = refresh_access_token(config, td, http_client)
        # Token rotation: the new refresh token is already saved by refresh_access_token.

    return td


def _extract_fault_detail(resp: "httpx.Response") -> str:
    """
    Extract a short, sanitised error detail from a non-success QBO response.

    Reads Fault.Message and Fault.Detail from Intuit's JSON error body (when present).
    The body is UNTRUSTED DATA: it is passed through sanitise_field and truncated to
    200 characters total.  The Authorization header, tokens, and full response body are
    never included.
    """
    try:
        body = resp.json()
    except Exception:
        return ""

    fault = body.get("Fault") if isinstance(body, dict) else None
    if not isinstance(fault, dict):
        return ""

    errors = fault.get("Error")
    if not isinstance(errors, list) or not errors:
        return ""

    first = errors[0] if isinstance(errors[0], dict) else {}
    message = first.get("Message", "")
    detail = first.get("Detail", "")

    # Sanitise as untrusted data before including in an exception message.
    from qbo_mcp.sanitise import sanitise_field
    combined = " ".join(filter(None, [sanitise_field(message), sanitise_field(detail)]))
    return combined[:200]


_REPORT_PATHS_WITH_PERIOD_GUARD = frozenset([
    "reports/ProfitAndLoss",
    "reports/BalanceSheet",
])


def _check_report_period(path: str, params: Optional[dict], raw_data: Any) -> None:
    """
    Guard against QuickBooks silently ignoring date parameters in report responses.

    Live finding: GET reports/BalanceSheet with only end_date ignores end_date
    and returns today's year-to-date report. get_balance_sheet sends both
    start_date and end_date.

    For reports/ProfitAndLoss and reports/BalanceSheet, when start/end dates were
    requested, compare the response Header.StartPeriod and Header.EndPeriod with
    the requested values. On mismatch or a missing Header raise RuntimeError.
    Do not return the data. Dates in that message are fine; never include tokens
    or headers.
    """
    if path not in _REPORT_PATHS_WITH_PERIOD_GUARD:
        return

    if not params:
        return

    req_start = params.get("start_date")
    req_end = params.get("end_date")

    # If no dates were requested, Intuit's default period applies; no guard is applied.
    if req_start is None and req_end is None:
        return

    header = raw_data.get("Header") if isinstance(raw_data, dict) else None
    if not isinstance(header, dict):
        raise RuntimeError(
            f"QuickBooks returned a report for a different period than requested: "
            f"requested {req_start}..{req_end}, got None..None"
        )

    resp_start = header.get("StartPeriod")
    resp_end = header.get("EndPeriod")

    mismatch = False
    if req_start is not None and resp_start != req_start:
        mismatch = True
    if req_end is not None and resp_end != req_end:
        mismatch = True

    if mismatch:
        raise RuntimeError(
            f"QuickBooks returned a report for a different period than requested: "
            f"requested {req_start}..{req_end}, got {resp_start}..{resp_end}"
        )


def qbo_get(
    config: Config,
    path: str,
    params: Optional[dict] = None,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """
    Issue a GET request to the QBO Accounting API.

    - Only GET is issued. See module docstring for why.
    - path must be in QBO_ALLOWED_PATHS.
    - minorversion=75 is always appended.
    - Retries on 429 / 5xx with exponential backoff.
    - On 401 or invalid_grant: raises without retrying, with a clear message.
    - Response data is sanitised before return.
    """

    _check_path_allowed(path)

    # SELECT-only guard (strict): path=='query' requires params['query'],
    # non-empty, and starting with SELECT (case-insensitive).
    # Raises ValueError BEFORE any network call.
    if path == "query":
        if params is None or "query" not in params:
            raise ValueError(
                "path='query' requires params['query'] to be set. "
                "No params dict was provided."
            )
        query_val = params["query"]
        if not query_val or not str(query_val).strip():
            raise ValueError(
                "path='query' requires a non-empty params['query'] value."
            )
        _check_select_only(str(query_val))


    td = _get_valid_token(config, http_client)

    url_params = dict(params or {})
    url_params["minorversion"] = QBO_MINOR_VERSION

    # Build the actual request path.
    # companyinfo requires the realmId appended: .../companyinfo/<realmId>.
    # The allowlist check above already rejected any path not in QBO_ALLOWED_PATHS,
    # so "companyinfo/anything" is still rejected (it is not in the frozenset).
    # The realm_id here comes from the validated token (checked against
    # QBO_EXPECTED_REALM_ID inside _get_valid_token), so it is trusted.
    if path == "companyinfo":
        request_path = f"companyinfo/{td.realm_id}"
    else:
        request_path = path

    url = f"{config.base_url}/{td.realm_id}/{request_path}"

    own_client = http_client is None
    client = http_client or httpx.Client()

    try:
        for attempt in range(_MAX_RETRIES):
            resp = client.get(
                url,
                params=url_params,
                headers={
                    "Authorization": f"Bearer {td.access_token}",
                    "Accept": "application/json",
                },
                timeout=30,
            )

            if resp.status_code == 200:
                raw = resp.json()
                _check_report_period(path, params, raw)
                sanitised = sanitise_dict(raw)
                return wrap_untrusted_data(sanitised)

            if resp.status_code == 401:
                raise RuntimeError(
                    "QuickBooks returned 401 Unauthorized. "
                    "Run 'qbo-mcp-auth login' to re-authenticate."
                )

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < _MAX_RETRIES - 1:
                    wait = _BACKOFF_BASE * (2 ** attempt)
                    log.warning(
                        "QBO HTTP %s on attempt %d/%d; retrying in %.1fs",
                        resp.status_code,
                        attempt + 1,
                        _MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"QuickBooks returned HTTP {resp.status_code} after {_MAX_RETRIES} retries."
                )

            # Non-200/401/429/5xx: include HTTP status plus Intuit Fault details
            # (when present) so the caller gets a meaningful error without the
            # full response body or any token values.
            fault_detail = _extract_fault_detail(resp)
            raise RuntimeError(
                f"QuickBooks returned HTTP {resp.status_code}."
                + (f" {fault_detail}" if fault_detail else "")
            )
    finally:
        if own_client:
            client.close()

    raise RuntimeError("Exhausted retries without raising.")


# ---------------------------------------------------------------------------
# Named API helpers used by MCP tools
# ---------------------------------------------------------------------------

def get_company_info(config: Config, http_client: Optional[httpx.Client] = None) -> dict:
    """Fetch company info from /companyinfo/<realmId>."""
    return qbo_get(config, "companyinfo", http_client=http_client)


def get_profit_and_loss(
    config: Config,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """
    Fetch Profit and Loss report.

    start_date / end_date: YYYY-MM-DD or None (not provided).
    Empty string is treated as not provided.
    Both are validated BEFORE any HTTP call; start_date must be <= end_date.
    """
    start_date = _validate_date(start_date, "start_date")
    end_date = _validate_date(end_date, "end_date")
    _validate_date_range(start_date, end_date)
    params: dict = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return qbo_get(config, "reports/ProfitAndLoss", params=params, http_client=http_client)


def get_balance_sheet(
    config: Config,
    as_of_date: Optional[str] = None,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """
    Fetch Balance Sheet report.

    as_of_date: YYYY-MM-DD or None (not provided). Empty string is treated as not
    provided. Validated BEFORE any HTTP call.

    Live finding: GET reports/BalanceSheet with only end_date silently ignores
    end_date and returns today's year-to-date report. With BOTH start_date and
    end_date it honours them. When as_of_date is given, both start_date and
    end_date are sent equal to as_of_date. When as_of_date is not given, no date
    params are sent (Intuit's default).
    """
    as_of_date = _validate_date(as_of_date, "as_of_date")
    params: dict = {}
    if as_of_date:
        params["start_date"] = as_of_date
        params["end_date"] = as_of_date
    return qbo_get(config, "reports/BalanceSheet", params=params, http_client=http_client)


def list_accounts(
    config: Config,
    start_position: int = 1,
    max_results: int = 100,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """
    List accounts using a FIXED query string.

    The model may only supply paging parameters. The base query is hardcoded
    as 'SELECT * FROM Account'. Bounds are enforced; a SELECT-only check runs
    as defence in depth.
    """
    # Enforce integer bounds on paging parameters.
    start_position = int(start_position)
    max_results = int(max_results)

    if not (LIST_ACCOUNTS_MIN_START <= start_position <= LIST_ACCOUNTS_MAX_START):
        raise ValueError(
            f"start_position must be between {LIST_ACCOUNTS_MIN_START} and "
            f"{LIST_ACCOUNTS_MAX_START}. Got: {start_position}"
        )
    if not (LIST_ACCOUNTS_MIN_MAX_RESULTS <= max_results <= LIST_ACCOUNTS_MAX_MAX_RESULTS):
        raise ValueError(
            f"max_results must be between {LIST_ACCOUNTS_MIN_MAX_RESULTS} and "
            f"{LIST_ACCOUNTS_MAX_MAX_RESULTS}. Got: {max_results}"
        )

    # Build the full query with paging - the base is fixed; only STARTPOSITION
    # and MAXRESULTS are appended.
    query = f"{LIST_ACCOUNTS_BASE_QUERY} STARTPOSITION {start_position} MAXRESULTS {max_results}"

    # Defence in depth: verify this is still a SELECT query.
    _check_select_only(query)

    return qbo_get(config, "query", params={"query": query}, http_client=http_client)