"""Independent, deliberately naive scan of the ABC workbook.

Shares NO code with the trend package. It trusts nothing but openpyxl and
the visible layout: header row 5, labels in columns A/B, numbers in the
period columns. Slow and dumb on purpose; tests use it to derive structural
facts (row counts, label sets, periods) instead of hard-coding them.
"""
from datetime import datetime
from pathlib import Path

import openpyxl

WORKBOOK = Path(__file__).resolve().parent.parent / "data" / "ABC Inc - Financial Statements.xlsx"
SHEET = "Income Statement"
DERIVED = {"Gross Profit", "Operating Income", "Net Income"}
SECTIONS = {"Income": "income", "Cost of Goods Sold": "cogs", "Operating Expenses": "opex"}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def scan(path=WORKBOOK):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]

    # periods: every header-row cell that parses as "Mon YYYY"
    periods = []
    period_cols = []
    for c in range(1, ws.max_column + 1):
        v = ws.cell(5, c).value
        if isinstance(v, str):
            try:
                d = datetime.strptime(v.strip(), "%b %Y")
            except ValueError:
                continue
            periods.append(d.strftime("%Y-%m"))
            period_cols.append(c)

    leaves, subtotals, derived, headers = [], [], [], []
    cur = None
    for r in range(6, ws.max_row + 1):
        a, b = ws.cell(r, 1).value, ws.cell(r, 2).value
        label = a if a else b
        if label is None:
            continue
        label = str(label).strip()
        vals = [ws.cell(r, c).value for c in period_cols]
        all_num = all(_is_num(v) for v in vals)
        any_num = any(_is_num(v) for v in vals)
        if not any_num:
            headers.append((r, label))
            if label in SECTIONS:
                cur = SECTIONS[label]
        elif label in DERIVED:
            derived.append((r, label))
        elif label.startswith("Total"):
            subtotals.append((r, label))
        elif all_num:
            leaves.append({"row": r, "label": label, "values": vals, "section": cur})
        else:
            raise AssertionError(f"oracle: partial numeric row {r} {label!r}")

    return {
        "periods": periods,
        "period_cols": period_cols,
        "leaves": leaves,
        "leaf_labels": [x["label"] for x in leaves],
        "subtotals": subtotals,
        "derived": derived,
        "headers": headers,
        "slash_labels": [x["label"] for x in leaves if "/" in x["label"]],
        "all_zero_leaves": [x["label"] for x in leaves if all(v == 0 for v in x["values"])],
    }


# ---------------------------------------------------------------------------
# Naive metric calculations (P4). Independent of the trend package: plain loops,
# exact Fractions, no Decimal, no shared helpers. Percent values are in percent units.
# ---------------------------------------------------------------------------
from fractions import Fraction  # noqa: E402

import yaml  # noqa: E402


def cents(v):
    """Workbook float -> exact 2dp Fraction."""
    return Fraction(round(Fraction(repr(v)) * 100), 100)


def shift(period, k):
    y, m = int(period[:4]), int(period[5:7])
    n = y * 12 + (m - 1) + k
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def monthly(o, zeroed=()):
    """{period: {rev, cogs, gp, opex, opinc}} as Fractions. `zeroed` = {(account, period)}."""
    out = {}
    for i, p in enumerate(o["periods"]):
        t = {"income": Fraction(0), "cogs": Fraction(0), "opex": Fraction(0)}
        for leaf in o["leaves"]:
            if (leaf["label"], p) in zeroed:
                continue
            t[leaf["section"]] += cents(leaf["values"][i])
        out[p] = {"rev": t["income"], "cogs": t["cogs"], "gp": t["income"] - t["cogs"],
                  "opex": t["opex"], "opinc": t["income"] - t["cogs"] - t["opex"]}
    return out


def by_category(o, mapping_path, field, zeroed=()):
    """{period: {category: Fraction}} over opex leaves, using mapping.yaml read raw with PyYAML."""
    entries = {e["account"]: e for e in yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))}
    out = {p: {} for p in o["periods"]}
    for leaf in o["leaves"]:
        if leaf["section"] != "opex":
            continue
        cat = entries[leaf["label"]][field]
        for i, p in enumerate(o["periods"]):
            if (leaf["label"], p) in zeroed:
                continue
            out[p][cat] = out[p].get(cat, Fraction(0)) + cents(leaf["values"][i])
    return out


