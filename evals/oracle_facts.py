"""Expected values for eval cases, computed by tests/oracle.py only. Shares NO code with the trend package.

A case file pins its expected values as literals (PLAN 8.1: golden cases pin exact values on purpose). The runner
recomputes every pinned value here and fails the case if the literal and the oracle disagree, so a typo in a case
file, or a value copied from the package's own output, cannot pass unnoticed.

Supported fact ids (a subset of the package grammar, parsed here independently):
  <basis>.<metric>[.<view>].<YYYY-MM>
  basis   rep | adj | delta (= adj - rep)
  metric  rev.total | cogs.total | gp | opex.total | opinc | gm_pct | opinc_margin_pct |
          opex.fn.<function> | opex.nat.<nature> | rev.stream.<slug of an income leaf>
  view    (none) | mom_abs | mom_pct | pct_rev | tw<N>
Values: Fraction, or None for "not available" (missing month, short window, zero or negative base).
"""
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import oracle  # noqa: E402

RATIOS = {"gm_pct": "gp", "opinc_margin_pct": "opinc"}


def _slug(label):
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def zeroed_from(company_yaml):
    """{(account, period)} for every configured adjustment, read raw from company.yaml."""
    doc = yaml.safe_load(Path(company_yaml).read_text(encoding="utf-8")) or {}
    return {(a["account"], str(p)) for a in doc.get("adjustments") or [] for p in a["periods"]}


class Oracle:
    def __init__(self, workbook, company_yaml, mapping_yaml):
        self.o = oracle.scan(workbook)
        self.zeroed = zeroed_from(company_yaml)
        self.mapping = mapping_yaml
        self._cache = {}

    def _series(self, basis, metric):
        """{period: Fraction} of a summable metric on one basis."""
        key = (basis, metric)
        if key in self._cache:
            return self._cache[key]
        zeroed = self.zeroed if basis == "adj" else ()
        o = self.o
        if metric in ("rev.total", "cogs.total", "gp", "opex.total", "opinc"):
            name = {"rev.total": "rev", "cogs.total": "cogs", "opex.total": "opex"}.get(metric, metric)
            s = {p: v[name] for p, v in oracle.monthly(o, zeroed).items()}
        elif metric.startswith(("opex.fn.", "opex.nat.")):
            field = "function" if metric.startswith("opex.fn.") else "nature"
            cat = metric.split(".", 2)[2]
            s = {p: d.get(cat, Fraction(0)) for p, d in oracle.by_category(o, self.mapping, field, zeroed).items()}
        elif metric.startswith("rev.stream."):
            want = metric.split(".", 2)[2]
            leaves = [x for x in o["leaves"] if x["section"] == "income" and _slug(x["label"]) == want]
            if len(leaves) != 1:
                raise KeyError(f"oracle: no single income leaf with slug {want!r}")
            s = {p: oracle.cents(v) for p, v in zip(o["periods"], leaves[0]["values"])}
        else:
            raise KeyError(f"oracle: unsupported metric {metric!r}")
        self._cache[key] = s
        return s

    def _month(self, basis, metric, period):
        if metric in RATIOS:
            rev = self._series(basis, "rev.total")
            num = self._series(basis, RATIOS[metric])
            if period not in rev:
                return None
            return oracle.pct(num[period], rev[period])
        s = self._series(basis, metric)
        return s.get(period)

    def _window(self, basis, metric, period, n):
        if metric in RATIOS:
            rev = oracle.window(self._series(basis, "rev.total"), period, n)
            num = oracle.window(self._series(basis, RATIOS[metric]), period, n)
            return None if rev is None or num is None else oracle.pct(num, rev)
        return oracle.window(self._series(basis, metric), period, n)

    def value(self, fid):
        parts = fid.split(".")
        basis, period, mid = parts[0], parts[-1], parts[1:-1]
        if basis == "delta":
            a, r = self.value(".".join(["adj"] + mid + [period])), self.value(".".join(["rep"] + mid + [period]))
            return None if a is None or r is None else a - r
        if basis not in ("rep", "adj"):
            raise KeyError(f"oracle: unsupported basis in {fid!r}")
        view = ""
        if mid and re.fullmatch(r"mom_abs|mom_pct|pct_rev|tw\d+", mid[-1]):
            view = mid[-1]
            mid = mid[:-1]
        metric = ".".join(mid)
        if view == "":
            return self._month(basis, metric, period)
        if view.startswith("tw"):
            return self._window(basis, metric, period, int(view[2:]))
        if view == "pct_rev":
            x, rev = self._month(basis, metric, period), self._month(basis, "rev.total", period)
            return None if x is None or rev is None else oracle.pct(x, rev)
        cur, prev = self._month(basis, metric, period), self._month(basis, metric, oracle.shift(period, -1))
        if cur is None or prev is None:
            return None
        if view == "mom_abs":
            return cur - prev
        return None if prev == 0 else (cur - prev) / abs(prev) * 100

    def leaf(self, label, period):
        """One workbook row's value in one period (leaf or subtotal row), as a Fraction."""
        for x in self.o["leaves"]:
            if x["label"] == label:
                return oracle.cents(x["values"][self.o["periods"].index(period)])
        raise KeyError(f"oracle: no leaf row {label!r}")


def text2(v):
    """Fraction / Decimal / str -> '1234.56' (2 dp, half up); None stays None."""
    if v is None:
        return None
    if isinstance(v, Fraction):
        v = Decimal(v.numerator) / Decimal(v.denominator)
    d = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(abs(d) if d == 0 else d)
