"""Oversight (v2.377.0) — the global administrators' programme dashboard.

Admin-only end to end; every registered project (archived included); the
counting rules shared with Portfolio; dates, tester and engagement-window
filters; the "no project admin" signal that moved here from Portfolio.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project, ProjectMembership
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)
from app.main import app

URL = "/api/v1/oversight/dashboard"
PORTFOLIO_URL = "/api/v1/portfolio/dashboard"
NOW = datetime.now(timezone.utc)
_UID = [8100]


def _user(db, name, role=UserRole.MEMBER, active=True):
    _UID[0] += 1
    u = User(
        id=_UID[0], username=name, email=f"{name}@example.com", full_name=name.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role,
        is_active=active, is_verified=True, created_at=NOW,
    )
    db.add(u)
    db.flush()
    return u


def _project(db, name, status="active", start=None, end=None):
    p = Project(name=name, slug=name.lower().replace(" ", "-"), status=status,
                is_archived=(status == "archived"), start_date=start, end_date=end)
    db.add(p)
    db.flush()
    return p


def _host(db, project, ip, first_seen=None):
    h = models.Host(project_id=project.id, ip_address=ip, state="up")
    if first_seen is not None:
        h.first_seen = first_seen
    db.add(h)
    db.flush()
    return h


def _finding(db, project, severity, hosts, status="confirmed", host_status="open"):
    f = Finding(project_id=project.id, title=f"{severity} issue", severity=severity,
                status=status, source="manual")
    db.add(f)
    db.flush()
    for h in hosts:
        db.add(FindingHost(finding_id=f.id, host_id=h.id, host_status=host_status))
    db.flush()
    return f


def _follow(db, user, host, status, reviewed_at=None):
    db.add(HostFollow(user_id=user.id, host_id=host.id, status=status, reviewed_at=reviewed_at))
    db.flush()


def _row(body, pid):
    return next(r for r in body["projects"] if r["id"] == pid)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def test_members_and_project_admins_are_refused(client, db_session, test_project):
    member = _user(db_session, "plain-member")
    proj_admin = _user(db_session, "proj-admin")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=proj_admin.id, role="admin"))
    db_session.commit()
    try:
        for who in (member, proj_admin):
            # merge: a request may end with the session expunged; re-attach.
            app.dependency_overrides[get_current_user] = lambda who=who: db_session.merge(who)
            assert client.get(URL).status_code == 403
            # Portfolio stays open to them.
            assert client.get(PORTFOLIO_URL).status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Cohort and current figures
# ---------------------------------------------------------------------------

def test_every_registered_project_including_archived(client, db_session, test_project):
    for name, status in (("Ongoing", "active"), ("Done", "completed"), ("Kept", "archived")):
        _project(db_session, name, status)
    db_session.commit()
    body = client.get(URL).json()
    s = body["summary"]
    assert s["projects_total"] == 4  # test_project is active
    assert s["projects_in_progress"] == 2
    assert s["projects_complete"] == 2
    assert {r["status"] for r in body["projects"]} == {"active", "completed", "archived"}
    only_done = client.get(URL, params={"status": ["completed", "archived"]}).json()
    assert only_done["summary"]["projects_total"] == 2
    # A saved link with the old status name still means active.
    old = client.get(URL, params={"status": ["in_progress"]}).json()
    assert old["summary"]["projects_total"] == 2


def test_testing_findings_and_defect_rate(client, db_session, test_project):
    p = test_project
    tester = _user(db_session, "tess")
    h1, h2, h3, h4 = (_host(db_session, p, f"10.20.0.{i}") for i in range(1, 5))
    _follow(db_session, tester, h1, FollowStatus.REVIEWED, reviewed_at=NOW)
    _follow(db_session, tester, h2, FollowStatus.IN_REVIEW)
    _follow(db_session, tester, h3, FollowStatus.WATCHING)          # not testing
    _finding(db_session, p, "critical", [h1])                         # tested host
    _finding(db_session, p, "high", [h1, h4])                         # one tested, one not
    _finding(db_session, p, "high", [h4])                             # untested only
    _finding(db_session, p, "critical", [h2], host_status="false_positive")  # not a result
    db_session.commit()

    body = client.get(URL).json()
    row = _row(body, p.id)
    assert (row["host_count"], row["hosts_tested"], row["hosts_in_review"], row["hosts_reviewed"]) == (4, 2, 1, 1)
    assert row["findings"] == {"critical": 1, "high": 2, "medium": 0, "low": 0}
    assert row["finding_affected_targets"] == 2
    # Of 2 tested targets: h1 has a critical and a high; h4 is not tested.
    assert row["defect_rate"] == {"critical": 50.0, "high": 50.0, "medium": 0.0, "low": 0.0}
    # Where the findings stand: all three confirmed, the dismissed one apart.
    assert row["finding_states"] == {"under_investigation": 0, "confirmed": 3, "closed": 0}
    assert row["findings_false_positive"] == 1
    sev = body["summary"]["severity"]
    assert sev["finding_states"] == row["finding_states"]
    assert sev["findings_false_positive"] == 1
    assert sev["tested_targets"] == 2
    assert all(v is None or v <= 100 for v in sev["defect_rate"].values())

    # Tester row: findings attributed through the hosts they worked on.
    t = next(t for t in body["testers"] if t["user_id"] == tester.id)
    assert (t["tested"], t["in_review"], t["reviewed"]) == (2, 1, 1)
    assert t["findings"] == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    assert t["projects"][0]["role"] is None  # reviews hosts without a membership row
    # ...and still counts the project they review in (v2.403.0: was memberships → 0).
    assert t["projects_tested"] == 1
    assert "active_projects" not in t


def test_same_numbers_as_portfolio(client, db_session, test_project):
    p = test_project
    scan = models.Scan(project_id=p.id, filename="n.nessus")
    db_session.add(scan)
    h = _host(db_session, p, "10.21.0.1")
    for title, sev in (("A", VulnerabilitySeverity.CRITICAL), ("B", VulnerabilitySeverity.HIGH)):
        db_session.add(Vulnerability(title=title, severity=sev, source=VulnerabilitySource.NESSUS,
                                     host_id=h.id, scan_id=scan.id))
    _finding(db_session, p, "medium", [h])
    db_session.commit()
    o = _row(client.get(URL).json(), p.id)
    c = next(c for c in client.get(PORTFOLIO_URL).json()["projects"] if c["id"] == p.id)
    assert o["findings"] == c["findings"]
    assert o["observations_unjudged"] == c["unjudged_observations"]
    assert o["hosts_tested"] == c["hosts_tested"]


def test_no_admin_signal_lives_here(client, db_session, test_project):
    body = client.get(URL).json()
    assert "no_admin" in _row(body, test_project.id)["attention_reasons"]
    assert body["attention"]["no_admin_projects"] >= 1

    admin = _user(db_session, "owner")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=admin.id, role="admin"))
    db_session.commit()
    row = _row(client.get(URL).json(), test_project.id)
    assert "no_admin" not in row["attention_reasons"]
    assert row["admins"] == ["Owner"]


# ---------------------------------------------------------------------------
# Dates, tester, engagement window
# ---------------------------------------------------------------------------

def test_period_figures_and_contributors(client, db_session, test_project):
    other = _project(db_session, "Second")
    ana = _user(db_session, "ana")
    old, recent = NOW - timedelta(days=60), NOW - timedelta(days=3)
    _host(db_session, test_project, "10.22.0.1", first_seen=old)
    h2 = _host(db_session, test_project, "10.22.0.2", first_seen=recent)
    h3 = _host(db_session, other, "10.22.0.3", first_seen=recent)
    _follow(db_session, ana, h2, FollowStatus.REVIEWED, reviewed_at=recent)
    _follow(db_session, ana, h3, FollowStatus.REVIEWED, reviewed_at=recent)
    s_old = models.Scan(project_id=test_project.id, filename="old.xml", uploaded_by_id=ana.id)
    s_old.created_at = old
    s_orphan = models.Scan(project_id=other.id, filename="x.xml", uploaded_by_id=None)
    s_orphan.created_at = recent
    db_session.add_all([s_old, s_orphan])
    db_session.commit()

    start = (NOW - timedelta(days=7)).date().isoformat()
    body = client.get(URL, params={"start": start, "end": NOW.date().isoformat()}).json()
    s = body["summary"]
    assert s["targets_current"] == 3
    assert s["targets_through_end"] == 3
    assert s["targets_added"] == 2
    assert s["reviews_concluded"] == 2
    assert s["imports"] == 1
    # Ana worked in both projects inside the period: one contributor, not two.
    assert s["contributors"] == 1
    assert _row(body, test_project.id)["contributors"] == 1
    assert _row(body, other.id)["contributors"] == 1
    assert s["unattributed_events"] == 1   # the scan whose uploader is unknown
    # Current figures ignore the dates.
    assert s["targets_tested"] == 2

    whole = client.get(URL).json()["summary"]
    assert whole["imports"] == 2 and whole["targets_added"] == 3


def test_tester_filter_limits_projects_and_figures(client, db_session, test_project):
    other = _project(db_session, "Elsewhere")
    ana, ben = _user(db_session, "ana2"), _user(db_session, "ben2")
    ha, hb = _host(db_session, test_project, "10.23.0.1"), _host(db_session, test_project, "10.23.0.2")
    _host(db_session, other, "10.23.0.3")
    _follow(db_session, ana, ha, FollowStatus.REVIEWED, reviewed_at=NOW)
    _follow(db_session, ben, hb, FollowStatus.REVIEWED, reviewed_at=NOW)
    _finding(db_session, test_project, "critical", [ha])
    _finding(db_session, test_project, "high", [hb])
    db_session.commit()

    body = client.get(URL, params={"tester_id": ana.id}).json()
    assert [r["id"] for r in body["projects"]] == [test_project.id]
    row = body["projects"][0]
    assert row["host_count"] == 2          # the inventory is still the inventory
    assert row["hosts_tested"] == 1
    assert row["findings"] == {"critical": 1, "high": 0, "medium": 0, "low": 0}
    assert [t["user_id"] for t in body["testers"]] == [ana.id]
    assert {o["id"] for o in body["tester_options"]} >= {ana.id, ben.id}


def test_engagement_window_overlap(client, db_session, test_project):
    d = lambda days: NOW - timedelta(days=days)  # noqa: E731
    inside = _project(db_session, "Q-now", start=d(20), end=d(5))
    ongoing = _project(db_session, "Open-ended", start=d(90))
    _project(db_session, "Long-ago", start=d(200), end=d(150))
    db_session.commit()
    params = {"start": d(30).date().isoformat(), "end": NOW.date().isoformat(), "window_overlap": True}
    ids = {r["id"] for r in client.get(URL, params=params).json()["projects"]}
    assert ids == {inside.id, ongoing.id}   # test_project has no dates → excluded


def test_invalid_dates_are_refused(client):
    today = date.today()
    assert client.get(URL, params={"start": today.isoformat(),
                                   "end": (today - timedelta(days=1)).isoformat()}).status_code == 422
    assert client.get(URL, params={"end": (today + timedelta(days=2)).isoformat()}).status_code == 422


def test_organisation_accounts(client, db_session):
    _user(db_session, "off", active=False)
    db_session.commit()
    acc = client.get(URL).json()["accounts"]
    assert acc["total"] == acc["enabled"] + acc["disabled"]
    assert acc["disabled"] >= 1
    assert acc["without_membership"] >= 1


# ---------------------------------------------------------------------------
# Growth series and the "first recorded in the period" severity basis
# ---------------------------------------------------------------------------

def test_growth_series_carries_the_earlier_total(client, db_session, test_project):
    ana = _user(db_session, "grower")
    today = NOW.date()
    _host(db_session, test_project, "10.24.0.1", first_seen=NOW - timedelta(days=40))   # before
    h2 = _host(db_session, test_project, "10.24.0.2", first_seen=NOW - timedelta(days=2))
    _host(db_session, test_project, "10.24.0.3", first_seen=NOW - timedelta(days=2))
    _follow(db_session, ana, h2, FollowStatus.REVIEWED, reviewed_at=NOW - timedelta(days=1))
    db_session.commit()

    start = today - timedelta(days=6)
    g = client.get(URL, params={"start": start.isoformat(), "end": today.isoformat()}).json()["growth"]
    assert g["unit"] == "day"
    assert [p["start"] for p in g["points"]][0] == start.isoformat()
    assert len(g["points"]) == 7
    by_day = {p["start"]: p for p in g["points"]}
    two_ago = (NOW - timedelta(days=2)).date().isoformat()
    assert g["points"][0]["cumulative_targets"] == 1                      # the host recorded earlier
    assert by_day[two_ago]["targets_added"] == 2
    assert by_day[two_ago]["cumulative_targets"] == 3
    assert by_day[(NOW - timedelta(days=1)).date().isoformat()]["reviews_concluded"] == 1
    assert g["points"][-1]["cumulative_targets"] == 3

    # A long range folds into weeks / months, never thousands of points.
    wide = client.get(URL, params={"start": (today - timedelta(days=200)).isoformat(),
                                   "end": today.isoformat()}).json()["growth"]
    assert wide["unit"] == "week" and len(wide["points"]) <= 30
    assert client.get(URL).json()["growth"]["unit"] in ("day", "week", "month")


def test_period_severity_basis_counts_what_was_recorded_in_the_dates(client, db_session, test_project):
    p = test_project
    scan = models.Scan(project_id=p.id, filename="n.nessus")
    db_session.add(scan)
    h = _host(db_session, p, "10.25.0.1")
    old = Vulnerability(title="Old", severity=VulnerabilitySeverity.CRITICAL,
                        source=VulnerabilitySource.NESSUS, host_id=h.id, scan_id=scan.id)
    old.first_seen = (NOW - timedelta(days=90)).replace(tzinfo=None)
    new = Vulnerability(title="New", severity=VulnerabilitySeverity.CRITICAL,
                        source=VulnerabilitySource.NESSUS, host_id=h.id, scan_id=scan.id)
    db_session.add_all([old, new])
    f_old = _finding(db_session, p, "high", [h])
    f_old.created_at = NOW - timedelta(days=90)
    _finding(db_session, p, "high", [h])
    db_session.commit()

    params = {"start": (NOW - timedelta(days=7)).date().isoformat(), "end": NOW.date().isoformat()}
    current = client.get(URL, params=params).json()
    period = client.get(URL, params={**params, "severity_basis": "period"}).json()
    assert current["severity_basis"] == "current" and period["severity_basis"] == "period"
    assert current["summary"]["severity"]["observations"]["critical"] == 2
    assert period["summary"]["severity"]["observations"]["critical"] == 1
    assert current["summary"]["severity"]["findings"]["high"] == 2
    assert period["summary"]["severity"]["findings"]["high"] == 1
    # The defect rate is current in both.
    assert current["summary"]["severity"]["defect_rate"] == period["summary"]["severity"]["defect_rate"]


def test_rows_carry_every_scanner_observation_and_the_judged_split(client, db_session, test_project):
    """The table's Scanner observations column: the total and the judged /
    not-yet-judged split of the SAME rows, so judged + not yet = total."""
    p = test_project
    h = _host(db_session, p, "10.20.9.1")
    scan = models.Scan(project_id=p.id, filename="n.nessus")
    db_session.add(scan)
    db_session.flush()
    for title, sev in (("A", VulnerabilitySeverity.CRITICAL), ("B", VulnerabilitySeverity.HIGH),
                       ("C", VulnerabilitySeverity.MEDIUM), ("D", VulnerabilitySeverity.INFO)):
        db_session.add(Vulnerability(title=title, severity=sev, source=VulnerabilitySource.NESSUS,
                                     host_id=h.id, scan_id=scan.id))
    db_session.commit()
    row = _row(client.get(URL).json(), p.id)
    assert row["observations"] == {"critical": 1, "high": 1, "medium": 1, "low": 0}  # info left out
    for k in ("critical", "high", "medium", "low"):
        assert row["observations_judged"][k] + row["observations_unjudged"][k] == row["observations"][k]


# ---------------------------------------------------------------------------
# A subset of projects (the page's project multi-select)
# ---------------------------------------------------------------------------

_ADDITIVE = ("projects_total", "projects_in_progress", "projects_complete", "targets_current",
             "targets_through_end", "targets_added", "targets_tested", "targets_in_review",
             "targets_reviewed", "reviews_concluded", "imports")


def _seed_three(db):
    """Three projects with different inventories, testing, findings and
    tester work — so a wrong subset cannot add up by accident."""
    ana, ben = _user(db, "subset-ana"), _user(db, "subset-ben")
    a, b, c = (_project(db, n, s) for n, s in
               (("Subset A", "active"), ("Subset B", "active"), ("Subset C", "completed")))
    ha = [_host(db, a, f"10.60.0.{i}", first_seen=NOW) for i in range(1, 4)]
    hb = [_host(db, b, f"10.61.0.{i}", first_seen=NOW) for i in range(1, 6)]
    hc = [_host(db, c, f"10.62.0.{i}", first_seen=NOW) for i in range(1, 3)]
    _follow(db, ana, ha[0], FollowStatus.REVIEWED, reviewed_at=NOW)
    _follow(db, ana, hb[0], FollowStatus.IN_REVIEW)
    _follow(db, ben, hb[1], FollowStatus.REVIEWED, reviewed_at=NOW)
    _follow(db, ben, hc[0], FollowStatus.REVIEWED, reviewed_at=NOW)
    _finding(db, a, "critical", [ha[0]])
    _finding(db, b, "high", [hb[0], hb[1]])
    _finding(db, b, "medium", [hb[2]])
    _finding(db, c, "low", [hc[0]])
    db.commit()
    return (a, b, c), (ana, ben)


def _merged(*severities):
    return {k: sum(s[k] for s in severities) for k in severities[0]}


def test_a_subset_of_projects_adds_up_to_exactly_those_projects(client, db_session, test_project):
    (a, b, c), (ana, ben) = _seed_three(db_session)
    one = {p.id: client.get(URL, params={"project_id": [p.id]}).json() for p in (a, b, c)}
    both = client.get(URL, params={"project_id": [a.id, b.id]}).json()

    assert {r["id"] for r in both["projects"]} == {a.id, b.id}
    for key in _ADDITIVE:
        assert both["summary"][key] == one[a.id]["summary"][key] + one[b.id]["summary"][key], key
    sev, sa, sb = both["summary"]["severity"], one[a.id]["summary"]["severity"], one[b.id]["summary"]["severity"]
    for key in ("findings", "observations", "observations_unjudged", "defect_targets"):
        assert sev[key] == _merged(sa[key], sb[key]), key
    assert sev["tested_targets"] == sa["tested_targets"] + sb["tested_targets"]
    assert both["attention"]["critical_projects"] == 1   # A's critical; C (the low) is not in it
    # Testers: only work inside the subset — Ben's review in C is not counted.
    rows = {t["user_id"]: t for t in both["testers"]}
    assert rows[ben.id]["reviewed"] == 1
    assert {p["project_id"] for p in rows[ben.id]["projects"]} == {b.id}
    assert rows[ben.id]["projects_tested"] == 1
    assert rows[ana.id]["tested"] == 2
    assert rows[ana.id]["projects_tested"] == 2
    # Growth covers the subset too.
    assert sum(pt["targets_added"] for pt in both["growth"]["points"]) == 3 + 5
    # The option list still offers every project, so the subset can be changed.
    assert {o["id"] for o in both["project_options"]} >= {a.id, b.id, c.id, test_project.id}


def test_no_project_filter_is_every_project(client, db_session, test_project):
    (a, b, c), _ = _seed_three(db_session)
    every = client.get(URL).json()
    listed = client.get(URL, params={"project_id": [a.id, b.id, c.id, test_project.id]}).json()
    assert every["summary"] == listed["summary"]
    assert every["summary"]["projects_total"] == 4


def test_project_filter_validation(client, db_session, test_project):
    # An id naming no project covers nothing; the rest still apply.
    body = client.get(URL, params={"project_id": [test_project.id, 999_999]}).json()
    assert [r["id"] for r in body["projects"]] == [test_project.id]
    assert body["summary"]["projects_total"] == 1
    # Only unknown ids: an empty cohort, never silently every project.
    assert client.get(URL, params={"project_id": [999_999]}).json()["summary"]["projects_total"] == 0
    assert client.get(URL, params={"project_id": [0]}).status_code == 422
    assert client.get(URL, params={"project_id": ["x"]}).status_code == 422
    assert client.get(URL, params={"project_id": list(range(1, 502))}).status_code == 422
    assert client.get(URL, params={"project_id": list(range(1, 501))}).status_code == 200


def test_a_project_subset_stays_admin_only(client, db_session, test_project):
    member = _user(db_session, "subset-member")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="admin"))
    db_session.commit()
    try:
        app.dependency_overrides[get_current_user] = lambda: db_session.merge(member)
        assert client.get(URL, params={"project_id": [test_project.id]}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
