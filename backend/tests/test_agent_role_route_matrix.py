"""Every role against a representative agent route, as a matrix.

Phase 1 gave agent keys their operator's permissions. Phase 4 lowers the floor
so an auditor can start a session at all — which is when "what can each role's
agent reach?" stops being hypothetical and starts needing a table.

The matrix is deliberately over *route shapes* rather than all ~60 routes: one
ordinary read, one bulk export, one project write, one session-metadata write.
Those four are the distinct decisions the gate makes; enumerating every route
would restate the same four answers sixty times and rot on the next rename.

Two structural tests below cover the part a sample cannot: that every override
names a real route, and that no read route is left without a resolved minimum.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_agent import (
    Agent,
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
)
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole


_SEQ = [95000]

#: (label, method, path builder, is-it-allowed-per-role)
#: ANALYST is the floor for project writes; AUDITOR for bulk export; VIEWER for
#: an ordinary read; any member for session metadata.
MATRIX = [
    ("ordinary read", "GET", "/api/v1/agent/assist/vocabulary",
     {"analyst": True, "auditor": True, "viewer": True}),
    ("bulk export", "GET", "/api/v1/agent/assist/report-context.ndjson",
     {"analyst": True, "auditor": True, "viewer": False}),
    ("project write", "POST", "/api/v1/agent/hosts/{host_id}/notes",
     {"analyst": True, "auditor": False, "viewer": False}),
    ("session metadata", "POST", "/api/v1/agent/tool-suggestions",
     {"analyst": True, "auditor": True, "viewer": True}),
]


def _member(db, project, role):
    _SEQ[0] += 1
    user = User(
        id=_SEQ[0], username=f"matrix-{_SEQ[0]}",
        email=f"matrix-{_SEQ[0]}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER, is_active=True, is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user


def _key_for(db, project, user):
    agent = Agent(name=f"matrix-agent-{user.id}", project_id=project.id, owner_id=user.id)
    db.add(agent)
    db.flush()
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status="active",
    )
    db.add(base)
    db.flush()
    detail = AssistSession(
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status=AssistSessionStatus.ACTIVE, agent_session_id=base.id,
        purpose="role matrix",
    )
    db.add(detail)
    db.flush()
    raw = "nm_agent_" + secrets.token_urlsafe(32)
    db.add(APIKey(
        agent_id=agent.id, name=f"matrix-{user.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14], agent_session_id=base.id,
        assist_session_id=detail.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
    ))
    db.commit()
    return raw


@pytest.mark.parametrize("role", ["analyst", "auditor", "viewer"])
@pytest.mark.parametrize("label,method,path,expected", MATRIX,
                         ids=[m[0].replace(" ", "-") for m in MATRIX])
def test_role_route_matrix(
    client, db_session, test_project, role, label, method, path, expected
):
    user = _member(db_session, test_project, role)
    raw = _key_for(db_session, test_project, user)
    headers = {"X-API-Key": raw}

    if "{host_id}" in path:
        host = models.Host(
            project_id=test_project.id, ip_address=f"10.95.0.{_SEQ[0] % 250}", state="up",
        )
        db_session.add(host)
        db_session.commit()
        path = path.replace("{host_id}", str(host.id))

    body = None
    if method == "POST":
        body = (
            {"body": "matrix note"} if "notes" in path
            else {"name": "nuclei", "rationale": "matrix"}
        )
    resp = client.request(method, path, headers=headers, json=body)

    # The gate's refusals are the ones this matrix is about; a route's own
    # validation (404/422) still means the gate allowed the request through.
    refused_by_gate = resp.status_code == 403 and (
        "read-only" in resp.text or "requires" in resp.text
    )
    if expected[role]:
        assert not refused_by_gate, (
            f"{role} was refused {label} ({method} {path}): {resp.text}"
        )
    else:
        assert refused_by_gate, (
            f"{role} reached {label} ({method} {path}) — expected the operator "
            f"gate to refuse it. Got {resp.status_code}: {resp.text[:200]}"
        )


def test_every_read_override_names_a_real_route():
    """A stale override silently reverts that route to the member-only default,
    which is the direction that matters: a bulk export quietly losing its
    AUDITOR floor."""
    import app.main  # noqa: F401
    from app.main import app
    from app.api.deps import AGENT_READ_ROLE_OVERRIDES

    mounted = [
        (m.upper(), p)
        for p, ops in app.openapi()["paths"].items()
        for m in ops
        if p.startswith("/api/v1/agent")
    ]
    for (method, rel) in sorted(AGENT_READ_ROLE_OVERRIDES):
        matches = [p for m, p in mounted if m == method and p.endswith(rel)]
        assert len(matches) == 1, (
            f"read-role override {method} {rel!r} matched {len(matches)} routes "
            f"({matches}). Zero means it silently reverted to the default."
        )


def test_bulk_export_routes_are_not_left_on_the_default():
    """A guard against the gap the review found: adding a new export-shaped
    agent route and forgetting it needs the same floor its JWT twin has."""
    import app.main  # noqa: F401
    from app.main import app
    from app.api.deps import AGENT_READ_ROLE_OVERRIDES

    covered = {rel for _m, rel in AGENT_READ_ROLE_OVERRIDES}
    suspicious = []
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api/v1/agent"):
            continue
        if "GET" not in {m.upper() for m in ops}:
            continue
        # Export-shaped: a file extension, or the word "export".
        if not (path.endswith((".ndjson", ".txt", ".csv", ".json")) or "export" in path):
            continue
        if not any(path.endswith(rel) for rel in covered):
            suspicious.append(path)
    assert not suspicious, (
        f"export-shaped agent read routes with no minimum-role override: "
        f"{suspicious}. Give each one the floor its JWT equivalent requires, or "
        "add it here with a reason."
    )
