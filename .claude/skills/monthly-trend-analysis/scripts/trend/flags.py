"""Flags (PLAN section 5): seven types, each with the evidence that raised it.

A flag states WHAT moved or looks odd, never why. Every flag carries: type, period, subject
(account, or metric id), observed value(s), the threshold used, basis (reported | adjusted | both),
a plain-English reason, and evidence (fact ids that resolve in the metrics, and/or source cells).
A flag without evidence cannot be constructed (ValueError).

Nothing runs unless reconciliation status is OK (ensure_ok).

Basis rules (so the same finding is never duplicated across bases):
  * Cost lines (COGS, opex) are judged on the ADJUSTED basis, income lines on the REPORTED basis.
    The configured one-off items are covered by NONRECURRING; they do not also raise a spike.
  * The detector also runs on the other basis. Identical result -> one flag with basis "both";
    otherwise one flag with the primary basis. A result that exists only on the other basis is not raised.
  * MOM_MOVE: totals / functions / natures adjusted, revenue streams reported, margins on both bases
    (two flags if the two bases give different moves). Only the latest month is judged (PLAN: "latest-month movers").
  * NEGATIVE_VALUE judges the booked (reported) sign; "both" when the adjusted basis shows the same value.
  * NONRECURRING is by definition about both bases.

Per type
  MISSING_MONTH   one flag per calendar month missing between the first and last period.
  NONRECURRING    a configured adjustment whose reported amount is non-zero in its period.
  LEVEL_SHIFT     t is the first month at a new level: the average of the `level_shift_window` months from t
                  differs from the average of the same number of months before t by >= level_shift_min_pct,
                  and the following window-1 months each stay within +/- level_shift_band_pct of the new
                  average. Needs every one of those months to exist (so it is not raised within window-1 months
                  of as-of). Runs of consecutive qualifying months collapse to the first. A zero "before" average is skipped.
                  Optional materiality floor level_shift_min_abs (default 0 = none, as in PLAN): the monthly average
                  must also move by at least that amount.
  ONE_OFF_SPIKE   |x - median| > max(spike_mad_multiple * MAD, spike_min_abs), where median and MAD (median absolute
                  deviation) are over the prior months within `spike_window`, with at least `spike_min_history`
                  of them. A first-ever non-zero value triggers it (median 0). Each spike states its direction
                  (up / down, relative to the median). Consecutive months flagged in the same direction collapse into
                  one flag at the first month, with observed["continued_months"] = how many months followed; a change of
                  direction, or an unflagged month in between, starts a new flag.
  MOM_MOVE        |change| >= mom_min_abs and |change %| >= mom_min_pct (a move from zero counts as an unbounded %);
                  margins: |change| >= margin_min_pp percentage points.
  NEGATIVE_VALUE  an income or expense line below zero that is not marked expected_negative in mapping.yaml.
                  Boundary rule: below the minimum is suppressed; equal to the minimum or above is flagged
                  (minimum = negative_min_abs_amount, compared on the absolute amount). Suppressed values stay in
                  the data and in every total and are listed in FlagSet.suppressed_negatives. A value equal to
                  minus a value up to `reversal_lookback_months` earlier (within `reversal_tolerance`) is annotated
                  "possible reversal of <month>".
BLOCKED flags describe a stopped run (see blocked_flag); compute_flags never returns one.
"""
import hashlib
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .adapters.base import AdapterError
from .basis import apply_adjustments
from .config import ConfigError
from .mapping import MappingError
from .metrics import NA, PREC, fact_id, round_cents, shift, slug
from .model import MetricsError
from .reconcile import BlockedError, ensure_ok

TYPES = ("BLOCKED", "MISSING_MONTH", "NONRECURRING", "LEVEL_SHIFT", "ONE_OFF_SPIKE", "MOM_MOVE", "NEGATIVE_VALUE")
SEVERITY = {"BLOCKED": "blocking", "MISSING_MONTH": "high", "NONRECURRING": "high", "LEVEL_SHIFT": "medium",
            "ONE_OFF_SPIKE": "medium", "MOM_MOVE": "medium", "NEGATIVE_VALUE": "low"}
SEVERITIES = ("blocking", "high", "medium", "low")
ABBR = {"BLOCKED": "BLK", "MISSING_MONTH": "MISS", "NONRECURRING": "NONREC", "LEVEL_SHIFT": "SHIFT",
        "ONE_OFF_SPIKE": "SPIKE", "MOM_MOVE": "MOVE", "NEGATIVE_VALUE": "NEG"}
