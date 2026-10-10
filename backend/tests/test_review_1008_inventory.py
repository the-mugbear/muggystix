"""Review 2026-10-08, inventory read paths.

One test (or group) per defect; each fails on the code as it stood:

* NOT over a column that may be NULL dropped the host from the negated list;
* a discrete filter value that could not be understood was dropped, and the
  filter with it — the answer was the whole project (and its ids, for "select
  all matching");
* ``NOT certorg:`` listed nothing once any matching web interface had no host;
* the agents' host filter was a second assembly that had come to mean
  something else than the Hosts page's;
* "Changed since review" was counted and paged in Python from every review;
* the import history stopped at 500 batches;
* a scans search treated ``_`` and ``%`` as wildcards.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.db import models
from app.db.models import FollowStatus
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services import operations_read_service
from app.services.host_query import parse_id_list, parse_port_list
from app.services.host_query_dsl import BuildCtx, evaluate, parse_query
from app.services.host_serialization import serialize_host_base


def _base(pid):
    return f"/api/v1/projects/{pid}"


def _host(db, pid, ip, **extra):
    host = models.Host(project_id=pid, ip_address=ip, state="up", **extra)
    db.add(host)
    db.flush()
    return host


def _scan(db, pid, name="s.xml", **extra):
    scan = models.Scan(project_id=pid, filename=name, tool_name="nmap", scan_type="nmap", **extra)
    db.add(scan)
    db.flush()
    return scan


def _port(db, host, number, **extra):
    port = models.Port(host_id=host.id, port_number=number, protocol="tcp", state="open", **extra)
    db.add(port)
    db.flush()
    return port


def _vuln(db, host, scan, severity, title="t", **extra):
    db.add(Vulnerability(
        host_id=host.id, scan_id=scan.id, plugin_id=f"p-{host.id}-{title}", title=title,
        severity=severity, source=VulnerabilitySource.NESSUS, **extra,
    ))
    db.flush()


def _ips(client, pid, **params):
    r = client.get(f"{_base(pid)}/hosts/", params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return {item["ip_address"] for item in r.json()["items"]}


def _agent(client, pid):
    r = client.post(f"{_base(pid)}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _agent_ips(client, headers, **params):
    r = client.get("/api/v1/agent/assist/hosts", headers=headers, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["items"])
    return {item["ip_address"] for item in body["items"]}


# ---------------------------------------------------------------------------
# A — NOT over a column that may be NULL
# ---------------------------------------------------------------------------

@pytest.fixture
def null_estate(db_session, test_project):
    """A Windows host named dc01, a Linux host named web01, and a host with
    no name, no OS and no ports at all."""
    pid = test_project.id
    _host(db_session, pid, "10.60.0.1", hostname="dc01.corp", os_name="Windows Server 2019", os_family="Windows")
    _host(db_session, pid, "10.60.0.2", hostname="web01.corp", os_name="Ubuntu 22.04", os_family="Linux")
    _host(db_session, pid, "10.60.0.3")
    db_session.commit()
    return pid


@pytest.mark.parametrize("q,expected", [
    ("NOT os:windows", {"10.60.0.2", "10.60.0.3"}),
    ("NOT hostname:dc01", {"10.60.0.2", "10.60.0.3"}),
    ("NOT dc01", {"10.60.0.2", "10.60.0.3"}),                    # a bare term
    ("NOT windows", {"10.60.0.2", "10.60.0.3"}),
    ("NOT NOT os:windows", {"10.60.0.1"}),
    ("NOT (NOT os:windows)", {"10.60.0.1"}),
    ("hostname:web01 OR NOT os:linux", {"10.60.0.1", "10.60.0.2", "10.60.0.3"}),
    ("NOT (os:windows OR hostname:web01)", {"10.60.0.3"}),
    ("NOT os:windows AND NOT hostname:web01", {"10.60.0.3"}),
    ("NOT (os:windows AND hostname:dc01)", {"10.60.0.2", "10.60.0.3"}),
])
def test_not_keeps_a_host_the_child_knows_nothing_about(client, null_estate, q, expected):
    assert _ips(client, null_estate, q=q) == expected


def test_a_query_and_its_negation_partition_the_project(client, null_estate):
    everyone = _ips(client, null_estate)
    for q in ("os:windows", "hostname:corp", "state:up", "has:smb_unsigned", "dc01", "os:linux OR hostname:dc01"):
        matched, rest = _ips(client, null_estate, q=q), _ips(client, null_estate, q=f"NOT ({q})")
        assert matched | rest == everyone and not (matched & rest), q


def test_not_exists_is_not_wrapped(db_session, test_user, test_project):
    """An EXISTS cannot be NULL, so its negation stays ``NOT EXISTS`` — the
    anti-join Postgres plans — and only a column test is read as false when
    unknown."""
    ctx = BuildCtx(db_session, test_user, test_project.id)
    dialect = db_session.get_bind().dialect

    def sql(q):
        return str(evaluate(parse_query(q), ctx).compile(dialect=dialect))

    for q in ("NOT port:22", "NOT has:critical", "NOT (port:22 OR has:notes)", "NOT follow:none"):
        assert "coalesce" not in sql(q).lower(), q
    assert "NOT coalesce(" in sql("NOT os:windows")
    assert "NOT coalesce(" in sql("NOT (port:22 OR os:windows)")


# ---------------------------------------------------------------------------
# B — a discrete value that cannot be understood is refused, naming it
# ---------------------------------------------------------------------------

@pytest.fixture
def filter_estate(db_session, test_project):
    pid = test_project.id
    tag = models.HostTag(project_id=pid, name="prod")
    db_session.add(tag)
    db_session.flush()
    tagged = _host(db_session, pid, "10.61.0.1")
    db_session.add(models.HostTagAssignment(host_id=tagged.id, tag_id=tag.id))
    _port(db_session, tagged, 22, service_name="ssh")
    _host(db_session, pid, "10.61.0.2")
    _port(db_session, _host(db_session, pid, "10.61.0.3"), 80, service_name="http")
    db_session.commit()
    return pid, tag.id


@pytest.mark.parametrize("route", ["/hosts/", "/hosts/ids", "/hosts/filters/data"])
@pytest.mark.parametrize("params,named", [
    ({"tags": "prod"}, "prod"),
    ({"tags": "1,prod"}, "prod"),
    ({"tags": ","}, "tags"),
    ({"subnet_labels": "dmz"}, "dmz"),
    ({"ports": "ssh"}, "ssh"),
    ({"ports": "22,80-90"}, "80-90"),
    ({"ports": "70000"}, "70000"),
    ({"ports": "²"}, "²"),
    ({"ports": ","}, "ports"),
])
def test_an_unusable_discrete_value_is_refused_not_dropped(client, filter_estate, route, params, named):
    pid, _tag = filter_estate
    r = client.get(f"{_base(pid)}{route}", params=params)
    assert r.status_code == 422, r.text
    assert named in r.json()["detail"]


def test_usable_discrete_values_still_filter(client, filter_estate):
    pid, tag_id = filter_estate
    assert _ips(client, pid, tags=str(tag_id)) == {"10.61.0.1"}
    assert _ips(client, pid, ports="22, 80") == {"10.61.0.1", "10.61.0.3"}
    ids = client.get(f"{_base(pid)}/hosts/ids", params={"tags": str(tag_id)}).json()
    assert ids["total"] == 1 and len(ids["ids"]) == 1


def test_no_open_ports_is_applied_together_with_a_port_filter(client, db_session, test_project):
    """``has_open_ports=false`` used to make the other port filters vanish:
    ``ports=22&port_states=closed&has_open_ports=false`` listed every host
    with no open port, whatever its port 22."""
    pid = test_project.id

    def host(ip, *ports):
        h = _host(db_session, pid, ip)
        for number, state in ports:
            db_session.add(models.Port(host_id=h.id, port_number=number, protocol="tcp", state=state))
        return h

    host("10.62.0.1", (22, "closed"))
    host("10.62.0.2", (80, "closed"))
    host("10.62.0.3", (22, "open"))
    host("10.62.0.4", (22, "closed"), (443, "open"))
    db_session.commit()

    assert _ips(client, pid, has_open_ports="false") == {"10.62.0.1", "10.62.0.2"}
    assert _ips(client, pid, has_open_ports="false", ports="22", port_states="closed") == {"10.62.0.1"}
    # A port filter with no state means an OPEN port, which no such host has.
    assert _ips(client, pid, has_open_ports="false", ports="22") == set()


@pytest.mark.parametrize("term", ["²", "٣", "99999999999999999999"])
def test_a_search_that_only_looks_like_a_number_is_a_search(client, filter_estate, term):
    """``"²".isdigit()`` is true and ``int("²")`` raises: the search box
    answered 500.  A number too large to be a port matches no port either."""
    pid, _tag = filter_estate
    assert _ips(client, pid, search=term) == set()
    r = client.get(f"{_base(pid)}/hosts/", params={"q": f"port:{term}"})
    assert r.status_code == 400, r.text


def test_the_parsers_name_what_they_refuse():
    from fastapi import HTTPException

    assert parse_port_list("22, 80 ,443") == [22, 80, 443]
    assert parse_id_list("tags", "3, 7") == [3, 7]
    for call in (lambda: parse_port_list("22,smb"), lambda: parse_id_list("tags", "3,x"),
                 lambda: parse_id_list("tags", str(2**31))):
        with pytest.raises(HTTPException) as refused:
            call()
        assert refused.value.status_code == 422


def test_select_all_matching_returns_the_same_ids_every_time(client, db_session, test_project, monkeypatch):
    """A capped answer is the first ids in id order, not whichever the planner
    produced first."""
    from app.services import host_query

    pid = test_project.id
    hosts = [_host(db_session, pid, f"10.62.0.{n}") for n in range(1, 9)]
    db_session.commit()
    monkeypatch.setattr(host_query, "BULK_SELECT_CAP", 5)
    body = client.get(f"{_base(pid)}/hosts/ids", params={"sort_by": "ip_address"}).json()
    assert body["capped"] is True and body["total"] == 8
    assert body["ids"] == sorted(h.id for h in hosts)[:5]


def test_the_server_states_the_bulk_select_cap_it_applies(client, db_session, test_project, monkeypatch):
    """The Hosts page says "the first N of M" BEFORE the action, so the cap is
    in the answers it already reads — the list's and ``/hosts/ids``' — and is
    the one the bulk routes refuse above.  (The page carried its own 5000.)"""
    from app.services import host_query

    pid = test_project.id
    hosts = [_host(db_session, pid, f"10.62.1.{n}") for n in range(1, 5)]
    db_session.commit()

    listed = client.get(f"{_base(pid)}/hosts/", params={"limit": 2}).json()
    assert listed["bulk_select_cap"] == host_query.BULK_SELECT_CAP == 5000
    whole = client.get(f"{_base(pid)}/hosts/ids").json()
    assert whole["cap"] == 5000 and whole["capped"] is False and len(whole["ids"]) == 4

    # One number, three places: the list, the ids and the bulk refusal.
    monkeypatch.setattr(host_query, "BULK_SELECT_CAP", 3)
    assert client.get(f"{_base(pid)}/hosts/", params={"limit": 2}).json()["bulk_select_cap"] == 3
    capped = client.get(f"{_base(pid)}/hosts/ids").json()
    assert capped["cap"] == 3 and capped["capped"] is True and capped["total"] == 4
    assert capped["ids"] == sorted(h.id for h in hosts)[:3]
    refused = client.post(f"{_base(pid)}/hosts/bulk/follow",
                          json={"host_ids": [h.id for h in hosts], "status": "in_review"})
    assert refused.status_code == 413, refused.text
    assert "max 3" in refused.json()["detail"]
    accepted = client.post(f"{_base(pid)}/hosts/bulk/follow",
                           json={"host_ids": capped["ids"], "status": "in_review"})
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# C — certorg: with a web interface nobody tied to a host
# ---------------------------------------------------------------------------

def test_not_certorg_survives_a_web_interface_with_no_host(client, db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    acme = _host(db_session, pid, "10.63.0.1")
    _host(db_session, pid, "10.63.0.2")
    for host_id, url in ((acme.id, "https://10.63.0.1/"), (None, "https://orphan.example/")):
        db_session.add(models.WebInterface(
            scan_id=scan.id, project_id=pid, host_id=host_id, source="httpx", url=url,
            cert_subject_org="Acme Corp"))
    db_session.commit()
    assert _ips(client, pid, q="certorg:acme") == {"10.63.0.1"}
    # ``NOT IN (…, NULL)`` is never true: this listed nothing.
    assert _ips(client, pid, q="NOT certorg:acme") == {"10.63.0.2"}


# ---------------------------------------------------------------------------
# E — the agents' host filter is the page's
# ---------------------------------------------------------------------------

@pytest.fixture
def parity_estate(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    crit = _host(db_session, pid, "10.64.0.1", os_name="Microsoft Windows", os_family="Windows")
    _vuln(db_session, crit, scan, VulnerabilitySeverity.CRITICAL)
    high = _host(db_session, pid, "10.64.0.2", os_family="Linux")          # family only, no OS name
    _vuln(db_session, high, scan, VulnerabilitySeverity.HIGH)
    both = _host(db_session, pid, "10.64.0.3")
    _vuln(db_session, both, scan, VulnerabilitySeverity.CRITICAL, title="c")
    _vuln(db_session, both, scan, VulnerabilitySeverity.HIGH, title="h")
    web = _host(db_session, pid, "10.64.0.4")
    _port(db_session, web, 8443, service_name="https", service_product="nginx")
    split = _host(db_session, pid, "10.64.0.5")      # 445 open with no name, smb named on another port
    _port(db_session, split, 445)
    _port(db_session, split, 4450, service_name="microsoft-ds")
    same = _host(db_session, pid, "10.64.0.6")
    _port(db_session, same, 445, service_name="microsoft-ds")
    db_session.commit()
    return pid


@pytest.mark.parametrize("params", [
    {"search": "linux"},                 # os_family
    {"search": "nginx"},                 # a port's product
    {"search": "https"},                 # a port's service name
    {"search": "8443"},                  # a port number
    {"search": "10.64.0"},
    {"has_critical_vulns": "true", "has_high_vulns": "true"},
    {"has_critical_vulns": "true"},
    {"ports": "445", "services": "microsoft-ds"},
    {"ports": "445"},
    {"services": "microsoft-ds"},
    {"state": "up", "subnets": "10.64.0.0/29"},
])
def test_an_agent_and_the_page_list_the_same_hosts_for_the_same_filter(client, parity_estate, params):
    headers = _agent(client, parity_estate)
    assert _agent_ips(client, headers, **params) == _ips(client, parity_estate, **params)


def test_what_changed_for_agents(client, parity_estate):
    headers = _agent(client, parity_estate)
    # search reaches the OS family and the ports, as the page's search box does
    assert _agent_ips(client, headers, search="linux") == {"10.64.0.2"}
    assert _agent_ips(client, headers, search="nginx") == {"10.64.0.4"}
    assert _agent_ips(client, headers, search="8443") == {"10.64.0.4"}
    # the two severity flags together are critical OR high, as the page's chips are
    assert _agent_ips(client, headers, has_critical_vulns="true", has_high_vulns="true") == {
        "10.64.0.1", "10.64.0.2", "10.64.0.3"}
    # ports + services are one port meeting both
    assert _agent_ips(client, headers, ports="445", services="microsoft-ds") == {"10.64.0.6"}


@pytest.mark.parametrize("params", [{"ports": "smb"}, {"ports": "445/tcp"}, {"ports": ","}])
def test_an_agent_is_still_refused_an_unusable_port(client, parity_estate, params):
    headers = _agent(client, parity_estate)
    r = client.get("/api/v1/agent/assist/hosts", headers=headers, params=params)
    assert r.status_code == 422, r.text


def test_a_malformed_agent_query_is_still_a_400(client, parity_estate):
    headers = _agent(client, parity_estate)
    r = client.get("/api/v1/agent/assist/hosts", headers=headers, params={"q": "has:stale_review"})
    assert r.status_code == 400 and "Invalid query" in r.json()["detail"]


# ---------------------------------------------------------------------------
# G — "Changed since review" is counted, ordered and cut by Postgres
# ---------------------------------------------------------------------------

@pytest.fixture
def followup_estate(db_session, test_project, test_user):
    """40 finished reviews of the caller's: 12 need evidence, 5 gained a port
    after the review, 3 gained a critical after it, 20 are simply done."""
    pid = test_project.id
    scan = _scan(db_session, pid)
    now = datetime.now(timezone.utc)
    listed = []
    for n in range(40):
        host = _host(db_session, pid, f"10.65.0.{n + 1}")
        reviewed_at = now - timedelta(days=40 - n)
        kind = "evidence" if n < 12 else "port" if n < 17 else "vuln" if n < 20 else "done"
        db_session.add(models.HostFollow(
            host_id=host.id, user_id=test_user.id, status=FollowStatus.REVIEWED, reviewed_at=reviewed_at,
            review_conclusion="needs_evidence" if kind == "evidence" else "no_issue",
        ))
        if kind == "port":
            _port(db_session, host, 8080, first_seen=reviewed_at + timedelta(hours=1))
            _port(db_session, host, 22, first_seen=reviewed_at - timedelta(days=1))
        if kind == "vuln":
            _vuln(db_session, host, scan, VulnerabilitySeverity.CRITICAL,
                  created_at=reviewed_at + timedelta(hours=1))
        if kind != "done":
            listed.append(host.id)
    db_session.commit()
    return pid, listed


def _statements(db_session, call, *loaded):
    """``call()`` and the SELECTs it ran.  ``loaded`` are the entities it is
    handed: read once here, so the refresh a commit left pending is not
    counted as the function's."""
    for entity in loaded:
        assert entity.id
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        return call(), seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_the_followup_count_is_one_statement_and_reads_no_review_rows(
    db_session, test_project, test_user, followup_estate,
):
    _pid, listed = followup_estate
    result, seen = _statements(
        db_session,
        lambda: operations_read_service.compute_review_followups(db_session, test_user, test_project, limit=0),
        test_user, test_project,
    )
    assert result.total == len(listed) == 20 and result.items == []
    assert len(seen) == 1, "\n".join(seen)
    assert "count(" in seen[0].lower()


