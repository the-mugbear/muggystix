#!/usr/bin/env python3
"""Record misconfiguration-catalog observations from evidence already stored
(v2.414.0).  Idempotent: a second run changes nothing.

    docker compose exec backend python scripts/backfill_misconfigs.py [--project ID]

See app/services/misconfig_backfill.py for what it can and cannot rebuild.

A run over every project is recorded in the data-repair ledger
(``data_repairs``; ``scripts/data_repairs.py`` shows it), in the same
transaction as the observations.  A ``--project`` run is not: it does not
settle the repair for the instance.
"""
import argparse
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.services.data_repair_service import record_run  # noqa: E402
from app.services.misconfig_backfill import UNMERGED_KEY, backfill_misconfigs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=int, default=None, help="only this project id")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        unmerged: list = []
        counts = backfill_misconfigs(db, project_id=args.project, unmerged=unmerged)
        if args.project is None:
            record_run(db, "misconfig_backfill", applied_by="scripts/backfill_misconfigs.py",
                       rows_affected=counts)
        db.commit()
    finally:
        db.close()
    observations = {k: n for k, n in counts.items() if k != UNMERGED_KEY}
    total = sum(observations.values())
    print(f"Recorded or refreshed {total} observation(s)")
    for check_id, n in sorted(observations.items()):
        print(f"  {check_id}: {n}")
    if unmerged:
        # Two scanner findings for one catalog check in a project.  Nothing is
        # merged here: which finding's text, status and history is kept is a
        # person's decision.  Reported by every run until it is settled.
        print(f"\n{len(unmerged)} finding(s) NOT moved onto their catalog check: "
              "the project already has a finding for that check.")
        print("Each pair is one weakness recorded as two findings. Merge them by hand "
              "(move the endpoints and text you want onto one, delete the other), then run this again.")
        for pair in unmerged:
            print(f"  project {pair['project_id']}: finding #{pair['finding_id']} left as it is; "
                  f"finding #{pair['kept_finding_id']} is the one for check {pair['check_id']}")
    if args.project is None:
        print("Recorded in the data-repair ledger (scripts/data_repairs.py).")
    else:
        print("Not recorded in the data-repair ledger: limited to one project. "
              "Run without --project to settle it for the instance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
