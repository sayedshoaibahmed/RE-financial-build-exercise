import dataclasses
import re
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl.utils import get_column_letter

from tests import oracle
from tests.fixtures import builders as b
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.config import load_company, parse_source
from trend.reconcile import BlockedError, ControlResult, ReconResult, ensure_ok, reconcile

ROOT = Path(__file__).resolve().parents[2]
D = Decimal


def tiny(tmp_path, rows=None, with_other=False, tamper=None):
    """Load a tiny workbook; `tamper(rows)` edits leaf values AFTER controls were computed."""
    rows = rows if rows is not None else b.default_rows(with_other=with_other)
    if tamper:
        tamper(rows)
    p = tmp_path / "t.xlsx"
    b.write(p, rows)
    src = b.source(path=str(p))
    if with_other:
        src["sections"] = src["sections"] + b.OTHER_SECTIONS
    cfg = parse_source(src, base_dir=tmp_path)
    records, controls = ExcelPLAdapter(cfg, "tiny").load()
    return cfg, records, controls


def fails(res):
    return {(r.control_name, r.period) for r in res.results if not r.ok}


def leaf(rows, label):
    return next(r for r in rows if r[1] == label)


# ---------- matching fixture passes ----------

def test_matching_fixture_passes_all_controls(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    res = reconcile(records, controls, cfg)
    assert isinstance(res, ReconResult) and res.status == "OK"
    assert len(res.results) == 7 * len(b.PERIODS)
    assert all(isinstance(r, ControlResult) and r.ok for r in res.results)
    assert res.failures == []
    ensure_ok(res)  # does not raise


def test_result_fields(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    res = reconcile(records, controls, cfg)
    r = next(x for x in res.results if x.control_id == "C1" and x.period == "2025-02")
    assert r.control_name == "Total Income"
    assert r.expected == D("1160.00") and r.actual == D("1160.00") and r.difference == D("0.00")
    assert r.source_ref == "Income Statement!D10"  # row of Total Income, column of Feb 2025
    ids = {x.control_id for x in res.results}
    assert ids == {"C1", "C2", "C3", "C4", "C6", "C7", "C8"}  # one group in this fixture


def test_two_groups_get_c4_and_c5(tmp_path):
    cfg, _, _ = tiny(tmp_path)
    src = b.source(path="x.xlsx")
    src["groups"] = src["groups"] + [{"header": "Mkt", "total": "Total Mkt"}]
    rows = b.default_rows()
    i = b.row_of(rows, "Marketing") - b.FIRST_ROW
    rows[i:i + 1] = [[None, "Mkt", None], [None, "Ads", [10, 20, 30]], [None, "Total Mkt", [10, 20, 30]]]
    p = tmp_path / "g.xlsx"
    b.write(p, rows)
    src["path"] = str(p)
    cfg2 = parse_source(src, base_dir=tmp_path)
    records, controls = ExcelPLAdapter(cfg2, "tiny").load()
    res = reconcile(records, controls, cfg2)
    assert res.status == "OK"
    assert {x.control_name: x.control_id for x in res.results if x.control_id in ("C4", "C5")} == \
        {"Total Payroll": "C4", "Total Mkt": "C5"}


# ---------- tampered leaf ----------

def test_tampered_income_leaf_fails_c1_c3_c7_c8_with_exact_difference(tmp_path):
    def t(rows):
        leaf(rows, "Sales")[2] = [1000, 1100 + 1000, 1200]  # Feb +1000
    cfg, records, controls = tiny(tmp_path, tamper=t)
    res = reconcile(records, controls, cfg)
    assert res.status == "BLOCKED"
    assert fails(res) == {("Total Income", "2025-02"), ("Gross Profit", "2025-02"),
                          ("Operating Income", "2025-02"), ("Net Income", "2025-02")}
    for r in res.failures:
        assert r.difference == D("1000.00")  # actual (recomputed) - expected (workbook)
        assert r.actual - r.expected == r.difference
    ids = {r.control_id for r in res.failures}
    assert ids == {"C1", "C3", "C7", "C8"}
    gp = next(r for r in res.failures if r.control_id == "C3")
    assert gp.source_ref == f"Income Statement!D{b.row_of(b.default_rows(), 'Gross Profit')}"


def test_tampered_opex_group_leaf_fails_c4_c6_c7_c8(tmp_path):
    def t(rows):
        leaf(rows, "Salaries")[2] = [400, 400, 400 - 25]  # Mar -25
    cfg, records, controls = tiny(tmp_path, tamper=t)
    res = reconcile(records, controls, cfg)
    assert {r.control_id for r in res.failures} == {"C4", "C6", "C7", "C8"}
    assert {r.period for r in res.failures} == {"2025-03"}
    by = {r.control_id: r.difference for r in res.failures}
    assert by["C4"] == D("-25.00") and by["C6"] == D("-25.00")
    assert by["C7"] == D("25.00") and by["C8"] == D("25.00")  # opex lower -> op income higher


def test_tampered_cogs_leaf_fails_c2_c3_c7_c8(tmp_path):
    def t(rows):
        leaf(rows, "Hosting")[2] = [301, 300, 350]
    cfg, records, controls = tiny(tmp_path, tamper=t)
    res = reconcile(records, controls, cfg)
    assert {r.control_id for r in res.failures} == {"C2", "C3", "C7", "C8"}


# ---------- tolerance edges ----------

def _with_income_control(controls, period, delta):
    out = []
    for c in controls:
        if c.control_name == "Total Income" and c.period == period:
            c = dataclasses.replace(c, amount=c.amount + D(delta))
        out.append(c)
    return out


@pytest.mark.parametrize("delta,ok", [
    ("0.009", True), ("-0.009", True), ("0.010", True), ("-0.010", True),
    ("0.011", False), ("-0.011", False), ("0", True),
])
def test_default_tolerance_is_one_cent(tmp_path, delta, ok):
    cfg, records, controls = tiny(tmp_path)
    res = reconcile(records, _with_income_control(controls, "2025-02", delta), cfg)
    c1 = next(r for r in res.results if r.control_id == "C1" and r.period == "2025-02")
    assert c1.ok is ok
    assert (res.status == "OK") is ok
    assert c1.difference == -D(delta)  # actual - expected


def test_tolerance_is_configurable(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    c = _with_income_control(controls, "2025-02", "0.011")
    assert reconcile(records, c, cfg, tolerance=D("0.02")).status == "OK"
    c = _with_income_control(controls, "2025-02", "0.009")
    assert reconcile(records, c, cfg, tolerance=D("0")).status == "BLOCKED"


def test_negative_tolerance_rejected(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    with pytest.raises(ValueError):
        reconcile(records, controls, cfg, tolerance=D("-0.01"))


# ---------- other sections (C8) and missing pieces ----------

def test_c8_includes_other_sections(tmp_path):
    cfg, records, controls = tiny(tmp_path, with_other=True)
    res = reconcile(records, controls, cfg)
    assert res.status == "OK"
    ni = next(r for r in res.results if r.control_id == "C8" and r.period == "2025-01")
    opinc = next(r for r in res.results if r.control_id == "C7" and r.period == "2025-01")
    assert ni.actual == opinc.actual + D("5.00") - D("2.00")


def test_c8_detects_tampered_other_income(tmp_path):
    def t(rows):
        leaf(rows, "Interest")[2] = [5, 5, 9]
    cfg, records, controls = tiny(tmp_path, with_other=True, tamper=t)
    res = reconcile(records, controls, cfg)
    assert fails(res) == {("Total Other Income", "2025-03"), ("Net Income", "2025-03")}


def test_missing_control_cell_fails_closed(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    kept = [c for c in controls if not (c.control_name == "Gross Profit" and c.period == "2025-01")]
    res = reconcile(records, kept, cfg)
    assert res.status == "BLOCKED"
    r = next(x for x in res.failures)
    assert (r.control_name, r.period) == ("Gross Profit", "2025-01")
    assert r.expected is None and r.difference is None and r.reason == "MISSING_CONTROL"


def test_blocked_raises_with_recon_fail_and_blocks_downstream(tmp_path):
    def t(rows):
        leaf(rows, "Sales")[2] = [1, 1100, 1200]
    cfg, records, controls = tiny(tmp_path, tamper=t)
    res = reconcile(records, controls, cfg)
    with pytest.raises(BlockedError) as e:
        ensure_ok(res)
    assert e.value.reason == "RECON_FAIL"
    assert e.value.result is res
    assert "Income Statement!C" in str(e.value)  # cell reference is in the message


def test_reconcile_does_not_mutate_inputs(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    r0, c0 = list(records), list(controls)
    reconcile(records, controls, cfg)
    assert records == r0 and controls == c0


# ---------- C9a / C9b: function and nature completeness ----------

def tiny_mapped(tmp_path, tamper=None, **kw):
    from tests.fixtures import companies as co
    from trend.categories import load_vocabulary
    from trend.config import load_company
    from trend.mapping import load_mapping, map_records
    cdir = co.write_company(tmp_path, rows=b.default_rows())
    company = load_company(cdir)
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    mapped = map_records(records, load_mapping(cdir, load_vocabulary()), company, cdir)
    return company.source, records, controls, mapped


def test_c9a_c9b_pass_when_mapping_is_complete(tmp_path):
    cfg, records, controls, mapped = tiny_mapped(tmp_path)
    res = reconcile(records, controls, cfg, mapped=mapped)
    assert res.status == "OK"
    assert {r.control_id for r in res.results} >= {"C9a", "C9b"}
    c9 = [r for r in res.results if r.control_id in ("C9a", "C9b")]
    assert len(c9) == 2 * len(b.PERIODS) and all(r.ok for r in c9)
    assert [x.control_id for x in res.not_applicable] == ["C5"]
    a = next(r for r in c9 if r.control_id == "C9a" and r.period == "2025-02")
    assert a.expected == a.actual and a.source_ref == "Income Statement!D" + str(b.row_of(b.default_rows(), "Total Operating Expenses"))


def test_c9_fails_when_a_mapped_opex_record_is_missing(tmp_path):
    cfg, records, controls, mapped = tiny_mapped(tmp_path)
    victim = next(m for m in mapped if m.record.account == "Rent" and m.record.period == "2025-02")
    res = reconcile(records, controls, cfg, mapped=[m for m in mapped if m is not victim])
    assert res.status == "BLOCKED"
    assert {(r.control_id, r.period) for r in res.failures} == {("C9a", "2025-02"), ("C9b", "2025-02")}
    assert {r.difference for r in res.failures} == {-victim.record.amount}


def test_c9_fails_when_a_mapped_record_is_counted_twice(tmp_path):
    cfg, records, controls, mapped = tiny_mapped(tmp_path)
    dup = next(m for m in mapped if m.record.account == "Marketing" and m.record.period == "2025-03")
    res = reconcile(records, controls, cfg, mapped=mapped + [dup])
    assert {(r.control_id, r.difference) for r in res.failures} == {("C9a", dup.record.amount),
                                                                    ("C9b", dup.record.amount)}


def test_c9a_and_c9b_are_checked_independently(tmp_path):
    cfg, records, controls, mapped = tiny_mapped(tmp_path)
    broken = [dataclasses.replace(m, nature=None) if m.record.account == "Rent" and m.record.period == "2025-01"
              else m for m in mapped]  # nature lost on one record: C9b only
    res = reconcile(records, controls, cfg, mapped=broken)
    assert {(r.control_id, r.period) for r in res.failures} == {("C9b", "2025-01")}


def test_c9_uses_the_workbook_total_so_a_tampered_leaf_also_breaks_c9(tmp_path):
    from tests.fixtures import companies as co
    rows = b.default_rows()
    leaf(rows, "Rent")[2] = [100, 100, 130]
    cdir = co.write_company(tmp_path, rows=rows)
    from trend.categories import load_vocabulary
    from trend.config import load_company
    from trend.mapping import load_mapping, map_records
    company = load_company(cdir)
    records, controls = ExcelPLAdapter(company.source, "tiny").load()
    controls = [dataclasses.replace(c, amount=c.amount - D("30")) if c.control_name == "Total Operating Expenses"
                and c.period == "2025-03" else c for c in controls]  # workbook says 30 less than the leaves
    mapped = map_records(records, load_mapping(cdir, load_vocabulary()), company, cdir)
    res = reconcile(records, controls, company.source, mapped=mapped)
    assert {r.control_id for r in res.failures if r.period == "2025-03"} >= {"C6", "C9a", "C9b"}


def test_c9_is_not_applicable_without_an_opex_section(tmp_path):
    cfg, records, controls, mapped = tiny_mapped(tmp_path)
    cfg2 = dataclasses.replace(cfg, sections=tuple(s for s in cfg.sections if s.kind != "opex"))
    res = reconcile([r for r in records if r.section != "opex"], controls, cfg2, mapped=[])
    na = {x.control_id: x.reason for x in res.not_applicable}
    assert "C9a" in na and "C9b" in na and "opex" in na["C9a"]


# ---------- optional derived roles; not-applicable controls are visible ----------

def no_cogs_company(tmp_path, derived, drop_labels=()):
    """Services-style company: no COGS section, no Gross Profit row."""
    rows = b.default_rows()
    start = next(i for i, r in enumerate(rows) if r[0] == "Cost of Goods Sold")
    end = next(i for i, r in enumerate(rows) if r[1] == "Gross Profit")
    del rows[start:end + 1]
    t_inc = leaf_total = next(r for r in rows if r[0] == "Total Income")[2]
    t_opex = next(r for r in rows if r[0] == "Total Operating Expenses")[2]
    opinc = [a - c for a, c in zip(t_inc, t_opex)]
    for r in rows:
        if r[1] in ("Operating Income", "Net Income"):
            r[2] = opinc
    rows = [r for r in rows if r[1] not in drop_labels]
    p = tmp_path / "svc.xlsx"
    b.write(p, rows)
    src = b.source(path=str(p), derived_rows=derived)
    src["sections"] = [s for s in src["sections"] if s["kind"] != "cogs"]
    cfg = parse_source(src, base_dir=tmp_path)
    records, controls = ExcelPLAdapter(cfg, "svc").load()
    return cfg, records, controls


def test_no_cogs_no_gross_profit_reconciles_and_lists_c3_and_c2_as_not_applicable(tmp_path):
    cfg, records, controls = no_cogs_company(
        tmp_path, {"operating_income": "Operating Income", "net_income": "Net Income"})
    res = reconcile(records, controls, cfg)
    assert res.status == "OK"
    na = {x.control_id: x.reason for x in res.not_applicable}
    assert set(na) == {"C2", "C3", "C5", "C9a", "C9b"}  # no cogs, no gross_profit role, one group, no mapping given
    assert "gross_profit" in na["C3"] and "cogs" in na["C2"].lower()
    assert {r.control_id for r in res.results} == {"C1", "C4", "C6", "C7", "C8"}
    assert all(r.ok for r in res.results)


def test_each_undeclared_role_skips_only_its_own_control(tmp_path):
    cfg, records, controls = tiny(tmp_path)
    for role, cid in (("gross_profit", "C3"), ("operating_income", "C7"), ("net_income", "C8")):
        d = {k: v for k, v in b.SOURCE["derived_rows"].items() if k != role}
        cfg2 = dataclasses.replace(cfg, derived_rows=parse_source(b.source(derived_rows=d, path="x"),
                                                                  base_dir=tmp_path).derived_rows)
        res = reconcile(records, controls, cfg2)
        assert [x.control_id for x in res.not_applicable if x.control_id not in ("C5", "C9a", "C9b")] == [cid]
        assert cid not in {r.control_id for r in res.results}


def test_fully_declared_company_lists_missing_second_group_and_unsupplied_mapping(tmp_path):
    cfg, records, controls = tiny(tmp_path)  # one group -> C5 n/a; no mapping given -> C9a/C9b n/a
    res = reconcile(records, controls, cfg)
    assert [x.control_id for x in res.not_applicable] == ["C5", "C9a", "C9b"]
    assert all("mapping" in x.reason for x in res.not_applicable if x.control_id.startswith("C9"))


def test_declared_but_missing_derived_row_is_blocked(tmp_path):
    # gross_profit is declared in config but the sheet has no such row (and no cogs either)
    rows = b.default_rows()
    rows = [r for r in rows if r[1] != "Gross Profit"]
    p = tmp_path / "t.xlsx"
    b.write(p, rows)
    cfg = parse_source(b.source(path=str(p)), base_dir=tmp_path)
    records, controls = ExcelPLAdapter(cfg, "tiny").load()
    res = reconcile(records, controls, cfg)
    assert res.status == "BLOCKED"
    f = res.failures
    assert [(x.control_id, x.reason) for x in f if x.period == "2025-01"] == [("C3", "MISSING_CONTROL")]
    assert "C3" not in [x.control_id for x in res.not_applicable]


# ---------- real ABC workbook ----------

@pytest.fixture(scope="module")
def abc():
    company = load_company(ROOT / "companies" / "abc_inc")
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    return company, records, controls, oracle.scan()


def test_abc_all_controls_pass_in_all_periods(abc):
    company, records, controls, o = abc
    res = reconcile(records, controls, company.source)
    assert res.status == "OK", [(r.control_id, r.period, r.difference) for r in res.failures]
    n_controls = len(o["subtotals"]) + len(o["derived"])
    assert len(res.results) == n_controls * len(o["periods"])
    assert {r.period for r in res.results} == set(o["periods"])
    assert {r.control_id for r in res.results} == {f"C{i}" for i in range(1, 9)}
    assert all(r.ok for r in res.results)
    # C1-C8 all apply to ABC; only C9a/C9b are skipped, because no mapping was passed in
    assert [x.control_id for x in res.not_applicable] == ["C9a", "C9b"]


# ---------- tampered copy of the real workbook (temp folder only) ----------

def tamper_xlsx(src: Path, dst: Path, cell: str, delta: str):
    """Edit one literal numeric cell in sheet1.xml, keeping every cached formula value."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode("utf-8")
                pat = re.compile(r'(<c r="%s"(?: [^>]*)?><v>)([^<]*)(</v>)' % cell)
                assert pat.search(xml), f"{cell} is not a literal numeric cell"
                xml = pat.sub(lambda m: m.group(1) + str(D(m.group(2)) + D(delta)) + m.group(3), xml, count=1)
                data = xml.encode("utf-8")
            zout.writestr(item, data)


def _cell_of(o, label, period):
    leaf_ = next(x for x in o["leaves"] if x["label"] == label)
    col = o["period_cols"][o["periods"].index(period)]
    return f"{get_column_letter(col)}{leaf_['row']}", col


def _control_ref(o, label, col):
    row = next(r for r, l in o["subtotals"] + o["derived"] if l == label)
    return f"Income Statement!{get_column_letter(col)}{row}"


def test_tampered_abc_copy_is_blocked_with_cell_references(abc, tmp_path):
    company, _, _, o = abc
    period = o["periods"][0]
    cell, col = _cell_of(o, "Usage Fees", period)
    copy = tmp_path / "tampered.xlsx"
    tamper_xlsx(company.source.path, copy, cell, "1000.00")
    src = dataclasses.replace(company.source, path=copy)
    records, controls = ExcelPLAdapter(src, "abc_inc").load()
    res = reconcile(records, controls, src)
    assert res.status == "BLOCKED"
    assert {(r.control_name, r.period) for r in res.failures} == {
        (n, period) for n in ("Total Income", "Gross Profit", "Operating Income", "Net Income")}
    assert {r.difference for r in res.failures} == {D("1000.00")}
    assert {r.source_ref for r in res.failures} == {
        _control_ref(o, n, col) for n in ("Total Income", "Gross Profit", "Operating Income", "Net Income")}
    with pytest.raises(BlockedError) as e:
        ensure_ok(res)
    assert _control_ref(o, "Total Income", col) in str(e.value)


def test_tampered_abc_group_leaf_hits_group_and_opex_controls(abc, tmp_path):
    company, _, _, o = abc
    period = o["periods"][3]
    cell, col = _cell_of(o, "Events & Conferences", period)
    copy = tmp_path / "tampered2.xlsx"
    tamper_xlsx(company.source.path, copy, cell, "-250.50")
    src = dataclasses.replace(company.source, path=copy)
    records, controls = ExcelPLAdapter(src, "abc_inc").load()
    res = reconcile(records, controls, src)
    assert {r.period for r in res.failures} == {period}
    assert {r.control_name for r in res.failures} == {
        "Total Marketing", "Total Operating Expenses", "Operating Income", "Net Income"}
    assert {r.control_id for r in res.failures} == {"C5", "C6", "C7", "C8"}


def test_original_workbook_is_untouched_by_the_tests(abc):
    company, *_ = abc
    assert company.source.path == oracle.WORKBOOK.resolve() or company.source.path.samefile(oracle.WORKBOOK)


@pytest.mark.skipif(not (ROOT / "companies" / "abc_inc" / "mapping.yaml").exists(),
                    reason="companies/abc_inc/mapping.yaml not yet approved by a human")
def test_abc_c9a_c9b_tie_in_all_periods_and_nothing_is_not_applicable():
    from trend.categories import load_vocabulary
    from trend.mapping import load_mapping, map_records
    company = load_company(ROOT / "companies" / "abc_inc")
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    mapped = map_records(records, load_mapping(ROOT / "companies" / "abc_inc", load_vocabulary()),
                         company, ROOT / "companies" / "abc_inc")
    res = reconcile(records, controls, company.source, mapped=mapped)
    o = oracle.scan()
    assert res.status == "OK", [(r.control_id, r.period, r.difference) for r in res.failures]
    assert res.not_applicable == []
    assert len(res.results) == (len(o["subtotals"]) + len(o["derived"]) + 2) * len(o["periods"])
    assert {r.control_id for r in res.results} == {f"C{i}" for i in range(1, 9)} | {"C9a", "C9b"}
