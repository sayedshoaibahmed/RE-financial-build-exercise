# qbo-mcp

A **read-only** [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server
that connects a **QuickBooks Online Sandbox** to Claude Desktop.

> **Sandbox only.** The server refuses to start if `QBO_ENVIRONMENT` is not `"sandbox"`.

---

## Works / Does not work

| Feature | Status | Backing test(s) |
|---------|--------|-----------------|
| OAuth 2.0 authorization-code flow (`auth login`) | **PARTLY TESTED (offline)** | Token exchange/refresh TESTED offline (`TestTokenRotation`, `TestFlowPostsToTokenURLOnly`); callback server and `state` check not unit-tested; live login UNTESTED |
| Token refresh with rotation | **TESTED (offline, mocked)** | `TestTokenRotation::test_refresh_saves_new_token` |
| `auth status` command | **TESTED (offline)** | `TestAuthStatusNeverPrintsTokens::test_status_output_does_not_contain_access_token`, `::test_status_prints_realm_id` |
| `get_company_info` tool | **TESTED offline (mocked, URL shape and params checked); live check done by the user separately** | `TestURLShape::test_companyinfo_url_includes_realm_suffix`; `TestTransportLevelGetOnly::test_tool_issues_only_get[get_company_info_tool-...]`; `TestFaultErrorMessage::test_error_contains_fault_message` |
| `get_profit_and_loss` tool | **TESTED offline (mocked, URL shape and params checked); live check done by the user separately** | `TestURLShape::test_profit_and_loss_url_shape`; `TestURLShapeParams::test_profit_and_loss_sends_start_and_end_date_params`; `TestDateValidation::test_pnl_invalid_start_date_no_http`, `::test_pnl_injection_attempt_no_http`, `::test_pnl_start_after_end_no_http`; `TestTransportLevelGetOnly::test_tool_issues_only_get[get_profit_and_loss_tool-...]` |
| `get_balance_sheet` tool | **TESTED offline (mocked URL shape, start_date+end_date params, and period guard); as-of-date values TESTED (one date): on 2026-10-13, accrual basis, sandbox company, Total Assets 23,436.29, Total Liabilities 31,131.33 and Total Equity -7,695.04 matched the QuickBooks Balance Sheet screen, checked by hand through Claude Desktop, not repeated by reviewers** | `TestURLShapeParams::test_balance_sheet_sends_start_and_end_date_not_date_macro`; `TestReportPeriodGuard::test_mismatched_end_period_raises_and_returns_no_data`, `::test_matching_reply_returns_data`, `::test_missing_header_raises`, `::test_no_dates_requested_no_guard_and_no_date_params`; `TestURLShape::test_balance_sheet_url_shape`; `TestDateValidation::test_balance_sheet_invalid_date_no_http`; `TestTransportLevelGetOnly::test_tool_issues_only_get[get_balance_sheet_tool-...]` |
| `list_accounts` tool | **TESTED offline (mocked, URL shape and params checked); live check done by the user separately** | `TestURLShape::test_list_accounts_url_shape_and_select_param`; `TestTransportLevelGetOnly::test_tool_issues_only_get[list_accounts_tool-...]` |
| `invalid_grant` raises once, no retry | **TESTED (offline)** | `TestInvalidGrantNoSilentRetry::test_invalid_grant_raises_immediately` |
| GET-only enforcement | **TESTED (transport-level + mutant)** | `TestTransportLevelGetOnly::test_tool_issues_only_get[*]`; mutant caught by `TestPathAllowlist::test_qbo_get_disallowed_path_raises_before_http` |
| Path allowlist | **TESTED (offline + mutant)** | `TestPathAllowlist::test_qbo_get_disallowed_path_raises_before_http` (zero mock calls); `::test_disallowed_path_raises`; `::test_delete_path_raises`; `TestURLShape::test_companyinfo_arbitrary_suffix_still_rejected` |
| Date validation (YYYY-MM-DD; injection; range) | **TESTED (offline)** | `TestDateValidation::test_word_today_rejected`, `::test_invalid_month_rejected`, `::test_injection_attempt_rejected`, `::test_start_after_end_rejected`, `::test_pnl_*_no_http`, `::test_balance_sheet_*_no_http` (all record zero HTTP calls) |
| Non-200/non-retryable error includes Fault detail | **TESTED (offline)** | `TestFaultErrorMessage::test_error_contains_fault_message`, `::test_400_is_not_retried`, `::test_error_does_not_contain_bearer_keyword` |
| Response-period guard (`ProfitAndLoss` & `BalanceSheet`) | **TESTED (offline + mutant)** | `TestReportPeriodGuard::test_mismatched_end_period_raises_and_returns_no_data`, `::test_matching_reply_returns_data`, `::test_missing_header_raises`, `::test_no_dates_requested_no_guard_and_no_date_params` |
| SELECT-only check in `qbo_get` (`path=query`) | **TESTED (offline)** | `TestQueryPathValidation::test_missing_params_raises`, `::test_missing_query_key_raises`, `::test_empty_query_raises`, `::test_whitespace_only_query_raises`, `::test_non_select_query_raises`, `::test_insert_query_raises` |
| Fixed query for `list_accounts` | **TESTED (offline)** | `TestFixedQuery::test_list_accounts_uses_fixed_base_query` |
| Paging bounds enforced | **TESTED (offline)** | `TestPagingBounds::test_start_position_too_low_raises`, `::test_max_results_too_high_raises`, etc. |
| Exactly 4 tools registered, names match design | **TESTED (runtime)** | `TestToolRegistration::test_exactly_four_tools`, `::test_tool_names_match_design`, `::test_no_extra_tools` |
| Auth POSTs only to `QBO_TOKEN_URL` (not accounting API) | **TESTED (offline)** | `TestFlowPostsToTokenURLOnly::test_exchange_code_posts_to_token_url`, `::test_refresh_access_token_posts_to_token_url` |
| `QBO_TOKEN_URL` host = `oauth.platform.intuit.com` (https) | **TESTED (offline)** | `TestAuthURLConstants::test_token_url_host`, `::test_token_url_path` |
| `QBO_AUTH_URL` host = `appcenter.intuit.com` (https) | **TESTED (offline)** | `TestAuthURLConstants::test_auth_url_host`, `::test_auth_url_path` |
| Prompt-injection containment | **TESTED (offline)** | `TestPromptInjection::test_injection_account_name_is_sanitised_and_wrapped` |
| Realm ID mismatch refusal | **TESTED (offline)** | `TestRealmMismatch::test_per_request_realm_mismatch_raises`, `::test_startup_realm_mismatch_raises` |
| Production env refusal | **TESTED (offline)** | `TestProductionRefusal::test_raises_when_environment_is_production` |
| Scope hardcoded | **TESTED (offline)** | `TestScope::test_scope_is_hardcoded_accounting_only`, `::test_scope_not_configurable_from_env` |
| Atomic token write | **TESTED (offline)** | `TestAtomicWrite::test_save_tokens_uses_os_replace` |
| Secrets never in logs | **TESTED (offline)** | `TestSecretsNotInLogs::test_config_repr_does_not_leak_secrets`, `::test_secret_str_repr_redacted`, `::test_token_store_redact_function` |
| Token path under `%LOCALAPPDATA%` | **TESTED (offline)** | `TestTokenPath::test_token_dir_under_localappdata`, `::test_token_file_under_localappdata` |
| `minorversion=75` in every request | **TESTED (offline)** | `TestMinorVersion::test_minor_version_sent_in_every_get` |
| Live QBO sandbox connection (all 4 tools via Claude Desktop) | **LIVE-TESTED by the user on Oct 12 2026 against Sandbox Company US b44a via Claude Desktop** | get_company_info, get_profit_and_loss, get_balance_sheet, list_accounts; 'create customer' refused (no write tool exists). Profit and loss Net Income matched the QuickBooks UI (user-reported); balance sheet values TESTED (one date): on 2026-10-13, accrual basis, sandbox company, Total Assets 23,436.29, Total Liabilities 31,131.33 and Total Equity -7,695.04 matched the QuickBooks Balance Sheet screen, checked by hand through Claude Desktop, not repeated by reviewers |
| Token rotation under Claude Desktop | **LIVE-OBSERVED** | tokens.json rewritten after refresh, no private copy in the package folder |
| `x_refresh_token_hard_expires_in` field presence | **UNVERIFIED** | Third-party source only; not seen in a live Intuit response |
| `minorversion=75` validity | **UNVERIFIED** | Intuit’s blog confirms 75 is the minimum and default; whether a newer version exists is UNVERIFIED |

### Found live, missed by offline tests

- **(a) `companyinfo` needs `/companyinfo/<realmId>`** — path was `.../companyinfo`; live returned HTTP 400 "Unsupported Operation"; correct path is `.../companyinfo/<realmId>`. Fixed in `get_company_info` / `qbo_get`. Backed by `TestURLShape::test_companyinfo_url_includes_realm_suffix`.
- **(b) BalanceSheet `date_macro` takes named periods only** — live returned HTTP 400 "Invalid Enumeration" when `date_macro=2026-10-12` was sent; `date_macro` accepts only named macro strings (e.g. `This Year`), not YYYY-MM-DD values.
- **(c) BalanceSheet ignores end_date alone and silently returns today's year-to-date** — mitigation: `start_date` + `end_date` together and a response-period guard (`_check_report_period`). Backed by `TestURLShapeParams::test_balance_sheet_sends_start_and_end_date_not_date_macro` and `TestReportPeriodGuard`. Balance sheet as-of-date values TESTED (one date): on 2026-10-13, accrual basis, sandbox company, Total Assets 23,436.29, Total Liabilities 31,131.33 and Total Equity -7,695.04 matched the QuickBooks Balance Sheet screen, checked by hand through Claude Desktop, not repeated by reviewers.

---

## Known limits

- **Refresh-token deadline:** Stored expiry shown is the hard cap; the 100-day non-use window is an estimate based on Intuit's documented non-use rule, not read from an Intuit API response.
- **Plaintext credentials & tokens:** Tokens (`tokens.json`) and `.env` are plaintext files protected only by per-user Windows NTFS permissions. They are NOT encrypted at rest and NOT protected from malware or software running under the same user session.
- **Read-only enforcement:** Read-only is enforced solely by our client code (`qbo_get` GET-only, path allowlist, fixed SELECT query), NOT by Intuit permissions. The Intuit OAuth accounting scope (`com.intuit.quickbooks.accounting`) technically permits writes; additionally, the Intuit Developer portal locked the payments scope on.
- **Account ordering:** `list_accounts` uses a fixed `SELECT * FROM Account` without an `ORDERBY` clause, so record order is not guaranteed across pages.
- **BalanceSheet historical bounds:** QuickBooks returns `NoReportData` for periods before the sandbox company existed.

---

## Mutant checks

| Mutation | Failing test(s) | Verification |
|----------|-----------------|--------------|
| Period guard call removed from `qbo_get` | `TestReportPeriodGuard::test_mismatched_end_period_raises_and_returns_no_data`, `::test_missing_header_raises` | Catches silent fallback to mismatched report periods |
| Balance sheet `start_date` dropped (sending only `end_date`) | `TestURLShapeParams::test_balance_sheet_sends_start_and_end_date_not_date_macro`, `TestReportPeriodGuard::*` | Catches omission of start_date causing Intuit to ignore end_date |
| Allowlist call `_check_path_allowed` removed from `qbo_get` | `TestPathAllowlist::test_qbo_get_disallowed_path_raises_before_http` | Catches regression in path enforcement before network I/O |

---

## Verification results (pre-implementation research)

| Claim | Source | Status |
|-------|--------|--------|
| MCP SDK package: `mcp==2.3.0`, Python ≥ 3.10, class `MCPServer` | PyPI JSON API (live read) | **VERIFIED** |
| QBO accounting API minor version: `75` (current, mandatory since Aug 2025) | Intuit deprecation notice (multiple sources) | **VERIFIED (sources cited)** |
| Refresh token response field: `x_refresh_token_expires_in` (seconds) | Intuit OAuth2 docs | **VERIFIED** |
| Hard expiry field: `x_refresh_token_hard_expires_in` (with request header `x-include-refresh-token-hard-expires-in: true`) | Third-party source only | **UNVERIFIED (third-party source, not seen in a live Intuit response)** |
| Claude Desktop config path on Windows: `%APPDATA%\Claude\claude_desktop_config.json` | Multiple sources | **VERIFIED** |
| Refresh tokens: max 5 years, expire after 100 days non-use, rotate ~every 24 h | User confirmed | **USER-CONFIRMED** |

---

## Security design

### Credential handling

- Secrets (`QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`) are loaded into `_SecretStr` objects.
  `repr()` and `str()` of `_SecretStr` return `***` — never the value.
- Secrets are never written to logs, error messages, or exception text.
- The `_redact()` function in `token_store.py` strips token patterns from any
  string before logging.

### Token storage

Tokens live in:
```
%LOCALAPPDATA%\qbo-mcp\tokens.json
```

**Protection:** NTFS permissions set by `icacls /inheritance:r /grant:r <USER>:(OI)(CI)F`
on first directory creation. This restricts access to the current Windows user account.

**Honest limitations:**
- This is **NOT** 0600 in the Unix sense. Windows does not enforce `chmod 0600`.
- Tokens are stored in **plaintext JSON**. They are NOT encrypted at rest.
- Encryption at rest would require **BitLocker** (disk-level) or **Windows DPAPI**
  (`CryptProtectData`). **This implementation uses neither.**
- If an attacker has access to the current user's session (e.g., malware running
  as the same user), they can read the token file regardless of NTFS permissions.

