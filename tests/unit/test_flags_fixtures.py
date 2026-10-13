"""Every flag type on code-built fixtures: fires, just under the threshold, exactly at the threshold."""
import json
import re
from decimal import Decimal

import pytest

from tests.fixtures import mini
from tests.fixtures.mini import months
from trend.adapters.base import AdapterError
from trend.flags import TYPES, Flag, FlagSet, blocked_flag, compute_flags
from trend.mapping import MappingError
from trend.metrics import compute_metrics
from trend.model import MetricsError
from trend.reconcile import BlockedError

D = Decimal


def run(tmp_path, n=None, *, periods=None, sales=None, cogs=None, rent=None, mkt=None, thresholds=None,
        adjustments=None, expected_negative=(), as_of=None, shuffle=False):
    periods = periods or months(n)
    n = len(periods)
    c = mini.build(tmp_path, periods, sales or [100000] * n, cogs or [40000] * n, rent or [100] * n,
                   mkt or [50] * n, adjustments=adjustments, thresholds=thresholds,
                   expected_negative=expected_negative)
    m = compute_metrics(c.mapped, c.recon, c.company, as_of=as_of)
    mapped = list(reversed(c.mapped)) if shuffle else c.mapped
    return compute_flags(mapped, c.recon, m, c.company), m, c


def of(fs, typ, subject=None, period=None):
    return [f for f in fs.flags if f.type == typ and (subject is None or f.subject == subject)
            and (period is None or f.period == period)]


# ================= MISSING_MONTH =================

def test_missing_month_fires_for_each_gap_month(tmp_path):
    p = ["2025-01", "2025-02", "2025-05", "2025-06"]            # 03 and 04 are missing
    fs, m, _ = run(tmp_path, periods=p)
    got = of(fs, "MISSING_MONTH")
    assert [f.period for f in got] == ["2025-03", "2025-04"]
    f = got[0]
    assert f.severity == "high" and f.basis == "both"
    assert f.fact_ids == ("rep.rev.total.2025-02", "rep.rev.total.2025-05")      # the data either side of the gap
    assert all(i in m.facts for i in f.fact_ids)


def test_no_gap_no_flag_and_edges_are_not_gaps(tmp_path):
    fs, _, _ = run(tmp_path, 6)
    assert of(fs, "MISSING_MONTH") == []
    fs2, _, _ = run(tmp_path / "b", periods=["2025-03", "2025-04", "2025-05"])
    assert of(fs2, "MISSING_MONTH") == []                        # only gaps between first and last count


def test_as_of_hides_a_gap_that_lies_after_it(tmp_path):
    p = ["2025-01", "2025-02", "2025-03", "2025-05", "2025-06"]
    fs, _, _ = run(tmp_path, periods=p, as_of="2025-03")
    assert of(fs, "MISSING_MONTH") == []


# ================= NONRECURRING =================

ADJ_RENT = [{"account": "Rent", "periods": ["2025-03"], "reason": "one-off"}]


def test_nonrecurring_fires_for_a_configured_nonzero_adjustment(tmp_path):
    fs, m, _ = run(tmp_path, 6, adjustments=ADJ_RENT)
    (f,) = of(fs, "NONRECURRING")
    assert (f.period, f.subject, f.severity, f.basis) == ("2025-03", "Rent", "high", "both")
    assert f.observed == {"reported": D("100.00"), "adjusted": D("0.00")}
    assert set(f.fact_ids) == {"rep.opex.line.rent.2025-03", "adj.opex.line.rent.2025-03",
                               "delta.opex.line.rent.2025-03", "rep.opinc.2025-03", "adj.opinc.2025-03",
                               "delta.opinc.2025-03"}
    assert all(i in m.facts for i in f.fact_ids)
    assert "reported" in f.reason and "adjusted" in f.reason


def test_nonrecurring_threshold_is_nonzero(tmp_path):
    # exactly zero in its period: not flagged. One cent: flagged.
    fs, _, _ = run(tmp_path, 6, rent=[100, 100, 0, 100, 100, 100], adjustments=ADJ_RENT)
    assert of(fs, "NONRECURRING") == []
    fs2, _, _ = run(tmp_path / "b", 6, rent=[100, 100, 0.01, 100, 100, 100], adjustments=ADJ_RENT)
    assert [f.observed["reported"] for f in of(fs2, "NONRECURRING")] == [D("0.01")]


