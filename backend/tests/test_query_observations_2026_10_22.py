"""Query and Scanner observations, backlog group D (review 2026-10-16, Q / O).

* Q1 — ``observations`` carries ``judged`` (the Observations page's ONE rule,
  ``observation_judged_on_host``) and ``finding``; ``findings`` carries
  ``hosts`` (the Findings list's own count).
* Q2 — ``hosts`` carries ``site`` (the Hosts row's inherited site,
  ``resolve_host_locations``), ``scope`` (the ``scope:`` states) and
  ``open_ports`` (``has:open_ports`` counted).
* Q5 — a listing past its cap is paged by key (``after`` / ``next_after``);
  ``total`` stays the whole answer and the pages put together are the
  listing.
* Q7 — a ``hosts`` listing with no ``where`` says which Hosts query lists it.
* Q9 — the builder's reading of a query is the server's (``/query/parse``).
* O1 / O2 / O3 / O5 — the observations list's check ids, CVSS, ports, last
  seen and order; the judged filter's four values; one scanner's words.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest

from app.api.v1.endpoints.scanner_observations import IssueJudged, IssueSort
from app.db import models
from app.db.models import Host, HostSubnetMapping, Port, Scope, Subnet
from app.db.models_findings import Finding, FindingHost
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services import query_service
from app.services import scanner_observation_service as svc
from app.services.host_query_predicates import SCOPE_COVERAGE_STATES
from app.services.query_datasets import ScopeCoverage
from app.services.query_language import QueryError
from app.services.subnet_insight_service import resolve_host_locations
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="the Query page compiles for Postgres")

NOW = datetime.now(timezone.utc)


def _url(project, tail):
    return f"/api/v1/projects/{project.id}{tail}"


@pytest.fixture
def estate(db_session, test_project):
    """14 hosts.  Scope: 10.40.0.0/16 site "DC", inside it 10.40.1.0/24 with
    no site (inherits DC) and 10.40.2.0/24 site "Lab"; hosts .0.x are out of
    every subnet.  Ports: every host 22 open; hosts with an even number 80
    open too, and 25 closed.  Observations: "SMB signing" (medium, nessus
    plugin 57608, port 445) on hosts 0–5, the same issue from openvas on host 0;
    "OpenSSH RCE" (critical, CVE, CVSS 9.8, plugin 201194, port 22) on hosts
    0–1; "TLS 1.0" (low, CVSS 4.3, no port) on host 2.  A scanner finding of
    SMB signing covers hosts 0 and 1; one of TLS 1.0 covers host 2."""
    scan = models.Scan(project_id=test_project.id, filename="d.nessus", tool_name="nessus")
    scope = Scope(project_id=test_project.id)
    db_session.add_all([scan, scope])
    db_session.flush()
    dc = Subnet(scope_id=scope.id, cidr="10.40.0.0/16", site="DC")
    inner = Subnet(scope_id=scope.id, cidr="10.40.1.0/24")
    lab = Subnet(scope_id=scope.id, cidr="10.40.2.0/24", site="Lab")
    db_session.add_all([dc, inner, lab])
    db_session.flush()
    hosts = []
    for i in range(14):
        third = (1, 2, 9)[i % 3] if i < 12 else 0
        ip = f"10.40.{third}.{i + 10}" if third else f"192.0.2.{i}"
        h = Host(project_id=test_project.id, ip_address=ip, state="up",
                 hostname=None if i % 4 == 0 else f"d{i:02d}.lab", first_seen=NOW, last_seen=NOW)
        db_session.add(h)
        db_session.flush()
        hosts.append(h)
        if third:
            db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=dc.id))
            if third == 1:
                db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=inner.id))
            if third == 2:
                db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=lab.id))
        db_session.add(Port(host_id=h.id, port_number=22, protocol="tcp", state="open", service_name="ssh"))
        if i % 2 == 0:
            db_session.add(Port(host_id=h.id, port_number=80, protocol="tcp", state="open", service_name="http"))
            db_session.add(Port(host_id=h.id, port_number=25, protocol="tcp", state="closed", service_name="smtp"))
    db_session.flush()
    for i, h in enumerate(hosts[:6]):
        p = Port(host_id=h.id, port_number=445, protocol="tcp", state="open", service_name="microsoft-ds")
        db_session.add(p)
        db_session.flush()
        db_session.add(Vulnerability(host_id=h.id, port_id=p.id, scan_id=scan.id, title="SMB signing not required",
                                     severity=VulnerabilitySeverity.MEDIUM, source="nessus", plugin_id="57608",
                                     cvss_score=5.3, last_seen=(NOW - timedelta(days=i)).replace(tzinfo=None)))
    db_session.add(Vulnerability(host_id=hosts[0].id, scan_id=scan.id, title="SMB signing not required",
                                 severity=VulnerabilitySeverity.MEDIUM, source="openvas",
                                 plugin_id="1.3.6.1.4.1.25623.1.0.102011", description="OpenVAS words"))
    for h in hosts[:2]:
        ssh = next(p for p in db_session.query(Port).filter(Port.host_id == h.id, Port.port_number == 22))
        db_session.add(Vulnerability(host_id=h.id, port_id=ssh.id, scan_id=scan.id, title="OpenSSH RCE",
                                     cve_id="CVE-2024-6387", severity=VulnerabilitySeverity.CRITICAL,
                                     source="nessus", plugin_id="201194", cvss_score=9.8, exploitable=True,
                                     description="Nessus words"))
    db_session.add(Vulnerability(host_id=hosts[2].id, scan_id=scan.id, title="TLS 1.0", source="nessus",
                                 severity=VulnerabilitySeverity.LOW, plugin_id="104743", cvss_score=4.3))
    db_session.commit()
    smb_key = "title:smb signing not required"
    tls_key = db_session.query(Vulnerability.issue_key).filter(Vulnerability.title == "TLS 1.0").scalar()
    for key, covered in ((smb_key, hosts[:2]), (tls_key, hosts[2:3])):
        f = Finding(project_id=test_project.id, title=key, severity="medium", status="confirmed",
                    source="scanner", dedup_key=key)
        db_session.add(f)
        db_session.flush()
        for h in covered:
            db_session.add(FindingHost(finding_id=f.id, host_id=h.id, host_status="open"))
    db_session.commit()
    return {"hosts": hosts, "smb": smb_key, "tls": tls_key}


def _run(db, project, user, q, **kw):
    return query_service.run(db, project.id, user, q, **kw)


# ---------------------------------------------------------------------------
# Q1
# ---------------------------------------------------------------------------

def test_judged_is_the_observation_lists_rule(db_session, test_project, test_user, estate):
    """Per issue, the hosts the column calls judged are the list's
    ``judged_host_count`` — the same rule, so "unjudged criticals by host"
    is askable and agrees with the page."""
    page = svc.list_issues(db_session, test_project.id, judged="any", limit=200)
    assert {i.issue_key for i in page.items} >= {estate["smb"], estate["tls"]}
    for issue in page.items:
        answer = _run(db_session, test_project, test_user,
                      f'observations | where issue="{issue.issue_key}" | stats dc(host) as hosts by judged')
        by = {row[0]: row[1] for row in answer["rows"]}
        assert by.get(True, 0) == issue.judged_host_count, issue.issue_key
        assert by.get(True, 0) + by.get(False, 0) == issue.host_count


def test_finding_names_the_issues_finding_and_judged_this_host(db_session, test_project, test_user, estate):
    smb_finding = db_session.query(Finding.id).filter(Finding.dedup_key == estate["smb"]).scalar()
    rows = _run(db_session, test_project, test_user,
                f'observations | where issue="{estate["smb"]}" | table ip, judged, finding')["rows"]
    assert {r[2] for r in rows} == {smb_finding}          # every row of the issue names it
    judged_ips = {r[0] for r in rows if r[1]}
    assert judged_ips == {h.ip_address for h in estate["hosts"][:2]}
    ssh = _run(db_session, test_project, test_user, 'observations | where cve="CVE-2024-6387" | table finding')
    assert {r[0] for r in ssh["rows"]} == {None}


def test_a_group_by_judged_opens_its_exact_rows(db_session, test_project, test_user, estate):
    answer = _run(db_session, test_project, test_user, "observations | stats count by judged, severity")
    for row, drill in zip(answer["rows"], answer["drilldowns"]):
        assert _run(db_session, test_project, test_user, drill)["total"] == row[2]


def test_findings_hosts_is_the_lists_host_count(db_session, test_project, test_user, estate):
    rows = _run(db_session, test_project, test_user, "findings | table issue, hosts")["rows"]
    assert {r[0]: r[1] for r in rows} == {estate["smb"]: 2, estate["tls"]: 1}


# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------

def test_site_is_the_hosts_rows_inherited_site(db_session, test_project, test_user, estate):
    rows = _run(db_session, test_project, test_user, "hosts | table host, site")["rows"]
    locations = resolve_host_locations(db_session, test_project.id, host_ids=[h.id for h in estate["hosts"]])
    assert {r[0]: r[1] for r in rows} == {
        h.id: (locations.get(h.id) or {}).get("site") for h in estate["hosts"]
    }
    # The unlabelled /24 inherits DC; the out-of-scope hosts have none.
    assert {r[1] for r in rows} == {"DC", "Lab", None}


def test_hosts_by_site_and_scope_open_their_exact_lists(db_session, test_project, test_user, estate):
    answer = _run(db_session, test_project, test_user, "hosts | stats count by site, scope")
    assert sum(r[2] for r in answer["rows"]) == len(estate["hosts"])
    for row, drill in zip(answer["rows"], answer["drilldowns"]):
        assert _run(db_session, test_project, test_user, drill)["total"] == row[2]


@pytest.mark.parametrize("state", SCOPE_COVERAGE_STATES)
def test_scope_column_is_the_scope_search_term(db_session, test_project, test_user, estate, state):
    by_column = _run(db_session, test_project, test_user, f'hosts | where scope="{state}" | table host')
    by_term = _run(db_session, test_project, test_user, f"hosts scope:{state} | table host")
    assert sorted(r[0] for r in by_column["rows"]) == sorted(r[0] for r in by_term["rows"])
    assert {m.value for m in ScopeCoverage} == set(SCOPE_COVERAGE_STATES)


def test_open_ports_counts_has_open_ports(db_session, test_project, test_user, estate):
    rows = _run(db_session, test_project, test_user, "hosts | table host, open_ports")["rows"]
    expected = {
        h.id: db_session.query(Port).filter(Port.host_id == h.id, Port.state == "open").count()
        for h in estate["hosts"]
    }
    assert {r[0]: r[1] for r in rows} == expected
    with_open = _run(db_session, test_project, test_user, "hosts has:open_ports")["total"]
    assert _run(db_session, test_project, test_user, "hosts | where open_ports > 0")["total"] == with_open


# ---------------------------------------------------------------------------
# Q5 — key-set pages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "hosts",                                   # by address
    "hosts | sort -hostname",                  # descending, with nulls (last)
    "hosts | sort site, -open_ports",          # a subquery key with nulls, then a count
    "observations",                            # -severity (a rank), then the address
    "observations | sort -last_seen, source",  # a datetime with nulls, then an enum
    "ports | sort -state, port",
])
def test_the_pages_put_together_are_the_listing(db_session, test_project, test_user, estate, q):
    whole = _run(db_session, test_project, test_user, q, row_cap=1000)
    assert whole["next_after"] is None and whole["start"] == 0
    rows, after, starts = [], None, []
    for _ in range(100):
        page = query_service.run(db_session, test_project.id, test_user, q, row_cap=4, after=after)
        assert page["total"] == whole["total"]
        starts.append(page["start"])
        rows += page["rows"]
        after = page["next_after"]
        if after is None:
            break
    assert rows == whole["rows"]
    assert starts == list(range(0, whole["total"], 4))


def test_a_listing_with_head_has_no_next_page(db_session, test_project, test_user, estate):
    assert _run(db_session, test_project, test_user, "hosts | head 3", row_cap=2)["next_after"] is None
    first = _run(db_session, test_project, test_user, "hosts", row_cap=2)
    with pytest.raises(QueryError, match="without `head`"):
        _run(db_session, test_project, test_user, "hosts | head 3", after=first["next_after"])
    with pytest.raises(QueryError, match="pages a listing"):
        _run(db_session, test_project, test_user, "hosts | stats count", after=first["next_after"])
    # A place from another listing's order, or no place at all, is refused.
    with pytest.raises(QueryError, match="not a place"):
        _run(db_session, test_project, test_user, "hosts | sort -hostname, ip", after=first["next_after"])
    with pytest.raises(QueryError, match="not a place"):
        _run(db_session, test_project, test_user, "hosts", after="garbage!")


def test_after_on_both_doors(client, db_session, test_project, estate):
    first = client.get(_url(test_project, "/query"), params={"q": "hosts"}).json()
    assert first["total"] == 14 and first["start"] == 0 and first["next_after"] is None
    refused = client.get(_url(test_project, "/query"), params={"q": "hosts", "after": "nope"})
    assert refused.status_code == 400 and "not a place" in refused.json()["detail"]
    headers = {"X-API-Key": client.post(_url(test_project, "/assist/start"), json={}).json()["api_key"]}
    agent = client.get("/api/v1/agent/assist/query", params={"q": "hosts", "after": "nope"}, headers=headers)
    assert agent.status_code == 400


def test_a_page_turn_is_not_a_second_run_in_the_history(client, db_session, test_project, test_user, estate):
    from app.db.models import QueryHistory
    page = query_service.run(db_session, test_project.id, test_user, "hosts", row_cap=4)
    client.get(_url(test_project, "/query"), params={"q": "hosts"})
    client.get(_url(test_project, "/query"), params={"q": "hosts", "after": page["next_after"]})
    entry = db_session.query(QueryHistory).filter(QueryHistory.q == "hosts").one()
    db_session.refresh(entry)
    assert entry.run_count == 1


# ---------------------------------------------------------------------------
# Q7 — the Hosts pivot
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q, hosts_q", [
    ("hosts", ""),
    ("hosts port:22 site:DC", "port:22 site:DC"),
    ("hosts port:22 | search has:open_ports | sort -hostname | head 3", "(port:22) AND (has:open_ports)"),
    ("hosts port:22 | where open_ports > 1", None),
    ("hosts | stats count by site", None),
    ("ports port:22", None),
])
def test_a_hosts_listing_names_its_hosts_query(db_session, test_project, test_user, estate, q, hosts_q):
    answer = _run(db_session, test_project, test_user, q)
    assert answer["hosts_q"] == hosts_q
    if hosts_q is not None and "head" not in q:
        from app.services import host_query_dsl as dsl
        listed = {h.id for h in db_session.query(Host).filter(
            Host.project_id == test_project.id,
            dsl.evaluate(dsl.parse_query(hosts_q), dsl.BuildCtx(db=db_session, current_user=test_user,
                                                                project_id=test_project.id)),
        )} if hosts_q else {h.id for h in estate["hosts"]}
        assert answer["total"] == len(listed)


# ---------------------------------------------------------------------------
# Q9 — the builder reads the server's parse
# ---------------------------------------------------------------------------

def test_the_builder_shape_of_a_query(client, test_project):
    def shape(q):
        r = client.get(_url(test_project, "/query/parse"), params={"q": q})
        assert r.status_code == 200, r.text
        return r.json()

    got = shape('observations has:critical | where severity="high" AND isnull(port) AND like(title, "%tls%") '
                '| stats dc(host) as hosts by title | sort -hosts | head 20')
    assert got["shape"] == {
        "dataset": "observations", "terms": "has:critical",
        "conditions": [{"column": "severity", "op": "=", "value": "high"},
                       {"column": "port", "op": "isnull", "value": ""},
                       {"column": "title", "op": "like", "value": "%tls%"}],
        "by": ["title"], "measure": {"fn": "dc", "column": "host", "as": "hosts"},
        "most_first": True, "head": 20,
    }
    top = shape("ports | top limit=5 product, version")["shape"]
    assert (top["by"], top["measure"]["fn"], top["head"]) == (["product", "version"], "top", 5)
    values = shape("hosts | stats values(ip) by site")["shape"]
    assert values["measure"] == {"fn": "values", "column": "ip", "as": None}
    assert shape("hosts")["shape"]["head"] == 0
    for q, why in [('hosts | where os IN ("a")', "IN"), ('hosts | where os="a" OR os="b"', "OR"),
                   ("hosts | sort ip", "order"), ("hosts | stats count by os | where count > 2", "after")]:
        got = shape(q)
        assert got["shape"] is None and why in got["reason"]
    bad = client.get(_url(test_project, "/query/parse"), params={"q": "hosts | where nope=1"})
    assert bad.status_code == 400 and bad.json()["position"] == 14


# ---------------------------------------------------------------------------
# Observations: O1 / O2 / O3 / O5
# ---------------------------------------------------------------------------

def _obs(client, project, **params):
    r = client.get(_url(project, "/scanner-observations"), params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_row_names_its_check_ids_and_search_finds_them(client, test_project, estate):
    rows = {r["issue_key"]: r for r in _obs(client, test_project, judged="any")["items"]}
    assert rows[estate["smb"]]["plugin_ids"] == ["1.3.6.1.4.1.25623.1.0.102011", "57608"]
    found = _obs(client, test_project, judged="any", search="201194")["items"]
    assert [r["title"] for r in found] == ["OpenSSH RCE"]


def test_a_row_carries_cvss_ports_and_last_seen(client, test_project, estate):
    rows = {r["issue_key"]: r for r in _obs(client, test_project, judged="any")["items"]}
    smb = rows[estate["smb"]]
    assert (smb["cvss_score"], smb["ports"], smb["port_count"]) == (5.3, [445], 1)
    assert smb["last_seen"].startswith(NOW.strftime("%Y-%m-%d"))
    tls = rows[estate["tls"]]
    assert (tls["ports"], tls["port_count"]) == ([], 0)
    by_cvss = [r["cvss_score"] for r in _obs(client, test_project, judged="any", sort="cvss")["items"]]
    assert by_cvss == [9.8, 5.3, 4.3]


def test_a_port_list_is_cut_and_says_how_many(db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="p.nessus", tool_name="nessus")
    db_session.add(scan)
    h = Host(project_id=test_project.id, ip_address="10.41.0.1", state="up")
    db_session.add(h)
    db_session.flush()
    for n in range(1, 13):
        p = Port(host_id=h.id, port_number=1000 - n, protocol="tcp", state="open")
        db_session.add(p)
        db_session.flush()
        db_session.add(Vulnerability(host_id=h.id, port_id=p.id, scan_id=scan.id, title="Service detection",
                                     severity=VulnerabilitySeverity.INFO, source="nessus"))
    db_session.commit()
    row = svc.list_issues(db_session, test_project.id, judged="any").items[0]
    assert (row.ports, row.port_count) == (sorted(1000 - n for n in range(1, 13))[:svc.PORT_CAP], 12)


def test_the_judged_filter_has_four_values(client, test_project, estate):
    keys = {j: {r["issue_key"] for r in _obs(client, test_project, judged=j)["items"]} for j in svc.JUDGED_STATES}
    ssh = next(iter(keys["any"] - {estate["smb"], estate["tls"]}))
    assert keys["none"] == {ssh}                       # nobody judged any host
    assert keys["all"] == {estate["tls"]}              # judged on its one host
    assert keys["waiting"] == {ssh, estate["smb"]}     # a host still waits
    assert keys["any"] == keys["waiting"] | keys["all"]
    # The default and the older spelling.
    assert {r["issue_key"] for r in _obs(client, test_project)["items"]} == keys["waiting"]
    assert {r["issue_key"] for r in _obs(client, test_project, include_judged=True)["items"]} == keys["any"]
    conflict = client.get(_url(test_project, "/scanner-observations"), params={"judged": "none", "include_judged": True})
    assert conflict.status_code == 422
    assert client.get(_url(test_project, "/scanner-observations"), params={"judged": "most"}).status_code == 422


def test_the_routes_enums_are_the_services(client):
    assert set(get_args(IssueSort)) == set(svc.ISSUE_SORTS)
    assert set(get_args(IssueJudged)) == set(svc.JUDGED_STATES)


def test_one_scanners_words(client, test_project, estate):
    url = _url(test_project, "/scanner-observations/detail")
    default = client.get(url, params={"issue_key": estate["smb"]}).json()
    assert default["source"] == "openvas"              # the one with a description
    nessus = client.get(url, params={"issue_key": estate["smb"], "source": "nessus"}).json()
    assert (nessus["source"], nessus["plugin_id"]) == ("nessus", "57608")
    assert client.get(url, params={"issue_key": estate["smb"], "source": "nmap"}).status_code == 404
    assert client.get(url, params={"issue_key": estate["smb"], "source": "bogus"}).status_code == 422