### Read-only guarantee

- The auth module POSTs to `oauth.platform.intuit.com` (the token endpoint).
  This is required by OAuth 2.0 and is **not** a write to the accounting API.
- Intuit's `com.intuit.quickbooks.accounting` scope technically permits write operations.
- **Read-only is enforced by our code:**
  - `qbo_get()` issues only HTTP GET.
  - Only 4 paths are on the allowlist (`companyinfo`, `reports/ProfitAndLoss`,
    `reports/BalanceSheet`, `query`).
  - `list_accounts` uses a hardcoded `SELECT *` query.
  - No tool exposes any write path.

### Alternatives considered

The brief lists three ways to connect: a native connector, an open-source MCP server you deploy, or a custom MCP built on the QuickBooks API. **Chosen: custom.**

Criteria:
- Read-only enforced in code. The Intuit accounting scope allows writes, so the scope cannot give read-only.
- Sandbox-only enforced. The server refuses to start unless the environment is `sandbox`.
- Small enough to audit: four read-only tools, covered by offline tests.

Not chosen: a native or hosted connector, because its code cannot be inspected or tested here, so read-only and sandbox-only could not be proved. An open-source server, because it would need a review of someone else's code that handles OAuth tokens, and many such servers include write tools (general statement, UNVERIFIED for any specific project).

