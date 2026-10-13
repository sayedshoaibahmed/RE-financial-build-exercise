"""P4 against the real ABC workbook: user-verified anchors, PLAN anchors, and an
independent cross-check of every metric against tests/oracle.py (exact Fractions)."""
from decimal import Decimal
from fractions import Fraction

import pytest

from tests import oracle
from trend.metrics import NA, compute_metrics, round_cents, round_pct

D = Decimal
TINY = Fraction(1, 10 ** 40)


@pytest.fixture(scope="module")
def m(abc):
    return compute_metrics(abc.mapped, abc.recon, abc.company, window_lengths=(3, 6, 12))


@pytest.fixture(scope="module")
def m6_mar(abc):
    return compute_metrics(abc.mapped, abc.recon, abc.company, window_lengths=(6,), window_end="2026-03")


def cents(m, fid):
    return round_cents(m.facts[fid])


def pct(m, fid):
    return round_pct(m.facts[fid], 2)


class TestUserVerifiedAnchors:
    def test_revenue_jun_and_may_2026(self, m):
        assert cents(m, "rep.rev.total.2026-06") == D("195898.16")
        assert cents(m, "rep.rev.total.2026-05") == D("208350.96")

    def test_revenue_mom(self, m):
        assert pct(m, "rep.rev.total.mom_pct.2026-06") == D("-5.98")

    def test_gross_margin(self, m):
        assert pct(m, "rep.gm_pct.2026-06") == D("70.15")
        assert pct(m, "rep.gm_pct.2026-05") == D("67.04")

    def test_ttm_revenue(self, m):
        assert cents(m, "rep.rev.total.tw12.2025-12") == D("2145316.28")
        assert cents(m, "rep.rev.total.tw12.2026-06") == D("2303975.51")

    def test_dec_2025_operating_income(self, m):
        assert cents(m, "rep.opinc.2025-12") == D("534551.41")
        assert cents(m, "adj.opinc.2025-12") == D("-375217.90")
        assert cents(m, "delta.opinc.2025-12") == D("-909769.31")

    def test_dec_2025_operating_margin(self, m):
        assert pct(m, "rep.opinc_margin_pct.2025-12") == D("281.30")
        assert pct(m, "adj.opinc_margin_pct.2025-12") == D("-197.45")

    def test_ttm_operating_income_ending_dec_2025(self, m):
        assert cents(m, "rep.opinc.tw12.2025-12") == D("-3415098.97")
        assert cents(m, "adj.opinc.tw12.2025-12") == D("-4324868.28")

    def test_ttm_operating_income_ending_jun_2026(self, m):
        assert cents(m, "rep.opinc.tw12.2026-06") == D("-3089779.59")
        assert cents(m, "adj.opinc.tw12.2026-06") == D("-3999548.90")

    def test_six_month_window_ending_mar_2026(self, m6_mar):
        assert cents(m6_mar, "rep.rev.total.tw6.2026-03") == D("1148039.20")
        assert cents(m6_mar, "rep.opinc.tw6.2026-03") == D("-1095637.76")
        assert cents(m6_mar, "adj.opinc.tw6.2026-03") == D("-2005407.07")

    def test_six_month_window_has_nothing_after_its_end(self, m6_mar):
        for p in ("2026-04", "2026-05", "2026-06"):
            assert f"rep.rev.total.tw6.{p}" not in m6_mar.facts
            assert f"adj.opinc.tw6.{p}" not in m6_mar.facts

    def test_opex_pct_of_revenue_jun_2026(self, m):
        assert pct(m, "rep.opex.total.pct_rev.2026-06") == D("238.99")
        assert cents(m, "rep.opex.total.2026-06") == D("468181.80")


class TestFunctionAndNatureTieToOpex:
    @pytest.mark.parametrize("basis", ["rep", "adj"])
    @pytest.mark.parametrize("dim", ["fn", "nat"])
    def test_every_month(self, m, abc, basis, dim):
        for p in abc.o["periods"]:
            parts = [v for k, v in m.facts.items()
                     if k.startswith(f"{basis}.opex.{dim}.") and k.endswith("." + p)
                     and k.count(".") == 4]   # basis.opex.dim.category.period
            assert parts and sum(parts) == m.facts[f"{basis}.opex.total.{p}"]

    def test_reconciliation_includes_c9(self, abc):
        ids = {r.control_id for r in abc.recon.results}
        assert {"C9a", "C9b"} <= ids and abc.recon.status == "OK"


