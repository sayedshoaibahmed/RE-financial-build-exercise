"""Reconciliation C1-C8 (PLAN 3.4). Every control is recomputed from the records and
compared with the workbook's own control total. Any failure -> BLOCKED; callers
must pass the result through ensure_ok() before producing any metric.

C9a/C9b (function/nature completeness) belong to P3 and are not built here.
"""
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_TOLERANCE = Decimal("0.01")


@dataclass
class ControlResult:
    control_id: str
    control_name: str
    period: str
    expected: Decimal      # the workbook's control total (None if the cell is missing)
    actual: Decimal        # recomputed from records
    difference: Decimal    # actual - expected (None if expected is missing)
    source_ref: str        # control cell, e.g. "Income Statement!C14"
    ok: bool
    reason: str = None     # None | MISMATCH | MISSING_CONTROL


@dataclass
class ReconResult:
    results: list
    status: str            # OK | BLOCKED

    @property
    def failures(self):
        return [r for r in self.results if not r.ok]


class BlockedError(Exception):
    def __init__(self, result, reason="RECON_FAIL"):
        self.reason = reason
        self.result = result
        lines = []
        for r in result.failures:
            if r.reason == "MISSING_CONTROL":
                lines.append(f"  {r.control_id} {r.control_name} {r.period}: control cell missing "
                             f"(recomputed {r.actual})")
            else:
                lines.append(f"  {r.control_id} {r.control_name} {r.period} [{r.source_ref}]: workbook "
                             f"{r.expected}, recomputed {r.actual}, difference {r.difference:+}")
        super().__init__(f"{reason}: {len(result.failures)} control(s) failed\n" + "\n".join(lines))


def ensure_ok(result):
    """Gate for everything downstream: raises unless every control tied."""
    if result.status != "OK":
        raise BlockedError(result)


def reconcile(records, controls, source, tolerance=DEFAULT_TOLERANCE):
    tolerance = Decimal(tolerance)
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    ctl = {(c.control_name, c.period): c for c in controls}
    periods = sorted({r.period for r in records} | {c.period for c in controls})

    def total(period, pred):
        return sum((r.amount for r in records if r.period == period and pred(r)), Decimal("0.00"))

    by_header = lambda h: (lambda r: r.account_path[0] == h)
    by_kind = lambda k: (lambda r: r.section == k)
    secs = {k: [s for s in source.sections if s.kind == k] for k in
            ("income", "cogs", "opex", "other_income", "other_expense")}
    gp_name, opinc_name, net_name = source.derived_rows

    out = []
    for p in periods:
        def check(cid, name, actual):
            c = ctl.get((name, p))
            if c is None:
                out.append(ControlResult(cid, name, p, None, actual, None, None, False, "MISSING_CONTROL"))
                return
            diff = actual - c.amount
            ok = abs(diff) <= tolerance
            out.append(ControlResult(cid, name, p, c.amount, actual, diff, c.source_ref, ok,
                                     None if ok else "MISMATCH"))

        for cid, kind in (("C1", "income"), ("C2", "cogs")):
            for s in secs[kind]:
                check(cid, s.total, total(p, by_header(s.header)))
        income, cogs, opex = (total(p, by_kind(k)) for k in ("income", "cogs", "opex"))
        gp = income - cogs
        check("C3", gp_name, gp)
        for i, g in enumerate(source.groups):
            cid = f"C{4 + i}" if i < 2 else f"C5.{i - 1}"
            check(cid, g.total, total(p, lambda r, g=g: len(r.account_path) > 2 and r.account_path[1] == g.header))
        for s in secs["opex"]:
            check("C6", s.total, total(p, by_header(s.header)))
        opinc = gp - opex
        check("C7", opinc_name, opinc)
        n = 0
        for kind in ("other_income", "other_expense"):
            for s in secs[kind]:
                n += 1
                check(f"CO{n}", s.total, total(p, by_header(s.header)))
        net = opinc + total(p, by_kind("other_income")) - total(p, by_kind("other_expense"))
        check("C8", net_name, net)

    return ReconResult(out, "OK" if all(r.ok for r in out) else "BLOCKED")
