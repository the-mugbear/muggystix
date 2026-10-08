"""Remediation deadlines (v2.461.0): the installation's switch and timelines,
the one definition of a deadline state, follow-ups by contact, the
cross-project reads and the alerts."""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Notification, Project, ProjectMembership
from app.db.models_remediation import FindingHostRemediation, RemediationEvent, RemediationPolicy
from app.main import app
from app.services import remediation_alerts, remediation_policy

TODAY = datetime.now(timezone.utc).date()
POLICY_URL = "/api/v1/remediation-policy"
OVERVIEW = "/api/v1/remediation-overview"


def day(n: int) -> str:
    """``n`` days ago, as the API takes a date."""
    return (TODAY - timedelta(days=n)).isoformat()


def base(project):
    return f"/api/v1/projects/{project.id}/remediation"


def _on(db, **overrides):
    values = dict(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90, days_low=120,
                  days_info=None, due_soon_days=7)
    values.update(overrides)
    db.add(RemediationPolicy(**values))
    db.commit()


@pytest.fixture
def on(db_session):
    _on(db_session)


def _project(db, name, status="active"):
    project = Project(name=name, slug=name, status=status, is_archived=(status == "archived"))
    db.add(project)
    db.flush()
    return project


_UID = [8600]


def _user(db, name, role=UserRole.MEMBER):
    _UID[0] += 1
    user = User(id=_UID[0], username=name, email=f"{name}@example.com", full_name=name.title(),
                hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=True,
                is_verified=True, created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    return user


def _rows(db, project, count, *, severity="high", net=90, title=None):
    """One finding on ``count`` hosts; returns the finding-on-host ids."""
    finding = Finding(project_id=project.id, title=title or f"{severity} issue", severity=severity,
                      status="confirmed", source="manual")
    db.add(finding)
    db.flush()
    links = []
    for n in range(1, count + 1):
        host = models.Host(project_id=project.id, ip_address=f"10.{net}.{n // 250}.{n % 250 + 1}", state="up")
        db.add(host)
        db.flush()
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(link)
        links.append(link)
    db.commit()
    return [link.id for link in links]


def apply(client, project, rows, **flags):
    response = client.post(f"{base(project)}/apply", json={"rows": rows, "overwrite": True, **flags})
    assert response.status_code == 200, response.text
    return response.json()


def listing(client, project, **params):
    response = client.get(base(project), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_id(page):
    return {item["finding_host_id"]: item for item in page["items"]}


def as_user(user):
    app.dependency_overrides[get_current_user] = lambda: user

    class _Restore:
        def __enter__(self):
            return user

        def __exit__(self, *exc):
            app.dependency_overrides.pop(get_current_user, None)
    return _Restore()


# --- the installation decides ------------------------------------------------

def test_an_installation_that_did_not_opt_in_has_no_remediation_at_all(client, db_session, test_project):
    _rows(db_session, test_project, 2)
    assert client.get(POLICY_URL).json() == {
        "enabled": False, "due_soon_days": 7,
        "days": {"critical": 30, "high": 30, "medium": 90, "low": 120, "info": None}}
    for method, url in (("get", base(test_project)), ("get", f"{base(test_project)}/contacts"),
                        ("get", OVERVIEW), ("get", f"{OVERVIEW}/projects"), ("get", f"{OVERVIEW}/contacts")):
        response = getattr(client, method)(url)
        assert response.status_code == 404, (url, response.text)
        assert "not enabled on this installation" in response.json()["detail"]
    refused = client.post(f"{base(test_project)}/apply", json={"rows": [{"finding_id": 1, "status": "closed"}]})
    assert refused.status_code == 404
    assert client.get("/api/v1/oversight/dashboard").json()["summary"]["remediation"] is None
    assert remediation_alerts.sweep(db_session) == 0


def test_turning_it_on_is_the_global_admins_and_is_audited(client, db_session, test_project):
    r = client.put(POLICY_URL, json={"enabled": True, "days": {"high": 45, "low": None}, "due_soon_days": 10})
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "due_soon_days": 10,
                        "days": {"critical": 30, "high": 45, "medium": 90, "low": None, "info": None}}
    entry = db_session.query(AuditLog).filter(AuditLog.action == "remediation_policy_updated").one()
    assert "45" in str(entry.details)
    assert client.get(base(test_project)).status_code == 200

    # The same values again change nothing and record nothing.
    client.put(POLICY_URL, json={"due_soon_days": 10})
    assert db_session.query(AuditLog).filter(AuditLog.action == "remediation_policy_updated").count() == 1

    member = _user(db_session, "pol-project-admin")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="admin"))
    db_session.commit()
    with as_user(member):
        assert client.put(POLICY_URL, json={"enabled": False}).status_code == 403
        assert client.get(POLICY_URL).json()["enabled"] is True      # every user may read it