I compared the options on how well they fit these criteria and chose on that basis; I did not install or test the other options.

### Connection choice

**OAuth 2.0 Authorization Code flow** was chosen over alternatives because:

- Intuit requires it for production apps accessing company data.
- It does not embed long-lived secrets in the request URL.
- The `state` parameter prevents CSRF.
- Rotating refresh tokens limit blast radius if a token file is compromised.

---

## Setup

### 1. Prerequisites

- Python 3.11.9 (tested)
- Git 2.53

### 2. Clone and install

```powershell
git clone <repo> C:\Users\shoai\projects\qbo-mcp
cd C:\Users\shoai\projects\qbo-mcp
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
pip install -e .
```

### 3. Create your .env

The `.env` file lives **OUTSIDE** the repository at `%LOCALAPPDATA%\qbo-mcp\.env`. `QBO_ENV_FILE` points to it.
Setup creates the directory and copies `.env.example` there:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\qbo-mcp"
Copy-Item .env.example "$env:LOCALAPPDATA\qbo-mcp\.env"
notepad "$env:LOCALAPPDATA\qbo-mcp\.env"
```

Keys:
```
QBO_CLIENT_ID=<your sandbox client id>
QBO_CLIENT_SECRET=<your sandbox client secret>
QBO_REDIRECT_URI=http://localhost:8765/callback
QBO_ENVIRONMENT=sandbox
QBO_EXPECTED_REALM_ID=<your sandbox company realm id>
```

**The `.env` file lives outside the repo.** Never put `.env` inside the project folder or commit credentials.

### 4. Authenticate

```powershell
qbo-mcp-auth login
```

This opens your browser, completes the OAuth flow, and saves tokens to
`%LOCALAPPDATA%\qbo-mcp\tokens.json`.

### 5. Check token status

```powershell
qbo-mcp-auth status
```

### 6. Configure Claude Desktop

Claude Desktop (Microsoft Store version) reads config from:
`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
(NOT `%APPDATA%\Claude\claude_desktop_config.json`; note this package name may differ per machine).

