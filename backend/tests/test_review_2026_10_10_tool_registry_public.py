"""Review 2026-10-10, H6 — agent-written free text was readable with no
credential: ``GET /references/tools`` is public, and a ``suggested`` row is
2,000 characters an agent wrote (in practice: client names, addresses).

Anonymous callers get the catalogue (``reference`` rows, no rationale).  The
two callers that legitimately see more keep what they had: a signed-in user
(the Tool reference page) and an agent presenting its key (``list_tools``).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, create_session
from app.db.models_tools import TOOL_REFERENCE, TOOL_REJECTED, TOOL_SUGGESTED, ToolRegistryEntry
from app.db.session import get_db
from app.main import app
from app.services import agent_session_service as sessions
from app.services import tool_registry_service as registry

SECRET = "Needed for ACME Corp's 10.20.30.0/24 jump host"
URL = "/api/v1/references/tools"


@pytest.fixture()
def seeded(db_session, test_project, test_user):
    registry.seed_registry(db_session)
    registry.record_suggestion(db_session, name="acme-pivot", rationale=SECRET)
    registry.record_suggestion(db_session, name="acme-declined", rationale=f"{SECRET} (second)")
    db_session.query(ToolRegistryEntry).filter_by(name="acme-declined").update({"status": TOOL_REJECTED})
    # A suggestion a curator took into the catalogue keeps its rationale column.
    registry.record_suggestion(db_session, name="acme-accepted", rationale=f"{SECRET} (third)")
    db_session.query(ToolRegistryEntry).filter_by(name="acme-accepted").update(
        {"status": TOOL_REFERENCE, "description": "A curated description."},
    )
    db_session.commit()
    agent = sessions.resolve_project_agent(db_session, project_id=test_project.id, user=test_user)
    session = sessions.create_agent_session(
        db_session, project_id=test_project.id, agent_id=agent.id, started_by_id=test_user.id,
    )
    raw = sessions.mint_session_key(db_session, agent=agent, session=session)
    db_session.commit()
    return {"key": raw}


@pytest.fixture()
def anonymous(db_session, test_user):
    """A client with NO signed-in user: only the database is the test's.
    (``test_user`` first, as the ``client`` fixture has it: the app's startup
    creates a default admin only when none exists.)"""
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _names(body):
    return {t["name"] for t in body["tools"]}


def test_an_anonymous_caller_gets_the_catalogue_and_no_agent_text(anonymous, seeded):
    body = anonymous.get(URL).json()
    assert {t["status"] for t in body["tools"]} == {TOOL_REFERENCE}
    assert body["count"] == len(body["tools"]) > 10
    assert {"nmap", "acme-accepted"} <= _names(body)
    assert not {"acme-pivot", "acme-declined"} & _names(body)
    assert all(t["suggested_rationale"] is None for t in body["tools"])
    assert "ACME" not in anonymous.get(URL).text

    for status in (TOOL_SUGGESTED, TOOL_REJECTED):
        listed = anonymous.get(URL, params={"status": status})
        assert listed.status_code == 200
        assert listed.json() == {"count": 0, "tools": []}, status
    assert _names(anonymous.get(URL, params={"status": TOOL_REFERENCE}).json()) == _names(body)
    # A credential that is not one is anonymous too.
    for headers in ({"Authorization": "Bearer not-a-token"}, {"X-API-Key": "nm_agent_nope"}):
        assert "ACME" not in anonymous.get(URL, headers=headers).text


def test_an_agent_key_reads_what_it_always_read(anonymous, seeded):
    headers = {"X-API-Key": seeded["key"]}
    body = anonymous.get(URL, headers=headers).json()
    assert {"nmap", "acme-pivot", "acme-declined", "acme-accepted"} <= _names(body)
    suggested = anonymous.get(URL, headers=headers, params={"status": TOOL_SUGGESTED}).json()
    assert _names(suggested) == {"acme-pivot"}
    assert suggested["tools"][0]["suggested_rationale"] == SECRET


def test_a_signed_in_user_reads_what_the_page_always_read(anonymous, db_session, seeded, test_user):
    jti = str(uuid.uuid4())
    create_session(db_session, test_user, jti)
    token = create_access_token({"sub": str(test_user.id), "username": test_user.username,
                                 "role": test_user.role}, jti=jti)
    headers = {"Authorization": f"Bearer {token}"}
    body = anonymous.get(URL, headers=headers).json()
    assert {"acme-pivot", "acme-declined", "acme-accepted"} <= _names(body)
    by_name = {t["name"]: t for t in body["tools"]}
    assert by_name["acme-pivot"]["suggested_rationale"] == SECRET
    assert _names(anonymous.get(URL, headers=headers, params={"status": TOOL_REJECTED}).json()) == {"acme-declined"}


def test_the_other_public_references_carry_no_agent_text(anonymous, seeded):
    """The same question asked of the rest of the file: none of them reads
    the registry's free text (or any row an agent wrote)."""
    for path in (
        "/api/v1/references/mcp-tools", "/api/v1/references/parser-coverage",
        "/api/v1/references/", "/api/v1/agents-guide",
    ):
        resp = anonymous.get(path)
        assert resp.status_code in (200, 404), f"{path}: {resp.status_code}"
        assert "ACME" not in resp.text and "acme-pivot" not in resp.text, path
    assert anonymous.get("/api/v1/references/sbom").status_code in (401, 403)
    assert anonymous.patch(f"{URL}/acme-pivot", json={"status": "reference"}).status_code in (401, 403)