BASES = ("reported", "adjusted", "both")
_TOKEN = {"reported": "rep", "adjusted": "adj"}
_NAME = {"rep": "reported", "adj": "adjusted"}


# ---------------------------------------------------------------------------
# Flag / FlagSet
# ---------------------------------------------------------------------------

def _empty(v):
    return v is None or v == "" or v == {} or v == ()


@dataclass(frozen=True)
class Flag:
    id: str
    type: str
    severity: str
    basis: str            # reported | adjusted | both  (n/a only for BLOCKED)
    period: str           # YYYY-MM (n/a only for BLOCKED)
    subject: str          # account, or metric id
    observed: object      # dict of Decimals / strings, or a string
    threshold: object
    reason: str
    fact_ids: tuple = ()
    source_refs: tuple = ()

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown flag type {self.type!r}")
        if self.severity != SEVERITY[self.type]:
            raise ValueError(f"{self.type} must have severity {SEVERITY[self.type]!r}, got {self.severity!r}")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("a flag needs a reason")
        if _empty(self.subject) or _empty(self.observed) or _empty(self.threshold):
            raise ValueError("a flag needs a subject, an observed value and a threshold")
        if self.type == "BLOCKED":
            if self.basis != "n/a" or self.period != "n/a" or not isinstance(self.observed, str):
                raise ValueError("a BLOCKED flag has basis/period 'n/a' and its reason code as observed")
        else:
            if self.basis not in BASES:
                raise ValueError(f"basis must be one of {BASES}, got {self.basis!r}")
            if not re.fullmatch(r"\d{4}-\d{2}", self.period or ""):
                raise ValueError(f"bad period {self.period!r}")
            if not self.fact_ids and not self.source_refs:
                raise ValueError("a flag with no evidence is a bug: give fact_ids or source_refs")

    def to_dict(self):
        return {"id": self.id, "type": self.type, "severity": self.severity, "basis": self.basis,
                "period": self.period, "subject": self.subject, "observed": _present(self.observed),
                "threshold": _present(self.threshold), "reason": self.reason,
                "fact_ids": list(self.fact_ids), "source_refs": list(self.source_refs)}


def _present(v):
    """Rounding happens here, at presentation, and nowhere else."""
    if isinstance(v, Decimal):
        with localcontext() as c:
            c.prec = PREC
            return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if isinstance(v, dict):
        return {k: _present(x) for k, x in v.items()}
    return v


@dataclass
class FlagSet:
    flags: list
    suppressed_negatives: list     # negatives below negative_min_abs_amount: kept in the data, not flagged
    thresholds: dict               # the thresholds actually used

    @property
    def counts(self):
        return {t: sum(1 for f in self.flags if f.type == t) for t in TYPES}

    @property
    def by_severity(self):
        return {s: sum(1 for f in self.flags if f.severity == s) for s in SEVERITIES}

    def to_dict(self):
        return {"counts": self.counts, "by_severity": self.by_severity,
                "thresholds": _present(self.thresholds),
                "suppressed_negatives": [_present(s) for s in self.suppressed_negatives],
                "flags": [f.to_dict() for f in self.flags]}


def _make_id(typ, period, subject, basis):
    h = hashlib.sha1(f"{typ}|{period}|{subject}|{basis}".encode("utf-8")).hexdigest()[:6]
    return f"{ABBR[typ]}-{period}-{h}"


# ---------------------------------------------------------------------------
# BLOCKED
# ---------------------------------------------------------------------------

def blocked_flag(exc):
    """Describe a stopped run (reconciliation failure, adapter / mapping / metrics / config error)."""
    refs = ()
    if isinstance(exc, BlockedError):
        code, subject = exc.reason, "reconciliation"
        refs = tuple(r.source_ref for r in exc.result.failures if r.source_ref)
    elif isinstance(exc, AdapterError):
        code, subject = exc.code, "source"
        refs = (exc.source_ref,) if exc.source_ref else ()
    elif isinstance(exc, MappingError):
        code, subject = exc.code, "mapping"
    elif isinstance(exc, MetricsError):
        code, subject = exc.code, "metrics"
    elif isinstance(exc, ConfigError):
        code, subject = exc.code, "config"
    else:
        raise TypeError(f"{type(exc).__name__} is not a pipeline error")
    reason = (str(exc).splitlines() or [code])[0]
    return Flag(_make_id("BLOCKED", "n/a", subject, "n/a"), "BLOCKED", "blocking", "n/a", "n/a", subject,
                code, "the run must not be blocked", reason, (), refs)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@dataclass
