import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PS is None, reason="PowerShell not available")

# Obviously fake; built by concatenation so this file does not itself trip the scanner.
FAKE = "client_secret" + " = 'FAKEFAKEFAKEFAKEFAKE1234'\n"


def scan(folder):
    return subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "secret_scan.ps1"), "-Path", str(folder)],
                          capture_output=True, text=True)


def put(base, rel, text):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_fake_assignment_outside_allowlist_is_caught(tmp_path):
    put(tmp_path, "src/app.py", FAKE)
    r = scan(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "credential-assign" in r.stdout
    assert "FAKEFAKE" not in r.stdout


def test_fake_assignment_in_connector_non_tests_is_caught(tmp_path):
    put(tmp_path, "connector/src/app.py", FAKE)
    assert scan(tmp_path).returncode == 1


def test_fake_assignment_under_connector_tests_not_flagged(tmp_path):
    put(tmp_path, "connector/tests/test_x.py", FAKE)
    r = scan(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "credential-assign" not in r.stdout


def test_other_rules_still_active_under_connector_tests(tmp_path):
    put(tmp_path, "connector/tests/test_x.py", "-----BEGIN " + "RSA PRIVATE KEY-----\n")
    r = scan(tmp_path)
    assert r.returncode == 1
    assert "private-key" in r.stdout


def test_dot_env_is_caught(tmp_path):
    put(tmp_path, ".env", "")
    r = scan(tmp_path)
    assert r.returncode == 1
    assert "FORBIDDEN-NAME" in r.stdout


def test_dot_env_example_is_not_flagged(tmp_path):
    put(tmp_path, ".env.example", "")
    put(tmp_path, "connector/.env.example", "")
    r = scan(tmp_path)
    assert r.returncode == 0, r.stdout
