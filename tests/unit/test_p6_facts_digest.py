"""Stage A: fact ids / display strings, the JSON writers, and the digest."""
import json
import re
from decimal import Decimal

import pytest

from tests.fixtures import mini
from tests.fixtures.mini import months
from trend.digest import SIZE_BUDGET_BYTES, TOP_N, build_digest
from trend.facts import (display, month_name, observed_text, parse_fact_id, to_cell, to_text, unit_of)
from trend.flags import compute_flags
from trend.metrics import NA, compute_metrics
from trend.writers.json_out import dumps

D = Decimal
STATED_BUDGET = 60_000        # bytes. The ABC digest must stay below this; see trend/digest.py for the reasoning.
ID_RE = re.compile(r"^((rep|adj|delta)\.[a-z0-9_.]+\.\d{4}-\d{2}|flags?\.[A-Za-z0-9_.\-]+)$")
TOKEN_RE = re.compile(r"\{\{([^{}]+)\}\}")
MONTH_YEAR = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}\b")


# ---------------------------------------------------------------- facts.py

@pytest.mark.parametrize("fid,basis,metric,dim,view,period", [
    ("rep.rev.total.2026-06", "rep", "rev", "total", "", "2026-06"),
    ("adj.opinc.tw12.2026-06", "adj", "opinc", "", "tw12", "2026-06"),
    ("delta.opinc.2025-12", "delta", "opinc", "", "", "2025-12"),
    ("rep.opex.nat.payroll_benefits.pct_rev.2026-06", "rep", "opex.nat", "payroll_benefits", "pct_rev", "2026-06"),
    ("rep.opex.line.marketing_advertising_ad_spend.mom_pct.2025-03", "rep", "opex.line",
     "marketing_advertising_ad_spend", "mom_pct", "2025-03"),
    ("rep.rev.stream.platform_revenue.tw3_prev_abs.2026-06", "rep", "rev.stream", "platform_revenue", "tw3_prev_abs", "2026-06"),
    ("rep.gm_pct.mom_pp.2026-06", "rep", "gm_pct", "", "mom_pp", "2026-06"),
    ("rep.rev.mix.usage_fees.2026-06", "rep", "rev.mix", "usage_fees", "", "2026-06"),
    ("rep.cogs.total.2026-06", "rep", "cogs", "total", "", "2026-06"),
    ("adj.opex.fn.g_and_a.tw12_pct_rev.2026-06", "adj", "opex.fn", "g_and_a", "tw12_pct_rev", "2026-06"),
])
def test_parse_fact_id(fid, basis, metric, dim, view, period):
    p = parse_fact_id(fid)
    assert (p["basis"], p["metric"], p["dimension"], p["view"], p["period"]) == (basis, metric, dim, view, period)


@pytest.mark.parametrize("bad", ["rev.total.2026-06", "rep.rev.total", "rep.2026-06", "xyz.rev.total.2026-06",
                                 "rep.nonsense.2026-06", "rep.rev.total.mom_pct.extra.2026-06"])
def test_parse_fact_id_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_fact_id(bad)


def test_every_abc_fact_id_parses_and_roundtrips(abc_metrics):
    for fid in abc_metrics.facts:
        p = parse_fact_id(fid)
        assert p["period"] == fid[-7:] and p["basis"] == fid.split(".")[0]


@pytest.mark.parametrize("fid,unit", [
    ("rep.rev.total.2026-06", "usd"), ("rep.rev.total.mom_pct.2026-06", "pct"), ("rep.rev.total.mom_abs.2026-06", "usd"),
    ("rep.gm_pct.2026-06", "pct"), ("rep.gm_pct.mom_pp.2026-06", "pp"), ("rep.opex.total.pct_rev.2026-06", "pct"),
    ("rep.opex.total.pct_rev_mom_pp.2026-06", "pp"), ("rep.opinc.tw12.2026-06", "usd"),
    ("rep.opinc_margin_pct.tw12.2026-06", "pct"), ("rep.opex.total.tw12_pct_rev.2026-06", "pct"),
    ("rep.rev.total.tw12_mom_pct.2026-06", "pct"), ("rep.rev.total.tw12_prev_abs.2026-06", "usd"),
    ("delta.opinc.2025-12", "usd"), ("delta.gm_pct.2025-12", "pp"), ("delta.opex.total.pct_rev.2025-12", "pp"),
    ("rep.rev.mix.usage_fees.2026-06", "pct"),
])
def test_units(fid, unit):
    assert unit_of(parse_fact_id(fid)) == unit


