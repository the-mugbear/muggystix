"""Tests for notes-as-work (refactor P3).

Covers the permission split (body author-only; thread work-state open to
any project member), resolve-requires-summary, status history, the
thread-root semantics, and assignee/pinned/note_type handling.

The ``client`` fixture authenticates as ``test_user`` (id=1, admin).
Notes authored by a *different* user exercise the non-author path.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import models
from app.db.models import HostNote, NoteStatus
from app.db.models_project import ProjectMembership, ProjectRole
from app.api.deps import require_project_role

_USER_ID_SEQ = [3000]


def _make_user(db_session, username):
    from app.db.models_auth import User, UserRole
    from datetime import datetime, timezone
    _USER_ID_SEQ[0] += 1
    u = User(
        id=_USER_ID_SEQ[0],
        username=username,
        email=f"{username}@example.com",
        full_name=username.capitalize(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.flush()
    return u


def _make_host(db_session, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(h)
    db_session.flush()
    return h


def _make_note(db_session, host_id, user_id, status=NoteStatus.OPEN, parent_id=None, body="note"):
    n = HostNote(host_id=host_id, user_id=user_id, body=body, status=status, parent_id=parent_id)
    db_session.add(n)
    db_session.flush()
    return n


def _note_url(pid, host_id, note_id, suffix=""):
    return f"/api/v1/projects/{pid}/hosts/{host_id}/notes/{note_id}{suffix}"


def test_non_author_can_change_status_but_not_body(client, db_session, test_project):
    other = _make_user(db_session, "note-author")
    host = _make_host(db_session, test_project.id, "10.5.0.1")
    note = _make_note(db_session, host.id, other.id)

    # Body edit by a non-author is rejected.
    r_body = client.patch(_note_url(test_project.id, host.id, note.id), json={"body": "hijack"})
    assert r_body.status_code == 403, r_body.text

    # Status change by a project member (non-author) is allowed.
    r_status = client.patch(
        _note_url(test_project.id, host.id, note.id), json={"status": "in_progress"},
    )
    assert r_status.status_code == 200, r_status.text
    assert r_status.json()["status"] == "in_progress"


def test_resolve_requires_summary(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.1.1")
    note = _make_note(db_session, host.id, test_user.id)

    bad = client.patch(_note_url(test_project.id, host.id, note.id), json={"status": "resolved"})
    assert bad.status_code == 400, bad.text
    assert "summary" in bad.json()["detail"].lower()

    ok = client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"status": "resolved", "resolution_summary": "patched and verified"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "resolved"
    assert body["resolution_summary"] == "patched and verified"


def test_status_history_recorded(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.2.1")
    note = _make_note(db_session, host.id, test_user.id)

    client.patch(_note_url(test_project.id, host.id, note.id), json={"status": "in_progress"})
    client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"status": "resolved", "resolution_summary": "fixed"},
    )

    hist = client.get(_note_url(test_project.id, host.id, note.id, "/history"))
    assert hist.status_code == 200, hist.text
    rows = hist.json()
    assert [r["to_status"] for r in rows] == ["in_progress", "resolved"]
    assert rows[0]["from_status"] == "open"
    assert rows[1]["summary"] == "fixed"


def test_thread_meta_targets_root_so_reply_doesnt_reopen(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.3.1")
    root = _make_note(db_session, host.id, test_user.id, status=NoteStatus.RESOLVED)
    root.resolution_summary = "done"
    reply = _make_note(db_session, host.id, test_user.id, parent_id=root.id, body="reply")
    db_session.flush()

    # Updating thread state via the REPLY id moves the ROOT's status.
    r = client.patch(
        _note_url(test_project.id, host.id, reply.id), json={"status": "in_progress"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == root.id           # response is the thread root
    assert body["status"] == "in_progress"

    # The reply's own status is untouched.
    db_session.refresh(reply)
    assert reply.status == NoteStatus.OPEN


def test_invalid_note_type_rejected(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.4.1")
    note = _make_note(db_session, host.id, test_user.id)
    r = client.patch(_note_url(test_project.id, host.id, note.id), json={"note_type": "bogus"})
    assert r.status_code == 400, r.text


def test_patch_is_atomic_no_partial_commit(client, db_session, test_project, test_user):
    """CR-A1/#1 — a PATCH with a valid body but invalid metadata (resolve
    without summary) must 400 AND leave the body unchanged (no partial
    commit)."""
    host = _make_host(db_session, test_project.id, "10.5.6.1")
    note = _make_note(db_session, host.id, test_user.id, body="original body")

    r = client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"body": "rewritten body", "status": "resolved"},  # no summary → 400
    )
    assert r.status_code == 400, r.text
    db_session.expire_all()
    refreshed = db_session.query(HostNote).filter(HostNote.id == note.id).first()
    assert refreshed.body == "original body"  # body NOT committed
    assert refreshed.status == NoteStatus.OPEN


def test_status_change_notifies_thread_author(client, db_session, test_project, test_user):
    """CR-A1/#2 — a status change by a non-author creates an in-app
    notification for the note author (not just a webhook)."""
    from app.db.models_project import Notification
    author = _make_user(db_session, "note-owner")
    host = _make_host(db_session, test_project.id, "10.5.7.1")
    note = _make_note(db_session, host.id, author.id)  # authored by someone else

    # Actor is the admin client (id 1); changes status → author notified.
    r = client.patch(
        _note_url(test_project.id, host.id, note.id), json={"status": "in_progress"},
    )
    assert r.status_code == 200, r.text
    notifs = (
        db_session.query(Notification)
        .filter(Notification.user_id == author.id, Notification.type == "status_change")
        .all()
    )
    assert len(notifs) >= 1


def test_assignee_must_be_project_member(client, db_session, test_project, test_user):
    """CR-A1/#3 — assigning to a non-member is rejected with 400."""
    outsider = _make_user(db_session, "outsider-nomember")  # no membership
    host = _make_host(db_session, test_project.id, "10.5.8.1")
    note = _make_note(db_session, host.id, test_user.id)

    r = client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"assignee_id": outsider.id},
    )
    assert r.status_code == 400, r.text
    assert "member" in r.json()["detail"].lower()