@pytest.mark.parametrize("body", [
    {"days": {"high": 0}}, {"days": {"high": 4000}}, {"days": {"urgent": 5}},
    {"due_soon_days": -1}, {"due_soon_days": 400}, {"unknown": 1},
])
def test_a_timeline_that_makes_no_sense_is_refused(client, on, body):
    assert client.put(POLICY_URL, json=body).status_code == 422


# --- the deadline --------------------------------------------------------------

def test_each_state_is_decided_from_the_assigned_date_and_the_severity(client, db_session, test_project, on):
    high = _rows(db_session, test_project, 6, severity="high", net=90)
    info = _rows(db_session, test_project, 2, severity="info", net=91)
    apply(client, test_project, [
        {"finding_host_id": high[0], "notified_on": day(31)},                      # 30 days: 1 day overdue
        {"finding_host_id": high[1], "notified_on": day(30)},                      # due today
        {"finding_host_id": high[2], "notified_on": day(23)},                      # due in 7 days: the window's edge
        {"finding_host_id": high[3], "notified_on": day(22)},                      # due in 8 days
        {"finding_host_id": high[4], "notified_on": day(200), "status": "deferred"},
        # high[5]: nothing recorded
        {"finding_host_id": info[0], "notified_on": day(500)},                     # informational: no deadline
    ])
    page = listing(client, test_project, limit=200)
    rows = by_id(page)
    assert [rows[i]["state"] for i in high] == [
        "overdue", "due_soon", "due_soon", "on_track", "deferred", "not_assigned"]
    assert [rows[i]["days_left"] for i in high] == [-1, 0, 7, 8, None, None]
    assert rows[high[0]]["due_on"] == day(1) and rows[high[4]]["due_on"] is None
    assert [rows[i]["state"] for i in info] == ["no_deadline", "no_deadline"]
    assert rows[info[0]]["due_on"] is None
    assert page["state_counts"] == {"overdue": 1, "due_soon": 2, "on_track": 1, "not_assigned": 1,
                                    "no_deadline": 2, "deferred": 1, "closed": 0}
    assert page["status_counts"] == {"open": 7, "closed": 0, "deferred": 1}


def test_an_open_deadline_follows_the_current_timeline_and_the_current_severity(
        client, db_session, test_project, on):
    (row,) = _rows(db_session, test_project, 1, severity="high")
    apply(client, test_project, [{"finding_host_id": row, "notified_on": day(40)}])
    assert by_id(listing(client, test_project))[row]["state"] == "overdue"

    client.put(POLICY_URL, json={"days": {"high": 60}})
    moved = by_id(listing(client, test_project))[row]
    assert (moved["state"], moved["days_left"]) == ("on_track", 20)

    finding = db_session.query(Finding).filter_by(project_id=test_project.id).one()
    finding.severity = "low"                                # 120 days
    db_session.commit()
    assert by_id(listing(client, test_project))[row]["days_left"] == 80


