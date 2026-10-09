"""Managing due dates: a deadline set by hand, a deferral with a review date,
the flags, search, starting the clock from an issued report, the policy
preview, alerts that name rows, one contact across projects, and a severity
change on the record."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Notification, Project, ProjectMembership
from app.db.models_remediation import FindingHostRemediation, RemediationEvent, RemediationPolicy
from app.db.models_reports import Report
from app.main import app
from app.services import remediation_alerts

TODAY = datetime.now(timezone.utc).date()
POLICY_URL = "/api/v1/remediation-policy"
OVERVIEW = "/api/v1/remediation-overview"
AGENT = "/api/v1/agent/remediation"
WHY = [{"body": "Agreed with the owner on the call."}]


def day(n: int) -> str:
    """``n`` days ago (negative: ahead), as the API takes a date."""
    return (TODAY - timedelta(days=n)).isoformat()


def base(project) -> str:
    return f"/api/v1/projects/{project.id}/remediation"


@pytest.fixture
def on(db_session):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, days_info=None, due_soon_days=7))
    db_session.commit()


_NET = [100]


def _rows(db, project, count, *, severity="high", title=None, hostname=None):
    """One finding on ``count`` new hosts; ``(finding, [finding-on-host ids])``."""
    _NET[0] += 1
    finding = Finding(project_id=project.id, title=title or f"{severity} issue {_NET[0]}", severity=severity,
                      status="confirmed", source="manual")
    db.add(finding)
    db.flush()
    links = []
    for n in range(1, count + 1):
        host = models.Host(project_id=project.id, ip_address=f"10.{_NET[0]}.{n // 250}.{n % 250 + 1}", state="up",
                           hostname=hostname)
        db.add(host)
        db.flush()
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(link)
        links.append(link)
    db.commit()
    return finding, [link.id for link in links]


def send(client, project, rows, **flags):
    return client.post(f"{base(project)}/apply", json={"rows": rows, "overwrite": True, **flags})


def apply(client, project, rows, **flags):
    response = send(client, project, rows, **flags)
    assert response.status_code == 200, response.text
    return response.json()


def listing(client, url, **params):
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_id(page):
    return {item["finding_host_id"]: item for item in page["items"]}


def paged(client, url, *, limit=5, headers=None, **params):
    seen, offset, first = [], 0, None
    while True:
        response = client.get(url, params={**params, "limit": limit, "offset": offset}, headers=headers)
        assert response.status_code == 200, response.text
        page = response.json()
        first = first or page
        seen += page["items"]
        if not page["has_more"]:
            return seen, first
        offset += limit


def record(db, fh_id):
    db.expire_all()
    return db.query(FindingHostRemediation).filter_by(finding_host_id=fh_id).one_or_none()


_UID = [8800]


def _user(db, name, role=UserRole.MEMBER):
    _UID[0] += 1
    user = User(id=_UID[0], username=name, email=f"{name}@example.com", full_name=name.title(),
                hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=True,
                is_verified=True, created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    return user


def _project(db, name, status="active"):
    project = Project(name=name, slug=name, status=status, is_archived=(status == "archived"))
    db.add(project)
    db.flush()
    return project


class as_user:
    def __init__(self, user):
        self.user = user

    def __enter__(self):
        self.before = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: self.user

    def __exit__(self, *exc):
        app.dependency_overrides[get_current_user] = self.before        # the ``client`` fixture's admin


def _alerts(db, kind):
    return (db.query(Notification).filter(Notification.source_type == remediation_alerts.SOURCE_TYPES[kind])
            .order_by(Notification.id).all())


# --- 1. a deadline set by hand ---------------------------------------------------

def test_a_hand_set_deadline_is_the_deadline_until_it_is_cleared(client, db_session, test_project, on):
    _, (late, plain) = _rows(db_session, test_project, 2)
    apply(client, test_project, [{"finding_host_id": i, "notified_on": day(40)} for i in (late, plain)])
    before = by_id(listing(client, base(test_project)))[late]
    assert (before["state"], before["due_on"], before["deadline_source"]) == ("overdue", day(10), "policy")
    assert before["policy_due_on"] == day(10) and before["due_override_on"] is None

    result = apply(client, test_project, [{"finding_host_id": late, "due_override_on": day(-20), "notes": WHY}])
    assert result["rows"][0]["changed"] == [
        {"finding_host_id": late, "field": "due_override_on", "from": None, "to": day(-20)}]
    page = listing(client, base(test_project))
    row = by_id(page)[late]
    assert (row["state"], row["due_on"], row["days_left"]) == ("on_track", day(-20), 20)
    assert (row["policy_due_on"], row["due_override_on"], row["deadline_source"]) == (day(10), day(-20), "override")
    assert page["state_counts"]["overdue"] == 1 and page["state_counts"]["on_track"] == 1
    assert [r["finding_host_id"] for r in listing(client, base(test_project), state="overdue")["items"]] == [plain]
    # The alert follows the deadline in force: only the row still on the policy's date.
    assert remediation_alerts.sweep(db_session) == 1
    db_session.commit()
    assert "1 finding on a host is past" in _alerts(db_session, "overdue")[0].title

    # Clearing it goes back to the policy's date — and that needs a reason too.
    assert send(client, test_project, [{"finding_host_id": late, "due_override_on": None}]).status_code == 422
    apply(client, test_project, [{"finding_host_id": late, "due_override_on": None, "notes": WHY}])
    row = by_id(listing(client, base(test_project)))[late]
    assert (row["state"], row["due_on"], row["deadline_source"]) == ("overdue", day(10), "policy")
    fields = [e.field for e in db_session.query(RemediationEvent).filter_by(finding_host_id=late, kind="change")]
    assert fields.count("due_override_on") == 2


def test_a_hand_set_deadline_runs_without_an_assigned_date_and_without_a_timeline(
        client, db_session, test_project, on):
    _, (unassigned,) = _rows(db_session, test_project, 1)
    _, (informational,) = _rows(db_session, test_project, 1, severity="info")
    rows = by_id(listing(client, base(test_project)))
    assert (rows[unassigned]["state"], rows[informational]["state"]) == ("not_assigned", "no_deadline")
    apply(client, test_project, [
        {"finding_host_id": unassigned, "due_override_on": day(3), "notes": WHY},
        {"finding_host_id": informational, "due_override_on": day(-3), "notes": WHY}])
    rows = by_id(listing(client, base(test_project)))
    assert (rows[unassigned]["state"], rows[unassigned]["days_left"]) == ("overdue", -3)
    assert (rows[informational]["state"], rows[informational]["due_on"]) == ("due_soon", day(-3))
    assert rows[informational]["policy_due_on"] is None and rows[informational]["deadline_source"] == "override"
    assert rows[unassigned]["policy_due_on"] is None


def test_closing_freezes_the_hand_set_deadline(client, db_session, test_project, on):
    _, (fh,) = _rows(db_session, test_project, 1)
    apply(client, test_project, [{"finding_host_id": fh, "notified_on": day(40), "due_override_on": day(-5),
                                  "notes": WHY}])
    apply(client, test_project, [{"finding_host_id": fh, "status": "closed", "closed_on": day(0)}])
    assert record(db_session, fh).closed_due_on.isoformat() == day(-5)
    row = by_id(listing(client, base(test_project)))[fh]
    assert (row["state"], row["due_on"], row["closed_days_late"]) == ("closed", day(-5), 0)


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_hand_set_deadline_without_a_note_refuses_the_whole_call(client, db_session, test_project, on, dry_run):
    _, (first, second) = _rows(db_session, test_project, 2)
    refused = send(client, test_project, [
        {"finding_host_id": first, "contact_name": "Roger Smith"},                   # fine by itself
        {"finding_host_id": second, "due_override_on": day(-10)},
    ], dry_run=dry_run)
    assert refused.status_code == 422, refused.text
    problem = refused.json()["detail"]["problems"][0]
    assert f"finding_host_id {second}" in problem and "needs a note" in problem
    # The earlier row of the same call was not written either.
    assert db_session.query(FindingHostRemediation).count() == 0
    assert db_session.query(RemediationEvent).count() == 0


def test_a_changed_hand_set_deadline_alerts_again(client, db_session, test_project, on):
    _, (fh,) = _rows(db_session, test_project, 1)
    apply(client, test_project, [{"finding_host_id": fh, "due_override_on": day(5), "notes": WHY}])
    assert remediation_alerts.sweep(db_session) == 1
    db_session.commit()
    assert remediation_alerts.sweep(db_session) == 0
    assert record(db_session, fh).overdue_alerted_for.isoformat() == day(5)
    apply(client, test_project, [{"finding_host_id": fh, "due_override_on": day(2), "notes": WHY}])
    assert record(db_session, fh).overdue_alerted_for is None
    assert remediation_alerts.sweep(db_session) == 1


# --- 2. a deferral has a review date and a reason ---------------------------------

@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("row, said", [
    ({"status": "deferred"}, "needs deferred_review_on"),
    ({"status": "deferred", "notes": WHY}, "needs deferred_review_on"),
    ({"status": "deferred", "deferred_review_on": day(-30)}, "needs a note"),
    ({"status": "deferred", "deferred_review_on": day(1), "notes": WHY}, "cannot be in the past"),
])
def test_a_deferral_without_its_date_or_its_reason_is_refused(client, db_session, test_project, on,
                                                               dry_run, row, said):
    _, (first, second) = _rows(db_session, test_project, 2)
    refused = send(client, test_project, [{"finding_host_id": first, "team": "Platform"},
                                          {"finding_host_id": second, **row}], dry_run=dry_run)
    assert refused.status_code == 422, refused.text
    assert said in str(refused.json()), refused.text
    assert db_session.query(FindingHostRemediation).count() == 0       # nor the row before it


def test_a_review_date_goes_only_with_a_deferral_and_leaves_with_it(client, db_session, test_project, on):
    _, (fh,) = _rows(db_session, test_project, 1)
    assert send(client, test_project, [{"finding_host_id": fh, "deferred_review_on": day(-30),
                                        "notes": WHY}]).status_code == 422          # the row is open
    assert send(client, test_project, [{"finding_host_id": fh, "status": "closed",
                                        "deferred_review_on": day(-30)}]).status_code == 422
    apply(client, test_project, [{"finding_host_id": fh, "status": "deferred", "deferred_review_on": day(-30),
                                  "notes": WHY}])
    row = by_id(listing(client, base(test_project)))[fh]
    assert (row["state"], row["deferred_review_on"], row["deferral_review_due"]) == ("deferred", day(-30), False)

    # While deferred: the date moves only with a reason, and is never taken away.
    assert send(client, test_project, [{"finding_host_id": fh, "deferred_review_on": day(-60)}]).status_code == 422
    assert send(client, test_project, [{"finding_host_id": fh, "deferred_review_on": None,
                                        "notes": WHY}]).status_code == 422
    apply(client, test_project, [{"finding_host_id": fh, "deferred_review_on": day(-60), "notes": WHY}])
    assert record(db_session, fh).deferred_review_on.isoformat() == day(-60)
    # Another field on a deferred row asks for nothing.
    apply(client, test_project, [{"finding_host_id": fh, "team": "Platform"}])

    # Leaving the deferral clears the date by itself, with no note asked for.
    result = apply(client, test_project, [{"finding_host_id": fh, "status": "open"}])
    assert {(c["field"], c["to"]) for c in result["rows"][0]["changed"]} == {
        ("status", "open"), ("deferred_review_on", None)}
    stored = record(db_session, fh)
    assert (stored.status, stored.deferred_review_on) == ("open", None)
    assert by_id(listing(client, base(test_project)))[fh]["deferral_review_due"] is False


def test_a_refused_status_change_keeps_the_review_date_and_reports_one_conflict(
        client, db_session, test_project, on):
    _, (fh,) = _rows(db_session, test_project, 1)
    apply(client, test_project, [{"finding_host_id": fh, "status": "deferred", "deferred_review_on": day(-30),
                                  "notes": WHY}])
    kept = client.post(f"{base(test_project)}/apply", json={"rows": [{"finding_host_id": fh, "status": "open"}]})
    assert kept.status_code == 200, kept.text
    assert [c["field"] for c in kept.json()["rows"][0]["conflicts"]] == ["status"]
    stored = record(db_session, fh)
    assert (stored.status, stored.deferred_review_on.isoformat()) == ("deferred", day(-30))


def test_the_database_keeps_a_review_date_on_deferred_rows_only(db_session, test_project, on):
    _, (fh,) = _rows(db_session, test_project, 1)
    db_session.add(FindingHostRemediation(finding_host_id=fh, project_id=test_project.id, status="open",
                                          deferred_review_on=TODAY))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def _legacy_deferral(db, project, fh_id):
    """A row deferred before review dates existed."""
    db.add(FindingHostRemediation(finding_host_id=fh_id, project_id=project.id, status="deferred"))
    db.commit()


def test_a_deferral_from_before_review_dates_reads_as_due_for_review(client, db_session, test_project, on):
    _, (old, new) = _rows(db_session, test_project, 2)
    _legacy_deferral(db_session, test_project, old)
    apply(client, test_project, [{"finding_host_id": new, "status": "deferred", "deferred_review_on": day(-9),
                                  "notes": WHY}])
    rows = by_id(listing(client, base(test_project)))
    assert (rows[old]["deferred_review_on"], rows[old]["deferral_review_due"]) == (None, True)
    assert rows[new]["deferral_review_due"] is False
    due = listing(client, base(test_project), flag="deferral_review_due")
    assert [r["finding_host_id"] for r in due["items"]] == [old]
    # It can be given a date like any other — with a reason.
    apply(client, test_project, [{"finding_host_id": old, "deferred_review_on": day(-14), "notes": WHY}])
    assert listing(client, base(test_project), flag="deferral_review_due")["total"] == 0


# --- 3. flags ----------------------------------------------------------------------

@pytest.fixture
def flagged(client, db_session, test_project, on):
    """More of each flag than a page of five holds."""
    _, review = _rows(db_session, test_project, 9)
    _, moved = _rows(db_session, test_project, 8, severity="medium")
    _rows(db_session, test_project, 4)
    for fh in review[:3]:
        _legacy_deferral(db_session, test_project, fh)
    apply(client, test_project,
          [{"finding_host_id": i, "status": "deferred", "deferred_review_on": day(0), "notes": WHY}
           for i in review[3:7]]                                                     # the day has come
          + [{"finding_host_id": i, "status": "deferred", "deferred_review_on": day(-20), "notes": WHY}
             for i in review[7:]]                                                    # not yet
          + [{"finding_host_id": i, "notified_on": day(100), "due_override_on": day(-10), "notes": WHY}
             for i in moved[:6]]
          + [{"finding_host_id": i, "notified_on": day(100)} for i in moved[6:]])     # overdue by the policy
    return {"deferral_review_due": set(review[:7]), "deadline_overridden": set(moved[:6])}


def test_each_flag_count_is_the_list_it_opens_across_pages(client, test_project, flagged):
    counts = listing(client, base(test_project))["flag_counts"]
    assert counts == {"deferral_review_due": 7, "deadline_overridden": 6}
    for flag, expected in flagged.items():
        rows, first = paged(client, base(test_project), flag=flag)
        ids = [row["finding_host_id"] for row in rows]
        assert len(ids) == len(set(ids)) == counts[flag] == first["total"]
        assert set(ids) == expected
        # Taken BEFORE this filter: both stay on every page; the states follow it.
        assert first["flag_counts"] == counts
        assert sum(first["state_counts"].values()) == first["total"]
    assert all(row["deferral_review_due"] for row in paged(client, base(test_project),
                                                           flag="deferral_review_due")[0])
    assert all(row["deadline_source"] == "override" for row in paged(client, base(test_project),
                                                                     flag="deadline_overridden")[0])


def test_the_flag_counts_ignore_state_status_and_verification_and_follow_the_rest(client, test_project, flagged):
    whole = {"deferral_review_due": 7, "deadline_overridden": 6}
    for params in ({"state": "overdue"}, {"status": "deferred"}, {"verification": "remediated_record_open"},
                   {"flag": "deadline_overridden"}):
        assert listing(client, base(test_project), **params)["flag_counts"] == whole, params
    medium = listing(client, base(test_project), severity="medium")
    assert medium["flag_counts"] == {"deferral_review_due": 0, "deadline_overridden": 6}
    # With a state, the list is both.
    assert listing(client, base(test_project), flag="deadline_overridden", state="on_track")["total"] == 6
    assert listing(client, base(test_project), flag="deadline_overridden", state="overdue")["total"] == 0
    # The gap counts are taken before the flag filter, as the flags before theirs.
    assert listing(client, base(test_project), flag="deadline_overridden")["verification_counts"] == \
        listing(client, base(test_project))["verification_counts"]


def test_the_flags_are_on_the_agent_and_cross_project_mounts(client, test_project, flagged):
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    headers = {"X-API-Key": key.json()["api_key"]}
    for url, with_key in ((AGENT, headers), (OVERVIEW, None),
                          (f"{OVERVIEW}/projects/{test_project.id}/remediation", None)):
        for flag, expected in flagged.items():
            rows, first = paged(client, url, headers=with_key, flag=flag)
            assert {row["finding_host_id"] for row in rows} == expected, url
            assert first["flag_counts"] == {"deferral_review_due": 7, "deadline_overridden": 6}
    assert client.get(base(test_project), params={"flag": "overdue"}).status_code == 422


def test_the_list_takes_no_more_statements_for_the_flags_and_the_search(client, test_project, flagged):
    def statements(**params):
        seen: list = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            seen.append(" ".join(statement.split()))

        event.listen(Engine, "after_cursor_execute", _count)
        try:
            listing(client, base(test_project), **params)
        finally:
            event.remove(Engine, "after_cursor_execute", _count)
        return [s for s in seen if "finding_hosts" in s]

    assert len(statements()) == 3
    assert len(statements(flag="deferral_review_due")) == 3
    assert len(statements(flag="deadline_overridden", q="issue", state="on_track")) == 3


# --- 4. search ---------------------------------------------------------------------

def test_search_matches_title_address_and_host_name_and_the_counts_follow(client, db_session, test_project, on):
    _, tls = _rows(db_session, test_project, 3, title="Weak TLS ciphers", hostname="web-prod.example.com")
    _, smb = _rows(db_session, test_project, 2, title="SMB signing 100% off", hostname="files_01")
    _, other = _rows(db_session, test_project, 2, title="Open relay", hostname="filesX01")
    apply(client, test_project, [{"finding_host_id": i, "notified_on": day(40)} for i in tls[:2]]
          + [{"finding_host_id": smb[0], "due_override_on": day(-5), "notes": WHY}])
    found = lambda q, **more: {r["finding_host_id"] for r in listing(  # noqa: E731
        client, base(test_project), q=q, limit=200, **more)["items"]}
    assert found("weak tls") == set(tls)                                   # the title, whatever the case
    assert found("WEB-PROD") == set(tls)                                   # the host's name
    address = db_session.get(models.Host, db_session.get(FindingHost, other[1]).host_id).ip_address
    assert found(address) == {other[1]}                                    # the address
    # LIKE's own characters are text.
    assert found("100%") == set(smb) and found("100% off") == set(smb)
    assert found("files_01") == set(smb)                                   # not filesX01
    assert found("0%o") == set()
    assert found("no such thing") == set()
    # Every count is of the searched rows.
    page = listing(client, base(test_project), q="weak tls")
    assert page["total"] == 3 and page["state_counts"]["overdue"] == 2 and page["state_counts"]["not_assigned"] == 1
    assert page["severity_counts"]["high"]["overdue"] == 2
    assert page["flag_counts"]["deadline_overridden"] == 0
    assert listing(client, base(test_project), q="smb")["flag_counts"]["deadline_overridden"] == 1
    assert found("weak tls", state="overdue") == set(tls[:2])
    # The same on the other mounts; too short a word is refused.
    assert listing(client, OVERVIEW, q="weak tls")["total"] == 3
    assert listing(client, f"{OVERVIEW}/projects/{test_project.id}/remediation", q="weak tls")["total"] == 3
    assert client.get(base(test_project), params={"q": "w"}).status_code == 422


# --- 5. starting the clock from a report --------------------------------------------

@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"]}))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(folder.parent))


def _issue(client, project, **body):
    created = client.post(f"/api/v1/projects/{project.id}/client-reports", json=body or {"kind": "full"})
    assert created.status_code == 201, created.text
    issued = client.post(f"/api/v1/projects/{project.id}/client-reports/{created.json()['id']}/issue")
    assert issued.status_code == 200, issued.text
    return issued.json()


@pytest.fixture
def reported(client, db_session, test_project, on, template_dir):
    """An issued report of four findings on hosts, and what happened since."""
    one, (a, b) = _rows(db_session, test_project, 2, title="SQL injection")
    _, (c,) = _rows(db_session, test_project, 1, title="Weak TLS")
    _, (gone,) = _rows(db_session, test_project, 1, title="Default credentials")
    report = _issue(client, test_project)
    # Since: one row was assigned by hand, one was reported fixed, one system
    # left its finding, and the first finding reached a host the report never listed.
    apply(client, test_project, [{"finding_host_id": a, "notified_on": day(3)},
                                 {"finding_host_id": c, "status": "closed"}])
    db_session.delete(db_session.get(FindingHost, gone))
    late_host = models.Host(project_id=test_project.id, ip_address="10.250.0.9", state="up")
    db_session.add(late_host)
    db_session.flush()
    later = FindingHost(finding_id=one.id, host_id=late_host.id, host_status="open")
    db_session.add(later)
    db_session.commit()
    return {"report": report, "a": a, "b": b, "c": c, "later": later.id}


def test_assigning_from_a_report_dates_what_it_listed_and_nothing_else(client, db_session, test_project, reported):
    url, body = f"{base(test_project)}/assign-from-report", {"report_id": reported["report"]["id"]}
    numbers = {"assigned": 1, "already_assigned": 1, "not_open": 1, "not_in_list": 1}
    plan = client.post(url, json={**body, "dry_run": True})
    assert plan.status_code == 200, plan.text
    assert plan.json() == {**numbers, "report_id": body["report_id"], "assigned_on": day(0), "dry_run": True}
    assert record(db_session, reported["b"]) is None                        # the dry run wrote nothing
    # An agent's key gets the same answer from the same route.
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]
    assert client.post(f"{AGENT}/assign-from-report", json={**body, "dry_run": True},
                       headers={"X-API-Key": key}).json() == plan.json()

    done = client.post(url, json=body)
    assert done.status_code == 200, done.text
    assert done.json() == {**plan.json(), "dry_run": False}
    assert record(db_session, reported["b"]).notified_on.isoformat() == day(0)       # the day it was issued
    assert record(db_session, reported["a"]).notified_on.isoformat() == day(3)       # left alone
    assert record(db_session, reported["c"]).notified_on is None                     # reported fixed: not open
    assert record(db_session, reported["later"]) is None                             # never in the report
    entries = db_session.query(RemediationEvent).filter_by(finding_host_id=reported["b"]).all()
    assert {(e.kind, e.field) for e in entries} == {("change", "notified_on"), ("note", None)}
    note = next(e for e in entries if e.kind == "note")
    assert reported["report"]["title"] in note.body and "issued on" in note.body

    again = client.post(url, json=body).json()
    assert (again["assigned"], again["already_assigned"]) == (0, 2)
    assert db_session.query(RemediationEvent).filter_by(finding_host_id=reported["b"]).count() == 2


def test_assigning_from_a_report_takes_a_date_and_refuses_what_is_not_an_issued_report_here(
        client, db_session, test_project, reported):
    url = f"{base(test_project)}/assign-from-report"
    assert client.post(url, json={"report_id": reported["report"]["id"], "assigned_on": day(-1)}).status_code == 422
    assert record(db_session, reported["b"]) is None
    draft = client.post(f"/api/v1/projects/{test_project.id}/client-reports", json={"kind": "addendum"}).json()
    assert client.post(url, json={"report_id": draft["id"]}).status_code == 409
    assert client.post(url, json={"report_id": 999999}).status_code == 404
    other = _project(db_session, "elsewhere")
    foreign = Report(project_id=other.id, kind="full", status="issued", title="Theirs", template="pentest",
                     number=1, issued_at=datetime.now(timezone.utc), snapshot={"reported": {}})
    db_session.add(foreign)
    db_session.commit()
    assert client.post(url, json={"report_id": foreign.id}).status_code == 404
    done = client.post(url, json={"report_id": reported["report"]["id"], "assigned_on": day(2)})
    assert done.status_code == 200 and done.json()["assigned_on"] == day(2)
    assert record(db_session, reported["b"]).notified_on.isoformat() == day(2)


def test_an_addendum_dates_only_what_it_added(client, db_session, test_project, on, template_dir):
    one, (a,) = _rows(db_session, test_project, 1, title="SQL injection")
    _issue(client, test_project)
    _, (new,) = _rows(db_session, test_project, 1, title="Anonymous LDAP")
    addendum = _issue(client, test_project, kind="addendum")
    result = client.post(f"{base(test_project)}/assign-from-report", json={"report_id": addendum["id"]}).json()
    assert (result["assigned"], result["not_in_list"]) == (1, 0)
    assert record(db_session, new).notified_on is not None and record(db_session, a) is None


# --- 6. what a policy change would do -------------------------------------------------

def test_the_policy_preview_is_what_the_change_then_does_and_writes_nothing(client, db_session, test_project, on):
    _, ids = _rows(db_session, test_project, 6)
    apply(client, test_project, [
        {"finding_host_id": ids[0], "notified_on": day(40)},                                  # overdue
        {"finding_host_id": ids[1], "notified_on": day(20)},                                  # due in 10 days
        {"finding_host_id": ids[2], "notified_on": day(1)},                                   # on track
        {"finding_host_id": ids[3], "notified_on": day(20), "due_override_on": day(-40), "notes": WHY},
        {"finding_host_id": ids[4], "notified_on": day(1), "due_override_on": day(4), "notes": WHY},
    ])
    now = client.get(f"{OVERVIEW}/projects").json()["totals"]
    shorter = {"days": {"high": 15}}
    preview = client.post(f"{POLICY_URL}/preview", json=shorter)
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    assert preview["current"] == now
    # The row due in 10 days becomes 5 days late; the day-old one is due in 14.
    assert (preview["becomes_overdue"], preview["no_longer_overdue"], preview["becomes_due_soon"]) == (1, 0, 0)
    # Nothing was written: the policy, the audit log and every state are as they were.
    assert client.get(POLICY_URL).json()["days"]["high"] == 30
    assert db_session.query(AuditLog).filter(AuditLog.action == "remediation_policy_updated").count() == 0
    assert client.get(f"{OVERVIEW}/projects").json()["totals"] == now

    longer = client.post(f"{POLICY_URL}/preview", json={"days": {"high": 60}, "due_soon_days": 30}).json()
    # The policy's overdue row is no longer late; the hand-set late one stays late.
    assert longer["no_longer_overdue"] == 1 and longer["proposed"]["overdue"] == 1

    # Applying the change gives exactly the previewed states.
    assert client.put(POLICY_URL, json=shorter).status_code == 200
    after = client.get(f"{OVERVIEW}/projects").json()["totals"]
    assert after == preview["proposed"]
    rows = by_id(listing(client, base(test_project)))
    assert rows[ids[3]]["due_on"] == day(-40) and rows[ids[4]]["state"] == "overdue"     # hand-set: unmoved
    assert rows[ids[1]]["state"] == "overdue"


def test_the_policy_preview_is_the_global_admins_and_answers_with_tracking_off(client, db_session, test_project):
    assert client.post(f"{POLICY_URL}/preview", json={"days": {"high": 15}}).status_code == 200
    assert client.post(f"{POLICY_URL}/preview", json={"days": {"high": 0}}).status_code == 422
    member = _user(db_session, "preview-project-admin")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="admin"))
    db_session.commit()
    with as_user(member):
        assert client.post(f"{POLICY_URL}/preview", json={"days": {"high": 15}}).status_code == 403


# --- 7. alerts that say what -----------------------------------------------------------

def test_an_alert_names_the_worst_rows_and_stays_one_per_recipient(client, db_session, test_project, on, test_user):
    _, ids = _rows(db_session, test_project, 5, title="Weak TLS ciphers")
    apply(client, test_project, [{"finding_host_id": fh, "notified_on": day(40 + n)} for n, fh in enumerate(ids)])
    assert remediation_alerts.sweep(db_session) == 1                 # one recipient, one project, one kind
    db_session.commit()
    (alert,) = _alerts(db_session, "overdue")
    assert alert.title == "test-project: 5 findings on hosts are past the remediation deadline"
    address = lambda fh: db_session.get(models.Host, db_session.get(FindingHost, fh).host_id).ip_address  # noqa: E731
    # The most overdue first, three of them, then how many more.
    assert alert.body.startswith("; ".join(f"Weak TLS ciphers on {address(fh)}" for fh in reversed(ids[2:]))
                                 + "; and 2 more.")
    assert (alert.source_type, alert.source_id, alert.user_id) == ("remediation_overdue", test_project.id,
                                                                    test_user.id)


def test_a_deferral_alerts_once_when_its_review_date_comes(client, db_session, test_project, on):
    _, (fh, old) = _rows(db_session, test_project, 2, title="Default credentials")
    _legacy_deferral(db_session, test_project, old)                  # no date to reach: listed, never alerted
    apply(client, test_project, [{"finding_host_id": fh, "status": "deferred", "deferred_review_on": day(-10),
                                  "notes": WHY}])
    assert remediation_alerts.sweep(db_session) == 0                 # not yet
    later = TODAY + timedelta(days=10)
    assert remediation_alerts.sweep(db_session, today=later) == 1
    db_session.commit()
    (alert,) = _alerts(db_session, "deferral_review")
    assert alert.source_type == "remediation_deferral_review" and alert.source_id == test_project.id
    assert alert.title == "test-project: 1 deferred finding on a host has reached its review date"
    assert alert.body.startswith("Default credentials on 10.")
    assert remediation_alerts.sweep(db_session, today=later) == 0    # once per review date
    assert record(db_session, fh).deferral_alerted_for == later
    # A new review date is a new day to be told about.
    apply(client, test_project, [{"finding_host_id": fh, "deferred_review_on": day(-30), "notes": WHY}])
    assert record(db_session, fh).deferral_alerted_for is None
    assert remediation_alerts.sweep(db_session, today=later) == 0
    assert remediation_alerts.sweep(db_session, today=TODAY + timedelta(days=30)) == 1


# --- 8. one contact, several projects ----------------------------------------------------

ROGER = "roger@testdomain.com"


@pytest.fixture
def across(client, db_session, test_project, on):
    mine, theirs = _project(db_session, "fu-mine"), _project(db_session, "fu-theirs")
    done = _project(db_session, "fu-archived", status="archived")
    _, a = _rows(db_session, mine, 3, title="Weak TLS ciphers")
    _, b = _rows(db_session, theirs, 1, title="Open relay")
    _, c = _rows(db_session, done, 1, title="Default credentials")
    for project, rows in ((mine, [{"finding_host_id": a[0], "notified_on": day(40)},        # 10 days overdue
                                  {"finding_host_id": a[1], "notified_on": day(10)},        # due in 20 days
                                  {"finding_host_id": a[2], "notified_on": day(0)}]),       # due in 30 days
                          (theirs, [{"finding_host_id": b[0], "notified_on": day(60)}]),
                          (done, [{"finding_host_id": c[0], "notified_on": day(45)}])):     # 15 days overdue
        written = client.post(f"{OVERVIEW}/projects/{project.id}/remediation/apply", json={"rows": [
            {**row, "contact_email": ROGER, "contact_name": "Roger Smith"} for row in rows]})
        assert written.status_code == 200, written.text
    person = _user(db_session, "fu-admin")
    db_session.add_all([ProjectMembership(project_id=mine.id, user_id=person.id, role="admin"),
                        ProjectMembership(project_id=done.id, user_id=person.id, role="admin"),
                        ProjectMembership(project_id=theirs.id, user_id=person.id, role="analyst")])
    db_session.commit()
    return {"mine": mine, "theirs": theirs, "done": done, "a": a, "b": b, "c": c, "person": person}


def test_the_reminder_is_grouped_by_project_and_can_look_ahead(client, across):
    with as_user(across["person"]):
        plain = listing(client, f"{OVERVIEW}/follow-up", contact_email=ROGER)
        assert (plain["total"], plain["overdue"], plain["due_soon"], plain["upcoming"]) == (2, 2, 0, 0)
        message = listing(client, f"{OVERVIEW}/follow-up", contact_email=ROGER, upcoming_days=25)
    assert (message["total"], message["upcoming"], message["not_listed"]) == (3, 1, 0)
    assert [row["finding_host_id"] for row in message["items"]] == [across["c"][0], across["a"][0], across["a"][1]]
    lines = message["text"].split("\n")
    assert lines[0] == "Hello Roger Smith,"
    assert lines[2] == (f"This is a reminder about 3 findings assigned to you for remediation across 2 projects, "
                        f"as of {day(0)}.")
    # The most urgent project first, each under its name, then its rows by kind.
    assert lines.index("fu-archived") < lines.index("fu-mine") and "fu-theirs" not in message["text"]
    assert "Coming up in the next 25 days (1):" in lines
    overdue = next(line for line in lines if "15 days overdue" in line)
    assert overdue.startswith("- Default credentials (high) — 10.") and f"— due {day(15)} (15 days overdue)" in overdue
    assert any(line.endswith(f"due {day(-20)} (due in 20 days)") for line in lines)
    # A reader outside BlueStick: no record numbers, no product name.
    assert "#" not in message["text"] and "BlueStick" not in message["text"] and "finding_host" not in message["text"]

    # One project, the same structure.
    with as_user(across["person"]):
        one = listing(client, f"{OVERVIEW}/projects/{across['mine'].id}/remediation/follow-up",
                      contact_email=ROGER, upcoming_days=30)
    assert (one["total"], one["upcoming"]) == (3, 2)
    assert "across" not in one["text"] and "fu-mine" in one["text"].split("\n")
    assert client.get(f"{OVERVIEW}/follow-up", params={"contact_email": ROGER, "upcoming_days": 366}).status_code == 422


def test_a_reminder_is_recorded_in_every_project_the_caller_administers(client, db_session, across):
    body = {"contact_email": "Roger@TestDomain.com", "upcoming_days": 25, "note": "Mailed the list."}
    with as_user(across["person"]):
        refused = client.post(f"{OVERVIEW}/follow-up", json={**body, "followed_up_on": day(-1)})
        assert refused.status_code == 422 and db_session.query(RemediationEvent).filter_by(kind="follow_up").count() == 0
        done = client.post(f"{OVERVIEW}/follow-up", json=body)
        assert done.status_code == 200, done.text
        assert done.json() == {"projects": 2, "recorded": 3, "already_today": 0, "followed_up_on": day(0)}
        again = client.post(f"{OVERVIEW}/follow-up", json=body).json()
        assert (again["projects"], again["recorded"], again["already_today"]) == (2, 0, 3)
    entries = db_session.query(RemediationEvent).filter_by(kind="follow_up").all()
    assert len(entries) == 3 and {e.body for e in entries} == {"Mailed the list."}
    assert {e.project_id for e in entries} == {across["mine"].id, across["done"].id}
    assert {e.author_id for e in entries} == {across["person"].id}
    for fh in (across["a"][0], across["a"][1], across["c"][0]):
        assert record(db_session, fh).last_follow_up_on == TODAY
    assert record(db_session, across["a"][2]).last_follow_up_on is None       # outside the 25 days
    assert record(db_session, across["b"][0]).last_follow_up_on is None       # not theirs to record

    # The project route records what its reminder listed, upcoming rows included.
    project = f"/api/v1/projects/{across['mine'].id}/remediation/follow-up"
    more = client.post(project, json={"contact_email": ROGER, "upcoming_days": 30})
    assert more.status_code == 200 and (more.json()["recorded"], more.json()["already_recorded"]) == (1, 2)


# --- 9. a severity change is on the record -------------------------------------------------

def _statements(run, containing):
    seen: list = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", _count)
    try:
        run()
    finally:
        event.remove(Engine, "after_cursor_execute", _count)
    return [s for s in seen if containing in s]


def _severity(client, project, finding, severity):
    response = client.patch(f"/api/v1/projects/{project.id}/findings/{finding.id}", json={"severity": severity})
    assert response.status_code == 200, response.text


def test_a_severity_change_is_written_where_it_moves_a_deadline(client, db_session, test_project, on, test_user):
    finding, (policy, by_hand, unassigned, fixed) = _rows(db_session, test_project, 4, title="Weak TLS ciphers")
    apply(client, test_project, [
        {"finding_host_id": policy, "notified_on": day(10)},
        {"finding_host_id": by_hand, "notified_on": day(10), "due_override_on": day(-5), "notes": WHY},
        {"finding_host_id": fixed, "notified_on": day(10), "status": "closed"},
        {"finding_host_id": unassigned, "contact_name": "Roger Smith"},
    ])
    _severity(client, test_project, finding, "medium")
    entries = db_session.query(RemediationEvent).filter_by(field="severity").all()
    assert [e.finding_host_id for e in entries] == [policy]
    (entry,) = entries
    assert (entry.kind, entry.old_value, entry.new_value) == ("change", "high", "medium")
    assert entry.body == f"Remediation deadline before: {day(-20)}; after: {day(-80)}."
    assert (entry.author_id, entry.finding_id, entry.finding_title) == (test_user.id, finding.id, "Weak TLS ciphers")
    assert entry.host_id == db_session.get(FindingHost, policy).host_id and entry.project_id == test_project.id
    assert by_id(listing(client, base(test_project)))[policy]["due_on"] == day(-80)
    timeline = client.get(f"{base(test_project)}/hosts/{entry.host_id}/events").json()["items"]
    assert timeline[0]["field"] == "severity" and (timeline[0]["from"], timeline[0]["to"]) == ("high", "medium")

    # The same severity again, and a severity with no timeline: said where a deadline was.
    _severity(client, test_project, finding, "medium")
    assert db_session.query(RemediationEvent).filter_by(field="severity").count() == 1
    _severity(client, test_project, finding, "info")
    last = db_session.query(RemediationEvent).filter_by(field="severity").order_by(RemediationEvent.id.desc()).first()
    assert last.body == f"Remediation deadline before: {day(-80)}; after: none."


def test_a_severity_change_is_one_statement_however_many_hosts(client, db_session, test_project, on):
    few, few_ids = _rows(db_session, test_project, 2)
    many, many_ids = _rows(db_session, test_project, 40)
    apply(client, test_project, [{"finding_host_id": i, "notified_on": day(5)} for i in few_ids + many_ids])
    counts = []
    for finding in (few, many):
        written = _statements(lambda: _severity(client, test_project, finding, "low"), "remediation_events")
        counts.append(len(written))
    assert counts == [1, 1], counts
    assert db_session.query(RemediationEvent).filter_by(field="severity").count() == 42


def test_a_severity_change_writes_nothing_with_tracking_off(client, db_session, test_project):
    finding, (fh,) = _rows(db_session, test_project, 1)
    db_session.add(FindingHostRemediation(finding_host_id=fh, project_id=test_project.id, status="open",
                                          notified_on=TODAY))
    db_session.commit()
    touched = _statements(lambda: _severity(client, test_project, finding, "low"), "remediation_events")
    assert touched == [] and db_session.query(RemediationEvent).count() == 0


# --- off means off ----------------------------------------------------------------------

def test_off_means_none_of_it_answers(client, db_session, test_project):
    for method, url, sent in (
        ("get", base(test_project), {"params": {"flag": "deferral_review_due"}}),
        ("get", base(test_project), {"params": {"q": "tls"}}),
        ("get", f"{base(test_project)}/follow-up", {"params": {"contact_email": ROGER, "upcoming_days": 30}}),
        ("post", f"{base(test_project)}/assign-from-report", {"json": {"report_id": 1}}),
        ("post", f"{OVERVIEW}/projects/{test_project.id}/remediation/assign-from-report", {"json": {"report_id": 1}}),
        ("post", f"{OVERVIEW}/follow-up", {"json": {"contact_email": ROGER}}),
        ("get", f"{OVERVIEW}/follow-up", {"params": {"contact_email": ROGER}}),
    ):
        response = getattr(client, method)(url, **sent)
        assert response.status_code == 404, (url, response.text)
        assert "not enabled on this installation" in response.json()["detail"]
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]
    assert client.post(f"{AGENT}/assign-from-report", json={"report_id": 1},
                       headers={"X-API-Key": key}).status_code == 404
    assert remediation_alerts.sweep(db_session) == 0


def test_only_a_project_admin_starts_the_clock_from_a_report(client, db_session, test_project, on):
    analyst = _user(db_session, "clock-analyst")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=analyst.id, role="analyst"))
    db_session.commit()
    with as_user(analyst):
        assert client.post(f"{base(test_project)}/assign-from-report", json={"report_id": 1}).status_code == 403
        assert client.post(f"{OVERVIEW}/follow-up", json={"contact_email": ROGER}).json() == {
            "projects": 0, "recorded": 0, "already_today": 0, "followed_up_on": day(0)}


def test_the_tools_take_their_arguments_from_the_routes():
    from app.api.v1.endpoints.mcp_tools import TOOLS

    listed = TOOLS["remediation_list"]["input_schema"]["properties"]
    assert "deferral_review_due" in str(listed["flag"]) and "deadline_overridden" in str(listed["flag"])
    assert "q" in listed
    assert "upcoming_days" in TOOLS["remediation_follow_up"]["input_schema"]["properties"]
    assign = TOOLS["remediation_assign_from_report"]
    assert assign["path"] == "/api/v1/agent/remediation/assign-from-report"
    assert {"report_id", "assigned_on", "dry_run"} <= set(assign["input_schema"]["properties"])
    assert "dry_run" in assign["description"]
