"""Review 2026-10-23b — the agent surface (AGT-1 … AGT-9).

* AGT-1 — a finding's ``evidence_records`` was cut at 50 and the total thrown away.
* AGT-2 — an unknown scan-hosts ``state`` was an empty page on both doors.
* AGT-3 — a list filter naming nothing in the project: the evidence list
  checked ``host_id`` on the agents' door only and nothing else anywhere.
* AGT-4 — the pre-assist reads are gone; ``created_after`` moved onto
  ``/agent/assist/scans`` (its tests: ``test_review_1008_auth_agent.py``,
  ``test_review_2026_10_16_t4.py``).
* AGT-5 — NetExec results: one read for the inspector and the agent.
* AGT-6 — the TLS summary is read once on the server.
* AGT-7 — ``owner=`` / ``author=`` resolved any username and answered 400.
* AGT-8 — the MCP handshake named the session for a key the gate refuses.
* AGT-9 — every proposal create answers with the create acknowledgement.
"""
import ast
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding
from app.db.models_proposals import EvidenceRecord

API = "/api/v1"


def _start(client, project):
    r = client.post(f"{API}/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"X-API-Key": body["api_key"]}, body["agent_session_id"]


def _host(db, project, ip="10.88.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    return host


def _scan(db, project, filename="s.xml"):
    scan = models.Scan(project_id=project.id, filename=filename, tool_name="nmap")
    db.add(scan)
    db.flush()
    return scan


# ---------------------------------------------------------------------- AGT-1

def test_a_findings_evidence_records_say_their_total(client, db_session, test_project, test_user):
    host = _host(db_session, test_project)
    finding = Finding(project_id=test_project.id, title="SMB signing not required", severity="medium",
                      status="confirmed", source="manual", created_by_id=test_user.id)
    db_session.add(finding)
    db_session.flush()
    for i in range(51):
        db_session.add(EvidenceRecord(project_id=test_project.id, host_id=host.id, tool="nxc",
                                      outcome="finding", summary=f"run {i}", finding_id=finding.id))
    db_session.commit()
    headers, _ = _start(client, test_project)

    body = client.get(f"{API}/agent/assist/findings/{finding.id}", headers=headers).json()
    assert len(body["evidence_records"]) == 50
    assert body["evidence_records_total"] == 51          # was absent: 50 read as all
    assert body["evidence_records_truncated"] is True
    # The rest are the paged list's.
    rest = client.get(f"{API}/agent/evidence", headers=headers,
                      params={"finding_id": finding.id, "offset": 50}).json()
    assert rest["total"] == 51 and rest["returned"] == 1


def test_a_short_list_of_evidence_records_is_not_truncated(client, db_session, test_project, test_user):
    host = _host(db_session, test_project)
    finding = Finding(project_id=test_project.id, title="One", severity="low",
                      status="open", source="manual", created_by_id=test_user.id)
    db_session.add(finding)
    db_session.flush()
    db_session.add(EvidenceRecord(project_id=test_project.id, host_id=host.id, tool="nxc",
                                  outcome="finding", summary="s", finding_id=finding.id))
    db_session.commit()
    headers, _ = _start(client, test_project)
    body = client.get(f"{API}/agent/assist/findings/{finding.id}", headers=headers).json()
    assert (body["evidence_records_total"], body["evidence_records_truncated"]) == (1, False)


# ---------------------------------------------------------------------- AGT-2

def _scan_with_states(db, project):
    scan = _scan(db, project)
    for ip, state in (("10.88.1.1", "up"), ("10.88.1.2", "up"), ("10.88.1.3", "down")):
        host = _host(db, project, ip)
        db.add(models.HostScanHistory(host_id=host.id, scan_id=scan.id, state_at_scan=state))
    db.commit()
    return scan


@pytest.mark.parametrize("door", ["agent", "page"])
def test_an_unknown_scan_host_state_is_refused_on_both_doors(client, db_session, test_project, door):
    scan = _scan_with_states(db_session, test_project)
    headers, _ = _start(client, test_project)

    def get(state):
        if door == "agent":
            return client.get(f"{API}/agent/assist/scans/{scan.id}/hosts", headers=headers,
                              params={"state": state})
        return client.get(f"{API}/projects/{test_project.id}/scans/{scan.id}/host-snapshots",
                          params={"state": state})

    refused = get("alive")
    assert refused.status_code == 422, refused.text          # was 200 with no rows
    assert "alive" in refused.text
    up = get("up")
    assert up.status_code == 200, up.text
    assert up.json()["total"] == 2


def test_the_scan_hosts_tool_names_the_states():
    from app.api.v1.endpoints.mcp_tools import tool_list_payload
    from app.services.host_query import HOST_STATES

    (tool,) = [t for t in tool_list_payload() if t["name"] == "assist_list_scan_hosts"]
    assert tool["inputSchema"]["properties"]["state"]["enum"] == list(HOST_STATES)


# ---------------------------------------------------------------------- AGT-3

@pytest.mark.parametrize("name", ["host_id", "finding_id", "host_test_id", "agent_session_id"])
@pytest.mark.parametrize("door", ["agent", "page"])
def test_an_evidence_filter_naming_nothing_here_is_a_404_on_both_doors(
    client, db_session, test_project, name, door,
):
    headers, _ = _start(client, test_project)
    params = {name: 987654}
    if door == "agent":
        r = client.get(f"{API}/agent/evidence", headers=headers, params=params)
    else:
        r = client.get(f"{API}/projects/{test_project.id}/evidence", params=params)
    assert r.status_code == 404, r.text                    # the page said total 0


def test_an_evidence_filter_naming_a_record_here_still_lists(client, db_session, test_project):
    host = _host(db_session, test_project)
    db_session.add(EvidenceRecord(project_id=test_project.id, host_id=host.id, tool="nmap",
                                  outcome="no_finding", summary="s"))
    db_session.commit()
    headers, sid = _start(client, test_project)
    page = client.get(f"{API}/projects/{test_project.id}/evidence", params={"host_id": host.id})
    assert page.status_code == 200 and page.json()["total"] == 1
    own = client.get(f"{API}/agent/evidence", headers=headers, params={"agent_session_id": sid})
    assert own.status_code == 200 and own.json()["total"] == 0


@pytest.mark.parametrize("door", ["agent", "page"])
def test_a_findings_host_filter_naming_nothing_here_is_a_404_on_both_doors(client, db_session, test_project, door):
    headers, _ = _start(client, test_project)
    if door == "agent":
        r = client.get(f"{API}/agent/assist/findings", headers=headers, params={"host_id": 987654})
    else:
        r = client.get(f"{API}/projects/{test_project.id}/findings", params={"host_id": 987654})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------- AGT-4

@pytest.mark.parametrize("path", [
    "/agent/project", "/agent/dashboard", "/agent/hosts", "/agent/hosts/1",
    "/agent/hosts/1/notes", "/agent/scans",
])
def test_the_pre_assist_reads_are_gone(client, test_project, path):
    headers, _ = _start(client, test_project)
    r = client.get(f"{API}{path}", headers=headers)
    assert r.status_code in (404, 405), r.text


def test_no_pre_assist_read_is_routed():
    from app.main import app

    paths = app.openapi()["paths"]
    for path in ("/api/v1/agent/project", "/api/v1/agent/dashboard", "/api/v1/agent/hosts",
                 "/api/v1/agent/scans"):
        assert path not in paths
    assert "get" not in paths.get("/api/v1/agent/hosts/{host_id}", {})
    assert "get" not in paths.get("/api/v1/agent/hosts/{host_id}/notes", {})


# ---------------------------------------------------------------------- AGT-5

def test_the_inspector_and_the_agent_read_the_same_netexec_rows(client, db_session, test_project):
    host = _host(db_session, test_project)
    scan = _scan(db_session, test_project, "nxc.txt")
    # Inserted out of order: the two doors ordered (protocol, id) and
    # (protocol, port, id).
    for protocol, port, user in (("smb", 445, "b"), ("ldap", 389, "a"), ("smb", 139, "c")):
        db_session.add(NetexecResult(scan_id=scan.id, host_id=host.id, protocol=protocol, port=port,
                                     username=user, hostname="DC01", domain_name="corp.example",
                                     auth_success=True, writable_share=False,
                                     raw_output=f"{protocol.upper()} line"))
    db_session.commit()
    headers, _ = _start(client, test_project)

    page = client.get(f"{API}/projects/{test_project.id}/hosts/{host.id}/netexec")
    assert page.status_code == 200, page.text
    agent = client.get(f"{API}/agent/assist/hosts/{host.id}/access", headers=headers)
    assert agent.status_code == 200, agent.text
    assert agent.json()["items"] == page.json()
    assert [(r["protocol"], r["port"]) for r in page.json()] == [("ldap", 389), ("smb", 139), ("smb", 445)]
    row = agent.json()["items"][0]
    assert (row["hostname"], row["domain_name"]) == ("DC01", "corp.example")   # the agent lacked both
    assert row["first_seen"] is not None
    assert page.json()[0]["writable_share"] is False                          # the page lacked it


# ---------------------------------------------------------------------- AGT-6

def test_the_tls_summary_is_the_tool_record_read_once():
    from app.services.cert_fields import tls_summary

    assert tls_summary(None) is None
    assert tls_summary({}) is None
    assert tls_summary({"unrelated": 1}) is None
    record = {
        "version": "tls12", "tls_version": "tls13",           # the first key wins
        "issuer_org": "Example Org", "issuer_dn": "CN=x",       # CN, else org, else DN
        "subject_cn": "app.example.com", "not_after": " 2030-01-01T00:00:00Z ",
        "subject_an": ["a.example.com", "b.example.com"],
        "self_signed": True, "expired": False, "mismatched": True, "wildcard_certificate": True,
    }
    assert tls_summary(record) == {
        "version": "tls13", "issuer": "Example Org", "subject_cn": "app.example.com",
        "not_after": "2030-01-01T00:00:00Z", "sans": ["a.example.com", "b.example.com"],
        "flags": ["self_signed", "hostname_mismatch", "wildcard"],
    }
    # A flag the record does not state as true is not one: no inference.
    assert tls_summary({"expired": "yes", "mismatched": 1}) is None
    assert tls_summary({"subject_alt_names": ["x"]})["sans"] == ["x"]


def test_the_page_and_the_agent_carry_the_same_tls_reading(client, db_session, test_project):
    host = _host(db_session, test_project)
    scan = _scan(db_session, test_project, "httpx.json")
    db_session.add(models.WebInterface(
        project_id=test_project.id, host_id=host.id, scan_id=scan.id, source="httpx",
        url="https://app.example.com", protocol="https", port=443,
        tls_info={"tls_version": "tls12", "issuer_cn": "Example CA", "mismatched": True,
                  "wildcard_certificate": True, "subject_an": ["app.example.com"]},
    ))
    db_session.commit()
    headers, _ = _start(client, test_project)

    (page_row,) = client.get(f"{API}/projects/{test_project.id}/hosts/{host.id}/web-interfaces").json()
    (agent_row,) = client.get(f"{API}/agent/assist/hosts/{host.id}/web-interfaces", headers=headers).json()["items"]
    summary = page_row["tls_summary"]
    assert summary["flags"] == ["hostname_mismatch", "wildcard"]
    assert agent_row["tls_flags"] == summary["flags"]       # the agent had neither flag
    assert agent_row["tls_version"] == summary["version"] == "tls12"
    assert agent_row["cert_issuer"] == summary["issuer"] == "Example CA"
    assert agent_row["cert_sans"] == summary["sans"]


# ---------------------------------------------------------------------- AGT-7

def test_an_owner_or_author_nobody_here_goes_by_is_a_422(client, db_session, test_project):
    stranger = User(id=93011, username="stranger-93011", email="stranger-93011@example.com",
                    hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True)
    db_session.add(stranger)
    db_session.commit()
    headers, _ = _start(client, test_project)
    for path, name in (("/agent/assist/findings", "owner"), ("/agent/assist/notes", "author")):
        # An account of the installation that is not in this project, and a
        # name no account has, get the same answer: nothing about who exists.
        for value in ("stranger-93011", "nobody-at-all"):
            r = client.get(f"{API}{path}", headers=headers, params={name: value})
            assert r.status_code == 422, (path, value, r.text)    # was 400 / 200
            assert value in r.json()["detail"]
        assert client.get(f"{API}{path}", headers=headers, params={name: "me"}).status_code == 200


def test_an_owner_in_the_project_still_filters(client, db_session, test_project, test_user):
    finding = Finding(project_id=test_project.id, title="Owned", severity="low", status="open",
                      source="manual", created_by_id=test_user.id, owner_id=test_user.id)
    db_session.add(finding)
    db_session.commit()
    headers, _ = _start(client, test_project)
    r = client.get(f"{API}/agent/assist/findings", headers=headers,
                   params={"owner": test_user.username.upper()})
    assert r.status_code == 200, r.text
    assert [f["id"] for f in r.json()["items"]] == [finding.id]


# ---------------------------------------------------------------------- AGT-8

def _initialize(client, key, name):
    return client.post(f"{API}/mcp", headers={"X-API-Key": key}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name}},
    })