def test_closing_freezes_the_deadline_and_reopening_derives_it_again(client, db_session, test_project, on):
    late, on_time, undated = _rows(db_session, test_project, 3, severity="high")
    apply(client, test_project, [
        {"finding_host_id": late, "notified_on": day(50), "status": "closed", "closed_on": day(10)},
        {"finding_host_id": on_time, "notified_on": day(50), "status": "closed", "closed_on": day(25)},
        {"finding_host_id": undated, "status": "closed", "closed_on": day(3)},
    ])
    rows = by_id(listing(client, test_project))
    assert (rows[late]["state"], rows[late]["due_on"], rows[late]["closed_days_late"]) == ("closed", day(20), 10)
    assert rows[on_time]["closed_days_late"] == 0
    assert rows[undated]["due_on"] is None and rows[undated]["closed_days_late"] is None

    # A longer timeline afterwards does not make a late closure on time.
    client.put(POLICY_URL, json={"days": {"high": 365}})
    assert by_id(listing(client, test_project))[late]["closed_days_late"] == 10

    apply(client, test_project, [{"finding_host_id": late, "status": "open"}])
    reopened = by_id(listing(client, test_project))[late]
    assert reopened["state"] == "on_track" and reopened["closed_days_late"] is None
    stored = db_session.query(FindingHostRemediation).filter_by(finding_host_id=late).one()
    db_session.refresh(stored)
    assert stored.closed_due_on is None


def test_every_state_count_equals_the_list_it_opens_across_pages(client, db_session, test_project, on):
    ids = _rows(db_session, test_project, 31, severity="medium")           # 90 days
    apply(client, test_project,
          [{"finding_host_id": i, "notified_on": day(100)} for i in ids[:13]]             # overdue
          + [{"finding_host_id": i, "notified_on": day(85)} for i in ids[13:20]]          # due soon
          + [{"finding_host_id": i, "notified_on": day(1)} for i in ids[20:24]]           # on track
          + [{"finding_host_id": i, "status": "closed"} for i in ids[24:26]])
    counts = listing(client, test_project)["state_counts"]
    assert counts == {"overdue": 13, "due_soon": 7, "on_track": 4, "not_assigned": 5, "no_deadline": 0,
                      "deferred": 0, "closed": 2}
    for state, expected in counts.items():
        seen, offset = [], 0
        while True:
            page = listing(client, test_project, state=state, limit=5, offset=offset)
            assert page["total"] == expected and page["state_counts"] == counts
            seen += [item["finding_host_id"] for item in page["items"]]
            assert all(item["state"] == state for item in page["items"])
            if not page["has_more"]:
                break
            offset += 5
        assert len(seen) == len(set(seen)) == expected
    both = listing(client, test_project, state=["overdue", "due_soon"], limit=200)
    assert both["total"] == 20
    # A status and a state that cannot both hold match nothing.
    assert listing(client, test_project, state="overdue", status="closed")["total"] == 0
    assert listing(client, test_project, status="open")["total"] == 29


def test_ordered_by_deadline_the_longest_overdue_comes_first(client, db_session, test_project, on):
    ids = _rows(db_session, test_project, 5, severity="high")
    apply(client, test_project, [
        {"finding_host_id": ids[0], "notified_on": day(5)},
        {"finding_host_id": ids[1], "notified_on": day(60)},
        {"finding_host_id": ids[2], "status": "closed"},
        {"finding_host_id": ids[3], "notified_on": day(35)},
        {"finding_host_id": ids[4], "notified_on": day(28)},
    ])
    order = [item["finding_host_id"] for item in listing(client, test_project, group="due")["items"]]
    assert order == [ids[1], ids[3], ids[4], ids[0], ids[2]]


# --- following up by contact ---------------------------------------------------

@pytest.fixture
def chased(client, db_session, test_project, on):
    ids = _rows(db_session, test_project, 5, severity="high", title="SMB signing not required")
    apply(client, test_project, [
        {"finding_host_id": ids[0], "contact_email": "roger@testdomain.com", "contact_name": "Roger Smith",
         "notified_on": day(45)},
        {"finding_host_id": ids[1], "contact_email": "roger@testdomain.com", "notified_on": day(26)},
        {"finding_host_id": ids[2], "contact_email": "roger@testdomain.com", "notified_on": day(2)},
        {"finding_host_id": ids[3], "contact_email": "jane@testdomain.com", "notified_on": day(1)},
        {"finding_host_id": ids[4], "contact_email": "jane@testdomain.com", "status": "closed"},
    ])
    return ids


