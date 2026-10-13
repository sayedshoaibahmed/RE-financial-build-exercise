"""P5 against the real ABC workbook (PLAN section 5 expectations) and an independent oracle."""
import dataclasses
import json
from decimal import Decimal
from fractions import Fraction

import pytest

from tests import oracle
from trend.flags import TYPES, compute_flags
from trend.metrics import compute_metrics

D = Decimal


@pytest.fixture(scope="module")
def metrics(abc):
    return compute_metrics(abc.mapped, abc.recon, abc.company)


@pytest.fixture(scope="module")
def fs(abc, metrics):
    return compute_flags(abc.mapped, abc.recon, metrics, abc.company)


def of(fs, typ):
    return [f for f in fs.flags if f.type == typ]


def pairs(fs, typ):
    return {(f.subject, f.period) for f in of(fs, typ)}


class TestPlanSection5:
    def test_no_blocking_or_missing_month_flags(self, fs):
        assert fs.counts["BLOCKED"] == 0 and fs.counts["MISSING_MONTH"] == 0

    def test_two_nonrecurring_flags_in_dec_2025_on_both_bases(self, fs):
        got = of(fs, "NONRECURRING")
        assert {(f.subject, f.period, f.basis, f.severity) for f in got} == {
            ("Income Tax Benefit", "2025-12", "both", "high"),
            ("Stock-Based Compensation Expense", "2025-12", "both", "high")}
        by = {f.subject: f for f in got}
        assert by["Income Tax Benefit"].observed == {"reported": D("-1244429.84"), "adjusted": D("0.00")}
        assert by["Stock-Based Compensation Expense"].observed == {"reported": D("334660.53"), "adjusted": D("0.00")}
        for f in got:   # reported, adjusted and delta facts are all cited
            kinds = {i.split(".")[0] for i in f.fact_ids}
            assert kinds == {"rep", "adj", "delta"}

    def test_level_shift_platform_revenue_from_jan_2026(self, fs):
        assert ("Platform Revenue", "2026-01") in pairs(fs, "LEVEL_SHIFT")
        f = next(f for f in of(fs, "LEVEL_SHIFT") if f.subject == "Platform Revenue")
        assert f.observed["before_avg"] == D("45000") and f.observed["after_avg"] == D("26666.66")
        assert round(f.observed["change_pct"], 2) == D("-40.74")

    def test_one_off_spike_sku_profiling_apr_2026_first_nonzero(self, fs):
        f = next(f for f in of(fs, "ONE_OFF_SPIKE") if f.subject == "SKU Profiling")
        assert f.period == "2026-04" and f.observed["value"] == D("10000.00") and f.observed["median"] == 0

    def test_negative_value_list_matches_plan(self, fs):
        assert pairs(fs, "NEGATIVE_VALUE") == {
            ("White-Label", "2026-03"), ("Sales Commission", "2026-03"), ("Bad Debts", "2026-06"),
            ("Events & Conferences", "2025-04"), ("Events & Conferences", "2026-03"), ("Rent", "2025-02"),
            ("Federal & State Taxes", "2026-03"), ("Referral Fees", "2025-04"), ("Partnership Income", "2026-03")}

    def test_partnership_income_mar_2026_is_a_reversal_of_jan_2026(self, fs):
        f = next(f for f in of(fs, "NEGATIVE_VALUE") if f.subject == "Partnership Income")
        assert f.observed == {"amount": D("-1608.09"), "reversal_of": "2026-01"}
        assert "possible reversal of 2026-01" in f.reason


class TestBankServiceChargesCent:
    def test_not_flagged_at_min_abs_1_and_listed_as_suppressed(self, fs):
        assert ("Bank Service Charges", "2025-04") not in pairs(fs, "NEGATIVE_VALUE")
        assert {"account": "Bank Service Charges", "period": "2025-04", "amount": D("-0.01")} in fs.suppressed_negatives

    def test_flagged_when_min_abs_is_zero(self, abc, metrics):
        company = dataclasses.replace(abc.company, thresholds=dataclasses.replace(
            abc.company.thresholds, negative_min_abs_amount=D("0")))
        got = compute_flags(abc.mapped, abc.recon, metrics, company)
        assert ("Bank Service Charges", "2025-04") in pairs(got, "NEGATIVE_VALUE")
        assert len(of(got, "NEGATIVE_VALUE")) == 10 and got.suppressed_negatives == []

    def test_the_cent_stays_in_the_totals(self, metrics, abc):
        line = next(m.record.amount for m in abc.mapped
                    if m.record.account == "Bank Service Charges" and m.record.period == "2025-04")
        assert line == D("-0.01")
        assert metrics.facts["rep.opex.line.bank_service_charges.2025-04"] == D("-0.01")

    def test_boundary_rule_documented_in_the_module(self):
        import trend.flags as f
        assert "below the minimum is suppressed" in f.__doc__.lower() and "equal" in f.__doc__.lower()