class TestPlanAnchorsNotInTheUserList:
    """From PLAN 4.2-4.3 (TESTED offline in the plan, not re-verified by hand)."""

    def test_dec_2025_opex_both_bases(self, m):
        assert cents(m, "rep.opex.total.2025-12") == D("-402142.09")
        assert cents(m, "adj.opex.total.2025-12") == D("507627.22")

    def test_ttm_opex_and_margins(self, m):
        assert cents(m, "rep.opex.total.tw12.2025-12") == D("4936683.00")
        assert cents(m, "adj.opex.total.tw12.2025-12") == D("5846452.31")
        assert pct(m, "rep.opinc_margin_pct.tw12.2025-12") == D("-159.19")
        assert pct(m, "adj.opinc_margin_pct.tw12.2025-12") == D("-201.60")
        assert cents(m, "rep.opex.total.tw12.2026-06") == D("4690207.50")
        assert cents(m, "adj.opex.total.tw12.2026-06") == D("5599976.81")
        assert pct(m, "rep.opinc_margin_pct.tw12.2026-06") == D("-134.11")
        assert pct(m, "adj.opinc_margin_pct.tw12.2026-06") == D("-173.59")

    def test_ttm_gross_margin(self, m):
        assert pct(m, "rep.gm_pct.tw12.2025-12") == D("70.93")
        assert pct(m, "rep.gm_pct.tw12.2026-06") == D("69.46")

    def test_ttm_is_unavailable_before_dec_2025(self, m):
        for p in ["2025-%02d" % i for i in range(1, 12)]:
            na = m.facts[f"rep.rev.total.tw12.{p}"]
            assert isinstance(na, NA) and na.reason == "insufficient_history"
        na = m.facts["rep.rev.total.tw12.2025-11"]
        assert na.missing == ("2024-12",) and "insufficient data" in str(na)

    def test_three_and_six_month_preceding_windows(self, m):
        assert cents(m, "rep.rev.total.tw3.2026-06") == D("608761.54")
        assert cents(m, "rep.rev.total.tw3.2026-03") == D("578782.45")
        assert cents(m, "rep.rev.total.tw6.2025-09") == D("1061270.19")

    def test_platform_revenue_dec_to_jan(self, m):
        assert cents(m, "rep.rev.stream.platform_revenue.2025-12") == D("45000.00")
        assert cents(m, "rep.rev.stream.platform_revenue.2026-01") == D("26666.66")
        assert pct(m, "rep.rev.stream.platform_revenue.mom_pct.2026-01") == D("-40.74")

    def test_payroll_benefits_jun_2026(self, m):
        assert cents(m, "rep.opex.nat.payroll_benefits.2026-06") == D("328377.23")

    def test_adjusted_nonop_category_dec_2025(self, m):
        assert cents(m, "adj.opex.fn.non_operating_tax.2025-12") == D("-6023.37")
        assert cents(m, "adj.opex.nat.stock_based_comp.2025-12") == D("0.00")
        assert cents(m, "rep.opex.fn.stock_based_comp.2025-12") == D("334660.53")

    def test_adjustments_applied_come_from_company_yaml(self, m):
        got = {(a.account, a.period): a.delta for a in m.adjustments}
        assert got == {("Income Tax Benefit", "2025-12"): D("1244429.84"),
                       ("Stock-Based Compensation Expense", "2025-12"): D("-334660.53")}

    def test_windows_without_an_adjusted_period_are_identical_on_both_bases(self, m):
        assert m.facts["adj.opinc.tw12.2026-06"] != m.facts["rep.opinc.tw12.2026-06"]   # 12m to Jun 26 contains Dec 25
        assert m.facts["adj.opinc.tw3.2026-06"] == m.facts["rep.opinc.tw3.2026-06"]
        assert m.facts["delta.opinc.tw3.2026-06"] == 0


@pytest.fixture(scope="module")
def ref(abc):
    zeroed = {(a.account, p) for a in abc.company.adjustments for p in a.periods}
    mp = abc.dir / "mapping.yaml"
    return {
        "rep": (oracle.monthly(abc.o), oracle.by_category(abc.o, mp, "function"),
                oracle.by_category(abc.o, mp, "nature")),
        "adj": (oracle.monthly(abc.o, zeroed), oracle.by_category(abc.o, mp, "function", zeroed),
                oracle.by_category(abc.o, mp, "nature", zeroed)),
    }


