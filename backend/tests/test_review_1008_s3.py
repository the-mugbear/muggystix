"""Review 2026-10-08, findings / promotion / remediation.

* Removing a finding from a host (or deleting the finding) deletes that
  row's remediation record: on an installation that tracks remediation that
  is the project admins' to do, and it is written to the host's timeline.
* Every promotion of a scanner observation links the test results that
  showed it, the bulk promotion included.
* A promotion made on a host un-dismisses that host's endpoint.
* Follow-ups, trend months, the installation's time zone, the sweeps.
"""
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from app.db import models
from app.db.models_auth import AuditLog, UserRole
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
from app.db.models_project import Notification, ProjectMembership
from app.db.models_proposals import EvidenceRecord
from app.db.models_remediation import (
    FindingHostRemediation, RemediationDaily, RemediationEvent, RemediationPolicy,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services import remediation_alerts, remediation_policy, remediation_service
from app.services.finding_service import FindingService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)

UTC_TODAY = datetime.now(timezone.utc).date()
POLICY_URL = "/api/v1/remediation-policy"


def day(n: int) -> str:
    return (UTC_TODAY - timedelta(days=n)).isoformat()


def _policy(db, **overrides):
    values = dict(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90, days_low=120,
                  days_info=None, due_soon_days=7)
    values.update(overrides)
    db.add(RemediationPolicy(**values))
    db.commit()


@pytest.fixture
def tracking_on(db_session):
    _policy(db_session)


def remediation(project):
    return f"/api/v1/projects/{project.id}/remediation"


def findings(project):
    return f"/api/v1/projects/{project.id}/findings"


def _host(db, project, ip):
    row = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(row)
    db.flush()
    return row


def _finding(db, project, hosts, *, title="SMB signing not required", severity="high", author=None):
    finding = Finding(project_id=project.id, title=title, severity=severity, status="confirmed",
                      source="manual", created_by_id=author)
    db.add(finding)
    db.flush()
    links = [FindingHost(finding_id=finding.id, host_id=host.id, host_status="open") for host in hosts]
    db.add_all(links)
    db.commit()
    return finding, [link.id for link in links]


def _apply(client, project, rows, **flags):
    response = client.post(f"{remediation(project)}/apply", json={"rows": rows, "overwrite": True, **flags})
    assert response.status_code == 200, response.text
    return response.json()


def _become(db, project, user, role):
    """The signed-in user is a project member with ``role`` and no global one."""
    user.role = UserRole.MEMBER
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()


@pytest.fixture
def tracked(client, db_session, test_project, test_user):
    """One finding (the user's own) on hosts A and B; A's row has a contact
    and an assigned date, B's has nothing recorded."""
    a, b = _host(db_session, test_project, "10.31.0.1"), _host(db_session, test_project, "10.31.0.2")
    finding, (on_a, on_b) = _finding(db_session, test_project, [a, b], author=test_user.id)
    return {"finding": finding.id, "a": a.id, "b": b.id, "on_a": on_a, "on_b": on_b}


def _assign(client, project, finding_host_id):
    _apply(client, project, [{"finding_host_id": finding_host_id, "contact_email": "roger@example.com",
                              "notified_on": day(3)}])


# --- A: an endpoint's remediation record goes with it ------------------------

@pytest.mark.parametrize("route", ["endpoint", "host"])
def test_an_analyst_cannot_remove_an_endpoint_that_carries_a_remediation_record(
    client, db_session, test_project, test_user, tracking_on, tracked, route,
):
    _assign(client, test_project, tracked["on_a"])
    _become(db_session, test_project, test_user, "analyst")
    url = (f"{findings(test_project)}/{tracked['finding']}/endpoints/{tracked['on_a']}" if route == "endpoint"
           else f"{findings(test_project)}/{tracked['finding']}/hosts/{tracked['a']}")
    refused = client.delete(url)
    assert refused.status_code == 409, refused.text
    assert "project admin" in refused.json()["detail"]
    db_session.expire_all()
    assert db_session.get(FindingHost, tracked["on_a"]) is not None
    record = db_session.query(FindingHostRemediation).filter_by(finding_host_id=tracked["on_a"]).one()
    assert record.contact_email == "roger@example.com"