def test_contacts_carry_their_states_and_the_most_overdue_comes_first(client, test_project, chased):
    items = client.get(f"{base(test_project)}/contacts").json()["items"]
    assert [c["contact_email"] for c in items] == ["roger@testdomain.com", "jane@testdomain.com"]
    roger, jane = items
    assert (roger["overdue"], roger["due_soon"], roger["on_track"], roger["open"], roger["total"]) == (1, 1, 1, 3, 3)
    assert (jane["overdue"], jane["due_soon"], jane["on_track"], jane["closed"]) == (0, 0, 1, 1)
    assert roger["last_follow_up_on"] is None


def test_the_follow_up_names_the_at_risk_rows_and_recording_it_is_once_a_day(
        client, db_session, test_project, chased):
    message = client.get(f"{base(test_project)}/follow-up", params={"contact_email": "Roger@TestDomain.com"}).json()
    assert (message["overdue"], message["due_soon"], message["contact_name"]) == (1, 1, "Roger Smith")
    assert [row["finding_host_id"] for row in message["items"]] == chased[:2]
    text = message["text"]
    assert text.startswith("Hello Roger Smith,")
    assert "Past the remediation deadline (1):" in text and "15 days overdue" in text
    assert "Approaching the deadline (1):" in text and "due in 4 days" in text
    assert "SMB signing not required (high)" in text
    assert text.count("\n- ") == 2                       # the on-track row is not in it

    body = {"contact_email": "roger@testdomain.com", "note": "Mailed; he asked for a week."}
    first = client.post(f"{base(test_project)}/follow-up", json=body).json()
    assert (first["recorded"], first["already_recorded"]) == (2, 0)
    again = client.post(f"{base(test_project)}/follow-up", json=body).json()
    assert (again["recorded"], again["already_recorded"]) == (0, 2)
    events = db_session.query(RemediationEvent).filter_by(kind="follow_up").all()
    assert len(events) == 2 and {e.finding_host_id for e in events} == set(chased[:2])
    assert all(e.body == "Mailed; he asked for a week." for e in events)

    rows = by_id(listing(client, test_project))
    assert rows[chased[0]]["last_follow_up_on"] == TODAY.isoformat()
    assert rows[chased[2]]["last_follow_up_on"] is None
    roger = client.get(f"{base(test_project)}/contacts").json()["items"][0]
    assert roger["last_follow_up_on"] == TODAY.isoformat()

    # A recorded follow-up is not a note: nobody edits or removes it.
    assert client.delete(f"{base(test_project)}/events/{events[0].id}").status_code == 409
    # Rows that are not this contact's at-risk rows are refused, nothing written.
    refused = client.post(f"{base(test_project)}/follow-up", json={
        "contact_email": "roger@testdomain.com", "finding_host_ids": [chased[2]]})
    assert refused.status_code == 422


def test_nothing_at_risk_says_so_and_records_nothing(client, db_session, test_project, chased):
    message = client.get(f"{base(test_project)}/follow-up", params={"contact_email": "jane@testdomain.com"}).json()
    assert message["items"] == [] and "Nothing assigned to you is overdue" in message["text"]
    recorded = client.post(f"{base(test_project)}/follow-up", json={"contact_email": "jane@testdomain.com"}).json()
    assert recorded["recorded"] == 0
    assert db_session.query(RemediationEvent).filter_by(kind="follow_up").count() == 0


# --- across projects -----------------------------------------------------------

