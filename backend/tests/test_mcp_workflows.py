"""MCP across the three agentic workflows (v2.278.0).

Before this, MCP covered the assist surface only: an operator running recon or
working an approved plan had an MCP client connected to a server that offered
them nothing they could use, and every tool it *did* list 403'd on their key.

What these tests pin is the part that makes three entry points work over one
endpoint — the caller's key decides which workflow's tools exist, and the
arguments that key already answers (which plan, which session) are filled in
server-side rather than guessed by a model. Plus the two properties that keep
the split honest: hiding a tool is presentation, not authorisation (the
endpoint still decides), and an agent that wants an unapproved tool has a way to
say so that grants it nothing.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.mcp_assist import _identity_cache


@pytest.fixture(autouse=True)
def _clear_identity_cache():
    # The 60s identity cache is keyed by API key; tests mint fresh keys, but
    # clear anyway so an assertion never depends on a neighbouring test's timing.
    _identity_cache.clear()
    yield
    _identity_cache.clear()


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
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/generate",
        json={"title": title},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _recon_key(client, test_project, scope):
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/recon/start",
        json={"notes": "mcp recon"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


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

    plan_id = identity(plan["api_key"])
    assert plan_id["workflow"] == "plan_generation"
    assert plan_id["workflow_family"] == "plan"
    assert plan_id["plan_id"] == plan["plan_id"]

    recon_id = identity(recon["api_key"])
    assert recon_id["workflow"] == "recon"
    assert recon_id["scope_id"] == scope_with_subnets.id
    # The recon URLs are keyed by the ReconSession id, which is a different row
    # from the AgentSession the key binds to.
    assert recon_id["workflow_session_id"] == recon["recon_session_id"]

    assist_id = identity(assist["api_key"])
    assert assist_id["workflow"] == "assist"
    assert assist_id["workflow_session_id"] == assist["assist_session_id"]
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

    plan_tools = _tool_names(client, {"X-API-Key": plan["api_key"]})
    recon_tools = _tool_names(client, {"X-API-Key": recon["api_key"]})
    assist_tools = _tool_names(client, {"X-API-Key": assist["api_key"]})

    assert "plan_add_entries" in plan_tools and "plan_submit" in plan_tools
    assert not {t for t in plan_tools if t.startswith(("recon_", "assist_"))}

    assert "recon_get_context" in recon_tools and "recon_get_summary" in recon_tools
    assert not {t for t in recon_tools if t.startswith(("plan_", "execution_"))}

    assert "assist_list_hosts" in assist_tools
    assert not {t for t in assist_tools if t.startswith(("plan_", "recon_", "execution_"))}

    # The cross-workflow tools are in all three — an agent must always be able to
    # ask what it is, and to report a tool it needed but didn't have.
    for tools in (plan_tools, recon_tools, assist_tools):
        assert {"agent_identity", "suggest_tool"} <= tools


def test_unauthenticated_list_is_the_documentation_view(client):
    """No key means no workflow to filter by, so discovery shows everything —
    degrading to an empty list would make the server look broken to a client
    that hasn't been given a key yet."""
    tools = _tool_names(client)
    assert {"assist_list_hosts", "plan_submit", "recon_get_context",
            "execution_get_progress"} <= tools


def test_hiding_a_tool_is_presentation_not_authorisation(
    client, test_project, scope_with_subnets
):
    """The MCP layer makes no security decision — it forwards the key and the
    real endpoint decides. If a model calls a tool its client never listed, it
    must still hit the endpoint's own 403 rather than a permissive shortcut."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    headers = {"X-API-Key": recon["api_key"]}

    assert "plan_submit" not in _tool_names(client, headers)

    result = _call(client, headers, "plan_submit", {"plan_id": 1})
    assert result["isError"] is True
    assert "403" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Arguments the key already answers
# ---------------------------------------------------------------------------

def test_plan_id_is_filled_from_the_key(client, test_project):
    """A plan key is bound to exactly one plan. Making the model carry the id
    means the model can get it wrong; filling it server-side means it can't."""
    plan = _plan_key(client, test_project, title="auto-filled plan")
    headers = {"X-API-Key": plan["api_key"]}

    result = _call(client, headers, "plan_get")
    assert result["isError"] is False, result
    assert result["structuredContent"]["id"] == plan["plan_id"]


