"""Read costs looked at on 2026-10-10 (the plan's C1 and C3).

C3 rewrote HOW the host-test list is produced, never WHAT it says — the
pattern of ``test_read_path_review.py``: the code that was replaced is written
out here as it stood, the new answer is compared with it field by field over
rows chosen to separate the two, and the statement the rewrite removed is
counted.

C1 (the Hosts facets' three port statements) was measured and NOT changed:
two single-statement forms returned the same values and were no faster at
70,000 hosts — each answer needs its own grouping of the port rows, and the
groupings, not the reads, are the cost.  What stays here is the estate built
for that comparison, pinning the facets by value.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.orm import joinedload

from app.api.v1.endpoints import hosts as hosts_endpoint
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.db.models_host_tests import ACTIVE_TEST_STATUSES, HostTest
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.services import host_test_service
from app.services.host_query import build_filtered_host_query


@contextmanager
def statements(db_session):
    """The SQL statements run inside the block."""
    seen = []
    bind = db_session.get_bind()
    engine = getattr(bind, "engine", bind)

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


# ---------------------------------------------------------------------------
# C1 — /hosts/filters/data: the port and service facets, by value
# ---------------------------------------------------------------------------

@pytest.fixture
def facet_cache():
    hosts_endpoint._FACET_CACHE.clear()
    yield hosts_endpoint._FACET_CACHE
    hosts_endpoint._FACET_CACHE.clear()


@pytest.fixture
def port_estate(db_session, test_project):
    """Ports chosen so that every way the three passes could disagree shows:
    one port number under several service names (and under none, and under the
    empty string), one service on several numbers, the same number on tcp and
    udp of one host in different states, a NULL state, a state the editor has
    no box for, hosts the filters leave out, ties in every ordering, and the
    same ports in another project."""
    pid = test_project.id

    def host(ip, state="up", os_name=None, *ports):
        h = models.Host(project_id=pid, ip_address=ip, state=state, os_name=os_name)
        db_session.add(h)
        db_session.flush()
        for number, proto, port_state, service, product, version in ports:
            db_session.add(models.Port(
                host_id=h.id, port_number=number, protocol=proto, state=port_state,
                service_name=service, service_product=product, service_version=version,
            ))
        return h

    host("10.61.0.1", "up", "Linux",
         (22, "tcp", "open", "ssh", "OpenSSH", "7.4"),
         (80, "tcp", "open", "http", "nginx", None),
         (443, "tcp", "open", "https", None, None),
         (2049, "tcp", "open", "nfs", None, None),
         (2049, "udp", "closed", "nfs", None, None))
    host("10.61.0.2", "up", "Linux",
         (22, "tcp", "closed", "ssh", "OpenSSH", "8.9"),
         (80, "tcp", "filtered", "http-proxy", None, None),
         (8080, "tcp", "open", "http", "Jetty", "9.4"),
         (2049, "tcp", "open", "nfs_acl", None, None))
    host("10.61.0.3", "up", "Windows Server 2019",
         (22, "tcp", "filtered", "ssh", None, None),
         (80, "tcp", "open", "http", "IIS", "10.0"),
         (445, "tcp", "open", "microsoft-ds", None, None),
         (2049, "tcp", "open", None, None, None),
         (3389, "tcp", None, "ms-wbt-server", None, None))
    host("10.61.0.4", "up", "Windows Server 2019",
         (80, "tcp", "open", "", None, None),
         (445, "tcp", "open|filtered", "microsoft-ds", None, None),
         (2049, "tcp", "filtered", "nfs", None, None),
         (2222, "tcp", "open", "ssh", "Dropbear", None))
    host("10.61.0.5", "down", "Linux",
         (22, "tcp", "open", "ssh", "OpenSSH", "7.4"),
         (80, "tcp", "closed", "http-proxy", None, None),
         (9999, "tcp", "open", "abyss", None, None))
    host("10.61.0.6", "down", None,
         (80, "tcp", "open", "http-proxy", None, None),
         (5900, "tcp", "open", "vnc", None, None))
    host("10.61.0.7", "up", None)  # no port at all

    other = Project(name="c1-other", slug="c1-other")
    db_session.add(other)
    db_session.flush()
    stranger = models.Host(project_id=other.id, ip_address="10.61.0.1", state="up")
    db_session.add(stranger)
    db_session.flush()
    for number, service in ((22, "ssh"), (80, "http"), (6000, "x11")):
        db_session.add(models.Port(host_id=stranger.id, port_number=number, protocol="tcp",
                                   state="open", service_name=service))
    db_session.flush()
    return pid


def test_the_port_and_service_facets_by_value(client, db_session, port_estate, facet_cache):
    """What the three port statements answer, pinned by value on the rows
    where a rewrite could go wrong."""
    pid = port_estate
    body = client.get(f"/api/v1/projects/{pid}/hosts/filters/data").json()
    ports = {row["port"]: row for row in body["common_ports"]}
    services = {row["name"]: row for row in body["services"]}

    # 2049: open on .1 (tcp; closed on udp), .2, .3 (no service name); filtered on .4.
    assert ports[2049]["count"] == 3 and ports[2049]["state"] == "open"
    assert ports[2049]["state_counts"] == {
        "closed": 1, "filtered": 1, "open": 3, "closed,filtered": 2, "closed,open": 3,
        "filtered,open": 4, "closed,filtered,open": 4, "any": 4,
    }
    assert ports[2049]["service"] == "nfs"            # two hosts say nfs, one nfs_acl, one nothing
    # 80: http on two hosts, http-proxy on three, the empty string on one —
    # the label is the name most hosts report, and the empty name is no name.
    assert ports[80]["service"] == "http-proxy" and ports[80]["state_counts"]["any"] == 6
    # A port with no state and one in a state the editor has no box for count
    # under "any" only.
    assert ports[3389]["state_counts"]["any"] == 1 and ports[3389]["state_counts"]["closed,filtered,open"] == 0
    assert ports[445]["state_counts"]["any"] == 2 and ports[445]["state_counts"]["open"] == 1
    # Services: no row for the missing or empty name; ssh spans 22 and 2222.
    assert "" not in services and None not in services
    assert services["ssh"]["state_counts"]["any"] == 5 and services["ssh"]["count"] == 3
    # Another project's ports are not counted.
    assert 6000 not in ports and "x11" not in services
    assert ports[22]["state_counts"]["any"] == 4

    # Under a filter that leaves hosts out, with the states named: only the
    # matching hosts' ports are counted.  `port_states` alone is itself a host
    # filter (a host with a filtered port), so three hosts are left: .2, .3, .4.
    up = client.get(f"/api/v1/projects/{pid}/hosts/filters/data",
                    params={"state": "up", "port_states": "filtered"}).json()
    up_ports = {row["port"]: row for row in up["common_ports"]}
    assert 9999 not in up_ports and 5900 not in up_ports and 443 not in up_ports
    assert (up_ports[80]["count"], up_ports[80]["state"], up_ports[80]["state_counts"]["any"]) == (1, "filtered", 3)
    # One host says http, one http-proxy, one nothing: a tie goes to the later name.
    assert up_ports[80]["service"] == "http-proxy"
    assert {row["name"]: row["count"] for row in up["services"]}["nfs"] == 1


# ---------------------------------------------------------------------------
# C3 — the host-test list: one load with the joins, not a load and a reload
# ---------------------------------------------------------------------------

def _old_serialize_many(db, rows):
    """``host_test_service.serialize_many`` as it stood before C3: the rows it
    is handed are read AGAIN by id, with the host and the two people joined."""
    ids = [row.id for row in rows]
    if not ids:
        return []
    loaded = {r.id: r for r in db.query(HostTest).options(
        joinedload(HostTest.host), joinedload(HostTest.assigned_to), joinedload(HostTest.created_by),
    ).filter(HostTest.id.in_(ids))}
    results = {}
    for test_id, outcome, finding_id in db.query(
        EvidenceRecord.host_test_id, EvidenceRecord.outcome, EvidenceRecord.finding_id,
    ).filter(EvidenceRecord.host_test_id.in_(ids)).order_by(EvidenceRecord.created_at, EvidenceRecord.id):
        r = results.setdefault(test_id, {"count": 0, "last": None, "open": 0, "finding_ids": []})
        r["count"] += 1
        r["last"] = outcome
        if outcome == "finding" and finding_id is None:
            r["open"] += 1
        if finding_id is not None and finding_id not in r["finding_ids"]:
            r["finding_ids"].append(finding_id)
    result = []
    for test_id in ids:
        row = loaded[test_id]
        data = {c.name: getattr(row, c.name) for c in HostTest.__table__.columns
                if c.name not in ("request_hash", "request_key")}
        r = results.get(row.id, {"count": 0, "last": None, "open": 0, "finding_ids": []})
        data.update(host_ip=row.host.ip_address, evidence_count=r["count"],
                    last_outcome=r["last"], unpromoted_findings=r["open"], finding_ids=r["finding_ids"],
                    assigned_to=host_test_service._display(row.assigned_to),
                    created_by=host_test_service._display(row.created_by))
        result.append(data)
    return result


def _old_list_tests(db, project_id, user_id, *, host_id=None, status=None, label=None,
                    assigned_to_id=None, agent_session_id=None, mine=False, q=None, active_only=False,
                    limit=50, offset=0):
    """``host_test_service.list_tests`` as it stood before C3."""
    query = db.query(HostTest).filter(HostTest.project_id == project_id)
    if active_only:
        query = query.filter(HostTest.status.in_(ACTIVE_TEST_STATUSES))
    for column, value in ((HostTest.host_id, host_id), (HostTest.status, status),
                          (HostTest.label, label), (HostTest.agent_session_id, agent_session_id),
                          (HostTest.assigned_to_id, user_id if mine else assigned_to_id)):
        if value is not None:
            query = query.filter(column == value)
    if q:
        hosts = build_filtered_host_query(db, db.get(User, user_id), project_id=project_id, q=q)
        query = query.filter(HostTest.host_id.in_(hosts.with_entities(models.Host.id)))
    total = query.count()
    rows = query.order_by(HostTest.created_at.desc(), HostTest.id.desc()).offset(offset).limit(limit).all()
    return {"items": _old_serialize_many(db, rows), "total": total, "has_more": offset + len(rows) < total}


@pytest.fixture
def test_estate(db_session, test_project, test_user):
    """Tests chosen so every serialized field takes more than one value: two
    hosts, a second project, a person with a display name and one with only a
    username, tests assigned / unassigned / written by nobody (an agent's),
    every status, equal ``created_at`` values (the id breaks the tie), and
    evidence in every combination the row summarises."""
    pid = test_project.id
    # An explicit id: the fixture user took id 1 without moving the sequence.
    plain = User(id=9063, username="c3-plain",hashed_password="x", role=UserRole.MEMBER, is_active=True)
    db_session.add(plain)
    hosts = []
    for ip in ("10.63.0.1", "10.63.0.2"):
        h = models.Host(project_id=pid, ip_address=ip, state="up", os_name="Linux" if ip.endswith("1") else None)
        db_session.add(h)
        hosts.append(h)
    other = Project(name="c3-other", slug="c3-other")
    db_session.add(other)
    db_session.flush()
    elsewhere = models.Host(project_id=other.id, ip_address="10.63.0.1", state="up")
    db_session.add(elsewhere)
    finding = Finding(project_id=pid, title="c3", severity="high", source="manual")
    db_session.add(finding)
    db_session.flush()

    started = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    statuses = ["proposed", "in_progress", "done", "dismissed"]
    tests = []
    for n in range(14):
        row = HostTest(
            project_id=pid, host_id=hosts[n % 2].id, tool="curl" if n % 3 else None,
            description=f"test {n}", rationale="r", command=f"curl {n}" if n % 2 else None,
            expected_result="200" if n % 4 == 0 else None, references=["https://example.test/a"] if n % 5 == 0 else None,
            priority=["critical", "high", "medium", "low", "info"][n % 5],
            label="web" if n % 3 == 0 else ("smb" if n % 3 == 1 else None),
            issue_key="k:1" if n % 4 == 1 else None, issue_title="An issue" if n % 4 == 1 else None,
            status=statuses[n % 4], source="person" if n % 2 else "agent",
            assigned_to_id=[None, test_user.id, plain.id][n % 3],
            created_by_user_id=[test_user.id, None, plain.id][n % 3],
            tester_summary="looked at it" if n % 4 == 2 else None,
            dismissed_reason="not this engagement" if n % 4 == 3 else None,
            dismissed_by_id=test_user.id if n % 4 == 3 else None,
            request_key=f"c3-{n}", request_hash="h" * 64, revision=1 + n % 3,
            # Pairs share a timestamp: the id decides their order.
            created_at=started + timedelta(minutes=n // 2),
        )
        db_session.add(row)
        tests.append(row)
    db_session.add(HostTest(
        project_id=other.id, host_id=elsewhere.id, description="another project's", rationale="r",
        source="person", request_key="c3-other", request_hash="h" * 64,
    ))
    db_session.flush()

    def evidence(test, outcome, on_finding=False, minutes=0):
        db_session.add(EvidenceRecord(
            project_id=pid, host_id=test.host_id, host_test_id=test.id, tool="curl", outcome=outcome,
            summary="s", finding_id=finding.id if on_finding else None,
            created_at=started + timedelta(hours=1, minutes=minutes),
        ))

    evidence(tests[2], "no_finding")
    evidence(tests[3], "finding")                       # an issue with no finding yet
    evidence(tests[5], "inconclusive", minutes=1)
    evidence(tests[5], "finding", on_finding=True, minutes=2)
    evidence(tests[6], "finding", on_finding=True, minutes=1)
    evidence(tests[6], "finding", minutes=2)
    evidence(tests[6], "failed", minutes=3)            # the latest outcome
    evidence(tests[9], "finding", minutes=5)
    evidence(tests[9], "finding", minutes=5)            # same instant: the id orders them
    db_session.add(EvidenceRecord(project_id=pid, host_id=hosts[0].id, tool="nmap", outcome="info", summary="no test"))
    db_session.flush()
    return pid, hosts, tests, plain


C3_LISTS = [
    {},
    {"limit": 5},
    {"limit": 5, "offset": 5},
    {"limit": 5, "offset": 10},
    {"limit": 3, "offset": 13},
    {"offset": 50},
    {"host": 0},
    {"host": 1, "status": "in_progress"},
    {"status": "done"},
    {"label": "web"},
    {"active_only": True},
    {"active_only": True, "limit": 2, "offset": 1},
    {"mine": True},
    {"assigned_to": "plain"},
    {"q": "os:linux"},
    {"q": "os:linux", "status": "proposed"},
    {"label": "no-such-label"},
]


def _list_kwargs(case, hosts, plain):
    kwargs = dict(case)
    if "host" in kwargs:
        kwargs["host_id"] = hosts[kwargs.pop("host")].id
    if kwargs.pop("assigned_to", None):
        kwargs["assigned_to_id"] = plain.id
    return kwargs


@pytest.mark.parametrize("case", C3_LISTS, ids=lambda c: ",".join(f"{k}={v}" for k, v in c.items()) or "all")
def test_the_host_test_list_says_what_the_two_loads_said(db_session, test_user, test_estate, case):
    pid, hosts, _tests, plain = test_estate
    kwargs = _list_kwargs(case, hosts, plain)

    db_session.expunge_all()
    new = host_test_service.list_tests(db_session, pid, test_user.id, **kwargs)
    db_session.expunge_all()
    old = _old_list_tests(db_session, pid, test_user.id, **kwargs)

    assert new["total"] == old["total"] and new["has_more"] == old["has_more"]
    assert [item["id"] for item in new["items"]] == [item["id"] for item in old["items"]]  # the order
    for mine, theirs in zip(new["items"], old["items"]):
        assert set(mine) == set(theirs)
        for field in theirs:
            assert mine[field] == theirs[field], (mine["id"], field, mine[field], theirs[field])
    if case.get("label") != "no-such-label" and case.get("offset") != 50:
        assert new["items"], "the comparison is not of two empty lists"


def test_the_list_fixture_separates_the_cases(db_session, test_user, test_estate):
    pid, hosts, tests, plain = test_estate
    db_session.expunge_all()
    listing = host_test_service.list_tests(db_session, pid, test_user.id)
    assert listing["total"] == 14 and not listing["has_more"]
    by_id = {item["id"]: item for item in listing["items"]}
    ids = [t.id for t in tests]
    # Newest first; two tests made in the same minute are ordered by id.
    assert [item["id"] for item in listing["items"]] == list(reversed(ids))
    assert {item["assigned_to"] for item in listing["items"]} == {None, "Test Admin", "c3-plain"}
    assert {item["created_by"] for item in listing["items"]} == {None, "Test Admin", "c3-plain"}
    assert {item["host_ip"] for item in listing["items"]} == {"10.63.0.1", "10.63.0.2"}
    six = by_id[ids[6]]
    assert (six["evidence_count"], six["last_outcome"], six["unpromoted_findings"]) == (3, "failed", 1)
    assert len(six["finding_ids"]) == 1
    assert by_id[ids[9]]["unpromoted_findings"] == 2 and by_id[ids[0]]["evidence_count"] == 0
    assert "request_key" not in six and "request_hash" not in six


def test_the_host_test_list_is_three_statements_whatever_the_page(db_session, test_user, test_estate):
    """Count, the page with its host and people joined, the page's evidence.
    It was four: the page was read, then read again by id with the joins."""
    pid, _hosts, _tests, _plain = test_estate

    db_session.expunge_all()
    with statements(db_session) as old:
        _old_list_tests(db_session, pid, test_user.id)
    assert len(old) == 4, old

    for kwargs in ({}, {"limit": 3}, {"limit": 200}):
        db_session.expunge_all()
        with statements(db_session) as seen:
            listing = host_test_service.list_tests(db_session, pid, test_user.id, **kwargs)
        assert listing["items"]
        assert len(seen) == C3_LIST_STATEMENTS, seen
        assert len([s for s in seen if "FROM evidence_records" in s]) == 1


def test_the_route_answers_with_the_same_list(client, db_session, test_estate):
    pid, hosts, _tests, _plain = test_estate
    response = client.get(f"/api/v1/projects/{pid}/host-tests", params={"host_id": hosts[0].id, "limit": 4})
    assert response.status_code == 200, response.text
    body = response.json()
    db_session.expunge_all()
    old = _old_list_tests(db_session, pid, 1, host_id=hosts[0].id, limit=4)
    assert body["total"] == old["total"] == 7 and body["has_more"] is True
    assert [item["id"] for item in body["items"]] == [item["id"] for item in old["items"]]
    for mine, theirs in zip(body["items"], old["items"]):
        for field in ("host_ip", "assigned_to", "created_by", "evidence_count", "last_outcome",
                      "unpromoted_findings", "finding_ids", "status", "revision", "label", "description"):
            assert mine[field] == theirs[field], (field, mine[field], theirs[field])


# Measured on the unchanged code (2.482.0): 4.
C3_LIST_STATEMENTS = 3
