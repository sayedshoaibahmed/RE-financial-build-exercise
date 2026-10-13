import copy
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from evals import qbo_shape
from trend.adapters.base import Adapter, AdapterError
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.adapters.qbo_report import QboReportAdapter, SectionAlias
from trend.config import load_company

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "qbo" / "pnl_2026-09.json"
FIXTURE_SHA256 = "eb126ffdfd63591fc4f63cf22a46b62e5eb131eed183b29fe9d45b274b8dfd59"
FORBIDDEN = ("access_token", "refresh_token", "Bearer", "Authorization", "client_secret", "client_id")
D = Decimal


def load(path, **kw):
    return QboReportAdapter(path, "sandbox", **kw).load()


def doc():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def put(tmp_path, d, name="2026-09.json"):
    p = tmp_path / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def err(tmp_path, d, **kw):
    with pytest.raises(AdapterError) as e:
        load(put(tmp_path, d), **kw)
    return e.value


def top(d, group):
    return next(r for r in d["data"]["Rows"]["Row"] if r["group"] == group)


# ---------------------------------------------------------------- the fixture itself

def test_fixture_is_the_pinned_capture_and_holds_no_secrets():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == FIXTURE_SHA256
    text = raw.decode("utf-8").lower()
    for s in FORBIDDEN:
        assert s.lower() not in text, s


def test_adapter_satisfies_the_protocol():
    assert isinstance(QboReportAdapter(FIXTURE, "x"), Adapter)


# ---------------------------------------------------------------- records versus the file's own Summary rows

def test_section_sums_equal_the_files_summary_rows_to_the_cent():
    records, _ = load(FIXTURE)
    s = defaultdict(lambda: D("0.00"))
    for r in records:
        s[r.section] += r.amount
    d = doc()
    summ = {r["group"]: D(r["Summary"]["ColData"][1]["value"]) for r in d["data"]["Rows"]["Row"]}
    # the literals come from the capture as read by eye; the dict above comes from the file
    assert summ == {"Income": D("6915.88"), "COGS": D("405.00"), "GrossProfit": D("6510.88"),
                    "Expenses": D("2830.10"), "NetOperatingIncome": D("3680.78"),
                    "OtherExpenses": D("2000.00"), "NetOtherIncome": D("-2000.00"), "NetIncome": D("1680.78")}
    assert s["income"] == summ["Income"] == D("6915.88")
    assert s["cogs"] == summ["COGS"] == D("405.00")
    assert s["income"] - s["cogs"] == summ["GrossProfit"] == D("6510.88")
    assert s["opex"] == summ["Expenses"] == D("2830.10")
    assert s["income"] - s["cogs"] - s["opex"] == summ["NetOperatingIncome"] == D("3680.78")
    assert s["other_income"] - s["other_expense"] == summ["NetOtherIncome"] == D("-2000.00")
    assert s["income"] - s["cogs"] - s["opex"] + s["other_income"] - s["other_expense"] == summ["NetIncome"] == D("1680.78")


def test_controls_carry_the_summary_amounts():
    _, controls = load(FIXTURE)
    c = {x.control_name: x.amount for x in controls}
    assert c["Total Income"] == D("6915.88") and c["Total Cost of Goods Sold"] == D("405.00")
    assert c["Gross Profit"] == D("6510.88") and c["Total Expenses"] == D("2830.10")
    assert c["Net Operating Income"] == D("3680.78") and c["Net Other Income"] == D("-2000.00")
    assert c["Net Income"] == D("1680.78")
    assert all(x.period == "2026-09" for x in controls)


def test_parent_own_balance_is_a_record_and_empty_header_is_not():
    records, _ = load(FIXTURE)
    own = [r for r in records if r.account == "Landscaping Services"]
    assert len(own) == 1 and own[0].amount == D("642.50")
    assert own[0].account_path == ["Income", "Landscaping Services"]
    assert not any(r.account == "Job Materials" for r in records)   # header "" -> no own balance
    # Landscaping Services total 4244.08 = own 642.50 + Job Materials 3351.58 + Labor 250.00
    ls = [r for r in records if r.account_path[:2] == ["Income", "Landscaping Services"]]
    assert sum((r.amount for r in ls), D("0.00")) == D("4244.08")