def test_nonrecurring_ignores_periods_without_data(tmp_path):
    fs, _, _ = run(tmp_path, 6, adjustments=[{"account": "Rent", "periods": ["2030-01"], "reason": "x"}])
    assert of(fs, "NONRECURRING") == []


def test_nonrecurring_evidence_for_revenue_and_cogs_accounts(tmp_path):
    fs, m, _ = run(tmp_path, 6, adjustments=[{"account": "Sales", "periods": ["2025-02"], "reason": "x"},
                                             {"account": "Hosting", "periods": ["2025-02"], "reason": "y"}])
    sales = of(fs, "NONRECURRING", "Sales")[0]
    hosting = of(fs, "NONRECURRING", "Hosting")[0]
    assert "rep.rev.stream.sales.2025-02" in sales.fact_ids
    assert "rep.cogs.total.2025-02" in hosting.fact_ids
    assert all(i in m.facts for f in (sales, hosting) for i in f.fact_ids)


def test_adjusted_items_get_no_other_flag_on_the_adjusted_account(tmp_path):
    # a huge one-off that is configured away: NONRECURRING says so; no spike / negative duplicates it
    fs, _, _ = run(tmp_path, 9, rent=[100] * 8 + [9000], adjustments=[{"account": "Rent", "periods": ["2025-09"], "reason": "x"}])
    assert [f.type for f in fs.flags if f.subject == "Rent"] == ["NONRECURRING"]


# ================= LEVEL_SHIFT =================

def shift_sales(tail, head=(12500,) * 3):
    s = list(head) + list(tail)
    return s


def test_level_shift_fires_with_a_sustained_drop(tmp_path):
    s = shift_sales([10000, 10500, 9500, 10000, 10000, 10000])          # 12.5k -> 10k = -20.00% exactly
    fs, m, _ = run(tmp_path, sales=s, periods=months(len(s)))
    (f,) = of(fs, "LEVEL_SHIFT")
    assert (f.subject, f.period, f.severity, f.basis) == ("Sales", "2025-04", "medium", "both")
    assert f.observed["change_pct"] == D("-20")
    assert f.observed["before_avg"] == D("12500") and f.observed["after_avg"] == D("10000")
    assert all(i in m.facts for i in f.fact_ids) and "rep.rev.stream.sales.2025-04" in f.fact_ids


def test_level_shift_change_just_under_20_percent_is_not_flagged(tmp_path):
    s = [10000] * 3 + [8001] * 6                                       # -19.99%
    fs, _, _ = run(tmp_path, sales=s, periods=months(len(s)))
    assert of(fs, "LEVEL_SHIFT") == []
    s2 = [10000] * 3 + [8000] * 6                                      # exactly -20.00%
    fs2, _, _ = run(tmp_path / "b", sales=s2, periods=months(len(s2)))
    assert [f.period for f in of(fs2, "LEVEL_SHIFT")] == ["2025-04"]


def test_level_shift_increase_is_also_a_shift(tmp_path):
    s = [10000] * 3 + [12500] * 6
    fs, _, _ = run(tmp_path, sales=s, periods=months(len(s)))
    assert [f.observed["change_pct"] for f in of(fs, "LEVEL_SHIFT")] == [D("25")]


def test_level_shift_band_edge(tmp_path):
    exactly = shift_sales([10000, 10500, 9500, 10000, 10000, 10000])    # +/-5.00% of the new level: passes
    fs, _, _ = run(tmp_path, sales=exactly, periods=months(len(exactly)))
    assert len(of(fs, "LEVEL_SHIFT")) == 1
    over = shift_sales([10000, 10501, 9500, 10000, 10000, 10000])        # just outside the band
    fs2, _, _ = run(tmp_path / "b", sales=over, periods=months(len(over)))
    assert of(fs2, "LEVEL_SHIFT") == []


def test_level_shift_needs_the_following_months_to_confirm(tmp_path):
    s = [100000] * 3 + [60000] * 2                                      # only 1 month after the shift month
    fs, _, _ = run(tmp_path, sales=s, periods=months(len(s)))
    assert of(fs, "LEVEL_SHIFT") == []
    s2 = [100000] * 3 + [60000] * 3                                     # 2 confirming months: raised
    fs2, _, _ = run(tmp_path / "b", sales=s2, periods=months(len(s2)))
    assert [f.period for f in of(fs2, "LEVEL_SHIFT")] == ["2025-04"]