def test_a_followup_page_reads_its_own_rows_only(db_session, test_project, test_user, followup_estate):
    _pid, listed = followup_estate
    result, seen = _statements(
        db_session,
        lambda: operations_read_service.compute_review_followups(
            db_session, test_user, test_project, limit=5, offset=10),
        test_user, test_project,
    )
    # Oldest review first: rows 11–15 of the list, i.e. two that need evidence
    # and three that gained a port.
    assert [row.host_id for row in result.items] == listed[10:15]
    assert result.total == 20
    assert [row.reasons[0].kind for row in result.items] == [
        "needs_evidence", "needs_evidence", "new_ports", "new_ports", "new_ports"]
    assert "8080" in result.items[2].reasons[0].text and "22" not in result.items[2].reasons[0].text
    assert len(seen) == 3, "\n".join(seen)
    assert " LIMIT " in seen[0] and " OFFSET " in seen[0] and "ORDER BY" in seen[0]


def test_followup_pages_add_up_to_follow_revisit(client, db_session, test_project, test_user, followup_estate):
    pid, listed = followup_estate
    collected = []
    for offset in range(0, 40, 7):
        page = operations_read_service.compute_review_followups(
            db_session, test_user, test_project, limit=7, offset=offset)
        assert page.total == 20
        collected += [row.host_id for row in page.items]
        assert all(row.reasons for row in page.items)
    assert collected == listed
    r = client.get(f"{_base(pid)}/hosts/", params={"q": "follow:revisit", "limit": 500})
    assert {item["id"] for item in r.json()["items"]} == set(listed)
    # A page past the end still says how long the list is.
    past = operations_read_service.compute_review_followups(db_session, test_user, test_project, limit=7, offset=70)
    assert past.items == [] and past.total == 20


