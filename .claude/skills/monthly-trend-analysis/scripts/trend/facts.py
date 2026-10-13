"""Fact ids: parse them, give each a unit, and turn values into display strings / file text / cell values.

Fact id grammar (PLAN 4):  <basis>.<metric>[.<dimension>][.<view>].<YYYY-MM>
  basis      rep | adj | delta (adj - rep)
  metric     rev | rev.stream | rev.mix | cogs | opex | opex.fn | opex.nat | opex.line | gp | gm_pct | opinc |
             opinc_margin_pct | net_income
  dimension  total, or a slug (stream / function / nature / line)
Commentary quotes a number as {{<fact id>}}; the digest maps each allowed id to its display string.
Rounding happens only in this module (display / to_text / to_cell); the engine keeps 60 digits.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .metrics import NA, PREC

BASIS_NAME = {"rep": "reported", "adj": "adjusted", "delta": "delta"}
_SIMPLE = ("gp", "gm_pct", "opinc", "opinc_margin_pct", "net_income")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PERIOD = re.compile(r"\d{4}-\d{2}")
_SLUG = re.compile(r"[a-z0-9_]+")


def month_name(period):
    return f"{_MONTHS[int(period[5:7]) - 1]} {period[:4]}"


def parse_fact_id(fid):
    """-> {basis, metric, dimension, view, period}. ValueError if the id is not in the grammar."""
    parts = fid.split(".")
    if len(parts) < 3 or parts[0] not in BASIS_NAME or not _PERIOD.fullmatch(parts[-1]):
        raise ValueError(f"not a fact id: {fid!r}")
    basis, period, mid = parts[0], parts[-1], parts[1:-1]
    head = mid[0]
    if head in _SIMPLE:
        metric, dim, rest = head, "", mid[1:]
    elif head == "cogs" and len(mid) > 1 and mid[1] == "total":
        metric, dim, rest = "cogs", "total", mid[2:]
    elif head == "rev" and len(mid) > 1 and mid[1] == "total":
        metric, dim, rest = "rev", "total", mid[2:]
    elif head == "rev" and len(mid) > 2 and mid[1] in ("stream", "mix"):
        metric, dim, rest = f"rev.{mid[1]}", mid[2], mid[3:]
    elif head == "opex" and len(mid) > 1 and mid[1] == "total":
        metric, dim, rest = "opex", "total", mid[2:]
    elif head == "opex" and len(mid) > 2 and mid[1] in ("fn", "nat", "line"):
        metric, dim, rest = f"opex.{mid[1]}", mid[2], mid[3:]
    else:
        raise ValueError(f"not a fact id: {fid!r}")
    if len(rest) > 1 or (dim and not _SLUG.fullmatch(dim)) or any(not _SLUG.fullmatch(r) for r in rest):
        raise ValueError(f"not a fact id: {fid!r}")
    return {"basis": basis, "metric": metric, "dimension": dim, "view": rest[0] if rest else "", "period": period}


def unit_of(parsed):
    """usd | pct | pp. A difference of two percentages is in percentage points."""
    view, metric = parsed["view"], parsed["metric"]
    if view.endswith("_pp"):
        unit = "pp"
    elif metric in ("gm_pct", "opinc_margin_pct", "rev.mix") or "pct" in view:
        unit = "pct"
    else:
        unit = "usd"
    if parsed["basis"] == "delta" and unit == "pct":
        unit = "pp"
    return unit


def _q(value, exp):
    with localcontext() as c:
        c.prec = PREC
        q = value.quantize(Decimal(1).scaleb(exp), rounding=ROUND_HALF_UP)
    return abs(q) if q == 0 else q            # never show "-0.00"


def display(value, unit):
    """The string a reader sees: 195,898.16 | 70.15% | 3.11 pp | n/a | insufficient data (missing: ...)."""
    if isinstance(value, NA):
        return str(value)
    if unit == "usd":
        return f"{_q(value, -2):,}"
    if unit == "pct":
        return f"{_q(value, -2):,}%"
    if unit == "pp":
        return f"{_q(value, -2):,} pp"
    if unit == "count":
        return str(value)
    raise ValueError(unit)


def to_text(value, unit):
    """Text for JSON files: cents exactly, ratios to 10 dp. None for a value that does not exist."""
    if isinstance(value, NA):
        return None
    return str(_q(value, -2 if unit == "usd" else -10))


def to_cell(value, unit):
    """Value for a spreadsheet cell: Decimal (2 dp for usd, 4 dp for ratios) or the n/a text."""
    if isinstance(value, NA):
        return str(value)
    return _q(value, -2 if unit == "usd" else -4)


def observed_text(o):
    def one(v):
        if v is None:
            return "none"
        return str(_q(v, -2)) if isinstance(v, Decimal) else str(v)
    if isinstance(o, dict):
        return ", ".join(f"{k}={one(v)}" for k, v in o.items())
    return str(o)
