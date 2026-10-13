import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PS is None, reason="PowerShell not available")


def check(zip_path):
    return subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "check_zip.ps1"), "-Zip", str(zip_path)],
                          capture_output=True, text=True)


def make(tmp_path, names):
    z = tmp_path / "t.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return z


def test_clean_zip_passes(tmp_path):
    r = check(make(tmp_path, ["README.md", "connector/.env.example", "src/a.py"]))
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("bad", [
    ".env", "connector/.env", ".venv/x.py", "connector/.venv/Lib/x.py", "outputs/a.xlsx",
    "evals/results/r.json", ".git/config", "src/__pycache__/a.pyc", "tokens.json",
    "connector/tokens.json", "a/b.token",
])
def test_forbidden_entry_fails(tmp_path, bad):
    r = check(make(tmp_path, ["README.md", bad]))
    assert r.returncode == 1, r.stdout
    assert "FORBIDDEN-ENTRY" in r.stdout
