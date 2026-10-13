from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from openpyxl.utils import get_column_letter

from tests import oracle
from tests.fixtures import builders as b
from trend.adapters.base import Adapter, AdapterError
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.config import load_company, parse_source
from trend.model import ControlTotal, Record

ROOT = Path(__file__).resolve().parents[2]


def load_tiny(tmp_path, rows=None, order="oldest", src=None, **wkw):
    p = tmp_path / "t.xlsx"
    b.write(p, rows if rows is not None else b.default_rows(), order=order, **wkw)
    cfg = parse_source(b.source(path=str(p), **(src or {})), base_dir=tmp_path)
    return ExcelPLAdapter(cfg, company_id="tiny").load()


def err(tmp_path, rows, **kw):
    with pytest.raises(AdapterError) as e:
        load_tiny(tmp_path, rows, **kw)
    return e.value


# ---------- protocol / basic shape ----------

def test_adapter_satisfies_protocol(tmp_path):
    cfg = parse_source(b.source(path=str(tmp_path / "x.xlsx")), base_dir=tmp_path)
    assert isinstance(ExcelPLAdapter(cfg, "tiny"), Adapter)


def test_tiny_records_and_controls(tmp_path):
    records, controls = load_tiny(tmp_path)
    leaves = ["Sales", "Fees/Misc: Setup", "Hosting", "Rent", "Salaries", "Payroll Taxes", "Marketing"]
    assert len(records) == len(leaves) * len(b.PERIODS)
    assert all(isinstance(r, Record) for r in records)
    assert all(isinstance(c, ControlTotal) for c in controls)
    assert {r.account for r in records} == set(leaves)
    # 7 control rows (3 section totals, 1 group total, 3 derived)
    assert len(controls) == 7 * len(b.PERIODS)
    assert {r.company_id for r in records} == {"tiny"} and {r.source for r in records} == {"excel"}


def test_record_fields_sections_paths_refs_and_decimals(tmp_path):
    rows = b.default_rows()
    records, _ = load_tiny(tmp_path, rows)
    sales = [r for r in records if r.account == "Sales"]
    assert [r.period for r in sales] == b.PERIODS  # chronological, regardless of sheet order
    r = sales[0]
    assert r.section == "income" and r.account_path == ["Income", "Sales"]
    assert r.source_ref == f"Income Statement!C{b.row_of(rows, 'Sales')}"
    assert isinstance(r.amount, Decimal) and r.amount == Decimal("1000.00")
    sal = [x for x in records if x.account == "Salaries"][0]
    assert sal.section == "opex" and sal.account_path == ["Operating Expenses", "Payroll", "Salaries"]
    rent = [x for x in records if x.account == "Rent"][0]
    assert rent.account_path == ["Operating Expenses", "Rent"]
    mkt = [x for x in records if x.account == "Marketing"][0]  # leaf named like a group elsewhere is fine
    assert mkt.account_path == ["Operating Expenses", "Marketing"]  # after group total, path resets


def test_amounts_are_quantized_to_cents(tmp_path):
    rows = b.default_rows()
    rows[1][2] = [1000.004, 1100.1 + 0.2, 1199.999]  # float noise
    records, _ = load_tiny(tmp_path, rows)
    got = [r.amount for r in records if r.account == "Sales"]
    assert got == [Decimal("1000.00"), Decimal("1100.30"), Decimal("1200.00")]


# ---------- column order ----------

@pytest.mark.parametrize("order", ["oldest", "newest"])
def test_both_column_orders_give_identical_results(tmp_path, order):
    rows = b.default_rows()
    ref_r, ref_c = load_tiny(tmp_path, rows, order="oldest")
    sub = tmp_path / order
    sub.mkdir()
    records, controls = load_tiny(sub, rows, order=order)
    key = lambda r: (r.account, r.period, r.amount)
    assert sorted(map(key, records)) == sorted(map(key, ref_r))
    assert sorted((c.control_name, c.period, c.amount) for c in controls) == \
        sorted((c.control_name, c.period, c.amount) for c in ref_c)
    assert [r.period for r in records if r.account == "Sales"] == b.PERIODS