class _Line:
    account: str
    section: str
    group: str
    expected_negative: bool
    series: dict
    refs: dict

    @property
    def key(self):
        """Metric key that carries this line's value (income -> stream, opex -> line, cogs -> total)."""
        if self.section == "income":
            return f"rev.stream.{slug(self.group)}"
        if self.section == "opex":
            return f"opex.line.{slug(self.account)}"
        if self.section == "cogs":
            return "cogs.total"
        return "net_income"

    @property
    def primary(self):
        return "reported" if self.section in ("income", "other_income") else "adjusted"


def _lines(mapped):
    out = {}
    for m in mapped:
        r = m.record
        ln = out.get(r.account)
        if ln is None:
            ln = out[r.account] = _Line(r.account, r.section, m.group, m.expected_negative, {}, {})
        ln.series[r.period] = r.amount
        ln.refs[r.period] = r.source_ref
    return out


@dataclass
class _Finding:
    subject: str
    period: str
    observed: object
    threshold: object
    reason: str
    fact_ids: tuple
    refs: tuple = ()


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _m(x):
    return f"{round_cents(x):,}"


def _pct(x):
    return f"{round_cents(x):,}%"


def _combine(primary, fp, fs, secondary_only=False):
    """-> [(basis, finding)]. primary/secondary are 'reported'/'adjusted'."""
    secondary = "adjusted" if primary == "reported" else "reported"
    out = []
    for key, f in fp.items():
        g = fs.get(key)
        out.append(("both" if g is not None and g.observed == f.observed else primary, f))
    if secondary_only:
        for key, g in fs.items():
            f = fp.get(key)
            if f is None or f.observed != g.observed:
                out.append((secondary, g))
    return out


# ---------------------------------------------------------------------------
# detectors (one function each)
# ---------------------------------------------------------------------------

def _missing_months(metrics):
    ps = list(metrics.periods)
    have = set(ps)
    out = []
    m = shift(ps[0], 1)
    while m < ps[-1]:
        if m not in have:
            prev = max(p for p in ps if p < m)
            nxt = min(p for p in ps if p > m)
            out.append(_Finding("period", m, "no data", "every month between the first and last period must have data",
                                f"No data for {m}, although data exists for {prev} and {nxt}.",
                                (fact_id("rep", "rev.total", None, prev), fact_id("rep", "rev.total", None, nxt))))
        m = shift(m, 1)
    return out


def _nonrecurring(metrics, lines):
    out = []
    for a in metrics.adjustments:
        ln = lines.get(a.account)
        if ln is None or not a.present or a.reported_amount == 0:
            continue
        p, k = a.period, ln.key
        ids = [fact_id(b, k, None, p) for b in ("rep", "adj", "delta")] + \
              [fact_id(b, "opinc", None, p) for b in ("rep", "adj", "delta")]
        out.append(_Finding(a.account, p, {"reported": a.reported_amount, "adjusted": a.adjusted_amount},
                            {"rule": "a configured adjustment is non-zero in its period"},
                            f"Configured adjustment: {a.account} is {_m(a.reported_amount)} on the reported basis "
                            f"and {_m(a.adjusted_amount)} on the adjusted basis in {p} "
                            f"(company.yaml: \"{a.reason}\").", tuple(ids)))
    return out


def _spikes(ln, series, tok, th):
    raw = {}
    for p in sorted(series):
        prior = [series[q] for k in range(1, th.spike_window + 1) if (q := shift(p, -k)) in series]
        if len(prior) < th.spike_min_history:
            continue
        med = _median(prior)
        mad = _median([abs(x - med) for x in prior])
        dev = abs(series[p] - med)
        cutoff = max(th.spike_mad_multiple * mad, th.spike_min_abs)
        if dev > cutoff:
            direction = "up" if series[p] > med else "down"
            raw[p] = _Finding(
                ln.account, p,
                {"value": series[p], "median": med, "mad": mad, "deviation": dev, "direction": direction,
                 "continued_months": 0},
                {"cutoff": cutoff, "mad_multiple": th.spike_mad_multiple, "min_abs": th.spike_min_abs},
                f"{ln.account} is {_m(series[p])} in {p} ({direction}) against a trailing median of {_m(med)}; "
                f"the deviation of {_m(dev)} exceeds the cutoff of {_m(cutoff)}.",
                (fact_id(tok, ln.key, None, p),), (ln.refs[p],))
    # A one-off should not persist: consecutive months flagged in the same direction are one flag at the first month.
    # The trailing median only catches up with a new level after more than half the window, so such runs are common.
    out, first, last = {}, None, None
    for p in sorted(raw):
        if first is not None and p == shift(last, 1) and raw[p].observed["direction"] == raw[first].observed["direction"]:
            raw[first].observed["continued_months"] += 1
            last = p
            continue
        _end_run(raw, first, last)
        first, last = p, p
        out[(ln.account, p)] = raw[p]
    _end_run(raw, first, last)
    return out


