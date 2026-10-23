"""Review 2026-10-23, inventory reads (build E).

* H-INV1 — "active finding" has ONE definition
  (``engagement_metrics_service.finding_is_active``): Posture's active counts
  and the Findings list's ``status=active`` group (both doors) agree, and a
  finding judged a false positive on every endpoint is in neither.
"""
from __future__ import annotations

import pytest

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.services import agent_session_service as sessions
from app.services.posture_service import compute_posture


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding(db, project_id, title, *, severity, status="open", source="manual", endpoints=()):
    f = Finding(project_id=project_id, title=title, severity=severity, status=status, source=source)
    db.add(f)
    db.flush()
    for host, state in endpoints:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=state))
    db.flush()
    return f


@pytest.fixture()
def agent_headers(db_session, test_project):
    user = User(
        id=93101, username="operator-93101", email="operator-93101@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value,
    ))
    db_session.commit()
    agent = sessions.resolve_project_agent(db_session, project_id=test_project.id, user=user)
    session = sessions.create_agent_session(
        db_session, project_id=test_project.id, agent_id=agent.id, started_by_id=user.id,
    )
    raw = sessions.mint_session_key(db_session, agent=agent, session=session)
    db_session.commit()
    return {"X-API-Key": raw}


def _seed_findings(db, project_id):
    a = _host(db, project_id, "10.231.0.1")
    b = _host(db, project_id, "10.231.0.2")
    # Active and a result.
    _finding(db, project_id, "crit real", severity="critical", endpoints=[(a, "open")])
    _finding(db, project_id, "high half fp", severity="high", status="confirmed",
             endpoints=[(a, "false_positive"), (b, "open")])
    _finding(db, project_id, "medium no endpoint", severity="medium")
    _finding(db, project_id, "high scanner", severity="high", source="scanner", endpoints=[(b, "open")])
    # Open by status, but a false positive on EVERY endpoint: not a result.
    _finding(db, project_id, "crit all fp", severity="critical",
             endpoints=[(a, "false_positive"), (b, "false_positive")])
    _finding(db, project_id, "high scanner all fp", severity="high", source="scanner",
             endpoints=[(a, "false_positive")])
    # Not active by status.
    _finding(db, project_id, "low closed", severity="low", status="remediated", endpoints=[(a, "remediated")])
    db.commit()


def test_posture_active_counts_are_the_active_lists_totals(client, db_session, test_project, agent_headers):
    _seed_findings(db_session, test_project.id)
    out = compute_posture(db_session, test_project.id, use_cache=False)
    exposure = out["headline"]["active_exposure"]
    base = f"/api/v1/projects/{test_project.id}/findings"

    whole = client.get(base, params={"status": "active"}).json()
    assert exposure["active_findings"] == whole["total"] == 4
    titles = {f["title"] for f in whole["items"]}
    assert "crit all fp" not in titles and "high scanner all fp" not in titles

    for sev in ("critical", "high", "medium", "low", "info"):
        page = client.get(base, params={"status": "active", "severity": sev}).json()
        assert exposure["by_severity"][sev] == page["total"], sev
        agent = client.get(
            "/api/v1/agent/assist/findings", params={"status": "active", "severity": sev},
            headers=agent_headers,
        ).json()
        assert agent["total"] == page["total"], sev

    d = out["disposition"]
    scanner = client.get(base, params={"status": "active", "source": "scanner"}).json()
    assert d["scanner_active"] == scanner["total"] == 1
    assert d["scanner_active"] + d["non_scanner_active"] == d["active_total"] == exposure["active_findings"]
    # Ownership is the same selection (every seeded finding is unowned).
    assert out["headline"]["ownership"]["unowned"] == whole["total"]


# --- R-FE2 + the list's ports: the server marks the ports a row matched -------

def _matched_estate(db, project_id):
    """frontend/src/tests/utils/endpointMatch.test.ts's ports, on one host,
    plus a full-range sweep's worth of filtered ports on it."""
    h = _host(db, project_id, "10.234.0.1")
    for number, state, name, product, version in (
        (21, "open", "ftp", "vsftpd", "3.0.3"),
        (2121, "filtered", "ccproxy-ftp", None, None),
        (22, "open", "ssh", "OpenSSH", "7.4"),
        (23, "open", "telnet", None, None),
    ):
        db.add(models.Port(host_id=h.id, port_number=number, protocol="tcp", state=state,
                           service_name=name, service_product=product, service_version=version))
    for number in range(30000, 30200):
        db.add(models.Port(host_id=h.id, port_number=number, protocol="tcp", state="filtered"))
    db.commit()
    return h


