"""Review 2026-10-08, agent surface (second wave).

* A discrete host filter an agent mistypes (``state``, ``subnets``) is a 422
  naming the value on every host read, never an ordinary empty result.
* The agents' host-filter help is one text, on the routes and the MCP tools.
"""
import pytest

from app.api.v1.endpoints import agent_common
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.services import agent_session_service as sessions

HOST_READS = [
    "/api/v1/agent/hosts",
    "/api/v1/agent/assist/hosts",
    "/api/v1/agent/assist/hosts/count",
    "/api/v1/agent/assist/hosts.ndjson",
    "/api/v1/agent/assist/report-context.ndjson",
]


@pytest.fixture()
def agent_headers(db_session, test_project):
    user = User(
        id=92001, username="operator-92001", email="operator-92001@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER, is_active=True, is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value,
    ))
    for last, state in ((1, "up"), (2, "up"), (3, "down")):
        db_session.add(models.Host(project_id=test_project.id, ip_address=f"10.72.0.{last}", state=state))
    db_session.add(models.Host(project_id=test_project.id, ip_address="10.73.0.1", state="up"))
    db_session.commit()
    agent = sessions.resolve_project_agent(db_session, project_id=test_project.id, user=user)
    session = sessions.create_agent_session(
        db_session, project_id=test_project.id, agent_id=agent.id, started_by_id=user.id,
    )
    raw = sessions.mint_session_key(db_session, agent=agent, session=session)
    db_session.commit()
    return {"X-API-Key": raw}


@pytest.mark.parametrize("path", HOST_READS)
@pytest.mark.parametrize("params,named", [
    ({"state": "Up"}, "Up"),
    ({"state": "alive"}, "alive"),
    ({"subnets": "10.72.0"}, "10.72.0"),
    ({"subnets": "10.72.0.0/24,dmz"}, "dmz"),
    ({"subnets": "10.72.0.0/33"}, "10.72.0.0/33"),
    ({"subnets": " , "}, "names nothing"),
])
def test_a_host_filter_that_cannot_be_understood_is_refused_by_name(
    client, agent_headers, path, params, named,
):
    resp = client.get(path, headers=agent_headers, params=params)
    assert resp.status_code == 422, f"{path} {params}: {resp.status_code} {resp.text[:200]}"
    assert named in resp.text


def test_the_filters_that_are_understood_still_filter(client, agent_headers):
    def ips(**params):
        resp = client.get("/api/v1/agent/assist/hosts", headers=agent_headers, params=params)
        assert resp.status_code == 200, resp.text
        return {h["ip_address"] for h in resp.json()["items"]}

    assert ips(state="down") == {"10.72.0.3"}
    assert ips(subnets="10.72.0.0/24") == {"10.72.0.1", "10.72.0.2", "10.72.0.3"}
    assert ips(subnets="10.72.0.0/24, 10.73.0.1") == {"10.72.0.1", "10.72.0.2", "10.72.0.3", "10.73.0.1"}
    assert ips(state="up", subnets="10.72.0.0/24") == {"10.72.0.1", "10.72.0.2"}
    browse = client.get(
        "/api/v1/agent/hosts", headers=agent_headers, params={"state": "up", "subnets": "10.73.0.0/16"},
    )
    assert [h["ip_address"] for h in browse.json()] == ["10.73.0.1"]


def test_the_host_states_are_the_dsls():
    from app.services.host_query_dsl import _FIELD_SPECS

    (state,) = [spec for spec in _FIELD_SPECS if spec.name == "state"]
    assert sorted(agent_common.HOST_STATES) == sorted(state.enum_values)


def test_the_mcp_tools_describe_the_host_filters_as_the_routes_do():
    from app.api.v1.endpoints.mcp_tools import tool_list_payload

    tools = {tool["name"]: tool["inputSchema"]["properties"] for tool in tool_list_payload()}
    for name in ("assist_list_hosts", "assist_count_hosts"):
        props = tools[name]
        assert props["search"]["description"] == agent_common.SEARCH_PARAM_HELP
        assert props["has_critical_vulns"]["description"] == agent_common.SEVERITY_FLAGS_HELP
        assert props["has_high_vulns"]["description"] == agent_common.SEVERITY_FLAGS_HELP
        assert props["state"]["description"] == agent_common.STATE_PARAM_HELP
        assert props["subnets"]["description"] == agent_common.SUBNETS_PARAM_HELP
