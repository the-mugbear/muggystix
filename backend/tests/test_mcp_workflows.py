"""MCP across the kinds of agent work (v2.278.0; one session since v2.337.0).

Before this, MCP covered the assist surface only: an operator running recon or
testing hosts had an MCP client connected to a server that offered them
nothing they could use.

What these tests pin: one session lists the whole catalogue; hiding or listing
a tool is presentation, not authorisation (the endpoint still decides); and
the testing loop — propose tests on hosts, run one, record its evidence —
works end to end as tool calls.

v2.442.0 — test plans and execution runs are gone, and with them the
``plan_*`` / ``execution_*`` tools and the ``plan_id`` the server filled in
from the key (``test_plan_id_is_filled_from_the_key``,
``test_an_explicit_argument_beats_the_auto_filled_one``: no tool has a plan to
address).  The one auto-filled argument left is the guide's ``workflow``.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def scope_with_subnets(db_session, test_project):
    from app.db.models import Scope, Subnet

    scope = Scope(name="mcp-recon-scope", description="fixture", project_id=test_project.id)
    db_session.add(scope)
    db_session.commit()
    db_session.refresh(scope)
    db_session.add_all([
        Subnet(scope_id=scope.id, cidr="10.77.1.0/24", description="first"),
        Subnet(scope_id=scope.id, cidr="10.77.2.0/24", description="second"),
    ])
    db_session.commit()
    return scope


def _rpc(client, body, headers=None):
    return client.post("/api/v1/mcp", json=body, headers=headers or {})


def _tool_names(client, headers=None):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {t["name"] for t in resp.json()["result"]["tools"]}


def _call(client, headers, name, arguments=None):
    resp = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _plan_key(client, test_project, title="MCP plan"):
    """A project session.  (It used to register a plan too; the name is kept so
    the multi-session tests below still read as "several sessions".)"""
    return _assist_key(client, test_project)


def _recon_key(client, test_project, scope):
    """v2.433.0 — recon runs are gone; this is just a project session. Kept
    under the old name so the multi-session tests below still read clearly."""
    return _assist_key(client, test_project)


def _assist_key(client, test_project):
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "mcp workflow test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Identity — the lookup the whole split rests on
# ---------------------------------------------------------------------------

def test_identity_classifies_each_workflow_key(client, test_project, scope_with_subnets):
    """A caller cannot pick the right tools without knowing what its key is, and
    every *other* introspection route is behind the workflow it describes — so
    classifying an unknown key meant probing surfaces until one stopped saying
    403, writing audit noise into whichever guess was wrong."""
    plan = _plan_key(client, test_project)
    recon = _recon_key(client, test_project, scope_with_subnets)
    assist = _assist_key(client, test_project)

    def identity(api_key):
        resp = client.get("/api/v1/agent/identity", headers={"X-API-Key": api_key})
        assert resp.status_code == 200, resp.text
        return resp.json()

    # v2.337.0 — every start mints one project session.  v2.442.0 — and a
    # session has no phases: identity names the session, nothing it "opened".
    plan_id = identity(plan["api_key"])
    assert plan_id["workflow"] == "project"
    assert plan_id["session_id"] == plan["agent_session_id"]
    for retired in ("open_phases", "plan_id", "execution_session_id"):
        assert retired not in plan_id

    recon_id = identity(recon["api_key"])
    assert recon_id["workflow"] == "project"
    assert recon_id["session_id"] == recon["agent_session_id"] != plan_id["session_id"]

    assist_id = identity(assist["api_key"])
    assert assist_id["workflow"] == "project"
    assert assist_id["project_id"] == test_project.id


def test_identity_needs_a_key(client):
    assert client.get("/api/v1/agent/identity").status_code == 401


# ---------------------------------------------------------------------------
# tools/list is scoped to the caller's workflow
# ---------------------------------------------------------------------------

def test_each_key_sees_only_its_own_workflows_tools(
    client, test_project, scope_with_subnets
):
    """Three entry points, one endpoint. A recon agent offered plan_submit would
    try it and read the 403 as its own mistake."""
    plan = _plan_key(client, test_project)
    recon = _recon_key(client, test_project, scope_with_subnets)
    assist = _assist_key(client, test_project)

    # v2.337.0 — one project session does everything, so every key lists the
    # FULL catalogue (testing + scope + assist). Whether a call succeeds is
    # the operator's role, decided at the endpoint — the list is presentation.
    for body in (plan, recon, assist):
        tools = _tool_names(client, {"X-API-Key": body["api_key"]})
        assert {"host_tests_propose", "host_tests_list", "host_tests_get",
                "host_tests_update", "record_evidence", "scope_list_subnets",
                "assist_list_hosts", "agent_identity", "suggest_tool"} <= tools
        assert "start_recon" not in tools
        # v2.442.0 — no plan or execution-run tool survives under any name.
        assert not [t for t in tools if t.startswith(("plan_", "execution_"))]
        assert not {"create_test_plan", "start_execution"} & tools


def test_unauthenticated_list_is_the_documentation_view(client):
    """No key means no workflow to filter by, so discovery shows everything —
    degrading to an empty list would make the server look broken to a client
    that hasn't been given a key yet."""
    tools = _tool_names(client)
    assert {"assist_list_hosts", "host_tests_propose", "scope_list_subnets",
            "list_evidence", "get_upload_job"} <= tools
    assert "plan_submit" not in tools  # v2.433.0 — no approval step
    assert "plan_add_entries" not in tools  # v2.442.0 — no plans


