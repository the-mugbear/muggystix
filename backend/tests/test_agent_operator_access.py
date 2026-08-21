"""An agent key may do what its operator may do — checked every request.

The agent surface used to perform **zero** project-role checks. Authorization
was "which workflow is this key scoped to", an entirely separate model from the
RBAC the rest of the product uses. Two consequences:

* **Role changes never reached live keys.** Demote an analyst to viewer, or
  remove them from the project, and their agent kept its old powers until the
  key expired. v2.304.0 made keys renewable, which *widened* that window.
* It produced a bug class of its own — v2.90.3 patched a viewer minting an agent
  key to bypass the analyst gate on the user-side plan routes. Deriving
  authority from the operator makes that unrepresentable rather than patched.

Nothing narrows for existing deployments: every session-start endpoint already
requires ANALYST, so no viewer or auditor keys exist. This is what makes the
role *stay* true afterwards.
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


_SEQ = [90000]


def _member(db, project, role):
    _SEQ[0] += 1
    user = User(
        id=_SEQ[0],
        username=f"operator-{_SEQ[0]}",
        email=f"operator-{_SEQ[0]}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user


def _assist_key(db, project, user):
    """A live assist session owned by ``user``, and its key."""
    agent = Agent(name=f"agent-{user.id}", project_id=project.id, owner_id=user.id)
    db.add(agent)
    db.flush()
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=project.id, agent_id=agent.id,
        started_by_id=user.id, status="active",
    )
    db.add(base)
    db.flush()
    detail = AssistSession(
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status=AssistSessionStatus.ACTIVE, agent_session_id=base.id,
        purpose="operator access test",
    )
    db.add(detail)
    db.flush()
    raw = "nm_agent_" + secrets.token_urlsafe(32)
    db.add(APIKey(
        agent_id=agent.id, name=f"opaccess-{user.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14], agent_session_id=base.id,
        # Bind the assist scope too, or the workflow guard refuses these routes
        # for an unrelated reason and the test proves nothing about the
        # operator gate.
        assist_session_id=detail.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
    ))
    db.commit()
    return raw, agent


def _host(db, project, ip="10.90.0.1"):
    from app.db import models
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def test_an_analysts_agent_can_read_and_write(client, db_session, test_project):
    user = _member(db_session, test_project, ProjectRole.ANALYST.value)
    raw, _ = _assist_key(db_session, test_project, user)
    host = _host(db_session, test_project, "10.90.1.1")
    headers = {"X-API-Key": raw}

    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200
    wrote = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": "analyst agent note"},
    )
    assert wrote.status_code in (200, 201), wrote.text


def test_a_demotion_reaches_a_live_key_immediately(
    client, db_session, test_project
):
    """The point of checking per request rather than at mint time.

    Pre-v2.305.0 the key kept its powers until expiry — and expiry became
    renewable in v2.304.0, so the window was widening, not closing.
    """
    user = _member(db_session, test_project, ProjectRole.ANALYST.value)
    raw, _ = _assist_key(db_session, test_project, user)
    host = _host(db_session, test_project, "10.90.2.1")
    headers = {"X-API-Key": raw}

    first = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": "before demotion"},
    )
    assert first.status_code in (200, 201)

    membership = (
        db_session.query(ProjectMembership)
        .filter(
            ProjectMembership.project_id == test_project.id,
            ProjectMembership.user_id == user.id,
        )
        .first()
    )
    membership.role = ProjectRole.VIEWER.value
    db_session.commit()

    after = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": "after demotion"},
    )
    assert after.status_code == 403, (
        "the key kept write access after its operator was demoted — a role "
        "change has to reach keys already in the field"
    )
    # Reads survive: a viewer may still read, so their agent may too.
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200


def test_an_auditors_agent_is_read_only(client, db_session, test_project):
    """Auditors get a read-only agent for free — no agent-specific rule to keep
    in sync with the role model."""
    user = _member(db_session, test_project, ProjectRole.AUDITOR.value)
    raw, _ = _assist_key(db_session, test_project, user)
    host = _host(db_session, test_project, "10.90.3.1")
    headers = {"X-API-Key": raw}

    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200
    blocked = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": "auditors do not write"},
    )
    assert blocked.status_code == 403


@pytest.mark.parametrize(
    "role, expected_write",
    [
        (ProjectRole.ANALYST.value, True),
        (ProjectRole.ADMIN.value, True),
        (ProjectRole.AUDITOR.value, False),
        (ProjectRole.VIEWER.value, False),
    ],
)
def test_identity_advertises_the_same_answer_the_write_gate_gives(
    client, db_session, test_project, role, expected_write,
):
    """v2.311.0 — ``can_write_project_data`` must not be a second opinion.

    The capability list was removed because it was an authorization model
    running beside the real one. Publishing a *predicted* authority on identity
    risks recreating exactly that: a number the agent plans around that the gate
    then contradicts. So this asserts the two agree rather than asserting either
    one's value in isolation — if someone changes the write floor in
    ``enforce_agent_operator_access``, this fails until identity follows.
    """
    user = _member(db_session, test_project, role)
    raw, _ = _assist_key(db_session, test_project, user)
    host = _host(db_session, test_project, f"10.90.9.{len(role)}")
    headers = {"X-API-Key": raw}

    identity = client.get("/api/v1/agent/identity", headers=headers)
    assert identity.status_code == 200, identity.text
    body = identity.json()
    assert body["operator"]["project_role"] == role
    assert body["operator"]["is_global_admin"] is False
    assert body["can_write_project_data"] is expected_write

    wrote = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": f"probe from a project {role}"},
    )
    actually_wrote = wrote.status_code in (200, 201)
    assert actually_wrote is body["can_write_project_data"], (
        f"identity said can_write_project_data={body['can_write_project_data']} "
        f"but the write returned {wrote.status_code}: {wrote.text}"
    )


def test_identity_reports_a_global_admin_as_unbounded_by_project_role(
    client, db_session, test_project
):
    """A global admin has no membership row, so ``project_role`` is None — and
    reporting None must not be read as "cannot write". The flag carries it."""
    user = _member(db_session, test_project, ProjectRole.VIEWER.value)
    user.role = UserRole.ADMIN
    db_session.commit()
    raw, _ = _assist_key(db_session, test_project, user)

    body = client.get("/api/v1/agent/identity", headers={"X-API-Key": raw}).json()
    assert body["operator"]["is_global_admin"] is True
    assert body["can_write_project_data"] is True


def test_losing_project_membership_stops_the_key(
    client, db_session, test_project
):
    """A key must not act on a project its operator has left."""
    user = _member(db_session, test_project, ProjectRole.ANALYST.value)
    raw, _ = _assist_key(db_session, test_project, user)
    headers = {"X-API-Key": raw}
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200

    db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == test_project.id,
        ProjectMembership.user_id == user.id,
    ).delete()
    db_session.commit()

    resp = client.get("/api/v1/agent/identity", headers=headers)
    assert resp.status_code == 403
    assert "member" in resp.json()["detail"].lower()


def test_deactivating_the_operator_stops_the_key(
    client, db_session, test_project
):
    """Disabling a user account has to reach their agent too, or offboarding
    leaves a live credential behind."""
    user = _member(db_session, test_project, ProjectRole.ANALYST.value)
    raw, _ = _assist_key(db_session, test_project, user)
    headers = {"X-API-Key": raw}
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200

    user.is_active = False
    db_session.commit()
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 403


def test_session_metadata_writes_stay_open_to_a_read_only_operator(
    client, db_session, test_project
):
    """Renewal and environment probes are writes by HTTP verb but record
    something about the session, not project data. A read-only operator's agent
    needs them as much as anyone's — refusing renewal to the sessions least able
    to recover would be the wrong way round."""
    user = _member(db_session, test_project, ProjectRole.AUDITOR.value)
    raw, _ = _assist_key(db_session, test_project, user)
    headers = {"X-API-Key": raw}

    renewed = client.post("/api/v1/agent/session/renew", headers=headers)
    assert renewed.status_code == 200, renewed.text


def test_a_global_admin_operator_bypasses_membership(
    client, db_session, test_project
):
    """Matches require_project_role: a global admin does not need a membership
    row. Without this an admin's agent would be denied on a project they can
    administer directly."""
    _SEQ[0] += 1
    admin = User(
        id=_SEQ[0], username=f"gadmin-{_SEQ[0]}",
        email=f"gadmin-{_SEQ[0]}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.ADMIN, is_active=True, is_verified=True,
    )
    db_session.add(admin)
    db_session.commit()
    raw, _ = _assist_key(db_session, test_project, admin)
    host = _host(db_session, test_project, "10.90.4.1")
    headers = {"X-API-Key": raw}

    wrote = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers, json={"body": "admin agent note"},
    )
    assert wrote.status_code in (200, 201), wrote.text


def test_every_metadata_write_path_template_actually_matches_a_route():
    """The allowlist is matched by exact (method, path-template) pair.

    A typo, or a route whose path later changes, would silently drop an entry
    out of the allowlist — and the failure is invisible in the direction that
    matters: the route simply starts requiring ANALYST, so a read-only
    operator loses the ability to renew a key or report its environment, with
    nothing to indicate why.
    """
    import app.main  # noqa: F401 - registers the routes
    from app.main import app
    from app.api.deps import AGENT_SESSION_METADATA_WRITES

    # Entries are router-relative (what request.scope["route"].path returns);
    # the OpenAPI map is keyed by the mounted path. Each entry must resolve to
    # exactly one mounted agent route.
    mounted = [
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        for method in ops
        if path.startswith("/api/v1/agent")
    ]
    for method, rel in sorted(AGENT_SESSION_METADATA_WRITES):
        matches = [p for m, p in mounted if m == method and p.endswith(rel)]
        assert len(matches) == 1, (
            f"metadata-write allowlist entry {method} {rel!r} matched "
            f"{len(matches)} mounted routes ({matches}). Zero means that route "
            "now silently requires ANALYST; more than one means the entry is "
            "ambiguous."
        )


@pytest.mark.parametrize(
    "probe_path",
    [
        "/api/v1/agent/assist/sessions/{session_id}/environment",
        "/api/v1/agent/feedback",
        "/api/v1/agent/tool-suggestions",
    ],
)
def test_a_read_only_operator_reaches_the_gated_metadata_writes(
    client, db_session, test_project, probe_path
):
    """The five gated exceptions, exercised through the gate itself.

    The earlier version of this test called only renewal — which is mounted
    OUTSIDE ``enforce_agent_operator_access`` — so it proved nothing about the
    allowlist. These paths do run through the gate, so they show that an
    auditor's agent is not refused for being read-only.

    Asserted as "not 403-from-the-operator-gate" rather than a specific success
    code: these routes have their own validation, and a 404/422 from *inside*
    the handler still proves the gate let the request through, which is the
    only thing this test is about.
    """
    from app.db.models_agent import AssistSession

    user = _member(db_session, test_project, ProjectRole.AUDITOR.value)
    raw, agent = _assist_key(db_session, test_project, user)
    headers = {"X-API-Key": raw}

    if "{session_id}" in probe_path:
        session_id = (
            db_session.query(AssistSession.id)
            .filter(AssistSession.agent_id == agent.id)
            .scalar()
        )
        probe_path = probe_path.replace("{session_id}", str(session_id))
        body = {"os_family": "linux", "shell": "bash"}
    elif probe_path.endswith("/feedback"):
        body = {"category": "prompt", "message": "feedback from a restricted operator"}
    else:
        body = {"name": "nuclei", "rationale": "not in the approved set"}

    resp = client.post(probe_path, headers=headers, json=body)
    # Assert on WHICH gate answered. These routes have their own validation, so
    # a 404/422 from inside the handler still proves the operator gate let the
    # request through — which is the only thing this test is about.
    assert "read-only" not in resp.text, (
        f"{probe_path} was refused by the OPERATOR gate for a read-only "
        f"operator: {resp.text}. It is on the metadata-write allowlist."
    )
