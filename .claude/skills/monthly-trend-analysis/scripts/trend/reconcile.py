"""Reconciliation C1-C8 (PLAN 3.4). Every control is recomputed from the records and
compared with the workbook's own control total. Any failure -> BLOCKED; callers
must pass the result through ensure_ok() before producing any metric.

C9a/C9b (function/nature completeness, P3) run when the mapped records are supplied: the sum of
the function (nature) totals must equal the workbook's Total Operating Expenses.
"""
from dataclasses import dataclass, field
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
class NotApplicable:
    """A control that was not run, and why. Never silently dropped."""
    control_id: str
    reason: str


@dataclass
class ReconResult:
    results: list
    status: str            # OK | BLOCKED
    not_applicable: list = field(default_factory=list)

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


def reconcile(records, controls, source, tolerance=DEFAULT_TOLERANCE, mapped=None):
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
    dr = source.derived_rows

    na = []

    def skip(cid, why):
        na.append(NotApplicable(cid, why))

    if not secs["income"]:
        skip("C1", "no section of kind 'income' declared")
    if not secs["cogs"]:
        skip("C2", "no section of kind 'cogs' declared")
    if not dr.gross_profit:
        skip("C3", "derived role 'gross_profit' not declared")
    for i, cid in enumerate(("C4", "C5")):
        if len(source.groups) <= i:
            skip(cid, f"fewer than {i + 1} group(s) declared")
    if not secs["opex"]:
        skip("C6", "no section of kind 'opex' declared")
    if not dr.operating_income:
        skip("C7", "derived role 'operating_income' not declared")
    if not dr.net_income:
        skip("C8", "derived role 'net_income' not declared")
    c9 = mapped is not None and bool(secs["opex"])
    if mapped is None:
        skip("C9a", "no mapping supplied (mapped records not passed in)")
        skip("C9b", "no mapping supplied (mapped records not passed in)")
    elif not secs["opex"]:
        skip("C9a", "no section of kind 'opex' declared")
        skip("C9b", "no section of kind 'opex' declared")
    if c9:
        from .mapping import category_totals
        by_fn, by_nat = category_totals(mapped, "function"), category_totals(mapped, "nature")

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
        if dr.gross_profit:
            check("C3", dr.gross_profit, gp)
        for i, g in enumerate(source.groups):
            cid = f"C{4 + i}" if i < 2 else f"C5.{i - 1}"
            check(cid, g.total, total(p, lambda r, g=g: len(r.account_path) > 2 and r.account_path[1] == g.header))
        for s in secs["opex"]:
            check("C6", s.total, total(p, by_header(s.header)))
        opinc = gp - opex
        if dr.operating_income:
            check("C7", dr.operating_income, opinc)
        n = 0
        for kind in ("other_income", "other_expense"):
            for s in secs[kind]:
                n += 1
                check(f"CO{n}", s.total, total(p, by_header(s.header)))
        if c9:
            names = [s.total for s in secs["opex"]]
            found = [ctl.get((n, p)) for n in names]
            for cid, label, tot in (("C9a", "function", by_fn), ("C9b", "nature", by_nat)):
                actual = sum(tot.get(p, {}).values(), Decimal("0.00"))
                name = f"{' + '.join(names)} (by {label})"
                if any(c is None for c in found):
                    out.append(ControlResult(cid, name, p, None, actual, None, None, False, "MISSING_CONTROL"))
                    continue
                expected = sum((c.amount for c in found), Decimal("0.00"))
                diff = actual - expected
                ok = abs(diff) <= tolerance
                out.append(ControlResult(cid, name, p, expected, actual, diff,
                                         ", ".join(c.source_ref for c in found), ok,
                                         None if ok else "MISMATCH"))
        net = opinc + total(p, by_kind("other_income")) - total(p, by_kind("other_expense"))
        if dr.net_income:
            check("C8", dr.net_income, net)

    return ReconResult(out, "OK" if all(r.ok for r in out) else "BLOCKED", na)
