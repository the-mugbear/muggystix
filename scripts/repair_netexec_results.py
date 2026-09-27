#!/usr/bin/env python3
"""Correct NetExec rows stored before v2.428.4: command/module results stored
as logins, and hosts named "None".  Dry run unless --apply; idempotent.

    docker compose exec backend python scripts/repair_netexec_results.py [--project ID] [--apply]

See app/services/netexec_repair.py for the rule (it only clears, never invents).
"""
import argparse
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.services.netexec_repair import repair_netexec_results  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=int, default=None, help="only this project id")
    parser.add_argument("--apply", action="store_true", help="write the corrections (default: report only)")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        counts = repair_netexec_results(db, project_id=args.project, apply=args.apply)
        if args.apply:
            db.commit()
    finally:
        db.close()
    verb = "Corrected" if args.apply else "Would correct (dry run; pass --apply)"
    print(f"{verb}:")
    for key, n in counts.items():
        print(f"  {key}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
