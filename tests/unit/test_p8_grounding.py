"""P8: the deterministic grounding checker. The good draft passes; every bad draft is the good draft with one edit
and must fail for exactly one code."""
import inspect
import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures import mini
from tests.fixtures.mini import months
from trend import grounding
from trend.digest import build_digest
from trend.flags import compute_flags
from trend.grounding import CHECKS, Failure, check_draft, load_rules, tokens_in
from trend.metrics import compute_metrics

ROOT = Path(__file__).resolve().parents[2]
GOOD = (ROOT / "tests" / "fixtures" / "commentary" / "good_abc_2026-06.md").read_text(encoding="utf-8")
META = {"input_sha256": "0" * 64, "config_sha256": "1" * 64}


@pytest.fixture(scope="module")
def digest(abc, abc_metrics, abc_flagset):
    return build_digest(abc.company, abc_metrics, abc_flagset, abc.mapped, META)


def codes(text, digest):
    return sorted({f.code for f in check_draft(text, digest)})


def line_of(fragment, text=GOOD):
    """1-based line number of the one line of `text` that contains `fragment`."""
    hits = [n for n, ln in enumerate(text.split("\n"), start=1) if fragment in ln]
    assert len(hits) == 1, (fragment, hits)
    return hits[0]


def edit(old, new, text=GOOD):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


# ---------------------------------------------------------------- rules live in the yaml, not in the code

def test_rules_file_holds_the_word_lists():
    r = yaml.safe_load((ROOT / ".claude/skills/monthly-trend-analysis/reference/commentary_rules.yaml").read_text(encoding="utf-8"))
    for p in ("because", "due to", "caused by", "driven by", "as a result of", "attributable to", "thanks to", "owing to"):
        assert p in r["causal_phrases"]
    assert "hedges" not in r                                  # P8b: causal phrasing is banned outright, no hedge exception
    assert r["max_body_words"] == 250 and "will" in r["banned_words"]
    assert load_rules() == r


def test_no_word_list_is_hard_coded_in_the_checker():
    src = inspect.getsource(grounding)
    for w in ("because", "due to", "driven by", "attributable", "owing to", "worth confirming", "definitely", "increased"):
        assert w not in src, w


def test_every_check_has_a_stable_code_and_name():
    assert CHECKS["G1"][0] == "UNKNOWN_TOKEN" and CHECKS["G2"][0] == "BARE_NUMBER" and CHECKS["G3"][0] == "CAUSAL_CLAIM"
    assert CHECKS["G4"][0] == "DIRECTION_MISMATCH" and CHECKS["G5"][0] == "MISSING_COVERAGE"
    assert CHECKS["G6"][0] == "MISSING_DRAFT_BANNER" and CHECKS["G7"][0] == "MALFORMED_TOKEN"
    assert {"G8", "G9", "G10"} <= set(CHECKS)


# ---------------------------------------------------------------- the good draft

def test_good_draft_passes_every_check(digest):
    assert check_draft(GOOD, digest) == []


def test_good_draft_is_hedged_mentions_dec_2025_both_ways_and_has_no_free_numbers(digest):
    assert "likely non-recurring" in GOOD and "to be confirmed" in GOOD and "may be a reversal" in GOOD
    ids = tokens_in(GOOD)
    assert "rep.opinc.2025-12" in ids and "adj.opinc.2025-12" in ids and "delta.opinc.2025-12" in ids
    stripped = re.sub(r"\{\{[^}]*\}\}", "", GOOD)
    stripped = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}\b|\b12-month\b", "", stripped)
    assert not re.search(r"\d", stripped)
    assert all(i in digest["allowed_facts"] for i in ids)


def test_good_draft_body_is_within_the_word_limit():
    body = GOOD.split("## Data notes")[0].split("\n", 1)[1]
    words = [w for w in re.sub(r"\{\{[^}]*\}\}", "TOKEN", body).split() if re.search(r"\w", w)]
    assert 100 < len(words) <= 250, len(words)


