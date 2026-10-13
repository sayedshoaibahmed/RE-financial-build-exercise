"""P7b: .claude/settings.json permission rules that back up the NEVER rules in SKILL.md.
Syntax: https://code.claude.com/docs/en/permissions (section "Read and Edit"). Whether Claude Code enforces them is not
testable from here; these tests check the file's content, and that each rule matches what it is meant to protect."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
SKILL = ROOT / ".claude" / "skills" / "monthly-trend-analysis"
SKILL_TEXT = (SKILL / "SKILL.md").read_text(encoding="utf-8")

CONFIG_EDIT_RULES = {                    # a human edits these, never Claude: deny, not ask (P7e)
    "Edit(/companies/**/mapping.yaml)",
    "Edit(/companies/**/company.yaml)",
}
EXPECTED_DENY = CONFIG_EDIT_RULES | {
    "Read(**/metrics.json)",
    "Read(**/trend_pack.xlsx)",
    "Read(/data/**)",
    "Read(**/.env)",
    "Read(**/tokens.json)",
    "Read(//c/Users/shoai/AppData/Local/qbo-mcp/**)",
    "Edit(/tests/golden/abc_flags.expected.json)",
    "Edit(**/reference/commentary_rules.yaml)",
    "Edit(**/digest.json)",
}


def load():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- a small matcher for the documented syntax

def _regex(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out += "/.*"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return out


def rule_matches(rule, tool, path):
    """Does `Tool(pattern)` cover `path`? `path` is relative to the repository root (posix) or an absolute '/c/...' path.
    '//x' = absolute from the filesystem root, '/x' = anchored at the project root (project settings), 'x' = floating."""
    m = re.fullmatch(r"(\w+)\((.*)\)", rule)
    if not m or m.group(1) != tool:
        return False
    pat = m.group(2)
    if pat.startswith("//"):
        return re.fullmatch(_regex(pat[1:]), path) is not None
    if pat.startswith("/"):
        return re.fullmatch(_regex(pat[1:]), path) is not None
    if "/" not in pat.strip("/"):                                       # a bare file name: any depth
        return re.fullmatch(r"(?:.*/)?" + _regex(pat), path) is not None
    return re.fullmatch(_regex(pat), path) is not None or re.fullmatch(r"(?:.*/)?" + _regex(pat), path) is not None


def covered(rules, tool, path):
    return any(rule_matches(r, tool, path) for r in rules)


# ---------------------------------------------------------------- the file

def test_settings_json_exists_parses_and_holds_only_permissions():
    assert SETTINGS.is_file()
    raw = SETTINGS.read_text(encoding="utf-8")
    assert raw.isascii() and not raw.startswith("﻿")
    data = json.loads(raw)
    assert set(data) == {"permissions"}                                   # no hooks, env or other settings
    assert set(data["permissions"]) == {"deny"}                           # no allow rules, and no ask rules


def test_rule_groups_are_exactly_the_requested_ones():
    p = load()["permissions"]
    assert not p.get("ask")                                                              # nothing is left on "ask"
    assert CONFIG_EDIT_RULES <= set(p["deny"]) and not CONFIG_EDIT_RULES & set(p.get("ask", []))
    assert set(p["deny"]) == EXPECTED_DENY and len(p["deny"]) == len(EXPECTED_DENY)


def test_only_edit_and_read_rules_are_used_because_write_rules_are_never_consulted():
    # docs: "Claude Code checks file permissions against Edit(path) and Read(path) rules only. A path rule for Write ... is
    # accepted but never consulted" and warns at startup. Edit rules apply to all built-in tools that edit files.
    p = load()["permissions"]
    for r in p.get("ask", []) + p["deny"]:
        assert re.fullmatch(r"(Edit|Read)\([^()]+\)", r), r
    assert not [r for r in p.get("ask", []) + p["deny"] if r.startswith(("Write(", "MultiEdit(", "Glob(", "NotebookEdit("))]


def test_rules_carry_no_secrets_and_are_plain_paths():
    for r in load()["permissions"].get("ask", []) + load()["permissions"]["deny"]:
        assert not re.search(r"(?i)(secret|password|api[_-]?key|bearer|sk-)[^/]*[:=]", r), r
        assert "\\" not in r                                               # POSIX form, as the docs require on Windows


# ---------------------------------------------------------------- the matcher itself (so the next tests mean something)

@pytest.mark.parametrize("rule,tool,path,want", [
    ("Edit(/companies/**/mapping.yaml)", "Edit", "companies/abc_inc/mapping.yaml", True),
    ("Edit(/companies/**/mapping.yaml)", "Edit", "companies/a/b/mapping.yaml", True),
    ("Edit(/companies/**/mapping.yaml)", "Edit", "companies/abc_inc/mapping.proposed.yaml", False),
    ("Edit(/companies/**/mapping.yaml)", "Edit", "other/mapping.yaml", False),
    ("Edit(/companies/**/mapping.yaml)", "Read", "companies/abc_inc/mapping.yaml", False),
    ("Read(**/metrics.json)", "Read", "outputs/abc_inc/2026-06_w12-2026-06/metrics.json", True),
    ("Read(**/metrics.json)", "Read", "metrics.json", True),
    ("Read(**/metrics.json)", "Read", "outputs/x/metrics.jsonl", False),
    ("Read(/data/**)", "Read", "data/ABC Inc - Financial Statements.xlsx", True),
    ("Read(/data/**)", "Read", "tests/data/x.xlsx", False),
    ("Read(**/.env)", "Read", ".env", True),
    ("Read(**/.env)", "Read", "a/b/.env", True),
    ("Read(**/.env)", "Read", "a/.env.example", False),
    ("Read(//c/Users/shoai/AppData/Local/qbo-mcp/**)", "Read", "/c/Users/shoai/AppData/Local/qbo-mcp/tokens.json", True),
    ("Read(//c/Users/shoai/AppData/Local/qbo-mcp/**)", "Read", "/c/Users/shoai/AppData/Local/other/tokens.json", False),
    ("Edit(**/reference/commentary_rules.yaml)", "Edit", ".claude/skills/monthly-trend-analysis/reference/commentary_rules.yaml", True),
    ("Edit(**/reference/commentary_rules.yaml)", "Edit", ".claude/skills/monthly-trend-analysis/reference/commentary_rules.md", False),
])
def test_matcher_examples(rule, tool, path, want):
    assert rule_matches(rule, tool, path) is want


# ---------------------------------------------------------------- the rules protect what they are meant to protect

def test_company_files_are_denied_but_the_proposal_and_templates_stay_writable():
    deny = load()["permissions"]["deny"]
    for name in ("mapping.yaml", "company.yaml"):
        assert covered(deny, "Edit", f"companies/abc_inc/{name}")
    for ok in ("companies/abc_inc/mapping.proposed.yaml", ".claude/skills/monthly-trend-analysis/templates/company.template.yaml",
               ".claude/skills/monthly-trend-analysis/templates/mapping.template.yaml"):
        assert not covered(deny, "Edit", ok), ok                           # Claude must be able to write proposals
    assert not covered(deny, "Read", "companies/abc_inc/mapping.yaml")     # reading stays allowed


def test_generated_and_secret_files_cannot_be_read():
    deny = load()["permissions"]["deny"]
    for p in ("outputs/abc_inc/2026-06_w12-2026-06/metrics.json", "outputs/abc_inc/2026-06_w12-2026-06/trend_pack.xlsx",
              "data/ABC Inc - Financial Statements.xlsx", ".env", "companies/abc_inc/.env", "tokens.json",
              "/c/Users/shoai/AppData/Local/qbo-mcp/tokens.json", "/c/Users/shoai/AppData/Local/qbo-mcp/sub/dir/x.db"):
        assert covered(deny, "Read", p), p
    for p in ("outputs/abc_inc/2026-06_w12-2026-06/digest.json", "outputs/abc_inc/2026-06_w12-2026-06/grounding_report.json",
              "outputs/abc_inc/2026-06_w12-2026-06/blocked_report.json", "companies/abc_inc/mapping.yaml"):
        assert not covered(deny, "Read", p), p                             # what the skill must read stays readable


def test_golden_flags_and_the_checker_rules_cannot_be_edited():
    deny = load()["permissions"]["deny"]
    assert covered(deny, "Edit", "tests/golden/abc_flags.expected.json")
    assert covered(deny, "Edit", ".claude/skills/monthly-trend-analysis/reference/commentary_rules.yaml")
    assert not covered(deny, "Edit", "tests/golden/README.md")
    assert not covered(deny, "Edit", ".claude/skills/monthly-trend-analysis/reference/commentary_rules.md")


# ---------------------------------------------------------------- SKILL.md and the permission rules agree

def never_paths():
    """Backticked file names in the NEVER rules (text after 'Read only' is the allow-list, not a prohibition)."""
    start = SKILL_TEXT.index("## Hard rules")
    block = SKILL_TEXT[start:SKILL_TEXT.index("\n## ", start + 5)]
    found = set()
    for ln in block.splitlines():
        if "NEVER" in ln:
            for tok in re.findall(r"`([^`]+)`", ln.split("Read only")[0]):
                if re.search(r"\.(json|yaml|xlsx)$", tok):
                    found.add(tok)
    return found


SAMPLE = {   # NEVER-rule file -> (tool, a concrete path it stands for)
    "mapping.yaml": ("Edit", "companies/abc_inc/mapping.yaml"),
    "company.yaml": ("Edit", "companies/abc_inc/company.yaml"),
    "metrics.json": ("Read", "outputs/abc_inc/2026-06_w12-2026-06/metrics.json"),
    "trend_pack.xlsx": ("Read", "outputs/abc_inc/2026-06_w12-2026-06/trend_pack.xlsx"),
    "digest.json": ("Edit", "outputs/abc_inc/2026-06_w12-2026-06/digest.json"),
    "reference/commentary_rules.yaml": ("Edit", ".claude/skills/monthly-trend-analysis/reference/commentary_rules.yaml"),
}
# NEVER rules that have no permission rule, because the requested rule groups do not include them. Naming them here keeps
# the gap visible: adding a rule for one of them means removing it from this set.
TEXT_ONLY = set()      # digest.json was a gap until P7c added Edit(**/digest.json)


def test_every_path_named_in_the_never_rules_has_a_matching_ask_or_deny_rule_or_is_a_listed_gap():
    p = load()["permissions"]
    rules = p.get("ask", []) + p["deny"]
    named = never_paths()
    assert {"mapping.yaml", "company.yaml", "metrics.json", "trend_pack.xlsx", "digest.json",
            "reference/commentary_rules.yaml"} <= named, named
    unmatched = set()
    for name in named:
        assert name in SAMPLE or name in TEXT_ONLY, f"new NEVER path with no permission rule and no listed gap: {name}"
        if name in SAMPLE:
            tool, path = SAMPLE[name]
            assert covered(rules, tool, path), name
        else:
            unmatched.add(name)
    assert unmatched == TEXT_ONLY
    assert covered(p["deny"], "Edit", "outputs/abc_inc/2026-06_w12-2026-06/digest.json")
    assert not covered(p["deny"], "Read", "outputs/abc_inc/2026-06_w12-2026-06/digest.json")   # the skill must still read it


def test_the_source_workbook_rule_is_backed_by_a_data_folder_deny_rule():
    assert re.search(r"NEVER open the source workbook", SKILL_TEXT)
    assert covered(load()["permissions"]["deny"], "Read", "data/ABC Inc - Financial Statements.xlsx")


def test_skill_md_says_the_permission_rules_back_the_text_rules_up_but_the_text_applies_without_them():
    start = SKILL_TEXT.index("## Hard rules")
    block = SKILL_TEXT[start:SKILL_TEXT.index("\n## ", start + 5)]
    lines = [l for l in block.splitlines() if "settings.json" in l]
    assert len(lines) == 1, lines
    low = lines[0].lower()
    assert "permission rules" in low and "back these rules up" in low and "even where that file is absent" in low
    assert len(SKILL_TEXT.splitlines()) < 160


# ---------------------------------------------------------------- tests and code never depend on the settings

def test_nothing_in_the_code_or_the_other_tests_reads_the_settings_file():
    me = Path(__file__).resolve()
    sources = [p for p in list((SKILL / "scripts").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))
               if "__pycache__" not in p.parts and p.resolve() != me]
    assert len(sources) > 30
    for p in sources:
        assert "settings.json" not in p.read_text(encoding="utf-8"), p
    pytest_ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "settings" not in pytest_ini and "permissions" not in pytest_ini