The entry uses the absolute venv python path, args `-m qbo_mcp.server`, and `QBO_ENV_FILE` with the absolute path:

```json
{
  "mcpServers": {
    "qbo-sandbox": {
      "command": "C:\\Users\\shoai\\projects\\qbo-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "qbo_mcp.server"],
      "env": {
        "QBO_ENV_FILE": "C:\\Users\\shoai\\AppData\\Local\\qbo-mcp\\.env"
      }
    }
  }
}
```

Fully quit and restart Claude Desktop after saving.

---

## Running tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

**TESTED (offline + runtime, mocked HTTP):** 121 tests, 121 passed.

---

## DESIGN NOT BUILT — Team hosting, 10 clients

> This section describes design only. Nothing below is implemented.

### Multi-client architecture

For a team of 10 sharing one QBO company:

1. **Central MCP server** running as a Windows Service or Docker container
   (accessible over the network, not stdio).
2. **One set of QBO credentials** registered as a single Intuit app; all clients
   share the same realm ID.
3. **Token store**: Replace the per-user file with a shared secret store
   (e.g., Azure Key Vault, HashiCorp Vault, or AWS Secrets Manager) so token
   rotation by one process is visible to all replicas.
4. **Transport**: Replace `stdio` with Streamable HTTP (MCP 2.0 supports this).
   Each Claude Desktop instance points to `https://internal-host:8443/mcp`.
5. **Authentication between Claude and the MCP server**: Add mTLS or a shared
   API key header so only authorised clients can call the MCP endpoint.
6. **Token refresh coordination**: Use a distributed lock (e.g., Redis `SET NX`)
   so only one replica refreshes at a time; others wait and then read the updated
   token from the shared store.
7. **Audit logging**: Log every tool call (realm ID, tool name, timestamp, user identity)
   to a structured log sink (e.g., Azure Monitor). No token values in logs.
8. **Rate limit handling**: QBO applies per-app rate limits. A token-bucket or
   fixed-window counter shared across replicas prevents exceeding the limit.
   Exact limits are UNVERIFIED and not stated here.
9. **High availability**: Run two or more replicas behind a load balancer.
   Stateless by design (all state in shared token store).
10. **Secret rotation**: Rotate `QBO_CLIENT_SECRET` on a schedule via the Intuit
    Developer portal; update the secret store without downtime using blue/green
    credential rotation.