# ---------------------------------------------------------------- one bad draft per check, each failing for exactly that code

def test_g1_unknown_token(digest):
    bad = edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.opex.total.pct_rev.2030-01}}")
    assert codes(bad, digest) == ["G1"]
    (f,) = check_draft(bad, digest)
    assert f.name == "UNKNOWN_TOKEN" and f.line == line_of("pct_rev.2026-06") and "2030-01" in f.text and "allowed" in f.message


def test_g1_a_token_for_a_real_metric_that_is_not_in_the_digest_is_unknown(digest):
    assert codes(edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.rev.total.2026-05}}"), digest) == ["G1"]


def test_g2_bare_number(digest):
    bad = edit("month on month.", "month on month by about 5 points.")
    assert codes(bad, digest) == ["G2"]
    (f,) = check_draft(bad, digest)
    assert f.name == "BARE_NUMBER" and f.text == "5" and f.line == line_of("Revenue moved")


def test_g2_dollar_sign(digest):
    bad = edit("Revenue was {{rep.rev.total.2026-06}}", "Revenue was ${{rep.rev.total.2026-06}}")
    assert codes(bad, digest) == ["G2"]
    assert check_draft(bad, digest)[0].text.startswith("$")


def test_g2_percent_sign(digest):
    assert codes(edit("{{rep.opex.total.pct_rev.2026-06}} of revenue", "{{rep.opex.total.pct_rev.2026-06}}% of revenue"), digest) == ["G2"]


def test_g2_numbers_inside_headings_fail(digest):
    assert codes(edit("# Monthly trend commentary, Jun 2026", "# Monthly trend commentary 7, Jun 2026"), digest) == ["G2"]
    # a numbered section heading also stops being the required heading, so it fails G10 as well
    assert codes(edit("## Revenue", "## 2. Revenue"), digest) == ["G10", "G2"]


def test_g2_allowed_years_and_window_lengths_pass_but_other_years_and_lengths_fail(digest):
    assert codes(edit("Jun 2026", "Jun 2026 and Dec 2025"), digest) == []                      # years in the digest's periods
    assert codes(edit("Jun 2026", "Jun 2031"), digest) == ["G2"]                               # a year the digest never mentions
    assert codes(edit("12-month total", "6-month total"), digest) == ["G2"]                    # not a configured window length
    assert codes(edit("12-month total", "twelve-month total"), digest) == []


def test_g2_a_numeric_date_format_is_not_allowed(digest):
    assert codes(edit("Dec 2025 operating income was", "2025-12 operating income was"), digest) == ["G2"]


def test_g3_causal_phrase_fails_and_the_message_says_what_to_do(digest):
    bad = edit("month on month.", "month on month because of fewer usage fees.")
    assert codes(bad, digest) == ["G3"]
    (f,) = check_draft(bad, digest)
    assert f.name == "CAUSAL_CLAIM" and "because" in f.text and f.line == line_of("Revenue moved")
    assert "movements, not causes" in f.message
    assert "question for the analyst" in f.message and "worth confirming what changed in" in f.message
    assert "even with a hedge" in f.message


@pytest.mark.parametrize("tail", [
    "month on month, which may be due to fewer usage fees.",
    "month on month, possibly driven by fewer usage fees.",
    "month on month; it is unclear whether this was caused by fewer usage fees.",
    "month on month, and this could be attributable to fewer usage fees.",
    "month on month, worth confirming whether it is because of fewer usage fees."])
def test_g3_a_hedge_no_longer_excuses_a_causal_phrase(digest, tail):
    assert codes(edit("month on month.", tail), digest) == ["G3"]


@pytest.mark.parametrize("tail", [
    "month on month. Worth confirming what changed in usage fees.",
    "month on month. Is it worth confirming what changed in usage fees?",
    "month on month; worth confirming what changed in usage fees and when."])
