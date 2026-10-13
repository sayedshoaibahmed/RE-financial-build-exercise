# Flags glossary

A flag says **what** moved or looks odd, never why. Thresholds live in the company's `company.yaml` under `thresholds:`.
`flags.top` in the digest merges flags per account (or metric) and month and shows three groups: `as_of` (any flag in the
as-of month), `high_severity`, and `largest` (the biggest of the rest, capped). The first two groups must be covered by the
commentary; `largest` is optional.

| Type | Severity | Meaning | Main thresholds |
|---|---|---|---|
| BLOCKED | blocking | The run stopped: a reconciliation control failed, the source could not be read, or an account is unmapped. Only `blocked_report.json` is written. | none |
| MISSING_MONTH | high | A month between the first and last month has no data. | none |
| NONRECURRING | high | A configured adjustment is non-zero in its month. The adjusted basis sets it to zero. Both bases are shown. | none (set by `adjustments:`) |
| LEVEL_SHIFT | medium | The average of 3 months from month t differs from the 3 months before it by at least 20%, the next 2 months stay within 5% of the new level, and the monthly average moved by at least `level_shift_min_abs`. | `level_shift_min_pct`, `level_shift_band_pct`, `level_shift_min_abs`, `level_shift_window` |
| ONE_OFF_SPIKE | medium | A line is far from its trailing 12-month median (more than 4 times the median absolute deviation and more than `spike_min_abs`), with at least 6 months of history. Consecutive months in the same direction are one flag with `continued_months`. | `spike_mad_multiple`, `spike_min_abs`, `spike_min_history`, `spike_window` |
| MOM_MOVE | medium | The latest month moved by at least `mom_min_abs` and `mom_min_pct` (totals, functions, natures, revenue streams), or a margin moved by at least `margin_min_pp` percentage points. | `mom_min_abs`, `mom_min_pct`, `margin_min_pp` |
| NEGATIVE_VALUE | low | A line is negative and not marked `expected_negative`. Below `negative_min_abs_amount` it is suppressed (listed, not flagged). May be annotated "possible reversal of <month>". | `negative_min_abs_amount`, `reversal_lookback_months`, `reversal_tolerance` |

Basis: cost lines are judged on the adjusted basis, income lines on the reported basis, and a flag says `both` when the other
basis gives the same result. Do not claim a cause for any flag; ask the analyst what changed.
