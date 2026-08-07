"""
Bulk finding operations (v2.234.0).

Before this there was no bulk endpoint for findings at all: the page fired
one PATCH per finding from the browser, unbounded and partially failable,
and ownership could only be changed one detail page at a time.

The properties worth pinning are the ones a naive loop-in-the-client gets
wrong: project scoping, the terminal-justification rule surviving the batch,
one notification instead of N, and honest reporting when some ids are
rejected.
"""

import pytest

from app.db.models_findings import Finding
from app.db.models_project import Notification


def _finding(db, project_id, title, **over):
    f = Finding(
        project_id=project_id,
        title=title,
        severity=over.pop("severity", "high"),
        status=over.pop("status", "open"),
        source=over.pop("source", "manual"),
        **over,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@pytest.fixture
def three_findings(db_session, test_project):
    return [
        _finding(db_session, test_project.id, f"Finding {i}") for i in range(1, 4)
    ]


def _url(project_id, action):
    return f"/api/v1/projects/{project_id}/findings/bulk/{action}"


def test_bulk_status_applies_to_every_selected_finding(
    client, db_session, test_project, three_findings
):
    ids = [f.id for f in three_findings]
    resp = client.post(
        _url(test_project.id, "status"), json={"finding_ids": ids, "status": "confirmed"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 3

    for f in three_findings:
        db_session.refresh(f)
        assert f.status == "confirmed"


def test_bulk_status_enforces_the_terminal_justification_rule(
    client, db_session, test_project, three_findings
):
    """A terminal disposition without a reason must fail the whole batch —
    dispositioning some findings unjustified would corrupt the audit trail
    that reports are built from."""
    ids = [f.id for f in three_findings]
    resp = client.post(
        _url(test_project.id, "status"),
        json={"finding_ids": ids, "status": "false_positive"},
    )
    assert resp.status_code == 422, resp.text

    for f in three_findings:
        db_session.refresh(f)
        assert f.status == "open", "no finding may be dispositioned without a reason"


def test_bulk_status_accepts_a_terminal_move_with_a_justification(
    client, db_session, test_project, three_findings
):
    ids = [f.id for f in three_findings]
    resp = client.post(
        _url(test_project.id, "status"),
        json={
            "finding_ids": ids,
            "status": "false_positive",
            "summary": "scanner flagged the backport, not the CVE",
        },
    )
    assert resp.status_code == 200, resp.text
    for f in three_findings:
        db_session.refresh(f)
        assert f.status == "false_positive"


def test_bulk_ignores_findings_from_another_project_and_says_so(
    client, db_session, test_project, three_findings
):
    """Cross-project ids must never be touched, and the caller must be told
    rather than left assuming the whole batch landed."""
    from app.db.models_project import Project

    other = Project(name="other-project", slug="other-project")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    foreign = _finding(db_session, other.id, "Foreign finding")

    ids = [three_findings[0].id, foreign.id]
    resp = client.post(
        _url(test_project.id, "status"), json={"finding_ids": ids, "status": "confirmed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 1
    assert body["requested"] == 2
    assert body["skipped_ids"] == [foreign.id]

    db_session.refresh(foreign)
    assert foreign.status == "open", "a finding in another project must be untouched"


def test_bulk_assign_sets_owner_and_sends_one_notification(
    client, db_session, test_project, test_user, three_findings
):
    from app.db.models_auth import User
    from app.db.models_project import ProjectMembership

    # Explicit id: the test_user fixture hardcodes id=1 without advancing the
    # sequence, so an auto-assigned id collides on users_pkey.
    assignee = User(
        id=901, username="assignee", email="assignee@example.com",
        hashed_password="x", is_active=True,
    )
    db_session.add(assignee)
    db_session.commit()
    db_session.refresh(assignee)
    db_session.add(
        ProjectMembership(project_id=test_project.id, user_id=assignee.id, role="analyst")
    )
    db_session.commit()

    ids = [f.id for f in three_findings]
    resp = client.post(
        _url(test_project.id, "assign"),
        json={"finding_ids": ids, "assignee_user_id": assignee.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 3

    for f in three_findings:
        db_session.refresh(f)
        assert f.owner_id == assignee.id

    notes = (
        db_session.query(Notification)
        .filter(Notification.user_id == assignee.id, Notification.type == "assignment")
        .all()
    )
    assert len(notes) == 1, "one summary notification per batch, not one per finding"
    assert "3 findings" in notes[0].title


def test_bulk_assign_can_unassign(
    client, db_session, test_project, test_user, three_findings
):
    """The single-finding PATCH skips owner_id when it's None, so bulk is the
    only way to clear ownership in one action."""
    for f in three_findings:
        f.owner_id = test_user.id
    db_session.commit()

    ids = [f.id for f in three_findings]
    resp = client.post(
        _url(test_project.id, "assign"),
        json={"finding_ids": ids, "assignee_user_id": None},
    )
    assert resp.status_code == 200, resp.text
    for f in three_findings:
        db_session.refresh(f)
        assert f.owner_id is None


def test_bulk_assign_rejects_a_non_member(
    client, db_session, test_project, three_findings
):
    from app.db.models_auth import User

    outsider = User(
        id=902, username="outsider", email="outsider@example.com",
        hashed_password="x", is_active=True,
    )
    db_session.add(outsider)
    db_session.commit()
    db_session.refresh(outsider)

    resp = client.post(
        _url(test_project.id, "assign"),
        json={"finding_ids": [three_findings[0].id], "assignee_user_id": outsider.id},
    )
    assert resp.status_code == 400, resp.text
    assert "member" in resp.json()["detail"].lower()


def test_bulk_rejects_an_oversized_batch(client, test_project):
    resp = client.post(
        _url(test_project.id, "status"),
        json={"finding_ids": list(range(1, 5002)), "status": "confirmed"},
    )
    assert resp.status_code == 413, resp.text