# ---------------------------------------------------------------------------
# H — one definition of "the project has a scope"
# ---------------------------------------------------------------------------

def _coverage(client, pid):
    r = client.get(f"{_base(pid)}/coverage")
    assert r.status_code == 200, r.text
    body = r.json()
    return body["hosts_in_subnet_scope"], body["hosts_name_scope_only"], body["hosts_outside_scope"], body["total_hosts"]


def test_an_empty_scope_row_is_not_a_scope(client, db_session, test_project):
    """A Scope row holding no subnet and no domain declares nothing: the
    coverage counts read what a project with no Scope row reads (all three 0),
    by the one ``project_has_any_scope`` — and add up once a subnet exists."""
    pid = test_project.id
    inside = _host(db_session, pid, "10.66.0.1")
    _host(db_session, pid, "10.66.9.1")
    db_session.commit()
    without_row = _coverage(client, pid)

    scope = models.Scope(project_id=pid, name="empty")
    db_session.add(scope)
    db_session.commit()
    assert _coverage(client, pid) == without_row == (0, 0, 0, 2)

    subnet = models.Subnet(scope_id=scope.id, cidr="10.66.0.0/24")
    db_session.add(subnet)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=inside.id, subnet_id=subnet.id))
    db_session.commit()
    assert _coverage(client, pid) == (1, 0, 1, 2)


