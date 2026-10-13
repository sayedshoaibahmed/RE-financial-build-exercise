import re
from decimal import Decimal
from fractions import Fraction

import pytest

from tests.fixtures import mini
from tests.fixtures.mini import P6
from trend.metrics import (NA, MetricSet, compute_metrics, fact_id, round_cents, round_pct,
                           window_from_period_range)
from trend.model import MetricsError
from trend.reconcile import BlockedError

D = Decimal
Z = [0] * 6


def run(c, **kw):
    return compute_metrics(c.mapped, c.recon, c.company, **kw)


def val(m, fid):
    return m.facts[fid]


def near(got, want):
    """Exact comparison through Fractions (tests run at Python's default 28-digit Decimal context)."""
    assert isinstance(got, Decimal), got
    assert abs(Fraction(got) - Fraction(want)) < Fraction(1, 10 ** 40), (got, want)


def F(m, fid):
    return Fraction(m.facts[fid])


# ---------- gate: nothing computes unless reconciliation is OK ----------

def test_blocked_reconciliation_produces_no_metrics(tmp_path):
    c = mini.build(tmp_path, P6, [1000] * 6, [400] * 6, [10] * 6, [5] * 6,
                   sales_leaf=[1000, 1000, 1001, 1000, 1000, 1000])
    assert c.recon.status == "BLOCKED"
    with pytest.raises(BlockedError) as e:
        run(c)
    assert e.value.reason == "RECON_FAIL"


def test_reconciliation_without_the_mapping_check_is_not_enough(tmp_path):
    c = mini.build(tmp_path, P6, [1000] * 6, [400] * 6, [10] * 6, [5] * 6, with_mapping_in_recon=False)
    assert c.recon.status == "OK"
    with pytest.raises(MetricsError) as e:
        run(c)
    assert e.value.code == "RECON_INCOMPLETE" and "C9" in str(e.value)


def test_recon_for_different_periods_is_rejected(tmp_path):
    a = mini.build(tmp_path / "a", P6, [1000] * 6, [400] * 6, [10] * 6, [5] * 6)
    b = mini.build(tmp_path / "b", P6[:5], [1000] * 5, [400] * 5, [10] * 5, [5] * 5)
    with pytest.raises(MetricsError) as e:
        compute_metrics(a.mapped, b.recon, a.company)
    assert e.value.code == "RECON_MISMATCH"


# ---------- basic arithmetic, ids, precision ----------

def base(tmp_path, **kw):
    args = dict(periods=P6, sales=[1000, 1100, 900, 1000, 1200, 1000], cogs=[400, 450, 360, 400, 500, 400],
                rent=[100, 100, 100, 100, 100, 100], mkt=[50, 60, 40, 50, 70, 50])
    args.update(kw)
    return mini.build(tmp_path, **args)


def test_monthly_values_and_fact_ids(tmp_path):
    m = run(base(tmp_path))
    assert isinstance(m, MetricSet)
    assert val(m, "rep.rev.total.2025-02") == D("1100.00")
    assert val(m, "rep.cogs.total.2025-02") == D("450.00")
    assert val(m, "rep.gp.2025-02") == D("650.00")
    assert val(m, "rep.opex.total.2025-02") == D("160.00")
    assert val(m, "rep.opinc.2025-02") == D("490.00")
    assert val(m, "rep.net_income.2025-02") == D("490.00")
    assert val(m, "rep.rev.stream.sales.2025-02") == D("1100.00")
    assert val(m, "rep.opex.line.rent.2025-02") == D("100.00")
    assert val(m, "rep.opex.fn.g_and_a.2025-02") == D("100.00")
    assert val(m, "rep.opex.nat.marketing_events.2025-02") == D("60.00")
    assert fact_id("rep", "opex.nat.payroll_benefits", "pct_rev", "2026-06") == "rep.opex.nat.payroll_benefits.pct_rev.2026-06"
    assert fact_id("adj", "opinc", "tw12", "2026-06") == "adj.opinc.tw12.2026-06"
    assert fact_id("rep", "rev.total", None, "2026-06") == "rep.rev.total.2026-06"


