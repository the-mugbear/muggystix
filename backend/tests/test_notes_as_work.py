"""Host notes are discussion (v2.446.0).

The file keeps its name from "notes as work" (P3), whose work fields —
status, assignee, due date, resolution, status history — were removed from
the application: work is a host test and its evidence.  What remains and is
pinned here: the body is its author's, the thread's labels (type, pin) are
any analyst's and live on the root, a PATCH is all-or-nothing, a root with
replies is kept, and the role gate.

The ``client`` fixture authenticates as ``test_user`` (id=1, admin).
Notes authored by a *different* user exercise the non-author path.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import models
from app.db.models import Annotation
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


def _make_note(db_session, host_id, user_id, parent_id=None, body="note"):
    n = Annotation(host_id=host_id, user_id=user_id, body=body, parent_id=parent_id)
    db_session.add(n)
    db_session.flush()
    return n


def _note_url(pid, host_id, note_id, suffix=""):
    return f"/api/v1/projects/{pid}/hosts/{host_id}/notes/{note_id}{suffix}"


def test_non_author_can_label_a_thread_but_not_rewrite_it(client, db_session, test_project):
    other = _make_user(db_session, "note-author")
    host = _make_host(db_session, test_project.id, "10.5.0.1")
    note = _make_note(db_session, host.id, other.id)

    # Body edit by a non-author is rejected.
    r_body = client.patch(_note_url(test_project.id, host.id, note.id), json={"body": "hijack"})
    assert r_body.status_code == 403, r_body.text

    # Pinning it, by an analyst who did not write it, is allowed.
    r_pin = client.patch(_note_url(test_project.id, host.id, note.id), json={"pinned": True})
    assert r_pin.status_code == 200, r_pin.text
    assert r_pin.json()["pinned"] is True


def test_a_note_has_no_work_state(client, db_session, test_project, test_user):
    """Status, assignee, due date and resolution are gone from the contract:
    they are not returned, and sending them changes nothing."""
    host = _make_host(db_session, test_project.id, "10.5.1.1")
    created = client.post(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/notes",
        json={"body": "is the share writable?", "status": "resolved"},
    )
    assert created.status_code in (200, 201), created.text
    data = created.json()
    gone = ("status", "assignee_id", "assignee_name", "due_at", "resolution_summary")
    assert not set(gone) & set(data)

    r = client.patch(
        _note_url(test_project.id, host.id, data["id"]),
        json={"status": "resolved", "resolution_summary": "done", "assignee_id": test_user.id,
              "due_at": "2026-12-01T00:00:00Z"},
    )
    assert r.status_code == 200, r.text
    assert not set(gone) & set(r.json())
    # The columns themselves are gone (migration c2e6a4f8d103).
    assert not {"status", "assignee_id", "due_at", "resolution_summary"} & set(Annotation.__table__.columns.keys())
    assert not hasattr(models, "AnnotationStatusHistory") and not hasattr(models, "NoteStatus")
    # And there is no history to read.
    assert client.get(_note_url(test_project.id, host.id, data["id"], "/history")).status_code in (404, 405)


def test_a_status_change_notifies_nobody(client, db_session, test_project):
    from app.db.models_project import Notification
    author = _make_user(db_session, "note-owner")
    host = _make_host(db_session, test_project.id, "10.5.7.1")
    note = _make_note(db_session, host.id, author.id)
    r = client.patch(_note_url(test_project.id, host.id, note.id), json={"status": "in_progress"})
    assert r.status_code == 200, r.text
    assert db_session.query(Notification).filter(Notification.type == "status_change").count() == 0


def test_thread_labels_live_on_the_root(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.3.1")
    root = _make_note(db_session, host.id, test_user.id)
    reply = _make_note(db_session, host.id, test_user.id, parent_id=root.id, body="reply")
    db_session.flush()

    # Labelling through the REPLY id labels the ROOT.
    r = client.patch(_note_url(test_project.id, host.id, reply.id), json={"pinned": True, "note_type": "handoff"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == root.id           # response is the thread root
    assert (body["pinned"], body["note_type"]) == (True, "handoff")
    db_session.refresh(reply)
    assert (bool(reply.pinned), reply.note_type) == (False, None)


def test_patch_is_atomic_no_partial_commit(client, db_session, test_project, test_user):
    """CR-A1/#1 — a PATCH with a valid body but an invalid label must 400 AND
    leave the body unchanged (no partial commit)."""
    host = _make_host(db_session, test_project.id, "10.5.6.1")
    note = _make_note(db_session, host.id, test_user.id, body="original body")

    r = client.patch(
        _note_url(test_project.id, host.id, note.id),
        json={"body": "rewritten body", "note_type": "bogus"},
    )
    assert r.status_code == 400, r.text
    db_session.expire_all()
    refreshed = db_session.query(Annotation).filter(Annotation.id == note.id).first()
    assert refreshed.body == "original body"  # body NOT committed
    assert refreshed.note_type is None


def test_type_and_pin_are_set_and_the_type_cleared(client, db_session, test_project, test_user):
    host = _make_host(db_session, test_project.id, "10.5.5.1")
    note = _make_note(db_session, host.id, test_user.id)

    r = client.patch(
        _note_url(test_project.id, host.id, note.id), json={"pinned": True, "note_type": "question"},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["pinned"], r.json()["note_type"]) == (True, "question")

    # Explicit null clears the type (model_fields_set distinguishes omitted
    # from null); pinned was omitted this call, so it stays True.
    cleared = client.patch(_note_url(test_project.id, host.id, note.id), json={"note_type": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["note_type"] is None
    assert cleared.json()["pinned"] is True


@pytest.mark.parametrize("label", ["bogus", "finding", "action"])
def test_invalid_note_type_rejected(client, db_session, test_project, test_user, label):
    """"finding" and "action" were labels until v2.447.0: a note is neither."""
    host = _make_host(db_session, test_project.id, "10.5.4.1")
    note = _make_note(db_session, host.id, test_user.id)
    r = client.patch(_note_url(test_project.id, host.id, note.id), json={"note_type": label})
    assert r.status_code == 400, r.text


def test_delete_root_with_replies_rejected(client, db_session, test_project, test_user):
    """CR3-#3 — deleting a thread root that still has replies returns 409
    (the self-FKs have no ON DELETE)."""
    host = _make_host(db_session, test_project.id, "10.5.9.1")
    root = _make_note(db_session, host.id, test_user.id)
    _make_note(db_session, host.id, test_user.id, parent_id=root.id, body="reply")
    db_session.flush()

    r = client.delete(_note_url(test_project.id, host.id, root.id))
    assert r.status_code == 409, r.text
    # The reply itself (a leaf) can still be deleted.
    reply = (
        db_session.query(Annotation)
        .filter(Annotation.parent_id == root.id)
        .first()
    )
    assert client.delete(_note_url(test_project.id, host.id, reply.id)).status_code == 204


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


def test_the_dashboard_still_reports_note_activity(client, db_session, test_project, test_user):
    """The dashboard swallowed a KeyError on the note's status after the
    column went (found in the backend log, v2.447.1) and answered
    ``note_activity: null`` — an error nothing surfaced."""
    host = _make_host(db_session, test_project.id, "10.5.11.1")
    _make_note(db_session, host.id, test_user.id, body="who owns the backup job?")
    db_session.commit()
    stats = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats").json()
    activity = stats["note_activity"]
    assert activity is not None and activity["total_notes"] == 1
    assert activity["recent_notes"][0]["preview"] == "who owns the backup job?"
    assert "status" not in activity["recent_notes"][0]
