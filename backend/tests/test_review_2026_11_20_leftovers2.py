"""Review 2026-11-20, what the seven batches left — the reads.

* T1 — a web interface's technologies are rows that follow the row's
  ``technologies`` by themselves; ``tech:``, ``techis:``, the facet and the
  monoculture count the same CURRENT rows, so the monoculture's count is the
  list ``techis:"<name>" scope:subnet`` opens;
* T6 — the sessions list reads a session's transport and first call from the
  session row, set once by the call-log middleware;
* C18 — Oversight's four remediation reads are one statement, equal to the four;
* C20 — recording a follow-up is two statements, whatever the number of rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.db import models
from app.db.models import Scope, Subnet, WebInterface, WebInterfaceTechnology
from app.db.models_agent import AgentApiCall, AgentSession
from app.db.models_remediation import RemediationEvent
from app.services import remediation_policy, remediation_service
from app.services.agent_session_service import activity_for_agent_sessions
from app.services.systemic_insight_service import compute_systemic_insights
from tests.test_remediation_deadlines import TODAY, _rows, apply, base, day, on  # noqa: F401 — fixture
from tests.test_systemic_insights import _host, _map

NOW = datetime.now(timezone.utc)


# --- technologies --------------------------------------------------------------

def _names(db, interface):
    return sorted(
        name for (name,) in db.query(WebInterfaceTechnology.name).filter_by(web_interface_id=interface.id)
    )


def _scan(db, project_id, name="s"):
    scan = models.Scan(project_id=project_id, filename=name, tool_name="httpx", scan_type="web_fingerprint")
    db.add(scan)
    db.flush()
    return scan


def test_the_rows_follow_what_the_row_says(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    host = _host(db_session, pid, "10.86.0.1")
    said = WebInterface(host_id=host.id, project_id=pid, scan_id=scan.id, url="http://10.86.0.1/",
                        technologies=[" nginx 1.14.0 ", "React", "React", "", None, "x" * 400])
    unsaid = WebInterface(host_id=host.id, project_id=pid, scan_id=scan.id, url="http://10.86.0.1:81/")
    hostless = WebInterface(project_id=pid, scan_id=scan.id, url="http://orphan.example.test/",
                            technologies=["nginx 1.14.0"])
    db_session.add_all([said, unsaid, hostless])
    db_session.flush()
    assert _names(db_session, said) == ["React", "nginx 1.14.0", "x" * 160]
    assert said.technologies_stated is True and not unsaid.technologies_stated
    assert _names(db_session, unsaid) == [] and _names(db_session, hostless) == []

    said.technologies = ["Tomcat 9.0.1"]                  # a re-import replaces them
    db_session.flush()
    assert _names(db_session, said) == ["Tomcat 9.0.1"]
    said.technologies = None                              # "none seen" is a statement too
    db_session.flush()
    assert _names(db_session, said) == [] and said.technologies_stated is True


def _total(client, project, q):
    response = client.get(f"/api/v1/projects/{project.id}/hosts", params={"q": q})
    assert response.status_code == 200, response.text
    return response.json()["total"]


def test_every_reader_counts_the_latest_observation_and_the_monoculture_opens_its_list(
    client, db_session, test_project,
):
    pid = test_project.id
    scope = Scope(project_id=pid)
    db_session.add(scope)
    db_session.flush()
    subnet = Subnet(scope_id=scope.id, cidr="10.87.0.0/24", site=None, site_id=None)
    db_session.add(subnet)
    earlier, later = _scan(db_session, pid, "earlier"), _scan(db_session, pid, "later")
    for n in range(1, 7):
        host = _host(db_session, pid, f"10.87.0.{n}")
        _map(db_session, host, subnet)
        url = f"https://10.87.0.{n}"
        db_session.add(WebInterface(
            host_id=host.id, project_id=pid, scan_id=earlier.id, url=url, protocol="https",
            technologies=["nginx 1.14.0", "Bootstrap"], last_seen=NOW - timedelta(days=9),
        ))
        if n == 6:                                        # upgraded since: the later scan says so
            db_session.add(WebInterface(
                host_id=host.id, project_id=pid, scan_id=later.id, url=url, protocol="https",
                technologies=["nginx 1.14.2"], last_seen=NOW,
            ))
    outside = _host(db_session, pid, "10.99.0.1")         # same technology, in no declared subnet
    db_session.add(WebInterface(
        host_id=outside.id, project_id=pid, scan_id=earlier.id, url="https://10.99.0.1",
        technologies=["nginx 1.14.0"], last_seen=NOW,
    ))
    db_session.commit()

    assert _total(client, test_project, "tech:nginx") == 7             # part of a name, current rows
    assert _total(client, test_project, 'tech:"nginx 1.14"') == 7      # 1.14.0 x6 and 1.14.2 x1
    assert _total(client, test_project, 'techis:"nginx 1.14.0"') == 6  # exact: the upgraded host is out
    assert _total(client, test_project, 'techis:"nginx 1.14"') == 0
    assert _total(client, test_project, "NOT tech:nginx") == 0

    spot = next(
        b for b in compute_systemic_insights(db_session, pid)["blind_spots"] if b["key"] == "tech:nginx 1.14.0"
    )
    assert spot["affected_hosts"] == 5
    assert _total(client, test_project, 'techis:"nginx 1.14.0" scope:subnet') == spot["affected_hosts"]

    facets = client.get(f"/api/v1/projects/{pid}/hosts/filters/data").json()["technologies"]
    assert {f["name"]: f["host_count"] for f in facets} == {
        "nginx 1.14.0": 6, "Bootstrap": 5, "nginx 1.14.2": 1,
    }


def test_a_later_page_of_segments_is_cut_from_the_list_its_first_page_was(db_session, test_project, monkeypatch):
    from app.services import subnet_insight_service as segments

    pid = test_project.id
    scope = Scope(project_id=pid)
    db_session.add(scope)
    db_session.flush()
    for n in range(1, 4):
        subnet = Subnet(scope_id=scope.id, cidr=f"10.88.{n}.0/24", site=None, site_id=None)
        db_session.add(subnet)
        db_session.flush()
        _map(db_session, _host(db_session, pid, f"10.88.{n}.1"), subnet)
    db_session.flush()

    first = segments.compute_subnet_insights(db_session, pid, limit=2, offset=0)
    assert first["total"] == 3 and len(first["subnets"]) == 2

    reads: list = []
    real = segments._load_subnet_meta
    monkeypatch.setattr(segments, "_load_subnet_meta", lambda *a, **k: reads.append(1) or real(*a, **k))
    second = segments.compute_subnet_insights(db_session, pid, limit=2, offset=2)
    assert reads == [] and second["total"] == 3 and len(second["subnets"]) == 1
    assert second["totals"] == first["totals"]
    shown = [row["subnet_id"] for row in first["subnets"] + second["subnets"]]
    assert len(set(shown)) == 3

    again = segments.compute_subnet_insights(db_session, pid, limit=2, offset=0)   # a first page always reads
    assert reads == [1] and again["subnets"] == first["subnets"]


# --- agent sessions ------------------------------------------------------------

def test_a_sessions_transport_and_first_call_are_the_rows_and_its_count_the_logs(
    db_session, test_project, test_user,
):
    first = NOW - timedelta(hours=3)
    called = AgentSession(workflow="project", project_id=test_project.id, started_by_id=test_user.id,
                          status="active")
    silent = AgentSession(workflow="project", project_id=test_project.id, started_by_id=test_user.id,
                          status="active")
    db_session.add_all([called, silent])
    db_session.flush()
    # Recorded out of order, the second through MCP: the row keeps the
    # earliest time and that MCP carried a call — whoever writes the call.
    for at, mcp in ((NOW - timedelta(hours=1), False), (first, True), (NOW, False)):
        db_session.add(AgentApiCall(
            project_id=test_project.id, agent_session_id=called.id, error_class="x", via_mcp=mcp,
            method="GET", path="/api/v1/agent/assist/hosts", status_code=200, duration_ms=1, created_at=at,
        ))
        db_session.flush()
    db_session.expire_all()

    statements: list = []
    bind = db_session.get_bind()
    listener = lambda conn, cursor, statement, *rest: statements.append(statement)  # noqa: E731
    event.listen(bind, "before_cursor_execute", listener)
    try:
        activity = activity_for_agent_sessions(db_session, [called.id, silent.id])
    finally:
        event.remove(bind, "before_cursor_execute", listener)

    assert set(activity) == {called.id}                    # no call, no entry: "never connected"
    seen = activity[called.id]
    assert (seen.call_count, seen.via_mcp, seen.connection) == (3, True, "mcp")
    assert seen.first_call_at == first
    log_reads = [s for s in statements if "agent_api_calls" in s]
    assert len(log_reads) == 1 and "via_mcp" not in log_reads[0] and "min(" not in log_reads[0].lower()


# --- remediation ---------------------------------------------------------------

def test_oversights_one_statement_is_the_four_it_replaced(client, db_session, test_project, on):  # noqa: F811
    crit = _rows(db_session, test_project, 3, severity="critical", net=93, title="crit")
    med = _rows(db_session, test_project, 3, severity="medium", net=94, title="med")
    low = _rows(db_session, test_project, 2, severity="low", net=95, title="low")
    apply(client, test_project, [
        {"finding_host_id": crit[0], "notified_on": day(33), "contact_email": "a@testdomain.com"},
        {"finding_host_id": crit[1], "notified_on": day(50), "contact_email": "b@testdomain.com"},
        {"finding_host_id": crit[2], "notified_on": day(26), "contact_email": "c@testdomain.com"},
        {"finding_host_id": med[0], "notified_on": day(130), "contact_email": "c@testdomain.com"},
        {"finding_host_id": med[1], "notified_on": day(200), "contact_email": "d@testdomain.com"},
        {"finding_host_id": med[2], "notified_on": day(40), "status": "closed", "closed_on": day(2)},
        {"finding_host_id": low[0], "notified_on": day(300), "status": "closed", "closed_on": day(100)},
    ])
    policy = remediation_policy.load(db_session)
    today = policy.today()
    ids = [test_project.id, 999_999]                        # a project with no rows is present too

    one = remediation_service.oversight_by_project(db_session, ids, policy, today)
    assert {pid: row["states"] for pid, row in one.items()} == (
        remediation_service.state_counts_by_project(db_session, ids, policy, today))
    assert {pid: row["durations"] for pid, row in one.items()} == (
        remediation_service.durations_by_project(db_session, ids, today))
    assert {pid: row["overdue"] for pid, row in one.items()} == (
        remediation_service.overdue_breakdown_by_project(db_session, ids, policy, today))
    assert {pid: row["gaps"] for pid, row in one.items()} == (
        remediation_service.verification_counts_by_project(db_session, ids))
    mine = one[test_project.id]
    assert mine["states"]["overdue"] == 4 and mine["durations"]["closed_measured"] == 2
    assert mine["durations"]["longest_overdue_days"] is not None


def test_a_follow_up_is_two_statements_whatever_the_number_of_rows(client, db_session, test_project, on):  # noqa: F811
    ids = _rows(db_session, test_project, 12, severity="high", net=96, title="Many hosts")
    apply(client, test_project, [
        {"finding_host_id": i, "contact_email": "roger@testdomain.com", "notified_on": day(45)} for i in ids
    ])

    statements: list = []
    bind = db_session.get_bind()
    listener = lambda conn, cursor, statement, *rest: statements.append(statement)  # noqa: E731
    event.listen(bind, "before_cursor_execute", listener)
    try:
        answer = client.post(f"{base(test_project)}/follow-up", json={
            "contact_email": "roger@testdomain.com", "note": "Called."}).json()
    finally:
        event.remove(bind, "before_cursor_execute", listener)

    assert (answer["recorded"], answer["already_recorded"]) == (12, 0)
    written = [s for s in statements if s.lstrip().upper().startswith("INSERT INTO REMEDIATION_EVENTS")
               or s.lstrip().upper().startswith(f"INSERT INTO {RemediationEvent.__tablename__.upper()}")]
    assert len(written) == 1
    entries = db_session.query(RemediationEvent).filter_by(kind="follow_up").all()
    assert {e.finding_host_id for e in entries} == set(ids)
    assert all(
        (e.body, e.new_value, e.finding_title, e.project_id) == (
            "Called.", "roger@testdomain.com", "Many hosts", test_project.id)
        for e in entries
    )
    rows = client.get(base(test_project)).json()["items"]
    assert {row["last_follow_up_on"] for row in rows} == {TODAY.isoformat()}
    again = client.post(f"{base(test_project)}/follow-up", json={"contact_email": "roger@testdomain.com"}).json()
    assert (again["recorded"], again["already_recorded"]) == (0, 12)
