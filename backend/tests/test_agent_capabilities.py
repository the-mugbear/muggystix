"""Every agent write route is gated — now by the operator, not a capability.

This file used to test the capability system: a vocabulary of `write:*` grants,
a row-level `ASSIGNED` constraint, and a completeness sweep asserting that a
capability-less assist key was refused by every write route.

v2.309.0 deleted that system. It was a second authorization model beside the
product's own RBAC, and only assist ever consulted it — plan, execution and
recon keys resolved to `LEGACY_WRITE_CAPABILITIES` unconditionally. A key now
does what its operator may do, checked per request against the same project
roles a person is checked against.

**The completeness property survives, and is what this file keeps.** The
original concern was a new write endpoint shipping without a guard; that is
still worth preventing, the guard is just a different one. Since
`enforce_agent_operator_access` is applied at the *router*, the failure mode
inverts — instead of forgetting a per-route gate, someone mounts a router
without one, and every route under it is exposed at once.

Deliberately **behavioural**, not structural. A first draft of this test walked
the route tree looking for the dependency and passed while inspecting **zero of
19** routes: FastAPI 0.141 keeps an included router as a single node rather
than flattening it, so the walk found nothing and the assertion was vacuous.
Driving a real read-only key at every route cannot fail that way.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_agent import (
    Agent,
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
)
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole

#: Mutating agent routes deliberately reachable by a read-only operator.
#: These record something about the SESSION — its environment, its key
#: deadline, feedback about the prompt — rather than project data, so a
#: read-only operator's agent needs them as much as anyone's.
#: Mirrors ``AGENT_SESSION_METADATA_WRITES`` in deps.py, stated here as full
#: paths because that is what a caller sees.
OPERATOR_METADATA_WRITES = {
    ("POST", "/api/v1/agent/session/renew"),
    ("POST", "/api/v1/agent/assist/sessions/{session_id}/environment"),
    ("POST", "/api/v1/agent/execution-sessions/{session_id}/environment"),
    ("POST", "/api/v1/agent/recon/sessions/{session_id}/environment"),
    ("POST", "/api/v1/agent/feedback"),
    ("POST", "/api/v1/agent/tool-suggestions"),
}

#: Not under /agent, but mutation-capable: tools/call loops back into the
#: guarded routes, so the gate runs there. It makes no decision of its own.
MCP_TRANSPORT_PATH = "/api/v1/mcp"


def _agent_write_routes():
    """Every mutating route mounted under /api/v1/agent.

    Enumerated from the OpenAPI schema rather than by iterating ``app.routes``:
    FastAPI 0.141 stores an included router as a single node instead of
    flattening its sub-routes, so flat iteration finds nothing. No agent route
    is hidden from the schema, so the schema is a complete source — which is
    what a completeness check needs.
    """
    from app.main import app

    seen = []
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith("/api/v1/agent"):
            continue
        for method in operations:
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                seen.append((method.upper(), path))
    return sorted(set(seen))


@pytest.fixture
def viewer_key(db_session, test_project):
    """A live agent key whose operator is a project VIEWER — i.e. read-only.

    Built directly rather than through `/assist/start`, which requires AUDITOR:
    the point is to hold a key the product would not mint today, because that
    is precisely the credential a new write route must still refuse.
    """
    user = User(
        id=98001,
        username="sweep-viewer",
        email="sweep-viewer@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=user.id, role=ProjectRole.VIEWER.value,
    ))
    agent = Agent(name="sweep-agent", project_id=test_project.id, owner_id=user.id)
    db_session.add(agent)
    db_session.flush()
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=test_project.id, agent_id=agent.id,
        started_by_id=user.id, status="active",
    )
    db_session.add(base)
    db_session.flush()
    detail = AssistSession(
        project_id=test_project.id, agent_id=agent.id, started_by_id=user.id,
        status=AssistSessionStatus.ACTIVE, agent_session_id=base.id,
        purpose="write-route sweep",
    )
    db_session.add(detail)
    db_session.flush()
    raw = "nm_agent_" + secrets.token_urlsafe(32)
    db_session.add(APIKey(
        agent_id=agent.id, name="sweep",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14], agent_session_id=base.id,
        assist_session_id=detail.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
    ))
    db_session.commit()
    return raw


def test_every_agent_write_route_refuses_a_read_only_operator(client, viewer_key):
    """The completeness sweep: no agent write route may accept a key whose
    operator cannot write.

    A newly added write endpoint inherits the router-level gate and is refused
    by default; exempting one means adding it to OPERATOR_METADATA_WRITES,
    which is a deliberate act with a reason attached.
    """
    headers = {"X-API-Key": viewer_key}
    routes = _agent_write_routes()
    assert routes, "expected to discover agent write routes"

    checked = 0
    failures = []
    for method, path in routes:
        if (method, path) in OPERATOR_METADATA_WRITES:
            continue
        # Path params get an id that will not resolve; the gate runs before the
        # handler, so a 404 from inside would still mean the gate let it pass.
        concrete = path
        while "{" in concrete:
            head, _, rest = concrete.partition("{")
            _, _, tail = rest.partition("}")
            concrete = f"{head}999999{tail}"
        checked += 1
        resp = client.request(method, concrete, headers=headers, json={})
        if resp.status_code != 403 or "read-only" not in resp.text:
            failures.append(f"{method} {path} -> {resp.status_code} {resp.text[:120]}")

    # Guards against the vacuity that killed the first draft of this test: an
    # assertion that inspects nothing passes forever. 13 routes today.
    assert checked >= 10, (
        f"only {checked} write routes were exercised — the sweep is not seeing "
        "the surface it claims to cover"
    )
    assert not failures, (
        "agent write routes that did NOT refuse a read-only operator:\n  "
        + "\n  ".join(failures)
        + "\n\nMount the router with `dependencies=_agent_operator_access`, or "
        "add the route to OPERATOR_METADATA_WRITES with a justification."
    )