def test_g3_a_question_with_no_causal_assertion_passes(digest, tail):
    assert check_draft(edit("month on month.", tail), digest) == []


def test_g3_several_causal_phrases_in_one_sentence_are_one_failure_naming_them_all(digest):
    bad = edit("month on month.", "month on month because of fewer fees, which may be due to churn.")
    (f,) = check_draft(bad, digest)
    assert f.code == "G3" and "'because'" in f.message and "'due to'" in f.message


def test_g3_a_hedge_in_another_sentence_changes_nothing(digest):
    assert codes(edit("month on month.", "month on month because of fewer usage fees. This may be worth confirming."), digest) == ["G3"]


@pytest.mark.parametrize("phrase", ["due to", "caused by", "driven by", "as a result of", "attributable to",
                                    "thanks to", "owing to"])
def test_g3_each_listed_phrase_is_caught(digest, phrase):
    assert codes(edit("month on month.", f"month on month {phrase} fewer usage fees."), digest) == ["G3"]


def test_g4_direction_word_contradicts_a_negative_value(digest):
    bad = edit("Revenue moved {{rep.rev.total.mom_abs.2026-06}}", "Revenue rose {{rep.rev.total.mom_abs.2026-06}}")
    assert codes(bad, digest) == ["G4"]
    (f,) = check_draft(bad, digest)
    assert f.name == "DIRECTION_MISMATCH" and "rose" in f.message and f.line == line_of("Revenue moved")


def test_g4_direction_word_contradicts_a_positive_movement(digest):
    bad = edit("Gross margin moved {{flag.MOVE-2026-06-2073be.pp}}", "Gross margin fell {{flag.MOVE-2026-06-2073be.pp}}")
    assert codes(bad, digest) == ["G4"]


def test_g4_consistent_direction_words_pass(digest):
    assert check_draft(edit("Gross margin moved", "Gross margin rose"), digest) == []
    assert check_draft(GOOD.replace("Revenue moved", "Revenue declined"), digest) == []        # negative value, down word


def test_g4_is_skipped_when_a_sentence_has_several_signed_tokens(digest):
    ok = edit("G&A moved {{flag.MOVE-2026-06-7d0058.abs}}", "G&A rose {{flag.MOVE-2026-06-7d0058.abs}}")
    assert check_draft(ok, digest) == []                    # abs and pct (and others) are all signed: deliberately skipped


def test_g4_a_positive_level_that_is_not_a_movement_is_not_signed(digest):
    ok = edit("The 12-month total was {{rep.rev.total.tw12.2026-06}}.",
              "The 12-month total was {{rep.rev.total.tw12.2026-06}}, down on a year of earlier months.")
    assert check_draft(ok, digest) == []                    # a positive level is not a movement, so "down" is not judged


def test_g5_missing_as_of_flag(digest):
    bad = edit(", commissions {{flag.MOVE-2026-06-27e525.abs}} ({{flag.MOVE-2026-06-27e525.pct}}), other", ", other")
    assert codes(bad, digest) == ["G5"]
    (f,) = check_draft(bad, digest)
    assert f.name == "MISSING_COVERAGE" and "Commissions" in f.message and "2026-06" in f.message and f.line is None


def test_g5_missing_high_severity_flag(digest):
    bad = edit(" and stock compensation ({{flag.NONREC-2025-12-adf13c.reported}} reported, {{flag.NONREC-2025-12-adf13c.adjusted}} adjusted)", "")
    assert codes(bad, digest) == ["G5"]                     # citing operating income (shared evidence) does not cover it
    assert "Stock-Based Compensation Expense" in check_draft(bad, digest)[0].message


def test_g5_an_evidence_id_of_the_entry_also_covers_it(digest):
    ok = edit(", commissions {{flag.MOVE-2026-06-27e525.abs}} ({{flag.MOVE-2026-06-27e525.pct}}), other",
              ", commissions {{adj.opex.nat.commissions.mom_abs.2026-06}}, other")
    assert check_draft(ok, digest) == []