def test_level_shift_optional_materiality_floor(tmp_path):
    s = shift_sales([10000, 10500, 9500, 10000, 10000, 10000])           # the monthly average moves by exactly 2,500
    fs, _, _ = run(tmp_path, sales=s, periods=months(len(s)), thresholds={"level_shift_min_abs": 2500})
    assert len(of(fs, "LEVEL_SHIFT")) == 1                                # equal to the floor: flagged
    fs2, _, _ = run(tmp_path / "b", sales=s, periods=months(len(s)), thresholds={"level_shift_min_abs": 2500.01})
    assert of(fs2, "LEVEL_SHIFT") == []                                   # just above the move: not flagged
    fs3, _, _ = run(tmp_path / "c", sales=s, periods=months(len(s)))      # default 0: PLAN rule, no floor
    assert len(of(fs3, "LEVEL_SHIFT")) == 1 and of(fs3, "LEVEL_SHIFT")[0].threshold["min_abs_change"] == 0


def test_one_shift_gives_one_flag_not_one_per_qualifying_month(tmp_path):
    s = [45000] * 3 + [26666.66] * 6                                    # the Platform Revenue pattern
    fs, _, _ = run(tmp_path, sales=s, periods=months(len(s)))
    assert [f.period for f in of(fs, "LEVEL_SHIFT")] == ["2025-04"]


def test_level_shift_skips_a_zero_baseline_and_gaps(tmp_path):
    s = [0] * 3 + [10000] * 3
    fs, _, _ = run(tmp_path, sales=s, periods=months(6))
    assert of(fs, "LEVEL_SHIFT") == []
    p = ["2025-01", "2025-02", "2025-04", "2025-05", "2025-06", "2025-07"]        # 2025-03 missing
    fs2, _, _ = run(tmp_path / "b", periods=p, sales=[100000, 100000, 60000, 60000, 60000, 60000])
    assert of(fs2, "LEVEL_SHIFT") == []


# ================= ONE_OFF_SPIKE =================

def test_spike_strictly_greater_than_the_cutoff(tmp_path):
    at = [100] * 8 + [5100]                                             # deviation exactly 5,000.00: not a spike
    fs, _, _ = run(tmp_path, 9, rent=at)
    assert of(fs, "ONE_OFF_SPIKE") == []
    over = [100] * 8 + [5100.01]                                        # 5,000.01: spike
    fs2, m, _ = run(tmp_path / "b", 9, rent=over)
    (f,) = of(fs2, "ONE_OFF_SPIKE")
    assert (f.subject, f.period, f.severity, f.basis) == ("Rent", "2025-09", "medium", "both")
    assert f.observed["value"] == D("5100.01") and f.observed["median"] == D("100")
    assert f.observed["deviation"] == D("5000.01") and f.threshold["cutoff"] == D("5000")
    assert all(i in m.facts for i in f.fact_ids) and "adj.opex.line.rent.2025-09" in f.fact_ids


def test_spike_mad_edge(tmp_path):
    # spike_min_history=8 so only the 9th month can be judged (an earlier month with MAD 0 would otherwise
    # start its own run and absorb the target month)
    th = {"spike_min_abs": 0, "spike_min_history": 8}
    alt = [100, 200] * 4                                                # median 150, MAD 50, cutoff 4 x 50 = 200
    fs, _, _ = run(tmp_path, 9, rent=alt + [350], thresholds=th)        # deviation exactly 200: not a spike
    assert of(fs, "ONE_OFF_SPIKE") == []
    fs2, _, _ = run(tmp_path / "b", 9, rent=alt + [350.01], thresholds=th)
    (f,) = of(fs2, "ONE_OFF_SPIKE")
    assert f.period == "2025-09" and f.observed["mad"] == D("50") and f.threshold["cutoff"] == D("200")


def test_spike_needs_six_months_of_history(tmp_path):
    fs, _, _ = run(tmp_path, 6, rent=[100] * 5 + [9000])                # 5 prior months
    assert of(fs, "ONE_OFF_SPIKE") == []
    fs2, _, _ = run(tmp_path / "b", 7, rent=[100] * 6 + [9000])         # exactly 6 prior months
    assert [f.period for f in of(fs2, "ONE_OFF_SPIKE")] == ["2025-07"]


def test_first_ever_nonzero_value_is_a_spike(tmp_path):
    fs, _, _ = run(tmp_path, 9, mkt=[0] * 8 + [10000])
    (f,) = of(fs, "ONE_OFF_SPIKE")
    assert (f.subject, f.observed["median"]) == ("Marketing", D("0"))


