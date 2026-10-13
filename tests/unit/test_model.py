from decimal import Decimal

import pytest

from trend.model import ControlTotal, Period, Record, money


def test_money_rounds_to_cents_and_cleans_float_noise():
    assert money(468181.79999999993) == Decimal("468181.80")
    assert money(0.1 + 0.2) == Decimal("0.30")
    assert money(5) == Decimal("5.00")
    assert money(Decimal("1.005")) == Decimal("1.01")  # half-up
    assert money(-1806.24) == Decimal("-1806.24")


@pytest.mark.parametrize("bad", [True, "12.30", None])
def test_money_rejects_non_numbers(bad):
    with pytest.raises(TypeError):
        money(bad)


def test_period_parse_str_order():
    assert str(Period.parse("2026-06")) == "2026-06"
    assert Period.parse("2025-12") < Period.parse("2026-01")
    with pytest.raises(ValueError):
        Period.parse("2026-13")
    with pytest.raises(ValueError):
        Period.parse("June 2026")


def test_record_requires_decimal_and_list_path():
    r = Record("c", "excel", "2026-06", "opex", ["Operating Expenses", "A/B"], "A/B",
               Decimal("1.00"), "Income Statement!C9")
    assert r.account_path == ["Operating Expenses", "A/B"]
    with pytest.raises(TypeError):
        Record("c", "excel", "2026-06", "opex", ["x"], "x", 1.5, "S!C1")
    with pytest.raises(TypeError):
        Record("c", "excel", "2026-06", "opex", "Operating Expenses/x", "x", Decimal("1"), "S!C1")


def test_control_total_requires_decimal():
    ControlTotal("2026-06", "Total Income", Decimal("1.00"), "Income Statement!C14")
    with pytest.raises(TypeError):
        ControlTotal("2026-06", "Total Income", 1.0, "Income Statement!C14")
