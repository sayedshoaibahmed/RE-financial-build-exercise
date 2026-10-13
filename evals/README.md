# Eval suite (Task 3)

The suite runs offline and is deterministic. It uses no network and no model. It drives the real command line
(`trend_cli.py run` and `trend_cli.py render`) and grades the results against fixed expectations.

```
python evals/run_evals.py            # every case plus commentary grading
python evals/run_evals.py G03 X02    # only these cases (commentary grading always runs)
```

If the `python` on your PATH lacks `openpyxl` or `PyYAML`, the runner re-runs itself under the interpreter that
`scripts/ensure_python.py` picks (the project `.venv`). A full run takes about a minute.

The runner prints one PASS/FAIL line per case and writes `evals/results/latest.json`. It exits `0` only if every
row passes. Any other exit code means at least one row failed: `1` for a failed row, `2` for an unknown case id,
`5` when no usable Python is found.

## What is in the suite

| Folder | Content |
|---|---|
| `cases/*.json` | One golden case per file: id, title, what it protects, inputs (company, workbook transform, CLI args), expected outputs |
| `bad_drafts/g<N>_*.md` | The good ABC draft with one deliberate edit each. The file name states the one code it must fail with |
| `oracle_facts.py` | Recomputes every pinned number with `tests/oracle.py` (shares no code with the `trend` package) |
| `run_evals.py` | The runner |
| `results/latest.json` | The last run: every check, pass or fail, with a detail line for each failure |

The good drafts are the existing fixtures in `tests/fixtures/commentary/`.

## Grading approach

### 1. Graded exactly (no tolerance)

- **Numbers.** Each pinned fact, such as `rep.rev.total.2026-06 = 195898.16`, is compared to the cent (ratios to
  0.01 percentage points) with the value in the run's `metrics.json`.
- **Where expected values come from.** The case files pin literals on purpose: their job is to detect change
  (PLAN 8.1). The runner also recomputes every literal with the independent oracle. If the case file and the
  oracle disagree, the case fails with "fix the case file". No expected value is taken from the package's own
  output.
- **n/a values.** Each listed value must be n/a, with the stated reason where one is given (`insufficient_history`,
  `gap_in_window`). The oracle must agree that the value does not exist.
- **Flags.** These checks use exact flag fields:
  - required flags must be present (required ⊆ actual);
  - forbidden flags must be absent;
  - counts are checked where pinned;
  - X01 compares every ABC flag field by field with the human-reviewed `tests/golden/abc_flags.expected.json`.
- **BLOCKED runs.** These checks apply:
  - the exit code and the reason code;
  - the failed control, month, expected value, actual value and difference;
  - that the run folder holds only the blocked report;
  - that `mapping.yaml` is byte-identical after the run.
- **Guard.** After the suite, `companies/abc_inc/company.yaml`, `companies/abc_inc/mapping.yaml` and the golden
  flags file must hash the same as before it.

### 2. Graded by rules (deterministic grounding checker)

Each commentary draft goes through `render`, which runs `trend/grounding.py`:
- a good draft must exit 0 with no failure codes and write `commentary.md`;
- a bad draft must exit 4 and fail with **exactly** its intended code and no other, and `commentary.md` must not
  exist.

| Draft | Expected code |
|---|---|
| `g1_unknown_token.md` | G1 UNKNOWN_TOKEN |
| `g2_bare_number.md` | G2 BARE_NUMBER |
| `g3_causal_claim.md` | G3 CAUSAL_CLAIM |
| `g4_direction_mismatch.md` | G4 DIRECTION_MISMATCH |
| `g5_missing_coverage.md` | G5 MISSING_COVERAGE |
| `g6_banner.md` | G6 MISSING_DRAFT_BANNER |
| `g8_banned_word.md` | G8 BANNED_WORD |
| `g9_too_long.md` | G9 BODY_TOO_LONG |

These rules check the mechanics, not the quality. A draft can pass them and still be a poor piece of writing.

### 3. Not graded

- **Live prose quality.** The suite does not judge hedging, materiality order, whether the reviewer questions are
  useful, or how well the commentary handles the reported and adjusted bases. That judgement is PLAN's L3: a pinned
  rubric scored by a model. L3 is not part of this suite and is not automated.
- **The commentary Claude writes in a live session.** The suite grades fixed drafts only.
- **Workbook layout and formatting.** `trend_pack.xlsx` is covered by the unit tests, not here.

## Golden cases

Cases G01–G12 follow PLAN 8.1. Cases X01–X03 are extra cases that PLAN does not list; they use an X prefix so they do
not collide with PLAN's ids.

