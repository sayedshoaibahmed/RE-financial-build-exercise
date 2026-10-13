"""Synthetic companies for eval cases, built in code from the test fixtures (tests/fixtures). Nothing here is read
from or written to companies/; every company is built in a temp folder per run.

    sample_co_b   G06: another company. Different accounts (Sales, Fees/Misc: Setup, Hosting, Rent, a Payroll group,
                  Marketing), 14 months Jan 2025 - Feb 2026, columns newest-first like ABC, no adjustments
    quiet_co      G09: 14 months of flat data (every line within +/-1% of its level), oldest-first columns

Values carry cents so the to-the-cent checks are meaningful. Control rows are literal sums computed here, so
reconciliation passes. A case refers to a builder as {"company": "fixture:<name>"}.
"""
import yaml

from tests.fixtures import builders as b
from tests.fixtures import companies, mini


def _r(x):
    return round(x, 2)


def _sum(*lists):
    return [_r(sum(v)) for v in zip(*lists)]


def sample_co_b(tmp):
    """-> company folder."""
    periods = mini.months(14, "2025-01")
    n = len(periods)
    sales = [_r(20000.25 + 700 * i + (i % 3) * 250.5) for i in range(n)]
    setup = [_r(1500.10 - 40 * i) for i in range(n)]
    hosting = [_r(6000.40 + 150 * i) for i in range(n)]
    rent = [3000.00] * n
    salaries = [_r(9000.00 + (300 if i >= 7 else 0)) for i in range(n)]
    taxes = [_r(900.00 + (30 if i >= 7 else 0)) for i in range(n)]
    mkt = [_r(1200.75 + 100 * (i % 4)) for i in range(n)]
    t_inc = _sum(sales, setup)
    gp = [_r(a - c) for a, c in zip(t_inc, hosting)]
    t_pay = _sum(salaries, taxes)
    t_opex = _sum(rent, t_pay, mkt)
    opinc = [_r(g - o) for g, o in zip(gp, t_opex)]
    rows = [
        ["Income", None, None], [None, "Sales", sales], [None, "Fees/Misc: Setup", setup], ["Total Income", None, t_inc],
        ["Cost of Goods Sold", None, None], [None, "Hosting", hosting], ["Total Cost of Goods Sold", None, hosting],
        [None, "Gross Profit", gp],
        ["Operating Expenses", None, None], [None, "Rent", rent], [None, "Payroll", None], [None, "Salaries", salaries],
        [None, "Payroll Taxes", taxes], [None, "Total Payroll", t_pay], [None, "Marketing", mkt],
        ["Total Operating Expenses", None, t_opex], [None, "Operating Income", opinc], [None, "Net Income", opinc],
    ]
    cdir = tmp / "companies" / "sample_co_b"
    cdir.mkdir(parents=True)
    xlsx = tmp / "sample_co_b.xlsx"
    b.write(xlsx, rows, order="newest", periods=periods)
    doc = {"source": b.source(path=str(xlsx))}
    (cdir / "company.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    (cdir / "mapping.yaml").write_text(yaml.safe_dump(companies.entries(), sort_keys=False), encoding="utf-8")
    return cdir


# +/-1% around the level, a fixed pattern (no randomness)
_WIGGLE = [0, 0.6, -0.4, 0.9, -0.8, 0.3, -1.0, 0.5, -0.2, 1.0, -0.6, 0.2, -0.3, 0.7]


def quiet_co(tmp):
    """-> company folder."""
    n = len(_WIGGLE)
    flat = lambda level, shift=0: [_r(level * (1 + _WIGGLE[(i + shift) % n] / 100)) for i in range(n)]
    mini.build(tmp, mini.months(n, "2025-01"), flat(100000.00), flat(30000.00, 3), flat(12000.00, 5), flat(8000.00, 9))
    return tmp / "companies" / "mini"


BUILDERS = {"sample_co_b": sample_co_b, "quiet_co": quiet_co}