def test_g5_missing_headline_row(digest):
    bad = edit("Operating income was {{rep.opinc.2026-06}}, an operating margin of {{rep.opinc_margin_pct.2026-06}}.",
               "An operating margin of {{rep.opinc_margin_pct.2026-06}} was reported.")
    assert codes(bad, digest) == ["G5"]
    assert "Operating income" in check_draft(bad, digest)[0].message


def test_g5_missing_adjusted_figure(digest):
    bad = edit(" and {{adj.opinc.2025-12}} adjusted", "")
    assert codes(bad, digest) == ["G5"]
    (f,) = check_draft(bad, digest)
    assert "adjusted" in f.message and "2025-12" in f.message


def test_g5_missing_reported_figure(digest):
    bad = edit("Dec 2025 operating income was {{rep.opinc.2025-12}} reported and", "Dec 2025 operating income was")
    assert codes(bad, digest) == ["G5"]
    assert "reported" in check_draft(bad, digest)[0].message


def test_g5_the_warning_line_must_be_cited_when_the_digest_has_one(abc, abc_metrics, abc_flagset):
    d = build_digest(abc.company, abc_metrics, abc_flagset, abc.mapped, META, budget=1000)
    assert any(n.startswith("WARNING") for n in d["data_notes"])
    assert "G5" in codes(GOOD, d)
    assert any("WARNING" in f.message and "digest.budget_bytes" in f.message for f in check_draft(GOOD, d) if f.code == "G5")
    ok = GOOD + "- WARNING: the digest is over its size budget of {{digest.budget_bytes}} bytes.\n"
    assert not [f for f in check_draft(ok, d) if "WARNING" in f.message]


def test_g6_missing_banner(digest):
    bad = GOOD.split("\n", 2)[2]                               # drop the banner and the blank line
    assert codes(bad, digest) == ["G6"]
    (f,) = check_draft(bad, digest)
    assert f.name == "MISSING_DRAFT_BANNER" and f.line == 1


def test_g6_banner_must_be_the_first_line_and_say_draft(digest):
    assert codes("# Title\n" + GOOD, digest) == ["G6"]
    assert codes(edit("DRAFT - AI-generated from digest.json. Not reviewed. For analyst review.",
                      "AI-generated from digest.json. Not reviewed."), digest) == ["G6"]
    assert codes("\n\n" + GOOD, digest) == []                  # leading blank lines are fine
    assert codes(edit("DRAFT - AI-generated from digest.json. Not reviewed. For analyst review.",
                      "DRAFT - AI-generated from metrics.json (0a1b2c3d). Not reviewed. For reviewer completion."), digest) == []


def test_g7_malformed_tokens(digest):
    for bad_tok in ("{{rep.opex.total.pct_rev.2026-06}", "{rep.opex.total.pct_rev.2026-06}}",
                    "{{ rep.opex.total.pct_rev.2026-06 }}", "{{}}", "{{rep.opex.total.pct_rev.2026-06"):
        bad = edit("{{rep.opex.total.pct_rev.2026-06}}", bad_tok)
        assert codes(bad, digest) == ["G7"], bad_tok
    (f,) = check_draft(edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.opex.total.pct_rev.2026-06}"), digest)
    assert f.name == "MALFORMED_TOKEN" and f.line == line_of("of revenue") and "{{rep.opex.total.pct_rev.2026-06}" in f.text


def test_g7_a_token_split_over_two_lines_is_malformed(digest):
    bad = edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.opex.total.\npct_rev.2026-06}}")
    assert "G7" in codes(bad, digest)


def test_g7_text_that_looks_like_code_is_just_malformed_and_never_run(tmp_path, digest, monkeypatch):
    marker = tmp_path / "pwned.txt"
    evil = edit("{{rep.opex.total.pct_rev.2026-06}}", "{{__import__('pathlib').Path(r'%s').write_text('x')}}" % marker)
    assert codes(evil, digest) == ["G7"]
    assert not marker.exists()
    assert codes(edit("{{rep.opex.total.pct_rev.2026-06}}", "${{rep.opex.total.pct_rev.2026-06}}"), digest) == ["G2"]


