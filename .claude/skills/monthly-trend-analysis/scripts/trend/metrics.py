"""Metrics engine (PLAN 4.1-4.4). All arithmetic is Decimal at 60 digits; nothing is rounded
here. Use round_cents / round_pct at presentation time only.

Fact id:  <basis>.<metric>[.<view>].<YYYY-MM>   basis = rep | adj | delta (adj - rep)
  metric  rev.total  rev.stream.<slug>  rev.mix.<slug>  cogs.total  gp  gm_pct  opex.total
          opex.fn.<function>  opex.nat.<nature>  opex.line.<slug>  opinc  opinc_margin_pct  net_income
  view    (none) = the month itself        mom_abs mom_pct mom_pp
          pct_rev, pct_rev_mom_pp          (opex metrics as % of revenue)
          tw<N>, tw<N>_pct_rev             trailing N-month window (ratios are ratios of sums)
          tw<N>_mom_abs/_pct/_pp           window vs the window one month earlier
          tw<N>_prev_abs/_pct/_pp          window vs the preceding non-overlapping window
Ratios and percentage changes are in percent units (70.15 means 70.15%); *_pp values are
percentage-point differences of two such ratios.

Nothing is computed unless reconciliation status is OK (ensure_ok) and C9a/C9b were checked.
"""
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .basis import apply_adjustments
from .model import MetricsError, Period
from .reconcile import ensure_ok

PREC = 60
ZERO = Decimal("0.00")
_WINDOW_REASONS = ("insufficient_history", "gap_in_window")


@dataclass(frozen=True)
class NA:
    """A value that does not exist, and why. Never silently zero."""
    reason: str
    detail: str = ""
    missing: tuple = ()      # months that a window needed but the data does not have

    def __str__(self):
        if self.reason in _WINDOW_REASONS:
            return "insufficient data (missing: " + ", ".join(self.missing) + ")"
        return "n/a"


@dataclass
class MetricSet:
    facts: dict
    periods: tuple
    as_of: str
    window_lengths: tuple
    window_end: str
    adjustments: list = field(default_factory=list)

    def get(self, fid):
        return self.facts[fid]


# ---------- helpers ----------

def shift(period, k):
    p = Period.parse(period)
    n = p.year * 12 + (p.month - 1) + k
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def fact_id(basis, key, view, period):
    return ".".join([basis, key] + ([view] if view else []) + [period])


def _vname(*parts):
    return "_".join(p for p in parts if p) or None


def round_cents(x):
    """Presentation only."""
    if isinstance(x, NA):
        return x
    with localcontext() as c:
        c.prec = PREC
        return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_pct(x, dp=2):
    """Presentation only. PLAN 3.1 mentions 1 dp for display; the anchors are quoted at 2 dp."""
    if isinstance(x, NA):
        return x
    with localcontext() as c:
        c.prec = PREC
        return x.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)


def window_from_period_range(text):
    """'2025-07:2026-06' -> (12, '2026-06'): length in months (inclusive) and end period."""
    m = re.fullmatch(r"(\d{4}-\d{2}):(\d{4}-\d{2})", text or "")
    if not m:
        raise MetricsError("BAD_PERIOD_RANGE", f"expected START:END like 2025-07:2026-06, got {text!r}")
    try:
        a, b = Period.parse(m.group(1)), Period.parse(m.group(2))
    except ValueError as e:
        raise MetricsError("BAD_PERIOD_RANGE", str(e)) from e
    n = (b.year * 12 + b.month) - (a.year * 12 + a.month) + 1
    if n < 1:
        raise MetricsError("BAD_PERIOD_RANGE", f"{text!r}: END is before START")
    return n, m.group(2)


def _valid_period(text, code, what):
    try:
        return str(Period.parse(text)) if isinstance(text, str) else (_ for _ in ()).throw(ValueError(what))
    except ValueError:
        raise MetricsError(code, f"{what}: expected YYYY-MM, got {text!r}") from None


# ---------- series ----------

