"""Known data repairs and the ledger of their runs (review 2026-10-01 B10).

A data repair corrects rows that OLDER code wrote.  It is not a migration
(the schema does not change, and one of them deletes rows, so it must not run
unasked at boot) — which meant it lived only as a paragraph in
``scripts/README.md`` and an upgrade could cross two of them without anyone
being told.  This module is the list, and ``data_repairs`` is the record:

* ``KNOWN_REPAIRS`` — every repair, with the exact command that runs it;
* ``pending`` — the ones this database has no recorded run of, which
  ``scripts/deploy.sh`` and ``scripts/status.sh`` print after a healthy start;
* ``record_run`` — called by the repair's own script when a run settled it.

Nothing here runs a repair.  Each stays a deliberate operator action:

* the misconfiguration backfill is idempotent and only adds observations,
  but it walks every NetExec row, port script and host script and writes
  through the application-level vulnerability dedup (no database constraint
  backs it), so running it beside a live import could record an observation
  twice.  It is therefore not started from the ingestion worker;
* the NetExec repair deletes and rewrites rows — dry run by default, applied
  only with ``--apply``.

A run limited to one project (``--project``) is not recorded: it does not
settle the repair for the instance.

Adding a repair: append it to ``KNOWN_REPAIRS`` (the name is permanent), have
its script call ``record_run``, and document it in ``scripts/README.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models_data_repairs import DataRepairRun

MODE_APPLY = "apply"
MODE_NOTHING_TO_REPAIR = "nothing_to_repair"
MODE_NOT_NEEDED = "not_needed"
_MODES = (MODE_APPLY, MODE_NOTHING_TO_REPAIR, MODE_NOT_NEEDED)


@dataclass(frozen=True)
class DataRepair:
    name: str
    summary: str
    #: The backend version whose parser/logic change made the repair necessary
    #: for data imported before it.
    since: str
    #: True when a run changes or deletes existing rows (a dry run comes first).
    destructive: bool
    #: What to run to see what it would do (same as ``command`` when the
    #: repair has no dry run).
    preview_command: str
    #: What to run to settle it.
    command: str


KNOWN_REPAIRS: tuple = (
    DataRepair(
        name="misconfig_backfill",
        summary=(
            "Record misconfiguration-catalog observations for scans imported "
            "before v2.414.0 (idempotent; adds observations, changes no existing row)."
        ),
        since="2.414.0",
        destructive=False,
        preview_command="docker compose exec backend python scripts/backfill_misconfigs.py",
        command="docker compose exec backend python scripts/backfill_misconfigs.py",
    ),
    DataRepair(
        name="netexec_results_repair",
        summary=(
            "Correct NetExec rows stored by older parser rules: command/module "
            "results kept as logins, hosts named \"None\" (before v2.428.4), and "
            "NFS mount-daemon ports named \"nfs\" (before v2.430.1). Deletes and "
            "rewrites rows — read the dry run first."
        ),
        since="2.430.1",
        destructive=True,
        preview_command="docker compose exec backend python scripts/repair_netexec_results.py",
        command="docker compose exec backend python scripts/repair_netexec_results.py --apply",
    ),
)


def known_repair(name: str) -> DataRepair:
    for repair in KNOWN_REPAIRS:
        if repair.name == name:
            return repair
    raise KeyError(f"Unknown data repair {name!r}; add it to KNOWN_REPAIRS first.")


def recorded_runs(db: Session) -> Dict[str, DataRepairRun]:
    return {row.name: row for row in db.query(DataRepairRun).all()}


def pending(db: Session) -> List[DataRepair]:
    """Known repairs this database has no recorded run of, in list order."""
    done = recorded_runs(db)
    return [repair for repair in KNOWN_REPAIRS if repair.name not in done]


def record_run(
    db: Session,
    name: str,
    *,
    applied_by: str,
    mode: str = MODE_APPLY,
    rows_affected: Optional[Dict[str, Any]] = None,
) -> DataRepairRun:
    """Record that ``name`` was settled.  One row per repair: a repeat run
    updates it and counts.  Flushes; the caller commits — in the SAME
    transaction as the repair's writes, so a rolled-back repair is never
    recorded as applied."""
    from app.core.config import settings

    known_repair(name)  # an unknown name is a programming error, not a row
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, not {mode!r}")
    row = db.query(DataRepairRun).filter(DataRepairRun.name == name).first()
    if row is None:
        row = DataRepairRun(name=name, run_count=1)
        db.add(row)
    else:
        row.run_count = (row.run_count or 0) + 1
    row.applied_at = datetime.now(timezone.utc)
    row.applied_by = applied_by
    row.mode = mode
    row.rows_affected = rows_affected
    row.app_version = getattr(settings, "APP_VERSION", None)
    db.flush()
    return row


def ledger(db: Session) -> List[Dict[str, Any]]:
    """Every known repair with its recorded run (or none) — what
    ``scripts/data_repairs.py`` prints.  Counts only; no row values."""
    done = recorded_runs(db)
    out: List[Dict[str, Any]] = []
    for repair in KNOWN_REPAIRS:
        run = done.get(repair.name)
        out.append({
            "name": repair.name,
            "summary": repair.summary,
            "since": repair.since,
            "destructive": repair.destructive,
            "preview_command": repair.preview_command,
            "command": repair.command,
            "applied": run is not None,
            "applied_at": run.applied_at.isoformat() if run and run.applied_at else None,
            "applied_by": run.applied_by if run else None,
            "mode": run.mode if run else None,
            "rows_affected": run.rows_affected if run else None,
            "app_version": run.app_version if run else None,
            "run_count": run.run_count if run else 0,
        })
    return out