def test_all_fact_ids_follow_the_documented_grammar(tmp_path):
    m = run(base(tmp_path, adjustments=[{"account": "Rent", "periods": ["2025-02"], "reason": "x"}]),
            window_lengths=(3,))
    pat = re.compile(r"^(rep|adj|delta)\.[a-z0-9_]+(\.[a-z0-9_]+)*\.\d{4}-\d{2}$")
    bad = [f for f in m.facts if not pat.match(f)]
    assert not bad, bad[:5]
    assert {f.split(".")[0] for f in m.facts} == {"rep", "adj", "delta"}


def test_margins_are_percent_units_at_full_precision(tmp_path):
    m = run(base(tmp_path, sales=[1000, 1100, 900, 1000, 1200, 1000], cogs=[333, 450, 360, 400, 500, 400]))
    gm = val(m, "rep.gm_pct.2025-01")
    assert isinstance(gm, Decimal)
    exact = Fraction(1000 - 333, 1000) * 100
    assert abs(Fraction(gm) - exact) < Fraction(1, 10 ** 40)
    assert round_pct(gm) == D("66.70")
    third = val(run(base(tmp_path / "x", sales=[3] * 6, cogs=[1] * 6)), "rep.gm_pct.2025-01")
    assert len(str(third)) > 20 and round_pct(third) == D("66.67")  # not rounded internally
    assert round_cents(D("2.675")) == D("2.68")


def test_pct_of_revenue_views(tmp_path):
    m = run(base(tmp_path))
    assert val(m, "rep.opex.total.pct_rev.2025-01") == D("15")
    assert val(m, "rep.opex.fn.sales_marketing.pct_rev.2025-01") == D("5")
    assert val(m, "rep.opex.nat.facilities_office.pct_rev.2025-01") == D("10")
    assert val(m, "rep.opex.line.marketing.pct_rev.2025-01") == D("5")
    assert val(m, "rep.rev.mix.sales.2025-01") == D("100")
    assert val(m, "rep.opinc_margin_pct.2025-01") == D("45")


def test_function_and_nature_totals_tie_to_total_opex_on_both_bases(tmp_path):
    c = base(tmp_path, adjustments=[{"account": "Rent", "periods": ["2025-02", "2025-05"], "reason": "x"}])
    m = run(c)
    for basis in ("rep", "adj"):
        for p in P6:
            tot = val(m, f"{basis}.opex.total.{p}")
            for dim in ("fn", "nat"):
                s = sum(v for k, v in m.facts.items()
                        if re.fullmatch(rf"{basis}\.opex\.{dim}\.[a-z_]+\.{p}", k))
                assert s == tot


def test_inputs_are_not_mutated(tmp_path):
    c = base(tmp_path, adjustments=[{"account": "Rent", "periods": ["2025-02"], "reason": "x"}])
    before = [m.record.amount for m in c.mapped]
    run(c)
    assert [m.record.amount for m in c.mapped] == before


# ---------- MoM ----------

def test_mom_amount_and_ratio_views(tmp_path):
    m = run(base(tmp_path))
    assert val(m, "rep.rev.total.mom_abs.2025-02") == D("100")
    assert val(m, "rep.rev.total.mom_pct.2025-02") == D("10")
    assert abs(Fraction(val(m, "rep.rev.total.mom_pct.2025-03")) - Fraction(-200, 1100) * 100) < Fraction(1, 10 ** 40)
    # gross margin moves in percentage points, not percent
    gm1, gm2 = val(m, "rep.gm_pct.2025-01"), val(m, "rep.gm_pct.2025-02")
    near(val(m, "rep.gm_pct.mom_pp.2025-02"), Fraction(gm2) - Fraction(gm1))
    assert "rep.gm_pct.mom_pct.2025-02" not in m.facts
    # %-of-revenue moves in pp too
    near(val(m, "rep.opex.total.pct_rev_mom_pp.2025-02"),
         F(m, "rep.opex.total.pct_rev.2025-02") - F(m, "rep.opex.total.pct_rev.2025-01"))


