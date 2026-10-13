import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "monthly-trend-analysis"
CLI = SKILL / "scripts" / "trend_cli.py"


def test_trend_package_imports():
    import trend
    import trend.adapters
    import trend.writers
    assert trend.__version__


def test_cli_help_from_another_working_directory(tmp_path):
    r = subprocess.run([sys.executable, str(CLI), "--help"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for cmd in ("run", "check", "render", "list-accounts"):
        assert cmd in r.stdout


def test_cli_stub_subcommand_is_not_implemented(tmp_path):
    r = subprocess.run([sys.executable, str(CLI), "render", "--company-dir", "x"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode != 0
    assert "not implemented" in (r.stderr + r.stdout).lower()


def test_layout_matches_plan_section_2():
    expected = [
        "PLAN.md", "pytest.ini", "requirements-dev.txt", ".gitignore",
        "tests/oracle.py", "scripts/secret_scan.ps1", "scripts/make_zip.ps1",
        ".claude/skills/monthly-trend-analysis/SKILL.md",
        ".claude/skills/monthly-trend-analysis/scripts/requirements.txt",
        ".claude/skills/monthly-trend-analysis/scripts/trend_cli.py",
        ".claude/skills/monthly-trend-analysis/scripts/trend/__init__.py",
        ".claude/skills/monthly-trend-analysis/scripts/trend/adapters/__init__.py",
        ".claude/skills/monthly-trend-analysis/scripts/trend/writers/__init__.py",
    ]
    missing = [p for p in expected if not (ROOT / p).exists()]
    assert not missing, missing


def test_gitignore_has_required_entries():
    lines = {l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()}
    for entry in (".env", "tokens*", "outputs/", "inputs/qbo/", ".venv/", "*.log"):
        assert entry in lines, entry


def test_skill_md_frontmatter_and_size():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: monthly-trend-analysis" in head
    assert "description:" in head
    assert len(text.splitlines()) < 500


def test_pinned_requirements():
    for f in (SKILL / "scripts" / "requirements.txt", ROOT / "requirements-dev.txt"):
        for line in f.read_text().splitlines():
            if line and not line.startswith("-r"):
                assert "==" in line, (f.name, line)