def _amount_series(recs, periods, streams, lines, fns, nats):
    A = {}

    def new(key):
        A[key] = {p: ZERO for p in periods}

    new("rev.total")
    for s in streams:
        new(f"rev.stream.{s}")
    new("cogs.total")
    new("opex.total")
    for f in fns:
        new(f"opex.fn.{f}")
    for n in nats:
        new(f"opex.nat.{n}")
    for ln in lines:
        new(f"opex.line.{ln}")
    new("_other_income")
    new("_other_expense")
    for m in recs:
        r, p, a = m.record, m.record.period, m.record.amount
        if r.section == "income":
            A["rev.total"][p] += a
            A[f"rev.stream.{slug(m.group)}"][p] += a
        elif r.section == "cogs":
            A["cogs.total"][p] += a
        elif r.section == "opex":
            A["opex.total"][p] += a
            A[f"opex.fn.{m.function}"][p] += a
            A[f"opex.nat.{m.nature}"][p] += a
            A[f"opex.line.{slug(r.account)}"][p] += a
        elif r.section == "other_income":
            A["_other_income"][p] += a
        elif r.section == "other_expense":
            A["_other_expense"][p] += a
    A["gp"] = {p: A["rev.total"][p] - A["cogs.total"][p] for p in periods}
    A["opinc"] = {p: A["gp"][p] - A["opex.total"][p] for p in periods}
    A["net_income"] = {p: A["opinc"][p] + A["_other_income"][p] - A["_other_expense"][p] for p in periods}
    del A["_other_income"], A["_other_expense"]
    # public order
    order = (["rev.total"] + [f"rev.stream.{s}" for s in streams] + ["cogs.total", "gp", "opex.total"]
             + [f"opex.fn.{f}" for f in fns] + [f"opex.nat.{n}" for n in nats]
             + [f"opex.line.{ln}" for ln in lines] + ["opinc", "net_income"])
    return {k: A[k] for k in order}


def _ratio_specs(A, streams):
    """(key, base_view, numerator key, denominator key, needs_cogs)"""
    specs = [("gm_pct", None, "gp", "rev.total", True),
             ("opinc_margin_pct", None, "opinc", "rev.total", False)]
    specs += [(k, "pct_rev", k, "rev.total", False) for k in A if k.startswith("opex.")]
    specs += [(f"rev.mix.{s}", None, f"rev.stream.{s}", "rev.total", False) for s in streams]
    return specs


def _ratio(num, den, blocked_no_cogs):
    if blocked_no_cogs:
        return NA("no_cogs_section", "the company declares no COGS section, so gross margin is undefined")
    if isinstance(num, NA):
        return num
    if isinstance(den, NA):
        return den
    if den <= 0:
        return NA("non_positive_revenue", "revenue is zero or negative, so the ratio is undefined")
    return num / den * 100


def _window(series, period_set, first, t, n):
    months = [shift(t, -i) for i in range(n)]
    missing = sorted(m for m in months if m not in period_set)
    if missing:
        reason = "insufficient_history" if all(m < first for m in missing) else "gap_in_window"
        return NA(reason, f"{n}-month window ending {t}", tuple(missing))
    return sum((series[m] for m in months), ZERO)


def _emit_changes(out, basis, key, base_view, win, series, step, is_ratio, tag):
    """MoM (step 1, tag 'mom') or window-vs-preceding-window (step N, tag 'prev')."""
    for t, cur in series.items():
        prior_t = shift(t, -step)
        if prior_t not in series:
            na = NA("no_prior_period" if tag == "mom" else "no_prior_window",
                    f"{prior_t} is not available", (prior_t,))
            d, pc = na, na
        else:
            prior = series[prior_t]
            if isinstance(cur, NA) or isinstance(prior, NA):
                na = NA("unavailable_operand", "one of the two values is not available")
                d, pc = na, na
            else:
                d = cur - prior
                pc = (NA("zero_base", "prior value is zero") if prior == 0
                      else d / abs(prior) * 100)
        if is_ratio:
            out[fact_id(basis, key, _vname(win, base_view, f"{tag}_pp"), t)] = d
        else:
            out[fact_id(basis, key, _vname(win, base_view, f"{tag}_abs"), t)] = d
            out[fact_id(basis, key, _vname(win, base_view, f"{tag}_pct"), t)] = pc


def _basis_facts(out, basis, A, specs, periods, lengths, end, no_cogs):
    period_set, first = set(periods), periods[0]
    win_periods = [p for p in periods if p <= end]
    monthly = {}

    def put(key, view, ser):
        for t, v in ser.items():
            out[fact_id(basis, key, view, t)] = v

    # monthly amounts and their changes
    for key, ser in A.items():
        put(key, None, ser)
        _emit_changes(out, basis, key, None, None, ser, 1, False, "mom")
    # monthly ratios
    for key, bv, num, den, needs_cogs in specs:
        ser = {p: _ratio(A[num][p], A[den][p], needs_cogs and no_cogs) for p in periods}
        monthly[(key, bv)] = ser
        put(key, bv, ser)
        _emit_changes(out, basis, key, bv, None, ser, 1, True, "mom")
    # windows
    for n in lengths:
        win = f"tw{n}"
        W = {key: {t: _window(ser, period_set, first, t, n) for t in win_periods} for key, ser in A.items()}
        for key, ser in W.items():
            put(key, win, ser)
            _emit_changes(out, basis, key, None, win, ser, 1, False, "mom")
            _emit_changes(out, basis, key, None, win, ser, n, False, "prev")
        for key, bv, num, den, needs_cogs in specs:
            ser = {t: _ratio(W[num][t], W[den][t], needs_cogs and no_cogs) for t in win_periods}
            put(key, _vname(win, bv), ser)
            _emit_changes(out, basis, key, bv, win, ser, 1, True, "mom")
            _emit_changes(out, basis, key, bv, win, ser, n, True, "prev")