def test_duplicate_labels_under_different_parents_are_accepted():
    records, controls = load(FIXTURE)
    plants = [r for r in records if r.account == "Plants and Soil"]
    assert sorted((r.section, str(r.amount)) for r in plants) == [("income", "2095.08"), ("opex", "105.95")]
    assert plants[0].account_path != plants[1].account_path
    names = [c.control_name for c in controls]
    assert len(names) == len(set(names))                       # repeated "Total Job Materials" got path-qualified
    jm = {c.control_name: c.amount for c in controls if c.control_name.endswith("Total Job Materials")}
    assert jm == {"Income > Landscaping Services > Total Job Materials": D("3351.58"),
                  "Expenses > Job Expenses > Total Job Materials": D("555.65")}


def test_records_shape_and_source_refs():
    records, _ = load(FIXTURE)
    assert len(records) == 28
    assert all(r.source == "qbo" and r.company_id == "sandbox" and r.period == "2026-09" for r in records)
    assert all(isinstance(r.amount, Decimal) and r.amount == r.amount.quantize(D("0.01")) for r in records)
    d = doc()
    for r in records:                                           # every ref resolves to the amount it names
        fname, path = r.source_ref.split("#")
        assert fname == "pnl_2026-09.json"
        cur = d
        for part in path.replace("]", "").replace("[", ".").split("."):
            cur = cur[int(part)] if part.isdigit() else cur[part]
        assert D(cur["value"]) == r.amount
    refs = [r.source_ref for r in records]
    assert len(refs) == len(set(refs))


def test_bare_report_is_accepted_and_refs_drop_the_envelope_prefix(tmp_path):
    p = put(tmp_path, doc()["data"])
    records, _ = load(p)
    assert len(records) == 28 and records[0].source_ref.startswith("2026-09.json#Rows.Row[")


def test_folder_input_reads_every_month_in_date_order(tmp_path):
    for m in ("2026-08", "2026-09"):
        d = doc()
        d["data"]["Header"]["StartPeriod"], d["data"]["Header"]["EndPeriod"] = f"{m}-01", f"{m}-{'31' if m == '2026-08' else '30'}"
        put(tmp_path, d, f"{m}.json")
    records, controls = load(tmp_path)
    assert [r.period for r in records] == ["2026-08"] * 28 + ["2026-09"] * 28
    assert controls[0].period == "2026-08" and controls[-1].period == "2026-09"


# ---------------------------------------------------------------- malformed inputs -> existing AdapterError codes

def test_blank_data_amount_is_non_numeric_never_zero(tmp_path):
    d = doc()
    top(d, "Income")["Rows"]["Row"][0]["ColData"][1]["value"] = ""
    e = err(tmp_path, d)
    assert e.code == "NON_NUMERIC" and "Design income" in str(e) and "ColData[1]" in e.source_ref


def test_blank_summary_amount_is_non_numeric(tmp_path):
    d = doc()
    top(d, "Income")["Summary"]["ColData"][1]["value"] = ""
    assert err(tmp_path, d).code == "NON_NUMERIC"


@pytest.mark.parametrize("bad", ["abc", "1,234.50", "12.345", "$5.00", " ", "1e3"])
def test_non_numeric_or_inexact_amount(tmp_path, bad):
    d = doc()
    top(d, "Income")["Rows"]["Row"][0]["ColData"][1]["value"] = bad
    assert err(tmp_path, d).code == "NON_NUMERIC"


def test_non_numeric_own_balance(tmp_path):
    d = doc()
    ls = top(d, "Income")["Rows"]["Row"][2]
    ls["Header"]["ColData"][1]["value"] = "n/a"
    assert err(tmp_path, d).code == "NON_NUMERIC"


def test_data_row_labelled_total_is_undeclared_total(tmp_path):
    d = doc()
    top(d, "Income")["Rows"]["Row"][0]["ColData"][0]["value"] = "Total Design income"
    assert err(tmp_path, d).code == "UNDECLARED_TOTAL"


def test_unknown_top_level_group_is_unclassified(tmp_path):
    d = doc()
    top(d, "Income")["group"] = "Revenue"
    e = err(tmp_path, d)
    assert e.code == "UNCLASSIFIED_ROW" and "Revenue" in str(e)