def _tool(db, sid):
    db.expire_all()
    return db.get(AgentSession, sid).generated_by_tool


def test_the_handshake_names_no_session_for_an_expired_key(client, db_session, test_project):
    headers, sid = _start(client, test_project)
    key = db_session.query(APIKey).filter(APIKey.agent_session_id == sid).one()
    key.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    r = _initialize(client, headers["X-API-Key"], "expired-client")
    assert r.status_code == 200 and "result" in r.json(), r.text      # the handshake still succeeds
    assert _tool(db_session, sid) != "expired-client"                 # was renamed


def test_the_handshake_names_no_session_after_a_password_change(client, db_session, test_project, test_user):
    headers, sid = _start(client, test_project)
    test_user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    db_session.commit()
    assert _initialize(client, headers["X-API-Key"], "old-password-client").status_code == 200
    assert _tool(db_session, sid) != "old-password-client"


def test_the_handshake_still_names_a_live_session(client, db_session, test_project):
    headers, sid = _start(client, test_project)
    assert _initialize(client, headers["X-API-Key"], "live-client").status_code == 200
    assert _tool(db_session, sid) == "live-client"


def test_the_handshakes_rule_is_the_gates(db_session, test_project):
    from app.api import deps

    assert deps.agent_key_may_act(None) is False
    src = inspect.getsource(deps.agent_key_may_act)
    for predicate in ("_key_expired", "_issued_before_password_change", "membership_role", "operator_active"):
        assert predicate in src


# ---------------------------------------------------------------------- AGT-9

def test_every_proposal_create_answers_with_the_acknowledgement():
    from app.api.v1.endpoints import agent_proposals

    tree = ast.parse(inspect.getsource(agent_proposals))
    creates = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "post"
                    and isinstance(dec.args[0], ast.Constant) and dec.args[0].value.startswith("/proposals")
                    and "{" not in dec.args[0].value):
                creates.append(fn)
    assert len(creates) >= 5
    for fn in creates:
        called = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "serialize_created" in called, fn.name
        assert "serialize_many" not in called, fn.name
