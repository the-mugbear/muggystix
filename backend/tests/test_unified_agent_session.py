"""End-to-end proof of the v2.337.0 unified agent session.

One project-scoped session + key does every kind of work — query, read a
scope, propose tests on hosts, record what it ran — with nothing to open
first.  This replaces the four per-workflow entry points (assist / recon /
plan generation / execution) whose isolation these tests' predecessors pinned;
since v2.442.0 there are no plans or execution runs either.
"""
import uuid

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord


def _scope_with_subnet(db, project):
    scope = models.Scope(project_id=project.id, name="s1", description="")
    db.add(scope)
    db.flush()
    db.add(models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24"))
    db.commit()
    return scope


def _start_session(client, project):
    """Mint a unified session key via the (repointed) assist-start entry point."""
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "everything"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["api_key"], body["agent_session_id"]


def _session_id(db, started_id):
    """The session id IS the id the start returned (v2.449.0)."""
    return started_id


def _hdr(key):
    return {"X-API-Key": key}


def _host(db, project, ip="10.0.0.5"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _spec(host, **extra):
    return dict(request_key=str(uuid.uuid4()), host_id=host.id, tool="nmap",
                description="Service detection", rationale="Open ports, no versions", **extra)


def test_one_key_reaches_identity_scope_and_host_tests(client, test_project, db_session):
    key, assist_session_id = _start_session(client, test_project)
    # The start endpoint returns the AgentSession id — the session's one id.
    session_id = _session_id(db_session, assist_session_id)

    # The session is a PROJECT session and the key resolves to it.
    row = db_session.query(AgentSession).filter(AgentSession.id == session_id).first()
    assert row is not None and row.workflow == "project"

    # identity: one key, project-scoped.
    r = client.get("/api/v1/agent/identity", headers=_hdr(key))
    assert r.status_code == 200, r.text
    ident = r.json()
    assert "workflow" not in ident
    assert ident["project_id"] == test_project.id
    assert ident["session_id"] == session_id
    assert ident["can_write_project_data"] is True  # admin operator

    # read a scope — same key, no new credential.
    scope = _scope_with_subnet(db_session, test_project)
    r = client.get(f"/api/v1/agent/scopes/{scope.id}/subnets", headers=_hdr(key))
    assert r.status_code == 200, r.text
    assert "10.0.0.0/24" in r.json()["subnets"]

    # the SAME key proposes a test on a host, attributed to this session.
    host = _host(db_session, test_project)
    r = client.post("/api/v1/agent/host-tests", headers=_hdr(key), json={"tests": [_spec(host)]})
    assert r.status_code == 201, r.text
    test = db_session.get(HostTest, r.json()["items"][0]["id"])
    assert test.agent_session_id == session_id and test.source == "agent"


def test_mcp_guidance_describes_the_unified_session():
    """The MCP welcome text must not revive the retired assist-only model."""
    from app.api.v1.endpoints.mcp_assist import _server_instructions

    guidance = _server_instructions("https://127.0.0.1/api/v1")
    assert "one project session" in guidance
    assert "no key that does all four" not in guidance
    # v2.433.0 — no rails in the welcome text.
    for retired in ("human-approved", "list_approved_tools", "approved"):
        assert retired not in guidance


def test_agent_guide_does_not_describe_inventory_assistance_as_a_separate_session():
    """The guide is part of the MCP contract, not optional supporting copy."""
    # The suite runs inside the backend container, where the guide is
    # bind-mounted at /app/AGENT_GUIDE.md — not at the documentation/ path a
    # local checkout would give.  Reuse the docs-contract loader, which knows both.
    from tests.test_docs_contract import _load_agents_md

    guide = _load_agents_md()
    assert "You are in an **assist session**" not in guide
    assert "Scanning, plan creation, and execution are refused for every assist session" not in guide


def test_an_agent_works_its_own_test_with_no_approval_step(client, test_project, db_session):
    """No approval gate (v2.433.0) and nothing to open (v2.442.0): a test the
    agent proposes is on the host at once, and the same key takes it through
    in_progress → evidence → done.  A person sees it without accepting
    anything."""
    key, _ = _start_session(client, test_project)
    host = _host(db_session, test_project)

    r = client.post("/api/v1/agent/host-tests", headers=_hdr(key),
                    json={"tests": [_spec(host, command="nmap -sV {ip}")]})
    assert r.status_code == 201, r.text
    test = r.json()["items"][0]
    assert test["status"] == "proposed"

    # The operator's own view lists it straight away.
    seen = client.get(f"/api/v1/projects/{test_project.id}/host-tests", params={"host_id": host.id})
    assert [t["id"] for t in seen.json()["items"]] == [test["id"]]

    url = f"/api/v1/agent/host-tests/{test['id']}"
    r = client.patch(url, headers=_hdr(key),
                     json={"expected_revision": test["revision"], "status": "in_progress"})
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v1/agent/evidence", headers=_hdr(key), json={
        "host_id": host.id, "host_test_id": test["id"], "request_key": "run-1",
        "tool": "nmap", "command": "nmap -sV 10.0.0.5", "outcome": "no_finding",
        "summary": "Only the expected services answer",
    })
    assert r2.status_code == 201, r2.text
    done = client.patch(url, headers=_hdr(key),
                        json={"expected_revision": r.json()["revision"], "status": "done"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done" and done.json()["evidence_count"] == 1


def test_timeline_shows_one_row_per_session_whatever_it_did(client, test_project, db_session):
    """v2.337.0 — a project session appears ONCE on the unified timeline.  The
    work it did (tests proposed, evidence recorded) is counted on that row;
    it never becomes rows of its own, and the detail row the start endpoint
    made is not listed beside it."""
    key, assist_session_id = _start_session(client, test_project)
    base_id = _session_id(db_session, assist_session_id)
    host = _host(db_session, test_project)
    test = client.post("/api/v1/agent/host-tests", headers=_hdr(key),
                       json={"tests": [_spec(host)]}).json()["items"][0]
    r = client.post("/api/v1/agent/evidence", headers=_hdr(key), json={
        "host_id": host.id, "host_test_id": test["id"], "request_key": "tl-1",
        "tool": "nmap", "outcome": "finding", "summary": "Telnet is open",
    })
    assert r.status_code == 201, r.text
    assert db_session.query(EvidenceRecord).count() == 1

    listing = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    rows = listing["sessions"]
    assert listing["total"] == len(rows) == 1, rows
    assert rows[0]["kind"] == "project" and rows[0]["id"] == base_id
    assert rows[0]["host_test_count"] == 1 and rows[0]["evidence_count"] == 1
