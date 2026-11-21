"""The owner's decisions of 2026-11-21 on the 2026-11-20 review's open questions.

* A presentation agenda's edits are lines of the project's changelog;
* a notification is removed a week after it was read (never an unread one);
* the application role's password is re-applied only when the sign-in was
  REFUSED, not for any failed sign-in (``scripts/postgres/app-role.sh``).

(The remediation list's ``over-30`` filter is in ``test_remediation_deadlines.py``.)
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import startup
from app.core.config import settings
from app.db.models_project import Notification, ProjectEvent
from app.db.models_reports import Report

HERE = Path(__file__).resolve()
APP_ROLE = next(
    # Where test_ops_scripts.py finds the scripts: beside the tests' parent
    # (the gate's mount) or at the repository root.
    (p for p in (HERE.parents[1] / "scripts" / "postgres" / "app-role.sh",
                 HERE.parents[2] / "scripts" / "postgres" / "app-role.sh")
     if p.is_file()),
    None,
)


# --- presentation agendas in the changelog ------------------------------------

@pytest.fixture
def report(db_session, test_project, test_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    dataset = {
        "report": {"title": "Internal assessment", "kind": "full", "draft": False},
        "project": {"name": test_project.name}, "scope": {"sites": [], "segments": []},
        "summary": {}, "findings": [], "executive_summary": None,
    }
    row = Report(project_id=test_project.id, kind="full", status="issued", title="Internal assessment",
                 template="pentest", number=1, snapshot={"dataset": dataset}, created_by_id=test_user.id)
    db_session.add(row)
    db_session.commit()
    return row


def _agenda_lines(db, project_id):
    return [(e.action, e.subject_type, e.subject_id, e.subject_label)
            for e in db.query(ProjectEvent).filter(ProjectEvent.project_id == project_id)
            .filter(ProjectEvent.action.like("presentation_agenda_%")).order_by(ProjectEvent.id)]


def test_making_changing_and_deleting_an_agenda_are_changelog_lines(client, db_session, test_project, report):
    url = f"/api/v1/projects/{test_project.id}/client-reports/{report.id}/presentation/agendas"
    made = client.post(url, json={"name": "Walkthrough", "items": ["overview"]})
    assert made.status_code == 201, made.text
    agenda = made.json()
    assert _agenda_lines(db_session, test_project.id) == [
        ("presentation_agenda_created", "report", report.id, "Walkthrough")]

    # A save that changes nothing makes no version and no line…
    same = client.put(f"{url}/{agenda['id']}", json={"name": "Walkthrough", "items": ["overview"],
                                                    "expected_version": 1})
    assert same.status_code == 200 and same.json()["version"] == 1
    assert len(_agenda_lines(db_session, test_project.id)) == 1
    # …a rename does.
    renamed = client.put(f"{url}/{agenda['id']}", json={"name": "Short", "items": ["overview"],
                                                       "expected_version": 1})
    assert renamed.json()["version"] == 2
    assert _agenda_lines(db_session, test_project.id)[-1] == (
        "presentation_agenda_changed", "report", report.id, "Short")

    assert client.delete(f"{url}/{agenda['id']}").status_code in (200, 204)
    assert _agenda_lines(db_session, test_project.id)[-1] == (
        "presentation_agenda_deleted", "report", report.id, "Short")
    assert len(_agenda_lines(db_session, test_project.id)) == 3


# --- read notifications are removed after a week --------------------------------

def _notification(db, user, project, title, *, read_days_ago=None, created_days_ago=0, read_at=True):
    now = datetime.now(timezone.utc)
    row = Notification(
        user_id=user.id, project_id=project.id, type="system", title=title,
        is_read=read_days_ago is not None,
        read_at=(now - timedelta(days=read_days_ago)) if read_days_ago is not None and read_at else None,
        created_at=now - timedelta(days=created_days_ago),
    )
    db.add(row)
    return row


def test_only_notifications_read_more_than_a_week_ago_are_removed(db_session, test_project, test_user, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_READ_RETENTION_DAYS", raising=False)
    assert startup.notification_read_retention_days() == 7
    _notification(db_session, test_user, test_project, "read long ago", read_days_ago=30, created_days_ago=40)
    _notification(db_session, test_user, test_project, "read yesterday", read_days_ago=1, created_days_ago=40)
    _notification(db_session, test_user, test_project, "unread and old", created_days_ago=400)
    _notification(db_session, test_user, test_project, "read, undated, old", read_days_ago=9, created_days_ago=20,
                  read_at=False)
    db_session.commit()

    removed = startup.purge_read_notifications(db_session, startup.notification_read_retention_days())

    assert removed == 2
    kept = {title for (title,) in db_session.query(Notification.title)
            .filter(Notification.project_id == test_project.id)}
    assert kept == {"read yesterday", "unread and old"}


def test_zero_keeps_read_notifications(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_READ_RETENTION_DAYS", "0")
    assert startup.notification_read_retention_days() == 0


# --- the application role is repaired only when its sign-in was refused ---------

@pytest.mark.skipif(APP_ROLE is None, reason="scripts/ is not mounted here")
@pytest.mark.parametrize("message, refused", [
    ('psql: error: connection to server at "172.18.0.2", port 5432 failed: FATAL:  password authentication failed for user "bluestick_app"', True),
    ('psql: error: FATAL:  role "bluestick_app" does not exist', True),
    ('psql: error: FATAL:  no pg_hba.conf entry for host "172.18.0.2", user "bluestick_app"', True),
    ("psql: error: FATAL:  sorry, too many clients already", False),
    ("psql: error: FATAL:  remaining connection slots are reserved for roles with the SUPERUSER attribute", False),
    ("psql: error: timeout expired", False),
    ("psql: error: FATAL:  the database system is starting up", False),
    ("", False),
])
def test_what_counts_as_a_refused_sign_in(message, refused):
    source = APP_ROLE.read_text(encoding="utf-8")
    function = re.search(r"^sign_in_was_refused\(\) \{.*?^\}", source, re.S | re.M).group(0)
    result = subprocess.run(
        ["bash", "-c", f'{function}\nSIGN_IN_ERROR="$1"; sign_in_was_refused', "check", message],
        capture_output=True, text=True,
    )
    assert (result.returncode == 0) is refused, result.stderr
