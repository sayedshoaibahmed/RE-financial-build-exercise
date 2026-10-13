"""ensure_python.py: run trend_cli.py with a Python that has the pinned packages, whatever `python` is on PATH.

    python ensure_python.py <trend_cli arguments>    run trend_cli.py with a suitable Python; its exit code is passed through
    python ensure_python.py --which                  print the interpreter that would be used (exit 0), or exit 5
    python ensure_python.py --help                   this text

Chooses, in order: the project's .venv (searched upward from this script and from the current folder), then the
interpreter running this script. A candidate is suitable when it is Python 3.11+ and has exactly the versions pinned
in requirements.txt (next to this file) and can import openpyxl and yaml. This file needs only the standard library.

Exit codes:
  0  success (or the exit code trend_cli.py returned: 2 usage/config error, 3 BLOCKED, 4 GROUNDING_FAILED)
  5  no suitable Python found; the exact command to install the packages is printed on stderr:
       pip install -r "<path>/requirements.txt"
"""
import json
import re
import subprocess
import sys
from pathlib import Path

EXIT_ENVIRONMENT = 5                     # the same value as trend.pipeline.EXIT_ENVIRONMENT
HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"

_CHECK = """
import importlib.metadata as m, json, sys
if sys.version_info < (3, 11):
    print('Python ' + sys.version.split()[0] + ' is older than 3.11')
    sys.exit(1)
bad = []
for name, want in json.loads(sys.argv[1]).items():
    try:
        got = m.version(name)
    except m.PackageNotFoundError:
        got = None
    if got != want:
        bad.append(name + ' is ' + str(got) + ', need ' + want)
if bad:
    print('; '.join(bad))
    sys.exit(1)
import openpyxl, yaml
"""


def pins():
    out = {}
    for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-+]+)", ln.split("#")[0].strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def candidates():
    seen, out = set(), []

    def add(p):
        p = Path(p)
        if p.is_file() and p.resolve() not in seen:
            seen.add(p.resolve())
            out.append(p)

    for start in (HERE, Path.cwd()):
        for d in (start, *start.parents):
            add(d / ".venv" / "Scripts" / "python.exe")
            add(d / ".venv" / "bin" / "python")
    add(sys.executable)
    return out


def suitable(python, required):
    r = subprocess.run([str(python), "-c", _CHECK, json.dumps(required)], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip().splitlines()[-1:] or ["could not run"])[0] if r.returncode else ""


def find():
    required, tried = pins(), []
    for python in candidates():
        ok, why = suitable(python, required)
        if ok:
            return python, tried
        tried.append((python, why))
    return None, tried


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    python, tried = find()
    if python is None:
        need = ", ".join(f"{n}=={v}" for n, v in pins().items())
        print(f"No usable Python found. Need Python 3.11+ with: {need}", file=sys.stderr)
        for p, why in tried:
            print(f"  tried {p}: {why}", file=sys.stderr)
        print("Install the packages with the Python you want to use, for example:\n"
              f'  pip install -r "{REQUIREMENTS}"\n'
              f'  (or: "{sys.executable}" -m pip install -r "{REQUIREMENTS}")', file=sys.stderr)
        return EXIT_ENVIRONMENT
    if argv == ["--which"]:
        print(python)
        return 0
    return subprocess.run([str(python), str(HERE / "trend_cli.py"), *argv]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