def test_an_analyst_still_removes_an_endpoint_nobody_recorded_anything_for(
    client, db_session, test_project, test_user, tracking_on, tracked,
):
    _assign(client, test_project, tracked["on_a"])
    _become(db_session, test_project, test_user, "analyst")
    removed = client.delete(f"{findings(test_project)}/{tracked['finding']}/endpoints/{tracked['on_b']}")
    assert removed.status_code == 200, removed.text
    assert "remediation" not in removed.text.lower()
    db_session.expire_all()
    assert db_session.get(FindingHost, tracked["on_b"]) is None
    assert db_session.get(FindingHost, tracked["on_a"]) is not None


@pytest.mark.parametrize("route", ["endpoint", "host"])
def test_a_project_admin_removes_it_and_the_hosts_timeline_says_so(
    client, db_session, test_project, test_user, tracking_on, tracked, route,
):
    _assign(client, test_project, tracked["on_a"])
    _become(db_session, test_project, test_user, "admin")
    url = (f"{findings(test_project)}/{tracked['finding']}/endpoints/{tracked['on_a']}" if route == "endpoint"
           else f"{findings(test_project)}/{tracked['finding']}/hosts/{tracked['a']}")
    removed = client.delete(url)
    assert removed.status_code == 200, removed.text
    db_session.expire_all()
    assert db_session.get(FindingHost, tracked["on_a"]) is None
    assert db_session.query(FindingHostRemediation).filter_by(finding_host_id=tracked["on_a"]).count() == 0
    entries = client.get(f"{remediation(test_project)}/hosts/{tracked['a']}/events").json()["items"]
    gone = [e for e in entries if e["kind"] == "change" and e["field"] == "finding"]
    assert len(gone) == 1, entries
    assert gone[0]["to"] == "removed from this host" and "roger@example.com" in gone[0]["from"]
    assert gone[0]["finding_title"] == "SMB signing not required" and gone[0]["author_id"] == test_user.id


def test_with_tracking_off_removal_is_as_it_was_and_says_nothing_about_remediation(
    client, db_session, test_project, test_user, tracked,
):
    _policy(db_session)
    _assign(client, test_project, tracked["on_a"])
    db_session.get(RemediationPolicy, 1).enabled = False
    db_session.commit()
    events_before = db_session.query(RemediationEvent).count()
    _become(db_session, test_project, test_user, "analyst")
    removed = client.delete(f"{findings(test_project)}/{tracked['finding']}/endpoints/{tracked['on_a']}")
    assert removed.status_code == 200, removed.text
    assert "remediation" not in removed.text.lower()
    db_session.expire_all()
    assert db_session.get(FindingHost, tracked["on_a"]) is None
    assert db_session.query(RemediationEvent).count() == events_before
    deleted = client.delete(f"{findings(test_project)}/{tracked['finding']}")
    assert deleted.status_code == 204, deleted.text


def test_deleting_a_finding_with_remediation_records_is_the_project_admins(
    client, db_session, test_project, test_user, tracking_on, tracked,
):
    _assign(client, test_project, tracked["on_a"])
    # Its author, an analyst: may delete their own finding, but not this one.
    _become(db_session, test_project, test_user, "analyst")
    refused = client.delete(f"{findings(test_project)}/{tracked['finding']}")
    assert refused.status_code == 409, refused.text
    assert "project admin" in refused.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Finding, tracked["finding"]) is not None
    assert db_session.query(FindingHostRemediation).count() == 1

    membership = db_session.query(ProjectMembership).filter_by(
        project_id=test_project.id, user_id=test_user.id).one()
    membership.role = "admin"
    db_session.commit()
    deleted = client.delete(f"{findings(test_project)}/{tracked['finding']}")
    assert deleted.status_code == 204, deleted.text
    db_session.expire_all()
    assert db_session.get(Finding, tracked["finding"]) is None
    assert db_session.query(FindingHostRemediation).count() == 0
    entry = db_session.query(RemediationEvent).filter_by(kind="change", field="finding").one()
    assert (entry.host_id, entry.new_value, entry.finding_title) == (
        tracked["a"], "finding deleted", "SMB signing not required")
    assert db_session.query(AuditLog).filter_by(action="finding_deleted").count() == 1