def test_spike_downward_and_revenue_lines(tmp_path):
    fs, _, _ = run(tmp_path, 9, sales=[100000] * 8 + [0])
    (f,) = of(fs, "ONE_OFF_SPIKE")
    assert (f.subject, f.period, f.basis) == ("Sales", "2025-09", "both")
    assert "rep.rev.stream.sales.2025-09" in f.fact_ids


def test_spike_uses_the_adjusted_basis_for_cost_lines(tmp_path):
    # reported history has a 1000/month plateau that the adjustments zero out; the target month is
    # a spike on both bases but with different numbers, so the flag is labelled adjusted
    rent = [100, 100, 100, 1000, 1000, 1000, 1000, 1000, 6200]
    adj = [{"account": "Rent", "periods": months(8)[3:], "reason": "x"}]
    fs, _, _ = run(tmp_path, 9, rent=rent, adjustments=adj)
    (f,) = of(fs, "ONE_OFF_SPIKE", "Rent")
    assert f.basis == "adjusted" and f.observed["median"] == D("0") and f.observed["deviation"] == D("6200")
    assert "adj.opex.line.rent.2025-09" in f.fact_ids


# ---- collapsing repeated spikes ----

def spikes_of(fs, subject="Rent"):
    return [(f.period, f.observed["direction"], f.observed["continued_months"]) for f in of(fs, "ONE_OFF_SPIKE", subject)]


def test_every_spike_states_its_direction(tmp_path):
    up, _, _ = run(tmp_path, 9, rent=[100] * 8 + [9000])
    down, _, _ = run(tmp_path / "d", 9, sales=[100000] * 8 + [0])
    assert spikes_of(up) == [("2025-09", "up", 0)]
    assert spikes_of(down, "Sales") == [("2025-09", "down", 0)]
    f = of(up, "ONE_OFF_SPIKE")[0]
    assert "(up)" in f.reason and f.observed["direction"] == "up"
    assert "(down)" in of(down, "ONE_OFF_SPIKE")[0].reason


def test_consecutive_months_in_the_same_direction_collapse_into_one_flag_at_the_first_month(tmp_path):
    fs, m, _ = run(tmp_path, 11, rent=[100] * 8 + [9000, 9000, 9000])     # median stays 100 for all three months
    assert spikes_of(fs) == [("2025-09", "up", 2)]                        # one flag, two continued months
    f = of(fs, "ONE_OFF_SPIKE")[0]
    assert f.fact_ids == ("adj.opex.line.rent.2025-09",)                  # the first month's evidence is kept
    assert "2 more consecutive month" in f.reason and "2025-11" in f.reason
    assert all(i in m.facts for i in f.fact_ids)


def test_a_change_of_direction_starts_a_new_flag(tmp_path):
    fs, _, _ = run(tmp_path, 10, rent=[5000] * 8 + [15000, -10000])       # up, then down, both far from the median 5,000
    assert spikes_of(fs) == [("2025-09", "up", 0), ("2025-10", "down", 0)]


def test_an_unflagged_month_in_between_starts_a_new_flag(tmp_path):
    fs, _, _ = run(tmp_path, 11, rent=[100] * 8 + [9000, 100, 9000])
    assert spikes_of(fs) == [("2025-09", "up", 0), ("2025-11", "up", 0)]


def test_a_run_longer_than_the_gap_rule_and_two_runs_on_one_account(tmp_path):
    rent = [100] * 8 + [9000, 9000, 100, 9000, 9000, 9000]
    fs, _, _ = run(tmp_path, 14, rent=rent)
    assert spikes_of(fs) == [("2025-09", "up", 1), ("2025-12", "up", 2)]


def test_collapse_is_per_account(tmp_path):
    fs, _, _ = run(tmp_path, 10, rent=[100] * 8 + [9000, 9000], mkt=[50] * 8 + [9000, 9000])
    assert spikes_of(fs, "Rent") == [("2025-09", "up", 1)] and spikes_of(fs, "Marketing") == [("2025-09", "up", 1)]


def test_continued_months_is_in_the_compact_dict(tmp_path):
    fs, _, _ = run(tmp_path, 11, rent=[100] * 8 + [9000, 9000, 9000])
    spike = next(x for x in fs.to_dict()["flags"] if x["type"] == "ONE_OFF_SPIKE")
    assert spike["observed"]["continued_months"] == 2 and spike["observed"]["direction"] == "up"


