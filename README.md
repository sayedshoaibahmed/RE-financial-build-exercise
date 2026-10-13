# RE Financial Services build exercise

One repo, three tasks and an architecture note.

| Task | What it is | Where |
|---|---|---|
| 1. QuickBooks connector | A read-only MCP server that lets Claude Desktop read a QuickBooks Online Sandbox company | `connector\` |
| 2. Monthly trend skill | A Claude skill: scripts compute every number from an Excel P&L and write a digest, an Excel pack and a DRAFT commentary for analyst review | `.claude\skills\monthly-trend-analysis\`, `companies\`, `tests\` |
| 3. Eval suite | Golden cases, commentary grading, a regression baseline and mutation testing for the skill | `evals\` |
| Architecture note | How the pieces fit and why (to be added) | `ARCHITECTURE.md` |

Design and status notes for Task 2 are in `PLAN.md`.

## Repo map

```
connector\                 Task 1 (own tests, own Python environment, own README)
  src\qbo_mcp\             the MCP server and OAuth code
  tests\                   offline tests (mocked HTTP)
  SOURCE.txt               where this copy came from
  .env.example             template for your own Intuit keys (no real values)
.claude\skills\monthly-trend-analysis\
  SKILL.md                 instructions Claude follows
  scripts\trend_cli.py     the command line (run, check, render, list-accounts)
  scripts\trend\           the Python package that does all the arithmetic
  reference\  templates\   rules, glossaries, company/mapping templates
.claude\settings.json      permissions for Claude in this repo
companies\abc_inc\         company.yaml and mapping.yaml for the ABC Inc workbook
data\                      the ABC Inc financial statements workbook
tests\                     unit tests, an independent oracle, golden flags
evals\                     Task 3 (see evals\README.md)
scripts\                   secret_scan.ps1, make_zip.ps1, regen_golden_flags.py
outputs\                   run results (not committed)
```

## Two Python setups

The two parts do not share an environment. Use the right one for each.

| Environment | Used for | Python | Install from |
|---|---|---|---|
| `.venv` (repo root) | the skill, the evals, the root tests | 3.12 here (3.11 also works) | `requirements-dev.txt` |
| `connector\.venv` | the QuickBooks server and its tests | 3.11 or newer | `connector\pyproject.toml` and `connector\requirements.txt` |

Both `.venv` folders are gitignored.

## Quick start (PowerShell, from the repo root)

Set up the root environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Check the ABC Inc workbook reconciles (no credentials needed):

```powershell
.\.venv\Scripts\python.exe .claude\skills\monthly-trend-analysis\scripts\trend_cli.py check --company abc_inc
```

Output ends with `reconciliation: OK (180 controls, 0 failed)`.

Run the analysis (writes to `outputs\abc_inc\...`):

```powershell
.\.venv\Scripts\python.exe .claude\skills\monthly-trend-analysis\scripts\trend_cli.py run --company abc_inc
```

Run the root tests, then the evals:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe evals\run_evals.py
```

Set up and test the connector (separate environment, no credentials needed for the tests):

```powershell
cd connector
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q
cd ..
```

Note: `connector\pyproject.toml` lists only the runtime packages. The test packages
(pytest, pytest-asyncio, respx) come from `connector\requirements.txt`, so run both installs.

Optional extras: `python evals\run_mutants.py` (mutation testing, about 5 minutes) and
`powershell -File scripts\secret_scan.ps1` (secret scan).

## What is tested and how

| Claim | Status | Evidence (raw output, 2026-10-13) |
|---|---|---|
| Root unit tests (skill, adapter, metrics, flags, grounding, CLI, permissions) | TESTED | `762 passed in 159.52s`, run from the repo root |
| Root pytest does not collect `connector\` | TESTED | `pytest --collect-only` lists 0 items containing "connector" |
| Eval suite: 11 golden cases, 3 extra cases, commentary grading, guards | TESTED | `26/26 passed (313 checks, 0 failed)`, baseline: no differences |
| Mutation testing: 13 mutants | TESTED (last run recorded in `evals\README.md`) | 12 killed, 1 accepted survivor (an equivalent mutant) |
| Connector offline tests (mocked HTTP, from the copy in `connector\`) | TESTED | `121 passed in 3.31s` using `connector\.venv` |
| Connector against a live QuickBooks Sandbox | Done by the author on 2026-10-12 through Claude Desktop; reviewers have not repeated it | see `connector\README.md` |
| Balance sheet as-of values from the connector vs the QuickBooks screen | UNVERIFIED | see `connector\README.md` |

Why two pytest runs: the root `pytest.ini` limits collection to `tests\` and skips `connector`
and `evals`, so the root count (762) and the connector count (121) never overlap.

## Using the QuickBooks server

The server is meant to run under Claude Desktop (stdio). `connector\README.md` describes the
setup: create a `.env` outside the repo from `connector\.env.example`, run `qbo-mcp-auth login`
once, then add an `mcpServers` entry to the Claude Desktop config pointing at
`connector\.venv\Scripts\python.exe` with args `-m qbo_mcp.server`.

Credentials are not in this repo. Reviewers supply their own Intuit sandbox keys. The server
refuses to start unless the environment is `sandbox`. It exposes four read-only tools.

## More documentation

- `evals\README.md` - the eval suite: cases, grading, baseline, mutation testing.
- `ARCHITECTURE.md` - architecture note (to be added).
- `connector\README.md` - the QuickBooks server: setup, tests, security design.
- `PLAN.md` - design and status for the trend skill.

## Known limits

- Permissions are a second layer, not a sandbox. `.claude\settings.json` denies some edits, but it is not a security boundary. (UNTESTED as a boundary.)
- The manifest guards careless edits only. It does not stop a deliberate change. (TESTED for careless edits; UNTESTED against deliberate ones.)
- Chat answers are guided by an instruction in `SKILL.md`. Code does not check them. Only the written commentary is checked by the grounding rules.
- The QuickBooks adapter for the trend skill is not built. The skill reads Excel only, so eval case G10 (QBO parity) is not built either. The connector and the skill are not wired together.
- Reuse on a second real company is UNTESTED. Two synthetic companies are tested (evals G06 and G09), not a real second workbook.
- Live prose quality is not graded. The suite checks the mechanics of commentary (numbers, banner, banned words, length), not whether it reads well.
- Connector secrets are plaintext files protected only by Windows user permissions (see `connector\README.md`).