class TestExpectedNegativeAndDec2025:
    def test_interest_income_negative_in_every_month_is_never_flagged(self, fs):
        assert not [f for f in fs.flags if f.subject == "Interest Income"]

    def test_income_tax_benefit_is_negative_but_exempt(self, fs):
        assert [f.type for f in fs.flags if f.subject == "Income Tax Benefit"] == ["NONRECURRING"]

    def test_stock_comp_dec_2025_gets_only_nonrecurring(self, fs):
        assert [f.type for f in fs.flags if f.subject == "Stock-Based Compensation Expense"] == ["NONRECURRING"]


class TestEvidenceAndShape:
    def test_every_flag_has_evidence_that_resolves(self, fs, metrics):
        assert fs.flags
        for f in fs.flags:
            assert f.reason.strip() and f.observed not in (None, "") and f.threshold not in (None, "")
            assert f.fact_ids or f.source_refs, f.id
            assert all(i in metrics.facts for i in f.fact_ids), f.id
            assert f.basis in {"reported", "adjusted", "both"}

    def test_sorted_ids_unique_and_json_ready(self, fs):
        keys = [(f.period, f.type, f.subject, f.basis) for f in fs.flags]
        assert keys == sorted(keys)
        assert len({f.id for f in fs.flags}) == len(fs.flags)
        d = fs.to_dict()
        assert set(d["counts"]) == set(TYPES) and sum(d["counts"].values()) == len(d["flags"])
        json.dumps(d)

    def test_run_twice_gives_identical_output(self, abc, metrics, fs):
        again = compute_flags(abc.mapped, abc.recon, metrics, abc.company)
        assert again.to_dict() == fs.to_dict()

    def test_mom_move_is_only_for_the_latest_month_and_totals_cite_adjusted_facts(self, fs):
        mv = of(fs, "MOM_MOVE")
        assert {f.period for f in mv} <= {"2026-06"}
        for f in mv:
            if f.subject.startswith(("opex.", "gp", "rev.total", "opinc")) and f.basis != "reported":
                assert all(i.startswith("adj.") for i in f.fact_ids), f.id


class TestG12AndDec2025AsOf:
    def test_as_of_jan_2026_flags_the_move_but_not_a_platform_revenue_level_shift(self, abc):
        m = compute_metrics(abc.mapped, abc.recon, abc.company, as_of="2026-01")
        got = compute_flags(abc.mapped, abc.recon, m, abc.company)
        f = next(f for f in got.flags if f.type == "MOM_MOVE" and f.subject == "rev.stream.platform_revenue")
        assert f.period == "2026-01" and round(f.observed["pct"], 2) == D("-40.74")
        assert "Platform Revenue" not in {f.subject for f in of(got, "LEVEL_SHIFT")}    # PLAN G12: no lookahead

    def test_as_of_dec_2025_nonrecurring_both_bases_and_totals_on_adjusted(self, abc):
        m = compute_metrics(abc.mapped, abc.recon, abc.company, as_of="2025-12")
        got = compute_flags(abc.mapped, abc.recon, m, abc.company)
        assert {f.subject for f in of(got, "NONRECURRING")} == {"Income Tax Benefit", "Stock-Based Compensation Expense"}
        assert {f.basis for f in of(got, "NONRECURRING")} == {"both"}
        assert {f.period for f in got.flags if f.type == "MOM_MOVE"} <= {"2025-12"}
        for f in got.flags:
            assert all(i in m.facts for i in f.fact_ids)


@pytest.fixture(scope="module")
def zeroed(abc):
    return {(a.account, p) for a in abc.company.adjustments for p in a.periods}


