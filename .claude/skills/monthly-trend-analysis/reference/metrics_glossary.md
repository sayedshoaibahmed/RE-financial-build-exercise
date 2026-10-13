# Metrics glossary

## Fact ids
A fact id names one number: `<basis>.<metric>[.<dimension>][.<view>].<YYYY-MM>`. The commentary quotes it as `{{fact id}}`
and only if it is a key of `allowed_facts` in `digest.json`.

| Part | Values |
|---|---|
| basis | `rep` reported, `adj` adjusted (configured non-recurring items set to zero), `delta` adjusted minus reported |
| metric | `rev.total`, `rev.stream.<slug>`, `rev.mix.<slug>`, `cogs.total`, `gp`, `gm_pct`, `opex.total`, `opex.fn.<function>`, `opex.nat.<nature>`, `opex.line.<slug>`, `opinc`, `opinc_margin_pct`, `net_income` |
| view | none (the month), `mom_abs`, `mom_pct`, `mom_pp`, `pct_rev`, `pct_rev_mom_pp`, `tw<N>`, `tw<N>_pct_rev`, `tw<N>_mom_*`, `tw<N>_prev_*` |

Examples: `rep.rev.total.2026-06`, `adj.opinc.2025-12`, `delta.opinc.2025-12`, `rep.opinc.tw12.2026-06`.
The digest also has ids that are not metrics: `flags.count.<TYPE>`, `flags.shown`, and `flag.<flag id>.<field>` for the numbers
inside a flag (for example `flag.MOVE-2026-06-2073be.pp`).

## Units and signs
- Amounts have no currency symbol (the source does not state a currency). Percent values are in percent units.
- `pp` means percentage points: the difference of two percentages.
- Display strings keep the sign of the value (`-12,452.80`). A margin or percentage change of a negative base uses the
  size of the base, so read the sign of the display string, not the word "increase".

## Windows
- `tw<N>` is the sum of the last N months, and only exists when all N months have data. Otherwise the display reads
  "insufficient data (missing: ...)". Window ratios are ratios of sums, never averages of monthly ratios.
- `mom_*` compares with the calendar month before; it is "n/a" when that month is missing or the base is zero.
- A window "contains" an adjusted period when the adjusted month falls inside its N months. Then both the reported and the
  adjusted figure must be cited.

## Bases
The adjusted basis is the reported data with each configured (account, month) amount set to zero; every metric is recomputed
on it. Revenue is usually identical on both bases. Operating income as the source defines it includes interest and tax lines.