def test_display_strings():
    assert display(D("195898.16"), "usd") == "195,898.16"
    assert display(D("-375217.9"), "usd") == "-375,217.90"
    assert display(D("0"), "usd") == "0.00"
    assert display(D("2.675"), "usd") == "2.68"                       # half-up, only here at presentation
    assert display(D("70.1515151515"), "pct") == "70.15%"
    assert display(D("-5.9792"), "pct") == "-5.98%"
    assert display(D("3.1101993"), "pp") == "3.11 pp"
    assert display(D("-6.0868"), "pp") == "-6.09 pp"
    assert display(NA("zero_base"), "pct") == "n/a"
    assert display(NA("insufficient_history", "x", ("2024-11", "2024-12")), "usd") == "insufficient data (missing: 2024-11, 2024-12)"


def test_to_text_and_to_cell():
    assert to_text(D("195898.16"), "usd") == "195898.16"
    assert to_text(D("70.123456789012345"), "pct") == "70.1234567890"       # 10 dp in files, 60 in memory
    assert to_text(NA("zero_base"), "pct") is None
    assert to_cell(D("195898.164"), "usd") == D("195898.16")
    assert to_cell(D("70.123456"), "pct") == D("70.1235")
    assert to_cell(NA("zero_base"), "pct") == "n/a"
    assert to_cell(NA("gap_in_window", "", ("2025-03",)), "usd").startswith("insufficient data")


def test_month_name_and_observed_text():
    assert month_name("2026-06") == "Jun 2026" and month_name("2025-12") == "Dec 2025"
    assert observed_text({"amount": D("-98.5"), "reversal_of": None}) == "amount=-98.50, reversal_of=none"
    assert observed_text("RECON_FAIL") == "RECON_FAIL"


# ---------------------------------------------------------------- the digest on ABC

@pytest.fixture(scope="module")
def meta():
    return {"input_sha256": "0" * 64, "config_sha256": "1" * 64}


@pytest.fixture(scope="module")
def digest(abc, abc_metrics, abc_flagset, meta):
    return build_digest(abc.company, abc_metrics, abc_flagset, abc.mapped, meta)


def walk(o, skip=()):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in skip:
                continue
            yield from walk(v, skip)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v, skip)
    else:
        yield o


def referenced_ids(d):
    ids = set()
    for s in walk(d, skip=("allowed_facts", "conventions", "token_format")):
        if isinstance(s, str):
            if ID_RE.match(s):
                ids.add(s)
            ids.update(TOKEN_RE.findall(s))
    return ids


def test_digest_size_budget(digest):
    size = len(dumps(digest).encode("utf-8"))
    assert SIZE_BUDGET_BYTES == STATED_BUDGET
    assert size <= STATED_BUDGET, f"digest is {size} bytes, budget {STATED_BUDGET}"
    assert len(digest["allowed_facts"]) < 1000           # a digest, not the 134k-fact table
    assert len(dumps(digest)) < len(dumps({"x": 1})) * 10 ** 5


def test_digest_lists_allowed_ids_and_every_reference_resolves(digest):
    allowed = list(digest["allowed_facts"])
    assert allowed == sorted(allowed) and len(allowed) == len(set(allowed))
    assert "facts" not in digest and "allowed_fact_ids" not in digest        # one map, not two copies of the ids
    refs = referenced_ids(digest)
    assert refs, "digest references no fact ids"
    assert refs <= set(allowed), sorted(refs - set(allowed))[:5]
    assert digest["token_format"] == "{{<fact id>}}"


def test_every_metric_fact_in_the_digest_matches_the_metrics(digest, abc_metrics):
    n = 0
    for fid, shown in digest["allowed_facts"].items():
        if fid.startswith(("rep.", "adj.", "delta.")):
            assert fid in abc_metrics.facts, fid
            assert shown == display(abc_metrics.facts[fid], unit_of(parse_fact_id(fid))), fid
            n += 1
    assert n > 100
    assert all(ID_RE.match(i) for i in digest["allowed_facts"])