# ================= MOM_MOVE =================

def last(n):
    return months(n)[-1]


def test_mom_move_fires_on_the_latest_month_only(tmp_path):
    s = [100000] * 5 + [125000]
    fs, m, _ = run(tmp_path, 6, sales=s)
    f = of(fs, "MOM_MOVE", "rev.total")[0]
    assert (f.period, f.severity, f.basis) == ("2025-06", "medium", "both")
    assert f.observed == {"abs": D("25000"), "pct": D("25")} and f.threshold == {"min_abs": D("5000"), "min_pct": D("20")}
    assert all(i in m.facts for i in f.fact_ids)
    assert of(fs, "MOM_MOVE", "rev.stream.sales") and of(fs, "MOM_MOVE", "gp")
    assert {x.period for x in of(fs, "MOM_MOVE")} == {"2025-06"}


def test_mom_move_ignores_earlier_months(tmp_path):
    fs, _, _ = run(tmp_path, 6, sales=[100000, 100000, 150000, 150000, 150000, 150000])
    assert of(fs, "MOM_MOVE") == []


def test_mom_move_percent_boundary(tmp_path):
    fs, _, _ = run(tmp_path, 6, sales=[100000] * 5 + [119990])          # +19.99%
    assert of(fs, "MOM_MOVE", "rev.total") == []
    fs2, _, _ = run(tmp_path / "b", 6, sales=[100000] * 5 + [120000])   # exactly +20.00%
    assert len(of(fs2, "MOM_MOVE", "rev.total")) == 1


def test_mom_move_amount_boundary(tmp_path):
    fs, _, _ = run(tmp_path, 6, rent=[20000] * 5 + [24999.99])          # +4,999.99 (+25%)
    assert of(fs, "MOM_MOVE", "opex.fn.g_and_a") == []
    fs2, _, _ = run(tmp_path / "b", 6, rent=[20000] * 5 + [25000])      # exactly +5,000.00
    assert len(of(fs2, "MOM_MOVE", "opex.fn.g_and_a")) == 1
    assert len(of(fs2, "MOM_MOVE", "opex.nat.facilities_office")) == 1


def test_mom_move_from_a_zero_base(tmp_path):
    fs, _, _ = run(tmp_path, 6, mkt=[0] * 5 + [6000])
    (f,) = of(fs, "MOM_MOVE", "opex.fn.sales_marketing")
    assert f.observed == {"abs": D("6000"), "pct": "from zero"}


def test_mom_move_totals_use_the_adjusted_basis(tmp_path):
    # the reported jump is the configured one-off; on the adjusted basis opex falls by only 100
    adj = [{"account": "Rent", "periods": [last(6)], "reason": "x"}]
    fs, _, _ = run(tmp_path, 6, rent=[100] * 5 + [9000], adjustments=adj)
    assert of(fs, "MOM_MOVE", "opex.fn.g_and_a") == [] and of(fs, "MOM_MOVE", "opex.total") == []
    assert len(of(fs, "NONRECURRING")) == 1


def test_mom_move_totals_cite_adjusted_facts(tmp_path):
    adj = [{"account": "Rent", "periods": ["2025-02"], "reason": "x"}]
    fs, _, _ = run(tmp_path, 6, rent=[100, 100, 100, 100, 100, 30000], adjustments=adj)
    f = of(fs, "MOM_MOVE", "opex.total")[0]
    assert f.basis == "both" and all(i.startswith("adj.") for i in f.fact_ids)


def test_margin_move_in_percentage_points(tmp_path):
    base = dict(sales=[1000] * 6, rent=[100] * 6, mkt=[50] * 6)
    fs, m, _ = run(tmp_path, 6, cogs=[400] * 5 + [430], **base)             # 60% -> 57% = -3.00 pp exactly
    (g,) = of(fs, "MOM_MOVE", "gm_pct")
    assert g.observed == {"pp": D("-3")} and g.threshold == {"min_pp": D("3")} and g.basis == "both"
    assert all(i in m.facts for i in g.fact_ids) and of(fs, "MOM_MOVE", "opinc_margin_pct")
    fs2, _, _ = run(tmp_path / "b", 6, cogs=[400] * 5 + [429], **base)      # -2.90 pp
    assert of(fs2, "MOM_MOVE", "gm_pct") == [] and of(fs2, "MOM_MOVE", "opinc_margin_pct") == []


