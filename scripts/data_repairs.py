#!/usr/bin/env python3
"""Show the data-repair ledger: which one-off data corrections this instance has run.

    docker compose exec backend python scripts/data_repairs.py            # every repair and its last run
    docker compose exec backend python scripts/data_repairs.py --pending  # only those not yet applied
    docker compose exec backend python scripts/data_repairs.py --json

Read-only: it runs nothing.  Each pending repair is printed with the exact
command that applies it (and, for one that changes rows, the dry run to read
first).  ``deploy.sh`` and ``status.sh`` call ``--pending`` after a healthy
start.  Exit status is 0 either way — a pending repair is a reminder, not a
failure.

See app/services/data_repair_service.py for the list and the rules.
"""
import argparse
import json
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.services.data_repair_service import ledger  # noqa: E402


def _print_pending(rows) -> None:
    for row in rows:
        print(f"{row['name']}: {row['summary']}")
        if row["destructive"]:
            print(f"    dry run: {row['preview_command']}")
        print(f"    apply:   {row['command']}")


def _print_all(rows) -> None:
    for row in rows:
        if row["applied"]:
            counts = row["rows_affected"] or {}
            total = sum(v for v in counts.values() if isinstance(v, int))
            state = (
                f"{row['mode']} {row['applied_at']} by {row['applied_by'] or 'unknown'}"
                f" (version {row['app_version'] or 'unknown'}, {row['run_count']} run(s), {total} row(s))"
            )
        else:
            state = "NOT YET APPLIED"
        print(f"{row['name']}: {state}")
        print(f"    {row['summary']}")
        if not row["applied"]:
            if row["destructive"]:
                print(f"    dry run: {row['preview_command']}")
            print(f"    apply:   {row['command']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pending", action="store_true", help="only repairs with no recorded run")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        rows = ledger(db)
    finally:
        db.close()
    if args.pending:
        rows = [row for row in rows if not row["applied"]]
    if args.json:
        print(json.dumps(rows, indent=2))
    elif args.pending:
        _print_pending(rows)
    else:
        _print_all(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
