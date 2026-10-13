import datetime
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests import oracle
from tests.fixtures import builders as b
from tests.fixtures import companies as co
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.categories import load_vocabulary
from trend.config import load_company
from trend.mapping import (MappedRecord, MappingError, category_totals, load_mapping, map_records,
                           mapping_status)

ROOT = Path(__file__).resolve().parents[2]
D = Decimal


def setup(tmp_path, mapping=co.entries, rows=None, extra=None):
    cdir = co.write_company(tmp_path, rows=rows, mapping=mapping, company_yaml_extra=extra)
    company = load_company(cdir)
    records, _ = ExcelPLAdapter(company.source, company.company_id).load()
    return cdir, company, records


def vocab():
    return load_vocabulary()


def code_of(fn, *a, **k):
    with pytest.raises(MappingError) as e:
        fn(*a, **k)
    return e.value


def without(account):
    return lambda: [e for e in co.entries() if e["account"] != account]


# ---------- loading ----------

def test_load_valid_mapping(tmp_path):
    cdir, company, _ = setup(tmp_path)
    m = load_mapping(cdir, vocab())
    assert set(m) == {a for a, *_ in co.TINY_MAP}
    r = m["Rent"]
    assert (r.section, r.function, r.nature) == ("opex", "g_and_a", "facilities_office")
    assert r.approved_by == "Tester" and r.expected_negative is False
    assert m["Sales"].function is None and m["Sales"].group == "Sales"  # default group = the account itself


def test_approved_on_accepts_yaml_dates(tmp_path):
    def mk():
        es = co.entries()
        for e in es:
            e["approved_on"] = datetime.date(2026, 10, 13)
        return es
    cdir, *_ = setup(tmp_path, mapping=mk)
    assert load_mapping(cdir, vocab())["Rent"].approved_on == "2026-10-13"


def test_missing_mapping_yaml_means_nothing_is_mapped(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=None)
    assert load_mapping(cdir, vocab()) == {}


def test_proposed_file_is_never_read(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=None)
    prop = co.entries(approved_by=None, approved_on=None)
    for e in prop:
        e.update(proposed=True, rationale="x", confidence="high")
    (cdir / "mapping.proposed.yaml").write_text(yaml.safe_dump(prop), encoding="utf-8")
    assert load_mapping(cdir, vocab()) == {}  # only mapping.yaml counts


def test_proposed_file_with_different_content_is_ignored_when_mapping_exists(tmp_path):
    cdir, *_ = setup(tmp_path)
    garbage = [{"account": "Rent", "section": "opex", "function": "r_and_d", "nature": "payroll_benefits",
                "approved_by": None}]
    (cdir / "mapping.proposed.yaml").write_text(yaml.safe_dump(garbage), encoding="utf-8")
    m = load_mapping(cdir, vocab())
    assert m["Rent"].function == "g_and_a" and len(m) == len(co.TINY_MAP)


# ---------- approval guard ----------

def with_entry(**change):
    def mk():
        es = co.entries()
        es[3].update(change)  # Rent
        return es
    return mk


def without_key(key):
    def mk():
        es = co.entries()
        del es[3][key]
        return es
    return mk


@pytest.mark.parametrize("val", [None, "", "   "])
def test_empty_approved_by_is_unapproved(tmp_path, val):
    cdir, *_ = setup(tmp_path, mapping=with_entry(approved_by=val))
    assert code_of(load_mapping, cdir, vocab()).code == "UNAPPROVED_MAPPING"


def test_missing_approved_by_is_unapproved(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=without_key("approved_by"))
    assert code_of(load_mapping, cdir, vocab()).code == "UNAPPROVED_MAPPING"


@pytest.mark.parametrize("val", [None, "", "  "])
def test_empty_approved_on_is_unapproved(tmp_path, val):
    cdir, *_ = setup(tmp_path, mapping=with_entry(approved_on=val))
    assert code_of(load_mapping, cdir, vocab()).code == "UNAPPROVED_MAPPING"


