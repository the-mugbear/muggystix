"""Scanner integrations on the agent surface (2.482.0; owner, 2026-10-10).

What is pinned here:

* any session may read THAT scanners are configured — name, type, address —
  and no credential is in that answer, on either door (searched for in the
  serialized text, not only by key);
* credentials are one integration's, on a request that states the operator
  agreed (422 without it, saying what to ask), for an operator who can write
  to the project (403 for an auditor's and a viewer's key), and each one is
  recorded as an audit row that holds no secret;
* integrations are the installation's: a second project's session requests
  the same one;
* both doors are told the ask-first rule from ONE source, and neither the
  pasted prompt nor the MCP opening contains a credential.

The server does not — cannot — verify that the operator said yes; nothing here
tests an "approval", because there is none.
"""
from __future__ import annotations

import json

import pytest

from app.db.models_agent import AgentApiCall
from app.db.models_auth import AuditLog
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.services.agent_policy import (
    SCANNER_ASK_FIRST_REFUSAL,
    SCANNER_CONSENT_MEANING,
    SCANNER_CREDENTIALS_NOTE,
    SCANNER_LIST_NOTE,
    render_scanner_integrations_rule,
)
from app.services.integration_service import CREDENTIALS_SHARED_ACTION, IntegrationService
from tests.test_agent_role_route_matrix import _key_for, _member

#: Recognisable strings that must be in nothing an agent reads by default.
ACCESS = "zebra-access-key-5521"
SECRET = "zebra-secret-key-8843"

LIST = "/api/v1/agent/assist/scanner-integrations"


def _credentials_path(integration_id) -> str:
    return f"/api/v1/agent/scanner-integrations/{integration_id}/credentials"


@pytest.fixture
def scanner(db_session):
    return IntegrationService(db_session).create(
        created_by_id=None, name="Lab Nessus", integration_type="nessus",
        base_url="https://192.0.2.10:8834", secret=ACCESS, secret2=SECRET,
        extra_config={"max_hosts_per_scan": 256},
    )


def _headers(db_session, project, role: str) -> dict:
    user = _member(db_session, project, ProjectRole(role))
    return {"X-API-Key": _key_for(db_session, project, user)}


def _shared_rows(db_session):
    db_session.expire_all()
    return db_session.query(AuditLog).filter_by(action=CREDENTIALS_SHARED_ACTION).all()


def _mcp(client, headers, method, params=None):
    resp = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": method, "params": params or {}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp


# ---------------------------------------------------------------------------
# What any session sees
# ---------------------------------------------------------------------------

