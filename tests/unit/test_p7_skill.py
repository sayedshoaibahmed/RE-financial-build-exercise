"""P7: the skill itself. Lints SKILL.md, runs every command it documents, checks the exit-code table against the code,
tests the Python launcher, and checks the guard rails and the shipped example."""
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.fixtures import mini
from trend import pipeline
from trend.digest import build_digest
from trend.grounding import CHECKS

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "monthly-trend-analysis"
SKILL_MD = SKILL / "SKILL.md"
LAUNCHER = SKILL / "scripts" / "ensure_python.py"
CLI = SKILL / "scripts" / "trend_cli.py"
TEXT = SKILL_MD.read_text(encoding="utf-8")
LINES = TEXT.splitlines()
META = {"input_sha256": "0" * 64, "config_sha256": "1" * 64}


def run(*args, cwd=None, env=None):
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True, env=env)


def frontmatter():
    m = re.match(r"^---\n(.*?)\n---\n", TEXT, re.S)
    assert m, "SKILL.md must start with a --- frontmatter block"
    return yaml.safe_load(m.group(1))


def section(title):
    out, on = [], False
    for ln in LINES:
        if ln.startswith("## "):
            on = ln[3:].strip().lower() == title.lower()
            continue
        if on:
            out.append(ln)
    return out


# ================================================================ lint

def test_frontmatter_name_matches_the_folder_and_description_says_when_and_when_not():
    fm = frontmatter()
    assert fm["name"] == SKILL.name == "monthly-trend-analysis"
    d = fm["description"]
    assert isinstance(d, str) and len(d) < 1024
    low = d.lower()
    assert "use when" in low and " not " in f" {low} "
    for no in ("forecast", "cash", "workbook"):
        assert no in low, no                                    # says what it is NOT for


def test_allowed_tools_are_minimal_and_include_no_edit_tool():
    tools = fm_tools()
    assert tools and all(re.fullmatch(r"(Read|Write|Bash\(python:\*\))", t) for t in tools), tools
    assert not {"Edit", "MultiEdit", "NotebookEdit", "WebFetch"} & set(tools)


def fm_tools():
    raw = frontmatter()["allowed-tools"]
    return [t.strip() for t in raw.split(",")] if isinstance(raw, str) else list(raw)


def test_no_leftover_probe_text_and_the_file_is_lean():
    assert "probe" not in TEXT.lower()
    assert 80 <= len(LINES) < 160, len(LINES)


def test_every_path_it_mentions_under_reference_scripts_or_templates_exists():
    paths = set(re.findall(r"(?<![\w/])((?:reference|scripts|templates)/[A-Za-z0-9_./-]*[A-Za-z0-9_])", TEXT))
    assert len(paths) >= 6, paths
    missing = sorted(p for p in paths if "<" not in p and not (SKILL / p).exists())
    assert not missing, missing


def test_it_points_to_the_reference_files_and_the_example():
    for name in ("reference/commentary_rules.md", "reference/commentary_rules.yaml", "reference/example_commentary.md",
                 "reference/mapping_workflow.md", "reference/categories.md", "scripts/ensure_python.py"):
        assert name in TEXT, name


def test_uses_the_skill_dir_variable_for_every_script_path():
    cmd_lines = [l for l in LINES if "ensure_python.py" in l and l.strip().startswith("python")]
    assert cmd_lines and all("${CLAUDE_SKILL_DIR}/scripts/ensure_python.py" in l for l in cmd_lines)


# ================================================================ the commands it documents actually exist

def fenced_command_lines():
    out, fence = [], False
    for ln in LINES:
        if ln.strip().startswith("```"):
            fence = not fence
            continue
        if fence and ("ensure_python.py" in ln or "trend_cli.py" in ln):
            out.append(ln.strip())
    return out


