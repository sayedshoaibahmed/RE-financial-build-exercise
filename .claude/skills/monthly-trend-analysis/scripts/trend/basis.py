"""Reported and adjusted record sets (PLAN 4.2).

The adjusted basis is the reported record set with each configured (account, period)
amount set to zero, everything else unchanged. Nothing is patched at the totals level:
every metric is recomputed from the adjusted records.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from .model import MetricsError

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class AppliedAdjustment:
    account: str
    period: str
    reported_amount: Decimal   # sum of the matching records (0.00 if the period has no data)
    adjusted_amount: Decimal
    delta: Decimal             # adjusted - reported
    present: bool              # False: no record for that (account, period) existed
    reason: str


def apply_adjustments(mapped, adjustments):
    """-> (adjusted MappedRecord list, [AppliedAdjustment]). The input is never modified.

    An adjustment naming an account that appears nowhere in the data is an error
    (a typo would otherwise silently do nothing). An adjustment for a period that has
    no data, or for an account that is zero there, is allowed and recorded with delta 0.
    """
    accounts = {m.record.account for m in mapped}
    targets = {}
    for adj in adjustments:
        if adj.account not in accounts:
            raise MetricsError("UNKNOWN_ADJUSTMENT_ACCOUNT",
                               f"adjustment names account {adj.account!r}, which is not in the data")
        for p in adj.periods:
            targets[(adj.account, p)] = adj

    new = []
    amounts = {}   # (account, period) -> reported sum, for records that exist
    for m in mapped:
        key = (m.record.account, m.record.period)
        if key in targets:
            amounts[key] = amounts.get(key, ZERO) + m.record.amount
            if m.record.amount != 0:
                m = replace(m, record=replace(m.record, amount=ZERO))
        new.append(m)

    applied = []
    for adj in adjustments:
        for p in adj.periods:
            present = (adj.account, p) in amounts
            reported = amounts.get((adj.account, p), ZERO)
            applied.append(AppliedAdjustment(adj.account, p, reported, ZERO, ZERO - reported, present,
                                             adj.reason))
    return new, applied