def test_source_ref_follows_the_actual_column_when_newest_first(tmp_path):
    rows = b.default_rows()
    records, _ = load_tiny(tmp_path, rows, order="newest")
    r = [x for x in records if x.account == "Sales" and x.period == "2025-03"][0]
    assert r.source_ref == f"Income Statement!C{b.row_of(rows, 'Sales')}"  # newest is the first column
    r = [x for x in records if x.account == "Sales" and x.period == "2025-01"][0]
    assert r.source_ref == f"Income Statement!E{b.row_of(rows, 'Sales')}"


def test_empty_columns_inside_used_range_are_not_periods(tmp_path):
    p = tmp_path / "t.xlsx"
    b.write(p, b.default_rows(), extra_empty_cols=5)
    assert openpyxl.load_workbook(p)["Income Statement"].max_column > 3 + len(b.PERIODS)
    records, _ = load_tiny(tmp_path, extra_empty_cols=5)
    assert {r.period for r in records} == set(b.PERIODS)


# ---------- subtotals / derived become controls, never records ----------

def test_group_subtotal_and_derived_rows_are_controls_not_records(tmp_path):
    rows = b.default_rows()
    records, controls = load_tiny(tmp_path, rows)
    names = {r.account for r in records}
    for n in ("Total Payroll", "Total Income", "Gross Profit", "Operating Income", "Net Income",
              "Total Operating Expenses", "Payroll", "Income"):
        assert n not in names
    got = {(c.control_name, c.period): c for c in controls}
    c = got[("Total Payroll", "2025-02")]
    assert c.amount == Decimal("440.00")
    assert c.source_ref == f"Income Statement!D{b.row_of(rows, 'Total Payroll')}"
    assert ("Gross Profit", "2025-01") in got and ("Net Income", "2025-03") in got


# ---------- labels ----------

def test_slash_and_colon_labels_preserved_unchanged(tmp_path):
    records, _ = load_tiny(tmp_path)
    r = [x for x in records if "/" in x.account][0]
    assert r.account == "Fees/Misc: Setup"
    assert r.account_path == ["Income", "Fees/Misc: Setup"]  # a list element, not split on "/"
    assert len(r.account_path) == 2


def test_labels_are_stripped(tmp_path):
    rows = b.default_rows()
    rows[1][1] = "  Sales  "
    records, _ = load_tiny(tmp_path, rows)
    assert "Sales" in {r.account for r in records}


# ---------- error cases ----------

def test_undeclared_total_is_an_error(tmp_path):
    rows = b.default_rows()
    rows.insert(4, [None, "Total Mktg", [1, 1, 1]])  # inside COGS section
    e = err(tmp_path, rows)
    assert e.code == "UNDECLARED_TOTAL"
    assert e.source_ref.startswith("Income Statement!B")


def test_undeclared_total_in_section_column_is_an_error(tmp_path):
    rows = b.default_rows()
    rows.insert(4, ["Total Widgets", None, [1, 1, 1]])
    assert err(tmp_path, rows).code == "UNDECLARED_TOTAL"


def test_undeclared_total_without_numbers_is_still_an_error(tmp_path):
    rows = b.default_rows()
    rows.insert(4, [None, "Total Widgets", None])
    assert err(tmp_path, rows).code == "UNDECLARED_TOTAL"


def test_blank_leaf_cell_is_non_numeric_not_zero(tmp_path):
    rows = b.default_rows()
    rows[1][2] = [1000, None, 1200]
    e = err(tmp_path, rows)
    assert e.code == "NON_NUMERIC"
    assert e.source_ref == f"Income Statement!D{b.row_of(rows, 'Sales')}"


def test_text_in_a_leaf_cell_is_non_numeric(tmp_path):
    rows = b.default_rows()
    rows[1][2] = [1000, "n/a", 1200]
    assert err(tmp_path, rows).code == "NON_NUMERIC"


