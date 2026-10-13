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
    assert s.derived_rows == ("Gross Profit", "Operating Income", "Net Income")
    assert s.path.is_absolute() and s.path.exists()  # relative path resolved against repo root


def test_abc_company_yaml_has_only_source_block():
    raw = yaml.safe_load((ABC_DIR / "company.yaml").read_text(encoding="utf-8"))
    assert list(raw) == ["source"]


def test_parse_source_roundtrip():
    s = parse_source(builders.source(), base_dir=ROOT)
    assert isinstance(s, SourceConfig)
    assert s.section_label_col == "A" and s.account_label_col == "B"


def test_unknown_top_level_key_rejected_with_clear_message(tmp_path):
    (tmp_path / "company.yaml").write_text("source: {}\nwindows: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_company(tmp_path)
    assert "windows" in str(e.value) and "unknown" in str(e.value).lower()


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


@pytest.mark.parametrize("key", ["type", "path", "sheet", "header_row", "sections", "derived_rows"])
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
