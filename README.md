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
| `.venv` (repo root) | the skill, the evals, the root tests | 3.12 here (3.11 is UNTESTED) | `requirements-dev.txt` |
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

The eval run takes about 1 to 2 minutes (58 s to 96 s measured on this machine).

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
| Root unit tests (skill, adapter, metrics, flags, grounding, CLI, permissions) | TESTED | `822 passed in 290.53s (0:04:50)`, run from the repo root |
| Root pytest does not collect `connector\` | TESTED | `pytest --collect-only` lists 0 items containing "connector" |
| Eval suite: 11 golden cases, 3 extra cases, commentary grading, guards | TESTED | `26/26 passed (313 checks, 0 failed)`, baseline: no differences |
| Mutation testing: 13 mutants | TESTED (last run recorded in `evals\README.md`) | 12 killed, 1 accepted survivor (an equivalent mutant) |
| Connector offline tests (mocked HTTP, from the copy in `connector\`) | TESTED | `121 passed in 3.31s` using `connector\.venv` |
| Connector against a live QuickBooks Sandbox | Done by the author on 2026-10-12 through Claude Desktop; reviewers have not repeated it | see `connector\README.md` |
| QuickBooks P&L report adapter for the skill (`adapters\qbo_report.py`), record level | TESTED at record level only; NOT wired into `trend_cli.py`, `config.py` or `pipeline.py`, so the skill still runs on Excel only | 42 tests in `tests\unit\test_qbo_adapter.py`; one real sandbox capture (`tests\fixtures\qbo\pnl_2026-09.json`) reconciles to its own Summary rows to the cent; ABC rebuilt as 18 monthly QBO-shape files gives 1,494 of 1,494 records identical to the Excel adapter (source and source_ref excluded). Eval case G10 is not built. The adapter is not mutation-tested (UNTESTED) |
| L3 model-judged commentary review (`evals\l3\`) | Manual, run once, ungated; not part of `run_evals.py`, pytest or the baseline. The result is a model's opinion, not ground truth | 13 drafts (2 good, 9 bad, 2 live), 3 blind judge passes, judge is Claude (self-reported claude-opus-5-5, UNVERIFIED), rubric written before scoring and not edited. 5 of the 7 bad drafts that had a target criterion scored below both good drafts on it; 2 only tied with one. Neither good draft scored 3 or more on every criterion: the rubric asks for coverage of all high and medium flags in materiality order, and the good draft covers only the as-of month. That mismatch is documented, not tuned away |
| Balance sheet totals from the connector vs the QuickBooks screen | TESTED (one date) | 2026-10-13, accrual, sandbox company: Total Assets 23,436.29, Total Liabilities 31,131.33, Total Equity -7,695.04 matched the QuickBooks Balance Sheet screen; checked by hand through Claude Desktop, not repeated by reviewers |

Why two pytest runs: the root `pytest.ini` limits collection to `tests\` and skips `connector`
and `evals`, so the root count (822) and the connector count (121) never overlap.

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

- Permissions are a second layer, not a sandbox. The deny rules in `.claude\settings.json` for Read, Edit and Write were TESTED live in Claude Code sessions. Deny rules do not bind subprocesses: a python or shell call can still change files. So this is a guard against careless edits, not a sandbox. (UNTESTED as a security boundary.)
- The manifest guards careless edits only. It does not stop a deliberate change. (TESTED for careless edits; UNTESTED against deliberate ones.)
- Chat answers are guided by an instruction in `SKILL.md`. Code does not check them. Only the written commentary is checked by the grounding rules.
- A QuickBooks P&L report adapter for the trend skill exists (`adapters\qbo_report.py`) and is TESTED at record level only. It is not wired into `trend_cli.py`, `config.py` or `pipeline.py`, so the skill still runs on Excel only, eval case G10 (QBO parity) is not built, and the adapter is not mutation-tested (UNTESTED). The connector and the skill are not wired together.
- A company with the same account label under two paths (the sandbox company does this) would be BLOCKED at the mapping stage with AMBIGUOUS_ACCOUNT. Read from the code of `mapping.py`; UNTESTED end to end.
- Reuse on a second real company is UNTESTED. Two synthetic companies are tested (evals G06 and G09), not a real second workbook.
- Live prose quality is not graded by the suite, which checks only the mechanics of commentary (numbers, banner, banned words, length). It was reviewed once by hand with a model judge (`evals\l3\`); that review is manual, ungated and a model's opinion, not ground truth.
- Connector secrets are plaintext files protected only by Windows user permissions (see `connector\README.md`).