def window(series, end, n):
    """Sum of series[...] over the n calendar months ending `end`; None if any month is absent."""
    months = [shift(end, -i) for i in range(n)]
    if any(m not in series for m in months):
        return None
    return sum((series[m] for m in months), Fraction(0))


def pct(num, den):
    return None if den <= 0 else num / den * 100


# ---------------------------------------------------------------------------
# Naive flag calculations (P5). Written separately from trend.flags: plain loops over the
# scanned leaves, exact Fractions. Primary basis rule: income lines as reported, every other
# line with the configured adjustments zeroed.
# ---------------------------------------------------------------------------

def _line_series(o, leaf, zeroed):
    ser = {}
    for p, v in zip(o["periods"], leaf["values"]):
        if leaf["section"] != "income" and (leaf["label"], p) in zeroed:
            ser[p] = Fraction(0)
        else:
            ser[p] = cents(v)
    return ser


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def negatives(o, mapping_path, min_abs=Fraction(1)):
    """{(label, period): value} for booked negatives, excluding expected_negative accounts."""
    exp = {e["account"]: bool(e.get("expected_negative")) for e in yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))}
    out = {}
    for leaf in o["leaves"]:
        if exp[leaf["label"]]:
            continue
        for p, v in zip(o["periods"], leaf["values"]):
            c = cents(v)
            if c < 0 and abs(c) >= min_abs:
                out[(leaf["label"], p)] = c
    return out


def spikes_raw(o, zeroed=(), mult=4, min_abs=5000, hist=6, win=12):
    """Every flagged (label, period) before collapsing runs -> 'up' | 'down' (relative to the trailing median)."""
    out = {}
    for leaf in o["leaves"]:
        ser = _line_series(o, leaf, zeroed)
        for p in sorted(ser):
            prior = [ser[shift(p, -k)] for k in range(1, win + 1) if shift(p, -k) in ser]
            if len(prior) < hist:
                continue
            med = _median(prior)
            mad = _median([abs(x - med) for x in prior])
            dev = abs(ser[p] - med)
            if dev > mult * mad and dev > min_abs:
                out[(leaf["label"], p)] = "up" if ser[p] > med else "down"
    return out


def spikes_collapsed(o, **kw):
    """{(label, first_period): (direction, continued_months)}: consecutive months flagged in the same
    direction are one run; a change of direction, or one or more unflagged months, starts a new run."""
    raw = spikes_raw(o, **kw)
    out, first, last, direction = {}, {}, {}, {}
    for (label, p) in sorted(raw, key=lambda k: (k[0], k[1])):
        d = raw[(label, p)]
        if label in last and p == shift(last[label], 1) and d == direction[label]:
            key = (label, first[label])
            out[key] = (out[key][0], out[key][1] + 1)
        else:
            first[label] = p
            direction[label] = d
            out[(label, p)] = (d, 0)
        last[label] = p
    return out


def spikes(o, zeroed=(), **kw):
    """Collapsed run starts only: {(label, first_period)}."""
    return set(spikes_collapsed(o, zeroed=zeroed, **kw))


def level_shifts(o, zeroed=(), w=3, pct_min=20, band=5, min_abs=0):
    out = set()
    for leaf in o["leaves"]:
        ser = _line_series(o, leaf, zeroed)
        qual = set()
        for t in sorted(ser):
            before = [shift(t, -k) for k in range(w, 0, -1)]
            after = [shift(t, k) for k in range(w)]
            if not all(m in ser for m in before + after):
                continue
            sb, sa = sum(ser[m] for m in before), sum(ser[m] for m in after)
            if sb == 0 or abs(sa - sb) * 100 < pct_min * abs(sb):
                continue
            if abs(sa - sb) < min_abs * w:          # optional floor on the change in the monthly average
                continue
            if any(abs(ser[m] * w - sa) * 100 > band * abs(sa) for m in after[1:]):
                continue
            qual.add(t)
        out |= {(leaf["label"], t) for t in qual if shift(t, -1) not in qual}
    return out