def test_any_session_lists_the_active_scanners_and_no_credential(
    client, db_session, test_project, scanner,
):
    IntegrationService(db_session).create(
        created_by_id=None, name="Switched off", integration_type="burp",
        base_url="https://192.0.2.11:1337", secret="zebra-burp-key-1", secret2=None,
        extra_config=None, is_active=False,
    )
    headers = _headers(db_session, test_project, "viewer")

    resp = client.get(LIST, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "items": [{
            "id": scanner.id, "name": "Lab Nessus", "integration_type": "nessus",
            "base_url": "https://192.0.2.10:8834",
            "extra_config": {"max_hosts_per_scan": 256},
        }],
        "total": 1,
        "note": SCANNER_LIST_NOTE,
    }
    for text in (resp.text,
                 _mcp(client, headers, "tools/call",
                      {"name": "list_scanner_integrations", "arguments": {}}).text):
        assert "Lab Nessus" in text
        assert ACCESS not in text and SECRET not in text
        assert scanner.secret_encrypted not in text
        assert "zebra-burp-key-1" not in text and "Switched off" not in text
    # Listing shares nothing, so it records nothing.
    assert _shared_rows(db_session) == []


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{}, {"operator_agreed": False}], ids=["missing", "false"])
def test_a_request_that_does_not_state_agreement_is_refused_with_what_to_ask(
    client, db_session, test_project, scanner, body,
):
    resp = client.post(
        _credentials_path(scanner.id), json=body,
        headers=_headers(db_session, test_project, "analyst"),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == SCANNER_ASK_FIRST_REFUSAL
    assert "Ask the operator first" in resp.text and SCANNER_CONSENT_MEANING in resp.text
    assert ACCESS not in resp.text and SECRET not in resp.text
    assert _shared_rows(db_session) == []


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_agreement_is_the_literal_true_and_nothing_that_resembles_it(
    client, db_session, test_project, scanner, value,
):
    resp = client.post(
        _credentials_path(scanner.id), json={"operator_agreed": value},
        headers=_headers(db_session, test_project, "analyst"),
    )
    assert resp.status_code == 422, resp.text
    assert ACCESS not in resp.text and _shared_rows(db_session) == []


def test_an_unknown_or_inactive_scanner_is_a_404(client, db_session, test_project, scanner):
    headers = _headers(db_session, test_project, "analyst")
    assert client.post(
        _credentials_path(999999), json={"operator_agreed": True}, headers=headers,
    ).status_code == 404

    IntegrationService(db_session).update(integration_id=scanner.id, is_active=False)
    resp = client.post(
        _credentials_path(scanner.id), json={"operator_agreed": True}, headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert ACCESS not in resp.text and _shared_rows(db_session) == []


@pytest.mark.parametrize("role", ["auditor", "viewer"])
def test_a_read_only_operators_agent_is_refused(client, db_session, test_project, scanner, role):
    """The gate's default for a POST that declares nothing: the operator must
    be able to write to the project.  Stating agreement changes nothing."""
    resp = client.post(
        _credentials_path(scanner.id), json={"operator_agreed": True},
        headers=_headers(db_session, test_project, role),
    )
    assert resp.status_code == 403, resp.text
    assert ACCESS not in resp.text and SECRET not in resp.text
    assert _shared_rows(db_session) == []


def test_the_route_declares_nothing_so_the_write_default_holds():
    """No second check and no exemption: the credential route is an ordinary
    project write to the gate (not a session-metadata write)."""
    from app.main import app
    from tests.agent_route_declarations import agent_route_declarations

    declared = agent_route_declarations(app)
    access = declared[("POST", "/api/v1/agent/scanner-integrations/{integration_id}/credentials")]
    assert access.session_metadata_write is False
    assert declared[("GET", LIST)].read_floor is ProjectRole.VIEWER


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_a_writing_operators_agent_is_given_the_credentials_and_it_is_recorded(
    client, db_session, test_project, scanner, role,
):
    user = _member(db_session, test_project, ProjectRole(role))
    headers = {"X-API-Key": _key_for(db_session, test_project, user)}

    resp = client.post(_credentials_path(scanner.id), json={"operator_agreed": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": scanner.id, "name": "Lab Nessus", "integration_type": "nessus",
        "base_url": "https://192.0.2.10:8834",
        "extra_config": {"max_hosts_per_scan": 256},
        "credentials": {"access_key": ACCESS, "secret_key": SECRET},
        "note": SCANNER_CREDENTIALS_NOTE,
    }

    (audit,) = _shared_rows(db_session)
    assert audit.user_id == user.id
    assert (audit.resource_type, audit.resource_id) == ("integration", str(scanner.id))
    assert audit.success is True
    session_id = client.get("/api/v1/agent/identity", headers=headers).json()["session_id"]
    assert audit.details == {
        "integration_id": scanner.id, "integration_name": "Lab Nessus",
        "integration_type": "nessus", "project_id": test_project.id,
        "agent_session_id": session_id,
    }
    stored = json.dumps([audit.details, audit.error_message, audit.resource_id])
    assert ACCESS not in stored and SECRET not in stored


def test_each_request_is_its_own_record(client, db_session, test_project, scanner):
    headers = _headers(db_session, test_project, "analyst")
    for _ in range(2):
        assert client.post(
            _credentials_path(scanner.id), json={"operator_agreed": True}, headers=headers,
        ).status_code == 200
    assert len(_shared_rows(db_session)) == 2


def test_a_second_projects_session_requests_the_same_scanner(
    client, db_session, test_project, scanner,
):
    """Application-wide: the integration belongs to no project."""
    other = Project(name="Second engagement", slug="second-engagement", status="active")
    db_session.add(other)
    db_session.commit()

    for project in (test_project, other):
        resp = client.post(
            _credentials_path(scanner.id), json={"operator_agreed": True},
            headers=_headers(db_session, project, "analyst"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["credentials"]["access_key"] == ACCESS
    assert {row.details["project_id"] for row in _shared_rows(db_session)} == {
        test_project.id, other.id,
    }


def test_the_request_over_mcp_is_the_same_route(client, db_session, test_project, scanner):
    """The tool loops back into the route: refused without agreement, the
    credentials with it, one record."""
    headers = _headers(db_session, test_project, "analyst")

    refused = _mcp(client, headers, "tools/call", {
        "name": "request_scanner_credentials", "arguments": {"integration_id": scanner.id},
    }).json()["result"]
    assert refused["isError"] is True
    assert "Ask the operator first" in refused["content"][0]["text"]
    assert ACCESS not in json.dumps(refused)
    assert _shared_rows(db_session) == []

    given = _mcp(client, headers, "tools/call", {
        "name": "request_scanner_credentials",
        "arguments": {"integration_id": scanner.id, "operator_agreed": True},
    }).json()["result"]
    assert given["isError"] is False, given
    assert given["structuredContent"]["credentials"] == {"access_key": ACCESS, "secret_key": SECRET}
    assert len(_shared_rows(db_session)) == 1


def test_the_api_call_log_has_nowhere_to_keep_an_answer():
    """``agent_api_calls`` records every agent request.  For the credential
    route that is the path, the status and the request body
    (``{"operator_agreed": true}``); of the ANSWER it keeps a byte count and
    nothing else, so a credential cannot land there."""
    columns = {c.name for c in AgentApiCall.__table__.columns}
    assert "request_body_summary" in columns and "response_bytes" in columns
    assert {c for c in columns if "response" in c} == {"response_bytes"}


# ---------------------------------------------------------------------------
# What the agent is told — one source, both doors, no credential
# ---------------------------------------------------------------------------

def test_both_doors_carry_the_ask_first_rule_and_neither_a_credential(
    client, db_session, test_project, scanner,
):
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    prompt = started.json()["instructions"]
    assert render_scanner_integrations_rule() in prompt
    assert "/agent/assist/scanner-integrations" in prompt

    headers = {"X-API-Key": started.json()["api_key"]}
    opening = _mcp(client, headers, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    }).json()["result"]["instructions"]
    assert render_scanner_integrations_rule(over_mcp=True) in opening
    assert "list_scanner_integrations" in opening and "request_scanner_credentials" in opening

    resumed = client.post(
        f"/api/v1/projects/{test_project.id}/agent-sessions/"
        f"{started.json()['agent_session_id']}/resume", json={},
    )
    assert resumed.status_code in (200, 201), resumed.text

    for text in (prompt, opening, resumed.json()["instructions"]):
        # The rule, with what the operator must be told…
        assert "ask the operator whether they want you to" in text
        assert SCANNER_CONSENT_MEANING in text
        # …and nothing of the scanner itself: no credential, and not even its
        # name (neither door lists the scanners; the read does).
        assert ACCESS not in text and SECRET not in text
        assert "Lab Nessus" not in text and "192.0.2.10" not in text
        # Never described as something BlueStick enforces.
        for claim in ("approved by BlueStick", "BlueStick verifies", "BlueStick checks that"):
            assert claim not in text


def test_the_rule_steers_no_scanner_choice():
    """Which scanner, when and with what options is the operator's call and
    the agent's judgment: the rule names no product and gives no instruction
    to use one."""
    for text in (render_scanner_integrations_rule(), render_scanner_integrations_rule(over_mcp=True)):
        lowered = text.lower()
        for word in ("nessus", "openvas", "nuclei", "burp", "you should use", "prefer"):
            assert word not in lowered


def test_the_tool_descriptions_carry_the_rule_too():
    """An MCP agent may read a tool and nothing else."""
    from app.api.v1.endpoints.mcp_tools import TOOLS

    listing = TOOLS["list_scanner_integrations"]["description"]
    request = TOOLS["request_scanner_credentials"]["description"]
    assert "NO credentials" in listing and "ask the operator" in listing
    assert "ASK THE OPERATOR FIRST" in request
    for text in (listing, request):
        assert "shares" in text and "credentials with you" in text
    assert "recorded" in request and "notes, evidence, feedback and proposals" in request
    schema = TOOLS["request_scanner_credentials"]["input_schema"]
    assert set(schema["properties"]) == {"integration_id", "operator_agreed"}


def test_the_prompt_builder_takes_no_integrations():
    """Nothing reaches an agent's prompt in plaintext any more: the builder
    has no such argument and the service no such helper."""
    import inspect

    import app.services.agent_prompt_service as prompt_service
    import app.services.integration_service as integration_service

    params = inspect.signature(prompt_service.build_session_instructions).parameters
    assert "integrations" not in params
    assert not hasattr(prompt_service, "_integration_block")
    assert not hasattr(integration_service, "active_integrations_for_prompt")
    assert not hasattr(integration_service, "decrypt_integration")


def test_membership_is_still_what_lets_a_session_read_the_list(
    client, db_session, test_project, scanner,
):
    """Any SESSION may see that scanners exist — a session being a member's.
    A key whose operator left the project reads nothing."""
    user = _member(db_session, test_project, ProjectRole.VIEWER)
    headers = {"X-API-Key": _key_for(db_session, test_project, user)}
    assert client.get(LIST, headers=headers).status_code == 200
    db_session.query(ProjectMembership).filter_by(
        project_id=test_project.id, user_id=user.id,
    ).delete()
    db_session.commit()
    assert client.get(LIST, headers=headers).status_code == 403
