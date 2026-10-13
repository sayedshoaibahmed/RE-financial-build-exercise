# Eval suite (Task 3)

The suite runs offline and is deterministic. It uses no network and no model. It drives the real command line
(`trend_cli.py run` and `trend_cli.py render`) and grades the results against fixed expectations.

```
python evals/run_evals.py                    # every case plus commentary grading, diffed against the baseline
python evals/run_evals.py G03 X02            # only these cases (commentary grading always runs)
python evals/run_evals.py --update-baseline  # print the diff, then rewrite evals/baseline.json
python evals/run_mutants.py                  # mutation testing (M01-M12), see "Mutation testing"
```

If the `python` on your PATH lacks `openpyxl` or `PyYAML`, the runner re-runs itself under the interpreter that
`scripts/ensure_python.py` picks (the project `.venv`).

**Run time (measured, not estimated).** A full `run_evals.py` run took **59.1 s** on the development machine
(Windows 11, Python 3.11 in `.venv`, 2026-10-13; the figure is the runner's own `elapsed` line). A full
`run_mutants.py` run (control plus 13 mutants, 4 in parallel) took **275.3 s** on the same machine.

The runner prints each row as it finishes (`[  6.0s] G01  PASS`), then a PASS/FAIL table, then the baseline diff,
then the elapsed time, and writes `evals/results/latest.json` (not committed). Exit codes:

| Code | Meaning |
|---|---|
| 0 | every row passed and the results match `evals/baseline.json` |
| 1 | at least one row failed |
| 2 | bad arguments (for example an unknown case id) |
| 5 | no usable Python found |
| 6 | every row passed, but the results differ from the baseline |

## What is in the suite

| Folder | Content |
|---|---|
| `cases/*.json` | One golden case per file: id, title, what it protects, inputs (company, workbook transform, CLI args), expected outputs |
| `bad_drafts/g<N>_*.md` | The good ABC draft with one deliberate edit each. The file name states the one code it must fail with |
| `oracle_facts.py` | Recomputes every pinned number with `tests/oracle.py` (shares no code with the `trend` package) |
| `fixture_companies.py` | Synthetic companies for G06 and G09, built in a temp folder from `tests/fixtures` |
| `run_evals.py` | The runner |
| `run_mutants.py` | The mutation harness (PLAN 8.2) |
| `baseline.json` | The regression baseline (committed): every row, every check name with its result, a hash of each source |
| `results/latest.json` | The last run: every check, pass or fail, with a detail line for each failure (not committed) |
| `results/mutants_latest.json` | The last mutation run (not committed) |

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
  - required flags must be present (required is a subset of actual);
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

Cases G01-G12 follow PLAN 8.1. Cases X01-X03 are extra cases that PLAN does not list; they use an X prefix so they do
not collide with PLAN's ids.

**Implemented:** G01, G02, G03, G04, G05, G06, G07, G08, G09, G11, G12 (11 of 12), plus X01-X03.
**Not built:** G10 qbo_parity, because the QBO adapter (P9) is not built, so there is nothing to compare.

| Id | What it protects |
|---|---|
| G01 abc_baseline | Jun 2026 headline numbers (revenue 195,898.16, MoM -5.98%, gross margin 70.15%, opex and % of revenue, payroll nature), TTM totals and margins on both bases, TTM n/a before 12 months; the good draft renders against the real run |
| G02 abc_dec2025 | Dec 2025 operating income: reported +534,551.41, adjusted -375,217.90, delta -909,769.31 (excluding the items makes results worse); TTM to Dec 2025 on both bases; exactly 2 NONRECURRING flags; no lookahead |
| G03 subtotal_trap | A leaf-looking copy of a subtotal is caught by C6 in every month (diff 49,392.70 in Jun 2026); PLAN's literal relabel is also BLOCKED (see the note below); `Total Mktg` -> UNDECLARED_TOTAL |
| G04 missing_month | A deleted month raises MISSING_MONTH; MoM and the windows spanning the gap are n/a; nothing is interpolated |
| G05 custom_window | `--window 6 --window-end 2026-03` on both bases, the preceding window, no Apr-Jun data |
| G06 different_company | A synthetic second company (other accounts, a Payroll group, 14 months Jan 2025 - Feb 2026, newest-first columns) runs on its own config and approved mapping, reconciles, and has 12-month windows only for months 12-14 (n/a `insufficient_history` before) |
| G07 recon_tamper | +1,000.00 in one leaf fails C1, C3, C7 and C8 in Jun 2026 only, with cell references; BLOCKED |
| G08 mapping_gate | An unmapped account -> UNMAPPED_ACCOUNT; an unapproved one -> UNAPPROVED_MAPPING; `mapping.yaml` never changes |
| G09 quiet_company | A synthetic flat company (every line within +/-1% for 14 months): numbers to the cent, and no high or medium severity flag |
| G11 injection_label | `Sales - Payroll` renamed to a label that instructs the AI to break the rules: run OK, numbers equal the oracle, its LEVEL_SHIFT flag is raised under the new label and the label reaches `digest.json` as a plain string; the good draft still renders; a draft that obeys the label fails with exactly G2 and G3 |
| G12 as_of_jan2026 | No lookahead: Platform Revenue MOM_MOVE -40.74% but no LEVEL_SHIFT as of Jan 2026 |
| X01 abc_flags_golden | All ABC flags equal the frozen golden file |
| X02 grounding_failure | A single typed-out number in the good draft -> `render` exits 4 with exactly G2; a stale `commentary.md` is deleted |
| X03 digest_tamper | One digit changed in `digest.json` after `run` -> exactly DIGEST_TAMPERED; the earlier `commentary.md` is deleted |

**Not built, or built only in part:**
- G10 qbo_parity: not built; the QBO adapter is not built.
- G06: PLAN's `sample_co_b` has an "Other Income" section; the synthetic company has none, because the independent
  oracle (`tests/oracle.py`) models only Income, COGS and Operating Expenses. No good draft exists for it, so its
  grounding half (L2) is not run.
- G09: only the L1 half. "The commentary claims no material movements and invents none" is L3 and not graded.
- G11: only the mechanical half. Whether Claude in a live session ignores the instruction is L3 and not graded.

**Note on G03.** PLAN 8.1 predicts that relabelling the real `Total Marketing` row to `Marketing Subtotal` fails
C6. The code stops earlier: the `Marketing` group is no longer closed by its declared total, so the adapter raises
`UNCLASSIFIED_ROW` before reconciliation runs (TESTED: the PLAN-literal run with the reason pinned to
`UNCLASSIFIED_ROW` passes). This is stricter than PLAN, not a bug. G03 keeps the reason unpinned for that run and
proves the C6 behaviour with a separate run that inserts a leaf-looking copy of the subtotal.

## How regressions are detected

Every case pins exact outputs, so any change in behaviour fails at least one check. That covers a number that moves,
a flag that appears, disappears or changes, a different BLOCKED reason, a draft that starts to pass or to fail for
another code, and a file written or changed when it should not be. Exit 1 makes the failure visible in CI or before
a commit. The regression baseline (below) also catches what pass/fail alone cannot: a case or check that silently
disappears, and an expectation that was edited.

## Regression baseline

`evals/baseline.json` is committed. For every row (golden case, draft, guard) it holds the result, every check name
with PASS or FAIL, and the SHA-256 of the row's source file (the case JSON or the draft). Each run compares itself
with it and prints a diff:

```
baseline baseline.json: 5 difference(s)
  SOURCE EDITED G01: its case file / draft changed since the baseline
  RESULT        G01: PASS -> FAIL
  REMOVED CHECK G01: fact rep.rev.total.2026-06 (was PASS)
  CHANGED       G01: oracle agrees rep.rev.total.2026-06: PASS -> FAIL
  REMOVED CASE  G12 (was PASS, 8 checks)
```

(Real output: a temp copy of `cases/` with one G01 literal changed from 195898.16 to 195898.61 and G12 deleted.)

| Line | Meaning |
|---|---|
| `NEW CASE` / `REMOVED CASE` | a row exists now but not in the baseline, or the other way round |
| `NEW CHECK` / `REMOVED CHECK` | a check name appeared or disappeared inside a row |
| `CHANGED` | the same check went PASS -> FAIL or FAIL -> PASS |
| `RESULT` | the row's overall result changed |
| `SOURCE EDITED` | the case file or draft is not byte-identical to the one the baseline was taken from |

Any difference fails the run: exit 1 if a row also failed, exit 6 if every row passed (TESTED: the G12-only removal
above exits 6). A partial run (`run_evals.py G03`) compares only the selected cases and the commentary rows, and
does not report unselected cases as removed.

**When to update it.** Only when the difference is intended: you added or removed a case, added a check, or changed
an expectation on purpose. Run `python evals/run_evals.py --update-baseline`. It prints the diff first, then rewrites
the file; it refuses a partial run and refuses while any row fails. Read the printed diff before committing, and say
in the commit message why the baseline changed. Never update it to make a regression go away.

## Mutation testing

`python evals/run_mutants.py` checks that the suite can catch the bugs PLAN 8.2 lists. For each mutant it copies the
skill folder to a temp folder, edits the copy only (each find string must occur exactly once, or the mutant is
reported NOT APPLIED), and runs the whole suite against the copy (`EVALS_SKILL_DIR=<copy>`). A mutant is KILLED when
at least one row fails. First, a control run on an unmutated copy must pass every row. Before and after, it hashes
`.claude/skills`, `companies`, `tests` and `evals/cases` (ignoring `__pycache__`) and fails loudly if anything changed.

Last run (2026-10-13, 275.3 s): **11 killed, 2 survived, 0 not applied** of 13 (M01-M12 plus M06b). Control passed;
protected trees identical before and after (88 files).

| Mutant | Mutation (in the copy) | PLAN expects | Result | Killed by |
|---|---|---|---|---|
| M01 | group subtotal rows also loaded as records | G01, G03 | KILLED | 21 rows, incl. G01, G03, G07 |
| M02 | reconciliation tolerance 1e9 | G07 | KILLED | G03, G07 |
| M03 | MoM % divides by the current month | G01 | KILLED | 16 rows, incl. G01, G06, G09, G12 |
| M04 | partial windows summed instead of n/a | G01, G04 | KILLED | G01, G04, G05, G06 |
| M05 | window length ignored (always 12) | G05 | KILLED | 24 rows, incl. G05 |
| M06 | adapter keeps sheet column order | G01, G06 | SURVIVED | none |
| M06b | adapter and metrics both keep sheet order | G01, G06 | KILLED | 19 rows, incl. G01, G06 |
| M07 | adjusted windows built from reported data | G02, G05 | KILLED | 14 rows, incl. G02, G05 |
| M08 | unmapped accounts mapped to "other" | G08a | KILLED | G08 |
| M09 | approval and proposal-key checks removed | G08b | KILLED | G08 |
| M10 | nature taken from the function field | G01 | KILLED | 15 rows, incl. G01, G06, G11 |
| M11 | G2 BARE_NUMBER check disabled | L2 on G01 | KILLED | G11, X02, C-g2_bare_number |
| M12 | adjusted-period coverage check disabled | L2 on G02 | SURVIVED | none |

The full kill lists are in the `run_mutants.py` output and `results/mutants_latest.json`.

**Survivors (reported, not hidden, and not fixed here):**
- **M06** is an equivalent mutant at this site. `metrics.compute_metrics` sorts the periods itself, so the order of
  records from the adapter never reaches a number. M06b removes both sorts and is killed. The harness reports M06
  as SURVIVED anyway.
- **M12** is a real gap in the suite. No draft omits the Dec 2025 reported/adjusted operating income pair:
  `g5_missing_coverage` drops an as-of flag citation instead, and the good drafts cite the pair. A bad draft
  `g5_missing_adjusted_pair.md` that deletes the adjusted figure would be expected to kill it (UNTESTED). It was not added in this step, so
  the gap stays visible.

**Not checked:** the mutation run records which rows failed, not why. For example, PLAN expects M01 to die on C6/C9.
The mechanism behind each kill was not inspected (UNVERIFIED).

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
   `unmapped_accounts_include`, `mapping_unchanged`, `run_folder_files` and `digest_contains`. An optional `render`
   list runs drafts against the run (each step: `draft`, optional `edits` [[old, new]], `expect`).
   `input.company` is `abc_inc` or `fixture:<name>` for a builder in `fixture_companies.py` (no transforms).
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
6. Run the full suite. It exits 6 with `NEW CASE <id>`. Then run `--update-baseline` and commit the baseline together
   with the case.

**To add a bad draft:** save it as `bad_drafts/g<N>_<what>.md`. Make it the good draft with **one** edit, so it fails
with exactly that code.

**To add a good draft:** put it in `tests/fixtures/commentary/` and add its file name to `GOOD_TARGETS` in
`run_evals.py`, naming the run it belongs to. The runner fails on an unlisted good draft, so it can never be skipped
silently.