def test_top_level_data_row_is_unclassified(tmp_path):
    d = doc()
    d["data"]["Rows"]["Row"].insert(0, {"type": "Data", "ColData": [{"value": "Stray"}, {"value": "1.00"}]})
    assert err(tmp_path, d).code == "UNCLASSIFIED_ROW"


def test_unknown_row_type_is_unclassified(tmp_path):
    d = doc()
    top(d, "Income")["Rows"]["Row"][0]["type"] = "Total"
    assert err(tmp_path, d).code == "UNCLASSIFIED_ROW"


def test_section_without_summary_is_unclassified(tmp_path):
    d = doc()
    del top(d, "Income")["Rows"]["Row"][2]["Summary"]
    assert err(tmp_path, d).code == "UNCLASSIFIED_ROW"


def test_derived_row_with_detail_rows_is_unclassified(tmp_path):
    d = doc()
    top(d, "GrossProfit")["Rows"] = {"Row": [{"type": "Data", "ColData": [{"value": "x"}, {"value": "1.00"}]}]}
    assert err(tmp_path, d).code == "UNCLASSIFIED_ROW"


def test_label_missing_is_unclassified(tmp_path):
    d = doc()
    top(d, "Income")["Rows"]["Row"][0]["ColData"] = []
    assert err(tmp_path, d).code == "UNCLASSIFIED_ROW"


@pytest.mark.parametrize("start,end", [("2026-09-02", "2026-09-30"), ("2026-09-01", "2026-09-29"),
                                       ("2026-09-01", "2026-10-31"), ("2026-9-1", "2026-09-30")])
def test_report_must_cover_one_whole_calendar_month(tmp_path, start, end):
    d = doc()
    d["data"]["Header"]["StartPeriod"], d["data"]["Header"]["EndPeriod"] = start, end
    assert err(tmp_path, d).code == "SOURCE_UNREADABLE"


def test_duplicate_month_in_two_files(tmp_path):
    put(tmp_path, doc(), "a.json")
    put(tmp_path, doc(), "b.json")
    with pytest.raises(AdapterError) as e:
        load(tmp_path)
    assert e.value.code == "DUPLICATE_PERIOD"


def test_report_basis_must_match_across_files(tmp_path):
    put(tmp_path, doc(), "2026-09.json")
    d = doc()
    d["data"]["Header"].update(StartPeriod="2026-08-01", EndPeriod="2026-08-31", ReportBasis="Cash")
    put(tmp_path, d, "2026-08.json")
    with pytest.raises(AdapterError) as e:
        load(tmp_path)
    assert e.value.code == "SOURCE_UNREADABLE" and "ReportBasis" in str(e.value)


def test_missing_report_basis(tmp_path):
    d = doc()
    del d["data"]["Header"]["ReportBasis"]
    assert err(tmp_path, d).code == "SOURCE_UNREADABLE"


def test_wrong_columns(tmp_path):
    d = doc()
    d["data"]["Columns"]["Column"].append({"ColTitle": "Aug 2026", "ColType": "Money"})
    assert err(tmp_path, d).code == "SOURCE_UNREADABLE"


def test_not_a_report_and_bad_json_and_missing_paths(tmp_path):
    (tmp_path / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AdapterError) as e:
        load(tmp_path / "x.json")
    assert e.value.code == "SOURCE_UNREADABLE"
    (tmp_path / "y.json").write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(AdapterError) as e:
        load(tmp_path / "y.json")
    assert e.value.code == "SOURCE_UNREADABLE"
    for p in (tmp_path / "nope", tmp_path / "empty"):
        (tmp_path / "empty").mkdir(exist_ok=True)
        with pytest.raises(AdapterError) as e:
            load(p)
        assert e.value.code == "SOURCE_UNREADABLE"


def test_aliases_rename_section_header_and_control_names():
    sections = {"Income": SectionAlias("income", "Revenue", "Total Revenue"), "COGS": SectionAlias("cogs"),
                "Expenses": SectionAlias("opex"), "OtherExpenses": SectionAlias("other_expense")}
    records, controls = load(FIXTURE, sections=sections, derived={"GrossProfit": "GP", "NetOperatingIncome": None,
                                                                  "NetOtherIncome": None, "NetIncome": None})
    assert {r.account_path[0] for r in records if r.section == "income"} == {"Revenue"}
    names = {c.control_name for c in controls}
    assert "Total Revenue" in names and "GP" in names and "Total Income" not in names