# --- B, C, D: promotion ------------------------------------------------------

def host_tests(project):
    return f"/api/v1/projects/{project.id}/host-tests"


def _observation(db, project, host, title="SMB Signing not required"):
    scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         severity=VulnerabilitySeverity.MEDIUM, title=title)
    db.add(vuln)
    db.commit()
    return vuln


def _confirmed_by_a_test(client, project, host, vuln):
    """A test linked to the observation, with a result that showed the issue."""
    made = client.post(host_tests(project), json={"tests": [dict(
        request_key=str(uuid.uuid4()), host_id=host.id, tool="nxc", description="Check SMB signing",
        rationale="Confirm the scanner", vulnerability_id=vuln.id)]})
    assert made.status_code == 201, made.text
    test = made.json()["items"][0]
    result = client.post(f"{host_tests(project)}/{test['id']}/result", json={
        "expected_revision": test["revision"], "request_key": str(uuid.uuid4()),
        "outcome": "finding", "summary": "signing:False"})
    assert result.status_code == 201, result.text
    return test["id"], result.json()["evidence"]["id"]


def test_the_bulk_promotion_takes_the_test_results_that_showed_the_issue(client, db_session, test_project):
    seen, other = _host(db_session, test_project, "10.32.0.1"), _host(db_session, test_project, "10.32.0.2")
    db_session.commit()
    vuln = _observation(db_session, test_project, seen)
    _observation(db_session, test_project, other)
    test_id, evidence_id = _confirmed_by_a_test(client, test_project, seen, vuln)
    before = client.get(f"{host_tests(test_project)}/{test_id}").json()
    assert before["unpromoted_findings"] == 1

    promoted = client.post(f"/api/v1/projects/{test_project.id}/scanner-observations/promote",
                           json={"items": [{"issue_key": vuln.issue_key}]})
    assert promoted.status_code == 200, promoted.text
    finding_id = promoted.json()["results"][0]["finding_id"]
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == finding_id
    after = client.get(f"{host_tests(test_project)}/{test_id}").json()
    assert after["unpromoted_findings"] == 0 and after["finding_ids"] == [finding_id]


def test_a_bulk_promotion_of_other_hosts_leaves_this_hosts_result_unlinked(client, db_session, test_project):
    seen, other = _host(db_session, test_project, "10.32.1.1"), _host(db_session, test_project, "10.32.1.2")
    db_session.commit()
    vuln = _observation(db_session, test_project, seen)
    _observation(db_session, test_project, other)
    _, evidence_id = _confirmed_by_a_test(client, test_project, seen, vuln)
    promoted = client.post(f"/api/v1/projects/{test_project.id}/scanner-observations/promote",
                           json={"items": [{"issue_key": vuln.issue_key, "host_ids": [other.id]}]})
    assert promoted.status_code == 200, promoted.text
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id is None


