from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.fixtures import companies as co
from trend.basis import AppliedAdjustment, apply_adjustments
from trend.config import Adjustment, ConfigError, WindowsCfg, load_company
from trend.model import MetricsError
from tests.fixtures import mini

ROOT = Path(__file__).resolve().parents[2]
ABC_DIR = ROOT / "companies" / "abc_inc"
D = Decimal


# ---------- company.yaml: adjustments and windows ----------

def test_abc_adjustments_and_windows_are_read_from_company_yaml():
    c = load_company(ABC_DIR)
    assert {a.account: (a.periods, bool(a.reason)) for a in c.adjustments} == {
        "Income Tax Benefit": (("2025-12",), True),
        "Stock-Based Compensation Expense": (("2025-12",), True)}
    assert c.windows == WindowsCfg(lengths=(12,), end="latest")


def test_adjustments_and_windows_default_to_empty_and_ttm(tmp_path):
    c = load_company(co.write_company(tmp_path, mapping=None))
    assert c.adjustments == () and c.windows == WindowsCfg((12,), "latest")


def _company_with(tmp_path, **extra):
    return load_company(co.write_company(tmp_path, mapping=None, company_yaml_extra=extra))


def test_adjustment_parsing(tmp_path):
    c = _company_with(tmp_path, adjustments=[{"account": "Rent", "periods": ["2025-01", "2025-02"], "reason": "one-off"}])
    assert c.adjustments == (Adjustment("Rent", ("2025-01", "2025-02"), "one-off"),)


@pytest.mark.parametrize("adj", [
    "Rent",                                                              # not a list
    [{"account": "Rent", "periods": ["2025-01"]}],                       # reason missing
    [{"account": "Rent", "periods": ["2025-01"], "reason": ""}],
    [{"account": "Rent", "periods": [], "reason": "x"}],
    [{"account": "Rent", "periods": "2025-01", "reason": "x"}],
    [{"account": "Rent", "periods": ["Jan 2025"], "reason": "x"}],
    [{"account": "", "periods": ["2025-01"], "reason": "x"}],
    [{"account": "Rent", "periods": ["2025-01"], "reason": "x", "colour": 1}],
    [{"account": "Rent", "periods": ["2025-01", "2025-01"], "reason": "x"}],
    [{"account": "Rent", "periods": ["2025-01"], "reason": "x"},
     {"account": "Rent", "periods": ["2025-01"], "reason": "y"}],       # same (account, period) twice
])
def test_invalid_adjustments_rejected(tmp_path, adj):
    with pytest.raises(ConfigError):
        _company_with(tmp_path, adjustments=adj)


def test_windows_parsing(tmp_path):
    c = _company_with(tmp_path, windows={"lengths": [3, 6, 12], "end": "2026-03"})
    assert c.windows == WindowsCfg((3, 6, 12), "2026-03")
    assert _company_with(tmp_path / "b", windows={"lengths": [6]}).windows == WindowsCfg((6,), "latest")


@pytest.mark.parametrize("w", [
    {"lengths": []}, {"lengths": [0]}, {"lengths": [-3]}, {"lengths": [3, 3]}, {"lengths": ["12"]},
    {"lengths": [True]}, {"lengths": 12}, {"end": "March"}, {"end": "2026-13"}, {"colour": 1}, [12],
])
def test_invalid_windows_rejected(tmp_path, w):
    with pytest.raises(ConfigError):
        _company_with(tmp_path, windows=w)


def test_still_rejects_unknown_top_level_key(tmp_path):
    with pytest.raises(ConfigError) as e:
        _company_with(tmp_path, reporting={})
    assert "reporting" in str(e.value)


# ---------- basis.py ----------

def ctx(tmp_path, adjustments=None):
    return mini.build(tmp_path, mini.P6, [1000] * 6, [400] * 6, [10, 20, 30, 40, 50, 60], [5] * 6,
                      adjustments=adjustments)


def test_adjusted_records_zero_only_the_configured_account_period(tmp_path):
    c = ctx(tmp_path)
    adj = (Adjustment("Rent", ("2025-02", "2025-04"), "x"),)
    new, applied = apply_adjustments(c.mapped, adj)
    assert len(new) == len(c.mapped)
    changed = [(a.record.account, a.record.period) for a, b in zip(c.mapped, new) if a.record.amount != b.record.amount]
    assert sorted(changed) == [("Rent", "2025-02"), ("Rent", "2025-04")]
    assert all(m.record.amount == 0 for m in new if (m.record.account, m.record.period) in changed)
    # everything else, including mapping fields, is untouched
    for a, b in zip(c.mapped, new):
        if (a.record.account, a.record.period) not in changed:
            assert a == b
    assert [(x.account, x.period, x.reported_amount, x.adjusted_amount, x.delta, x.present) for x in applied] == [
        ("Rent", "2025-02", D("20.00"), D("0.00"), D("-20.00"), True),
        ("Rent", "2025-04", D("40.00"), D("0.00"), D("-40.00"), True)]
    assert all(isinstance(x, AppliedAdjustment) for x in applied)


def test_apply_adjustments_does_not_mutate_the_input(tmp_path):
    c = ctx(tmp_path)
    before = [m.record.amount for m in c.mapped]
    apply_adjustments(c.mapped, (Adjustment("Rent", ("2025-02",), "x"),))
    assert [m.record.amount for m in c.mapped] == before


def test_adjustment_for_a_period_with_no_data_is_recorded_but_changes_nothing(tmp_path):
    c = ctx(tmp_path)
    new, applied = apply_adjustments(c.mapped, (Adjustment("Rent", ("2030-01",), "future"),))
    assert new == c.mapped
    assert [(x.period, x.present, x.reported_amount, x.delta) for x in applied] == [("2030-01", False, D("0.00"), D("0.00"))]


def test_adjustment_on_an_account_that_is_zero_has_zero_delta(tmp_path):
    c = mini.build(tmp_path, mini.P6, [1000] * 6, [400] * 6, [0] * 6, [5] * 6)
    _, applied = apply_adjustments(c.mapped, (Adjustment("Rent", ("2025-02",), "x"),))
    assert applied[0].present is True and applied[0].delta == 0


def test_unknown_adjustment_account_is_an_error_not_a_silent_no_op(tmp_path):
    c = ctx(tmp_path)
    with pytest.raises(MetricsError) as e:
        apply_adjustments(c.mapped, (Adjustment("Rnet", ("2025-02",), "typo"),))
    assert e.value.code == "UNKNOWN_ADJUSTMENT_ACCOUNT" and "Rnet" in str(e.value)


def test_adjusting_a_revenue_account_works_like_any_other(tmp_path):
    c = ctx(tmp_path)
    new, _ = apply_adjustments(c.mapped, (Adjustment("Sales", ("2025-03",), "x"),))
    assert next(m.record.amount for m in new if m.record.account == "Sales" and m.record.period == "2025-03") == 0
