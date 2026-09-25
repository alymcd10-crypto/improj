"""Command-line runner (optional). Same logic as the app; outputs go to data/runs/.

  python cli.py reconcile --worklist W.csv --export TP.csv [--master M.xlsx] [--audit A.xlsx] [--corrections C.csv]
  python cli.py verify --pre PRE.csv --completed DONE.csv --post POST.csv
"""
from __future__ import annotations

import argparse
import os
import sys

from recon.pipeline import InputFile, run_reconciliation, run_verification


def _file(path, role):
    if not path:
        return None
    with open(path, "rb") as fh:  # read-only; the source file is never modified
        return InputFile(role, os.path.basename(path), fh.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LMT CRM reconciliation (local only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("reconcile")
    a.add_argument("--worklist", required=True)
    a.add_argument("--export", required=True)
    a.add_argument("--master")
    a.add_argument("--audit")
    a.add_argument("--corrections")
    b = sub.add_parser("verify")
    b.add_argument("--pre", required=True)
    b.add_argument("--completed", required=True)
    b.add_argument("--post", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "reconcile":
            out = run_reconciliation(_file(args.worklist, "worklist"), _file(args.export, "export"),
                                     _file(args.master, "master"), _file(args.audit, "audit"),
                                     _file(args.corrections, "corrections"))
        else:
            out = run_verification(_file(args.pre, "pre"), _file(args.completed, "completed"), _file(args.post, "post"))
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for k, v in out.counts.items():
        if not isinstance(v, dict):
            print(f"{k}: {v}")
    for name, ok in out.checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"Saved in {out.run_dir}")
    return 0 if all(ok for _, ok in out.checks) else 1


if __name__ == "__main__":
    sys.exit(main())