def test_a_promotion_says_whether_it_made_the_finding_and_joins_as_it_was_told(
    db_session, test_project, test_user,
):
    host = _host(db_session, test_project, "10.32.2.1")
    vuln = _observation(db_session, test_project, host)
    svc = FindingService(db_session)
    made, created = svc.promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id, status="open", host_ids=[host.id])
    assert created and made.status == "open"
    kept, created = svc.promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id, status="confirmed", on_join="keep")
    assert (kept.id, created, kept.status) == (made.id, False, "open")
    confirmed, _ = svc.promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id, status="confirmed", on_join="confirm")
    assert confirmed.status == "confirmed"
    svc.set_status(finding=confirmed, status="accepted_risk", actor_id=test_user.id)
    concluded, _ = svc.promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id, status="confirmed", on_join="confirm")
    assert concluded.status == "accepted_risk"
    chosen, created = svc.promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id, status="confirmed")
    assert (chosen.status, created) == ("confirmed", False)
    with pytest.raises(ValueError):
        svc.promote_vulnerability(vuln=vuln, project_id=test_project.id, actor_id=test_user.id, on_join="reopen")


def _promote(client, project, vuln, **body):
    response = client.post(f"/api/v1/projects/{project.id}/vulnerabilities/{vuln.id}/promote",
                           json={"vuln_id": vuln.id, **body})
    assert response.status_code == 201, response.text
    return response.json()


def _endpoint_states(db, finding_id):
    db.expire_all()
    return dict(db.query(FindingHost.host_id, FindingHost.host_status).filter_by(finding_id=finding_id))


def test_promoting_on_a_host_that_was_dismissed_there_reopens_that_hosts_endpoint(
    client, db_session, test_project,
):
    here, there = _host(db_session, test_project, "10.33.0.1"), _host(db_session, test_project, "10.33.0.2")
    db_session.commit()
    vuln = _observation(db_session, test_project, here)
    _observation(db_session, test_project, there)
    dismissed = _promote(client, test_project, vuln, status="false_positive")
    assert dismissed["status"] == "false_positive"
    assert _endpoint_states(db_session, dismissed["id"]) == {here.id: "false_positive"}

    promoted = _promote(client, test_project, vuln, scope="host")
    assert promoted["id"] == dismissed["id"] and promoted["status"] == "confirmed"
    # Confirmed with its only endpoint still a false positive would drop the
    # finding's only system from the report.
    assert _endpoint_states(db_session, promoted["id"]) == {here.id: "open"}
    lines = [h.summary for h in db_session.query(FindingStatusHistory).filter_by(finding_id=promoted["id"])]
    assert any("false_positive → open" in (line or "") for line in lines), lines


def test_an_issue_wide_promotion_leaves_another_hosts_dismissal_alone(client, db_session, test_project):
    here, there = _host(db_session, test_project, "10.33.1.1"), _host(db_session, test_project, "10.33.1.2")
    db_session.commit()
    dismissed_here = _observation(db_session, test_project, here)
    elsewhere = _observation(db_session, test_project, there)
    finding_id = _promote(client, test_project, dismissed_here, status="false_positive")["id"]
    promoted = _promote(client, test_project, elsewhere, scope="issue")
    assert promoted["id"] == finding_id and promoted["status"] == "confirmed"
    assert _endpoint_states(db_session, finding_id) == {here.id: "false_positive", there.id: "open"}


# --- E: follow-up ------------------------------------------------------------

@pytest.fixture
def six_at_risk(client, db_session, test_project, tracking_on):
    """One contact with six overdue rows.  The LATER the address, the longer
    overdue — so "most overdue first" and address order disagree."""
    hosts = [_host(db_session, test_project, f"10.34.0.{n}") for n in range(1, 7)]
    _, links = _finding(db_session, test_project, hosts)
    _apply(client, test_project, [
        {"finding_host_id": link, "contact_email": "roger@example.com", "notified_on": day(40 + 10 * n)}
        for n, link in enumerate(links)])
    return links


def test_the_follow_up_preview_says_how_many_it_does_not_list(client, test_project, six_at_risk, monkeypatch):
    monkeypatch.setattr(remediation_service, "FOLLOW_UP_MAX_ROWS", 4)
    preview = client.get(f"{remediation(test_project)}/follow-up",
                         params={"contact_email": "roger@example.com"}).json()
    assert [row["finding_host_id"] for row in preview["items"]] == six_at_risk[:1:-1]   # the four most overdue
    assert (preview["total"], preview["not_listed"], preview["has_more"], preview["overdue"]) == (6, 2, True, 6)
    assert "2 more findings on hosts are overdue or approaching the deadline and not listed here" in preview["text"]