def test_out_of_scope_only_is_scope_none(client, db_session, test_project):
    pid = test_project.id
    scope = models.Scope(project_id=pid, name="s")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.67.0.0/24")
    db_session.add(subnet)
    db_session.flush()
    inside = _host(db_session, pid, "10.67.0.1")
    _host(db_session, pid, "10.67.9.1")
    db_session.add(models.HostSubnetMapping(host_id=inside.id, subnet_id=subnet.id))
    db_session.commit()
    assert _ips(client, pid, out_of_scope_only="true") == _ips(client, pid, q="scope:none") == {"10.67.9.1"}
    # … composed with another filter and counted the same way.
    ids = client.get(f"{_base(pid)}/hosts/ids", params={"out_of_scope_only": "true"}).json()
    assert ids["total"] == 1


# ---------------------------------------------------------------------------
# I — the small ones
# ---------------------------------------------------------------------------

def test_the_import_history_does_not_stop_at_500_batches(client, db_session, test_project):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    batches = [
        models.ScanBatch(project_id=pid, label=f"b{n}", created_at=now - timedelta(minutes=n))
        for n in range(520)
    ]
    db_session.add_all(batches)
    db_session.flush()
    db_session.add_all([
        models.Scan(project_id=pid, filename=f"b{n}.xml", tool_name="nmap", scan_type="nmap",
                    created_at=batch.created_at, batch_id=batch.id)
        for n, batch in enumerate(batches)
    ])
    db_session.commit()

    url = f"{_base(pid)}/scans/history"
    first = client.get(url, params={"limit": 50}).json()
    assert first["total"] == first["batch_total"] == 520 and first["has_more"] is True
    assert [e["id"] for e in first["items"]] == [b.id for b in batches[:50]]
    last = client.get(url, params={"skip": 500, "limit": 50}).json()
    assert [e["id"] for e in last["items"]] == [b.id for b in batches[500:]]
    assert last["has_more"] is False