# ---------------------------------------------------------------- the PLAN rules beyond G1-G7

def test_g8_banned_word(digest):
    bad = edit("Operating margin moved {{flag.MOVE-2026-06-b7e4a6.pp}}.", "Operating margin moved {{flag.MOVE-2026-06-b7e4a6.pp}} and will recover.")
    assert codes(bad, digest) == ["G8"]
    assert codes(edit("Gross margin moved", "Gross margin clearly moved"), digest) == ["G8"]
    assert codes(edit("Is the Bad Debts amount", "We recommend checking whether the Bad Debts amount"), digest) == ["G8"]


def test_g9_body_too_long(digest):
    filler = " ".join(["word"] * 200)
    bad = edit("- Is the Bad Debts amount a reversal of an earlier month?", "- Is the Bad Debts amount a reversal of an earlier month? " + filler)
    assert codes(bad, digest) == ["G9"]
    assert "250" in check_draft(bad, digest)[0].message
    ok = GOOD.replace("- Four payroll lines are zero in every month.", "- " + filler)          # Data notes are not counted
    assert check_draft(ok, digest) == []


def test_g10_missing_section_and_headline_bullet_limit(digest):
    assert codes(edit("## Revenue\n", ""), digest) == ["G10"]
    assert any("'Revenue'" in f.message for f in check_draft(edit("## Revenue\n", ""), digest))
    assert codes(edit("## Margins", "## Margin commentary"), digest) == ["G10"]
    four = edit("\n## Revenue", "- A fourth headline bullet with no numbers.\n\n## Revenue")
    assert codes(four, digest) == ["G10"]


# ---------------------------------------------------------------- reporting

def test_a_draft_that_fails_several_checks_reports_all_of_them(digest):
    bad = GOOD.split("\n", 2)[2]                                                       # G6
    bad = edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.opex.total.pct_rev.2030-01}}", bad)    # G1
    bad = edit("month on month.", "month on month by 5 points because of fewer fees.", bad)        # G2 + G3
    bad = edit("Gross margin moved", "Gross margin fell", bad)                                    # G4
    bad = edit("Operating margin moved {{flag.MOVE-2026-06-b7e4a6.pp}}.", "Operating margin will move.", bad)  # G5 + G8
    bad = edit("## Margins", "## Margin commentary", bad)                                          # G10
    fails = check_draft(bad, digest)
    assert sorted({f.code for f in fails}) == ["G1", "G10", "G2", "G3", "G4", "G5", "G6", "G8"]
    assert all(isinstance(f, Failure) for f in fails)


def test_every_offence_is_reported_not_just_the_first(digest):
    bad = edit("month on month.", "month on month by 5 and 7 points.")
    assert [f.text for f in check_draft(bad, digest) if f.code == "G2"] == ["5", "7"]
    two = edit("{{rep.opex.total.pct_rev.2026-06}}", "{{rep.opex.total.pct_rev.2030-01}}")
    two = edit("{{rep.rev.total.tw12.2026-06}}", "{{rep.rev.total.tw12.2030-06}}", two)
    assert [f.line for f in check_draft(two, digest)] == [line_of("rep.rev.total.tw12.2026-06"), line_of("pct_rev.2026-06")]


def test_failures_carry_code_message_line_and_text_and_are_sorted(digest):
    bad = edit("month on month.", "month on month by 5 points because of fewer fees.")
    fails = check_draft(bad, digest)
    for f in fails:
        d = f.to_dict()
        assert set(d) == {"code", "name", "message", "line", "text"} and d["message"] and d["name"] == CHECKS[d["code"]][0]
    assert [(f.line or 0) for f in fails] == sorted((f.line or 0) for f in fails)


