"""v2.397.0 — @mentions resolve for real usernames, finding comments notify,
and a reply reaches the people already in the conversation without one.

Before: the mention pattern was ``@(\\w+)``, so ``@eval-ana`` resolved to
``eval`` and notified nobody; finding comments never parsed mentions at all;
and a reply to someone's comment told them nothing unless it @mentioned them.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, NoteMention, ProjectMembership, ProjectRole
from app.main import app
from app.services.notification_service import find_mentions


# ---------------------------------------------------------------------------
# find_mentions — the matching rule
# ---------------------------------------------------------------------------

NAMES = ["eval-ana", "eval-ben", "ana", "j.smith", "anabel", "Bob_2"]


@pytest.mark.parametrize("body, expected", [
    ("@eval-ana please look", {"eval-ana"}),              # hyphen (the reported bug)
    ("ping @j.smith.", {"j.smith"}),                      # dot inside, full stop after
    ("@ana, and @anabel", {"ana", "anabel"}),             # longest match, punctuation boundary
    ("@anab is nobody", set()),                           # no prefix matches
    ("@ana-maria is nobody either", set()),               # 'ana' followed by more name
    ("mail ana@example.com", set()),                      # e-mail address, not a mention
    ("(@eval-ben) @BOB_2!", {"eval-ben", "Bob_2"}),       # brackets, case-insensitive
    ("@eval-ana\n@eval-ana again", {"eval-ana"}),
    ("no mention here", set()),
    ("trailing @", set()),
])
def test_find_mentions(body, expected):
    assert find_mentions(body, NAMES) == expected


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _member(db_session, project, user_id: int, username: str, role=ProjectRole.ANALYST) -> User:
    user = User(
        id=user_id, username=username, email=f"{user_id}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    if project is not None:
        db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


@pytest.fixture
def act_as():
    def _act(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


@pytest.fixture
def people(db_session, test_project):
    return {
        "ana": _member(db_session, test_project, 301, "eval-ana"),
        "ben": _member(db_session, test_project, 302, "eval-ben"),
        "cy": _member(db_session, test_project, 303, "cy.lee"),
        "outsider": _member(db_session, None, 304, "outsider"),
    }


def _inbox(db_session, user):
    return db_session.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id).all()


def _finding(client, project, title="SMB signing disabled"):
    r = client.post(f"/api/v1/projects/{project.id}/findings", json={"title": title, "severity": "high"})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_mention_in_a_finding_comment_notifies_and_links_to_the_comment(
    client, db_session, test_project, people, act_as,
):
    act_as(people["ana"])
    f = _finding(client, test_project)
    base = f"/api/v1/projects/{test_project.id}/findings/{f['id']}/notes"
    note = client.post(base, json={"body": "@eval-ben can you confirm? cc @outsider"}).json()
    assert note.get("mention_warning") is None

    [n] = _inbox(db_session, people["ben"])
    assert n.type == "mention"
    assert n.finding_id == f["id"] and n.source_id == note["id"] and n.host_id is None
    assert "SMB signing disabled" in n.title
    # Not a project member: never told, even though named.
    assert _inbox(db_session, people["outsider"]) == []
    # The notifications API carries the deep-link id.
    act_as(people["ben"])
    [row] = client.get("/api/v1/notifications/").json()["notifications"]
    assert row["finding_id"] == f["id"] and row["source_id"] == note["id"]


def test_a_reply_reaches_the_discussion_without_a_mention(client, db_session, test_project, people, act_as):
    act_as(people["ana"])
    f = _finding(client, test_project)
    base = f"/api/v1/projects/{test_project.id}/findings/{f['id']}/notes"
    root = client.post(base, json={"body": "Seen on three DCs"}).json()
    # Ana created the finding and wrote the only comment; nobody else to tell.
    assert _inbox(db_session, people["ben"]) == []

    act_as(people["ben"])
    client.post(base, json={"body": "Confirmed on DC02", "parent_id": root["id"]})
    [n] = _inbox(db_session, people["ana"])
    assert n.type == "finding_comment" and n.finding_id == f["id"]
    assert n.title.startswith("@eval-ben replied")
    assert _inbox(db_session, people["ben"]) == []  # never yourself

    # Cy joins with a top-level comment that mentions Ana: Ana gets ONE
    # notification (the mention), Ben gets the discussion update.
    act_as(people["cy"])
    client.post(base, json={"body": "@eval-ana the fix is a GPO"})
    ana = _inbox(db_session, people["ana"])
    assert [x.type for x in ana] == ["finding_comment", "mention"]
    assert [x.type for x in _inbox(db_session, people["ben"])] == ["finding_comment"]


def test_the_finding_owner_hears_about_comments(client, db_session, test_project, people, act_as):
    act_as(people["ana"])
    f = _finding(client, test_project)
    url = f"/api/v1/projects/{test_project.id}/findings/{f['id']}"
    assert client.patch(url, json={"owner_id": people["cy"].id}).status_code == 200
    act_as(people["ben"])
    client.post(f"{url}/notes", json={"body": "Retest scheduled"})
    told = db_session.query(Notification).filter(Notification.type == "finding_comment").all()
    # The author (Ana) and the owner (Cy), though neither has commented.
    assert sorted(x.user_id for x in told) == [people["ana"].id, people["cy"].id]


def test_an_edit_notifies_only_newly_mentioned_users(client, db_session, test_project, people, act_as):
    act_as(people["ana"])
    f = _finding(client, test_project)
    base = f"/api/v1/projects/{test_project.id}/findings/{f['id']}/notes"
    note = client.post(base, json={"body": "@eval-ben look"}).json()
    assert len(_inbox(db_session, people["ben"])) == 1

    r = client.patch(f"{base}/{note['id']}", json={"body": "@eval-ben look again, and @cy.lee"})
    assert r.status_code == 200, r.text
    assert len(_inbox(db_session, people["ben"])) == 1          # not re-pinged
    [cy] = _inbox(db_session, people["cy"])
    assert cy.type == "mention"
    assert db_session.query(NoteMention).filter(NoteMention.note_id == note["id"]).count() == 2


def test_host_note_mentions_resolve_hyphenated_names_and_replies_reach_the_thread(
    client, db_session, test_project, people, act_as,
):
    host = models.Host(project_id=test_project.id, ip_address="10.0.0.5")
    db_session.add(host)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/hosts/{host.id}/notes"

    act_as(people["ana"])
    root = client.post(base, json={"body": "@eval-ben SMB open here"}).json()
    [m] = _inbox(db_session, people["ben"])
    assert m.type == "mention" and m.host_id == host.id and m.finding_id is None

    act_as(people["cy"])
    client.post(base, json={"body": "Looking", "parent_id": root["id"]})
    # Ana wrote the thread, Ben is only mentioned in it — only writers are participants.
    [r] = _inbox(db_session, people["ana"])
    assert r.type == "note_reply" and r.source_id != root["id"]
    assert [x.type for x in _inbox(db_session, people["ben"])] == ["mention"]
