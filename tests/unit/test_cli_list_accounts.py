import subprocess
import sys
from pathlib import Path

from tests.fixtures import builders as b
from tests.fixtures import companies as co

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / ".claude" / "skills" / "monthly-trend-analysis" / "scripts" / "trend_cli.py"


def run(*args, cwd):
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, capture_output=True, text=True)


def test_list_accounts_shows_section_path_and_status(tmp_path):
    cdir = co.write_company(tmp_path, mapping=lambda: [e for e in co.entries() if e["account"] != "Rent"])
    r = run("list-accounts", "--company-dir", str(cdir), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    lines = {ln.split("|")[0].strip(): ln for ln in r.stdout.splitlines() if "|" in ln}
    assert "UNMAPPED" in lines["Rent"] and "opex" in lines["Rent"]
    assert "Operating Expenses > Payroll > Salaries" in lines["Salaries"] and "mapped" in lines["Salaries"]
    assert "UNMAPPED" not in lines["Salaries"]
    assert "Fees/Misc: Setup" in lines  # label printed unchanged
    assert "7 accounts, 6 mapped, 1 unmapped" in r.stdout


def test_list_accounts_with_no_mapping_yaml_lists_everything_unmapped(tmp_path):
    cdir = co.write_company(tmp_path, mapping=None)
    r = run("list-accounts", "--company-dir", str(cdir), cwd=tmp_path)
    assert r.returncode == 0
    assert "7 accounts, 0 mapped, 7 unmapped" in r.stdout
    assert not (cdir / "mapping.yaml").exists() and not (cdir / "mapping.proposed.yaml").exists()


def test_list_accounts_reports_an_unapproved_mapping_as_an_error(tmp_path):
    def mk():
        es = co.entries()
        es[3]["approved_by"] = None
        return es
    cdir = co.write_company(tmp_path, mapping=mk)
    r = run("list-accounts", "--company-dir", str(cdir), cwd=tmp_path)
    assert r.returncode == 3 and "UNAPPROVED_MAPPING" in r.stderr          # BLOCKED-class error: exit 3


def test_list_accounts_reports_adapter_errors(tmp_path):
    rows = b.default_rows()
    rows.insert(2, [None, "Total Widgets", [1, 1, 1]])
    cdir = co.write_company(tmp_path, rows=rows)
    r = run("list-accounts", "--company-dir", str(cdir), cwd=tmp_path)
    assert r.returncode == 3 and "UNDECLARED_TOTAL" in r.stderr


def test_list_accounts_without_company_dir_or_with_bad_dir(tmp_path):
    assert run("list-accounts", cwd=tmp_path).returncode == 2  # argparse: required
    r = run("list-accounts", "--company-dir", str(tmp_path / "nope"), cwd=tmp_path)
    assert r.returncode == 2 and "company.yaml" in r.stderr                # config error: exit 2


def test_render_is_no_longer_a_stub(tmp_path):
    r = run("render", "--company-dir", "x", cwd=tmp_path)
    assert r.returncode == 2 and "not implemented" not in r.stderr.lower() and "no run folder" in r.stderr.lower()
