"""trend_cli.py: run | check | render | list-accounts.

P0 stub: parses arguments and exits non-zero with "not implemented".
Adds its own folder to sys.path so no package install is needed.
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
        sp.add_argument("--company-dir", help="folder holding company.yaml and mapping.yaml")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    print(f"trend_cli.py {args.command}: not implemented (P0 stub)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