def test_margin_move_on_both_bases_when_the_basis_changes_the_result(tmp_path):
    adj = [{"account": "Rent", "periods": ["2025-06"], "reason": "x"}]
    fs, _, _ = run(tmp_path, 6, sales=[1000] * 6, cogs=[400] * 6, rent=[100] * 5 + [200], mkt=[50] * 6, adjustments=adj)
    got = {f.basis: f.observed["pp"] for f in of(fs, "MOM_MOVE", "opinc_margin_pct")}
    assert got == {"reported": D("-10"), "adjusted": D("10")}                # -10 pp reported, +10 pp adjusted
    assert of(fs, "MOM_MOVE", "gm_pct") == []


# ================= NEGATIVE_VALUE =================

def neg_run(tmp_path, amount, min_abs=None, **kw):
    th = {} if min_abs is None else {"negative_min_abs_amount": min_abs}
    return run(tmp_path, 6, rent=[100, 100, amount, 100, 100, 100], thresholds=th or None, **kw)


def test_negative_value_fires(tmp_path):
    fs, m, c = neg_run(tmp_path, -500)
    (f,) = of(fs, "NEGATIVE_VALUE")
    assert (f.subject, f.period, f.severity, f.basis) == ("Rent", "2025-03", "low", "both")
    assert f.observed == {"amount": D("-500.00"), "reversal_of": None}
    assert f.threshold == {"min_abs_amount": D("0")}
    assert "opex.line.rent" in " ".join(f.fact_ids) and all(i in m.facts for i in f.fact_ids)
    assert f.source_refs and re.fullmatch(r"Income Statement!E\d+", f.source_refs[0])


@pytest.mark.parametrize("amount,flagged", [(-0.99, False), (-1.00, True), (-1.01, True), (-0.01, False)])
def test_min_abs_amount_boundary_below_is_suppressed_equal_or_above_is_flagged(tmp_path, amount, flagged):
    fs, m, _ = neg_run(tmp_path, amount, min_abs=1.00)
    assert bool(of(fs, "NEGATIVE_VALUE")) is flagged
    if not flagged:      # suppressed values stay visible, and stay in the totals
        assert fs.suppressed_negatives == [{"account": "Rent", "period": "2025-03", "amount": D(str(amount)).quantize(D("0.01"))}]
    else:
        assert fs.suppressed_negatives == []
    assert m.facts["rep.opex.line.rent.2025-03"] == D(str(amount)).quantize(D("0.01"))


def test_a_cent_negative_is_flagged_when_min_abs_is_zero_and_by_default(tmp_path):
    fs, _, _ = neg_run(tmp_path, -0.01, min_abs=0)
    assert [f.observed["amount"] for f in of(fs, "NEGATIVE_VALUE")] == [D("-0.01")]
    fs2, _, _ = neg_run(tmp_path / "b", -0.01)             # no thresholds block: code fallback is 0
    assert len(of(fs2, "NEGATIVE_VALUE")) == 1


def test_expected_negative_accounts_are_exempt_and_not_listed_as_suppressed(tmp_path):
    fs, _, _ = neg_run(tmp_path, -500, expected_negative=("Rent",))
    assert of(fs, "NEGATIVE_VALUE") == [] and fs.suppressed_negatives == []


def test_expected_negative_exempts_only_its_own_account(tmp_path):
    fs, _, _ = run(tmp_path, 6, rent=[100, 100, -500, 100, 100, 100], mkt=[50, 50, -60, 50, 50, 50],
                   expected_negative=("Rent",))
    assert [f.subject for f in of(fs, "NEGATIVE_VALUE")] == ["Marketing"]


def test_negative_on_revenue_and_cogs_lines(tmp_path):
    fs, m, _ = run(tmp_path, 6, sales=[100000, 100000, -2000, 100000, 100000, 100000],
                   cogs=[40000, 40000, -300, 40000, 40000, 40000])
    assert {f.subject for f in of(fs, "NEGATIVE_VALUE")} == {"Sales", "Hosting"}
    h = of(fs, "NEGATIVE_VALUE", "Hosting")[0]
    assert "rep.cogs.total.2025-03" in h.fact_ids and h.source_refs