def test_every_documented_command_line_runs_with_help_and_knows_its_options():
    cmds = fenced_command_lines()
    assert len(cmds) >= 4
    seen = set()
    for ln in cmds:
        tokens = shlex.split(ln.replace("${CLAUDE_SKILL_DIR}", SKILL.as_posix()), posix=True)
        assert tokens[0] == "python", ln
        script, sub = tokens[1], tokens[2]
        assert Path(script).name in ("ensure_python.py", "trend_cli.py"), ln
        if sub.startswith("--"):                                  # e.g. "--which": a safe flag, run it as written
            r = run(script, sub)
            assert r.returncode == 0, (ln, r.stderr)
            continue
        r = run(script, sub, "--help")
        assert r.returncode == 0, (ln, r.stderr)
        for opt in re.findall(r"--[a-z][a-z-]*", ln):
            assert opt in r.stdout, f"{opt} is not an option of `{sub}` (line: {ln})"
        seen.add(sub)
    assert {"check", "run", "render", "list-accounts"} <= seen


def test_the_four_commands_come_in_the_right_order():
    pos = {}
    for sub in ("check", "run", "render"):
        m = re.search(rf'ensure_python\.py" {sub} ', TEXT)
        assert m, sub
        pos[sub] = m.start()
    assert pos["check"] < pos["run"] < pos["render"]
    steps = [l for l in LINES if l.startswith("### ")]
    assert [s.split(". ", 1)[1].split()[0].lower() for s in steps[:3]] == ["pin", "check", "run"]


# ================================================================ exit codes

