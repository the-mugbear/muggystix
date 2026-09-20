"""Serialising notes costs a fixed number of queries, not one per note (v2.369.1).

Code review finding 7. ``_serialize_note`` reads four relationships. Loaders
spelled the eager loads out themselves and drifted: the host endpoints named
author + assignee + promoted_findings, while the report loaders — and, found
while fixing those, ``HostFollowService.list_notes`` (the HOST DETAIL) and
``FindingService.list_finding_notes`` — named only the author.
``promoted_findings`` is one-to-many, so it is never served from the session
the way a repeated author is: one query per note, every time.

``note_load_options()`` is now the one list, kept beside the serializer. The
first test is the drift guard: whatever the serializer reads must already be
loaded, so serialising emits NO statement. Fixtures use distinct assignees and
a promotion on every note, per the review, so nothing is hidden by the
identity map.
"""
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.services.host_follow_service import HostFollowService
from app.services.host_serialization import _serialize_note, note_load_options


def _seed(db, project, author, count, ip="10.77.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    # Explicit ids: the ``test_user`` fixture inserts id=1 by hand, so the
    # users sequence still hands out 1 to the next plain insert.
    base_id = 1000 + int(ip.split(".")[2]) * 100 + int(ip.split(".")[3]) * 20
    for i in range(count):
        assignee = User(
            id=base_id + i,
            username=f"assignee-{ip}-{i}", email=f"a{i}-{ip}@example.com",
            hashed_password="x", role=UserRole.MEMBER, is_active=True,
        )
        db.add(assignee)
        db.flush()
        note = models.Annotation(
            host_id=host.id, project_id=project.id, user_id=author.id,
            assignee_id=assignee.id, body=f"note {i}",
        )
        db.add(note)
        db.flush()
        db.add(Finding(
            project_id=project.id, title=f"finding {i}", severity="high",
            source="manual", evidence_annotation_id=note.id,
        ))
    db.commit()
    return host


class _Statements:
    def __enter__(self):
        self.seen = []
        self._fn = lambda conn, cur, stmt, params, ctx, many: self.seen.append(" ".join(stmt.split()))
        event.listen(Engine, "after_cursor_execute", self._fn)
        return self

    def __exit__(self, *exc):
        event.remove(Engine, "after_cursor_execute", self._fn)


def test_serialising_preloaded_notes_runs_no_query(db_session, test_project, test_user):
    host = _seed(db_session, test_project, test_user, count=6)
    db_session.expire_all()
    notes = (
        db_session.query(models.Annotation)
        .filter(models.Annotation.host_id == host.id)
        .options(*note_load_options())
        .all()
    )
    assert len(notes) == 6

    with _Statements() as during:
        out = [_serialize_note(n) for n in notes]

    assert during.seen == [], (
        "_serialize_note read a relationship note_load_options() does not load:\n"
        + "\n".join(during.seen[:5])
    )
    assert all(o.finding_id is not None and o.assignee_name for o in out)


def test_the_host_notes_loader_does_not_query_per_note(db_session, test_project, test_user):
    """``list_notes`` feeds the host detail. It preloaded only the author."""
    host = _seed(db_session, test_project, test_user, count=8)
    db_session.expire_all()

    with _Statements() as run:
        notes = HostFollowService(db_session).list_notes(host.id)
        [_serialize_note(n) for n in notes]

    per_note = [s for s in run.seen if "FROM findings" in s and "primary_keys" not in s]
    assert per_note == [], f"{len(per_note)} per-note finding lookups"


def test_host_detail_query_count_does_not_grow_with_notes(client, db_session, test_project, test_user):
    few = _seed(db_session, test_project, test_user, count=2, ip="10.77.1.1")
    many = _seed(db_session, test_project, test_user, count=12, ip="10.77.1.2")

    def _count(host):
        with _Statements() as run:
            r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
        assert r.status_code == 200, r.text
        assert len(r.json()["notes"]) == (2 if host is few else 12)
        return len(run.seen)

    # Two things that are NOT note count and would otherwise be measured:
    #  * the first request of a test pays a few one-time lookups — warm up;
    #  * the seeded Host objects sit EXPIRED in this shared session, and the
    #    handler touching one refreshes it with the model's default eager
    #    loads (seven statements). A real request starts with an empty
    #    session, so give each measured request one.
    _count(few)

    project_id = test_project.id
    few_id, many_id = few.id, many.id
    # Only the hosts: the project and user fixtures must stay attached.
    db_session.expunge(few)
    db_session.expunge(many)

    def _fresh(host_id):
        with _Statements() as run:
            r = client.get(f"/api/v1/projects/{project_id}/hosts/{host_id}")
        assert r.status_code == 200, r.text
        return len(run.seen), len(r.json()["notes"])

    many_count, many_notes = _fresh(many_id)
    few_count, few_notes = _fresh(few_id)
    assert (few_notes, many_notes) == (2, 12)
    assert many_count == few_count, "ten more notes must not cost ten more queries"
