# L3: model-judged commentary quality (manual, ungated)

PLAN's L3 layer. Claude scores commentary drafts against a fixed rubric. It is a manual check done once for this
submission. It is NOT part of `run_evals.py`, pytest, `baseline.json` or any gate, and nothing fails if it changes.
Files: `judge_rubric.md` (six criteria a-f, written before any scores), `targets.json` (each known draft's
label and the one criterion its edit targets, also written before scoring), `blind/D01-D13.md` (the drafts under
neutral ids), `blind_map.json` (id -> source; never shown to a judge), `runs/pass1-3.json` (raw judge output),
`results_2026-10-13.json` (per-run scores, medians, spreads, rubric SHA-256).
Rerun by hand: (1) run `trend_cli.py run` for the three digests listed in `blind_map.json` (`digest_runs`) into
`outputs\`; (2) put the rubric, the blind drafts, the digests (renamed digest_X/Y/Z) and an id -> digest file in a
folder outside the repo; (3) start three fresh Claude sessions or subagents, give each only that folder and ask
for the rubric's JSON for every draft; (4) save each reply as `runs/passN.json` and recompute the medians.
What it can show: whether a careful reader (a model) sees the defects that the mechanical rules (L2) also catch,
and how it rates two commentaries from earlier demo runs on the ABC workbook (D02 and D12; labelled live in
`targets.json`).
What it cannot show: real owner usefulness. The judge is Claude, the same model family that writes the
drafts. 13 drafts and 3 runs are a small sample, and the bad drafts are one-line edits of one good draft.

Results (medians of 3 runs, criteria a hedging, b materiality, c owner, d causes, e basis, f questions):

| id  | draft                         | label | target | a | b | c | d | e | f |
|-----|-------------------------------|-------|--------|---|---|---|---|---|---|
| D11 | good_abc_2026-06              | good  | -      | 5 | 2 | 4 | 5 | 5 | 5 |
| D01 | good_mini_no_adjustments      | good  | -      | 5 | 5 | 4 | 5 | 5 | 2 |
| D02 | earlier demo run, 12-month    | live  | -      | 5 | 2 | 4 | 5 | 5 | 5 |
| D12 | earlier demo run, 6-month     | live  | -      | 5 | 2 | 3 | 5 | 5 | 4 |
| D03 | g3_causal_claim               | bad   | d      | 4 | 2 | 4 | 1 | 5 | 5 |
| D07 | g4_direction_mismatch         | bad   | d      | 5 | 2 | 3 | 1 | 5 | 5 |
| D10 | g5_missing_adjusted_pair      | bad   | e      | 5 | 2 | 4 | 5 | 2 | 5 |
| D05 | g5_missing_coverage           | bad   | b      | 5 | 2 | 4 | 5 | 5 | 5 |
| D04 | g6_banner                     | bad   | a      | 3 | 2 | 4 | 5 | 5 | 5 |
| D13 | g8_banned_word                | bad   | a      | 2 | 2 | 4 | 5 | 5 | 5 |
| D09 | g9_too_long                   | bad   | f      | 5 | 2 | 3 | 5 | 5 | 2 |
| D06 | g1_unknown_token              | bad   | -      | 5 | 2 | 3 | 5 | 5 | 5 |
| D08 | g2_bare_number                | bad   | -      | 5 | 2 | 3 | 5 | 5 | 5 |

Disagreements: neither good draft reaches 3 everywhere (good_abc b = 2: medium spike and shift flags unmentioned;
good_mini f = 2: one generic question). g5_missing_coverage ties good_abc on b; g9_too_long ties good_mini on f.
No criterion varied by 2 or more across the 3 runs (33 of 78 cells varied by 1). Drafts and rubric are unchanged.