| Id | What it protects |
|---|---|
| G01 abc_baseline | Jun 2026 headline numbers (revenue 195,898.16, MoM −5.98%, gross margin 70.15%, opex and % of revenue, payroll nature), TTM totals and margins on both bases, TTM n/a before 12 months; the good draft renders against the real run |
| G02 abc_dec2025 | Dec 2025 operating income: reported +534,551.41, adjusted −375,217.90, delta −909,769.31 (excluding the items makes results worse); TTM to Dec 2025 on both bases; exactly 2 NONRECURRING flags; no lookahead |
| G03 subtotal_trap | A leaf-looking copy of a subtotal is caught by C6 in every month (diff 49,392.70 in Jun 2026); PLAN's literal relabel is also BLOCKED; `Total Mktg` → UNDECLARED_TOTAL |
| G04 missing_month | A deleted month raises MISSING_MONTH; MoM and the windows spanning the gap are n/a; nothing is interpolated |
| G05 custom_window | `--window 6 --window-end 2026-03` on both bases, the preceding window, no Apr–Jun data |
| G07 recon_tamper | +1,000.00 in one leaf fails C1, C3, C7 and C8 in Jun 2026 only, with cell references; BLOCKED |
| G08 mapping_gate | An unmapped account → UNMAPPED_ACCOUNT; an unapproved one → UNAPPROVED_MAPPING; `mapping.yaml` never changes |
| G12 as_of_jan2026 | No lookahead: Platform Revenue MOM_MOVE −40.74% but no LEVEL_SHIFT as of Jan 2026 |
| X01 abc_flags_golden | All ABC flags equal the frozen golden file |
| X02 grounding_failure | A single typed-out number in the good draft → `render` exits 4 with exactly G2; a stale `commentary.md` is deleted |
| X03 digest_tamper | One digit changed in `digest.json` after `run` → exactly DIGEST_TAMPERED; the earlier `commentary.md` is deleted |

**Not implemented in part 1:**
- G06 different_company;
- G09 quiet_company;
- G10 qbo_parity (the QBO adapter is not built);
- G11 injection_label;
- the 12 mutants (PLAN 8.2);
- the baseline-diff regression rule (PLAN 8.3).

## How regressions are detected

Every case pins exact outputs, so any change in behaviour fails at least one check. That covers a number that moves,
a flag that appears, disappears or changes, a different BLOCKED reason, a draft that starts to pass or to fail for
another code, and a file written or changed when it should not be. Exit 1 makes the failure visible in CI or before
a commit.

## How to read a failure

Illustrative output (not from a real run):

```
G05                         FAIL    13/14   custom_window
                              - fact rep.opinc.tw6.2026-03: expected -1095637.76, got -1095637.75
```

- The first line is the case id, the result, how many checks passed, and the case name.
- Each `-` line underneath is one failed check: its name, then what was expected and what was found.
- `oracle agrees ...: fix the case file` means the case file itself is wrong. The package was not checked for that
  value.
- `fact ...: expected X, got Y` means the package changed. Check whether the change was intended **before** you
  touch the case.
- In a case with several runs, check names start with `[run name]`.
- `results/latest.json` holds every check, including the ones that passed.

## How to add a case

1. Copy the closest file in `cases/` and give it a new id and name. Fill in `title` and `protects` (one or two
   sentences on the behaviour it guards).
2. Under `input`, set the CLI `args` and, if the input data should differ, a `transform`. The supported transforms
   are applied to a copy of the workbook, never to the original:
   - `relabel {from, to}`
   - `add_to_cell {label, period, amount}`
   - `insert_row {before, label, amount | copy_from}`
   - `delete_period`
   - `mapping_add [entries]`
3. Under `expect`, list what to check. The supported keys are `exit_code`, `status`, `as_of`, `max_period`,
   `facts`, `na`, `no_value`, `flags_required`, `flags_forbidden`, `flag_counts`, `flags_golden`, `blocked_reason`,
   `failed_controls`, `control_fails_every_period`, `failed_periods_only`, `failed_controls_have_source_ref`,
   `unmapped_accounts_include`, `mapping_unchanged` and `run_folder_files`. An optional `render` list runs drafts
   against the run.
4. Take every number from the oracle, never from a package run:
   ```python
   # run from the repo root with the project's Python (.venv)
   from evals.oracle_facts import Oracle, text2
   import tests.oracle as o
   O = Oracle(o.WORKBOOK, "companies/abc_inc/company.yaml", "companies/abc_inc/mapping.yaml")
   print(text2(O.value("rep.rev.total.tw12.2026-06")))     # 2303975.51
   ```
   If the fact grammar you need is not in `oracle_facts.py`, extend the oracle there first.
5. Run `python evals/run_evals.py <id>`. A new case must pass. Then break the behaviour it protects, for example
   with a wrong arg or a wrong literal, and confirm the case fails.

**To add a bad draft:** save it as `bad_drafts/g<N>_<what>.md`. Make it the good draft with **one** edit, so it fails
with exactly that code.

**To add a good draft:** put it in `tests/fixtures/commentary/` and add its file name to `GOOD_TARGETS` in
`run_evals.py`, naming the run it belongs to. The runner fails on an unlisted good draft, so it can never be skipped
silently.