def test_hiding_a_tool_is_presentation_not_authorisation(
    client, test_project, scope_with_subnets
):
    """The MCP layer makes no security decision — it forwards the key and the
    real endpoint decides. If a model calls a tool its client never listed, it
    must still hit the endpoint's own 403 rather than a permissive shortcut."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    headers = {"X-API-Key": recon["api_key"]}

    # Every tool is listed now; the endpoint is still the decider. Reading a
    # host test that does not exist reaches the real route and fails there.
    result = _call(client, headers, "host_tests_get", {"test_id": 999999})
    assert result["isError"] is True


def test_a_listed_write_tool_still_answers_with_the_operators_role(
    client, db_session, test_project, test_user
):
    """``host_tests_propose`` is listed for every session; an auditor's session
    calling it gets the endpoint's 403 and nothing is written."""
    from app.api.v1.endpoints.auth import get_current_user
    from app.db.models import Host
    from app.db.models_auth import User, UserRole
    from app.db.models_host_tests import HostTest
    from app.db.models_project import ProjectMembership, ProjectRole
    from app.main import app

    host = Host(ip_address="10.77.1.20", project_id=test_project.id, state="up")
    auditor = User(id=4343, username="mcp-auditor", email="mcp-auditor@example.com",
                   hashed_password="x", role=UserRole.MEMBER)
    db_session.add_all([host, auditor])
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=auditor.id,
                                     role=ProjectRole.AUDITOR.value))
    db_session.commit()

    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: auditor
    try:
        session = _assist_key(client, test_project)
    finally:
        app.dependency_overrides[get_current_user] = original
    headers = {"X-API-Key": session["api_key"]}
    assert "host_tests_propose" in _tool_names(client, headers)

    result = _call(client, headers, "host_tests_propose", {"tests": [{
        "request_key": "auditor-1", "host_id": host.id, "tool": "nmap",
        "description": "d", "rationale": "r",
    }]})
    assert result["isError"] is True, result
    assert db_session.query(HostTest).count() == 0
    # The read is the auditor's to make.
    assert _call(client, headers, "host_tests_list")["isError"] is False


# ---------------------------------------------------------------------------
# Testing over MCP, end to end
# ---------------------------------------------------------------------------