def test_the_overview_covers_the_projects_the_caller_administers(client, db_session, test_project, on):
    mine, theirs = _project(db_session, "ov-mine"), _project(db_session, "ov-theirs")
    done = _project(db_session, "ov-archived", status="archived")
    a = _rows(db_session, mine, 2, net=92)
    b = _rows(db_session, theirs, 1, net=93)
    c = _rows(db_session, done, 1, net=94)
    for project, ids in ((mine, a), (theirs, b), (done, c)):
        # Through the cross-project mount: the project routes answer 410 for
        # an archived project, and its remediation still has to be recorded.
        written = client.post(f"{OVERVIEW}/projects/{project.id}/remediation/apply", json={"rows": [
            {"finding_host_id": i, "contact_email": "roger@testdomain.com", "notified_on": day(40)}
            for i in ids]})
        assert written.status_code == 200, written.text
    assert client.get(base(done)).status_code == 410

    # A global admin: every project, archived included.
    everything = client.get(OVERVIEW, params={"state": "overdue"}).json()
    assert everything["total"] == 4
    assert {row["project_name"] for row in everything["items"]} == {"ov-mine", "ov-theirs", "ov-archived"}
    table = client.get(f"{OVERVIEW}/projects").json()
    per_project = {row["name"]: row for row in table["items"]}
    assert per_project["ov-archived"]["archived"] is True and per_project["ov-archived"]["states"]["overdue"] == 1
    assert table["totals"]["overdue"] == 4
    assert per_project["ov-mine"]["states"] == listing(client, mine)["state_counts"]

    person = _user(db_session, "ov-admin")
    db_session.add_all([
        ProjectMembership(project_id=mine.id, user_id=person.id, role="admin"),
        ProjectMembership(project_id=done.id, user_id=person.id, role="admin"),
        ProjectMembership(project_id=theirs.id, user_id=person.id, role="analyst"),   # a member, not its admin
    ])
    db_session.commit()
    with as_user(person):
        page = client.get(OVERVIEW, params={"state": "overdue"}).json()
        assert page["total"] == 3 and {r["project_name"] for r in page["items"]} == {"ov-mine", "ov-archived"}
        assert [r["name"] for r in client.get(f"{OVERVIEW}/projects").json()["items"]] == ["ov-archived", "ov-mine"]
        assert client.get(OVERVIEW, params={"project_id": theirs.id}).status_code == 404
        assert client.get(OVERVIEW, params={"project_id": mine.id}).json()["total"] == 2
        contact = client.get(f"{OVERVIEW}/contacts").json()["items"][0]
        assert (contact["overdue"], contact["projects"]) == (3, 2)
        message = client.get(f"{OVERVIEW}/follow-up", params={"contact_email": "roger@testdomain.com"}).json()
        assert len(message["items"]) == 3 and sorted(message["project_ids"]) == sorted([mine.id, done.id])
        assert "[ov-mine]" in message["text"] and "[ov-theirs]" not in message["text"]
        # Reads and writes on an archived project they administer; not on one they do not.
        archived = f"{OVERVIEW}/projects/{done.id}/remediation"
        assert client.get(archived).json()["total"] == 1
        assert client.post(f"{archived}/follow-up", json={"contact_email": "roger@testdomain.com"}).json()["recorded"] == 1
        assert client.get(f"{OVERVIEW}/projects/{theirs.id}/remediation").status_code == 403

    nobody = _user(db_session, "ov-nobody")
    db_session.commit()
    with as_user(nobody):
        assert client.get(OVERVIEW).json()["total"] == 0
        assert client.get(f"{OVERVIEW}/projects").json()["items"] == []


