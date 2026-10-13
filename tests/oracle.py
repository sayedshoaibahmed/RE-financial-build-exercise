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
        elif label in DERIVED:
            derived.append((r, label))
        elif label.startswith("Total"):
            subtotals.append((r, label))
        elif all_num:
            leaves.append({"row": r, "label": label, "values": vals})
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