def test_testing_workflow_over_mcp(client, test_project, db_session):
    """The loop an agent actually runs: see what is already proposed, propose
    tests on hosts, take one, record what it produced, close it — every step a
    tool call, no curl, and nothing waits on approval."""
    from app.db.models import Host
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    host = Host(ip_address="10.77.1.10", project_id=test_project.id, state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    session = _assist_key(client, test_project)
    headers = {"X-API-Key": session["api_key"]}

    before = _call(client, headers, "host_tests_list", {"host_id": host.id})
    assert before["isError"] is False, before
    assert before["structuredContent"]["total"] == 0

    spec = {
        "request_key": "mcp-ftp-1",
        "host_id": host.id,
        "tool": "nmap",
        "description": "Confirm the FTP service and its version.",
        "command": "nmap -p21 -sV -oX ftp.xml {ip}",
        "rationale": "FTP banner suggests an anonymous-login check is worth doing.",
        "priority": "high",
        "label": "FTP review",
    }
    proposed = _call(client, headers, "host_tests_propose",
                     {"tests": [spec], "agent_model": "test-model"})
    assert proposed["isError"] is False, proposed
    test = proposed["structuredContent"]["items"][0]
    assert (test["status"], test["source"], test["label"]) == ("proposed", "agent", "FTP review")
    assert test["agent_session_id"] == session["agent_session_id"]

    # A retry with the same key and content is the same test, not a second one.
    again = _call(client, headers, "host_tests_propose", {"tests": [spec]})
    assert again["structuredContent"]["items"][0]["id"] == test["id"]

    got = _call(client, headers, "host_tests_get", {"test_id": test["id"]})
    assert got["structuredContent"]["command"] == spec["command"]

    taken = _call(client, headers, "host_tests_update", {
        "test_id": test["id"], "expected_revision": test["revision"], "status": "in_progress",
    })
    assert taken["isError"] is False, taken
    revision = taken["structuredContent"]["revision"]
    assert revision == test["revision"] + 1

    # A write from the revision read before the claim is stale.
    stale = _call(client, headers, "host_tests_update", {
        "test_id": test["id"], "expected_revision": test["revision"], "status": "done",
    })
    assert stale["isError"] is True

    recorded = _call(client, headers, "record_evidence", {
        "host_id": host.id, "host_test_id": test["id"], "request_key": "mcp-ftp-1-run",
        "tool": "nmap", "command": "nmap -p21 -sV -oX ftp.xml 10.77.1.10",
        "outcome": "no_finding", "summary": "vsftpd 3.0.5, anonymous login refused",
    })
    assert recorded["isError"] is False, recorded

    listed = _call(client, headers, "list_evidence", {"host_test_id": test["id"]})
    assert [e["id"] for e in listed["structuredContent"]["items"]] == [
        recorded["structuredContent"]["id"]
    ]

    done = _call(client, headers, "host_tests_update", {
        "test_id": test["id"], "expected_revision": revision, "status": "done",
    })
    assert done["isError"] is False, done

    db_session.expire_all()
    stored = db_session.query(HostTest).one()
    assert (stored.status, stored.host_id, stored.agent_model) == ("done", host.id, "test-model")
    assert db_session.query(EvidenceRecord).filter_by(host_test_id=stored.id).count() == 1
    # The person's page reads the same row.
    row = client.get(f"/api/v1/projects/{test_project.id}/host-tests", params={"host_id": host.id}).json()
    assert [(t["id"], t["status"], t["evidence_count"]) for t in row["items"]] == [(stored.id, "done", 1)]


def test_the_guide_is_reachable_over_mcp_and_sliced_to_the_caller(
    client, test_project, scope_with_subnets
):
    """AGENTS.md is called binding by the prompts, and an MCP-only agent used to
    have no way to reach it: the guide pointer lives in the instructions block an
    operator pastes, which a client wired up purely over MCP never sees."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    plan = _plan_key(client, test_project)

    recon_guide = _call(client, {"X-API-Key": recon["api_key"]}, "read_agent_guide")
    assert recon_guide["isError"] is False, recon_guide
    recon_text = recon_guide["content"][0]["text"]

    plan_guide = _call(client, {"X-API-Key": plan["api_key"]}, "read_agent_guide")
    plan_text = plan_guide["content"][0]["text"]

    # v2.337.0 — a project session does every kind of work, so it gets the WHOLE
    # guide, not one slice.
    for text in (recon_text, plan_text):
        assert "Say the rules back before you start" in text
        # The whole guide: the reconnaissance-only and assist-only sections
        # are both in it.
        assert "/agent/uploads" in text and "/agent/assist/" in text
    assert recon_text == plan_text


def test_an_explicit_guide_slice_beats_the_auto_filled_one(client, test_project):
    """Auto-fill is a default, not an override: the session's ``project``
    workflow means "the whole guide", and naming a slice gets that slice."""
    from app.services.agents_guide_service import read_agent_guide, slice_agents_md

    full_text = read_agent_guide()
    if full_text is None:
        pytest.skip("the agent guide is not mounted in this environment")
    session = _assist_key(client, test_project)
    headers = {"X-API-Key": session["api_key"]}

    sliced = _call(client, headers, "read_agent_guide", {"workflow": "assist"})
    assert sliced["isError"] is False, sliced
    # The served text stamps the prompt version into the header, so compare
    # the slice by size rather than byte for byte.
    expected = slice_agents_md(full_text, "assist")
    assert abs(len(sliced["content"][0]["text"]) - len(expected)) < 40
    assert len(expected) < len(full_text)
    whole = _call(client, headers, "read_agent_guide")["content"][0]["text"]
    assert len(whole) > len(sliced["content"][0]["text"])
    # A retired slice name is not an advertised value any more.
    refused = _rpc(client, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
        "name": "read_agent_guide", "arguments": {"workflow": "plan_generation"}}}, headers=headers)
    assert "error" in refused.json() or refused.json()["result"]["isError"] is True


def test_the_tool_catalogue_is_readable_over_mcp(
    client, test_project, scope_with_subnets, db_session
):
    """v2.433.0 — list_tools is a catalogue, not a permission list: unfiltered
    by default, every status one of the catalogue states."""
    from app.services import tool_registry_service as registry

    registry.seed_registry(db_session)
    recon = _recon_key(client, test_project, scope_with_subnets)
    headers = {"X-API-Key": recon["api_key"]}

    names = _tool_names(client, headers)
    assert "list_tools" in names and "list_approved_tools" not in names

    result = _call(client, headers, "list_tools")
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["count"] > 0
    assert {t["status"] for t in body["tools"]} <= {"reference", "suggested", "rejected"}
    assert "nmap" in {t["name"] for t in body["tools"]}

    reference = _call(client, headers, "list_tools", {"status": "reference"})
    assert {t["status"] for t in reference["structuredContent"]["tools"]} == {"reference"}
    assert "nmap" in {t["name"] for t in reference["structuredContent"]["tools"]}


# ---------------------------------------------------------------------------
# Connecting a client to a non-assist session
# ---------------------------------------------------------------------------

def test_every_session_start_emits_client_setup(client, test_project, scope_with_subnets):
    """The tools exist for all four workflows; the connection recipe used to
    exist for one. An operator starting recon was handed a curl block and left
    to work out the client config themselves."""
    plan = _plan_key(client, test_project)
    recon = _recon_key(client, test_project, scope_with_subnets)

    for body in (plan, recon):
        assert body["mcp_url"].endswith("/mcp")
        ids = {c["id"] for c in body["mcp_clients"]}
        assert ids == {"vscode", "claude_code", "codex"}

    # v2.337.0 — one unified server entry ("bluestick"): a session does every
    # workflow, so there is no per-workflow server to disambiguate.
    plan_payloads = " ".join(c["payload"] for c in plan["mcp_clients"])
    recon_payloads = " ".join(c["payload"] for c in recon["mcp_clients"])
    assert "bluestick" in plan_payloads and "bluestick-plan" not in plan_payloads
    assert "bluestick" in recon_payloads and "bluestick-recon" not in recon_payloads

    # And each carries its own live key, not a shared one.
    assert plan["api_key"] in plan_payloads
    assert recon["api_key"] in recon_payloads


def test_every_recipe_carries_the_verification_handoff(
    client, test_project, scope_with_subnets
):
    """The recipe used to end at "config installed". Nothing said how to find
    out whether it worked, and the two signals a client offers both mislead:
    a server can be REGISTERED with a dead key, and tools/list succeeds without
    a key by design. The only proof is an authenticated tool call, so every
    recipe now ends with the prompt that makes one — naming the server, and
    the project + session it should answer with — plus the client's own
    "connected" check, which differs per client (Codex's `mcp list` is only
    "configured")."""
    plan = _plan_key(client, test_project)
    recon = _recon_key(client, test_project, scope_with_subnets)
    assist = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "verify"}
    ).json()

    for body in (plan, recon, assist):
        for entry in body["mcp_clients"]:
            prompt = entry["verify_prompt"]
            assert "bluestick" in prompt, entry["id"]
            assert "agent_identity" in prompt
            # The failure mode this exists to prevent: a model with no tools
            # answering from general knowledge and reading as connected.
            assert "not available" in prompt
            expected = entry["verify_expected"]
            assert test_project.name in expected
            assert "session #" in expected, (entry["id"], expected)
            assert entry["verify_check"], entry["id"]
            assert "bluestick" in entry["verify_check"]

    by_id = {e["id"]: e for e in assist["mcp_clients"]}
    # Codex is the client whose obvious check proves the least.
    assert "CONFIGURED" in by_id["codex"]["verify_check"]
    assert "/mcp" in by_id["codex"]["verify_check"]
    assert "claude mcp list" in by_id["claude_code"]["verify_check"]
    assert "List Servers" in by_id["vscode"]["verify_check"]


def test_sandbox_guidance_rides_with_the_workflows_that_run_commands(
    client, test_project, scope_with_subnets
):
    """The working-directory boundary is enforced by the client, not by us — so
    the flags that set it belong in the recipe for the workflows that actually
    execute things. v2.337.0: any session can run commands, so the flags ride
    every recipe."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    plan = _plan_key(client, test_project)

    # v2.337.0 — any session can run a scanner or a host test, so the
    # client-sandbox flags ride EVERY recipe.
    for body in (recon, plan):
        codex = next(c for c in body["mcp_clients"] if c["id"] == "codex")
        assert "--sandbox workspace-write" in codex["hint"]
        assert "--ask-for-approval" in codex["hint"]

    # The per-client certificate-pinning notes are retired (the local root CA
    # is installed once per machine), and no recipe switches verification off.
    for body in (recon, plan):
        for setup in body["mcp_clients"]:
            assert "trust-cert.sh" not in setup["hint"]
            assert "NODE_EXTRA_CA_CERTS" not in setup["hint"]
            assert "NODE_TLS_REJECT_UNAUTHORIZED" not in setup["hint"]


# ---------------------------------------------------------------------------
# Asking for a tool that isn't approved
# ---------------------------------------------------------------------------

def test_suggest_tool_records_the_ask_and_grants_nothing(
    client, test_project, db_session
):
    """The alternative to a recorded ask is an agent quietly substituting a tool
    nobody vetted, or abandoning the test with no trace of why."""
    from app.db.models_tools import ToolRegistryEntry
    from app.services import tool_registry_service as registry

    plan = _plan_key(client, test_project)
    headers = {"X-API-Key": plan["api_key"]}

    result = _call(
        client,
        headers,
        "suggest_tool",
        {
            "name": "ligolo-ng",
            "rationale": "Used it to reach the segmented VLAN.",
        },
    )
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["status"] == "suggested"
    assert body["already_catalogued"] is False
    assert "suggestion" in body["message"].lower()

    db_session.expire_all()
    row = db_session.query(ToolRegistryEntry).filter_by(name="ligolo-ng").one()
    assert "segmented VLAN" in row.suggested_rationale
    assert row.suggested_by_agent_id is not None
    # A suggestion awaits a curator; it is not in the catalogue yet.
    assert row.status == "suggested"


def test_suggesting_a_catalogued_tool_says_so(client, test_project, db_session):
    """A bare 201 would read as 'awaiting a curator' about a tool the catalogue
    already has."""
    from app.services import tool_registry_service as registry

    registry.seed_registry(db_session)
    plan = _plan_key(client, test_project)
    headers = {"X-API-Key": plan["api_key"]}

    result = _call(
        client,
        headers,
        "suggest_tool",
        {"name": "nmap", "rationale": "Need a port scanner."},
    )
    body = result["structuredContent"]
    assert body["already_catalogued"] is True
    assert body["status"] == "reference"


def test_a_registry_entry_can_omit_the_params_it_has_none_of(client, test_project):
    """Every entry used to declare `path_params: []`, `query_params: []` and
    `body_params: []` whether or not it had any — 62 lines of noise across the
    registry, and a *missing* key blew up at dispatch time (a failed tool call)
    rather than at import. The readers now treat absent as empty, which is what
    the empty lists said."""
    from app.api.v1.endpoints.mcp_tools import TOOLS

    # A read tool with no arguments at all declares none of the three.
    spec = TOOLS["agent_identity"]
    assert "path_params" not in spec
    assert "query_params" not in spec
    assert "body_params" not in spec

    # And it still dispatches — the property the empty lists were protecting.
    assist = _assist_key(client, test_project)
    result = _call(client, {"X-API-Key": assist["api_key"]}, "agent_identity")
    assert result["isError"] is False, result
    assert result["structuredContent"]["workflow"] == "project"