def test_first_period_has_no_mom(tmp_path):
    m = run(base(tmp_path))
    na = val(m, "rep.rev.total.mom_pct.2025-01")
    assert isinstance(na, NA) and na.reason == "no_prior_period" and na.missing == ("2024-12",)
    assert isinstance(val(m, "rep.rev.total.mom_abs.2025-01"), NA)
    assert isinstance(val(m, "rep.gm_pct.mom_pp.2025-01"), NA)


def test_prior_zero_gives_na_not_a_division_error(tmp_path):
    m = run(base(tmp_path, rent=[-50, 50, 0, 25, 25, 25]))
    assert val(m, "rep.opex.line.rent.mom_pct.2025-02") == D("200")   # (50 - -50) / |-50|
    assert val(m, "rep.opex.line.rent.mom_pct.2025-03") == D("-100")
    na = val(m, "rep.opex.line.rent.mom_pct.2025-04")                 # prior is 0
    assert isinstance(na, NA) and na.reason == "zero_base" and str(na) == "n/a"
    assert val(m, "rep.opex.line.rent.mom_abs.2025-04") == D("25")    # the absolute change is still defined


def test_gap_month_breaks_mom_for_the_month_after_it(tmp_path):
    periods = ["2025-01", "2025-02", "2025-04", "2025-05", "2025-06"]
    m = run(mini.build(tmp_path, periods, [100] * 5, [40] * 5, [10] * 5, [5] * 5))
    na = val(m, "rep.rev.total.mom_pct.2025-04")
    assert isinstance(na, NA) and na.reason == "no_prior_period" and na.missing == ("2025-03",)
    assert val(m, "rep.rev.total.mom_abs.2025-05") == D("0")           # April is present
    assert "rep.rev.total.2025-03" not in m.facts                      # nothing invented for the gap


def test_non_positive_revenue_makes_ratios_na(tmp_path):
    m = run(base(tmp_path, sales=[0, 1100, -100, 1000, 1200, 1000]))
    for k in ("rep.gm_pct.2025-01", "rep.opinc_margin_pct.2025-01", "rep.opex.total.pct_rev.2025-03",
              "rep.gm_pct.2025-03"):
        na = val(m, k)
        assert isinstance(na, NA) and na.reason == "non_positive_revenue", k
    assert isinstance(val(m, "rep.gm_pct.mom_pp.2025-02"), NA)         # prior ratio is n/a


# ---------- windows ----------

def test_complete_window_is_the_sum_of_n_months(tmp_path):
    m = run(base(tmp_path), window_lengths=(3,))
    assert val(m, "rep.rev.total.tw3.2025-03") == D("3000")            # 1000 + 1100 + 900
    assert val(m, "rep.opinc.tw3.2025-06") == D("1430")                # 3200 - 1300 - (300 rent + 170 marketing)
    assert val(m, "rep.rev.total.tw3.2025-06") == D("3200")


def test_window_ratio_is_ratio_of_sums_not_mean_of_ratios(tmp_path):
    c = mini.build(tmp_path, P6[:4], [100, 300, 100, 300], [50, 100, 90, 200], [0] * 4, [0] * 4)
    m = run(c, window_lengths=(2,))
    assert val(m, "rep.gm_pct.tw2.2025-02") == D("62.5")               # (400 - 150) / 400, not mean(50, 66.67)
    mean_of_ratios = (val(m, "rep.gm_pct.2025-01") + val(m, "rep.gm_pct.2025-02")) / 2
    assert val(m, "rep.gm_pct.tw2.2025-02") != mean_of_ratios
    assert val(m, "rep.opinc_margin_pct.tw2.2025-02") == D("62.5")
    # window %-of-revenue is also a ratio of sums
    c2 = base(tmp_path / "y")
    m2 = run(c2, window_lengths=(2,))
    exp = Fraction(100 + 50 + 100 + 60, 1000 + 1100) * 100
    assert abs(Fraction(val(m2, "rep.opex.total.tw2_pct_rev.2025-02")) - exp) < Fraction(1, 10 ** 40)