def test_oversight_shows_every_project_once_the_installation_tracks_remediation(
        client, db_session, test_project, on):
    used, unused = _project(db_session, "os-used"), _project(db_session, "os-unused")
    ids = _rows(db_session, used, 4, net=95)
    _rows(db_session, unused, 2, net=96)
    apply(client, used, [
        {"finding_host_id": ids[0], "notified_on": day(41)},
        {"finding_host_id": ids[1], "notified_on": day(35)},
        {"finding_host_id": ids[2], "notified_on": day(50), "status": "closed", "closed_on": day(10)},
        {"finding_host_id": ids[3], "notified_on": day(50), "status": "closed", "closed_on": day(30)},
    ])
    body = client.get("/api/v1/oversight/dashboard").json()
    rows = {row["id"]: row["remediation"] for row in body["projects"]}
    assert (rows[used.id]["overdue"], rows[used.id]["open"], rows[used.id]["closed"]) == (2, 2, 2)
    assert rows[used.id]["longest_overdue_days"] == 11
    assert (rows[used.id]["closed_late"], rows[used.id]["closed_with_deadline"]) == (1, 2)
    assert rows[used.id]["avg_days_to_close"] == 30.0
    # A project that assigned nobody is "not assigned", never overdue.
    assert (rows[unused.id]["not_assigned"], rows[unused.id]["overdue"], rows[unused.id]["open"]) == (2, 0, 2)
    # Nothing to measure is null, never a zero.
    assert rows[unused.id]["longest_overdue_days"] is None and rows[unused.id]["avg_days_to_close"] is None
    total = body["summary"]["remediation"]
    assert (total["overdue"], total["not_assigned"], total["longest_overdue_days"]) == (2, 2, 11)
    # The current state: the dates and the severity basis do not apply to it.
    old = client.get("/api/v1/oversight/dashboard",
                     params={"start": "2020-01-01", "end": "2020-01-31", "severity_basis": "period"}).json()
    assert old["summary"]["remediation"] == total
    assert total["days"]["high"] == 30 and total["due_soon_days"] == 7
    page = listing(client, used)["state_counts"]
    assert {k: rows[used.id][k] for k in ("overdue", "due_soon", "on_track", "not_assigned")} == {
        k: page[k] for k in ("overdue", "due_soon", "on_track", "not_assigned")}


# --- alerts ----------------------------------------------------------------------

def _alerts(db, kind=None):
    query = db.query(Notification).filter(Notification.type == "remediation")
    if kind:
        query = query.filter(Notification.source_type == f"remediation_{kind}")
    return query.all()


def test_a_deadline_alerts_once_per_kind_to_project_and_global_admins(client, db_session, test_project, on, test_user):
    project_admin, analyst = _user(db_session, "al-admin"), _user(db_session, "al-analyst")
    db_session.add_all([
        ProjectMembership(project_id=test_project.id, user_id=project_admin.id, role="admin"),
        ProjectMembership(project_id=test_project.id, user_id=analyst.id, role="analyst"),
    ])
    ids = _rows(db_session, test_project, 4, severity="high")
    apply(client, test_project, [
        {"finding_host_id": ids[0], "notified_on": day(25)},        # due in 5 days
        {"finding_host_id": ids[1], "notified_on": day(24)},        # due in 6 days
        {"finding_host_id": ids[2], "notified_on": day(40)},        # overdue
        {"finding_host_id": ids[3], "notified_on": day(1)},         # on track
    ])
    assert remediation_alerts.sweep(db_session) == 4       # two kinds x two recipients
    db_session.commit()
    soon, overdue = _alerts(db_session, "due_soon"), _alerts(db_session, "overdue")
    assert {n.user_id for n in soon} == {n.user_id for n in overdue} == {test_user.id, project_admin.id}
    assert soon[0].title == "test-project: 2 findings on hosts are due within 7 days"
    assert overdue[0].title == "test-project: 1 finding on a host is past the remediation deadline"
    assert soon[0].project_id == test_project.id

    # The same deadlines say nothing twice.
    assert remediation_alerts.sweep(db_session) == 0

    # Time passes: the due-soon rows become overdue and alert as that.
    assert remediation_alerts.sweep(db_session, today=TODAY + timedelta(days=7)) == 2
    db_session.commit()
    assert _alerts(db_session, "overdue")[-1].title.startswith("test-project: 2 findings on hosts are past")
    assert remediation_alerts.sweep(db_session, today=TODAY + timedelta(days=7)) == 0


def test_a_moved_deadline_alerts_again_and_a_closed_row_never(client, db_session, test_project, on):
    moved, closed = _rows(db_session, test_project, 2, severity="high")
    apply(client, test_project, [{"finding_host_id": moved, "notified_on": day(40)},
                                 {"finding_host_id": closed, "notified_on": day(40)}])
    assert remediation_alerts.sweep(db_session) == 1
    db_session.commit()
    apply(client, test_project, [{"finding_host_id": closed, "status": "closed"},
                                 {"finding_host_id": moved, "notified_on": day(35)}])
    assert remediation_alerts.sweep(db_session) == 1      # the re-assigned row; the closed one is done
    db_session.commit()
    assert remediation_alerts.sweep(db_session) == 0
    assert remediation_policy.load(db_session).enabled