# ---------- entry point ----------

def compute_metrics(mapped, recon, company, *, as_of=None, window_lengths=None, window_end=None):
    """-> MetricSet. Raises BlockedError if reconciliation failed, MetricsError for bad input."""
    ensure_ok(recon)
    if (any(s.kind == "opex" for s in company.source.sections)
            and not {"C9a", "C9b"} <= {r.control_id for r in recon.results}):
        raise MetricsError("RECON_INCOMPLETE", "function/nature completeness (C9a/C9b) was not checked; "
                           "reconcile with the mapped records before computing metrics")
    all_periods = sorted({m.record.period for m in mapped})
    if {r.period for r in recon.results} != set(all_periods):
        raise MetricsError("RECON_MISMATCH", "the reconciliation covers different periods than the records")

    if as_of is not None:
        as_of = _valid_period(as_of, "BAD_AS_OF", "as_of")
        mapped = [m for m in mapped if m.record.period <= as_of]        # drop later periods first: no lookahead
    periods = sorted({m.record.period for m in mapped})
    if not periods:
        raise MetricsError("NO_DATA", f"no data on or before {as_of}")
    latest = periods[-1]

    lengths = tuple(window_lengths) if window_lengths is not None else company.windows.lengths
    if (not lengths or len(set(lengths)) != len(lengths)
            or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in lengths)):
        raise MetricsError("BAD_WINDOW_LENGTHS", f"expected distinct positive integers, got {lengths!r}")
    end = window_end if window_end is not None else company.windows.end
    end = latest if end == "latest" else _valid_period(end, "BAD_WINDOW_END", "window_end")
    if as_of is not None and end > as_of:
        raise MetricsError("WINDOW_END_AFTER_AS_OF", f"window end {end} is after as-of {as_of}")
    if end > latest:
        raise MetricsError("WINDOW_END_AFTER_DATA", f"window end {end} is after the latest period {latest}")
    if end < periods[0]:
        raise MetricsError("WINDOW_END_BEFORE_DATA", f"window end {end} is before the first period {periods[0]}")

    adjusted, applied = apply_adjustments(mapped, company.adjustments)

    streams, lines = {}, {}
    fns, nats = set(), set()
    for m in mapped:
        r = m.record
        if r.section == "income":
            names, s = streams, slug(m.group)
            what = m.group
        elif r.section == "opex":
            names, s = lines, slug(r.account)
            what = r.account
            fns.add(m.function)
            nats.add(m.nature)
        else:
            continue
        if names.setdefault(s, what) != what:
            raise MetricsError("SLUG_COLLISION", f"{what!r} and {names[s]!r} both map to id '{s}'")
    streams, lines, fns, nats = sorted(streams), sorted(lines), sorted(fns), sorted(nats)
    no_cogs = not any(s.kind == "cogs" for s in company.source.sections)

    out = {}
    with localcontext() as ctx:
        ctx.prec = PREC
        sets = {}
        for basis, recs in (("rep", mapped), ("adj", adjusted)):
            A = _amount_series(recs, periods, streams, lines, fns, nats)
            for p in periods:                                    # internal tie: both groupings cover opex once
                for pre in ("opex.fn.", "opex.nat."):
                    if sum((A[k][p] for k in A if k.startswith(pre)), ZERO) != A["opex.total"][p]:
                        raise MetricsError("INTERNAL_TIE", f"{basis} {pre}* does not sum to opex.total in {p}")
            sets[basis] = A
            _basis_facts(out, basis, A, _ratio_specs(A, streams), periods, lengths, end, no_cogs)
        for fid, v in list(out.items()):
            if fid.startswith("rep."):
                a = out.get("adj." + fid[4:])
                if isinstance(v, Decimal) and isinstance(a, Decimal):
                    out["delta." + fid[4:]] = a - v
    return MetricSet(out, tuple(periods), as_of, lengths, end, applied)