def test_incomplete_window_is_insufficient_data_naming_the_missing_months(tmp_path):
    m = run(base(tmp_path), window_lengths=(3,))
    na = val(m, "rep.rev.total.tw3.2025-02")
    assert isinstance(na, NA) and na.reason == "insufficient_history"
    assert na.missing == ("2024-12",)
    assert "insufficient data" in str(na) and "2024-12" in str(na)
    na1 = val(m, "rep.rev.total.tw3.2025-01")
    assert na1.missing == ("2024-11", "2024-12")
    assert not isinstance(val(m, "rep.rev.total.tw3.2025-03"), NA)
    big = run(base(tmp_path / "b"), window_lengths=(12,))
    assert all(isinstance(val(big, f"rep.rev.total.tw12.{p}"), NA) for p in P6)   # never a partial sum


def test_window_with_a_gap_is_not_computed(tmp_path):
    periods = ["2025-01", "2025-02", "2025-04", "2025-05", "2025-06"]
    m = run(mini.build(tmp_path, periods, [100] * 5, [40] * 5, [10] * 5, [5] * 5), window_lengths=(3,))
    for p in ("2025-04", "2025-05"):
        na = val(m, f"rep.rev.total.tw3.{p}")
        assert isinstance(na, NA) and na.reason == "gap_in_window" and na.missing == ("2025-03",)
        assert "2025-03" in str(na)
    assert val(m, "rep.rev.total.tw3.2025-06") == D("300")
    assert isinstance(val(m, "rep.gm_pct.tw3.2025-05"), NA)               # ratios inherit the gap
    assert isinstance(val(m, "rep.opinc.tw3.2025-05"), NA)


def test_several_lengths_in_one_run_and_window_end(tmp_path):
    m = run(base(tmp_path), window_lengths=(2, 3), window_end="2025-04")
    assert val(m, "rep.rev.total.tw2.2025-04") == D("1900")
    assert val(m, "rep.rev.total.tw3.2025-04") == D("3000")      # 1100 + 900 + 1000
    assert "rep.rev.total.tw2.2025-05" not in m.facts                  # no windows after the window end
    assert "rep.rev.total.2025-06" in m.facts                          # monthly facts are still produced
    assert (m.window_lengths, m.window_end) == ((2, 3), "2025-04")


def test_window_settings_default_from_company_yaml(tmp_path):
    m = run(base(tmp_path, windows={"lengths": [2], "end": "2025-03"}))
    assert (m.window_lengths, m.window_end) == ((2,), "2025-03")
    assert val(m, "rep.rev.total.tw2.2025-03") == D("2000")
    assert "rep.rev.total.tw2.2025-04" not in m.facts
    # arguments override the config
    m2 = run(base(tmp_path / "o", windows={"lengths": [2], "end": "2025-03"}), window_lengths=(3,), window_end="latest")
    assert (m2.window_lengths, m2.window_end) == ((3,), "2025-06")


