"""``GET /remediation/export`` — the list's rows for a file, in one answer (v2.475.0).

The page built its CSV by reading the list 200 rows at a time: up to a hundred
requests, each recounting the page's states, flags and severities.  The export
is the list's own function with the counts left out.  Pins:

* the export is exactly the list paged through — the same rows, in the same
  order, with the same fields — under each filter, so a file cannot differ
  from what the page shows;
* it issues no count statement (three statements fewer than a list page would
  need per 200 rows — the point of it);
* ``total`` is every matching row and ``items`` stops at the ceiling;
* it is the list's floor (auditor) and answers 404 with tracking off;
* the cross-project export is the cross-project list's rows, of the projects
  the caller administers only.
"""
import pytest
from sqlalchemy import event

from app.db.models_project import ProjectMembership
from app.services import remediation_service

from tests.test_remediation_deadlines import (
    OVERVIEW, _project, _rows, _user, apply, as_user, base, day, listing, on,
)


def export(client, project, **params):
    response = client.get(f"{base(project)}/export", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _paged(client, project, **params):
    rows, offset = [], 0
    while True:
        page = listing(client, project, **params, limit=7, offset=offset)
        rows += page["items"]
        if not page["has_more"]:
            return rows, page["total"]
        offset += 7


@pytest.fixture
def worked(client, db_session, test_project, on):
    """31 medium rows in assorted states, 6 critical ones overdue, two contacts."""
    medium = _rows(db_session, test_project, 31, severity="medium", title="SMB signing not required")
    critical = _rows(db_session, test_project, 6, severity="critical", net=91, title="Default credentials")
    apply(client, test_project,
          [{"finding_host_id": i, "notified_on": day(100), "contact_email": "roger@example.com"} for i in medium[:13]]
          + [{"finding_host_id": i, "notified_on": day(85), "contact_email": "maria@example.com"} for i in medium[13:20]]
          + [{"finding_host_id": i, "notified_on": day(1)} for i in medium[20:24]]
          + [{"finding_host_id": i, "status": "closed"} for i in medium[24:26]]
          + [{"finding_host_id": i, "notified_on": day(40), "contact_email": "roger@example.com"} for i in critical])
    return {"medium": medium, "critical": critical}


@pytest.mark.parametrize("filters", [
    {},
    {"group": "due"},
    {"state": "overdue"},
    {"state": ["overdue", "due_soon"], "group": "due"},
    {"status": "closed"},
    {"severity": "critical"},
    {"contact": "roger"},
    {"unassigned": "true"},
    {"q": "default"},
    {"overdue_band": "31-90"},
])
def test_the_export_is_the_list_paged_through(client, test_project, worked, filters):
    listed, total = _paged(client, test_project, **filters)
    answer = export(client, test_project, **filters)
    assert answer["total"] == total == len(listed)
    assert answer["items"] == listed                      # the same rows, order and fields
    assert answer["as_of"] == listing(client, test_project, limit=1)["as_of"]
    assert set(answer) == {"items", "total", "limit", "as_of"}   # rows only: no counts to misread


def test_the_export_counts_nothing(client, db_session, test_project, worked):
    """One page of the list is three statements about the rows (two grouped
    counts and the page); the export is the rows and their total."""
    def statements(call):
        seen = []

        def _on_execute(conn, cursor, statement, params, context, executemany):
            if "finding_host" in statement.lower():
                seen.append(statement)
        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            call()
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        return seen

    listed = statements(lambda: listing(client, test_project, limit=200))
    exported = statements(lambda: export(client, test_project))
    grouped = [s for s in exported if "group by" in s.lower()]
    assert grouped == []
    assert len(exported) < len(listed)


def test_the_export_stops_at_its_ceiling_and_says_the_true_total(client, test_project, worked, monkeypatch):
    monkeypatch.setattr(remediation_service, "EXPORT_MAX_ROWS", 10)
    answer = export(client, test_project)
    assert answer["total"] == 37 and len(answer["items"]) == 10 and answer["limit"] == 10
    # …the first ten of the list, not some ten.
    assert answer["items"] == listing(client, test_project, limit=10)["items"]


def test_the_export_is_the_lists_to_read_and_not_there_with_tracking_off(client, db_session, test_project, worked):
    from app.db.models_remediation import RemediationPolicy

    viewer, auditor = _user(db_session, "exp-viewer"), _user(db_session, "exp-auditor")
    db_session.add_all([
        ProjectMembership(project_id=test_project.id, user_id=viewer.id, role="viewer"),
        ProjectMembership(project_id=test_project.id, user_id=auditor.id, role="auditor"),
    ])
    db_session.commit()

    # Off: every remediation route is absent, this one included.  (Checked
    # first: `as_user` below ends by removing the client's own sign-in.)
    db_session.query(RemediationPolicy).update({"enabled": False})
    db_session.commit()
    for url in (f"{base(test_project)}/export", f"{OVERVIEW}/export"):
        refused = client.get(url)
        assert refused.status_code == 404, (url, refused.text)
        assert "not enabled on this installation" in refused.json()["detail"]
    db_session.query(RemediationPolicy).update({"enabled": True})
    db_session.commit()

    with as_user(viewer):
        assert client.get(f"{base(test_project)}/export").status_code == 403
    with as_user(auditor):
        assert client.get(f"{base(test_project)}/export").status_code == 200


def test_the_cross_project_export_is_the_cross_project_list(client, db_session, test_project, worked):
    other = _project(db_session, "exp-other")
    theirs = _project(db_session, "exp-theirs")
    for project, net in ((other, 92), (theirs, 93)):
        ids = _rows(db_session, project, 3, severity="high", net=net, title=f"issue in {project.name}")
        apply(client, project, [{"finding_host_id": i, "notified_on": day(50)} for i in ids])

    def overview(**params):
        rows, offset = [], 0
        while True:
            page = client.get(OVERVIEW, params={**params, "limit": 7, "offset": offset}).json()
            rows += page["items"]
            if not page["has_more"]:
                return rows
            offset += 7

    for filters in ({}, {"state": "overdue"}, {"project_id": other.id}):
        answer = client.get(f"{OVERVIEW}/export", params=filters)
        assert answer.status_code == 200, answer.text
        assert answer.json()["items"] == overview(**filters)

    # Only the projects the caller administers.
    person = _user(db_session, "exp-admin")
    db_session.add_all([
        ProjectMembership(project_id=other.id, user_id=person.id, role="admin"),
        ProjectMembership(project_id=theirs.id, user_id=person.id, role="analyst"),
    ])
    db_session.commit()
    with as_user(person):
        mine = client.get(f"{OVERVIEW}/export").json()
        assert {row["project_name"] for row in mine["items"]} == {"exp-other"} and mine["total"] == 3
        assert client.get(f"{OVERVIEW}/export", params={"project_id": theirs.id}).status_code == 404