def test_digest_headline_anchors(digest):
    f = digest["allowed_facts"]
    head = {r["label"]: r for r in digest["headline"]["rows"]}
    assert digest["as_of"] == "2026-06" and digest["headline"]["period"] == "2026-06"
    assert f[head["Revenue"]["rep"]["value"]] == "195,898.16"
    assert f[head["Revenue"]["rep"]["mom_pct"]] == "-5.98%"
    assert f[head["Gross margin"]["rep"]["value"]] == "70.15%"
    assert f[head["Total operating expenses as % of revenue"]["rep"]["value"]] == "238.99%"
    assert f[head["Total operating expenses"]["rep"]["value"]] == "468,181.80"


def test_digest_windows_on_both_bases(digest):
    (w,) = digest["windows"]
    assert (w["length"], w["end"]) == (12, "2026-06")
    rows = {r["label"]: r for r in w["rows"]}
    f = digest["allowed_facts"]
    assert f[rows["Revenue"]["rep"]["value"]] == "2,303,975.51"
    assert f[rows["Operating income"]["rep"]["value"]] == "-3,089,779.59"
    assert f[rows["Operating income"]["adj"]["value"]] == "-3,999,548.90"
    assert f[rows["Operating income"]["delta"]] == "-909,769.31"


def test_adjusted_basis_is_given_only_where_it_differs(digest):
    head = {r["label"]: r for r in digest["headline"]["rows"]}
    assert head["Revenue"]["adj"] == "same_as_reported"                 # nothing adjusted in Jun 2026
    rows = {r["label"]: r for r in digest["windows"][0]["rows"]}
    assert isinstance(rows["Operating income"]["adj"], dict)             # the 12 months include Dec 2025
    assert rows["Revenue"]["adj"] == "same_as_reported"


def test_dec_2025_reported_vs_adjusted_comparison(digest):
    comp = {c["period"]: c for c in digest["adjusted_periods"]}
    c = comp["2025-12"]
    f = digest["allowed_facts"]
    month = {r["label"]: r for r in c["month"]}
    assert f[month["Operating income"]["rep"]] == "534,551.41" and f[month["Operating income"]["adj"]] == "-375,217.90"
    assert f[month["Operating income"]["delta"]] == "-909,769.31"
    assert f[month["Operating margin"]["rep"]] == "281.30%" and f[month["Operating margin"]["adj"]] == "-197.45%"
    win = {r["label"]: r for r in c["window"]["rows"]}
    assert c["window"]["length"] == 12
    assert f[win["Operating income"]["rep"]] == "-3,415,098.97" and f[win["Operating income"]["adj"]] == "-4,324,868.28"
    assert {a["account"] for a in c["adjustments"]} == {"Income Tax Benefit", "Stock-Based Compensation Expense"}
    for a in c["adjustments"]:
        assert a["reason"] and set(a["ids"]) == {"reported", "adjusted", "delta"}


def test_opex_by_function_and_nature_with_percent_of_revenue(digest):
    f = digest["allowed_facts"]
    nat = {r["label"]: r for r in digest["opex_by_nature"]}
    fn = {r["label"]: r for r in digest["opex_by_function"]}
    assert len(fn) == 8 and len(nat) == 11
    pb = nat["Payroll & benefits"]
    assert f[pb["rep"]["value"]] == "328,377.23" and f[pb["rep"]["pct_rev"]] == "167.63%"
    assert f[fn["Sales & Marketing"]["rep"]["value"]] == "201,102.74"


def test_flag_counts_match_the_flag_set(digest, abc_flagset):
    f = digest["allowed_facts"]
    for t, n in abc_flagset.counts.items():
        assert f[digest["flags"]["counts"][t]] == str(n)
    assert f[digest["flags"]["total"]] == str(len(abc_flagset.flags))
    assert f[digest["flags"]["suppressed_negatives"]] == str(len(abc_flagset.suppressed_negatives))
    assert f[digest["flags"]["shown"]] == str(len(digest["flags"]["top"]))