def test_recording_a_follow_up_covers_every_at_risk_row_not_one_page(
    client, db_session, test_project, six_at_risk, monkeypatch,
):
    monkeypatch.setattr(remediation_service, "FOLLOW_UP_MAX_ROWS", 4)
    # A row the preview lists (most overdue) that the first four by address
    # do not include: it is the contact's, so naming it is not a mistake.
    named = client.post(f"{remediation(test_project)}/follow-up", json={
        "contact_email": "roger@example.com", "finding_host_ids": [six_at_risk[5]]})
    assert named.status_code == 200, named.text
    assert named.json()["finding_host_ids"] == [six_at_risk[5]]
    everything = client.post(f"{remediation(test_project)}/follow-up", json={"contact_email": "roger@example.com"})
    assert everything.status_code == 200, everything.text
    assert (everything.json()["recorded"], everything.json()["already_recorded"]) == (5, 1)
    assert db_session.query(RemediationEvent).filter_by(kind="follow_up").count() == 6
    stranger = client.post(f"{remediation(test_project)}/follow-up", json={
        "contact_email": "roger@example.com", "finding_host_ids": [999999]})
    assert stranger.status_code == 422


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_finding_on_more_hosts_than_one_call_changes_is_refused_with_the_way_to_do_it(
    client, db_session, test_project, tracking_on, monkeypatch, dry_run,
):
    monkeypatch.setattr(remediation_service, "APPLY_MAX_TARGETS", 3)
    hosts = [_host(db_session, test_project, f"10.34.1.{n}") for n in range(1, 5)]
    finding, links = _finding(db_session, test_project, hosts)
    refused = client.post(f"{remediation(test_project)}/apply", json={
        "rows": [{"finding_id": finding.id, "status": "closed"}], "dry_run": dry_run})
    assert refused.status_code == 422, refused.text
    problem = refused.json()["detail"]["problems"][0]
    assert "finding_id with host_id" in problem and "at most 3 a call" in problem
    assert db_session.query(FindingHostRemediation).count() == 0
    # Named by host, the same change goes through.
    named = client.post(f"{remediation(test_project)}/apply", json={
        "rows": [{"finding_id": finding.id, "host_id": host.id, "status": "closed"} for host in hosts[:3]],
        "dry_run": dry_run})
    assert named.status_code == 200, named.text


# --- F: the trend's months ---------------------------------------------------

@pytest.mark.parametrize("today,first", [
    (date(2026, 10, 8), date(2025, 11, 1)),
    (date(2026, 11, 30), date(2025, 12, 1)),
    (date(2026, 1, 1), date(2025, 2, 1)),
    (date(2026, 12, 31), date(2026, 1, 1)),
    (date(2024, 3, 31), date(2023, 4, 1)),
])
def test_the_trend_covers_exactly_twelve_months(today, first):
    assert remediation_service.first_of_twelve_months(today) == first