def test_checker_is_deterministic(digest):
    bad = edit("month on month.", "month on month by 5 points because of fewer fees.")
    assert [f.to_dict() for f in check_draft(bad, digest)] == [f.to_dict() for f in check_draft(bad, digest)]


def test_tokens_in_returns_ids_in_order_and_ignores_malformed():
    assert tokens_in("a {{x.1}} b {{y-2}} c {{ bad }} {{z") == ["x.1", "y-2"]


def test_a_digest_without_allowed_facts_is_rejected():
    with pytest.raises(ValueError):
        check_draft(GOOD, {"status": "OK"})


def test_the_checker_works_on_a_tiny_company_digest(tmp_path):
    n = 6
    c = mini.build(tmp_path, months(n), [100000] * n, [40000] * n, [100] * n, [50] * n)
    m = compute_metrics(c.mapped, c.recon, c.company)
    fs = compute_flags(c.mapped, c.recon, m, c.company)
    d = build_digest(c.company, m, fs, c.mapped, META)
    draft = ("DRAFT - AI-generated from digest.json. Not reviewed. For analyst review.\n\n"
             "## Headline\n- Revenue was {{rep.rev.total.2025-06}}; gross margin was {{rep.gm_pct.2025-06}}.\n"
             "- Operating income was {{rep.opinc.2025-06}}.\n- Operating margin was {{rep.opinc_margin_pct.2025-06}}.\n"
             "## Revenue\n- Steady.\n## Margins\n- Steady.\n## Operating expenses\n- Steady.\n"
             "## Questions for the reviewer\n- None.\n## Data notes\n- None.\n")
    assert check_draft(draft, d) == []
    assert "G1" in codes(draft.replace("rep.rev.total.2025-06", "rep.rev.total.2024-06"), d)


# ================================================================ P8b: the window pair (G5 c)

PAIR = ("- The 12-month operating income was {{rep.opinc.tw12.2026-06}} reported and {{adj.opinc.tw12.2026-06}} "
        "adjusted; the window includes Dec 2025.\n")


def test_good_draft_cites_the_window_pair():
    assert PAIR in GOOD
    assert {"rep.opinc.tw12.2026-06", "adj.opinc.tw12.2026-06"} <= set(tokens_in(GOOD))


def test_g5_missing_window_pair_message(digest):
    bad = edit(PAIR, "")
    assert codes(bad, digest) == ["G5"]
    fails = check_draft(bad, digest)
    assert len(fails) == 2                                          # the reported and the adjusted figure
    by = {("reported" if "reported Operating income" in f.message else "adjusted"): f.message for f in fails}
    assert set(by) == {"reported", "adjusted"}
    for basis, rep_id in (("reported", "rep.opinc.tw12.2026-06"), ("adjusted", "adj.opinc.tw12.2026-06")):
        m = by[basis]
        assert "12-month window ending 2026-06" in m and "contains the adjusted period 2025-12" in m
        assert "{{" + rep_id + "}}" in m and "cite both" in m


def test_g5_missing_only_the_adjusted_or_only_the_reported_window_figure(digest):
    (f,) = check_draft(edit(" and {{adj.opinc.tw12.2026-06}} adjusted", ""), digest)
    assert f.code == "G5" and "adjusted Operating income" in f.message and "{{adj.opinc.tw12.2026-06}}" in f.message
    (g,) = check_draft(edit("was {{rep.opinc.tw12.2026-06}} reported and", "was"), digest)
    assert g.code == "G5" and "reported Operating income" in g.message


def test_g5_the_month_pair_is_still_required_as_well(digest):
    assert codes(edit(" and {{adj.opinc.2025-12}} adjusted", ""), digest) == ["G5"]


def abc_digest_for(abc, lengths):
    m = compute_metrics(abc.mapped, abc.recon, abc.company, window_lengths=lengths)
    fs = compute_flags(abc.mapped, abc.recon, m, abc.company)
    return build_digest(abc.company, m, fs, abc.mapped, META)