def test_window_mom_and_prev_views(tmp_path):
    m = run(base(tmp_path), window_lengths=(2,))
    assert val(m, "rep.rev.total.tw2_mom_abs.2025-03") == val(m, "rep.rev.total.tw2.2025-03") - val(m, "rep.rev.total.tw2.2025-02")
    assert isinstance(val(m, "rep.rev.total.tw2_mom_abs.2025-02"), NA)       # window at 2025-01 is not complete
    assert val(m, "rep.rev.total.tw2_prev_abs.2025-04") == val(m, "rep.rev.total.tw2.2025-04") - val(m, "rep.rev.total.tw2.2025-02")
    assert isinstance(val(m, "rep.rev.total.tw2_prev_abs.2025-03"), NA)      # the preceding window is incomplete
    near(val(m, "rep.gm_pct.tw2_mom_pp.2025-03"), F(m, "rep.gm_pct.tw2.2025-03") - F(m, "rep.gm_pct.tw2.2025-02"))


def test_period_range_sugar():
    assert window_from_period_range("2025-07:2026-06") == (12, "2026-06")
    assert window_from_period_range("2026-01:2026-03") == (3, "2026-03")
    assert window_from_period_range("2026-03:2026-03") == (1, "2026-03")
    for bad in ("2026-06:2025-07", "2026-06", "2026-13:2026-14", "a:b"):
        with pytest.raises(MetricsError):
            window_from_period_range(bad)


# ---------- as-of: no lookahead ----------

def test_as_of_drops_later_periods_before_any_computation(tmp_path):
    c = base(tmp_path)
    full = run(c, window_lengths=(2,))
    m = run(c, as_of="2025-04", window_lengths=(2,))
    assert m.periods == tuple(P6[:4]) and m.window_end == "2025-04" and m.as_of == "2025-04"
    assert not [f for f in m.facts if f.endswith("2025-05") or f.endswith("2025-06")]
    for f, v in m.facts.items():                                           # earlier values are identical
        assert full.facts[f] == v


def test_window_end_after_as_of_is_an_error(tmp_path):
    with pytest.raises(MetricsError) as e:
        run(base(tmp_path), as_of="2025-04", window_end="2025-05")
    assert e.value.code == "WINDOW_END_AFTER_AS_OF"


def test_window_end_must_fall_inside_the_data(tmp_path):
    for end in ("2025-12", "2024-06"):
        with pytest.raises(MetricsError):
            run(base(tmp_path / end), window_end=end)
    with pytest.raises(MetricsError):
        run(base(tmp_path / "ao"), as_of="2024-01")


def test_bad_arguments(tmp_path):
    c = base(tmp_path)
    for kw in ({"as_of": "April"}, {"window_end": "soon"}, {"window_lengths": (0,)}, {"window_lengths": ()},
               {"window_lengths": (3, 3)}):
        with pytest.raises(MetricsError):
            run(c, **kw)


# ---------- adjusted basis ----------

def adj_company(tmp_path, **kw):
    return base(tmp_path, adjustments=[{"account": "Rent", "periods": ["2025-02"], "reason": "one-off"}], **kw)


def test_adjusted_basis_is_computed_from_its_own_record_set(tmp_path):
    m = run(adj_company(tmp_path), window_lengths=(3,))
    assert val(m, "rep.opex.total.2025-02") == D("160") and val(m, "adj.opex.total.2025-02") == D("60")
    assert val(m, "adj.opex.line.rent.2025-02") == D("0")
    assert val(m, "adj.opex.fn.g_and_a.2025-02") == D("0")
    assert val(m, "adj.opex.nat.facilities_office.2025-02") == D("0")
    assert val(m, "adj.opinc.2025-02") == val(m, "rep.opinc.2025-02") + D("100")
    assert val(m, "delta.opinc.2025-02") == D("100") and val(m, "delta.opex.total.2025-02") == D("-100")
    # revenue, COGS and gross profit are untouched because the adjusted account is opex
    for k in ("rev.total", "cogs.total", "gp", "gm_pct"):
        assert val(m, f"rep.{k}.2025-02") == val(m, f"adj.{k}.2025-02")
    # adjusted ratios use adjusted numerators over unchanged revenue
    near(val(m, "adj.opinc_margin_pct.2025-02"), F(m, "adj.opinc.2025-02") / F(m, "adj.rev.total.2025-02") * 100)
    near(val(m, "delta.opinc_margin_pct.2025-02"),
         F(m, "adj.opinc_margin_pct.2025-02") - F(m, "rep.opinc_margin_pct.2025-02"))