def test_closed_rows_are_counted_per_month_for_twelve_months(db_session, test_project):
    today = date(2026, 10, 8)
    hosts = [_host(db_session, test_project, f"10.35.0.{n}") for n in range(1, 8)]
    _, links = _finding(db_session, test_project, hosts)
    closed = [
        (date(2025, 10, 31), date(2025, 11, 30)),   # thirteen months back: outside
        (date(2025, 11, 1), date(2025, 11, 30)),    # the first of the twelve, on time
        (date(2025, 11, 20), date(2025, 11, 10)),   # late
        (date(2025, 11, 21), None),                 # no deadline
        (date(2026, 10, 8), date(2026, 10, 8)),     # today, on its deadline: on time
        (date(2026, 10, 2), date(2026, 10, 1)),     # late
        (date(2026, 10, 9), None),                  # tomorrow: not yet
    ]
    for link, (closed_on, due_on) in zip(links, closed):
        db_session.add(FindingHostRemediation(
            finding_host_id=link, project_id=test_project.id, status="closed",
            closed_on=closed_on, closed_due_on=due_on))
    db_session.commit()
    trend = remediation_service.trend(db_session, [test_project.id], today=today)
    assert trend["closed_by_month"] == [
        {"month": "2025-11", "on_time": 1, "late": 1, "no_deadline": 1},
        {"month": "2026-10", "on_time": 1, "late": 1, "no_deadline": 0},
    ]


# --- G: the installation's time zone -----------------------------------------

def test_today_is_the_installations_day():
    evening_in_california = datetime(2026, 10, 9, 1, 30, tzinfo=timezone.utc)
    policy = remediation_policy.Policy(True, {}, 7, "America/Los_Angeles")
    assert policy.today(evening_in_california) == date(2026, 10, 8)
    assert remediation_policy.Policy(True, {}, 7).today(evening_in_california) == date(2026, 10, 9)
    assert remediation_policy.Policy(True, {}, 7, "Pacific/Kiritimati").today(
        datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)) == date(2026, 10, 9)
    # A stored zone this host no longer knows is UTC, not an error.
    assert remediation_policy.Policy(True, {}, 7, "Mars/Olympus").today(evening_in_california) == date(2026, 10, 9)


def _a_zone_whose_day_is_not_utcs():
    """A zone whose calendar day differs from UTC's right now, and its day."""
    now = datetime.now(timezone.utc)
    for name in ("Pacific/Kiritimati", "Etc/GMT+12"):          # UTC+14, UTC-12
        local = remediation_policy.Policy(True, {}, 7, name).today(now)
        if local != now.date():
            return name, local, now.date()
    raise AssertionError("one of UTC+14 and UTC-12 is always on another day")