def _end_run(raw, first, last):
    if first is None:
        return
    f = raw[first]
    n = f.observed["continued_months"]
    if n:
        f.reason += f" It stays flagged for {n} more consecutive month{'s' if n > 1 else ''}, through {last}."


def _level_shifts(ln, series, tok, th):
    w = th.level_shift_window
    qual = {}
    for t in sorted(series):
        before = [shift(t, -k) for k in range(w, 0, -1)]
        after = [shift(t, k) for k in range(w)]
        if not all(m in series for m in before + after):
            continue
        sb, sa = sum((series[m] for m in before), Decimal(0)), sum((series[m] for m in after), Decimal(0))
        if sb == 0 or abs(sa - sb) * 100 < th.level_shift_min_pct * abs(sb) or abs(sa - sb) < th.level_shift_min_abs * w:
            continue
        if any(abs(series[m] * w - sa) * 100 > th.level_shift_band_pct * abs(sa) for m in after[1:]):
            continue
        change = (sa - sb) / abs(sb) * 100
        qual[t] = _Finding(
            ln.account, t, {"before_avg": sb / w, "after_avg": sa / w, "change_pct": change},
            {"min_change_pct": th.level_shift_min_pct, "band_pct": th.level_shift_band_pct, "window": w,
             "min_abs_change": th.level_shift_min_abs},
            f"{ln.account} averages {_m(sa / w)} over {t} to {after[-1]}, against {_m(sb / w)} over the {w} months "
            f"before ({_pct(change)}), and the next {w - 1} months stay within {th.level_shift_band_pct}% of that level.",
            (fact_id(tok, ln.key, None, before[-1]), fact_id(tok, ln.key, None, t), fact_id(tok, ln.key, None, after[-1])))
    return {(ln.account, t): f for t, f in qual.items() if shift(t, -1) not in qual}


def _negatives(ln, series, tok, th, suppressed):
    out = {}
    if ln.expected_negative:
        return out
    for p in sorted(series):
        x = series[p]
        if x >= 0:
            continue
        if abs(x) < th.negative_min_abs_amount:          # below the minimum is suppressed
            if suppressed is not None:
                suppressed.append({"account": ln.account, "period": p, "amount": x})
            continue
        rev = None
        for k in range(1, th.reversal_lookback_months + 1):
            q = shift(p, -k)
            if q in series and series[q] > 0 and abs(x + series[q]) <= th.reversal_tolerance:
                rev = q
                break
        out[(ln.account, p)] = _Finding(
            ln.account, p, {"amount": x, "reversal_of": rev}, {"min_abs_amount": th.negative_min_abs_amount},
            f"{ln.account} has a negative booked amount of {_m(x)} in {p}" +
            (f"; possible reversal of {rev}." if rev else "."),
            (fact_id(tok, ln.key, None, p),), (ln.refs[p],))
    return out


def _amount_moves(metrics, tok, keys, latest, th):
    f = metrics.facts
    prev = shift(latest, -1)
    out = {}
    for key in keys:
        ab = f.get(fact_id(tok, key, "mom_abs", latest))
        pc = f.get(fact_id(tok, key, "mom_pct", latest))
        if not isinstance(ab, Decimal) or abs(ab) < th.mom_min_abs:
            continue
        if isinstance(pc, Decimal):
            if abs(pc) < th.mom_min_pct:
                continue
            shown, pct_txt = pc, _pct(pc)
        elif isinstance(pc, NA) and pc.reason == "zero_base":
            shown, pct_txt = "from zero", "from zero"
        else:
            continue
        out[(key, latest)] = _Finding(
            key, latest, {"abs": ab, "pct": shown}, {"min_abs": th.mom_min_abs, "min_pct": th.mom_min_pct},
            f"{key} moved {'+' if ab > 0 else ''}{_m(ab)} ({pct_txt}) from {prev} to {latest} "
            f"on the {_NAME[tok]} basis.",
            (fact_id(tok, key, None, latest), fact_id(tok, key, "mom_abs", latest), fact_id(tok, key, "mom_pct", latest)))
    return out


