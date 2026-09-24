"""GET /findings/comments/activity (v2.408.0).

UX review 2026-09-24: Collaboration listed host-note threads only, so finding
comments — and mentions made in them — never appeared on the page the mention
bell opens.  This feed lists each finding's discussion, most recently active
first, with the WHOLE thread's count, newest comment and participants.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import Annotation
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.db.models_project import Project


def _finding(db, project_id, title):
    f = Finding(project_id=project_id, title=title, severity="high", status="open", source="manual")
    db.add(f)
    db.flush()
    return f


def _comment(db, finding, user_id, body, at):
    n = Annotation(finding_id=finding.id, user_id=user_id, body=body, created_at=at)
    db.add(n)
    db.flush()
    return n


def test_lists_discussions_latest_first_with_the_whole_thread(client, db_session, test_project, test_user):
    other = User(
        id=2, username="sam", email="sam@example.com", full_name=None,
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    older = _finding(db_session, test_project.id, "Weak TLS")
    newer = _finding(db_session, test_project.id, "Default creds")
    _finding(db_session, test_project.id, "No discussion")          # never listed
    _comment(db_session, older, test_user.id, "first on TLS", t0)
    _comment(db_session, older, other.id, "@test-admin confirmed", t0 + timedelta(hours=1))
    _comment(db_session, newer, test_user.id, "admin/admin works", t0 + timedelta(hours=2))
    # Another project's discussion never leaks in.
    elsewhere = Project(name="other", slug="other")
    db_session.add(elsewhere)
    db_session.flush()
    _comment(db_session, _finding(db_session, elsewhere.id, "Theirs"), test_user.id, "x", t0 + timedelta(days=1))
    db_session.commit()

    res = client.get(f"/api/v1/projects/{test_project.id}/findings/comments/activity")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert [d["title"] for d in body["items"]] == ["Default creds", "Weak TLS"]
    tls = body["items"][1]
    assert tls["comment_count"] == 2
    assert tls["latest"]["body"] == "@test-admin confirmed"
    assert tls["latest"]["author_name"] == "sam"          # username when no full name
    assert sorted(tls["participants"]) == ["Test Admin", "sam"]
    assert tls["status"] == "open" and tls["severity"] == "high"


def test_filters_match_any_comment_but_report_the_whole_thread(client, db_session, test_project, test_user):
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    f = _finding(db_session, test_project.id, "Weak TLS")
    _comment(db_session, f, test_user.id, "cipher list attached", t0)
    _comment(db_session, f, None, "an agent's note", t0 + timedelta(hours=1))
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/findings/comments/activity"

    by_author = client.get(f"{base}?author_id={test_user.id}").json()
    assert [d["finding_id"] for d in by_author["items"]] == [f.id]
    assert by_author["items"][0]["comment_count"] == 2     # the whole thread
    assert client.get(f"{base}?search=cipher").json()["total"] == 1
    assert client.get(f"{base}?search=weak tls").json()["total"] == 1   # the title matches too
    assert client.get(f"{base}?search=nothing-like-it").json() == {"items": [], "total": 0}