def test_adjusted_window_equals_reported_window_minus_adjustments_inside_it(tmp_path):
    m = run(adj_company(tmp_path), window_lengths=(3,))
    assert val(m, "adj.opinc.tw3.2025-03") == val(m, "rep.opinc.tw3.2025-03") + D("100")   # window 01-03 contains 02
    assert val(m, "delta.opinc.tw3.2025-03") == D("100")
    for p in ("2025-05", "2025-06"):                                                          # windows 03-05 / 04-06 do not
        assert val(m, f"adj.opinc.tw3.{p}") == val(m, f"rep.opinc.tw3.{p}")
        assert val(m, f"adj.gm_pct.tw3.{p}") == val(m, f"rep.gm_pct.tw3.{p}")
        assert val(m, f"delta.opinc.tw3.{p}") == 0
    near(val(m, "adj.opinc_margin_pct.tw3.2025-03"),
         F(m, "adj.opinc.tw3.2025-03") / F(m, "adj.rev.total.tw3.2025-03") * 100)


def test_adjustment_with_no_data_changes_nothing_and_is_reported(tmp_path):
    c = base(tmp_path, adjustments=[{"account": "Rent", "periods": ["2030-01"], "reason": "future"}])
    m = run(c, window_lengths=(3,))
    for f, v in m.facts.items():
        if f.startswith("adj."):
            assert v == m.facts["rep." + f[4:]], f
    assert [(a.account, a.period, a.present) for a in m.adjustments] == [("Rent", "2030-01", False)]


def test_unknown_adjustment_account_blocks_metrics(tmp_path):
    c = base(tmp_path, adjustments=[{"account": "Typo", "periods": ["2025-02"], "reason": "x"}])
    with pytest.raises(MetricsError) as e:
        run(c)
    assert e.value.code == "UNKNOWN_ADJUSTMENT_ACCOUNT"


def test_adjusting_revenue_flows_into_margins_without_special_cases(tmp_path):
    c = base(tmp_path, adjustments=[{"account": "Sales", "periods": ["2025-03"], "reason": "x"}])
    m = run(c)
    assert val(m, "adj.rev.total.2025-03") == D("0")
    assert isinstance(val(m, "adj.gm_pct.2025-03"), NA) and val(m, "adj.gm_pct.2025-03").reason == "non_positive_revenue"
    assert val(m, "rep.gm_pct.2025-03") == D("60")


def test_no_adjustments_means_adj_equals_rep_and_zero_deltas(tmp_path):
    m = run(base(tmp_path), window_lengths=(3,))
    assert m.adjustments == []
    for f, v in m.facts.items():
        if f.startswith("adj."):
            assert v == m.facts["rep." + f[4:]]
        if f.startswith("delta.") and isinstance(v, Decimal):
            assert v == 0


# ---------- company without COGS ----------

def test_company_without_cogs(tmp_path):
    c = mini.build(tmp_path, P6, [1000] * 6, Z, [100] * 6, [50] * 6, with_cogs=False)
    assert c.recon.status == "OK"
    m = run(c, window_lengths=(3,))
    assert val(m, "rep.cogs.total.2025-01") == D("0") and val(m, "rep.gp.2025-01") == D("1000")
    assert val(m, "rep.opinc.2025-01") == D("850")
    na = val(m, "rep.gm_pct.2025-01")
    assert isinstance(na, NA) and na.reason == "no_cogs_section"          # no invented 100% gross margin
    assert isinstance(val(m, "rep.gm_pct.tw3.2025-03"), NA)
    assert val(m, "rep.opinc_margin_pct.2025-01") == D("85")
    assert val(m, "rep.opinc.tw3.2025-03") == D("2550")
