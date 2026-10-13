from pathlib import Path

import pytest
import yaml

from tests.fixtures import builders
from trend.config import ConfigError, SourceConfig, load_company, parse_source

ROOT = Path(__file__).resolve().parents[2]
ABC_DIR = ROOT / "companies" / "abc_inc"


def test_abc_company_yaml_loads():
    c = load_company(ABC_DIR)
    assert c.company_id == "abc_inc"
    s = c.source
    assert s.sheet == "Income Statement" and s.header_row == 5
    assert [x.kind for x in s.sections] == ["income", "cogs", "opex"]
    assert [g.header for g in s.groups] == ["S & M Payroll", "Marketing"]
    assert (s.derived_rows.gross_profit, s.derived_rows.operating_income, s.derived_rows.net_income) ==         ("Gross Profit", "Operating Income", "Net Income")
    assert s.path.is_absolute() and s.path.exists()  # relative path resolved against repo root


def test_abc_company_yaml_has_the_expected_top_level_blocks():
    raw = yaml.safe_load((ABC_DIR / "company.yaml").read_text(encoding="utf-8"))
    assert list(raw) == ["source", "adjustments", "windows"]  # P4 added adjustments + windows


def test_parse_source_roundtrip():
    s = parse_source(builders.source(), base_dir=ROOT)
    assert isinstance(s, SourceConfig)
    assert s.section_label_col == "A" and s.account_label_col == "B"


def test_unknown_top_level_key_rejected_with_clear_message(tmp_path):
    (tmp_path / "company.yaml").write_text("source: {}\nthresholds: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_company(tmp_path)
    assert "thresholds" in str(e.value) and "unknown" in str(e.value).lower()


def test_unknown_source_key_rejected_with_clear_message():
    with pytest.raises(ConfigError) as e:
        parse_source(builders.source(sheet_name="x"), base_dir=ROOT)
    assert "sheet_name" in str(e.value) and "unknown" in str(e.value).lower()


def test_unknown_nested_key_rejected():
    src = builders.source()
    src["sections"][0]["colour"] = "red"
    with pytest.raises(ConfigError) as e:
        parse_source(src, base_dir=ROOT)
    assert "colour" in str(e.value)


@pytest.mark.parametrize("key", ["type", "path", "sheet", "header_row", "sections"])
def test_missing_required_key_rejected(key):
    src = builders.source()
    del src[key]
    with pytest.raises(ConfigError) as e:
        parse_source(src, base_dir=ROOT)
    assert key in str(e.value)


@pytest.mark.parametrize("over", [
    {"type": "csv"},
    {"header_row": 0},
    {"header_row": "5"},
    {"section_label_col": "1"},
    {"account_label_col": "A"},  # must differ from section col
    {"derived_rows": ["Gross Profit"]},
    {"derived_rows": "Gross Profit"},
    {"derived_rows": {"gross_profit": ""}},
    {"derived_rows": {"gross_profit": 5}},
    {"period_header_format": ""},
])
def test_invalid_values_rejected(over):
    with pytest.raises(ConfigError):
        parse_source(builders.source(**over), base_dir=ROOT)


def test_bad_section_kind_rejected():
    src = builders.source()
    src["sections"][0]["kind"] = "revenue"
    with pytest.raises(ConfigError) as e:
        parse_source(src, base_dir=ROOT)
    assert "revenue" in str(e.value)


def test_duplicate_labels_rejected():
    src = builders.source()
    src["groups"][0]["total"] = "Total Income"
    with pytest.raises(ConfigError):
        parse_source(src, base_dir=ROOT)


def test_missing_company_yaml_and_non_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_company(tmp_path)
    (tmp_path / "company.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_company(tmp_path)


def test_old_list_format_for_derived_rows_is_rejected_with_a_clear_message():
    old = ["Gross Profit", "Operating Income", "Net Income"]
    with pytest.raises(ConfigError) as e:
        parse_source(builders.source(derived_rows=old), base_dir=ROOT)
    msg = str(e.value)
    assert "derived_rows" in msg and "mapping" in msg and "gross_profit" in msg


def test_unknown_derived_role_rejected():
    with pytest.raises(ConfigError) as e:
        parse_source(builders.source(derived_rows={"ebitda": "EBITDA"}), base_dir=ROOT)
    assert "ebitda" in str(e.value) and "unknown" in str(e.value).lower()


def test_each_derived_role_is_optional():
    for dr in ({}, {"operating_income": "Operating Income"}, {"gross_profit": "GP", "net_income": "NI"}):
        s = parse_source(builders.source(derived_rows=dr), base_dir=ROOT)
        assert {k: v for k, v in (("gross_profit", s.derived_rows.gross_profit),
                                  ("operating_income", s.derived_rows.operating_income),
                                  ("net_income", s.derived_rows.net_income)) if v} == dr


def test_derived_rows_key_may_be_omitted():
    src = builders.source()
    del src["derived_rows"]
    s = parse_source(src, base_dir=ROOT)
    assert s.derived_rows.gross_profit is None and s.derived_rows.net_income is None


def test_same_label_for_two_roles_rejected():
    with pytest.raises(ConfigError):
        parse_source(builders.source(derived_rows={"gross_profit": "X", "net_income": "X"}), base_dir=ROOT)