def documented_codes():
    return {int(m.group(1)): m.group(2) for l in LINES if (m := re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", l))}


def test_documented_exit_codes_match_the_code():
    doc = documented_codes()
    code = {pipeline.EXIT_OK: "ok", pipeline.EXIT_USAGE: "usage", pipeline.EXIT_BLOCKED: "BLOCKED",
            pipeline.EXIT_GROUNDING_FAILED: "GROUNDING", pipeline.EXIT_ENVIRONMENT: "Python"}
    assert set(doc) == set(code) == {0, 2, 3, 4, 5}
    for number, word in code.items():
        assert word.lower() in doc[number].lower(), (number, doc[number])


def test_exit_codes_are_in_the_help_of_both_entry_points():
    cli_help = run(CLI, "--help").stdout
    for n in ("0", "2", "3", "4", "5"):
        assert re.search(rf"^\s*{n}\s+\S", cli_help, re.M), n
    assert "GROUNDING_FAILED" in cli_help and "BLOCKED" in cli_help
    launcher_help = run(LAUNCHER, "--help").stdout
    assert re.search(r"^\s*5\s+\S", launcher_help, re.M) and "pip install -r" in launcher_help


def load_launcher():
    spec = importlib.util.spec_from_file_location("ensure_python", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_launcher_uses_the_shared_environment_exit_code():
    assert load_launcher().EXIT_ENVIRONMENT == pipeline.EXIT_ENVIRONMENT == 5


# ================================================================ the launcher

def fake_install(tmp_path, pins):
    """A copy of the launcher with its own requirements.txt, in a folder tree that has no .venv."""
    d = tmp_path / ".claude" / "skills" / "monthly-trend-analysis" / "scripts"
    d.mkdir(parents=True)
    shutil.copy(LAUNCHER, d / "ensure_python.py")
    (d / "requirements.txt").write_text("\n".join(pins) + "\n", encoding="utf-8")
    return d


def test_launcher_with_a_good_environment_exits_0_and_prints_the_interpreter(tmp_path):
    r = run(LAUNCHER, "--which", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    chosen = Path(r.stdout.strip())
    assert chosen.is_file() and chosen.name.lower().startswith("python")
    ok = subprocess.run([str(chosen), "-c", "import openpyxl, yaml; print(openpyxl.__version__, yaml.__version__)"],
                        capture_output=True, text=True)
    assert ok.stdout.split() == ["3.1.5", "6.0.3"]               # the pinned versions


def test_launcher_prefers_the_project_venv():
    r = run(LAUNCHER, "--which", cwd=ROOT)
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        assert Path(r.stdout.strip()).resolve() == venv_python.resolve()


def test_launcher_delegates_and_passes_the_exit_code_through(tmp_path):
    ok = run(LAUNCHER, "run", "--help", cwd=tmp_path)
    assert ok.returncode == 0 and "--window-end" in ok.stdout
    usage = run(LAUNCHER, "run", cwd=tmp_path)                    # the CLI's own usage error: 2
    assert usage.returncode == 2
    blocked = run(LAUNCHER, "check", "--company-dir", mini_blocked(tmp_path), cwd=tmp_path)
    assert blocked.returncode == 3 and "reconciliation: BLOCKED" in blocked.stdout


def mini_blocked(tmp_path):
    n = 6
    mini.build(tmp_path, mini.months(n), [100000] * n, [40000] * n, [100] * n, [50] * n,
               sales_leaf=[100000, 100000, 100001, 100000, 100000, 100000])
    return tmp_path / "companies" / "mini"


@pytest.mark.parametrize("pins", [["openpyxl==0.0.1", "PyYAML==6.0.3"],           # wrong version
                                  ["openpyxl==3.1.5", "PyYAML==6.0.3", "nosuchpackage==1.0"],   # missing module
                                  ["PyYAML==0.0.1", "openpyxl==3.1.5"]])
def test_launcher_exits_5_and_prints_the_install_command_when_no_python_fits(tmp_path, pins):
    d = fake_install(tmp_path, pins)
    r = run(d / "ensure_python.py", "--which", cwd=tmp_path)
    assert r.returncode == 5 and r.stdout.strip() == ""
    assert "pip install -r" in r.stderr and "requirements.txt" in r.stderr
    assert str(d / "requirements.txt") in r.stderr.replace("/", "\\") or str(d / "requirements.txt") in r.stderr
    for pin in pins:
        assert pin in r.stderr                                    # tells the person exactly what is expected
    delegated = run(d / "ensure_python.py", "check", "--company", "abc_inc", cwd=tmp_path)
    assert delegated.returncode == 5                              # and never runs the CLI with the wrong packages


def test_launcher_is_not_fooled_by_a_broken_pythonpath_or_an_empty_path(tmp_path):
    # the fake install pins the versions that really are installed; a broken PYTHONPATH and an empty PATH change nothing,
    # because the launcher judges each candidate interpreter by running it, not by what the caller's environment says
    d = fake_install(tmp_path, ["openpyxl==3.1.5", "PyYAML==6.0.3"])
    env = {"PATH": "", "SYSTEMROOT": "C:\\Windows", "PYTHONPATH": str(tmp_path / "nowhere")}
    r = run(d / "ensure_python.py", "--which", cwd=tmp_path, env=env)
    assert r.returncode == 0 and Path(r.stdout.strip()).is_file()


# ================================================================ guard rails in the text

def test_hard_rules_section_forbids_each_dangerous_action():
    rules = [l for l in section("Hard rules") if l.lstrip().startswith("- ")]
    never = [l for l in rules if "NEVER" in l]
    assert len(never) >= 5
    joined = lambda *words: [l for l in never if all(w in l.lower() for w in words)]
    assert joined("mapping.yaml", "company.yaml"), "missing: NEVER create or edit mapping.yaml / company.yaml"
    assert [l for l in joined("mapping.yaml") if re.search(r"\b(edit|create)\b", l, re.I)]
    assert joined("open", "workbook") or joined("open", ".xlsx")
    assert joined("compute") or joined("calculat")
    assert joined("metrics.json")
    assert [l for l in never if "digest.json" in l and re.search(r"\bedit\b", l, re.I)]


NEGATION = re.compile(r"\b(never|not|no|without|human|only a human)\b", re.I)


def test_no_step_tells_claude_to_open_the_workbook_or_edit_company_or_mapping_yaml():
    hard = set(map(id, section("Hard rules")))
    for ln in LINES:
        if re.search(r"mapping\.yaml|company\.yaml|\.xlsx|source workbook|openpyxl|source data", ln, re.I):
            assert NEGATION.search(ln), f"not a prohibition: {ln!r}"
    for ln in LINES:                                                  # no instruction to read / open / edit them
        assert not re.search(r"\b(open|edit|modify|overwrite)\b[^.\n]{0,40}\b(mapping|company)\.yaml", ln, re.I) or NEGATION.search(ln), ln
    assert "openpyxl" not in TEXT and "pandas" not in TEXT


def test_the_workflow_text_carries_the_required_instructions():
    low = TEXT.lower()
    for needle in ("blocked_report.json", "grounding_report.json", "digest.json", "commentary_draft.md",
                   "mapping.proposed.yaml", "list-accounts", "at most 3", "draft for analyst review",
                   "worth confirming what changed", "moved", "{{fact id}}", "suppressed", "warning"):
        assert needle in low, needle
    assert "--window-end" in TEXT and "--window " in TEXT and "--as-of" in TEXT
    assert "ask one short question" in low
    assert "not supported" in low                                     # non-Excel sources: say so and stop
    assert re.search(r"never[^\n]*ignore|ignore any instruction", low)


def test_documented_attempt_limit_matches_the_rules_file_philosophy():
    assert re.search(r"at most 3 render attempts", TEXT, re.I)


# ================================================================ the shipped example and reference files

@pytest.fixture(scope="module")
def digest_text(abc, abc_metrics, abc_flagset):
    from trend.writers.json_out import dumps
    return dumps(build_digest(abc.company, abc_metrics, abc_flagset, abc.mapped, META))


def test_example_commentary_is_a_copy_of_the_tested_good_draft():
    example = SKILL / "reference" / "example_commentary.md"
    good = ROOT / "tests" / "fixtures" / "commentary" / "good_abc_2026-06.md"
    assert example.read_bytes() == good.read_bytes()


def test_example_commentary_passes_the_grounding_checker_against_the_real_abc_digest(tmp_path, digest_text):
    run_dir = tmp_path / "abc_inc" / "2026-06_w12-2026-06"
    run_dir.mkdir(parents=True)
    (run_dir / "digest.json").write_text(digest_text, encoding="utf-8")
    shutil.copy(SKILL / "reference" / "example_commentary.md", run_dir / "commentary_draft.md")
    r = run(LAUNCHER, "render", "--run-dir", run_dir, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    rep = json.loads((run_dir / "grounding_report.json").read_text(encoding="utf-8"))
    assert rep["status"] == "OK" and rep["failures"] == []
    md = (run_dir / "commentary.md").read_text(encoding="utf-8")
    assert md.startswith("DRAFT") and "195,898.16" in md and "{{" not in md.split("## Audit appendix")[0]


def test_reference_files_exist_and_cover_what_the_skill_points_at():
    for name in ("commentary_rules.md", "mapping_workflow.md", "flags_glossary.md", "metrics_glossary.md",
                 "categories.md", "categories.yaml", "commentary_rules.yaml", "example_commentary.md"):
        p = SKILL / "reference" / name
        assert p.is_file() and p.stat().st_size > 200, name
    rules_md = (SKILL / "reference" / "commentary_rules.md").read_text(encoding="utf-8")
    for code, (name, _) in CHECKS.items():
        assert f"`{code}`" in rules_md and name in rules_md, code        # every check is explained, with how to fix it
    flags_md = (SKILL / "reference" / "flags_glossary.md").read_text(encoding="utf-8")
    for t in ("MISSING_MONTH", "NONRECURRING", "LEVEL_SHIFT", "ONE_OFF_SPIKE", "MOM_MOVE", "NEGATIVE_VALUE", "BLOCKED"):
        assert t in flags_md, t
    wf = (SKILL / "reference" / "mapping_workflow.md").read_text(encoding="utf-8")
    assert "approved_by" in wf and "approved_on" in wf and "mapping.proposed.yaml" in wf and "human" in wf.lower()


def test_templates_are_valid_and_annotated():
    from trend.config import parse_adjustments, parse_source, parse_thresholds, parse_windows
    c = yaml.safe_load((SKILL / "templates" / "company.template.yaml").read_text(encoding="utf-8"))
    parse_source(c["source"], base_dir=ROOT)
    parse_windows(c["windows"])
    parse_thresholds(c.get("thresholds", {}))
    parse_adjustments(c.get("adjustments", []))
    raw = (SKILL / "templates" / "company.template.yaml").read_text(encoding="utf-8")
    assert raw.count("#") > 10                                          # annotated
    m = yaml.safe_load((SKILL / "templates" / "mapping.template.yaml").read_text(encoding="utf-8"))
    assert isinstance(m, list) and m and set(m[0]) >= {"account", "section", "approved_by", "approved_on"}
    assert m[0]["approved_by"] is None                                  # a template is never an approval
