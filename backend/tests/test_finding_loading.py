"""Loading a Finding loads the finding — not its endpoints (review 2026-10-01 C2).

``Finding.hosts`` was ``lazy="selectin"``: every whole-entity Finding query
also fetched every endpoint row, and ``GET /findings`` then serialised all of
them — a page of widespread issues carried tens of thousands of endpoints
nobody looked at, each named one costing a query for its name.  The same
shape as ``Host`` before v2.393.0 (``test_host_loading.py``).

Now: the relationship is plain lazy and a path that reads endpoints names the
load; a LIST row carries the true ``host_count``, the per-state roll-up and a
preview of at most five endpoints, from two grouped statements for the page;
``GET /findings/{id}`` still returns every endpoint.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.db import models
from app.db.models_findings import Finding, FindingHost, FindingVulnerability
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.finding_service import ENDPOINT_PREVIEW, FindingService


@contextmanager
def selects(db):
    """The SELECT statements run inside the block (not the harness's SAVEPOINTs)."""
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", count)


def _findings(db, project, user, *, findings, endpoints, named=False, tag="a", net=70):
    """``findings`` findings, each on ``endpoints`` hosts (its own hosts).
    With ``named`` every endpoint is a named endpoint (a DNS name row).
    ``tag`` / ``net`` keep a second batch's names and addresses distinct."""
    out = []
    for f in range(net - 70, net - 70 + findings):
        finding = Finding(
            project_id=project.id, title=f"Issue {f}", severity="high", status="confirmed",
            source="manual", created_by_id=user.id, owner_id=user.id,
        )
        db.add(finding)
        db.flush()
        for e in range(endpoints):
            host = models.Host(project_id=project.id, ip_address=f"10.{70 + f}.{e // 250}.{e % 250 + 1}", state="up")
            db.add(host)
            db.flush()
            name_id = None
            if named:
                name = models.DNSName(project_id=project.id, fqdn=f"h{f}-{e}.{tag}.example.com")
                db.add(name)
                db.flush()
                name_id = name.id
            db.add(FindingHost(
                finding_id=finding.id, host_id=host.id, name_id=name_id,
                host_status="remediated" if e % 3 == 0 else "open",
            ))
        out.append(finding)
    db.commit()
    ids = [f.id for f in out]
    db.expire_all()   # the next read comes from the database, not this session's objects
    return ids


def test_querying_findings_runs_one_statement(db_session, test_project, test_user):
    """The guard: no relationship of Finding loads by itself."""
    _findings(db_session, test_project, test_user, findings=3, endpoints=4)
    pid = test_project.id
    db_session.expunge_all()
    with selects(db_session) as statements:
        rows = db_session.query(Finding).filter(Finding.project_id == pid).all()
    assert len(rows) == 3
    assert len(statements) == 1, statements


def test_a_list_row_carries_a_preview_and_the_true_count(client, db_session, test_project, test_user):
    [fid] = _findings(db_session, test_project, test_user, findings=1, endpoints=12, named=True)
    body = client.get(f"/api/v1/projects/{test_project.id}/findings").json()
    [row] = body["items"]
    assert row["id"] == fid
    assert row["host_count"] == 12                       # the true total …
    assert len(row["hosts"]) == ENDPOINT_PREVIEW == 5    # … beside a preview
    assert row["endpoint_status_counts"] == {"open": 8, "remediated": 4}   # over ALL endpoints
    first = row["hosts"][0]
    assert first["ip_address"] == "10.70.0.1" and first["fqdn"] == "h0-0.a.example.com"
    assert first["host_status"] == "remediated" and first["id"] and first["name_id"]
    # The preview is the first five endpoints in a stable order.
    assert [h["id"] for h in row["hosts"]] == sorted(h["id"] for h in row["hosts"])

    # The finding's own page still has every endpoint, with its name.
    detail = client.get(f"/api/v1/projects/{test_project.id}/findings/{fid}").json()
    assert detail["host_count"] == 12 and len(detail["hosts"]) == 12
    assert detail["endpoint_status_counts"] == {"open": 8, "remediated": 4}
    assert {h["fqdn"] for h in detail["hosts"]} == {f"h0-{e}.a.example.com" for e in range(12)}


def test_a_finding_with_no_endpoints_lists_cleanly(client, db_session, test_project, test_user):
    _findings(db_session, test_project, test_user, findings=1, endpoints=0)
    [row] = client.get(f"/api/v1/projects/{test_project.id}/findings").json()["items"]
    assert row["host_count"] == 0 and row["hosts"] == [] and row["endpoint_status_counts"] == {}


def test_the_host_count_sort_still_orders_by_the_true_count(client, db_session, test_project, test_user):
    small, = _findings(db_session, test_project, test_user, findings=1, endpoints=2)
    big = Finding(project_id=test_project.id, title="Everywhere", severity="low", status="open", source="manual")
    db_session.add(big)
    db_session.flush()
    for e in range(9):
        host = models.Host(project_id=test_project.id, ip_address=f"10.99.0.{e + 1}", state="up")
        db_session.add(host)
        db_session.flush()
        db_session.add(FindingHost(finding_id=big.id, host_id=host.id, host_status="open"))
    db_session.commit()
    url = f"/api/v1/projects/{test_project.id}/findings"
    desc = client.get(url, params={"sort": "host_count", "dir": "desc"}).json()["items"]
    assert [(r["id"], r["host_count"]) for r in desc] == [(big.id, 9), (small, 2)]
    asc = client.get(url, params={"sort": "host_count", "dir": "asc"}).json()["items"]
    assert [r["id"] for r in asc] == [small, big.id]


def _list_statements(client, db_session, project):
    db_session.expire_all()
    with selects(db_session) as statements:
        response = client.get(f"/api/v1/projects/{project.id}/findings")
    assert response.status_code == 200, response.text
    return response.json(), statements


def test_the_list_is_a_fixed_number_of_statements(client, db_session, test_project, test_user):
    """Not one per row, not one per endpoint, not one per named endpoint: the
    count is the same for 2 findings of 3 endpoints and 12 findings of 30."""
    _findings(db_session, test_project, test_user, findings=2, endpoints=3, named=True)
    few, base = _list_statements(client, db_session, test_project)
    assert len(few["items"]) == 2

    for finding in db_session.query(Finding).all():
        db_session.delete(finding)
    db_session.commit()
    _findings(db_session, test_project, test_user, findings=12, endpoints=30, named=True, tag="b", net=100)
    many, statements = _list_statements(client, db_session, test_project)
    assert len(many["items"]) == 12 and all(r["host_count"] == 30 for r in many["items"])
    assert len(statements) == len(base), "\n".join(statements)
    # And none of them brings back whole endpoint rows for the page.
    assert len(statements) <= 12, "\n".join(statements)


def test_endpoint_summaries_are_two_statements_for_any_page(db_session, test_project, test_user):
    ids = _findings(db_session, test_project, test_user, findings=6, endpoints=8, named=True)
    with selects(db_session) as statements:
        out = FindingService(db_session).endpoint_summaries(ids)
    assert len(statements) == 2, statements
    assert set(out) == set(ids)
    assert all(v["host_count"] == 8 and len(v["preview"]) == 5 for v in out.values())
    assert FindingService(db_session).endpoint_summaries([]) == {}


def test_a_host_filter_puts_that_hosts_endpoints_first_in_the_preview(client, db_session, test_project, test_user):
    """``GET /findings?host_id=X``: the host's findings card reads X's own
    endpoint state from the list.  X is the finding's LAST endpoint here, so
    a first-five-by-id preview leaves it out."""
    [fid] = _findings(db_session, test_project, test_user, findings=1, endpoints=12)
    rows = db_session.query(FindingHost).filter_by(finding_id=fid).order_by(FindingHost.id).all()
    last = rows[-1]
    # A second (named) endpoint row on the same host: both lead.
    name = models.DNSName(project_id=test_project.id, fqdn="vhost.example.com")
    db_session.add(name)
    db_session.flush()
    named = FindingHost(finding_id=fid, host_id=last.host_id, name_id=name.id, host_status="retest")
    db_session.add(named)
    db_session.commit()
    ordered = [r.id for r in rows]
    last_id, last_host, named_id = last.id, last.host_id, named.id

    url = f"/api/v1/projects/{test_project.id}/findings"
    db_session.expire_all()
    with selects(db_session) as with_host:
        [row] = client.get(url, params={"host_id": last_host}).json()["items"]
    assert [h["id"] for h in row["hosts"]] == [last_id, named_id] + ordered[:3]
    assert all(h["host_id"] == last_host for h in row["hosts"][:2])
    assert row["hosts"][1]["fqdn"] == "vhost.example.com" and row["hosts"][1]["host_status"] == "retest"
    assert row["host_count"] == 13 and len(row["hosts"]) == ENDPOINT_PREVIEW

    # Without the filter the preview is the first five by id, as before …
    db_session.expire_all()
    with selects(db_session) as plain:
        [row] = client.get(url).json()["items"]
    assert [h["id"] for h in row["hosts"]] == ordered[:5]
    # … and the filter adds no statement of its own beyond the host filter's.
    assert len(with_host) <= len(plain) + 1, "\n".join(with_host)

    # The service: still two statements whatever the page holds.
    with selects(db_session) as statements:
        out = FindingService(db_session).endpoint_summaries([fid], first_host_id=last_host)
    assert len(statements) == 2, statements
    assert [r.id for r in out[fid]["preview"]] == [last_id, named_id] + ordered[:3]


# --- the agent's finding detail -------------------------------------------

def _agent(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def test_agent_finding_detail_caps_endpoints_and_scanner_rows_in_sql(client, db_session, test_project, test_user):
    """It loaded every endpoint as three entities and sliced the list in
    Python, and returned every scanner row.  Both are capped at 100 with the
    totals beside them."""
    scan = models.Scan(project_id=test_project.id, filename="n.nessus", tool_name="nessus")
    finding = Finding(project_id=test_project.id, title="Widespread", severity="high", status="confirmed",
                      source="scanner", created_by_id=test_user.id)
    db_session.add_all([scan, finding])
    db_session.flush()
    for e in range(130):
        host = models.Host(project_id=test_project.id, ip_address=f"10.88.{e // 250}.{e % 250 + 1}", state="up")
        db_session.add(host)
        db_session.flush()
        db_session.add(FindingHost(finding_id=finding.id, host_id=host.id,
                                   host_status="remediated" if e < 10 else "open"))
        vuln = Vulnerability(host_id=host.id, scan_id=scan.id, title="Widespread", plugin_id="1",
                             severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS)
        db_session.add(vuln)
        db_session.flush()
        db_session.add(FindingVulnerability(finding_id=finding.id, vuln_id=vuln.id))
    db_session.commit()
    fid = finding.id
    key = _agent(client, test_project)
    db_session.expire_all()

    with selects(db_session) as statements:
        r = client.get(f"/api/v1/agent/assist/findings/{fid}", headers=key)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["hosts"]) == 100 and body["hosts_truncated"] is True
    assert body["endpoint_count"] == 130 and body["host_count"] == 130
    assert body["endpoint_status_counts"] == {"open": 120, "remediated": 10}
    assert len(body["scanner_evidence"]) == 100
    assert body["scanner_evidence_total"] == 130 and body["scanner_evidence_truncated"] is True
    assert {"vuln_id", "host_id", "source", "plugin_id", "title", "severity", "cve_id"} <= set(body["scanner_evidence"][0])
    # The endpoint read is limited where it is asked, not sliced afterwards.
    endpoint_reads = [s for s in statements if "finding_hosts" in s and "hosts_v2" in s]
    assert endpoint_reads and all("LIMIT" in s.upper() for s in endpoint_reads), endpoint_reads


@pytest.mark.parametrize("path", ["", "?status=active", "?sort=host_count&dir=desc"])
def test_agent_findings_list_still_counts_distinct_addresses(client, db_session, test_project, test_user, path):
    """The agents' list reads every endpoint of its rows for a distinct-address
    count, so it names the load now that the relationship is lazy."""
    _findings(db_session, test_project, test_user, findings=2, endpoints=12)
    key = _agent(client, test_project)
    body = client.get(f"/api/v1/agent/assist/findings{path}", headers=key).json()
    assert [f["host_count"] for f in body["findings"]] == [12, 12]
    assert all(len(f["hosts"]) == 10 and f["hosts_truncated"] for f in body["findings"])
