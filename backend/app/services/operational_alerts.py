"""Operational alerts: what is wrong with the installation, in the bell of
every global administrator (comprehensive review 2026-10-16, B-OPS-3; owner,
2026-10-17: "those are great features").

Each check below already decided "this is wrong" — and said so only as a
WARNING line in a container log nobody tails.  ``raise_alert`` turns that
decision into a notification, once per kind per ``ALERT_REPEAT``: the throttle
is read from the notifications themselves, so it holds across restarts and
any number of workers.  Installation-wide (``project_id`` null,
``type='system'``), so no project's webhook carries it.

Kinds (``source_type`` = ``operational:<kind>``):

* ``disk_low`` — the uploads disk is below its floor (``warn_if_disk_low``);
* ``ingestion_backlog`` — the import queue is backing up
  (``warn_if_ingestion_backlog_high``);
* ``worker_silent:<name>`` — another worker has not reported for longer than
  any step may legitimately take (``silent_after_seconds``; each worker
  checks the others — one cannot report its own silence).

Not here, and why: an expiring certificate (the workers do not mount
``ssl/``; ``status.sh`` says it — review N16) and a failed migration (the
application is not running to say anything; ``deploy.sh`` and
``migration-failure-report.sh`` do).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.db.models_auth import User, UserRole
from app.db.models_project import Notification

logger = logging.getLogger(__name__)

ALERT_REPEAT = timedelta(hours=24)
SOURCE_PREFIX = "operational:"


def _global_admin_ids(db: Session) -> list[int]:
    return [uid for (uid,) in db.query(User.id).filter(User.role == UserRole.ADMIN, User.is_active.is_(True))]


def raise_alert(db: Session, kind: str, title: str, body: str, *, now: datetime | None = None) -> int:
    """Tell every active global administrator, unless this kind was told in
    the last ``ALERT_REPEAT``.  Returns how many notifications were added
    (the caller commits)."""
    source_type = f"{SOURCE_PREFIX}{kind}"[:50]
    since = (now or datetime.now(timezone.utc)) - ALERT_REPEAT
    already = db.query(exists().where(
        Notification.source_type == source_type, Notification.created_at >= since,
    )).scalar()
    if already:
        return 0
    admins = _global_admin_ids(db)
    for uid in admins:
        db.add(Notification(
            user_id=uid, project_id=None, type="system", title=title[:255], body=body,
            source_type=source_type, source_id=None, actor_id=None,
        ))
    if admins:
        logger.warning("Operational alert %s sent to %d administrator(s): %s", kind, len(admins), title)
    return len(admins)


def alert_disk_low(db: Session, snapshot: dict) -> int:
    free_gb = snapshot["free_bytes"] / 1024 ** 3
    total_gb = snapshot["total_bytes"] / 1024 ** 3
    return raise_alert(
        db, "disk_low", f"Disk space is low: {free_gb:.1f} GB free of {total_gb:.1f} GB",
        "Imports and the database share this disk; when it fills, the database stops. Free space "
        "(old backups, Docker build cache) or lower INGESTION_RETAIN_FILES_DAYS. Uploads that would "
        "leave less than the reserve free are already refused.",
    )


def alert_ingestion_backlog(db: Session, snapshot: dict) -> int:
    age_min = int((snapshot.get("oldest_queued_age_seconds") or 0) // 60)
    return raise_alert(
        db, "ingestion_backlog",
        f"The import queue is backing up: {snapshot.get('queued', 0)} waiting"
        + (f", the oldest for {age_min} min" if age_min else ""),
        "Files are arriving faster than the import worker finishes them, or one import keeps "
        "coming back. System settings shows the queue; a second worker (docker compose up -d "
        "--scale worker=2) drains it faster.",
    )


#: The queue a worker serves (its report's name) → what people call it.
WORKER_LABELS = {"ingestion_jobs": "import", "report_jobs": "report"}


def silent_after_seconds() -> float:
    """How long a worker may go without reporting before it is alerted on:
    longer than any step that legitimately reports nothing — an import step
    up to the stall window and its grace, a report render up to its timeout
    — so an alert is never raised over a worker that is merely busy.  (The
    System page's "not reporting", two minutes, says it sooner, as a state.)"""
    from app.core.config import settings
    from app.services.ingestion_stall import STALL_GRACE_S

    return max(settings.INGESTION_STALL_TIMEOUT + STALL_GRACE_S, settings.REPORT_RENDER_TIMEOUT_SECONDS) + 300


def alert_silent_workers(db: Session, own_name: str, reports: Iterable[dict]) -> int:
    """Each worker checks the others' reports (``resource_metrics.worker_reports``)."""
    sent = 0
    threshold = silent_after_seconds()
    for report in reports:
        if report.get("name") == own_name or float(report.get("age_seconds", 0)) <= threshold:
            continue
        minutes = int(report.get("age_seconds", 0) // 60)
        label = WORKER_LABELS.get(report["name"], report["name"])
        sent += raise_alert(
            db, f"worker_silent:{report['name']}",
            f"The {label} worker has not reported for {minutes} min",
            "It was stopped, killed (out of memory?) or is stuck: the jobs it runs wait. System settings "
            "shows every worker; `docker compose ps` and `docker compose logs` on the host say why.",
        )
    return sent
