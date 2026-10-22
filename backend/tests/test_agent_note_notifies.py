"""An agent's note is announced like a person's (2.489.0).

``POST /hosts/{id}/notes`` (the page) and ``POST /agent/hosts/{id}/notes`` (an
agent, MCP ``assist_add_note``) both wrote the note through
``HostFollowService.create_note``, but only the page's route went on to tell
anybody: an agent's ``@mention`` reached nobody, and a reviewer was not told
that a note had landed on their host.  Writing and telling are one method now,
``HostFollowService.post_note``, and both doors call it.

An agent's note is its OPERATOR's (``annotations.user_id``), so the operator is
never told about it — not for mentioning themselves, not as the host's
reviewer.  Everybody else is told exactly as for a note the operator typed:
the two doors are compared notification for notification.
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import NoteMention, Notification, ProjectMembership
from app.services.notification_service import NotificationService


def _member(db, project, user_id, username):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    if project is not None:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role="analyst"))
    return user


@pytest.fixture
def cast(db_session, test_project, test_user):
    """A host the operator AND a teammate are reviewing, a teammate to mention,
    and somebody who is not in the project."""
    host = models.Host(project_id=test_project.id, ip_address="10.75.0.1", state="up")
    db_session.add(host)
    mentioned = _member(db_session, test_project, 801, "i5-ana")
    reviewer = _member(db_session, test_project, 802, "i5-ben")
    outsider = _member(db_session, None, 803, "i5-outsider")
    # The operator is a member too: only members are told anything (a global
    # admin with no membership row is not), so the tests below show that the
    # operator is left out for being the AUTHOR, not for that.
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="admin"))
    db_session.flush()
    for user in (reviewer, test_user):
        db_session.add(models.HostFollow(
            host_id=host.id, user_id=user.id, status=models.FollowStatus.IN_REVIEW,
        ))
    db_session.commit()
    return {
        "host": host.id, "mentioned": mentioned.id, "reviewer": reviewer.id,
        "outsider": outsider.id, "operator": test_user.id,
        "operator_name": test_user.username,
    }


def _agent(client, project):
    started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    return {"X-API-Key": started.json()["api_key"]}, started.json()["agent_session_id"]


def _told(db, note_id):
    """``{user_id: type}`` of the notifications about one note."""
    db.expire_all()
    rows = db.query(Notification).filter(
        Notification.source_type == "note", Notification.source_id == note_id,
    ).all()
    assert len({r.user_id for r in rows}) == len(rows), "somebody was told twice"
    return {r.user_id: r.type for r in rows}


def _body(cast):
    return (
        f"SMB signing is off here — @i5-ana please confirm. "
        f"cc @{cast['operator_name']} @i5-outsider"
    )


def test_an_agents_mention_notifies_the_member_and_never_its_operator(
    client, db_session, test_project, cast,
):
    headers, session_id = _agent(client, test_project)
    written = client.post(
        f"/api/v1/agent/hosts/{cast['host']}/notes", headers=headers, json={"body": _body(cast)},
    )
    assert written.status_code == 201, written.text
    note = db_session.get(models.Annotation, written.json()["id"])
    # The note is the operator's, written by their agent, in that session.
    assert (note.user_id, note.actor_type, note.agent_session_id) == (
        cast["operator"], "agent", session_id,
    )

    told = _told(db_session, note.id)
    # The mentioned member, and the teammate reviewing the host.
    assert told == {cast["mentioned"]: "mention", cast["reviewer"]: "host_note"}
    # Not the operator — mentioned in the note AND reviewing the host — and
    # nobody outside the project.
    assert cast["operator"] not in told and cast["outsider"] not in told

    mention = db_session.query(Notification).filter(
        Notification.source_id == note.id, Notification.user_id == cast["mentioned"],
    ).one()
    assert mention.actor_id == cast["operator"]
    assert mention.project_id == test_project.id and mention.host_id == cast["host"]
    assert mention.title.endswith("mentioned you on 10.75.0.1")
    # The record that this note told them (an edit does not tell them again).
    assert {
        m.user_id for m in db_session.query(NoteMention)
        .filter(NoteMention.note_id == note.id)
    } == {cast["mentioned"]}


def test_both_doors_tell_the_same_people_the_same_way(client, db_session, test_project, cast):
    """Old == new for the page's door, and door == door: the same note typed by
    the operator and written by the operator's agent notifies identically."""
    typed = client.post(
        f"/api/v1/projects/{test_project.id}/hosts/{cast['host']}/notes",
        json={"body": _body(cast)},
    )
    assert typed.status_code == 200, typed.text
    # The page's answer still says who the mentions reached and which matched nobody.
    assert [m["username"] for m in typed.json()["mentions_notified"]] == ["i5-ana"]
    assert typed.json()["unmatched_mentions"] == ["i5-outsider"]
    assert typed.json().get("mention_warning") is None

    headers, _ = _agent(client, test_project)
    by_agent = client.post(
        f"/api/v1/agent/hosts/{cast['host']}/notes", headers=headers, json={"body": _body(cast)},
    )
    assert by_agent.status_code == 201, by_agent.text
    # The agent is told which mentions reached nobody, as the page is
    # (backlog 2026-10-22: its answer dropped them).
    assert by_agent.json()["unmatched_mentions"] == typed.json()["unmatched_mentions"] == ["i5-outsider"]

    from_page = _told(db_session, typed.json()["id"])
    from_agent = _told(db_session, by_agent.json()["id"])
    assert from_page == from_agent == {
        cast["mentioned"]: "mention", cast["reviewer"]: "host_note",
    }


