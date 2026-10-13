import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PS is None, reason="PowerShell not available")


def ps(script, *args):
    return subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / script), *args],
                          capture_output=True, text=True)


def test_secret_scan_clean_dir_passes(tmp_path):
    (tmp_path / "a.py").write_text("print('hello')\n")
    r = ps("secret_scan.ps1", "-Path", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("payload", [
    # built by concatenation so this file does not itself trip the scanner
    "client_secret" + " = 'abcdefghijklmnop1234567890'\n",
    "refresh_" + "token: ABCDEFGHIJKLMNOPQRSTUVWX0123456789\n",
    "-----BEGIN " + "RSA PRIVATE KEY-----\n",
    "key = sk-" + "ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA\n",
])
def test_secret_scan_detects_planted_secret(tmp_path, payload):
    (tmp_path / "leak.txt").write_text(payload)
    r = ps("secret_scan.ps1", "-Path", str(tmp_path))
    assert r.returncode != 0
    assert "leak.txt" in r.stdout
    assert "abcdefghijklmnop1234567890" not in r.stdout  # never echo the secret itself
    assert "AAAAAAAAAAAAAAAAAAAAAAAA" not in r.stdout


def test_secret_scan_flags_forbidden_filenames(tmp_path):
    (tmp_path / "tokens.json").write_text("{}")
    assert ps("secret_scan.ps1", "-Path", str(tmp_path)).returncode != 0


def test_make_zip_contains_committed_files_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (repo / "committed.txt").write_text("c")
    g("add", "committed.txt")
    g("commit", "-q", "-m", "x")
    (repo / "untracked.txt").write_text("u")
    out = tmp_path / "out.zip"
    r = ps("make_zip.ps1", "-Repo", str(repo), "-Out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    names = zipfile.ZipFile(out).namelist()
    assert "committed.txt" in names
    assert "untracked.txt" not in names


def test_make_zip_fails_without_commits(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    r = ps("make_zip.ps1", "-Repo", str(repo), "-Out", str(tmp_path / "o.zip"))
    assert r.returncode != 0