# --- what a manager is asked: severity, how late, who is not being chased ----------

@pytest.fixture
def backlog(client, db_session, test_project, on):
    """Critical x3, medium x3, low x2, with assorted assigned dates."""
    crit = _rows(db_session, test_project, 3, severity="critical", net=97, title="crit")
    med = _rows(db_session, test_project, 3, severity="medium", net=98, title="med")
    low = _rows(db_session, test_project, 2, severity="low", net=99, title="low")
    apply(client, test_project, [
        {"finding_host_id": crit[0], "notified_on": day(33), "team": "Platform", "contact_email": "a@testdomain.com"},   # 3 late
        {"finding_host_id": crit[1], "notified_on": day(50), "team": "platform", "contact_email": "b@testdomain.com"},   # 20 late
        {"finding_host_id": crit[2], "notified_on": day(26), "team": "Web", "contact_email": "c@testdomain.com"},        # due in 4
        {"finding_host_id": med[0], "notified_on": day(130), "team": "Web", "contact_email": "c@testdomain.com"},        # 40 late
        {"finding_host_id": med[1], "notified_on": day(200), "contact_email": "d@testdomain.com"},                       # 110 late, no team
        {"finding_host_id": med[2], "notified_on": day(10), "team": "Web"},                                              # on track
        {"finding_host_id": low[0], "notified_on": day(118)},                                                            # due in 2
    ])
    return {"crit": crit, "med": med, "low": low}


def test_overdue_and_due_soon_by_severity_each_opening_its_list(client, test_project, backlog):
    page = listing(client, test_project)
    assert page["severity_counts"] == {
        "critical": {"overdue": 2, "due_soon": 1}, "high": {"overdue": 0, "due_soon": 0},
        "medium": {"overdue": 2, "due_soon": 0}, "low": {"overdue": 0, "due_soon": 1},
        "info": {"overdue": 0, "due_soon": 0}}
    for severity, counts in page["severity_counts"].items():
        for state, expected in counts.items():
            assert listing(client, test_project, state=state, severity=severity)["total"] == expected
    # Choosing a severity narrows the rows and the state chips, not the breakdown.
    one = listing(client, test_project, severity="critical")
    assert one["severity_counts"] == page["severity_counts"]
    assert (one["state_counts"]["overdue"], one["total"]) == (2, 3)
    assert one["overdue_ages"] == {"1-7": 1, "8-30": 1, "31-90": 0, "90+": 0}


def test_how_late_the_overdue_rows_are_and_each_band_opens_its_rows(client, test_project, backlog):
    page = listing(client, test_project)
    assert page["overdue_ages"] == {"1-7": 1, "8-30": 1, "31-90": 1, "90+": 1}
    assert sum(page["overdue_ages"].values()) == page["state_counts"]["overdue"]
    for band, expected in page["overdue_ages"].items():
        rows = listing(client, test_project, overdue_band=band)
        assert rows["total"] == expected == len(rows["items"])
        assert all(item["state"] == "overdue" for item in rows["items"])
    assert listing(client, test_project, overdue_band="90+")["items"][0]["days_left"] == -110
    assert client.get(base(test_project), params={"overdue_band": "soon"}).status_code == 422


def test_at_risk_rows_nobody_is_chasing(client, db_session, test_project, backlog):
    page = listing(client, test_project)
    assert (page["not_followed_up"], page["not_followed_up_days"]) == (6, 7)      # every at-risk row, none chased
    client.post(f"{base(test_project)}/follow-up", json={"contact_email": "c@testdomain.com"})
    after = listing(client, test_project)
    assert after["not_followed_up"] == 4
    stale = listing(client, test_project, no_follow_up_days=7)
    assert stale["total"] == 4 == len(stale["items"])
    assert all(item["state"] in ("overdue", "due_soon") and item["contact_email"] != "c@testdomain.com"
               for item in stale["items"])
    # A follow-up that is itself old counts as not chased again.
    stored = db_session.query(FindingHostRemediation).filter(
        FindingHostRemediation.last_follow_up_on.isnot(None)).all()
    for record in stored:
        record.last_follow_up_on = TODAY - timedelta(days=30)
    db_session.commit()
    assert listing(client, test_project, no_follow_up_days=14)["total"] == 6
    assert listing(client, test_project, no_follow_up_days=60)["total"] == 4


