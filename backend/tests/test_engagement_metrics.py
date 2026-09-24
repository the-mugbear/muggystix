"""engagement_metrics_service — the counts Portfolio and Oversight share.

The judged / not-yet-judged split is pinned row by row to the host inspector's
own rule (``host_serialization._vuln_coverage``), so the rollup and what an
analyst sees on the host can never disagree.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case

from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)
from app.services.engagement_metrics_service import (
    Window, join_judged, observation_judged_on_host, project_engagement, tester_rows,
)
from app.services.host_serialization import _vuln_coverage, issue_coverage_map


def _vuln(db, host, scan, title, sev, cve=None):
    v = Vulnerability(
        title=title, cve_id=cve, severity=sev, source=VulnerabilitySource.NESSUS,
        host_id=host.id, scan_id=scan.id,
    )
    db.add(v)
    db.flush()
    return v


def _promote(client, pid, vuln, **body):
    r = client.post(
        f"/api/v1/projects/{pid}/vulnerabilities/{vuln.id}/promote",
        json={"vuln_id": vuln.id, **body},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _user(db, uid, name):
    u = User(
        id=uid, username=name, email=f"{name}@example.com", full_name=name,
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    db.flush()
    return u


def _fixture(client, db, project):
    """Three hosts; one CVE issue on A and B, a title-only issue on C, noise."""
    pid = project.id
    scan = models.Scan(project_id=pid, filename="n.nessus")
    a = models.Host(project_id=pid, ip_address="10.9.0.1", state="up")
    b = models.Host(project_id=pid, ip_address="10.9.0.2", state="up")
    c = models.Host(project_id=pid, ip_address="10.9.0.3", state="up")
    db.add_all([scan, a, b, c])
    db.flush()
    crit = VulnerabilitySeverity.CRITICAL
    v_a = _vuln(db, a, scan, "OpenSSH RCE", crit, cve="CVE-2099-0001")
    v_b = _vuln(db, b, scan, "OpenSSH RCE (other scanner wording)", crit, cve="CVE-2099-0001")
    v_c = _vuln(db, c, scan, "Default credentials: admin/admin", VulnerabilitySeverity.HIGH)
    v_b2 = _vuln(db, b, scan, "SMB signing not required", VulnerabilitySeverity.MEDIUM)
    _vuln(db, a, scan, "Traceroute information", VulnerabilitySeverity.INFO)
    db.commit()
    return pid, (a, b, c), (v_a, v_b, v_c, v_b2)


def test_issue_key_is_maintained_on_insert_and_update(db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="s.xml")
    host = models.Host(project_id=test_project.id, ip_address="10.9.1.1", state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    v = _vuln(db_session, host, scan, "Nessus: Weak  Cipher!", VulnerabilitySeverity.LOW)
    assert v.issue_key == "title:weak cipher"
    v.cve_id = "cve-2099-7"
    db_session.flush()
    assert v.issue_key == "cve:CVE-2099-7"


def test_findings_observations_and_the_gap(client, db_session, test_project):
    pid, (a, b, c), (v_a, v_b, v_c, v_b2) = _fixture(client, db_session, test_project)

    # Promote the CVE on host A only: one finding, host B's row stays unjudged.
    _promote(client, pid, v_a, scope="host")
    # Dismiss the title-only issue on host C: judged, but not a result.
    _promote(client, pid, v_c, status="false_positive")

    e = project_engagement(db_session, [pid])[pid]
    assert e.host_count == 3
    assert e.findings.as_dict() == {"critical": 1, "high": 0, "medium": 0, "low": 0}
    assert e.finding_affected_targets == 1
    assert e.observations.as_dict() == {"critical": 2, "high": 1, "medium": 1, "low": 0}
    assert e.observations_judged.as_dict() == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    assert e.observations_unjudged.as_dict() == {"critical": 1, "high": 0, "medium": 1, "low": 0}

    # The same issue dismissed on host B: the finding stays (open on A), the
    # gap closes.
    _promote(client, pid, v_b, status="false_positive")
    e = project_engagement(db_session, [pid])[pid]
    assert e.findings.critical == 1
    assert e.finding_affected_targets == 1
    assert e.observations_unjudged.critical == 0


def test_issue_wide_promotion_judges_every_host(client, db_session, test_project):
    pid, _hosts, (v_a, _v_b, _v_c, _v_b2) = _fixture(client, db_session, test_project)
    _promote(client, pid, v_a)  # API default: the whole issue
    e = project_engagement(db_session, [pid])[pid]
    assert e.observations_unjudged.critical == 0
    assert e.finding_affected_targets == 2


def test_judged_matches_the_host_inspector_row_by_row(client, db_session, test_project):
    pid, hosts, (v_a, v_b, v_c, _v_b2) = _fixture(client, db_session, test_project)
    _promote(client, pid, v_a, scope="host")
    _promote(client, pid, v_c, status="false_positive")

    judged_sql = dict(
        db_session.query(Vulnerability.id, case((observation_judged_on_host(), True), else_=False))
        .join(models.Host, models.Host.id == Vulnerability.host_id)
        .filter(models.Host.project_id == pid)
        .all()
    )
    # The join form the aggregates use (join_judged) — the same answer.
    joined_query, judged_expr = join_judged(
        db_session.query(Vulnerability.id)
        .select_from(Vulnerability)
        .join(models.Host, models.Host.id == Vulnerability.host_id),
        [pid],
    )
    judged_join = dict(
        joined_query.add_columns(case((judged_expr, True), else_=False))
        .filter(models.Host.project_id == pid)
        .all()
    )
    assert judged_join == judged_sql
    checked = 0
    for host in hosts:
        vulns = db_session.query(Vulnerability).filter(Vulnerability.host_id == host.id).all()
        coverage = issue_coverage_map(db_session, pid, vulns, host_id=host.id)
        for v in vulns:
            inspector = _vuln_coverage(v, coverage)["finding_on_this_host"] is True
            assert judged_sql[v.id] is inspector, (v.title, host.ip_address)
            checked += 1
    assert checked == 5


def test_review_counts_each_host_once(db_session, test_project):
    pid = test_project.id
    h1 = models.Host(project_id=pid, ip_address="10.9.2.1", state="up")
    h2 = models.Host(project_id=pid, ip_address="10.9.2.2", state="up")
    h3 = models.Host(project_id=pid, ip_address="10.9.2.3", state="up")
    db_session.add_all([h1, h2, h3])
    db_session.flush()
    u1, u2 = _user(db_session, 7101, "rev1"), _user(db_session, 7102, "rev2")
    db_session.add_all([
        HostFollow(user_id=u1.id, host_id=h1.id, status=FollowStatus.IN_REVIEW),
        HostFollow(user_id=u2.id, host_id=h1.id, status=FollowStatus.REVIEWED),
        HostFollow(user_id=u1.id, host_id=h2.id, status=FollowStatus.REVIEWED),
        HostFollow(user_id=u2.id, host_id=h3.id, status=FollowStatus.WATCHING),
    ])
    db_session.flush()
    e = project_engagement(db_session, [pid])[pid]
    assert (e.hosts_in_review, e.hosts_reviewed, e.hosts_tested) == (1, 2, 2)


def test_tester_projects_count_where_they_review_not_their_memberships(db_session, test_project):
    """v2.403.0: a tester's project count is the asked-about projects where
    they have a target in review or reviewed.  It counted memberships on
    in-progress projects, so a global admin reviewing without a membership row
    read "Projects 0 · Reviewed 29"."""
    from app.db.models_project import Project
    other = Project(name="Second engagement", slug="second-engagement", status="active")
    third = Project(name="Third engagement", slug="third-engagement", status="active")
    db_session.add_all([other, third])
    db_session.flush()
    h1 = models.Host(project_id=test_project.id, ip_address="10.9.4.1", state="up")
    h2 = models.Host(project_id=other.id, ip_address="10.9.4.2", state="up")
    h3 = models.Host(project_id=third.id, ip_address="10.9.4.3", state="up")
    db_session.add_all([h1, h2, h3])
    db_session.flush()
    u = _user(db_session, 7201, "no-membership-reviewer")
    db_session.add_all([
        HostFollow(user_id=u.id, host_id=h1.id, status=FollowStatus.REVIEWED, reviewed_at=datetime.now(timezone.utc)),
        HostFollow(user_id=u.id, host_id=h2.id, status=FollowStatus.IN_REVIEW),
        HostFollow(user_id=u.id, host_id=h3.id, status=FollowStatus.WATCHING),  # not testing
    ])
    db_session.flush()

    every = {r.user_id: r for r in tester_rows(db_session, [test_project.id, other.id, third.id], Window())}
    assert every[u.id].projects_tested() == 2
    assert all(p.role is None for p in every[u.id].projects)
    subset = {r.user_id: r for r in tester_rows(db_session, [other.id, third.id], Window())}
    assert subset[u.id].projects_tested() == 1
    assert subset[u.id].total("in_review") == 1


def test_every_requested_project_is_present(db_session):
    assert project_engagement(db_session, []) == {}
    e = project_engagement(db_session, [987654])[987654]
    assert e.host_count == 0 and e.findings.critical == 0


def test_finding_states_add_up_to_the_findings_and_false_positives_stand_apart(db_session, test_project):
    """Under investigation (open/retest) + confirmed + closed (accepted risk /
    remediated) == the findings total; a false positive — by its own status or
    on every endpoint — is counted apart, never in a state."""
    pid = test_project.id
    h = models.Host(project_id=pid, ip_address="10.9.3.1", state="up")
    db_session.add(h)
    db_session.flush()
    for i, (status, host_status) in enumerate((
        ("open", "open"), ("retest", "open"), ("confirmed", "open"), ("accepted_risk", "open"),
        ("remediated", "remediated"), ("false_positive", "open"), ("open", "false_positive"),
    )):
        f = Finding(project_id=pid, title=f"f{i}", severity="high", status=status, source="manual")
        db_session.add(f)
        db_session.flush()
        db_session.add(FindingHost(finding_id=f.id, host_id=h.id, host_status=host_status))
    db_session.flush()

    e = project_engagement(db_session, [pid])[pid]
    assert e.finding_states.as_dict() == {"under_investigation": 2, "confirmed": 1, "closed": 2}
    assert sum(e.finding_states.as_dict().values()) == e.findings.high == 5
    assert e.findings_false_positive == 2