def test_top_flags_are_merged_ranked_and_always_include_high_severity(digest, abc_flagset):
    top = digest["flags"]["top"]
    keys = [(e["subject"], e["period"]) for e in top]
    assert len(keys) == len(set(keys))                                     # one entry per (account, month)
    high = {(f.subject, f.period) for f in abc_flagset.flags if f.severity in ("high", "blocking")}
    assert high <= set(keys)
    assert len(top) <= max(TOP_N, len(high))
    sizes = [float(digest["allowed_facts"][e["size"]].replace(",", "")) for e in top]
    assert sizes == sorted(sizes, reverse=True) and sizes[0] > 1_000_000          # largest first (the tax benefit)
    listed = {fid for e in top for d in e["details"] for fid in [d["flag_id"]]}
    assert listed <= {f.id for f in abc_flagset.flags}


def test_spike_flags_keep_continued_months(digest, abc_flagset):
    spikes = [d for e in digest["flags"]["top"] for d in e["details"] if d["type"] == "ONE_OFF_SPIKE"]
    assert spikes
    for d in spikes:
        assert isinstance(d["continued_months"], int) and "continued_months" in d["facts"]
    by_id = {f.id: f for f in abc_flagset.flags}
    assert all(d["continued_months"] == by_id[d["flag_id"]].observed["continued_months"] for d in spikes)
    assert any(d["continued_months"] > 0 for d in spikes)                  # Bonus: continued 5 months
    assert all(d["direction"] in ("up", "down") for d in spikes)


def test_text_fields_contain_no_free_numbers(digest):
    texts = list(digest["data_notes"])
    for e in digest["flags"]["top"]:
        texts += [d["text"] for d in e["details"]]
    assert len(texts) > 10
    for t in texts:
        stripped = MONTH_YEAR.sub("", TOKEN_RE.sub("", t))
        assert not re.search(r"\d", stripped), t              # digits only inside {{tokens}} or as 'Mon YYYY'
        for tok in TOKEN_RE.findall(t):
            assert tok in digest["allowed_facts"], tok


def test_data_notes_are_deterministic_text_with_tokens(digest):
    notes = " ".join(digest["data_notes"])
    assert "interest" in notes.lower() and "{{flags.suppressed_negatives}}" in notes
    assert "Sales & Marketing - Workers Comp Insurance" in notes             # an all-zero line is named


def test_digest_is_deterministic_and_has_no_timestamps(abc, abc_metrics, abc_flagset, meta, digest):
    again = build_digest(abc.company, abc_metrics, abc_flagset, abc.mapped, meta)
    assert dumps(again) == dumps(digest)
    assert dumps(again).encode() == dumps(digest).encode()
    text = dumps(digest)
    assert not re.search(r"20\d\d-\d\d-\d\dT", text) and "timestamp" not in text.lower()


def test_digest_conventions_explain_units_and_tokens(digest):
    c = " ".join(digest["conventions"])
    assert "percent" in c.lower() and "{{" in c and "adjusted" in c.lower()


# ---------------------------------------------------------------- merging on a code-built fixture

def test_level_shift_and_spike_on_the_same_account_and_month_become_one_entry(tmp_path):
    n = 11
    c = mini.build(tmp_path, months(n), [100000] * 8 + [0, 0, 0], [40000] * n, [100] * n, [50] * n)
    m = compute_metrics(c.mapped, c.recon, c.company)
    fs = compute_flags(c.mapped, c.recon, m, c.company)
    types = sorted(f.type for f in fs.flags if f.subject == "Sales" and f.period == "2025-09")
    assert types == ["LEVEL_SHIFT", "ONE_OFF_SPIKE"]
    d = build_digest(c.company, m, fs, c.mapped, {"input_sha256": "x", "config_sha256": "y"})
    entries = [e for e in d["flags"]["top"] if (e["subject"], e["period"]) == ("Sales", "2025-09")]
    assert len(entries) == 1
    e = entries[0]
    assert e["types"] == ["LEVEL_SHIFT", "ONE_OFF_SPIKE"] and len(e["details"]) == 2
    assert {x["flag_id"] for x in e["details"]} == {f.id for f in fs.flags if f.subject == "Sales" and f.period == "2025-09"}
    assert e["severity"] == "medium"


def test_blocked_digest_is_impossible_without_metrics():
    with pytest.raises(TypeError):
        build_digest(None, None, None, None, {})