def test_the_time_zone_is_a_global_admins_setting_validated_and_audited(client, db_session, tracking_on):
    assert client.get(POLICY_URL).json()["time_zone"] == "UTC"
    for bad in ("Mars/Olympus", "../../etc/passwd", "PST8"):
        refused = client.put(POLICY_URL, json={"time_zone": bad})
        assert refused.status_code == 422, (bad, refused.text)
        assert "IANA time zone" in refused.text
    saved = client.put(POLICY_URL, json={"time_zone": "America/Los_Angeles"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["time_zone"] == "America/Los_Angeles" and saved.json()["enabled"] is True
    assert client.get(POLICY_URL).json()["time_zone"] == "America/Los_Angeles"
    audit = db_session.query(AuditLog).filter_by(action="remediation_policy_updated").one()
    assert (audit.details["from"]["time_zone"], audit.details["to"]["time_zone"]) == ("UTC", "America/Los_Angeles")
    # A save that leaves the zone out keeps it.
    assert client.put(POLICY_URL, json={"due_soon_days": 5}).json()["time_zone"] == "America/Los_Angeles"


def test_states_and_the_dialogs_day_follow_the_installations_zone(client, db_session, test_project, tracking_on):
    zone, local_today, utc_today = _a_zone_whose_day_is_not_utcs()
    hosts = [_host(db_session, test_project, "10.36.0.1")]
    _, (link,) = _finding(db_session, test_project, hosts)
    # Due on the EARLIER of the two days: overdue where today is the later one.
    due = min(local_today, utc_today)
    _apply(client, test_project, [{"finding_host_id": link, "contact_email": "roger@example.com",
                                   "notified_on": (due - timedelta(days=30)).isoformat()}])
    in_utc = client.get(remediation(test_project)).json()
    assert in_utc["as_of"] == utc_today.isoformat()
    assert in_utc["items"][0]["state"] == ("overdue" if utc_today > due else "due_soon")

    assert client.put(POLICY_URL, json={"time_zone": zone}).status_code == 200
    local = client.get(remediation(test_project)).json()
    assert local["as_of"] == local_today.isoformat()
    assert local["items"][0]["state"] == ("overdue" if local_today > due else "due_soon")
    assert local["items"][0]["state"] != in_utc["items"][0]["state"]
    assert local["items"][0]["days_left"] == (due - local_today).days
    preview = client.get(f"{remediation(test_project)}/follow-up", params={"contact_email": "roger@example.com"})
    assert preview.json()["as_of"] == local_today.isoformat()
    # The alert sweep and the daily count read the same day.
    remediation_alerts.sweep(db_session)
    kinds = {n.source_type for n in db_session.query(Notification).filter_by(type="remediation")}
    assert kinds == {"remediation_overdue" if local_today > due else "remediation_due_soon"}
    remediation_service.snapshot(db_session)
    assert [row.day for row in db_session.query(RemediationDaily)] == [local_today]
    # A follow-up cannot be dated after the installation's today — with no slack.
    tomorrow = client.post(f"{remediation(test_project)}/follow-up", json={
        "contact_email": "roger@example.com", "followed_up_on": (local_today + timedelta(days=1)).isoformat()})
    assert tomorrow.status_code == 422, tomorrow.text
    today = client.post(f"{remediation(test_project)}/follow-up", json={
        "contact_email": "roger@example.com", "followed_up_on": local_today.isoformat()})
    assert today.status_code == 200, today.text


# --- H: the sweeps -----------------------------------------------------------

def test_the_alert_sweep_remembers_deadlines_a_deadline_at_a_time(client, db_session, test_project, tracking_on):
    hosts = [_host(db_session, test_project, f"10.37.0.{n}") for n in range(1, 7)]
    _, links = _finding(db_session, test_project, hosts)
    # Six overdue rows on two deadlines.
    _apply(client, test_project, [
        {"finding_host_id": link, "notified_on": day(40 if n < 3 else 50)} for n, link in enumerate(links)])
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        assert remediation_alerts.sweep(db_session) >= 1
    finally:
        event.remove(bind, "before_cursor_execute", record)
    db_session.commit()
    updates = [s for s in statements if s.startswith("UPDATE finding_host_remediation")]
    assert len(updates) == 2, updates
    remembered = {r.finding_host_id: r.overdue_alerted_for for r in db_session.query(FindingHostRemediation)}
    assert remembered == {link: UTC_TODAY - timedelta(days=(40 if n < 3 else 50) - 30)
                          for n, link in enumerate(links)}
    assert remediation_alerts.sweep(db_session) == 0            # each deadline alerts once


def test_the_daily_count_is_replaced_in_place(client, db_session, test_project, tracking_on):
    hosts = [_host(db_session, test_project, f"10.37.1.{n}") for n in range(1, 3)]
    _, links = _finding(db_session, test_project, hosts)
    _apply(client, test_project, [{"finding_host_id": links[0], "notified_on": day(40)}])
    assert remediation_service.snapshot(db_session) == 1
    db_session.commit()
    first = db_session.query(RemediationDaily).one()
    first_id, counts = first.id, (first.overdue, first.not_assigned, first.closed)
    assert counts == (1, 1, 0)
    _apply(client, test_project, [{"finding_host_id": links[0], "status": "closed", "closed_on": day(0)}])
    assert remediation_service.snapshot(db_session) == 1
    db_session.commit()
    db_session.expire_all()
    again = db_session.query(RemediationDaily).one()
    assert (again.id, again.overdue, again.not_assigned, again.closed) == (first_id, 0, 1, 1)
    # Nothing left to count: today's row goes.
    db_session.query(FindingHost).delete()
    db_session.commit()
    assert remediation_service.snapshot(db_session) == 0
    assert db_session.query(RemediationDaily).count() == 0


def test_two_workers_writing_the_daily_count_at_once_both_succeed(two_sessions):  # noqa: F811
    """A wrote today's row and has not committed.  B deleted nothing (it
    cannot see A's row), inserted, and waited on the unique index; when A
    committed, B's insert was refused and its whole sweep rolled back."""
    project, _ = two_sessions.project()

    def build(db):
        db.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                 days_low=120, due_soon_days=7))
        host = models.Host(project_id=project.id, ip_address="10.37.2.1", state="up")
        finding = Finding(project_id=project.id, title="SMB signing", severity="high",
                          status="confirmed", source="manual")
        db.add_all([host, finding])
        db.flush()
        db.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))

    two_sessions.commit(build)
    try:
        assert remediation_service.snapshot(two_sessions.a) >= 1
        outcome = {}

        def second():
            try:
                outcome["written"] = remediation_service.snapshot(two_sessions.b)
                two_sessions.b.commit()
            except BaseException as exc:  # noqa: BLE001
                two_sessions.b.rollback()
                outcome["error"] = exc

        thread = threading.Thread(target=second, daemon=True)
        thread.start()
        deadline, waiting = time.time() + 10, False
        while time.time() < deadline and not waiting and thread.is_alive():
            with two_sessions._engine.connect() as probe:
                waiting = bool(probe.execute(text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'")).scalar())
            time.sleep(0.05)
        assert waiting and thread.is_alive(), f"the second sweep did not wait for the first: {outcome}"
        two_sessions.a.commit()
        thread.join(20)
        assert not thread.is_alive()
        assert "error" not in outcome, outcome
        db = two_sessions.fresh()
        try:
            assert db.query(RemediationDaily).filter_by(project_id=project.id).count() == 1
        finally:
            db.close()
    finally:
        with two_sessions._engine.begin() as conn:
            conn.execute(text("DELETE FROM remediation_policy"))


# --- I: one definition of the frozen deadline --------------------------------

def test_the_deadline_frozen_at_close_is_the_one_the_list_derived(client, db_session, test_project, tracking_on):
    hosts = [_host(db_session, test_project, f"10.38.0.{n}") for n in range(1, 4)]
    _, links = _finding(db_session, test_project, hosts, severity="medium")
    _apply(client, test_project, [
        {"finding_host_id": links[0], "notified_on": day(100)},
        {"finding_host_id": links[1], "notified_on": day(20)},
    ])
    open_due = {row["finding_host_id"]: row["due_on"] for row in client.get(remediation(test_project)).json()["items"]}
    _apply(client, test_project, [{"finding_host_id": link, "status": "closed", "closed_on": day(0)}
                                  for link in links])
    closed = {row["finding_host_id"]: row for row in client.get(remediation(test_project)).json()["items"]}
    assert closed[links[0]]["due_on"] == open_due[links[0]] == day(10)
    assert closed[links[0]]["closed_days_late"] == 10
    assert (closed[links[1]]["due_on"], closed[links[1]]["closed_days_late"]) == (open_due[links[1]], 0)
    # Never assigned: closed with no deadline to be judged by.
    assert (closed[links[2]]["due_on"], closed[links[2]]["closed_days_late"]) == (None, None)
    # Correcting the assigned date of a row that is already closed freezes
    # the deadline again, against the timeline in force now.
    _apply(client, test_project, [{"finding_host_id": links[0], "notified_on": day(95)}])
    corrected = {row["finding_host_id"]: row for row in client.get(remediation(test_project)).json()["items"]}
    assert (corrected[links[0]]["due_on"], corrected[links[0]]["closed_days_late"]) == (day(5), 5)
    # Reopened, the stored deadline goes: an open row's deadline is derived.
    _apply(client, test_project, [{"finding_host_id": links[0], "status": "open"}])
    db_session.expire_all()
    assert db_session.query(FindingHostRemediation).filter_by(finding_host_id=links[0]).one().closed_due_on is None
