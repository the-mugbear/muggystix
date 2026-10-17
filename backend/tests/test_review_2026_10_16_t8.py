"""Comprehensive review 2026-10-16, B-OPS-3 / T8 (owner, 2026-10-17: "those
are great features"): what was a WARNING line in a container log is also an
alert in every global administrator's bell — once a day per kind, whatever
restarts or how many workers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models_auth import User, UserRole
from app.db.models_project import Notification
from app.services import operational_alerts as OA


def _user(db, uid, name, role, active=True):
    user = User(id=uid, username=name, hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=active)
    db.add(user)
    db.flush()
    return user


def _alerts(db, kind):
    return db.query(Notification).filter(Notification.source_type == f"operational:{kind}").all()


def test_an_alert_reaches_every_active_global_admin_once_a_day(db_session):
    admin = _user(db_session, 9_700_001, "t8-admin", UserRole.ADMIN)
    _user(db_session, 9_700_002, "t8-gone", UserRole.ADMIN, active=False)
    member = _user(db_session, 9_700_003, "t8-member", UserRole.MEMBER)
    admins = {uid for (uid,) in db_session.query(User.id).filter(User.role == UserRole.ADMIN, User.is_active.is_(True))}

    snapshot = {"free_bytes": 2 * 1024 ** 3, "total_bytes": 100 * 1024 ** 3}
    assert OA.alert_disk_low(db_session, snapshot) == len(admins)
    db_session.commit()
    told = {n.user_id for n in _alerts(db_session, "disk_low")}
    assert told == admins and admin.id in told and member.id not in told
    note = _alerts(db_session, "disk_low")[0]
    assert note.type == "system" and note.project_id is None and "2.0 GB free of 100.0 GB" in note.title

    # The same kind again within the day: nothing (a restart, a second worker).
    assert OA.alert_disk_low(db_session, snapshot) == 0
    # Another kind is its own.
    assert OA.alert_ingestion_backlog(db_session, {"queued": 12, "oldest_queued_age_seconds": 1800}) == len(admins)
    db_session.commit()
    assert "12 waiting, the oldest for 30 min" in _alerts(db_session, "ingestion_backlog")[0].title
    # A day later it is said again.
    later = datetime.now(timezone.utc) + OA.ALERT_REPEAT + timedelta(minutes=1)
    assert OA.raise_alert(db_session, "disk_low", "again", "body", now=later) == len(admins)


def test_a_worker_alerts_on_another_that_went_silent_never_on_itself_or_a_busy_one(db_session):
    _user(db_session, 9_700_011, "t8-admin2", UserRole.ADMIN)
    long_silence = OA.silent_after_seconds() + 60
    reports = [
        {"name": "ingestion_jobs", "age_seconds": long_silence},          # itself: never
        {"name": "report_jobs", "age_seconds": long_silence},             # silent: alerted
        {"name": "other_jobs", "age_seconds": OA.silent_after_seconds() - 1},  # busy, not silent
    ]
    assert OA.alert_silent_workers(db_session, "ingestion_jobs", reports) > 0
    db_session.commit()
    titles = {n.title for n in db_session.query(Notification).filter(
        Notification.source_type.like("operational:worker_silent:%"))}
    assert len(titles) == 1 and next(iter(titles)).startswith("The report worker has not reported for")
    assert _alerts(db_session, "worker_silent:other_jobs") == []


def test_the_silence_threshold_outlasts_any_quiet_step():
    from app.core.config import settings
    from app.services.ingestion_stall import STALL_GRACE_S

    assert OA.silent_after_seconds() > settings.INGESTION_STALL_TIMEOUT + STALL_GRACE_S
    assert OA.silent_after_seconds() > settings.REPORT_RENDER_TIMEOUT_SECONDS
