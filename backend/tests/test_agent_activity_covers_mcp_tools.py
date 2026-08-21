"""The activity log records what an agent did, not what it did under one prefix.

v2.312.0.  Reported from a real Codex session: `/assist-sessions/21` showed
three entries for a session that made four tool calls, and one of the three was
the MCP server's own identity lookup rather than anything the agent asked for.

Nothing was lost — the middleware simply decided what to record from a URL
prefix (`/api/v1/agent/`), on the assumption that "an agent call" and "a call
under the agent prefix" are the same set.  Two MCP tools break it:
`read_agent_guide` fetches `/api/v1/agents-guide` and `list_approved_tools`
fetches `/api/v1/references/tools`.  Both are public endpoints, so the agent's
key was ignored, no attribution reached `request.state`, and the row was
dropped for having no agent — a partial record that reads as a quiet agent.

These tests pin the property rather than the two paths: every tool the MCP
registry serves must be able to produce an audit row.  That check lives here
rather than in the service because a service may not import the router layer
(``test_service_router_boundary``), so the allowlist is spelled out in
``agent_api_log_service`` and this file is what stops it drifting from the
registry it describes.
"""

import pytest

from app.services.agent_api_log_service import (
    AGENT_API_PREFIX,
    AGENT_AUDITED_PUBLIC_PATHS,
    is_agent_audited_path,
)


def _start_assist(client, project_id):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": "activity coverage"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_every_mcp_tool_target_is_an_audited_path():
    """The registry and the audit predicate must agree.

    A tool pointed at a path the middleware ignores is invisible work — and the
    failure is silent, which is why it survived: nothing errors, the row just
    never appears. This is the only thing keeping the service's hand-written
    allowlist honest, so adding a tool that dispatches somewhere new fails here
    rather than quietly going unlogged.
    """
    from app.api.v1.endpoints.mcp_tools import TOOLS

    for name, spec in TOOLS.items():
        # Fill placeholders with a plausible id — the predicate matches the
        # concrete path a dispatch actually produces, not the template.
        concrete = spec["path"]
        for param in spec.get("path_params", []):
            concrete = concrete.replace("{" + param + "}", "1")
        assert is_agent_audited_path(concrete), (
            f"MCP tool {name!r} dispatches to {concrete}, which the agent "
            "activity log ignores. Its calls would never appear in the "
            "operator's session view."
        )


def test_the_allowlist_carries_nothing_the_registry_does_not_need():
    """Drift in the other direction: an entry no tool dispatches to.

    Harmless for correctness — an unused path logs nothing extra, because
    attribution still requires an agent key — but it is a stale claim about
    what the surface does, and the next reader would take it as evidence some
    tool still calls it.
    """
    from app.api.v1.endpoints.mcp_tools import TOOLS

    dispatched = {spec["path"] for spec in TOOLS.values()}
    assert AGENT_AUDITED_PUBLIC_PATHS <= dispatched, (
        f"{sorted(AGENT_AUDITED_PUBLIC_PATHS - dispatched)} is in the audit "
        "allowlist but no MCP tool dispatches there any more."
    )
    assert all(not p.startswith(AGENT_API_PREFIX) for p in AGENT_AUDITED_PUBLIC_PATHS)
    # Exact matching only — a template here would silently never match.
    assert all("{" not in p for p in AGENT_AUDITED_PUBLIC_PATHS)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/agents-guide", "/api/v1/references/tools"],
)
def test_an_agent_reading_a_public_reference_lands_in_its_session_log(
    client, db_session, test_project, path,
):
    """The reported bug, end to end.

    Asserted through the same endpoint the UI reads, not the table, so a fix
    that records the row but leaves the session view filtering it out still
    fails.
    """
    from app.db.models_agent import AgentApiCall

    started = _start_assist(client, test_project.id)
    key = started["api_key"]
    sid = started["assist_session_id"]

    before = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.assist_session_id == sid)
        .count()
    )
    resp = client.get(path, headers={"X-API-Key": key})
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    rows = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.assist_session_id == sid)
        .all()
    )
    assert len(rows) == before + 1, (
        f"reading {path} with an assist key produced no activity row; the "
        "operator's session view under-reports what the agent did"
    )
    row = rows[-1]
    assert row.path == path
    assert row.project_id == test_project.id
    assert row.agent_id is not None


@pytest.mark.parametrize(
    "path",
    ["/api/v1/agents-guide", "/api/v1/references/tools"],
)
def test_an_anonymous_read_of_the_same_endpoint_records_nothing(
    client, db_session, path,
):
    """Widening the predicate must not widen what is stored.

    These endpoints serve the UI and unauthenticated clients too. Attribution
    comes from authenticating a key, so a caller without one — including the
    browser rendering the reference page — produces no row, exactly as before.
    """
    from app.db.models_agent import AgentApiCall

    before = db_session.query(AgentApiCall).count()
    assert client.get(path).status_code == 200
    db_session.expire_all()
    assert db_session.query(AgentApiCall).count() == before


def test_a_bogus_key_neither_blocks_the_read_nor_writes_a_row(client, db_session):
    """The endpoint stays public: a bad key is not an error here, and it buys
    no audit row either — attribution requires a key that actually resolves."""
    from app.db.models_agent import AgentApiCall

    before = db_session.query(AgentApiCall).count()
    resp = client.get(
        "/api/v1/references/tools",
        headers={"X-API-Key": "nm_agent_not_a_real_key_at_all_000000"},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.query(AgentApiCall).count() == before