def test_missing_approved_on_is_unapproved(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=without_key("approved_on"))
    assert code_of(load_mapping, cdir, vocab()).code == "UNAPPROVED_MAPPING"


@pytest.mark.parametrize("key,val", [("proposed", True), ("rationale", "looks like rent"),
                                     ("confidence", "high")])
def test_proposal_only_keys_are_unapproved_even_if_approved_by_is_set(tmp_path, key, val):
    cdir, *_ = setup(tmp_path, mapping=with_entry(**{key: val}))
    e = code_of(load_mapping, cdir, vocab())
    assert e.code == "UNAPPROVED_MAPPING" and key in str(e)


def test_error_message_names_the_account(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=with_entry(approved_by=None))
    assert "Rent" in str(code_of(load_mapping, cdir, vocab()))


# ---------- vocabulary ----------

def test_unknown_function_is_unknown_category(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=with_entry(function="made_up"))
    e = code_of(load_mapping, cdir, vocab())
    assert e.code == "UNKNOWN_CATEGORY" and "made_up" in str(e)


def test_unknown_nature_is_unknown_category(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=with_entry(nature="made_up"))
    assert code_of(load_mapping, cdir, vocab()).code == "UNKNOWN_CATEGORY"


def test_labels_are_not_accepted_only_ids(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=with_entry(function="G&A"))
    assert code_of(load_mapping, cdir, vocab()).code == "UNKNOWN_CATEGORY"


def test_extended_vocabulary_allows_company_specific_category(tmp_path):
    cdir, company, _ = setup(tmp_path, mapping=with_entry(function="legal_ops"),
                             extra={"categories_extra": {"functions": ["legal_ops"]}})
    v = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
    assert load_mapping(cdir, v)["Rent"].function == "legal_ops"
    assert code_of(load_mapping, cdir, vocab()).code == "UNKNOWN_CATEGORY"  # not without the extension


# ---------- structure ----------

def test_opex_entry_needs_function_and_nature(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=without_key("nature"))
    assert code_of(load_mapping, cdir, vocab()).code == "INVALID_MAPPING"
    cdir2, *_ = setup(tmp_path / "f", mapping=without_key("function"))
    assert code_of(load_mapping, cdir2, vocab()).code == "INVALID_MAPPING"


def test_function_on_income_entry_is_rejected(tmp_path):
    def mk():
        es = co.entries()
        es[0].update(function="g_and_a", nature="payroll_benefits")
        return es
    cdir, *_ = setup(tmp_path, mapping=mk)
    assert code_of(load_mapping, cdir, vocab()).code == "INVALID_MAPPING"


def test_group_allowed_on_income_not_on_opex(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=lambda: [dict(e, group="Core") if e["section"] == "income" else e
                                                  for e in co.entries()])
    assert load_mapping(cdir, vocab())["Sales"].group == "Core"
    cdir2, *_ = setup(tmp_path / "x", mapping=with_entry(group="Core"))
    assert code_of(load_mapping, cdir2, vocab()).code == "INVALID_MAPPING"


def test_expected_negative_must_be_bool(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=with_entry(expected_negative="yes"))
    assert code_of(load_mapping, cdir, vocab()).code == "INVALID_MAPPING"
    cdir2, *_ = setup(tmp_path / "y", mapping=with_entry(expected_negative=True))
    assert load_mapping(cdir2, vocab())["Rent"].expected_negative is True


@pytest.mark.parametrize("mk", [
    lambda: dict(co.entries()[0]),                                   # not a list
    lambda: [co.entries()[0], "Rent"],                               # entry not a mapping
    lambda: [dict(co.entries()[0], colour="red")],                   # unknown key
    lambda: [dict(co.entries()[0], section="revenue")],              # bad section kind
    lambda: [dict(co.entries()[0], account="")],                     # empty account
    lambda: co.entries() + [co.entries()[0]],                        # duplicate account
])
def test_structural_errors(tmp_path, mk):
    cdir, *_ = setup(tmp_path, mapping=mk)
    assert code_of(load_mapping, cdir, vocab()).code == "INVALID_MAPPING"