def test_an_explicit_argument_beats_the_auto_filled_one(client, test_project):
    """Auto-fill is a default, not an override — otherwise a caller could never
    address anything but its own binding, and the 403 it should see for trying
    would be silently replaced by a success against the wrong plan."""
    first = _plan_key(client, test_project, title="first plan")
    second = _plan_key(client, test_project, title="second plan")
    headers = {"X-API-Key": first["api_key"]}

    result = _call(client, headers, "plan_get", {"plan_id": second["plan_id"]})
    assert result["isError"] is True
    assert "403" in result["content"][0]["text"]


def test_recon_probe_resolves_its_own_session_id(
    client, test_project, scope_with_subnets, db_session
):
    """The probe is the mandated first step, and the id it posts to belongs to a
    table the agent has not read yet. Resolving it from the key is what keeps
    'first step' actually first."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    headers = {"X-API-Key": recon["api_key"]}

    result = _call(
        client,
        headers,
        "recon_record_environment",
        {"os_family": "linux", "shell": "bash"},
    )
    assert result["isError"] is False, result
    assert result["structuredContent"]["session_id"] == recon["recon_session_id"]

    from app.db.models_agent import ReconSession

    db_session.expire_all()
    session = db_session.get(ReconSession, recon["recon_session_id"])
    assert session.environment_probed_at is not None
    assert session.environment["os_family"] == "linux"


# ---------------------------------------------------------------------------
# Plan generation over MCP, end to end
# ---------------------------------------------------------------------------

def test_plan_generation_workflow_over_mcp(client, test_project, db_session):
    """The stage-2 loop an agent actually runs: read context, propose tests,
    validate, submit for human approval — every step a tool call, no curl."""
    from app.db.models import Host

    host = Host(ip_address="10.77.1.10", project_id=test_project.id, state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    plan = _plan_key(client, test_project, title="stage 2 over mcp")
    headers = {"X-API-Key": plan["api_key"]}

    context = _call(client, headers, "plan_get_context")
    assert context["isError"] is False, context

    described = _call(
        client,
        headers,
        "plan_update",
        {
            "description": "Scope: one host. Prioritised by exposure. Methodology: manual.",
            "generated_by_model": "test-model",
        },
    )
    assert described["isError"] is False, described

    added = _call(
        client,
        headers,
        "plan_add_entries",
        {
            "entries": [
                {
                    "host_id": host.id,
                    "priority": "high",
                    "test_phase": "enumeration",
                    "rationale": "FTP banner suggests an anonymous-login check is worth doing.",
                    "proposed_tests": [
                        {
                            "tool": "nmap",
                            "description": "Confirm the FTP service and its version.",
                            "command": "nmap -p21 -sV -oX ftp.xml 10.77.1.10",
                        }
                    ],
                }
            ]
        },
    )
    assert added["isError"] is False, added

    validated = _call(client, headers, "plan_validate")
    assert validated["isError"] is False, validated

    submitted = _call(client, headers, "plan_submit")
    assert submitted["isError"] is False, submitted

    from app.db.models_agent import TestPlan

    db_session.expire_all()
    stored = db_session.get(TestPlan, plan["plan_id"])
    # Submitted, not approved: the human gate is the point of stage 2 ending here.
    assert stored.status != "approved"
    assert stored.entries and stored.entries[0].host_id == host.id


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

    # Sliced from the key, not from an argument a model has to name — the one
    # wrong answer there hands an agent another workflow's instructions.
    assert "Workflow C — Populate Host Data" in recon_text
    assert "Workflow C — Populate Host Data" not in plan_text
    assert "Workflow A — Build a Test Plan" in plan_text

    # The shared rules ride along in every slice.
    for text in (recon_text, plan_text):
        assert "Say the rules back before you start" in text


def test_the_approved_set_is_readable_from_every_workflow(
    client, test_project, scope_with_subnets, db_session
):
    """The agent is asked to tell the operator which tools it may run before it
    starts. Without a tool for it, that half of the read-back is recalled rather
    than read — and the set changes when an admin vets a suggestion."""
    from app.services import tool_registry_service as registry

    registry.seed_registry(db_session)
    recon = _recon_key(client, test_project, scope_with_subnets)
    headers = {"X-API-Key": recon["api_key"]}

    assert "list_approved_tools" in _tool_names(client, headers)

    result = _call(client, headers, "list_approved_tools")
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["count"] > 0
    # Defaulted to the approved set: handing a model 60 rows of
    # human-documentation and letting it infer which it may run is the confusion
    # the status column exists to prevent.
    assert {t["status"] for t in body["tools"]} == {"approved"}
    assert "nmap" in {t["name"] for t in body["tools"]}

    # The other statuses are reachable, so an agent can check whether the tool
    # it wants was already declined before suggesting it again.
    declined = _call(client, headers, "list_approved_tools", {"status": "reference"})
    assert {t["status"] for t in declined["structuredContent"]["tools"]} == {"reference"}


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

    # Distinct server names, so connecting a recon session and a plan session to
    # the same client leaves two servers rather than one overwriting the other.
    plan_payloads = " ".join(c["payload"] for c in plan["mcp_clients"])
    recon_payloads = " ".join(c["payload"] for c in recon["mcp_clients"])
    assert "bluestick-plan" in plan_payloads and "bluestick-recon" not in plan_payloads
    assert "bluestick-recon" in recon_payloads and "bluestick-plan" not in recon_payloads

    # And each carries its own live key, not a shared one.
    assert plan["api_key"] in plan_payloads
    assert recon["api_key"] in recon_payloads


def test_sandbox_guidance_rides_with_the_workflows_that_run_commands(
    client, test_project, scope_with_subnets
):
    """The working-directory boundary is enforced by the client, not by us — so
    the flags that set it belong in the recipe for the workflows that actually
    execute things. Attaching them to plan generation, which only calls the API,
    would train operators to ignore them."""
    recon = _recon_key(client, test_project, scope_with_subnets)
    plan = _plan_key(client, test_project)

    codex_recon = next(c for c in recon["mcp_clients"] if c["id"] == "codex")
    assert "--sandbox workspace-write" in codex_recon["hint"]
    assert "--ask-for-approval" in codex_recon["hint"]

    codex_plan = next(c for c in plan["mcp_clients"] if c["id"] == "codex")
    assert "--sandbox" not in codex_plan["hint"]

    # Every recipe still warns about the self-signed certificate, which is the
    # failure every client hits first — but with the mechanism that actually
    # applies to it. Node clients read NODE_EXTRA_CA_CERTS; Codex is a Rust
    # binary that reads SSL_CERT_DIR, and telling its operators to export the
    # Node variable was advice that could only ever fail (v2.282.0). Claiming
    # it could not be pinned at all was wrong too — v2.285.0 verified
    # SSL_CERT_DIR against codex 0.147.0.
    for body in (recon, plan):
        for setup in body["mcp_clients"]:
            if setup["id"] == "codex":
                assert "SSL_CERT_DIR" in setup["hint"]
                assert "NODE_EXTRA_CA_CERTS does nothing" in setup["hint"], (
                    "the Codex recipe must say the Node variable doesn't apply"
                )
                assert "CA-trusted certificate" not in setup["hint"], (
                    "a self-hosted deployment will never have one — telling an "
                    "operator to get one is a dead end, not a fix"
                )
            else:
                assert "NODE_EXTRA_CA_CERTS" in setup["hint"]
            # Both recipes point at the helper that installs it, and say the
            # variable is read at startup — the step operators actually miss.
            assert "trust-cert.sh" in setup["hint"]
            assert "RESTART" in setup["hint"]


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
            "rationale": "Needed to reach the segmented VLAN the approved set can't.",
        },
    )
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["status"] == "suggested"
    assert body["already_approved"] is False
    assert "not approved" in body["message"].lower()

    db_session.expire_all()
    row = db_session.query(ToolRegistryEntry).filter_by(name="ligolo-ng").one()
    assert "segmented VLAN" in row.suggested_rationale
    assert row.suggested_by_agent_id is not None
    # And it is emphatically not runnable.
    assert "ligolo-ng" not in registry.approved_tool_names(db_session)


def test_suggesting_an_approved_tool_says_so(client, test_project, db_session):
    """A bare 201 would read as 'wait for a human' about a tool already sitting
    in the agent's own approved set."""
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
    assert body["already_approved"] is True
    assert body["status"] == "approved"


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
    assert result["structuredContent"]["workflow"] == "assist"