def test_g5_window_pair_not_required_when_the_as_of_window_holds_no_adjusted_period(abc):
    d = abc_digest_for(abc, (3,))                                   # 3-month window: Apr-Jun 2026, no Dec 2025 in it
    assert [p["period"] for p in d["adjusted_periods"]] == ["2025-12"] and d["windows"][0]["length"] == 3
    draft = edit(PAIR, "")
    draft = edit("The 12-month total was {{rep.rev.total.tw12.2026-06}}.", "The 3-month total was {{rep.rev.total.tw3.2026-06}}.", draft)
    assert check_draft(draft, d) == []                              # no window pair asked for
    assert codes(edit(" and {{adj.opinc.2025-12}} adjusted", "", draft), d) == ["G5"]     # but the month pair still is


def test_g5_window_pair_is_required_only_for_the_windows_that_hold_an_adjusted_period(abc):
    d = abc_digest_for(abc, (3, 12))
    assert [w["length"] for w in d["windows"]] == [3, 12]
    assert check_draft(GOOD, d) == []
    fails = check_draft(edit(PAIR, ""), d)
    assert len(fails) == 2 and all("12-month window" in f.message for f in fails)       # nothing is asked about the 3-month one


def test_g5_no_window_requirement_for_a_company_with_no_adjustments(tmp_path):
    MINI = (ROOT / "tests" / "fixtures" / "commentary" / "good_mini_no_adjustments.md").read_text(encoding="utf-8")
    n = 6
    c = mini.build(tmp_path, months(n), [100000] * n, [40000] * n, [100] * n, [50] * n)
    m = compute_metrics(c.mapped, c.recon, c.company)
    d = build_digest(c.company, m, compute_flags(c.mapped, c.recon, m, c.company), c.mapped, META)
    assert d["adjusted_periods"] == [] and d["flags"]["top"] == []
    assert not [t for t in tokens_in(MINI) if ".tw" in t]            # the fixture really has no window figures
    assert check_draft(MINI, d) == []


def test_g5_window_pair_is_required_on_a_small_company_when_its_window_holds_the_adjusted_month(tmp_path):
    MINI = (ROOT / "tests" / "fixtures" / "commentary" / "good_mini_no_adjustments.md").read_text(encoding="utf-8")
    n = 6
    c = mini.build(tmp_path, months(n), [100000] * n, [40000] * n, [100] * n, [50] * n,
                   adjustments=[{"account": "Rent", "periods": ["2025-03"], "reason": "one-off"}])
    m = compute_metrics(c.mapped, c.recon, c.company, window_lengths=(6,))
    fs = compute_flags(c.mapped, c.recon, m, c.company)
    d = build_digest(c.company, m, fs, c.mapped, META)
    fid = next(f.id for f in fs.flags if f.type == "NONRECURRING")
    assert [p["period"] for p in d["adjusted_periods"]] == ["2025-03"] and (d["windows"][0]["length"], d["windows"][0]["end"]) == (6, "2025-06")
    base = MINI.replace("- No adjustments are configured for this company.", "- One adjustment is configured.")
    base = base.replace("## Operating expenses\n", "## Operating expenses\n"
                        f"- Rent in Mar 2025 was {{{{flag.{fid}.reported}}}} reported and {{{{flag.{fid}.adjusted}}}} adjusted, likely non-recurring, to be confirmed.\n"
                        "- Operating income in Mar 2025 was {{rep.opinc.2025-03}} reported and {{adj.opinc.2025-03}} adjusted.\n")
    missing = check_draft(base, d)
    assert sorted(f.code for f in missing) == ["G5", "G5"] and all("6-month window ending 2025-06" in f.message for f in missing)
    ok = base.replace("## Margins\n", "## Margins\n- The 6-month operating income was {{rep.opinc.tw6.2025-06}} reported and {{adj.opinc.tw6.2025-06}} adjusted.\n")
    assert check_draft(ok, d) == []