def _margin_moves(metrics, tok, latest, th):
    f = metrics.facts
    prev = shift(latest, -1)
    out = {}
    for key in ("gm_pct", "opinc_margin_pct"):
        pp = f.get(fact_id(tok, key, "mom_pp", latest))
        if not isinstance(pp, Decimal) or abs(pp) < th.margin_min_pp:
            continue
        out[(key, latest)] = _Finding(
            key, latest, {"pp": pp}, {"min_pp": th.margin_min_pp},
            f"{key} moved {'+' if pp > 0 else ''}{round_cents(pp)} percentage points from {prev} to {latest} "
            f"on the {_NAME[tok]} basis.",
            (fact_id(tok, key, None, latest), fact_id(tok, key, "mom_pp", latest)))
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def compute_flags(mapped, recon, metrics, company):
    """-> FlagSet (sorted by period, type, subject, basis). Raises BlockedError unless reconciliation is OK."""
    ensure_ok(recon)
    th = company.thresholds
    keep = set(metrics.periods)
    rep_recs = [m for m in mapped if m.record.period in keep]
    adj_recs, _ = apply_adjustments(rep_recs, company.adjustments)
    rep_lines, adj_lines = _lines(rep_recs), _lines(adj_recs)
    latest = metrics.periods[-1]
    found = []          # (type, basis, _Finding)
    suppressed = []

    with localcontext() as ctx:
        ctx.prec = PREC
        for f in _missing_months(metrics):
            found.append(("MISSING_MONTH", "both", f))
        for f in _nonrecurring(metrics, rep_lines):
            found.append(("NONRECURRING", "both", f))

        for account in sorted(rep_lines):
            ln = rep_lines[account]
            prim = ln.primary
            sec = "adjusted" if prim == "reported" else "reported"
            lines = {"reported": rep_lines[account], "adjusted": adj_lines[account]}
            for typ, det in (("ONE_OFF_SPIKE", _spikes), ("LEVEL_SHIFT", _level_shifts)):
                fp = det(ln, lines[prim].series, _TOKEN[prim], th)
                fs = det(ln, lines[sec].series, _TOKEN[sec], th)
                for basis, f in _combine(prim, fp, fs):
                    found.append((typ, basis, f))
            fp = _negatives(ln, lines["reported"].series, "rep", th, suppressed)
            fs = _negatives(ln, lines["adjusted"].series, "adj", th, None)
            for basis, f in _combine("reported", fp, fs):
                found.append(("NEGATIVE_VALUE", basis, f))

        keys = {}
        for fid in metrics.facts:
            m = re.fullmatch(r"rep\.(.+)\.mom_abs\." + re.escape(latest), fid)
            if m:
                keys[m.group(1)] = True
        totals = [k for k in keys if k in ("rev.total", "gp", "opex.total", "opinc")
                  or k.startswith(("opex.fn.", "opex.nat."))]
        streams = [k for k in keys if k.startswith("rev.stream.")]
        for basis, f in _combine("adjusted", _amount_moves(metrics, "adj", sorted(totals), latest, th),
                                 _amount_moves(metrics, "rep", sorted(totals), latest, th)):
            found.append(("MOM_MOVE", basis, f))
        for basis, f in _combine("reported", _amount_moves(metrics, "rep", sorted(streams), latest, th),
                                 _amount_moves(metrics, "adj", sorted(streams), latest, th)):
            found.append(("MOM_MOVE", basis, f))
        for basis, f in _combine("adjusted", _margin_moves(metrics, "adj", latest, th),
                                 _margin_moves(metrics, "rep", latest, th), secondary_only=True):
            found.append(("MOM_MOVE", basis, f))

    flags = []
    for typ, basis, f in found:
        for fid in f.fact_ids:
            if fid not in metrics.facts:
                raise MetricsError("FLAG_EVIDENCE_MISSING", f"{typ} flag for {f.subject} cites unknown fact {fid}")
        flags.append(Flag(_make_id(typ, f.period, f.subject, basis), typ, SEVERITY[typ], basis, f.period, f.subject,
                          f.observed, f.threshold, f.reason, tuple(f.fact_ids), tuple(f.refs)))
    flags.sort(key=lambda x: (x.period, x.type, x.subject, x.basis))
    if len({x.id for x in flags}) != len(flags):
        raise MetricsError("FLAG_ID_COLLISION", "two flags received the same id")
    suppressed.sort(key=lambda s: (s["period"], s["account"]))
    return FlagSet(flags, suppressed, th.as_dict())
