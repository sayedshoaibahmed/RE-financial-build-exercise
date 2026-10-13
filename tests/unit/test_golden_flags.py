"""The frozen ABC flag list. Any added, removed or changed flag fails this test, and so does a
threshold change in company.yaml that was not followed by regenerating the golden file."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests import golden_flags as g

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def expected():
    assert g.GOLDEN.is_file(), f"{g.GOLDEN} is missing: run scripts/regen_golden_flags.py and have a human review it"
    return json.loads(g.GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current():
    return g.current_golden()


def test_abc_flags_equal_the_frozen_golden_file(expected, current):
    problems = g.compare(expected, current)
    assert not problems, "\n" + "\n".join(["ABC flags differ from tests/golden/abc_flags.expected.json:"] + problems + [
        "If the change is intended, a HUMAN must review it, then run scripts/regen_golden_flags.py."])


def test_thresholds_in_company_yaml_match_the_ones_the_file_was_frozen_with(expected):
    raw = yaml.safe_load((ROOT / "companies" / "abc_inc" / "company.yaml").read_text(encoding="utf-8"))["thresholds"]
    assert expected["_header"]["thresholds"] == raw, (
        "company.yaml thresholds changed since the golden file was frozen: " +
        ", ".join(sorted(k for k in set(raw) | set(expected["_header"]["thresholds"])
                         if raw.get(k) != expected["_header"]["thresholds"].get(k))))


def test_header_counts_and_total_match_the_listed_flags(expected):
    flags = expected["flags"]
    counts = {}
    for f in flags:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    assert {t: counts.get(t, 0) for t in g.TYPES} == expected["_header"]["counts"]
    assert expected["_header"]["total"] == len(flags)
    assert "human" in expected["_header"]["warning"].lower()


def test_flags_are_stored_sorted_by_type_period_subject_basis(expected):
    keys = [(f["type"], f["period"], f["subject"], f["basis"]) for f in expected["flags"]]
    assert keys == sorted(keys) and len({f["id"] for f in expected["flags"]}) == len(keys)


def test_every_golden_flag_has_the_documented_fields(expected):
    for f in expected["flags"]:
        assert set(f) == {"id", "type", "severity", "period", "subject", "observed", "basis"}
        assert f["observed"] not in (None, "", {})


# ---- the comparer itself: added / removed / changed / header problems are all reported clearly ----

def base():
    return {"_header": {"thresholds": {"a": 1}, "counts": {"NEGATIVE_VALUE": 1}, "total": 1},
            "flags": [{"id": "NEG-2025-02-aaaaaa", "type": "NEGATIVE_VALUE", "severity": "low", "period": "2025-02",
                       "subject": "Rent", "observed": {"amount": "-98.50"}, "basis": "both"}]}


def test_compare_identical_is_clean():
    assert g.compare(base(), base()) == []


def test_compare_reports_an_added_flag():
    cur = base()
    cur["flags"].append(dict(cur["flags"][0], id="NEG-2025-03-bbbbbb", period="2025-03"))
    out = g.compare(base(), cur)
    assert len(out) == 1 and out[0].startswith("ADDED") and "NEG-2025-03-bbbbbb" in out[0] and "Rent" in out[0]


def test_compare_reports_a_removed_flag():
    cur = base()
    cur["flags"] = []
    out = g.compare(base(), cur)
    assert len(out) == 1 and out[0].startswith("REMOVED") and "NEG-2025-02-aaaaaa" in out[0]


def test_compare_reports_a_changed_flag_with_old_and_new_values():
    cur = base()
    cur["flags"][0] = dict(cur["flags"][0], observed={"amount": "-98.51"}, severity="medium")
    out = g.compare(base(), cur)
    assert len(out) == 2 and all(line.startswith("CHANGED") for line in out)
    text = "\n".join(out)
    assert "observed" in text and "-98.50" in text and "-98.51" in text and "severity" in text


def test_compare_reports_threshold_and_count_drift():
    cur = base()
    cur["_header"] = {"thresholds": {"a": 2}, "counts": {"NEGATIVE_VALUE": 2}, "total": 2}
    out = g.compare(base(), cur)
    text = "\n".join(out)
    assert out and all(line.startswith("HEADER") for line in out)
    assert "thresholds" in text and "a" in text and "counts" in text


def test_table_is_one_line_per_flag_sorted_by_type_period_subject(current):
    lines = g.format_table(current["flags"]).splitlines()
    assert len(lines) == len(current["flags"])
    keys = [(f["type"], f["period"], f["subject"], f["basis"]) for f in current["flags"]]
    assert keys == sorted(keys)
    assert all(line.count(" | ") == 6 for line in lines)


# ---- the regeneration script ----

def test_regen_script_exists_and_warns_loudly(tmp_path):
    script = ROOT / "scripts" / "regen_golden_flags.py"
    assert script.is_file()
    r = subprocess.run([sys.executable, str(script), "--table"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "table only" in r.stdout.lower()                      # --table never writes the file
    assert len([ln for ln in r.stdout.splitlines() if " | " in ln]) == len(json.loads(g.GOLDEN.read_text(encoding="utf-8"))["flags"])


def test_regen_script_prints_the_human_review_warning_before_writing(tmp_path):
    text = (ROOT / "scripts" / "regen_golden_flags.py").read_text(encoding="utf-8")
    assert "HUMAN" in text and "REVIEW" in text and "WARNING" in text
    assert "--table" in text
