"""Second review of 2026-10-23, inventory reads (INV-1 … INV-9).

* INV-1 — a host's conflicts are ONE count, host AND port rows
  (``host_detail_service.host_conflict_counts`` / ``conflicts_per_host``; owner,
  2026-10-23): the assessment's "Conflicts", the conflicts pane (which lists
  what it counts), the Hosts-list badge and the queue's tier 5 agree for a
  port-only conflict.
* INV-2 — scan compare's "state changed" needs both scans to have SAID a state.
* INV-3 — the assessment's eligible / assessed are the Evidence page's rules.
* INV-4 — ``assigned:`` suggests exactly whom ``assigned:`` accepts.
* INV-5 — "on a host I have In Review" is an EXISTS, not a literal IN list.
* INV-6 — Operations blockers read only the rows they show, counts intact.
* INV-7 — the scan page's Hosts and Ports tabs are in address order.
* INV-8 — systemic examples are the lowest host ids, read for those alone.
* INV-9 — the retired ``watching`` is not a review filter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.db import models
from app.db.models import FollowStatus, HostFollow, HostSubnetMapping, Scope, Site, Subnet
from app.db.models_auth import User, UserRole
from app.db.models_confidence import ConflictHistory, NetexecResult
from app.db.models_host_tests import HostTest
from app.db.models_project import Project, ProjectMembership
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.evidence_service import assessed_host_ids, eligible_host_ids
from app.services.host_assessment_service import host_assessment
from app.services.operations_read_service import (
    compute_blockers, compute_investigation_queue, compute_my_tasks,
)
from app.services.scan_diff_service import compute_scan_diff
from app.services.scan_snapshot_service import scan_host_snapshots, scan_port_observations
from app.services.systemic_insight_service import compute_systemic_insights

NOW = datetime.now(timezone.utc)


def _host(db, project_id, ip, **kw):
    h = models.Host(project_id=project_id, ip_address=ip, state="up", **kw)
    # An old host: "new this week" would put it in queue tier 4.
    h.first_seen = NOW - timedelta(days=30)
    db.add(h)
    db.flush()
    return h


def _port(db, host, number, *, state="open", service=None):
    p = models.Port(host_id=host.id, port_number=number, protocol="tcp", state=state, service_name=service)
    db.add(p)
    db.flush()
    return p


def _scan(db, project_id, name="s.xml", tool="nmap"):
    s = models.Scan(project_id=project_id, filename=name, tool_name=tool)
    db.add(s)
    db.flush()
    return s


def _user(db, uid, name, *, role=UserRole.MEMBER, active=True):
    u = User(id=uid, username=name, email=f"{name}@example.com", full_name=None,
             hashed_password="x", role=role, is_active=active, created_at=NOW)
    db.add(u)
    db.flush()
    return u


# --- INV-1 ------------------------------------------------------------------

def test_a_port_only_conflict_is_one_count_everywhere_and_the_pane_lists_it(client, db_session, test_project):
    pid = test_project.id
    host = _host(db_session, pid, "10.241.0.1")
    port = _port(db_session, host, 443, service="https")
    a, b = _scan(db_session, pid, "a.xml"), _scan(db_session, pid, "b.xml")
    # What two scans naming the port's service differently write: port_id set,
    # host_id NULL.
    db_session.add(ConflictHistory(port_id=port.id, field_name="service_name", previous_value="http",
                                   new_value="https", previous_scan_id=a.id, new_scan_id=b.id))
    clean = _host(db_session, pid, "10.241.0.2")
    db_session.commit()
    base = f"/api/v1/projects/{pid}/hosts"

    detail = client.get(f"{base}/{host.id}").json()
    pane = client.get(f"{base}/{host.id}/conflicts").json()
    rows = {r["id"]: r for r in client.get(f"{base}/").json()["items"]}
    assert detail["assessment"]["conflicts"] == pane["conflict_count"] == rows[host.id]["conflict_count"] == 1
    # The pane opens what it counts: the port row is listed.
    assert [(c["object_type"], c["object_id"]) for c in pane["conflict_history"]] == [("port", port.id)]
    assert rows[clean.id].get("conflict_count", 0) == 0

    # Queue tier 5 ("Scans disagree") holds exactly the hosts the count is not zero for.
    queue = compute_investigation_queue(db_session, test_project, limit=25)
    assert [(r.host_id, r.tier) for r in queue.items] == [(host.id, 5)]
    assert queue.items[0].reasons[0].kind == "conflicts"


def test_host_and_port_conflicts_add_up_and_the_cap_says_what_it_left(client, db_session, test_project):
    from app.services.host_detail_service import host_conflict_counts, host_conflict_history

    host = _host(db_session, test_project.id, "10.241.1.1")
    port = _port(db_session, host, 22)
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for n in range(3):
        db_session.add(ConflictHistory(host_id=host.id, field_name="hostname", previous_value=f"a{n}",
                                       new_value=f"b{n}", resolved_at=at))
        db_session.add(ConflictHistory(port_id=port.id, field_name="service_name", previous_value=f"x{n}",
                                       new_value=f"y{n}", resolved_at=at + timedelta(minutes=n + 1)))
    # Another host's port conflict is not this host's.
    other = _host(db_session, test_project.id, "10.241.1.2")
    db_session.add(ConflictHistory(port_id=_port(db_session, other, 80).id, field_name="service_name",
                                   previous_value="p", new_value="q"))
    db_session.commit()

    assert host_conflict_counts(db_session, [host.id, other.id]) == {host.id: 6, other.id: 1}
    assert host_assessment(db_session, host)["conflicts"] == 6
    listed = host_conflict_history(db_session, host, limit=4)
    # One list under one cap, newest first: the three port rows are newer.
    assert [c["object_type"] for c in listed] == ["port", "port", "port", "host"]


# --- INV-2 ------------------------------------------------------------------

def _seen(db, host, scan, state):
    db.add(models.HostScanHistory(host_id=host.id, scan_id=scan.id, state_at_scan=state, discovered_at=NOW))


def test_a_scan_that_recorded_no_state_changes_no_hosts_state(db_session, test_project):
    pid = test_project.id
    hosts = [_host(db_session, pid, f"10.242.0.{n}") for n in (1, 2, 3)]
    nmap, dns, gnmap = _scan(db_session, pid, "a.xml"), _scan(db_session, pid, "d.json", "dnsx"), \
        _scan(db_session, pid, "p.gnmap")
    later = _scan(db_session, pid, "b.xml")
    for h in hosts:
        _seen(db_session, h, nmap, "up")
        _seen(db_session, h, dns, None)          # a DNS import says nothing about liveness
        _seen(db_session, h, gnmap, "unknown")   # nor does a gnmap Ports-only line
    _seen(db_session, hosts[0], later, "down")
    _seen(db_session, hosts[1], later, "up")
    db_session.flush()

    for other in (dns, gnmap):
        diff = compute_scan_diff(db_session, pid, nmap.id, other.id)
        assert diff.counts.host_state_changes == 0 and diff.host_state_changes == []
        assert diff.scan_a.total_hosts == diff.scan_b.total_hosts == 3
        assert diff.scan_b.up_hosts is None      # this side said nothing
    # A stated change still counts.
    diff = compute_scan_diff(db_session, pid, nmap.id, later.id)
    assert diff.counts.host_state_changes == 1
    assert [(r.host_id, r.state_a, r.state_b) for r in diff.host_state_changes] == [(hosts[0].id, "up", "down")]


# --- INV-3 ------------------------------------------------------------------

def test_the_assessment_reads_the_evidence_pages_rules_host_by_host(db_session, test_project):
    pid = test_project.id
    other = Project(name="other-inv3", slug="other-inv3", description="x")
    db_session.add(other)
    db_session.flush()
    scan, other_scan = _scan(db_session, pid), _scan(db_session, other.id)
    nessus = _scan(db_session, pid, "v.nessus", "Nessus")

    web = _host(db_session, pid, "10.243.0.1")
    _port(db_session, web, 8443)
    db_session.add(models.WebInterface(project_id=pid, host_id=web.id, scan_id=scan.id, port=8443,
                                       url="https://10.243.0.1:8443/", source="httpx"))
    odd_http = _host(db_session, pid, "10.243.0.2")
    _port(db_session, odd_http, 7001, service="http-alt")
    # A web interface recorded under ANOTHER project does not assess this host
    # (the drift INV-3 named: the old copy counted it).
    foreign_web = _host(db_session, pid, "10.243.0.3")
    _port(db_session, foreign_web, 80)
    db_session.add(models.WebInterface(project_id=other.id, host_id=foreign_web.id, scan_id=other_scan.id,
                                       port=80, url="http://10.243.0.3/", source="httpx"))
    closed_smb = _host(db_session, pid, "10.243.0.4")
    _port(db_session, closed_smb, 445, state="closed")
    smb = _host(db_session, pid, "10.243.0.5", smb_signing="disabled")
    _port(db_session, smb, 445)
    netexec = _host(db_session, pid, "10.243.0.6")
    _port(db_session, netexec, 389)
    db_session.add(NetexecResult(host_id=netexec.id, scan_id=scan.id, protocol="smb"))
    scanned_clean = _host(db_session, pid, "10.243.0.7")
    db_session.add(models.HostScanHistory(host_id=scanned_clean.id, scan_id=nessus.id, state_at_scan="up"))
    with_row = _host(db_session, pid, "10.243.0.8")
    db_session.add(Vulnerability(host_id=with_row.id, scan_id=scan.id, title="t",
                                 severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS))
    bare = _host(db_session, pid, "10.243.0.9")
    db_session.commit()

    eligible = eligible_host_ids(db_session, pid)
    assessed = assessed_host_ids(db_session, pid)
    fields = {"web_tls": ("web_eligible", "web_assessed"),
              "auth_smb_ad": ("auth_eligible", "auth_assessed")}
    for host in (web, odd_http, foreign_web, closed_smb, smb, netexec, scanned_clean, with_row, bare):
        db_session.refresh(host)
        a = host_assessment(db_session, host)
        for domain, (e_key, a_key) in fields.items():
            assert a[e_key] == (host.id in eligible[domain]), (host.ip_address, e_key)
            assert a[a_key] == (host.id in assessed[domain]), (host.ip_address, a_key)
        assert a["vuln_assessed"] == (host.id in assessed["vuln_assessment"]), host.ip_address
    assert host_assessment(db_session, foreign_web)["web_assessed"] is False
    assert host_assessment(db_session, closed_smb)["auth_eligible"] is False


# --- INV-4 ------------------------------------------------------------------

def test_assigned_suggests_everyone_assigned_accepts(client, db_session, test_project):
    pid = test_project.id
    member = _user(db_session, 24101, "inv4.member")
    db_session.add(ProjectMembership(project_id=pid, user_id=member.id, role="analyst"))
    _user(db_session, 24102, "inv4.admin", role=UserRole.ADMIN)           # global admin, no membership
    former = _user(db_session, 24103, "inv4.former")                       # removed, still holds a host
    _user(db_session, 24104, "inv4.outsider")                              # nobody here
    host = _host(db_session, pid, "10.244.0.1")
    db_session.add(HostFollow(host_id=host.id, user_id=former.id, status=FollowStatus.IN_REVIEW,
                              assigned_at=NOW))
    db_session.commit()

    r = client.get(f"/api/v1/projects/{pid}/hosts/query/suggest", params={"field": "assigned", "prefix": "inv4"})
    assert r.status_code == 200, r.text
    values = {v["value"]: v for v in r.json()["values"]}
    assert set(values) == {"inv4.member", "inv4.admin", "inv4.former"}
    assert values["inv4.former"]["count"] == 1
    for name in values:
        ok = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": f"assigned:{name}"})
        assert ok.status_code == 200, (name, ok.text)
    refused = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": "assigned:inv4.outsider"})
    assert refused.status_code == 400


# --- INV-5 ------------------------------------------------------------------

def test_my_tasks_over_more_than_a_thousand_hosts_in_review_matches_the_reference(
    db_session, test_project, test_user,
):
    pid = test_project.id
    me = test_user.id
    teammate = _user(db_session, 24201, "inv5.teammate")
    hosts = [models.Host(project_id=pid, ip_address=f"10.245.{n // 250}.{n % 250}", state="up")
             for n in range(1200)]
    db_session.add_all(hosts)
    db_session.flush()
    follows, tests = [], []
    for n, h in enumerate(hosts):
        if n < 1100:
            follows.append(HostFollow(host_id=h.id, user_id=me, status=FollowStatus.IN_REVIEW))
        elif n < 1150:
            follows.append(HostFollow(host_id=h.id, user_id=teammate.id, status=FollowStatus.IN_REVIEW))
        tests.append(HostTest(
            project_id=pid, host_id=h.id, tool="nmap", description="d", rationale="r", source="person",
            priority=("critical", "high", "medium", "low")[n % 4],
            status=("proposed", "in_progress", "done")[n % 3],
            assigned_to_id=(me if n % 7 == 0 else teammate.id if n % 7 == 1 else None),
            request_key=f"inv5-{n}", request_hash=f"inv5-{n}",
        ))
    db_session.add_all(follows + tests)
    db_session.commit()

    # The reference: the rule as Python over the rows.
    mine_in_review = {f.host_id for f in follows if f.user_id == me}
    expected = {}
    for t in tests:
        if t.status not in ("proposed", "in_progress"):
            continue
        reasons = [r for r, hit in (
            ("assigned", t.assigned_to_id == me),
            ("in_review", t.host_id in mine_in_review),
            ("triage", t.assigned_to_id is None and t.priority in ("critical", "high")),
        ) if hit]
        if reasons:
            expected[t.id] = reasons

    bind = db_session.get_bind()
    engine = getattr(bind, "engine", bind)
    widest = {"n": 0}

    def record(conn, cursor, statement, parameters, context, executemany):
        widest["n"] = max(widest["n"], len(parameters or ()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        counts = compute_my_tasks(db_session, test_user, test_project, limit=0, offset=0)
        listed = {}
        for group in ("assigned", "in_review", "triage"):
            offset = 0
            while True:
                page = compute_my_tasks(db_session, test_user, test_project, limit=200,
                                        group=group, offset=offset)
                if not page.items:
                    break
                for item in page.items:
                    assert item.test_id not in listed
                    listed[item.test_id] = (group, item.reasons)
                offset += len(page.items)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert {tid: reasons for tid, (_g, reasons) in listed.items()} == expected
    # Each test under its strongest reason.
    assert all(group == reasons[0] for group, reasons in listed.values())
    assert counts.total_open == len(expected)
    for reason in ("assigned", "in_review", "triage"):
        assert getattr(counts.reason_counts, reason) == sum(reason in r for r in expected.values())
        assert getattr(counts.group_counts, reason) == sum(r[0] == reason for r in expected.values())
    # No statement binds the In Review set: 1,100 hosts would be 1,100 parameters.
    assert widest["n"] < 50, widest


# --- INV-6 ------------------------------------------------------------------

def _job(db, project, name, *, status, partial=False, created_at):
    job = models.IngestionJob(project_id=project.id, filename=name, original_filename=name,
                              storage_path="/x", status=status, partial=partial,
                              content_sha256=name.ljust(64, "0")[:64], created_at=created_at,
                              error_message="broken" if status == "failed" else None)
    db.add(job)
    db.flush()
    return job


def test_blockers_count_every_blocked_import_while_listing_only_three(db_session, test_project):
    old = NOW - timedelta(days=2)
    failed = _job(db_session, test_project, "failed", status="failed", created_at=old)
    instant = NOW - timedelta(hours=1)
    partials = [_job(db_session, test_project, f"partial{n}", status="completed", partial=True,
                     created_at=instant) for n in range(3)]
    db_session.commit()

    blockers = compute_blockers(db_session, test_project, limit=3)
    # The failed job is not among the three shown, and is still counted.
    assert (blockers.failed_import_count, blockers.partial_import_count) == (1, 3)
    # Created in one instant: the later id first, on every read.
    assert [i.job_id for i in blockers.imports] == [p.id for p in reversed(partials)]
    assert failed.id not in {i.job_id for i in blockers.imports}


# --- INV-7 ------------------------------------------------------------------

def test_the_scan_pages_tabs_are_in_address_order(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    for ip in ("10.247.0.10", "10.247.0.2", "10.247.0.1"):
        h = _host(db_session, pid, ip)
        _seen(db_session, h, scan, "up")
        p = _port(db_session, h, 22)
        db_session.add(models.PortScanHistory(port_id=p.id, scan_id=scan.id, state_at_scan="open"))
    db_session.commit()

    order = ["10.247.0.1", "10.247.0.2", "10.247.0.10"]
    assert [r.ip_address for r in scan_host_snapshots(db_session, pid, scan.id).items] == order
    assert [r.ip_address for r in scan_port_observations(db_session, pid, scan.id).items] == order


# --- INV-8 ------------------------------------------------------------------

def test_systemic_examples_are_the_lowest_host_ids_whatever_the_order(db_session, test_project):
    pid = test_project.id
    scope = Scope(project_id=pid)
    db_session.add(scope)
    sites = [Site(project_id=pid, name="HQ", criticality_tier=1), Site(project_id=pid, name="Branch", criticality_tier=3)]
    db_session.add_all(sites)
    db_session.flush()
    subnets = [Subnet(scope_id=scope.id, cidr="10.248.1.0/24", site="HQ", site_id=sites[0].id),
               Subnet(scope_id=scope.id, cidr="10.248.2.0/24", site="Branch", site_id=sites[1].id)]
    db_session.add_all(subnets)
    db_session.flush()
    hosts = []
    for index, subnet in enumerate(subnets, start=1):
        for n in range(6, 0, -1):     # created in falling address order
            h = _host(db_session, pid, f"10.248.{index}.{n}", smb_signing="disabled")
            db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
            hosts.append(h)
    db_session.commit()

    out = compute_systemic_insights(db_session, pid)
    smb = next(c for c in out["conditions"] if c["key"] == "smb_signing")
    assert smb["affected_hosts"] == 12
    assert smb["example_ips"] == [h.ip_address for h in sorted(hosts, key=lambda h: h.id)[:5]]
    assert compute_systemic_insights(db_session, pid)["conditions"] == out["conditions"]


# --- INV-9 ------------------------------------------------------------------

def test_the_retired_watching_is_not_a_review_filter_on_either_door(client, db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.249.0.1")
    db_session.add(HostFollow(host_id=host.id, user_id=test_user.id, status=FollowStatus.WATCHING))
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/hosts/"

    discrete = client.get(base, params={"follow_status": "watching"})
    assert discrete.status_code == 422 and "watching" in discrete.text, discrete.text
    dsl = client.get(base, params={"q": "follow:watching"})
    assert dsl.status_code == 400 and "watching" in dsl.text, dsl.text
    # What is offered still works.
    for value in ("none", "in_review", "reviewed", "in_review_any"):
        assert client.get(base, params={"follow_status": value}).status_code == 200, value
    for value in ("mine", "revisit", "none"):
        assert client.get(base, params={"q": f"follow:{value}"}).status_code == 200, value