def test_the_import_history_still_dates_a_batch_by_its_newest_matching_file(client, db_session, test_project):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    old = models.ScanBatch(project_id=pid, label="old", created_at=now - timedelta(hours=9))
    new = models.ScanBatch(project_id=pid, label="new", created_at=now - timedelta(hours=8))
    db_session.add_all([old, new])
    db_session.flush()

    def scan(name, hours, tool, batch=None):
        s = models.Scan(project_id=pid, filename=name, tool_name=tool, scan_type=tool,
                        created_at=now - timedelta(hours=hours), batch_id=batch.id if batch else None)
        db_session.add(s)
        db_session.flush()
        return s

    scan("o1.xml", 7, "nmap", old)
    scan("o2.xml", 1, "masscan", old)          # the old batch's newest file
    scan("n1.xml", 5, "nmap", new)
    single = scan("single.xml", 3, "nmap")
    db_session.commit()

    url = f"{_base(pid)}/scans/history"
    order = [(e["kind"], e["id"]) for e in client.get(url).json()["items"]]
    assert order == [("batch", old.id), ("scan", single.id), ("batch", new.id)]
    # Under a filter a batch is dated by its newest MATCHING file, and one
    # with no matching file is not listed.
    nmap = client.get(url, params={"tool": "nmap"}).json()
    assert [(e["kind"], e["id"]) for e in nmap["items"]] == [("scan", single.id), ("batch", new.id), ("batch", old.id)]
    masscan = client.get(url, params={"tool": "masscan"}).json()
    assert [(e["kind"], e["id"]) for e in masscan["items"]] == [("batch", old.id)]
    assert masscan["total"] == masscan["batch_total"] == 1