def test_blank_control_cell_is_non_numeric(tmp_path):
    rows = b.default_rows()
    rows[3][2] = [1050, None, 1270]
    assert err(tmp_path, rows).code == "NON_NUMERIC"


def test_bool_cell_is_non_numeric(tmp_path):
    rows = b.default_rows()
    rows[1][2] = [1000, True, 1200]
    assert err(tmp_path, rows).code == "NON_NUMERIC"


def test_section_header_row_containing_numbers_is_unclassified(tmp_path):
    rows = b.default_rows()
    rows[0][2] = [1, 2, 3]  # the "Income" header carries numbers
    e = err(tmp_path, rows)
    assert e.code == "UNCLASSIFIED_ROW"
    assert e.source_ref == f"Income Statement!A{b.FIRST_ROW}"


def test_group_header_row_containing_numbers_is_unclassified(tmp_path):
    rows = b.default_rows()
    rows[b.row_of(rows, "Payroll") - b.FIRST_ROW][2] = [1, 2, 3]
    assert err(tmp_path, rows).code == "UNCLASSIFIED_ROW"


def test_period_header_cell_that_is_a_number_is_unclassified(tmp_path):
    e = err(tmp_path, b.default_rows(), header_values=["Jan 2025", 2025, "Mar 2025"])
    assert e.code == "UNCLASSIFIED_ROW"
    assert e.source_ref == "Income Statement!D5"


def test_period_header_text_that_does_not_parse_is_unclassified(tmp_path):
    e = err(tmp_path, b.default_rows(), header_values=["Jan 2025", "Q1 2025", "Mar 2025"])
    assert e.code == "UNCLASSIFIED_ROW"


def test_numbers_outside_any_section_are_unclassified(tmp_path):
    rows = b.default_rows()
    rows.insert(0, [None, "Stray", [1, 2, 3]])
    e = err(tmp_path, rows)
    assert e.code == "UNCLASSIFIED_ROW"


def test_numbers_after_a_section_total_are_unclassified(tmp_path):
    rows = b.default_rows()
    rows.insert(4, [None, "Late Row", [1, 2, 3]])  # between Total Income and the COGS header
    assert err(tmp_path, rows).code == "UNCLASSIFIED_ROW"


def test_unknown_text_row_without_numbers_is_unclassified(tmp_path):
    rows = b.default_rows()
    rows.insert(2, [None, "Sub Header?", None])  # looks like a header but is not declared
    assert err(tmp_path, rows).code == "UNCLASSIFIED_ROW"


def test_unclosed_group_is_unclassified(tmp_path):
    rows = [r for r in b.default_rows() if r[1] != "Total Payroll"]
    assert err(tmp_path, rows).code == "UNCLASSIFIED_ROW"


def test_section_total_in_the_wrong_section_is_unclassified(tmp_path):
    rows = b.default_rows()
    i = b.row_of(rows, "Total Income") - b.FIRST_ROW
    rows[i][0] = "Total Operating Expenses"
    assert err(tmp_path, rows).code == "UNCLASSIFIED_ROW"


def test_duplicate_period_columns(tmp_path):
    e = err(tmp_path, b.default_rows(), header_values=["Jan 2025", "Feb 2025", "Jan 2025"])
    assert e.code == "DUPLICATE_PERIOD"
    assert e.source_ref == "Income Statement!E5"


def test_formula_without_cached_value_is_no_cached_values(tmp_path):
    rows = b.default_rows()
    i = b.row_of(rows, "Total Income") - b.FIRST_ROW
    rows[i][2] = ["=SUM(C8:C9)", "=SUM(D8:D9)", "=SUM(E8:E9)"]  # openpyxl writes no cached result
    e = err(tmp_path, rows)
    assert e.code == "NO_CACHED_VALUES"
    assert "open and save in Excel" in str(e).lower() or "open and save in excel" in str(e).lower()
    assert e.source_ref == f"Income Statement!C{b.row_of(rows, 'Total Income')}"