def test_invalid_yaml(tmp_path):
    cdir, *_ = setup(tmp_path, mapping=None)
    (cdir / "mapping.yaml").write_text("- {a: [", encoding="utf-8")
    assert code_of(load_mapping, cdir, vocab()).code == "INVALID_MAPPING"


# ---------- applying the mapping ----------

def test_map_records_adds_function_and_nature(tmp_path):
    cdir, company, records = setup(tmp_path)
    mapped = map_records(records, load_mapping(cdir, vocab()), company, cdir)
    assert len(mapped) == len(records) and all(isinstance(m, MappedRecord) for m in mapped)
    rent = next(m for m in mapped if m.record.account == "Rent")
    assert (rent.function, rent.nature) == ("g_and_a", "facilities_office")
    sales = next(m for m in mapped if m.record.account == "Sales")
    assert sales.function is None and sales.nature is None and sales.group == "Sales"


def test_category_totals_cover_opex_exactly_once(tmp_path):
    cdir, company, records = setup(tmp_path)
    mapped = map_records(records, load_mapping(cdir, vocab()), company, cdir)
    by_fn = category_totals(mapped, "function")
    by_nat = category_totals(mapped, "nature")
    for p in b.PERIODS:
        opex = sum(r.amount for r in records if r.period == p and r.section == "opex")
        assert sum(by_fn[p].values()) == opex == sum(by_nat[p].values())
    assert by_fn["2025-01"]["r_and_d"] == D("440.00") and by_nat["2025-01"]["payroll_benefits"] == D("440.00")
    assert set(by_fn["2025-01"]) == {"g_and_a", "r_and_d", "sales_marketing"}  # only categories that occur


def test_unmapped_account_blocks_and_writes_file(tmp_path):
    cdir, company, records = setup(tmp_path, mapping=without("Rent"))
    out = tmp_path / "out"
    before = hashlib.sha256((cdir / "mapping.yaml").read_bytes()).hexdigest()
    e = code_of(map_records, records, load_mapping(cdir, vocab()), company, cdir, out_dir=out)
    assert e.code == "UNMAPPED_ACCOUNT" and "Rent" in str(e)
    doc = yaml.safe_load((out / "unmapped_accounts.yaml").read_text(encoding="utf-8"))
    assert [d["account"] for d in doc] == ["Rent"]
    d = doc[0]
    assert set(d) == {"account", "section", "path", "first_nonzero_period", "last_nonzero_period", "total"}
    assert d["section"] == "opex" and d["path"] == ["Operating Expenses", "Rent"]
    assert d["first_nonzero_period"] == "2025-01" and d["last_nonzero_period"] == "2025-03"
    assert str(d["total"]) == "300.00"
    assert hashlib.sha256((cdir / "mapping.yaml").read_bytes()).hexdigest() == before  # never edited
    assert not (cdir / "mapping.proposed.yaml").exists()


def test_unmapped_file_has_null_periods_for_all_zero_accounts(tmp_path):
    rows = b.default_rows()
    rows[b.row_of(rows, "Rent") - b.FIRST_ROW][2] = [0, 0, 0]
    cdir, company, records = setup(tmp_path, rows=rows, mapping=without("Rent"))
    out = tmp_path / "out"
    code_of(map_records, records, load_mapping(cdir, vocab()), company, cdir, out_dir=out)
    d = yaml.safe_load((out / "unmapped_accounts.yaml").read_text(encoding="utf-8"))[0]
    assert d["first_nonzero_period"] is None and d["last_nonzero_period"] is None and str(d["total"]) == "0.00"


def test_unmapped_uses_first_and_last_nonzero_period(tmp_path):
    rows = b.default_rows()
    rows[b.row_of(rows, "Rent") - b.FIRST_ROW][2] = [0, 5, 0]
    cdir, company, records = setup(tmp_path, rows=rows, mapping=without("Rent"))
    out = tmp_path / "out"
    code_of(map_records, records, load_mapping(cdir, vocab()), company, cdir, out_dir=out)
    d = yaml.safe_load((out / "unmapped_accounts.yaml").read_text(encoding="utf-8"))[0]
    assert d["first_nonzero_period"] == d["last_nonzero_period"] == "2025-02"