def test_teams_group_people_without_minding_case(client, test_project, backlog):
    items = client.get(f"{base(test_project)}/teams").json()["items"]
    by_team = {(item["team"] or "").lower(): item for item in items}
    assert set(by_team) == {"platform", "web", ""}
    assert (by_team["platform"]["overdue"], by_team["platform"]["contacts"], by_team["platform"]["total"]) == (2, 2, 2)
    assert (by_team["web"]["overdue"], by_team["web"]["due_soon"], by_team["web"]["on_track"]) == (1, 1, 1)
    assert by_team[""]["overdue"] == 1 and items[-1]["team"] is None       # no team: last
    assert [item["team"].lower() for item in items[:2]] == ["platform", "web"]   # most overdue first
    rows = listing(client, test_project, team="PLATFORM")
    assert rows["total"] == 2 and {item["team"].lower() for item in rows["items"]} == {"platform"}
    # A team is a tracked field: the change is on the timeline, and clearing it works.
    apply(client, test_project, [{"finding_host_id": backlog["crit"][0], "team": None}])
    assert listing(client, test_project, team="platform")["total"] == 1


def test_the_daily_count_is_todays_and_the_trend_reads_it(client, db_session, test_project, backlog):
    from app.db.models_remediation import RemediationDaily
    from app.services import remediation_service
    assert remediation_service.snapshot(db_session) == 1
    assert remediation_service.snapshot(db_session) == 1          # replaced, not added
    db_session.add(RemediationDaily(project_id=test_project.id, day=TODAY - timedelta(days=3), overdue=1, on_track=6))
    db_session.commit()
    assert db_session.query(RemediationDaily).filter_by(day=TODAY).count() == 1
    apply(client, test_project, [
        {"finding_host_id": backlog["crit"][1], "status": "closed", "closed_on": day(0)},      # 20 days late
        {"finding_host_id": backlog["crit"][2], "status": "closed", "closed_on": day(0)},      # on time
        {"finding_host_id": backlog["low"][1], "status": "closed", "closed_on": day(0)},       # never assigned: no deadline
    ])
    trend = client.get(f"{base(test_project)}/trend").json()
    assert [point["day"] for point in trend["daily"]] == [(TODAY - timedelta(days=3)).isoformat(), TODAY.isoformat()]
    assert trend["daily"][0]["overdue"] == 1
    assert trend["daily"][1]["overdue"] == listing(client, test_project)["state_counts"]["overdue"] + 1   # counted before the closure
    assert trend["closed_by_month"] == [
        {"month": TODAY.strftime("%Y-%m"), "on_time": 1, "late": 1, "no_deadline": 1}]
    across = client.get(f"{OVERVIEW}/trend", params={"days": 30}).json()
    assert across["daily"] == trend["daily"] and across["closed_by_month"] == trend["closed_by_month"]


def test_no_history_is_written_while_the_installation_has_it_off(db_session, test_project):
    from app.services import remediation_service
    _rows(db_session, test_project, 2)
    assert remediation_service.snapshot(db_session) == 0


def test_oversight_breaks_overdue_down_by_severity_and_age(client, test_project, backlog):
    block = client.get("/api/v1/oversight/dashboard").json()["summary"]["remediation"]
    assert (block["overdue_critical"], block["overdue_medium"], block["overdue_high"]) == (2, 2, 0)
    assert [block[k] for k in ("overdue_age_1_7", "overdue_age_8_30", "overdue_age_31_90", "overdue_age_90_plus")] == [1, 1, 1, 1]
    assert block["overdue"] == 4