def test_viewer_role_blocked_from_note_mutations(db_session, test_project):
    """RV-4 — note write endpoints gate on ProjectRole.ANALYST, so a
    project VIEWER is rejected (global admins bypass; tested elsewhere)."""
    viewer = _make_user(db_session, "viewer-rv4")  # UserRole.MEMBER, not global admin
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=viewer.id, role="viewer",
    ))
    db_session.flush()
    checker = require_project_role(ProjectRole.ANALYST)
    with pytest.raises(HTTPException) as exc:
        checker(project_id=test_project.id, db=db_session, current_user=viewer)
    assert exc.value.status_code == 403


def test_analyst_role_allowed_note_mutations(db_session, test_project):
    analyst = _make_user(db_session, "analyst-rv4")
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=analyst.id, role="analyst",
    ))
    db_session.flush()
    checker = require_project_role(ProjectRole.ANALYST)
    # Returns the user without raising.
    assert checker(project_id=test_project.id, db=db_session, current_user=analyst) is analyst


def test_assignee_pinned_and_clear(client, db_session, test_project, test_user):
    other = _make_user(db_session, "assignee-target")
    # CR-A1/#3 — assignee must be a project member.
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=other.id, role="analyst",
    ))
    db_session.flush()
    host = _make_host(db_session, test_project.id, "10.5.5.1")
    note = _make_note(db_session, host.id, test_user.id)

    r = client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"assignee_id": other.id, "pinned": True, "note_type": "finding"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assignee_id"] == other.id
    assert body["assignee_name"] == other.full_name
    assert body["pinned"] is True
    assert body["note_type"] == "finding"

    # Explicit null clears the assignee (model_fields_set distinguishes
    # omitted from null).
    cleared = client.patch(
        _note_url(test_project.id, host.id, note.id), json={"assignee_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assignee_id"] is None
    # Pinned was omitted this call, so it stays True.
    assert cleared.json()["pinned"] is True
