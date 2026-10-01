#!/usr/bin/env python3
"""Correct NetExec rows stored by older parser rules.  Dry run unless --apply; idempotent.

  * before v2.428.4: command/module results stored as logins, hosts named "None";
  * before v2.430.1: the NFS mount daemon's ports (nxc logs NFS lines with
    them) stored as an "nfs" service — renamed "mountd" when only NetExec
    named them.

    docker compose exec backend python scripts/repair_netexec_results.py [--project ID] [--apply]

See app/services/netexec_repair.py for the rules (they only clear or correct
what NetExec itself wrote; another tool's identification is never touched).

The data-repair ledger (``data_repairs``; ``scripts/data_repairs.py`` shows
it) records the run when it settles the repair for the whole instance: an
``--apply`` over every project, or a dry run over every project that found
nothing to correct.  A ``--project`` run, or a dry run that found something,
is not recorded.
"""
import argparse
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.services.data_repair_service import (  # noqa: E402
    MODE_APPLY, MODE_NOTHING_TO_REPAIR, record_run,
)
from app.services.netexec_repair import repair_netexec_results, repair_nfs_mount_ports  # noqa: E402

_REPAIR = "netexec_results_repair"
_BY = "scripts/repair_netexec_results.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=int, default=None, help="only this project id")
    parser.add_argument("--apply", action="store_true", help="write the corrections (default: report only)")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        counts = repair_netexec_results(db, project_id=args.project, apply=args.apply)
        counts.update(repair_nfs_mount_ports(db, project_id=args.project, apply=args.apply))
        total = sum(n for n in counts.values() if isinstance(n, int))
        recorded = None
        if args.project is None:
            if args.apply:
                record_run(db, _REPAIR, applied_by=_BY, mode=MODE_APPLY, rows_affected=counts)
                recorded = "applied"
            elif total == 0:
                # Nothing to correct anywhere: the same end state as an apply.
                # Discard whatever the dry run touched, then write only the
                # ledger row.
                db.rollback()
                record_run(db, _REPAIR, applied_by=_BY, mode=MODE_NOTHING_TO_REPAIR,
                           rows_affected=counts)
                recorded = "nothing to repair"
        if args.apply or recorded:
            db.commit()
    finally:
        db.close()
    verb = "Corrected" if args.apply else "Would correct (dry run; pass --apply)"
    print(f"{verb}:")
    for key, n in counts.items():
        print(f"  {key}: {n}")
    if recorded:
        print(f"Recorded in the data-repair ledger as '{recorded}' (scripts/data_repairs.py).")
    elif args.project is not None:
        print("Not recorded in the data-repair ledger: limited to one project.")
    else:
        print("Not recorded in the data-repair ledger: this was a dry run that found rows to correct. "
              "Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