def test_a_reply_by_the_page_reaches_the_thread_an_agent_started(
    client, db_session, test_project, cast,
):
    """An agent's note is a thread like any other: a teammate's reply to it
    tells the thread's writer — the operator, this time, because the reply is
    not theirs."""
    from app.api.v1.endpoints.auth import get_current_user
    from app.main import app

    headers, _ = _agent(client, test_project)
    root = client.post(
        f"/api/v1/agent/hosts/{cast['host']}/notes", headers=headers, json={"body": "RDP is open"},
    ).json()["id"]

    teammate = db_session.get(User, cast["mentioned"])
    previous = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = lambda: teammate
    try:
        reply = client.post(
            f"/api/v1/projects/{test_project.id}/hosts/{cast['host']}/notes",
            json={"body": "confirmed", "parent_id": root},
        )
    finally:
        app.dependency_overrides[get_current_user] = previous
    assert reply.status_code == 200, reply.text
    assert _told(db_session, reply.json()["id"]) == {
        cast["operator"]: "note_reply", cast["reviewer"]: "host_note",
    }


def test_a_failed_announcement_never_loses_the_note_on_either_door(
    client, db_session, test_project, cast, monkeypatch,
):
    def broken(self, *args, **kwargs):
        raise RuntimeError("notification pipeline down")

    monkeypatch.setattr(NotificationService, "process_note_mentions", broken)
    headers, _ = _agent(client, test_project)

    typed = client.post(
        f"/api/v1/projects/{test_project.id}/hosts/{cast['host']}/notes",
        json={"body": "typed @i5-ana"},
    )
    assert typed.status_code == 200, typed.text
    assert "could not be delivered" in typed.json()["mention_warning"]
    by_agent = client.post(
        f"/api/v1/agent/hosts/{cast['host']}/notes", headers=headers, json={"body": "agent @i5-ana"},
    )
    assert by_agent.status_code == 201, by_agent.text

    db_session.expire_all()
    for note_id, body in ((typed.json()["id"], "typed @i5-ana"), (by_agent.json()["id"], "agent @i5-ana")):
        assert db_session.get(models.Annotation, note_id).body == body
        assert _told(db_session, note_id) == {}