def test_formula_in_a_leaf_without_cache_is_no_cached_values(tmp_path):
    rows = b.default_rows()
    rows[1][2] = [1000, "=1+1", 1200]
    assert err(tmp_path, rows).code == "NO_CACHED_VALUES"


def test_missing_sheet_or_file_is_a_clear_error(tmp_path):
    p = tmp_path / "t.xlsx"
    b.write(p, b.default_rows())
    cfg = parse_source(b.source(path=str(p), sheet="Nope"), base_dir=tmp_path)
    with pytest.raises(AdapterError) as e:
        ExcelPLAdapter(cfg, "tiny").load()
    assert e.value.code == "SOURCE_UNREADABLE"
    cfg = parse_source(b.source(path=str(tmp_path / "missing.xlsx")), base_dir=tmp_path)
    with pytest.raises(AdapterError) as e:
        ExcelPLAdapter(cfg, "tiny").load()
    assert e.value.code == "SOURCE_UNREADABLE"


def test_no_periods_in_header_is_an_error(tmp_path):
    e = err(tmp_path, b.default_rows(), header_values=[None, None, None])
    assert e.code == "UNCLASSIFIED_ROW"


# ---------- the real ABC workbook; every expectation comes from tests/oracle.py ----------

@pytest.fixture(scope="module")
def abc():
    company = load_company(ROOT / "companies" / "abc_inc")
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    return records, controls, oracle.scan()


def test_abc_record_count_is_oracle_leaves_times_oracle_periods(abc):
    records, _, o = abc
    assert len(records) == len(o["leaves"]) * len(o["periods"])
    assert len({r.period for r in records}) == len(o["periods"])
    assert {r.period for r in records} == set(o["periods"])


def test_abc_every_leaf_label_preserved_and_slash_labels_unchanged(abc):
    records, _, o = abc
    assert {r.account for r in records} == set(o["leaf_labels"])
    got_slash = {r.account for r in records if "/" in r.account}
    assert got_slash == set(o["slash_labels"]) and len(o["slash_labels"]) > 0
    for r in records:
        assert r.account_path[-1] == r.account  # leaf label is the last path element, never split


def test_abc_no_subtotal_derived_or_header_row_among_records(abc):
    records, _, o = abc
    banned = {l for _, l in o["subtotals"] + o["derived"] + o["headers"]}
    assert not banned & {r.account for r in records}
    assert not banned & {x for r in records for x in r.account_path[2:]}  # only leaf/group names deeper


def test_abc_amounts_match_oracle_cell_by_cell(abc):
    records, _, o = abc
    by = {(r.account, r.period): r for r in records}
    for leaf in o["leaves"]:
        for p, col, v in zip(o["periods"], o["period_cols"], leaf["values"]):
            r = by[(leaf["label"], p)]
            assert abs(float(r.amount) - v) < 0.005
            assert r.source_ref == f"Income Statement!{get_column_letter(col)}{leaf['row']}"


def test_abc_controls_cover_all_control_rows_and_periods(abc):
    _, controls, o = abc
    rows = o["subtotals"] + o["derived"]
    assert len(controls) == len(rows) * len(o["periods"])
    assert {c.control_name for c in controls} == {l for _, l in rows}
    by = {(c.control_name, c.period): c for c in controls}
    for r, label in rows:
        for p, col in zip(o["periods"], o["period_cols"]):
            assert by[(label, p)].source_ref == f"Income Statement!{get_column_letter(col)}{r}"


def test_abc_records_are_chronological_per_account(abc):
    records, _, o = abc
    first = o["leaf_labels"][0]
    assert [r.period for r in records if r.account == first] == sorted(o["periods"])


def test_abc_sections_and_groups(abc):
    records, _, o = abc
    assert {r.section for r in records} == {"income", "cogs", "opex"}
    grouped = [r for r in records if len(r.account_path) == 3]
    assert {r.account_path[1] for r in grouped} == {"S & M Payroll", "Marketing"}