class TestAgainstIndependentOracle:
    def test_negative_value_flags_equal_the_oracle(self, fs, abc):
        want = oracle.negatives(abc.o, abc.dir / "mapping.yaml", Fraction(1))
        got = {(f.subject, f.period): f.observed["amount"] for f in of(fs, "NEGATIVE_VALUE")}
        assert set(got) == set(want)
        assert all(Fraction(got[k]) == want[k] for k in want)

    def test_suppressed_negatives_are_exactly_the_sub_minimum_ones(self, fs, abc):
        all_neg = oracle.negatives(abc.o, abc.dir / "mapping.yaml", Fraction(0))
        big = oracle.negatives(abc.o, abc.dir / "mapping.yaml", Fraction(1))
        small = set(all_neg) - set(big)
        assert {(s["account"], s["period"]) for s in fs.suppressed_negatives} == small and small

    def test_spikes_equal_the_oracle(self, fs, abc, zeroed):
        assert pairs(fs, "ONE_OFF_SPIKE") == oracle.spikes(abc.o, zeroed)       # collapsed run starts only

    def test_spike_direction_and_continued_months_equal_the_oracle(self, fs, abc, zeroed):
        want = oracle.spikes_collapsed(abc.o, zeroed=zeroed)
        got = {(f.subject, f.period): (f.observed["direction"], f.observed["continued_months"])
               for f in of(fs, "ONE_OFF_SPIKE")}
        assert got == want

    def test_collapsed_runs_cover_exactly_the_uncollapsed_spikes(self, fs, abc, zeroed):
        raw = oracle.spikes_raw(abc.o, zeroed=zeroed)
        covered = {}
        for f in of(fs, "ONE_OFF_SPIKE"):
            p = f.period
            for k in range(f.observed["continued_months"] + 1):
                covered[(f.subject, oracle.shift(f.period, k))] = f.observed["direction"]
        assert covered == raw
        assert len(raw) == 52 and len(of(fs, "ONE_OFF_SPIKE")) < 52

    def test_no_account_has_two_spike_flags_in_consecutive_months_with_the_same_direction(self, fs):
        by = {}
        for f in of(fs, "ONE_OFF_SPIKE"):
            by.setdefault(f.subject, []).append((f.period, f.observed["direction"]))
        for acct, runs in by.items():
            for (p1, d1), (p2, d2) in zip(sorted(runs), sorted(runs)[1:]):
                assert not (p2 == oracle.shift(p1, 1) and d1 == d2), (acct, p1, p2)

    def test_repeated_spikes_named_in_the_request_are_collapsed(self, fs):
        pa = next(f for f in of(fs, "ONE_OFF_SPIKE") if f.subject == "Partnerships - Payroll")
        assert (pa.period, pa.observed["direction"]) == ("2026-01", "down") and pa.observed["continued_months"] == 5
        bonus = [f for f in of(fs, "ONE_OFF_SPIKE") if f.subject == "Bonus"]
        assert bonus[0].period == "2025-12" and bonus[0].observed["direction"] == "up"
        assert bonus[0].observed["continued_months"] == 5                      # Dec 2025 .. May 2026

    PLAN_SPIKE_CANDIDATES = [("Bonus", "2025-12"), ("Professional Services", "2026-01"),
                             ("Events & Conferences", "2026-02"), ("Bad Debts", "2026-05"),
                             ("General Expenses", "2026-01"), ("Sales - Payroll Taxes", "2026-01"),
                             ("SKU Profiling", "2026-04")]

    @pytest.mark.parametrize("subject,period", PLAN_SPIKE_CANDIDATES)
    def test_each_plan_spike_candidate_is_still_flagged(self, fs, subject, period):
        assert (subject, period) in pairs(fs, "ONE_OFF_SPIKE")

    def test_level_shifts_equal_the_oracle(self, fs, abc, zeroed):
        floor = Fraction(abc.company.thresholds.level_shift_min_abs)
        assert floor == 2500
        assert pairs(fs, "LEVEL_SHIFT") == oracle.level_shifts(abc.o, zeroed, min_abs=floor)

    def test_level_shifts_without_a_floor_still_equal_the_oracle(self, abc, metrics, zeroed):
        # the PLAN-literal rule (floor 0) is kept under test: 52 shifts, same as the independent oracle
        company = dataclasses.replace(abc.company, thresholds=dataclasses.replace(
            abc.company.thresholds, level_shift_min_abs=D("0")))
        got = compute_flags(abc.mapped, abc.recon, metrics, company)
        assert pairs(got, "LEVEL_SHIFT") == oracle.level_shifts(abc.o, zeroed, min_abs=0)
        assert len(of(got, "LEVEL_SHIFT")) == 52

    def test_the_floor_removes_shifts_but_keeps_platform_revenue(self, fs):
        assert len(of(fs, "LEVEL_SHIFT")) == 18
        assert ("Platform Revenue", "2026-01") in pairs(fs, "LEVEL_SHIFT")
        floor = D("2500")
        for f in of(fs, "LEVEL_SHIFT"):      # every surviving shift moves the monthly average by >= the floor
            assert abs(f.observed["after_avg"] - f.observed["before_avg"]) >= floor, f.subject

    def test_counts_by_type(self, fs):
        c = fs.counts
        assert c["NONRECURRING"] == 2 and c["NEGATIVE_VALUE"] == 9 and c["BLOCKED"] == 0 and c["MISSING_MONTH"] == 0
        assert c["LEVEL_SHIFT"] == 18            # 52 at floor 0; level_shift_min_abs = 2500 in company.yaml
        assert c["ONE_OFF_SPIKE"] >= 1 and c["MOM_MOVE"] == 6
