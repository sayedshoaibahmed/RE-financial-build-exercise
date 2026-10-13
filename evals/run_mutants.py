"""run_mutants.py: mutation testing for the eval suite (PLAN 8.2, mutants M01-M12).

    python evals/run_mutants.py              every mutant, 4 at a time
    python evals/run_mutants.py M03 M07      only these mutants
    python evals/run_mutants.py --jobs 2     fewer parallel runs

For each mutant: copy the skill folder (.claude/skills/monthly-trend-analysis) to a temp folder, apply the mutation to
the COPY only (every find string must occur exactly once, or the mutant is NOT APPLIED), then run the whole eval suite
against the copy (EVALS_SKILL_DIR=<copy>, --no-baseline, results to the temp folder). A mutant is KILLED when at least
one row fails; the failing rows are its killers. A control run on an unmutated copy must pass every row first,
otherwise the kill results would mean nothing and the run stops.

The real repo is never edited. .claude/skills, companies, tests and evals/cases are hashed before and after the whole
run (__pycache__ ignored); any change fails the run loudly.

Prints a kill matrix and writes evals/results/mutants_latest.json.
Exit 0: every mutant killed except those in ACCEPTED_SURVIVORS (which must still survive). 1: any other mutant
SURVIVED, was NOT APPLIED or errored, or an accepted survivor is now killed. 3: a protected file changed.
4: the control run failed. 2: bad arguments.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "monthly-trend-analysis"
RUNNER = ROOT / "evals" / "run_evals.py"
RESULTS = ROOT / "evals" / "results" / "mutants_latest.json"
PROTECTED = [ROOT / ".claude" / "skills", ROOT / "companies", ROOT / "tests", ROOT / "evals" / "cases"]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

A = "scripts/trend/adapters/excel_pl.py"
R = "scripts/trend/reconcile.py"
M = "scripts/trend/metrics.py"
MP = "scripts/trend/mapping.py"
G = "scripts/trend/grounding.py"

# id, PLAN 8.2 wording, PLAN's expected killers, [(file in the skill folder, find, replace)]
MUTANTS = [
    ("M01", "subtotal rows loaded as records", "G01 (C6/C9), G03", [
        (A, "                add_control(r, b)\n                cur_grp = None\n",
            "                add_control(r, b)\n"
            "                for c, v in numeric_or_raise(r, b):                                      # MUTANT M01\n"
            "                    records.append(Record(self.company_id, \"excel\", cols[c], cur_sec.kind,\n"
            "                                          [cur_sec.header, b], b, money(v), self._ref(c, r)))\n"
            "                cur_grp = None\n")]),
    ("M02", "reconciliation tolerance 1e9", "G07", [
        (R, "    tolerance = Decimal(tolerance)\n", "    tolerance = Decimal(\"1e9\")                                  # MUTANT M02\n")]),
    ("M03", "MoM denominator = current month", "G01 (-5.98%)", [
        (M, "pc = (NA(\"zero_base\", \"prior value is zero\") if prior == 0\n                      else d / abs(prior) * 100)",
            "pc = (NA(\"zero_base\", \"prior value is zero\") if cur == 0\n                      else d / abs(cur) * 100)  # MUTANT M03")]),
    ("M04", "partial windows allowed", "G01 (Nov 2025 n/a), G04", [
        (M, "    if missing:\n        reason = \"insufficient_history\"",
            "    if False:                                                       # MUTANT M04\n        reason = \"insufficient_history\""),
        (M, "    return sum((series[m] for m in months), ZERO)",
            "    return sum((series[m] for m in months if m in period_set), ZERO)")]),
    ("M05", "window length ignored (always 12)", "G05", [
        (M, "_window(ser, period_set, first, t, n) for t in win_periods}",
            "_window(ser, period_set, first, t, 12) for t in win_periods}  # MUTANT M05")]),
    ("M06", "periods in sheet order (newest-first): adapter keeps sheet column order", "G01, G06", [
        (A, "order = sorted(cols, key=lambda c: cols[c])  # chronological, whatever the sheet order",
            "order = sorted(cols)  # MUTANT M06: sheet column order")]),
    ("M06b", "periods in sheet order (newest-first): adapter AND metrics keep sheet order (no sort anywhere)", "G01, G06", [
        (A, "order = sorted(cols, key=lambda c: cols[c])  # chronological, whatever the sheet order",
            "order = sorted(cols)  # MUTANT M06b: sheet column order"),
        (M, "\n    periods = sorted({m.record.period for m in mapped})\n",
            "\n    periods = list(dict.fromkeys(m.record.period for m in mapped))  # MUTANT M06b\n")]),
    ("M07", "adjusted basis not applied inside windows (adj window = rep window)", "G02, G05", [
        (M, "def _basis_facts(out, basis, A, specs, periods, lengths, end, no_cogs):",
            "def _basis_facts(out, basis, A, specs, periods, lengths, end, no_cogs, WA=None):  # MUTANT M07"),
        (M, "for t in win_periods} for key, ser in A.items()}", "for t in win_periods} for key, ser in (WA or A).items()}"),
        (M, "_basis_facts(out, basis, A, _ratio_specs(A, streams), periods, lengths, end, no_cogs)",
            "_basis_facts(out, basis, A, _ratio_specs(A, streams), periods, lengths, end, no_cogs,\n"
            "                         WA=sets[\"rep\"] if basis == \"adj\" else None)")]),
    ("M08", "unmapped accounts silently -> \"Other\"", "G08a", [
        (MP, "    missing = [(a, s, p) for a, s, p in accts if a not in mapping]\n    if missing:\n",
             "    missing = [(a, s, p) for a, s, p in accts if a not in mapping]\n"
             "    mapping = {**mapping, **{a: MappingEntry(a, s, \"other\" if s == \"opex\" else None,\n"
             "                                             \"other\" if s == \"opex\" else None, None if s == \"opex\" else a,\n"
             "                                             False, \"auto\", \"auto\") for a, s, _ in missing}}  # MUTANT M08\n"
             "    if False:\n")]),
    ("M09", "loader accepts unapproved / proposed mapping entries", "G08b", [
        (MP, "    if bad:\n        raise MappingError(\"UNAPPROVED_MAPPING\"",
             "    if False:  # MUTANT M09\n        raise MappingError(\"UNAPPROVED_MAPPING\""),
        (MP, "    if _blank(raw.get(\"approved_by\")):", "    if False:  # MUTANT M09"),
        (MP, "    if _blank(on):", "    if False:  # MUTANT M09")]),
    ("M10", "nature totals built from the function field", "G01 (Payroll & benefits anchor), C9b", [
        (MP, "MappedRecord(r, mapping[r.account].function, mapping[r.account].nature,",
             "MappedRecord(r, mapping[r.account].function, mapping[r.account].function,  # MUTANT M10")]),
    ("M11", "commentary contains a free-typed number (G2 BARE_NUMBER check disabled)", "L2 on G01", [
        (G, "        for m in re.finditer(r\"[\\d%$][\\d,.%]*\", scan):", "        for m in []:  # MUTANT M11")]),
    ("M12", "NONRECURRING bullet without adj/delta figure (adjusted-period coverage check disabled)", "L2 on G02", [
        (G, "    for a in digest.get(\"adjusted_periods\", []):\n        row = next(",
            "    for a in []:  # MUTANT M12\n        row = next(")]),
]

# Mutants allowed to survive, each with the reason no case can see it (see evals/README.md). Anything else that
# survives fails the run; so does an accepted survivor that is now killed, so this list gets cleaned up.
ACCEPTED_SURVIVORS = {
    "M06": "equivalent mutant: metrics.compute_metrics re-sorts periods itself, so record order from the adapter never "
           "reaches a number; M06b (no sort anywhere) covers the real change",
}


def tree_hash(paths):
    h = hashlib.sha256()
    n = 0
    for base in paths:
        for p in sorted(base.rglob("*")):
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts):
                h.update(str(p.relative_to(ROOT)).replace("\\", "/").encode() + b"\0" + p.read_bytes() + b"\0")
                n += 1
    return h.hexdigest(), n


def copy_skill(dst):
    shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))


def apply(skill_copy, edits):
    """-> None, or the reason the mutation cannot be applied."""
    for rel, find, repl in edits:
        f = skill_copy / rel
        if not f.is_file():
            return f"{rel} does not exist"
        s = f.read_text(encoding="utf-8")
        if s.count(find) != 1:
            return f"find string occurs {s.count(find)} times in {rel} (need exactly 1): {find.strip()[:70]!r}"
        f.write_text(s.replace(find, repl), encoding="utf-8", newline="")
    return None


def run_suite(skill_copy, work):
    env = {**os.environ, "EVALS_SKILL_DIR": str(skill_copy), "PYTHONDONTWRITEBYTECODE": "1"}
    res = work / "results.json"
    t = time.perf_counter()
    r = subprocess.run([sys.executable, str(RUNNER), "--no-baseline", "--results", str(res)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    secs = time.perf_counter() - t
    if not res.is_file():
        return None, secs, (r.stdout + r.stderr)[-800:]
    doc = json.loads(res.read_text(encoding="utf-8"))
    return [row["id"] for row in doc["rows"] if row["result"] == "FAIL"], secs, None


def run_one(mid, edits, base):
    work = base / mid
    work.mkdir()
    skill_copy = work / "skill"
    copy_skill(skill_copy)
    why = apply(skill_copy, edits)
    if why:
        return {"id": mid, "status": "NOT APPLIED", "reason": why, "killers": []}
    failing, secs, err = run_suite(skill_copy, work)
    if failing is None:
        return {"id": mid, "status": "ERROR", "reason": f"the eval runner wrote no results: {err}", "killers": [],
                "seconds": round(secs, 1)}
    return {"id": mid, "status": "KILLED" if failing else "SURVIVED", "killers": failing, "seconds": round(secs, 1),
            "reason": "" if failing else ACCEPTED_SURVIVORS.get(mid, "no row failed")}


def main(argv):
    ap = argparse.ArgumentParser(prog="run_mutants.py", description="Mutation testing for the eval suite.")
    ap.add_argument("ids", nargs="*", help="mutant ids (default: all)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel suite runs (default 4)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code
    known = {m[0] for m in MUTANTS}
    if set(args.ids) - known:
        print(f"error: unknown mutant id(s) {sorted(set(args.ids) - known)}", file=sys.stderr)
        return 2
    chosen = [m for m in MUTANTS if not args.ids or m[0] in args.ids]
    t0 = time.perf_counter()
    before, nfiles = tree_hash(PROTECTED)
    print(f"protected trees hashed before: {nfiles} files, sha256 {before[:16]}")

    with tempfile.TemporaryDirectory(prefix="mutants_") as td:
        base = Path(td)
        print("control: unmutated copy of the skill ...", flush=True)
        control = run_one("M00", [], base)
        ok = control["status"] == "SURVIVED"             # "survived" = every row passed on the unmutated copy
        print(f"[{time.perf_counter() - t0:6.1f}s] M00 control  {'every row passes' if ok else control['status']}"
              f"  failing rows: {control['killers'] or 'none'}", flush=True)
        if not ok:
            print("CONTROL FAILED: the suite does not pass on an unmutated copy, so kills would mean nothing.",
                  file=sys.stderr)
            return 4
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(run_one, mid, edits, base): mid for mid, _, _, edits in chosen}
            for f in as_completed(futs):
                r = f.result()
                results[r["id"]] = r
                print(f"[{time.perf_counter() - t0:6.1f}s] {r['id']:<5} {r['status']}", flush=True)

    after, nfiles2 = tree_hash(PROTECTED)
    print(f"protected trees hashed after:  {nfiles2} files, sha256 {after[:16]}")
    if after != before:
        print("ERROR: a protected file under .claude/skills, companies, tests or evals/cases CHANGED during the "
              "mutation run.", file=sys.stderr)
        return 3

    print("\nKILL MATRIX (killers = eval rows that failed against the mutant)")
    print(f"{'mutant':<6}  {'result':<11}  {'PLAN expects':<36}  killers / reason")
    print(f"{'-' * 6}  {'-' * 11}  {'-' * 36}  {'-' * 16}")
    for mid, what, plan, _ in chosen:
        r = results[mid]
        tail = ", ".join(r["killers"]) if r["killers"] else r["reason"]
        print(f"{mid:<6}  {r['status']:<11}  {plan[:36]:<36}  {tail}")
        print(f"{'':<6}  {'':<11}  {what}")
    count = {s: sum(r["status"] == s for r in results.values()) for s in ("KILLED", "SURVIVED", "NOT APPLIED", "ERROR")}
    elapsed = time.perf_counter() - t0
    print("\n" + ", ".join(f"{v} {k.lower()}" for k, v in count.items()) + f" of {len(chosen)} mutants; "
          f"elapsed {elapsed:.1f}s")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({"schema_version": 1, "elapsed_seconds": round(elapsed, 1),
                                   "protected_sha256": before, "control": control,
                                   "mutants": [{**results[m[0]], "plan": m[1], "plan_killers": m[2]} for m in chosen]},
                                  indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {RESULTS.relative_to(ROOT)}")

    accepted = [m for m in chosen if m[0] in ACCEPTED_SURVIVORS]
    print("\nACCEPTED SURVIVORS (allowed to survive, with reason):" + ("" if accepted else " none selected"))
    for mid, *_ in accepted:
        print(f"  {mid}  {results[mid]['status']:<11}  {ACCEPTED_SURVIVORS[mid]}")
    unexpected = [m for m, r in results.items() if r["status"] != "KILLED" and m not in ACCEPTED_SURVIVORS]
    stale = [m for m, r in results.items() if m in ACCEPTED_SURVIVORS and r["status"] != "SURVIVED"]
    if unexpected:
        print(f"FAIL: not killed and not accepted: {', '.join(unexpected)}")
    if stale:
        print(f"FAIL: accepted survivor(s) no longer survive ({', '.join(stale)}): remove them from ACCEPTED_SURVIVORS")
    if not unexpected and not stale:
        print("OK: every mutant killed except the accepted survivors")
    return 1 if unexpected or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
