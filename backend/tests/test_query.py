"""The Query page (v2.509.0): SPL-style queries over hosts, ports and scanner
observations, and saved / shared queries.

The owner, 2026-10-16: the teams know Splunk.  What is pinned here:

* the grammar's refusals name what is wrong and where (a 400 with position);
* search terms are the Hosts language and describe the row's HOST;
* ``where`` compares the dataset's columns, ``=`` exactly;
* every stats / top row's drill-down lists EXACTLY that group's rows — the
  count it shows is the total of the list it opens (fixtures exceed the
  caps where a cap could hide a mismatch);
* answers are one project's, and say how much there was ("first N of M");
* saved queries: mine + the team's shared, changed only by their creator or a
  project admin; agents read both through the same service.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import Host, Port
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.main import app
from app.services import query_service
from app.services.query_language import QueryError, parse


def _url(project, tail=""):
    return f"/api/v1/projects/{project.id}{tail}"


def _run(client, project, q):
    return client.get(_url(project, "/query"), params={"q": q})


@pytest.fixture
def estate(db_session, test_project):
    """12 hosts in 10.30.0.0/24: every host has 22/ssh open; even hosts also
    445/microsoft-ds open; host 1 has 3389 closed.  Observations: SMB signing
    (medium) on the even hosts, a critical exploitable CVE on host 1 and 2,
    an info row with no port on host 3."""
    now = datetime.now(timezone.utc)
    scan = models.Scan(project_id=test_project.id, filename="q.nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()
    hosts = []
    for i in range(1, 13):
        h = Host(project_id=test_project.id, ip_address=f"10.30.0.{i}", state="up",
                 hostname=f"q{i}.lab" if i != 5 else None, os_family="windows" if i % 2 == 0 else "linux",
                 first_seen=now, last_seen=now)
        db_session.add(h)
        db_session.flush()
        hosts.append(h)
        db_session.add(Port(host_id=h.id, port_number=22, protocol="tcp", state="open", service_name="ssh"))
        if i % 2 == 0:
            p = Port(host_id=h.id, port_number=445, protocol="tcp", state="open", service_name="microsoft-ds")
            db_session.add(p)
            db_session.flush()
            db_session.add(Vulnerability(host_id=h.id, port_id=p.id, scan_id=scan.id, title="SMB Signing not required",
                                         severity=VulnerabilitySeverity.MEDIUM, source="nessus", cvss_score=5.3))
    db_session.add(Port(host_id=hosts[0].id, port_number=3389, protocol="tcp", state="closed", service_name="ms-wbt-server"))
    for h in hosts[:2]:
        db_session.add(Vulnerability(host_id=h.id, scan_id=scan.id, title="OpenSSH RCE", cve_id="CVE-2024-6387",
                                     severity=VulnerabilitySeverity.CRITICAL, source="nessus", exploitable=True,
                                     cvss_score=8.1))
    db_session.add(Vulnerability(host_id=hosts[2].id, scan_id=scan.id, title="OS identification",
                                 severity=VulnerabilitySeverity.INFO, source="nmap"))
    db_session.commit()
    return hosts


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, message", [
    ("", "Write a query"),
    ("widgets | stats count", None),  # unknown dataset: decided by the service
    ("hosts | frobnicate", "Unknown command 'frobnicate'"),
    ("hosts | stats median(port)", "Unknown function 'median'"),
    ("hosts | stats dc", "`dc` needs a column"),
    ("hosts | where ip", "Expected a comparison"),
    ('hosts | where ip = "10.0.0.1', "not closed"),
    ("hosts | stats count | where ip=\"x\"", "`where` comes before"),
    ("hosts | stats count | stats count", "One `stats` or `top`"),
    ("hosts | head 0", "at least 1"),
    ("hosts | fields ip | stats count", "cannot be combined"),
    ("hosts | stats count, count", "names two columns"),
])
def test_the_grammar_says_what_is_wrong(text, message):
    if message is None:
        assert parse(text).dataset == "widgets"
        return
    with pytest.raises(QueryError) as exc:
        parse(text)
    assert message in exc.value.message


def test_a_pipe_inside_quotes_is_text():
    pipe = parse('hosts | where hostname="a|b" | head 5')
    assert len(pipe.wheres) == 1 and pipe.head == 5


def test_errors_are_400s_with_a_position(client, test_project, estate):
    r = _run(client, test_project, "hosts | where bogus=1")
    assert r.status_code == 400
    body = r.json()
    assert "Unknown column 'bogus' in hosts" in body["detail"] and body["position"] == 14
    # The Hosts language's own error, placed in the whole query.
    r = _run(client, test_project, "ports nosuch:x | stats count")
    assert r.status_code == 400 and "Unknown field 'nosuch'" in r.json()["detail"]
    assert r.json()["position"] == 6
    assert _run(client, test_project, "widgets").json()["detail"].startswith("Unknown dataset 'widgets'")
    assert "avg() takes a number column" in _run(client, test_project, "ports | stats avg(service)").json()["detail"]
    assert "takes = or != only" in _run(client, test_project, 'observations | where severity > "low"').json()["detail"]
    assert "compares with one of" in _run(client, test_project, 'observations | where severity="bad"').json()["detail"]


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def test_a_listing_has_the_default_columns_in_address_order(client, test_project, estate):
    body = _run(client, test_project, "hosts").json()
    assert body["kind"] == "rows" and body["total"] == 12
    assert [c["name"] for c in body["columns"]] == ["ip", "hostname", "state", "os", "os_family"]
    # Address order, not text order: .2 before .10.
    assert [r[0] for r in body["rows"]][:3] == ["10.30.0.1", "10.30.0.2", "10.30.0.3"]
    assert [r[0] for r in body["rows"]][-1] == "10.30.0.12"


def test_search_terms_are_the_hosts_language_about_the_host(client, test_project, estate):
    # On ports, `port:445` is "the host has 445 open": both of an even host's ports.
    body = _run(client, test_project, "ports port:445 | fields ip, port").json()
    assert body["total"] == 12  # 6 even hosts × (22 + 445)
    # …and `where port=445` is the row's own port.
    assert _run(client, test_project, "ports | where port=445").json()["total"] == 6
    # `| search` adds terms like the first segment does.
    assert _run(client, test_project, "hosts | search has:critical").json()["total"] == 2


def test_where_compares_columns(client, test_project, estate):
    q = lambda text: _run(client, test_project, text).json()  # noqa: E731
    assert q('ports | where service="ssh" AND state="open"')["total"] == 12
    assert q('ports | where service="SSH"')["total"] == 0          # = is exact
    assert q('ports | where like(service, "%SS%")')["total"] == 12  # like() is not
    assert q("ports | where port IN (445, 3389)")["total"] == 7
    assert q('ports | where NOT state="open"')["total"] == 1
    assert q("hosts | where isnull(hostname)")["total"] == 1
    assert q("observations | where isnull(port)")["total"] == 3     # 2 RCE + the info row
    assert q("observations | where cvss >= 8")["total"] == 2
    assert q("observations | where exploitable=true")["total"] == 2
    assert q('observations | where severity="critical" OR (severity="info" AND source="nmap")')["total"] == 3
    assert q('hosts | where first_seen >= "2000-01-01"')["total"] == 12


def test_a_listing_says_how_much_there_was(client, test_project, estate):
    body = _run(client, test_project, "ports | sort -port | head 3").json()
    assert (body["total"], len(body["rows"]), body["limit"]) == (19, 3, 3)  # 12 ssh + 6 smb + 1 rdp
    assert [r[1] for r in body["rows"]] == [3389, 445, 445]
    capped = _run(client, test_project, "ports | head 100000").json()
    assert capped["capped"] is True and capped["limit"] == query_service.ROW_CAP


# ---------------------------------------------------------------------------
# stats / top, and every group's drill-down
# ---------------------------------------------------------------------------

def _drill_totals_match(client, project, body, count_col):
    i = [c["name"] for c in body["columns"]].index(count_col)
    for row, drill in zip(body["rows"], body["drilldowns"]):
        listed = _run(client, project, drill).json()
        assert listed["kind"] == "rows", drill
        assert listed["total"] == row[i], (drill, row)


def test_stats_counts_and_each_group_opens_exactly_its_rows(client, test_project, estate):
    body = _run(client, test_project, 'ports | stats count, dc(host) as hosts by service, state').json()
    assert body["kind"] == "stats"
    assert [c["name"] for c in body["columns"]] == ["service", "state", "count", "hosts"]
    rows = {(r[0], r[1]): (r[2], r[3]) for r in body["rows"]}
    assert rows == {("microsoft-ds", "open"): (6, 6), ("ms-wbt-server", "closed"): (1, 1), ("ssh", "open"): (12, 12)}
    assert body["total"] == 3
    _drill_totals_match(client, test_project, body, "count")


def test_a_null_group_opens_with_isnull_and_a_severity_sorts_by_rank(client, test_project, estate):
    body = _run(client, test_project, "observations | stats count by severity, port").json()
    assert [(r[0], r[1]) for r in body["rows"]] == [("info", None), ("medium", 445), ("critical", None)]
    assert any("isnull(port)" in d for d in body["drilldowns"])
    _drill_totals_match(client, test_project, body, "count")
    ranked = _run(client, test_project, "observations | stats count by severity | sort -severity").json()
    assert [r[0] for r in ranked["rows"]] == ["critical", "medium", "info"]


def test_aggregates(client, test_project, estate):
    body = _run(client, test_project,
                "observations | stats min(cvss) as lo, max(cvss) as hi, avg(cvss) as mean, values(severity) as sev, "
                "dc(host) as hosts").json()
    (row,) = body["rows"]
    assert row[:3] == [5.3, 8.1, round((5.3 * 6 + 8.1 * 2) / 8, 2)]
    assert row[3] == ["critical", "info", "medium"] and row[4] == 8
    assert body["drilldowns"] == ["observations"]


def test_top_counts_with_percent_and_drills_down(client, test_project, estate):
    body = _run(client, test_project, 'ports | where state="open" | top limit=1 service').json()
    assert [c["name"] for c in body["columns"]] == ["service", "count", "percent"]
    assert body["rows"] == [["ssh", 12, round(12 * 100 / 18, 2)]]
    assert body["total"] == 2  # groups, before the limit
    assert body["drilldowns"] == ['ports | where state="open" | where service="ssh"']
    _drill_totals_match(client, test_project, body, "count")


def test_sort_after_stats_takes_the_answers_columns(client, test_project, estate):
    body = _run(client, test_project, "ports | stats dc(host) as hosts by port | sort -hosts | head 1").json()
    assert body["rows"] == [[22, 12]] and body["total"] == 3
    assert "takes a column of the answer" in _run(client, test_project, "ports | stats count by port | sort -ip").json()["detail"]


def test_groups_past_the_cap_are_counted_and_each_shown_one_still_drills_exactly(
        client, db_session, test_project, estate, monkeypatch):
    monkeypatch.setattr(query_service, "GROUP_CAP", 4)
    body = _run(client, test_project, "hosts | stats count by ip").json()
    assert (body["total"], len(body["rows"]), body["limit"]) == (12, 4, 4)
    _drill_totals_match(client, test_project, body, "count")


def test_a_quote_in_a_group_value_is_written_back_safely(client, db_session, test_project, estate):
    estate[0].hostname = 'odd "name" \\ here'
    db_session.commit()
    body = _run(client, test_project, "hosts | stats count by hostname").json()
    _drill_totals_match(client, test_project, body, "count")


def test_answers_are_this_projects_only(client, db_session, test_project, estate):
    other = Project(name="Other", slug="other-query")
    db_session.add(other)
    db_session.flush()
    db_session.add(Host(project_id=other.id, ip_address="10.30.0.200", state="up"))
    db_session.commit()
    assert _run(client, test_project, "hosts").json()["total"] == 12


# ---------------------------------------------------------------------------
# Saved queries
# ---------------------------------------------------------------------------

def _member(db, project, user_id, username, role=ProjectRole.ANALYST):
    user = User(id=user_id, username=username, email=f"{user_id}@example.com", full_name=username,
                hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def test_saved_queries_are_mine_or_shared_and_only_their_creator_or_an_admin_changes_them(
        client, db_session, test_project, estate):
    base = _url(test_project, "/queries/saved")
    alice = _member(db_session, test_project, 471, "q-alice", ProjectRole.VIEWER)
    bob = _member(db_session, test_project, 472, "q-bob")
    as_admin = app.dependency_overrides.get(get_current_user)
    try:
        app.dependency_overrides[get_current_user] = lambda: alice
        mine = client.post(base, json={"name": "Open SMB", "query": "ports | where port=445"}).json()
        shared = client.post(base, json={"name": "By OS", "query": "hosts | stats count by os_family",
                                         "shared": True, "description": "Team view"}).json()
        assert (mine["mine"], mine["shared"], mine["can_modify"]) == (True, False, True)
        assert client.post(base, json={"name": "Open SMB", "query": "hosts"}).status_code == 409
        bad = client.post(base, json={"name": "Broken", "query": "hosts | where bogus"})
        assert bad.status_code == 400 and "Expected a comparison" in bad.json()["detail"]
        assert client.post(base, json={"name": "Bad dataset", "query": "widgets"}).status_code == 400

        app.dependency_overrides[get_current_user] = lambda: bob
        seen = client.get(base).json()
        assert [(q["name"], q["mine"], q["can_modify"], q["created_by"]) for q in seen] == [
            ("By OS", False, False, "q-alice"),
        ]
        assert client.patch(f"{base}/{shared['id']}", json={"name": "Mine now"}).status_code == 403
        assert client.delete(f"{base}/{shared['id']}").status_code == 403
        assert client.delete(f"{base}/{mine['id']}").status_code == 404   # private: not even visible

        app.dependency_overrides[get_current_user] = lambda: alice
        changed = client.patch(f"{base}/{mine['id']}", json={"shared": True, "query": "ports | where port=22"}).json()
        assert (changed["shared"], changed["query"]) == (True, "ports | where port=22")
    finally:
        app.dependency_overrides[get_current_user] = as_admin
    # The project admin (the client fixture's user) may change and delete a shared one.
    assert client.patch(f"{base}/{shared['id']}", json={"description": "Kept"}).json()["can_modify"] is True
    assert client.delete(f"{base}/{shared['id']}").status_code == 204


def test_agents_run_queries_and_read_saved_ones_through_the_same_service(client, test_project, estate):
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    key = {"X-API-Key": r.json()["api_key"]}
    q = "ports | stats dc(host) as hosts by service"
    page = _run(client, test_project, q).json()
    agent = client.get("/api/v1/agent/assist/query", headers=key, params={"q": q})
    assert agent.status_code == 200, agent.text
    assert agent.json() == page
    bad = client.get("/api/v1/agent/assist/query", headers=key, params={"q": "hosts | where bogus=1"})
    assert bad.status_code == 400 and bad.json()["position"] == 14
    assert client.get("/api/v1/agent/assist/query/schema", headers=key).json() == client.get(
        _url(test_project, "/query/schema")).json()
    client.post(_url(test_project, "/queries/saved"), json={"name": "Team", "query": q, "shared": True})
    listed = client.get("/api/v1/agent/assist/saved-queries", headers=key).json()
    assert [s["name"] for s in listed] == ["Team"]


# ---------------------------------------------------------------------------
# Phase 2 (v2.510.0): findings, endpoints, web, names; links; CSV export
# ---------------------------------------------------------------------------

@pytest.fixture
def more(db_session, test_project, estate):
    """Two findings: SMB signing (medium, open, owned by the admin) on the six
    even hosts — one of them retest —, and an unowned critical on host 1.
    Two web interfaces, one self-signed and tied to no host; two names."""
    from app.db.models import DNSName, WebInterface
    from app.db.models_findings import Finding, FindingHost

    admin = db_session.query(User).filter(User.username == "test-admin").first() or db_session.query(User).first()
    smb = Finding(project_id=test_project.id, title="SMB signing not required", severity="medium", status="open",
                  source="scanner", owner_id=admin.id, cvss_score=5.3)
    rce = Finding(project_id=test_project.id, title='OpenSSH "regreSSHion"', severity="critical",
                  status="confirmed", source="manual")
    db_session.add_all([smb, rce])
    db_session.flush()
    for h in estate[1::2]:
        db_session.add(FindingHost(finding_id=smb.id, host_id=h.id,
                                   host_status="retest" if h is estate[1] else "open",
                                   retest_requested_at=datetime.now(timezone.utc) if h is estate[1] else None))
    db_session.add(FindingHost(finding_id=rce.id, host_id=estate[0].id, host_status="open"))
    scan = db_session.query(models.Scan).filter(models.Scan.project_id == test_project.id).first()
    db_session.add_all([
        WebInterface(project_id=test_project.id, host_id=estate[0].id, scan_id=scan.id, source="httpx",
                     url="https://10.30.0.1/", protocol="https", port=443, status_code=200,
                     title="=HYPERLINK(\"x\")", server_header="nginx", cert_self_signed=False),
        WebInterface(project_id=test_project.id, host_id=None, scan_id=scan.id, source="httpx",
                     url="https://vhost.example/", protocol="https", port=443, status_code=401,
                     title="Login", server_header="nginx", cert_self_signed=True),
        DNSName(project_id=test_project.id, fqdn="a.example.com", kind="fqdn"),
        DNSName(project_id=test_project.id, fqdn="*.example.com", kind="wildcard"),
    ])
    db_session.commit()
    return {"smb": smb, "rce": rce}


def test_findings_take_search_terms_through_their_endpoints_hosts(client, test_project, more):
    q = lambda text: _run(client, test_project, text).json()  # noqa: E731
    body = q("findings")
    assert body["total"] == 2
    # Most severe first; the finding's page is each row's link.
    assert [r[1] for r in body["rows"]] == ["critical", "medium"]
    assert body["links"] == [f"/findings/{more['rce'].id}", f"/findings/{more['smb'].id}"]
    assert q("findings port:445")["total"] == 1                    # an endpoint's host has 445 open
    assert q('findings | where status="confirmed"')["total"] == 1   # stored as text, compared by value
    assert q('findings | where isnull(owner)')["rows"][0][4] is None
    by_status = q("findings | stats count by severity, status")
    _drill_totals_match(client, test_project, by_status, "count")


def test_endpoints_are_a_finding_on_one_host(client, test_project, more):
    q = lambda text: _run(client, test_project, text).json()  # noqa: E731
    assert q("endpoints")["total"] == 7
    retest = q('endpoints | where state="retest" | table ip, finding, retest_requested_at')
    assert retest["total"] == 1 and retest["rows"][0][0] == "10.30.0.2"
    assert retest["links"][0].startswith(f"/findings/{more['smb'].id}?endpoint=")
    grouped = q("endpoints | stats dc(host) as hosts by title, state")
    _drill_totals_match(client, test_project, grouped, "hosts")


def test_web_lists_hostless_interfaces_and_names_refuse_search_terms(client, test_project, more):
    q = lambda text: _run(client, test_project, text).json()  # noqa: E731
    web = q("web | sort ip")
    assert web["total"] == 2 and None in web["links"]
    assert q("web | where cert_self_signed=true")["total"] == 1
    assert q("web has:critical")["total"] == 1                     # the host-less row matches no host term
    assert q('names | where kind="wildcard"')["total"] == 1
    refused = _run(client, test_project, "names has:web")
    assert refused.status_code == 400 and "names has no host" in refused.json()["detail"]
    assert "links" in q("names") and q("names")["links"] is None


def test_the_csv_export_is_the_answer_for_auditors_and_up(client, db_session, test_project, more):
    r = client.get(_url(test_project, "/query/export.csv"), params={"q": "web | table url, title | sort url"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "filename=query-web.csv" in r.headers["content-disposition"]
    assert r.headers["x-total-count"] == "2"
    lines = r.text.strip().splitlines()
    assert lines[0] == "url,title"
    # A title a spreadsheet would run as a formula is text.
    assert lines[1] == "https://10.30.0.1/,\"'=HYPERLINK(\"\"x\"\")\""
    grouped = client.get(_url(test_project, "/query/export.csv"),
                         params={"q": "observations | stats values(severity) as sev by source"}).text
    assert "critical; medium" in grouped
    viewer = _member(db_session, test_project, 481, "q-viewer", ProjectRole.VIEWER)
    as_admin = app.dependency_overrides.get(get_current_user)
    try:
        app.dependency_overrides[get_current_user] = lambda: viewer
        assert client.get(_url(test_project, "/query/export.csv"), params={"q": "hosts"}).status_code == 403
        assert _run(client, test_project, "hosts").status_code == 200   # reading stays every member's
    finally:
        app.dependency_overrides[get_current_user] = as_admin


def test_the_export_lists_past_the_pages_cap(client, test_project, estate, monkeypatch):
    monkeypatch.setattr(query_service, "ROW_CAP", 5)
    assert len(_run(client, test_project, "ports").json()["rows"]) == 5
    r = client.get(_url(test_project, "/query/export.csv"), params={"q": "ports"})
    assert len(r.text.strip().splitlines()) == 1 + 19
