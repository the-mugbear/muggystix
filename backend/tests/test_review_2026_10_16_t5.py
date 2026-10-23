"""Comprehensive review 2026-10-16, batch T5, A1 — the latest-observation
weakness flags in SQL.

`has:cert_issue`, `has:weak_tls` and `has:weak_auth` were a loop over every
web-interface / NetExec row of the project in Python, handed to the Hosts
filter as a literal list of host ids on every filtered request (and the same
loop was in Subnet Insights).  They are correlated EXISTS now
(`host_condition_sets.*_condition`).  Pinned here: the SQL answers what the
loop answered (the loop is kept below as the reference), on data built to
test each rule, and the one place they may differ — two observations of one
thing at the same moment — follows the stated rule (the later row id wins).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import null

from app.db import models
from app.db.models import Host, WebInterface
from app.db.models_confidence import NetexecResult
from app.services import host_condition_sets as HC
from app.services.cert_fields import cert_issue_from_columns
from app.services.subnet_insight_service import _EPOCH, _is_weak_user, _normalize_dt

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
T = [NOW - timedelta(days=d) for d in range(10)]  # T[0] newest


# --- the loops these replaced (reference) -----------------------------------

def _old_latest(rows, key, stamp):
    """(The callers read fresh rows: `_fresh`.)"""
    latest = {}
    for row in rows:
        ls = _normalize_dt(getattr(row, stamp)) or _EPOCH
        prev = latest.get(key(row))
        if prev is None or ls >= prev[0]:
            latest[key(row)] = (ls, row)
    return [row for _ls, row in latest.values()]


def _old_cert(db, pid):
    db.expire_all()  # the rows as stored, server defaults included
    rows =[w for w in db.query(WebInterface).filter(WebInterface.project_id == pid, WebInterface.host_id.isnot(None))
            if w.cert_not_after is not None or w.cert_self_signed is not None]
    return {w.host_id for w in _old_latest(rows, lambda w: (w.host_id, w.url), "last_seen")
            if cert_issue_from_columns(_normalize_dt(w.cert_not_after), w.cert_self_signed, NOW)}


def _old_tls(db, pid):
    db.expire_all()
    rows =[w for w in db.query(WebInterface).filter(WebInterface.project_id == pid, WebInterface.host_id.isnot(None))
            if w.tls_weak_protocol is not None]
    return {w.host_id for w in _old_latest(rows, lambda w: (w.host_id, w.url), "last_seen") if w.tls_weak_protocol}


def _old_auth(db, pid):
    db.expire_all()
    rows =(db.query(NetexecResult).join(Host, NetexecResult.host_id == Host.id)
            .filter(Host.project_id == pid, NetexecResult.auth_success.isnot(None)).all())
    return {r.host_id for r in _old_latest(rows, lambda r: (r.host_id, r.protocol, r.port), "discovered_at")
            if r.auth_success and _is_weak_user(r.username)}


# --- data ---------------------------------------------------------------------

def _estate(db, project):
    scan = models.Scan(project_id=project.id, filename="w.json", tool_name="httpx")
    db.add(scan)
    db.flush()
    hosts = []
    for i in range(1, 13):
        h = Host(project_id=project.id, ip_address=f"10.77.0.{i}", state="up")
        db.add(h)
        db.flush()
        hosts.append(h)

    made = [0]

    def web(h, url, seen, **cols):
        # One row per observation: (scan, url, source) is unique, as it is for
        # one scan's report of a URL.
        made[0] += 1
        db.add(WebInterface(project_id=project.id, host_id=h.id, scan_id=scan.id, url=url,
                            source=f"httpx-{made[0]}", last_seen=seen, **cols))

    def nxc(h, proto, port, at, success, user):
        db.add(NetexecResult(scan_id=scan.id, host_id=h.id, protocol=proto, port=port,
                             auth_success=success, username=user, discovered_at=at))

    expired, future = NOW - timedelta(days=30), NOW + timedelta(days=300)
    h = hosts
    web(h[0], "https://a/", T[3], cert_not_after=expired)                          # expired, alone
    web(h[1], "https://b/", T[5], cert_not_after=expired)                          # expired, then
    web(h[1], "https://b/", T[1], cert_not_after=future, cert_self_signed=False)   # re-observed clean
    web(h[2], "https://c/", T[1], cert_self_signed=True)                           # self-signed now…
    web(h[2], "https://c/", T[4], cert_not_after=future, cert_self_signed=False)   # …clean before
    # No timestamp: the oldest.  `null()`, not None — the column's server
    # default would otherwise stamp the insert time.
    web(h[3], "https://d/", null(), cert_self_signed=True)
    web(h[3], "https://d/", T[6], cert_self_signed=False)
    web(h[4], "https://e1/", T[2], cert_self_signed=False)                          # one clean URL,
    web(h[4], "https://e2/", T[2], cert_not_after=expired)                          # one bad: flagged
    web(h[5], "https://f/", T[1])                                                   # nothing observed
    web(h[6], "https://g/", T[2], tls_weak_protocol=True)                           # weak TLS
    web(h[7], "https://h/", T[5], tls_weak_protocol=True)                           # weak, then
    web(h[7], "https://h/", T[1], tls_weak_protocol=False)                          # fixed
    web(h[7], "https://h/", T[0])                                                   # not enumerated: no observation
    nxc(h[8], "smb", 445, T[2], True, "guest")                                      # weak auth
    nxc(h[9], "smb", 445, T[4], True, "Guest ")                                     # weak, then
    nxc(h[9], "smb", 445, T[1], False, "guest")                                     # failed: retired
    nxc(h[10], "smb", 445, T[2], True, None)                                        # unknown user: never weak
    nxc(h[10], "ftp", None, T[3], True, "anonymous")                                # NULL port, weak
    nxc(h[11], "ldap", None, T[3], True, "")                                        # blank: weak, then
    nxc(h[11], "ldap", None, T[1], True, "admin")                                   # a real user: retired
    nxc(h[11], "smb", 445, T[0], None, "guest")                                     # a banner: no login
    db.commit()
    return hosts


def test_the_sql_answers_what_the_loop_answered(db_session, test_project):
    hosts = _estate(db_session, test_project)
    pid = test_project.id
    cert = HC.cert_issue_host_ids(db_session, pid, NOW)
    tls = HC.weak_tls_host_ids(db_session, pid)
    auth = HC.weak_auth_host_ids(db_session, pid)
    assert cert == _old_cert(db_session, pid) == {hosts[0].id, hosts[2].id, hosts[4].id}
    assert tls == _old_tls(db_session, pid) == {hosts[6].id}
    assert auth == _old_auth(db_session, pid) == {hosts[8].id, hosts[10].id}
    # `host_ids` narrows the answer, never changes a host's verdict.
    assert HC.cert_issue_host_ids(db_session, pid, NOW, host_ids=[hosts[0].id, hosts[1].id]) == {hosts[0].id}


def test_the_hosts_filter_lists_the_same_hosts_without_an_id_list(client, db_session, test_project):
    hosts = _estate(db_session, test_project)

    def ips(q):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q, "limit": 50})
        assert r.status_code == 200, r.text
        return {row["ip_address"] for row in r.json()["items"]}

    assert ips("has:weak_tls") == {hosts[6].ip_address}
    assert ips("has:weak_auth") == {hosts[8].ip_address, hosts[10].ip_address}
    # Two-valued under NOT: every other host, the ones nothing observed included.
    assert ips("NOT has:weak_tls") == {h.ip_address for h in hosts} - {hosts[6].ip_address}


def test_two_observations_at_one_moment_the_later_row_decides(db_session, test_project):
    """The stated tie rule: equal timestamps → the higher row id (written
    last).  The loop kept whichever row the database returned last."""
    scan = models.Scan(project_id=test_project.id, filename="t.json", tool_name="httpx")
    db_session.add(scan)
    db_session.flush()
    h = Host(project_id=test_project.id, ip_address="10.78.0.1", state="up")
    db_session.add(h)
    db_session.flush()
    for weak in (True, False):  # the clean one is written last
        db_session.add(WebInterface(project_id=test_project.id, host_id=h.id, scan_id=scan.id, url="https://t/",
                                    source=f"httpx-{weak}", last_seen=T[1], tls_weak_protocol=weak))
        db_session.flush()
    db_session.commit()
    assert HC.weak_tls_host_ids(db_session, test_project.id) == set()


# --- A2: the Scans list pages its scans before aggregating --------------------

def test_the_scans_list_pages_then_counts_and_says_what_it_said(client, db_session, test_project):
    """Every sort, paged three at a time, lists the scans in the order the
    whole list has, with each scan's figures counted from its own history
    rows — the page is chosen first now, the figures must not change."""
    from app.db.models import HostScanHistory

    hosts = []
    for i in range(1, 9):
        h = Host(project_id=test_project.id, ip_address=f"10.79.0.{i}", state="up")
        db_session.add(h)
        hosts.append(h)
    db_session.flush()
    scans = []
    for n in range(7):  # scan n saw the first n hosts (scan 0 saw none)
        s = models.Scan(project_id=test_project.id, filename=f"s{n % 3}-{n}.xml", tool_name=("nmap", "nessus")[n % 2],
                        created_at=NOW - timedelta(hours=n % 4), start_time=None if n % 3 == 0 else T[n])
        db_session.add(s)
        db_session.flush()
        scans.append(s)
        for k, h in enumerate(hosts[:n]):
            db_session.add(HostScanHistory(host_id=h.id, scan_id=s.id, state_at_scan="up" if k % 2 else "down",
                                           host_created=k == n - 1))
    db_session.commit()
    # (hosts seen, of them up — every odd one —, created — the last one)
    expected = {s.id: (n, n // 2, 1 if n else 0) for n, s in enumerate(scans)}

    def page(sort_by, order, skip):
        r = client.get(f"/api/v1/projects/{test_project.id}/scans/",
                       params={"sort_by": sort_by, "sort_order": order, "skip": skip, "limit": 3})
        assert r.status_code == 200, r.text
        return r.json()

    for sort_by in ("created_at", "start_time", "filename", "tool_name", "total_hosts", "new_hosts"):
        for order in ("asc", "desc"):
            whole = client.get(f"/api/v1/projects/{test_project.id}/scans/",
                               params={"sort_by": sort_by, "sort_order": order, "limit": 500}).json()
            paged = page(sort_by, order, 0) + page(sort_by, order, 3) + page(sort_by, order, 6)
            assert [s["id"] for s in paged] == [s["id"] for s in whole], (sort_by, order)
            assert len(paged) == len(scans)
            for s in paged:
                assert (s["total_hosts"], s["up_hosts"], s["new_hosts"]) == expected[s["id"]], (sort_by, s)
    # The whole list's order, independently: newest upload first, id breaking ties.
    whole = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"limit": 500}).json()
    assert [s["id"] for s in whole] == [s.id for s in sorted(scans, key=lambda s: (s.created_at, s.id), reverse=True)]


# --- A7: the Scan page's Ports tab lists the scan's own observations ---------

def test_the_scan_ports_are_what_the_scan_recorded_and_as_many_as_it_says(client, db_session, test_project):
    import json

    from app.db.models import PortScanHistory

    scan = models.Scan(project_id=test_project.id, filename="p.xml", tool_name="nmap")
    later = models.Scan(project_id=test_project.id, filename="later.xml", tool_name="nmap")
    db_session.add_all([scan, later])
    db_session.flush()
    rows = []
    for i, ip in enumerate(["10.80.0.10", "10.80.0.9"]):
        h = Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        for number in (445, 22, 80):
            # Today's record says closed / ssh-today: the page must not.
            p = models.Port(host_id=h.id, port_number=number, protocol="tcp", state="closed",
                            service_name="today", service_product="today")
            db_session.add(p)
            db_session.flush()
            db_session.add(PortScanHistory(
                port_id=p.id, scan_id=scan.id, state_at_scan="open", service_name=f"svc{number}",
                service_info=json.dumps({"service_name": f"svc{number}", "service_product": "OpenSSH",
                                         "service_version": "9.6"}) if number == 22 else None,
            ))
            if number == 80 and i == 0:  # a port another scan saw too: not this scan's twice
                db_session.add(PortScanHistory(port_id=p.id, scan_id=later.id, state_at_scan="closed"))
            rows.append((ip, number))
    db_session.commit()

    def ports(**params):
        r = client.get(f"/api/v1/projects/{test_project.id}/scans/{scan.id}/ports", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    whole = ports(limit=100)
    summary = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"ids": str(scan.id)}).json()[0]
    assert whole["total"] == summary["total_ports"] == 6
    # Address order as an address (as the host snapshots: .9 before .10 —
    # review 2026-10-23b, INV-7; this read text order before), then port.
    assert [(r["ip_address"], r["port_number"]) for r in whole["items"]] == [
        ("10.80.0.9", 22), ("10.80.0.9", 80), ("10.80.0.9", 445),
        ("10.80.0.10", 22), ("10.80.0.10", 80), ("10.80.0.10", 445),
    ]
    assert {r["state_at_scan"] for r in whole["items"]} == {"open"}
    ssh = whole["items"][0]
    assert (ssh["service_name"], ssh["service_product"], ssh["service_version"]) == ("svc22", "OpenSSH", "9.6")
    assert whole["items"][1]["service_product"] is None
    # Paged: the pages make the whole.
    assert ports(skip=0, limit=4)["items"] + ports(skip=4, limit=4)["items"] == whole["items"]
    # Another project's scan is not found.
    from app.db.models_project import Project
    other = Project(name="other-t5", slug="other-t5")
    db_session.add(other)
    db_session.flush()
    foreign = models.Scan(project_id=other.id, filename="x.xml", tool_name="nmap")
    db_session.add(foreign)
    db_session.commit()
    assert client.get(f"/api/v1/projects/{test_project.id}/scans/{foreign.id}/ports").status_code == 404