def test_reversal_annotation(tmp_path):
    fs, _, _ = run(tmp_path, 6, rent=[1000, 100, -1000, 100, 100, 100])    # +1000 two months before the -1000
    (f,) = of(fs, "NEGATIVE_VALUE")
    assert f.observed["reversal_of"] == "2025-01" and "possible reversal of 2025-01" in f.reason


def test_reversal_tolerance_and_lookback(tmp_path):
    fs, _, _ = run(tmp_path, 6, rent=[1000, 100, -999.99, 100, 100, 100])   # within +/-0.01
    assert of(fs, "NEGATIVE_VALUE")[0].observed["reversal_of"] == "2025-01"
    fs2, _, _ = run(tmp_path / "b", 6, rent=[1000, 100, -999.98, 100, 100, 100])   # 0.02 away
    assert of(fs2, "NEGATIVE_VALUE")[0].observed["reversal_of"] is None
    fs3, _, _ = run(tmp_path / "c", 6, rent=[1000, 100, 100, 100, -1000, 100])     # 4 months back: outside lookback 3
    assert of(fs3, "NEGATIVE_VALUE")[0].observed["reversal_of"] is None
    fs4, _, _ = run(tmp_path / "d", 6, rent=[1000, 100, 100, -1000, 100, 100])     # exactly 3 months back
    assert of(fs4, "NEGATIVE_VALUE")[0].observed["reversal_of"] == "2025-01"


# ================= several flags on one account and period =================

def test_two_flag_types_on_the_same_account_and_period(tmp_path):
    fs, _, _ = run(tmp_path, 9, rent=[200] * 8 + [-6000])
    same = [f for f in fs.flags if f.subject == "Rent" and f.period == "2025-09"]
    assert [f.type for f in same] == ["NEGATIVE_VALUE", "ONE_OFF_SPIKE"]          # sorted by type
    assert len({f.id for f in same}) == 2


# ================= gate, ordering, ids, compactness =================

def test_flags_need_ok_reconciliation(tmp_path):
    c = mini.build(tmp_path, months(6), [100000] * 6, [40000] * 6, [100] * 6, [50] * 6)
    m = compute_metrics(c.mapped, c.recon, c.company)
    bad = mini.build(tmp_path / "bad", months(6), [100000] * 6, [40000] * 6, [100] * 6, [50] * 6,
                     sales_leaf=[100000, 100001, 100000, 100000, 100000, 100000])
    assert bad.recon.status == "BLOCKED"
    with pytest.raises(BlockedError):
        compute_flags(bad.mapped, bad.recon, m, c.company)


def test_output_is_sorted_and_identical_across_runs_and_input_order(tmp_path):
    kw = dict(rent=[1000, 100, -1000, 100, 100, 100], mkt=[50, -80, 50, 50, 50, 50],
              sales=[100000, 100000, 100000, 140000, 140000, 100000], expected_negative=())
    a, _, _ = run(tmp_path / "a", 6, **kw)
    b, _, _ = run(tmp_path / "b", 6, shuffle=True, **kw)
    assert a.to_dict() == b.to_dict() and [f.id for f in a.flags] == [f.id for f in b.flags]
    keys = [(f.period, f.type, f.subject, f.basis) for f in a.flags]
    assert keys == sorted(keys) and len(a.flags) >= 3


def test_ids_are_short_stable_and_unique(tmp_path):
    fs, _, _ = run(tmp_path, 9, rent=[200] * 8 + [-6000], sales=[100000] * 8 + [140000])
    ids = [f.id for f in fs.flags]
    assert len(ids) == len(set(ids)) > 3
    assert all(re.fullmatch(r"[A-Z]+-\d{4}-\d{2}-[0-9a-f]{6}", i) for i in ids)
    again, _, _ = run(tmp_path / "again", 9, rent=[200] * 8 + [-6000], sales=[100000] * 8 + [140000])
    assert ids == [f.id for f in again.flags]


def test_compact_dict_has_counts_for_every_type_and_is_json_ready(tmp_path):
    fs, _, _ = run(tmp_path, 9, rent=[200] * 8 + [-6000])
    d = fs.to_dict()
    assert set(d["counts"]) == set(TYPES) and d["counts"]["NEGATIVE_VALUE"] == 1 and d["counts"]["ONE_OFF_SPIKE"] == 1
    assert d["counts"]["BLOCKED"] == 0 and sum(d["counts"].values()) == len(d["flags"])
    assert set(d["by_severity"]) == {"blocking", "high", "medium", "low"}
    assert d["thresholds"]["mom_min_abs"] == "5000.00"
    json.dumps(d)                                                      # serialisable as is
    f = d["flags"][0]
    assert set(f) == {"id", "type", "severity", "basis", "period", "subject", "observed", "threshold",
                      "reason", "fact_ids", "source_refs"}
    spike = next(x for x in d["flags"] if x["type"] == "ONE_OFF_SPIKE")
    assert spike["observed"]["value"] == "-6000.00"                    # rounded to cents only at presentation