def test_unaliased_section_group_is_rejected(tmp_path):
    e = err(tmp_path, doc(), sections={"Income": SectionAlias("income")})
    assert e.code == "UNCLASSIFIED_ROW"


def test_adapter_error_exposes_a_source_ref(tmp_path):
    d = doc()
    top(d, "Expenses")["Rows"]["Row"][0]["ColData"][1]["value"] = ""
    assert err(tmp_path, d).source_ref.startswith("2026-09.json#data.Rows.Row[3]")


# ---------------------------------------------------------------- parity: ABC as 18 QBO files vs the Excel adapter

ABC_SECTIONS = {"Income": SectionAlias("income", "Income", "Total Income"),
                "COGS": SectionAlias("cogs", "Cost of Goods Sold", "Total Cost of Goods Sold"),
                "Expenses": SectionAlias("opex", "Operating Expenses", "Total Operating Expenses")}
ABC_DERIVED = {"GrossProfit": "Gross Profit", "NetOperatingIncome": "Operating Income", "NetIncome": "Net Income"}


@pytest.fixture(scope="module")
def parity(tmp_path_factory):
    folder = tmp_path_factory.mktemp("abc_qbo")
    files = qbo_shape.write_months(folder)
    q_rec, q_ctl = QboReportAdapter(folder, "abc_inc", sections=ABC_SECTIONS, derived=ABC_DERIVED).load()
    company = load_company(ROOT / "companies" / "abc_inc")
    x_rec, x_ctl = ExcelPLAdapter(company.source, "abc_inc").load()
    return files, q_rec, q_ctl, x_rec, x_ctl


def _key(r):
    return (r.company_id, r.period, r.section, tuple(r.account_path), r.account, r.amount)


def test_parity_builds_18_monthly_files(parity):
    files = parity[0]
    assert len(files) == 18
    assert [f.name for f in files] == sorted(f.name for f in files)
    assert (files[0].name, files[-1].name) == ("2025-01.json", "2026-06.json")


def test_parity_records_identical_except_source_and_source_ref(parity):
    _, q_rec, _, x_rec, _ = parity
    assert len(x_rec) == 1494
    assert len(q_rec) == len(x_rec)
    assert sorted(map(_key, q_rec)) == sorted(map(_key, x_rec))
    assert {r.source for r in q_rec} == {"qbo"} and {r.source for r in x_rec} == {"excel"}
    assert all(r.source_ref.count("#") == 1 for r in q_rec)


def test_parity_record_order_is_chronological_then_document_order(parity):
    _, q_rec, _, _, _ = parity
    assert [r.period for r in q_rec] == sorted(r.period for r in q_rec)


def test_parity_every_excel_control_is_present_with_the_same_amount(parity):
    _, _, q_ctl, _, x_ctl = parity
    q = {(c.control_name, c.period): c.amount for c in q_ctl}
    assert len(x_ctl) == 8 * 18
    missing = [k for k in ((c.control_name, c.period) for c in x_ctl) if k not in q]
    assert missing == []
    assert [c for c in x_ctl if q[(c.control_name, c.period)] != c.amount] == []


def test_parity_parent_with_own_balance_form_is_exercised(parity):
    files, q_rec, _, _, _ = parity
    d = json.loads(files[0].read_text(encoding="utf-8"))
    found = []

    def walk(rows):
        for r in rows:
            if r["type"] == "Section":
                h = r.get("Header", {}).get("ColData", [{}, {}])
                if h[0].get("value") == "Marketing/Advertising":
                    found.append(r)
                walk(r.get("Rows", {}).get("Row", []))
    walk(d["data"]["Rows"]["Row"])
    assert len(found) == 1 and found[0]["Header"]["ColData"][1]["value"] != "" and "Rows" not in found[0]
    ma = [r for r in q_rec if r.account == "Marketing/Advertising" and r.period == "2025-01"]
    assert len(ma) == 1 and ma[0].amount == D("310.80")
    assert ma[0].account_path == ["Operating Expenses", "Marketing", "Marketing/Advertising"]
