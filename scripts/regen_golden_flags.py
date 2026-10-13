"""Regenerate tests/golden/abc_flags.expected.json from the real ABC workbook.

    .venv\\Scripts\\python.exe scripts\\regen_golden_flags.py           # show the differences, then rewrite the file
    .venv\\Scripts\\python.exe scripts\\regen_golden_flags.py --table   # only print the flag table; writes nothing

WARNING: the golden file is the reviewed, frozen answer. Regenerating it makes the test accept whatever the
code produces now. A HUMAN must REVIEW every ADDED / REMOVED / CHANGED line below before this is committed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".claude" / "skills" / "monthly-trend-analysis" / "scripts")]

from tests import golden_flags as g  # noqa: E402

BANNER = "!" * 78


def main(argv):
    golden = g.current_golden()
    if "--table" in argv:
        print("(table only - nothing was written)")
        print(g.format_table(golden["flags"]))
        return 0

    print(BANNER)
    print("WARNING: you are about to OVERWRITE the frozen ABC flag list.")
    print("A HUMAN must REVIEW every difference printed below before this change is committed.")
    print("Do NOT regenerate just to make a failing test pass.")
    print(BANNER)
    if g.GOLDEN.is_file():
        diffs = g.compare(json.loads(g.GOLDEN.read_text(encoding="utf-8")), golden)
        print(f"{len(diffs)} difference(s) from the existing file:" if diffs else "No differences from the existing file.")
        for line in diffs:
            print("  " + line)
    else:
        print("No existing file: creating the first version.")
    g.write(golden)
    h = golden["_header"]
    print(f"\nWrote {g.GOLDEN}\n  total={h['total']}  counts={h['counts']}")
    print(BANNER)
    print("REMINDER: this file is now only as trustworthy as the human review of the lines above.")
    print(BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