def test_same_finding_is_not_duplicated_across_bases(tmp_path):
    fs, _, _ = run(tmp_path, 9, rent=[200] * 8 + [-6000], sales=[100000] * 8 + [140000])
    keys = [(f.type, f.subject, f.period) for f in fs.flags]
    assert len(keys) == len(set(keys))                                  # no adjustments: one flag, basis "both"
    assert {f.basis for f in fs.flags} == {"both"}


def test_every_flag_has_evidence_and_unresolvable_evidence_is_a_bug(tmp_path):
    fs, m, _ = run(tmp_path, 9, rent=[200] * 8 + [-6000], sales=[100000] * 8 + [140000], adjustments=ADJ_RENT)
    for f in fs.flags:
        assert f.reason.strip() and f.observed not in (None, "") and f.threshold not in (None, "")
        assert f.fact_ids or f.source_refs
        assert all(i in m.facts for i in f.fact_ids)
    for bad in (dict(reason=""), dict(fact_ids=(), source_refs=()), dict(observed=None), dict(threshold=None),
                dict(type="NOPE"), dict(severity="high"), dict(basis="neither"), dict(subject="")):
        kw = dict(id="NEG-2025-03-aaaaaa", type="NEGATIVE_VALUE", severity="low", basis="both", period="2025-03",
                  subject="Rent", observed={"amount": D("-1")}, threshold={"min_abs_amount": D("0")},
                  reason="x", fact_ids=("rep.rev.total.2025-03",), source_refs=())
        kw.update(bad)
        with pytest.raises(ValueError):
            Flag(**kw)


def test_flagset_type(tmp_path):
    fs, _, _ = run(tmp_path, 6)
    assert isinstance(fs, FlagSet) and fs.flags == [] and fs.to_dict()["flags"] == []


# ================= BLOCKED =================

def test_blocked_flag_from_reconciliation_failure(tmp_path):
    bad = mini.build(tmp_path, months(6), [100000] * 6, [40000] * 6, [100] * 6, [50] * 6,
                     sales_leaf=[100000, 100001, 100000, 100000, 100000, 100000])
    with pytest.raises(BlockedError) as e:
        compute_metrics(bad.mapped, bad.recon, bad.company)
    f = blocked_flag(e.value)
    assert (f.type, f.severity, f.basis) == ("BLOCKED", "blocking", "n/a")
    assert f.observed == "RECON_FAIL" and f.source_refs and f.reason
    assert f.subject == "reconciliation"


@pytest.mark.parametrize("exc,code", [
    (AdapterError("UNDECLARED_TOTAL", "x", "Income Statement!B9"), "UNDECLARED_TOTAL"),
    (AdapterError("NON_NUMERIC", "x", "Income Statement!C9"), "NON_NUMERIC"),
    (AdapterError("NO_CACHED_VALUES", "x", "Income Statement!C9"), "NO_CACHED_VALUES"),
    (AdapterError("DUPLICATE_PERIOD", "x", "Income Statement!C5"), "DUPLICATE_PERIOD"),
    (AdapterError("UNCLASSIFIED_ROW", "x", "Income Statement!B9"), "UNCLASSIFIED_ROW"),
    (MappingError("UNMAPPED_ACCOUNT", "x"), "UNMAPPED_ACCOUNT"),
    (MappingError("UNAPPROVED_MAPPING", "x"), "UNAPPROVED_MAPPING"),
    (MappingError("UNKNOWN_CATEGORY", "x"), "UNKNOWN_CATEGORY"),
    (MetricsError("RECON_INCOMPLETE", "x"), "RECON_INCOMPLETE"),
])
def test_blocked_flag_for_each_blocking_reason(exc, code):
    f = blocked_flag(exc)
    assert f.type == "BLOCKED" and f.observed == code and f.reason and f.period == "n/a"


def test_blocked_flag_rejects_non_pipeline_errors():
    with pytest.raises(TypeError):
        blocked_flag(ValueError("nope"))