def test_a_scans_search_takes_underscore_and_percent_as_text(client, db_session, test_project):
    pid = test_project.id
    wanted = _scan(db_session, pid, name="dmz_full.xml")
    _scan(db_session, pid, name="dmzXfull.xml")
    _scan(db_session, pid, name="other.xml")
    db_session.commit()

    def found(term):
        r = client.get(f"{_base(pid)}/scans/", params={"search": term})
        assert r.status_code == 200, r.text
        body = r.json()
        return {row["id"] for row in (body["items"] if isinstance(body, dict) else body)}

    assert found("dmz_full") == {wanted.id}
    assert found("%") == set()
    assert len(found("dmz")) == 2


def test_my_queue_survives_a_failed_summary_and_lets_a_timeout_through(
    db_session, test_project, test_user, monkeypatch,
):
    """A section that carries on after a database error must roll back first,
    or its next statement fails with "current transaction is aborted"; and a
    statement the API timeout cancelled is the request's failure, not an
    empty column."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.services.vulnerability_service import VulnerabilityService

    host = _host(db_session, test_project.id, "10.68.0.1")
    _port(db_session, host, 22)
    db_session.add(models.HostFollow(host_id=host.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    db_session.commit()

    def broken(self, host_ids):
        self.db.execute(text("SELECT 1 FROM no_such_table_1008"))

    monkeypatch.setattr(VulnerabilityService, "get_bulk_host_vulnerability_summaries", broken)
    nested = db_session.begin_nested()
    try:
        result = operations_read_service.compute_my_attention_queue(db_session, test_user, test_project)
    finally:
        if nested.is_active:
            nested.rollback()
    assert [row.host_id for row in result.items] == [host.id]
    assert result.items[0].open_port_count == 1 and result.items[0].follow_status == "in_review"
    assert "watching_count" not in result.model_dump()

    class Cancelled(Exception):
        pgcode = "57014"

    def timed_out(self, host_ids):
        raise OperationalError("SELECT …", {}, Cancelled())

    monkeypatch.setattr(VulnerabilityService, "get_bulk_host_vulnerability_summaries", timed_out)
    with pytest.raises(OperationalError):
        operations_read_service.compute_my_attention_queue(db_session, test_user, test_project)


def test_the_list_serializer_does_not_read_host_scripts():
    """The list passes ``host_scripts=[]``; reading the relationship there is
    a lazy load per row."""
    class Row:
        id, ip_address, hostname, state, state_reason = 1, "10.0.0.1", None, "up", None
        os_name = os_family = os_generation = os_type = os_vendor = os_accuracy = None
        smb_signing = mac_address = mac_vendor = netbios_name = last_updated_scan_id = None
        first_seen = last_seen = None
        ports = []
        tag_assignments = []

        @property
        def host_scripts(self):
            raise AssertionError("the list read host_scripts")

    serialized = serialize_host_base(Row(), None, discoveries=[], note_count=0, host_scripts=[])
    assert serialized["host_scripts"] == []
    with pytest.raises(AssertionError):
        serialize_host_base(Row(), None, discoveries=[], note_count=0)
