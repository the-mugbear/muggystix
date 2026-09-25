#!/usr/bin/env python3
"""Record misconfiguration-catalog observations from evidence already stored
(v2.414.0).  Idempotent: a second run changes nothing.

    docker compose exec backend python scripts/backfill_misconfigs.py [--project ID]

See app/services/misconfig_backfill.py for what it can and cannot rebuild.
"""
import argparse
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.services.misconfig_backfill import backfill_misconfigs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=int, default=None, help="only this project id")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        counts = backfill_misconfigs(db, project_id=args.project)
        db.commit()
    finally:
        db.close()
    total = sum(counts.values())
    print(f"Recorded or refreshed {total} observation(s)")
    for check_id, n in sorted(counts.items()):
        print(f"  {check_id}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
