"""trend_cli.py: run | check | render | list-accounts.

Only list-accounts is implemented (P3); the others are stubs that exit non-zero with
"not implemented". Adds its own folder to sys.path so no package install is needed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trend  # noqa: E402

COMMANDS = {
    "run": "run the pipeline for a company",
    "check": "check a commentary draft against metrics",
    "render": "render the commentary draft into numbers",
    "list-accounts": "list the accounts found in a company's source",
}


def build_parser():
    p = argparse.ArgumentParser(prog="trend_cli.py",
                                description=f"Monthly trend analysis (trend {trend.__version__}).")
    sub = p.add_subparsers(dest="command", required=True)
    for name, helptext in COMMANDS.items():
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--company-dir", required=(name == "list-accounts"),
                        help="folder holding company.yaml and mapping.yaml")
    return p


def list_accounts(company_dir):
    from trend.adapters.base import AdapterError
    from trend.adapters.excel_pl import ExcelPLAdapter
    from trend.categories import load_vocabulary
    from trend.config import ConfigError, load_company
    from trend.mapping import MappingError, load_mapping, mapping_status

    try:
        company = load_company(company_dir)
        records, _ = ExcelPLAdapter(company.source, company.company_id).load()
        vocab = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
        rows = mapping_status(records, load_mapping(company_dir, vocab))
    except (ConfigError, AdapterError, MappingError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("account | section | path | status")
    for r in rows:
        print(f"{r['account']} | {r['section']} | {' > '.join(r['path'])} | "
              f"{'mapped' if r['mapped'] else 'UNMAPPED'}")
    n_map = sum(r["mapped"] for r in rows)
    print(f"{len(rows)} accounts, {n_map} mapped, {len(rows) - n_map} unmapped")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "list-accounts":
        return list_accounts(args.company_dir)
    print(f"trend_cli.py {args.command}: not implemented (P0 stub)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