def test_default_unmapped_location_is_outputs_company_unmapped(tmp_path):
    cdir, company, records = setup(tmp_path, mapping=None)
    code_of(map_records, records, {}, company, cdir)
    f = tmp_path / "outputs" / "tiny" / "unmapped" / "unmapped_accounts.yaml"
    assert f.is_file()
    # every account is listed; nothing is silently mapped to "Other"
    assert len(yaml.safe_load(f.read_text(encoding="utf-8"))) == len(co.TINY_MAP)


def test_no_unmapped_file_written_when_everything_is_mapped(tmp_path):
    cdir, company, records = setup(tmp_path)
    map_records(records, load_mapping(cdir, vocab()), company, cdir, out_dir=tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_entry_with_wrong_section_is_invalid(tmp_path):
    def mk():
        es = co.entries()
        es[3].update(section="cogs")
        del es[3]["function"], es[3]["nature"]
        return es
    cdir, company, records = setup(tmp_path, mapping=mk)
    e = code_of(map_records, records, load_mapping(cdir, vocab()), company, cdir)
    assert e.code == "INVALID_MAPPING" and "Rent" in str(e) and "cogs" in str(e)


def _rows_with_second_rent():
    rows = b.default_rows()
    rows.insert(b.row_of(rows, "Payroll Taxes") - b.FIRST_ROW, [None, "Rent", [1, 1, 1]])
    return rows  # a second "Rent", inside the Payroll group


def test_duplicate_labels_with_different_paths_are_ambiguous(tmp_path):
    cdir, company, records = setup(tmp_path, rows=_rows_with_second_rent())
    e = code_of(map_records, records, load_mapping(cdir, vocab()), company, cdir)
    assert e.code == "AMBIGUOUS_ACCOUNT"
    assert "Rent" in str(e) and "Payroll" in str(e) and "Operating Expenses" in str(e)


def test_ambiguity_is_reported_before_unmapped(tmp_path):
    cdir, company, records = setup(tmp_path, rows=_rows_with_second_rent(), mapping=None)
    assert code_of(map_records, records, {}, company, cdir).code == "AMBIGUOUS_ACCOUNT"


def test_mapping_status_lists_every_distinct_account_in_sheet_order(tmp_path):
    cdir, company, records = setup(tmp_path, mapping=without("Rent"))
    rows = mapping_status(records, load_mapping(cdir, vocab()))
    assert [r["account"] for r in rows] == [a for a, *_ in co.TINY_MAP]
    st = {r["account"]: r["mapped"] for r in rows}
    assert st["Rent"] is False and st["Sales"] is True
    sal = next(r for r in rows if r["account"] == "Salaries")
    assert sal["path"] == ["Operating Expenses", "Payroll", "Salaries"] and sal["section"] == "opex"


# ---------- ABC: the table checks run after the human approves companies/abc_inc/mapping.yaml ----------

ABC_DIR = ROOT / "companies" / "abc_inc"
needs_approval = pytest.mark.skipif(not (ABC_DIR / "mapping.yaml").exists(),
                                    reason="companies/abc_inc/mapping.yaml not yet approved by a human")


def test_abc_loader_ignores_mapping_proposed_yaml():
    company = load_company(ABC_DIR)
    v = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
    if not (ABC_DIR / "mapping.yaml").exists():
        assert load_mapping(ABC_DIR, v) == {}  # a proposal alone maps nothing


@pytest.fixture(scope="module")
def abc():
    company = load_company(ABC_DIR)
    records, _ = ExcelPLAdapter(company.source, company.company_id).load()
    v = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
    mapping = load_mapping(ABC_DIR, v)
    mapped = map_records(records, mapping, company, ABC_DIR)
    return company, records, mapping, mapped, oracle.scan()


@needs_approval
def test_abc_every_account_is_mapped_exactly_once(abc):
    _, records, mapping, mapped, o = abc
    assert len(mapping) == len(o["leaf_labels"])
    assert set(mapping) == set(o["leaf_labels"])
    assert len(mapped) == len(records)


@needs_approval
def test_abc_mapping_sections_match_the_workbook_sections(abc):
    _, records, mapping, _, _ = abc
    assert {a: m.section for a, m in mapping.items()} == {r.account: r.section for r in records}


@needs_approval
def test_abc_all_entries_carry_the_approver_stamp(abc):
    _, _, mapping, _, _ = abc
    assert {m.approved_by for m in mapping.values()} == {"Shoaib"}
    assert {m.approved_on for m in mapping.values()} == {"2026-10-13"}


@needs_approval
def test_abc_function_and_nature_totals_cover_opex_in_every_period(abc):
    _, records, _, mapped, o = abc
    by_fn, by_nat = category_totals(mapped, "function"), category_totals(mapped, "nature")
    for p in o["periods"]:
        opex = sum(r.amount for r in records if r.period == p and r.section == "opex")
        assert sum(by_fn[p].values()) == opex == sum(by_nat[p].values())


# PLAN 4.4, reported basis. Pinned on purpose: these are the golden numbers to be reproduced.
PLAN_FN = {  # (Dec 2025, Jun 2026, TTM Jun 2026)
    "r_and_d": ("130581.48", "139257.51", "1614507.83"),
    "sales_marketing": ("174192.63", "201102.74", "2428886.76"),
    "operations": ("33469.60", "35510.75", "412174.40"),
    "people_unallocated": ("104464.54", "30053.71", "419718.79"),
    "technology": ("24897.17", "33612.10", "299430.99"),
    "g_and_a": ("46045.17", "28780.88", "483999.10"),
    "non_operating_tax": ("-1250453.21", "-135.89", "-1303170.90"),
    "stock_based_comp": ("334660.53", "0.00", "334660.53"),
}
PLAN_NAT = {
    "payroll_benefits": ("408064.98", "328377.23", "4080307.55"),
    "contractors_professional_fees": ("29917.33", "20633.33", "325191.53"),
    "marketing_events": ("19198.61", "49392.70", "450027.85"),
    "software_technology": ("25375.62", "34089.48", "305163.45"),
    "travel_entertainment": ("12817.64", "20494.21", "221420.93"),
    "facilities_office": ("5491.56", "4913.02", "81409.38"),
    "commissions": ("2000.00", "1800.00", "69400.00"),
    "other_operating": ("9274.76", "8176.67", "114090.37"),
    "depreciation": ("1510.09", "441.05", "11706.81"),
    "stock_based_comp": ("334660.53", "0.00", "334660.53"),
    "interest_tax": ("-1250453.21", "-135.89", "-1303170.90"),
}


def _diffs(mapped, field, plan):
    tot = category_totals(mapped, field)
    ttm = [p for p in sorted(tot) if "2025-07" <= p <= "2026-06"]
    assert len(ttm) == 12
    out = {}
    cats = set(plan) | set(tot["2025-12"]) | set(tot["2026-06"])
    for cat in sorted(cats):
        got = (tot["2025-12"].get(cat, D("0.00")), tot["2026-06"].get(cat, D("0.00")),
               sum((tot[p].get(cat, D("0.00")) for p in ttm), D("0.00")))
        want = tuple(D(x) for x in plan.get(cat, ("0.00", "0.00", "0.00")))
        if got != want:
            out[cat] = {"got(Dec25,Jun26,TTM)": tuple(map(str, got)), "plan": tuple(map(str, want))}
    return out


@needs_approval
def test_abc_function_totals_equal_plan_4_4(abc):
    assert _diffs(abc[3], "function", PLAN_FN) == {}


@needs_approval
def test_abc_nature_totals_equal_plan_4_4(abc):
    assert _diffs(abc[3], "nature", PLAN_NAT) == {}