class TestAgainstIndependentOracle:
    """Every metric, every period, both bases, compared with exact Fraction arithmetic."""

    @staticmethod
    def same(got, want):
        if want is None:
            assert isinstance(got, NA)
        else:
            assert isinstance(got, Decimal) and abs(Fraction(got) - want) < TINY, (got, want)

    @pytest.mark.parametrize("basis", ["rep", "adj"])
    def test_monthly_amounts_and_ratios(self, m, abc, ref, basis):
        mo, fn, nat = ref[basis]
        for p in abc.o["periods"]:
            for key, fid in (("rev", "rev.total"), ("cogs", "cogs.total"), ("gp", "gp"),
                             ("opex", "opex.total"), ("opinc", "opinc")):
                self.same(m.facts[f"{basis}.{fid}.{p}"], mo[p][key])
            self.same(m.facts[f"{basis}.net_income.{p}"], mo[p]["opinc"])
            self.same(m.facts[f"{basis}.gm_pct.{p}"], oracle.pct(mo[p]["gp"], mo[p]["rev"]))
            self.same(m.facts[f"{basis}.opinc_margin_pct.{p}"], oracle.pct(mo[p]["opinc"], mo[p]["rev"]))
            self.same(m.facts[f"{basis}.opex.total.pct_rev.{p}"], oracle.pct(mo[p]["opex"], mo[p]["rev"]))
            for cat, v in fn[p].items():
                self.same(m.facts[f"{basis}.opex.fn.{cat}.{p}"], v)
                self.same(m.facts[f"{basis}.opex.fn.{cat}.pct_rev.{p}"], oracle.pct(v, mo[p]["rev"]))
            for cat, v in nat[p].items():
                self.same(m.facts[f"{basis}.opex.nat.{cat}.{p}"], v)
                self.same(m.facts[f"{basis}.opex.nat.{cat}.pct_rev.{p}"], oracle.pct(v, mo[p]["rev"]))

    @pytest.mark.parametrize("basis", ["rep", "adj"])
    @pytest.mark.parametrize("n", [3, 6, 12])
    def test_windows_for_every_period(self, m, abc, ref, basis, n):
        mo, fn, nat = ref[basis]
        series = {k: {p: mo[p][k] for p in mo} for k in ("rev", "gp", "opex", "opinc")}
        for p in abc.o["periods"]:
            w = {k: oracle.window(s, p, n) for k, s in series.items()}
            self.same(m.facts[f"{basis}.rev.total.tw{n}.{p}"], w["rev"])
            self.same(m.facts[f"{basis}.opinc.tw{n}.{p}"], w["opinc"])
            self.same(m.facts[f"{basis}.opex.total.tw{n}.{p}"], w["opex"])
            ok = w["rev"] is not None
            self.same(m.facts[f"{basis}.gm_pct.tw{n}.{p}"], oracle.pct(w["gp"], w["rev"]) if ok else None)
            self.same(m.facts[f"{basis}.opinc_margin_pct.tw{n}.{p}"], oracle.pct(w["opinc"], w["rev"]) if ok else None)
            self.same(m.facts[f"{basis}.opex.total.tw{n}_pct_rev.{p}"], oracle.pct(w["opex"], w["rev"]) if ok else None)

    @pytest.mark.parametrize("basis", ["rep", "adj"])
    def test_mom(self, m, abc, ref, basis):
        mo, _, _ = ref[basis]
        ps = abc.o["periods"]
        asc = sorted(ps)
        for i, p in enumerate(asc):
            if i == 0:
                assert isinstance(m.facts[f"{basis}.rev.total.mom_pct.{p}"], NA)
                continue
            prev = asc[i - 1]
            for key, fid in (("rev", "rev.total"), ("opex", "opex.total"), ("opinc", "opinc")):
                cur, pr = mo[p][key], mo[prev][key]
                self.same(m.facts[f"{basis}.{fid}.mom_abs.{p}"], cur - pr)
                self.same(m.facts[f"{basis}.{fid}.mom_pct.{p}"], None if pr == 0 else (cur - pr) / abs(pr) * 100)
            g1, g0 = oracle.pct(mo[p]["gp"], mo[p]["rev"]), oracle.pct(mo[prev]["gp"], mo[prev]["rev"])
            self.same(m.facts[f"{basis}.gm_pct.mom_pp.{p}"], g1 - g0)

    def test_deltas_are_adjusted_minus_reported(self, m, abc):
        n = 0
        for fid, v in m.facts.items():
            if fid.startswith("delta."):
                rest = fid[len("delta."):]
                a, r = m.facts["adj." + rest], m.facts["rep." + rest]
                assert isinstance(a, Decimal) and isinstance(r, Decimal)
                assert abs(Fraction(v) - (Fraction(a) - Fraction(r))) < TINY, fid
                n += 1
        assert n > 1000

    def test_periods_are_chronological_whatever_the_sheet_order(self, m):
        assert list(m.periods) == sorted(m.periods) and m.periods[0] == "2025-01" and m.periods[-1] == "2026-06"
        assert (m.window_lengths, m.window_end) == ((3, 6, 12), "2026-06")


class TestDefaultsFromCompanyYaml:
    def test_default_run_is_a_12_month_window_ending_at_the_latest_period(self, abc):
        d = compute_metrics(abc.mapped, abc.recon, abc.company)
        assert (d.window_lengths, d.window_end) == ((12,), "2026-06")
        assert round_cents(d.facts["rep.opinc.tw12.2026-06"]) == D("-3089779.59")
        assert "rep.opinc.tw6.2026-06" not in d.facts

    def test_dec_2025_as_of_run(self, abc):
        d = compute_metrics(abc.mapped, abc.recon, abc.company, as_of="2025-12")
        assert d.window_end == "2025-12" and d.periods[-1] == "2025-12"
        assert round_cents(d.facts["rep.opinc.tw12.2025-12"]) == D("-3415098.97")
        assert round_cents(d.facts["delta.opinc.tw12.2025-12"]) == D("-909769.31")
        assert not [f for f in d.facts if "2026-" in f]