def _row(client, project_id, **params):
    r = client.get(f"/api/v1/projects/{project_id}/hosts/", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = [h for h in body["items"] if h["ip_address"] == "10.234.0.1"]
    if not rows:
        return None, body
    row = rows[0]
    by_id = {p["id"]: p for p in row["ports"]}
    matched = [(by_id[i]["port_number"], by_id[i]["state"]) for i in row["matched_port_ids"]]
    return {"ports": row["ports"], "matched": matched}, body


# (filters, matched ports as (number, state)) — the frontend's endpointMatch
# cases, old == new, then the two its lexer read differently from the server.
MATCH_CASES = [
    ({"q": "tag:dmz"}, None),                               # nothing names a port: host not listed
    ({"has_open_ports": True}, []),
    ({"services": "ftp"}, [(21, "open")]),
    ({"services": "ftp", "port_states": "filtered"}, [(2121, "filtered")]),
    ({"services": "ftp", "port_states": "any"}, [(21, "open"), (2121, "filtered")]),
    ({"services": "ftp", "port_states": "open,filtered"}, [(21, "open"), (2121, "filtered")]),
    ({"q": "service:ftp"}, [(21, "open")]),
    ({"q": "port:2121@filtered"}, [(2121, "filtered")]),
    ({"q": "service:ftp@any"}, [(21, "open"), (2121, "filtered")]),
    ({"q": "port:2121@any,23"}, [(23, "open"), (2121, "filtered")]),
    ({"q": "port:23"}, [(23, "open")]),
    ({"q": "svc:ssh AND NOT service:telnet@filtered"}, [(22, "open")]),
    ({"q": "NOT (port:24 OR port:25) port:23"}, [(23, "open")]),
    ({"q": 'version:"OpenSSH 7"'}, [(22, "open")]),
    # (The page's test wrote this with a bare `NOT` after a term, which its
    # lexer took and the server's grammar refuses — "Unexpected 'NOT'".)
    ({"q": 'service:"ms-wbt-server",ftp AND NOT port:3389@any'}, [(21, "open")]),
    # The page's lexer read "port:" with nothing after it here and matched
    # nothing; the server's grammar reads `port: 22` as port:22.
    ({"q": "port: 22"}, [(22, "open")]),
    # A state after a closing quote is not part of the value to the server
    # (`@filtered` is a word of its own, which matches no host); the page's
    # lexer read it as service ccproxy@filtered and would have marked 2121.
    # Inside the quotes it is the state.
    ({"q": 'service:"ccproxy"@filtered'}, None),
    ({"q": 'service:"ccproxy@filtered"'}, [(2121, "filtered")]),
]


@pytest.mark.parametrize("filters,expected", MATCH_CASES)
def test_the_list_marks_the_ports_each_condition_matched(client, db_session, test_project, filters, expected):
    _matched_estate(db_session, test_project.id)
    row, _body = _row(client, test_project.id, **filters)
    if expected is None:
        assert row is None
        return
    assert row is not None
    assert sorted(row["matched"]) == sorted(expected)


def test_a_row_carries_its_open_and_matched_ports_not_every_stored_port(client, db_session, test_project):
    _matched_estate(db_session, test_project.id)
    row, body = _row(client, test_project.id)
    assert sorted(p["port_number"] for p in row["ports"]) == [21, 22, 23]
    assert row["matched"] == [] and body["match_by_product"] is False
    row, body = _row(client, test_project.id, q="port:2121@filtered")
    assert sorted(p["port_number"] for p in row["ports"]) == [21, 22, 23, 2121]
    row, body = _row(client, test_project.id, q='version:"OpenSSH"')
    assert body["match_by_product"] is True


# --- Nits -------------------------------------------------------------------

def test_the_scan_host_list_refuses_an_unknown_state(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="s.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}"
    assert client.get(base, params={"state": "up"}).status_code == 200
    r = client.get(base, params={"state": "upp"})
    assert r.status_code == 422 and "upp" in r.text


def test_has_notes_is_the_host_notes_untouched_reads(client, db_session, test_project, test_user):
    """A note on a PORT has no writer; ``has:notes`` matched one while
    ``has:untouched`` ignored it, so a host could be both."""
    noted = _host(db_session, test_project.id, "10.232.0.1")
    port_only = _host(db_session, test_project.id, "10.232.0.2")
    port = models.Port(host_id=port_only.id, port_number=22, protocol="tcp", state="open")
    db_session.add(port)
    db_session.flush()
    db_session.add(models.Annotation(host_id=noted.id, user_id=test_user.id, body="n"))
    db_session.add(models.Annotation(port_id=port.id, user_id=test_user.id, body="legacy port note"))
    db_session.commit()

    def ips(q):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q})
        assert r.status_code == 200, r.text
        return {h["ip_address"] for h in r.json()["items"]}

    assert ips("has:notes") == {"10.232.0.1"}
    assert ips("has:untouched") == {"10.232.0.2"}
    assert not ips("has:notes") & ips("has:untouched")


def test_conflict_history_breaks_ties_by_id(db_session, test_project):
    from datetime import datetime, timezone

    from app.db.models_confidence import ConflictHistory
    from app.services.host_detail_service import host_conflict_counts, host_conflict_history

    host = _host(db_session, test_project.id, "10.233.0.1")
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ids = []
    for n in range(5):
        row = ConflictHistory(host_id=host.id, field_name="hostname", previous_value=f"a{n}",
                              new_value=f"b{n}", resolved_at=at)
        db_session.add(row)
        db_session.flush()
        ids.append(row.id)
    db_session.commit()
    listed = host_conflict_history(db_session, host, limit=3)
    # Newest first; in one instant the latest row first — the cap keeps the
    # same rows on every read.
    assert [c["id"] for c in listed] == sorted(ids, reverse=True)[:3]
    # The badge counts every host-level row; the pane lists the newest under
    # its cap and says how many it left out (HostConflictsPanel's `unlisted`).
    assert host_conflict_counts(db_session, [host.id])[host.id] == 5
