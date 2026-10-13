"""trend_cli.py: run | check | render | list-accounts.

  run            load -> map -> reconcile -> metrics -> flags -> digest.json, metrics.json, trend_pack.xlsx
  check          load + map + reconcile only; prints the reconciliation table; writes nothing
  list-accounts  every account with section, path and mapped / unmapped status
  render         not implemented yet (a later phase)

Exit codes (run, check, list-accounts):
  0   success
  2   usage or configuration error: bad or conflicting arguments, unknown company, invalid company.yaml
  3   BLOCKED: the data failed a check (reconciliation, adapter error, unmapped or unapproved mapping).
      run then writes only blocked_report.json (and unmapped_accounts.yaml when accounts are unmapped)
  70  command not implemented (render)

Outputs go to <out>/<company_id>/<run_id>/ (default <out> = outputs/ beside companies/), never into
companies/ or the skill folder. Adds its own folder to sys.path so no package install is needed.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trend  # noqa: E402
from trend.adapters.base import AdapterError  # noqa: E402
from trend.config import ConfigError  # noqa: E402
from trend.mapping import MappingError  # noqa: E402
from trend.model import MetricsError  # noqa: E402
from trend.pipeline import (EXIT_BLOCKED, EXIT_NOT_IMPLEMENTED, EXIT_OK, EXIT_USAGE, UsageError,  # noqa: E402
                            check, format_recon_table, run)

EPILOG = """\
Exit codes:
  0  success
  2  usage or configuration error (bad arguments, unknown company, invalid company.yaml)
  3  BLOCKED: the data failed a check; run writes only blocked_report.json
  70 command not implemented (render)
"""
COMMANDS = {
    "run": "compute metrics and flags and write digest.json, metrics.json and trend_pack.xlsx",
    "check": "load and reconcile only; print the reconciliation table",
    "render": "render the commentary draft into numbers (not implemented yet)",
    "list-accounts": "list the accounts found in a company's source",
}


def build_parser():
    p = argparse.ArgumentParser(prog="trend_cli.py", description=f"Monthly trend analysis (trend {trend.__version__}).",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name, helptext in COMMANDS.items():
        sp = sub.add_parser(name, help=helptext, description=helptext, epilog=EPILOG,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
        g = sp.add_mutually_exclusive_group(required=(name != "render"))
        g.add_argument("--company", help="company id: the folder companies/<id> (found from the current folder upward)")
        g.add_argument("--company-dir", help="path to the folder holding company.yaml and mapping.yaml")
        if name == "run":
            sp.add_argument("--window", type=int, action="append", metavar="N",
                            help="trailing window length in months; repeat for several (default: company.yaml windows.lengths)")
            sp.add_argument("--window-end", metavar="YYYY-MM", help="last month of the headline window (default: latest)")
            sp.add_argument("--as-of", metavar="YYYY-MM", help="ignore every month after this one")
            sp.add_argument("--period", metavar="START:END", help="shorthand for --window <months> --window-end END")
            sp.add_argument("--out", metavar="DIR", help="output base folder (default: outputs/ beside companies/)")
    return p


def _search_roots():
    here = Path(__file__).resolve().parent
    seen, out = set(), []
    for start in (Path.cwd(), here):
        for d in (start, *start.parents):
            if d not in seen:
                seen.add(d)
                out.append(d)
    return out


def resolve_company_dir(args):
    if args.company_dir:
        return Path(args.company_dir)
    cid = args.company
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
        raise UsageError(f"invalid company id {cid!r}: use letters, digits, '_' and '-' only")
    for root in _search_roots():
        if (root / "companies" / cid / "company.yaml").is_file():
            return root / "companies" / cid
    raise UsageError(f"company {cid!r} not found: no companies/{cid}/company.yaml in the current folder, "
                     "its parents, or the repository of this script")


def cmd_run(args, company_dir):
    res = run(company_dir, as_of=args.as_of, window_lengths=args.window, window_end=args.window_end,
              period=args.period, out_base=args.out)
    if res.status == "BLOCKED":
        print(f"BLOCKED: {res.reason}: {res.message}", file=sys.stderr)
        for f in res.files:
            print(f"wrote {f}")
        return EXIT_BLOCKED
    print(f"status: OK  (run folder {res.out_dir})")
    for f in res.files:
        print(f"wrote {f} ({f.stat().st_size} bytes)")
    return EXIT_OK


def cmd_check(args, company_dir):
    res = check(company_dir)
    if res.recon is None:
        print(f"BLOCKED: {res.error}", file=sys.stderr)
        return EXIT_BLOCKED
    print(format_recon_table(res.recon))
    return EXIT_OK if res.status == "OK" else EXIT_BLOCKED


def cmd_list_accounts(args, company_dir):
    from trend.adapters.excel_pl import ExcelPLAdapter
    from trend.categories import load_vocabulary
    from trend.config import load_company
    from trend.mapping import load_mapping, mapping_status

    company = load_company(company_dir)
    try:
        records, _ = ExcelPLAdapter(company.source, company.company_id).load()
        vocab = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
        rows = mapping_status(records, load_mapping(company_dir, vocab))
    except (AdapterError, MappingError) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return EXIT_BLOCKED
    print("account | section | path | status")
    for r in rows:
        print(f"{r['account']} | {r['section']} | {' > '.join(r['path'])} | "
              f"{'mapped' if r['mapped'] else 'UNMAPPED'}")
    n_map = sum(r["mapped"] for r in rows)
    print(f"{len(rows)} accounts, {n_map} mapped, {len(rows) - n_map} unmapped")
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "render":
        print("trend_cli.py render: not implemented (a later phase)", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    try:
        company_dir = resolve_company_dir(args)
        return {"run": cmd_run, "check": cmd_check, "list-accounts": cmd_list_accounts}[args.command](args, company_dir)
    except (UsageError, ConfigError, MetricsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
