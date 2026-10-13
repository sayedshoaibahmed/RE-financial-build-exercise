from decimal import Decimal
from pathlib import Path

import pytest

from tests.fixtures import companies as co
from trend.config import ConfigError, Thresholds, load_company, parse_thresholds

ROOT = Path(__file__).resolve().parents[2]
D = Decimal

ALL_KEYS = ["mom_min_abs", "mom_min_pct", "margin_min_pp", "spike_window", "spike_min_history",
            "spike_mad_multiple", "spike_min_abs", "level_shift_window", "level_shift_min_pct",
            "level_shift_band_pct", "level_shift_min_abs", "negative_min_abs_amount", "reversal_lookback_months",
            "reversal_tolerance"]


def test_code_defaults_are_the_plan_a7_values():
    t = Thresholds()
    assert (t.mom_min_abs, t.mom_min_pct, t.margin_min_pp) == (D("5000"), D("20"), D("3"))
    assert (t.spike_mad_multiple, t.spike_min_abs, t.spike_min_history, t.spike_window) == (D("4"), D("5000"), 6, 12)
    assert (t.level_shift_min_pct, t.level_shift_band_pct, t.level_shift_window) == (D("20"), D("5"), 3)
    assert t.level_shift_min_abs == D("0")
    assert t.negative_min_abs_amount == D("0")      # fallback: every negative is flagged unless configured
    assert (t.reversal_lookback_months, t.reversal_tolerance) == (3, D("0.01"))
    assert set(t.as_dict()) == set(ALL_KEYS)


def test_abc_thresholds_come_from_company_yaml():
    t = load_company(ROOT / "companies" / "abc_inc").thresholds
    assert t.negative_min_abs_amount == D("1.00")
    assert t.mom_min_abs == D("5000") and t.spike_min_history == 6 and t.level_shift_window == 3
    raw = (ROOT / "companies" / "abc_inc" / "company.yaml").read_text(encoding="utf-8")
    for k in ALL_KEYS:                       # every threshold is spelled out in the file, none left to code defaults
        assert k in raw, k


def test_company_without_thresholds_block_uses_defaults(tmp_path):
    assert load_company(co.write_company(tmp_path, mapping=None)).thresholds == Thresholds()


def test_partial_block_keeps_other_defaults(tmp_path):
    c = load_company(co.write_company(tmp_path, mapping=None,
                                      company_yaml_extra={"thresholds": {"mom_min_abs": 250, "spike_min_history": 8}}))
    assert c.thresholds.mom_min_abs == D("250") and c.thresholds.spike_min_history == 8
    assert c.thresholds.mom_min_pct == D("20")


def test_float_thresholds_are_exact_decimals(tmp_path):
    c = load_company(co.write_company(tmp_path, mapping=None,
                                      company_yaml_extra={"thresholds": {"negative_min_abs_amount": 0.01,
                                                                         "reversal_tolerance": 0.05}}))
    assert c.thresholds.negative_min_abs_amount == D("0.01") and c.thresholds.reversal_tolerance == D("0.05")


def err(tmp_path, block):
    with pytest.raises(ConfigError) as e:
        load_company(co.write_company(tmp_path, mapping=None, company_yaml_extra={"thresholds": block}))
    return e.value


def test_unknown_threshold_key_is_rejected_with_a_code(tmp_path):
    e = err(tmp_path, {"mom_min_abs": 1, "mom_minimum": 5})
    assert e.code == "UNKNOWN_THRESHOLD" and "mom_minimum" in str(e) and "UNKNOWN_THRESHOLD" in str(e)


@pytest.mark.parametrize("key", ALL_KEYS)
def test_negative_threshold_is_rejected(tmp_path, key):
    e = err(tmp_path / key, {key: -1})
    assert e.code == "NEGATIVE_THRESHOLD" and key in str(e)


@pytest.mark.parametrize("bad", ["5000", "abc", None, True, [1], {"a": 1}, float("inf"), float("nan")])
def test_non_numeric_threshold_is_rejected(tmp_path, bad):
    e = err(tmp_path, {"mom_min_abs": bad})
    assert e.code == "INVALID_THRESHOLD" and "mom_min_abs" in str(e)


@pytest.mark.parametrize("key,val", [("spike_window", 0), ("spike_window", 2.5), ("spike_min_history", 0),
                                     ("level_shift_window", 1), ("reversal_lookback_months", 0),
                                     ("level_shift_window", 3.0)])
def test_count_thresholds_must_be_whole_numbers_in_range(tmp_path, key, val):
    e = err(tmp_path / f"{key}{val}", {key: val})
    assert e.code == "INVALID_THRESHOLD"


def test_zero_is_a_valid_amount_threshold(tmp_path):
    c = load_company(co.write_company(tmp_path, mapping=None, company_yaml_extra={"thresholds": {"negative_min_abs_amount": 0}}))
    assert c.thresholds.negative_min_abs_amount == D("0")


def test_thresholds_block_must_be_a_mapping(tmp_path):
    assert err(tmp_path, [1, 2]).code == "INVALID_CONFIG"


def test_parse_thresholds_directly():
    assert parse_thresholds({}) == Thresholds()
    with pytest.raises(ConfigError):
        parse_thresholds({"nope": 1})
