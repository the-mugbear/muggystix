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
    # v2.470.0 — the page's read (GET /report-writing-guidance) is every
    # signed-in user's, so the agent's is every member's.
    ("writing guidance", "GET", "/api/v1/agent/assist/writing-guidance",
     {"analyst": True, "auditor": True, "viewer": True}),
    # v2.476.0 — the Operations "Findings" tab (GET /workbench/findings) is
    # every member's own page, so the agent's whole-list read is too.
    ("findings that need me", "GET", "/api/v1/agent/assist/workbench/findings",
     {"analyst": True, "auditor": True, "viewer": True}),
    ("bulk export", "GET", "/api/v1/agent/assist/report-context.ndjson",
     {"analyst": True, "auditor": True, "viewer": False}),
    ("project write", "POST", "/api/v1/agent/hosts/{host_id}/notes",
     {"analyst": True, "auditor": False, "viewer": False}),
    ("session metadata", "POST", "/api/v1/agent/tool-suggestions",
     {"analyst": True, "auditor": True, "viewer": True}),
    # v2.428.0 — the agent read takes the UI page's level, both ways:
    # Ingestion Results is an analyst page; evidence images are shown to a
    # viewer (the gate lets the request through; the route then 404s).
    ("analyst page read", "GET", "/api/v1/agent/assist/ingestion-issues",
     {"analyst": True, "auditor": False, "viewer": False}),
    ("evidence file", "GET", "/api/v1/agent/assist/attachments/999999",
     {"analyst": True, "auditor": True, "viewer": True}),
    ("screenshot file", "GET", "/api/v1/agent/assist/web-interfaces/999999/screenshot",
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
    raw = "nm_agent_" + secrets.token_urlsafe(32)
    db.add(APIKey(
        agent_id=agent.id, name=f"matrix-{user.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14], agent_session_id=base.id,
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


#: Every agent read whose page or export requires more than membership of a
#: person, with that role — the route declares it (``deps.agent_read_floor``)
#: and this is the list a reviewer reads.  Anything not here is a viewer's.
_A = "/api/v1/agent"
READ_FLOORS = {
    # Bulk exports (``export.py`` / ``reports.py`` gate their routers on AUDITOR).
    f"{_A}/assist/report-context.ndjson": "auditor",
    f"{_A}/assist/hosts.ndjson": "auditor",
    f"{_A}/scopes/{{scope_id}}/hosts.ndjson": "auditor",
    f"{_A}/scopes/{{scope_id}}/live-hosts.txt": "auditor",
    f"{_A}/scopes/{{scope_id}}/web-targets.txt": "auditor",
    f"{_A}/scopes/{{scope_id}}/named-targets.ndjson": "auditor",
    # Ingestion Results is an analyst page (every /parse-errors route).
    f"{_A}/assist/ingestion-issues": "analyst",
    f"{_A}/assist/uninterpreted-lines": "analyst",
    # The Reports page (client_reports router).
    f"{_A}/assist/client-reports": "auditor",
    f"{_A}/assist/client-reports/{{report_id}}": "auditor",
    f"{_A}/assist/client-reports/{{report_id}}/files/{{fmt}}": "auditor",
    f"{_A}/assist/client-reports/{{report_id}}/scope.csv": "auditor",
    # Remediation tracking (its page's floor; the same router factory).
    f"{_A}/remediation": "auditor",
    f"{_A}/remediation/export": "auditor",
    f"{_A}/remediation/contacts": "auditor",
    f"{_A}/remediation/follow-up": "auditor",
    f"{_A}/remediation/teams": "auditor",
    f"{_A}/remediation/trend": "auditor",
    f"{_A}/remediation/contact-report/{{job_id}}": "auditor",
    f"{_A}/remediation/contact-report/{{job_id}}/download": "auditor",
    f"{_A}/remediation/hosts/{{host_id}}/events": "auditor",
}


def _declared_read_floors():
    import app.main  # noqa: F401
    from app.main import app
    from tests.agent_route_declarations import agent_route_declarations

    return {
        path: access.read_floor.value
        for (method, path), access in agent_route_declarations(app).items()
        if method == "GET"
    }


def test_every_agent_read_declares_its_pages_floor_and_no_other():
    """Both directions: a read losing its floor (a viewer's agent gets what
    the viewer's own session is refused) and a read gaining one its page does
    not have (an agent holding references it cannot open) both fail here."""
    declared = _declared_read_floors()
    above_default = {path: role for path, role in declared.items() if role != "viewer"}
    assert above_default == READ_FLOORS


def test_findings_that_need_me_is_a_members_read_like_its_page():
    """``GET /projects/{id}/workbench/findings`` asks for membership only
    (``get_current_project``); the agent read declares the same floor on its
    route."""
    declared = _declared_read_floors()
    assert declared[f"{_A}/assist/workbench/findings"] == "viewer"


def test_bulk_export_routes_are_not_left_on_the_default():
    """A guard against the gap the review found: adding a new export-shaped
    agent route and forgetting it needs the same floor its JWT twin has."""
    suspicious = [
        path for path, role in _declared_read_floors().items()
        # Export-shaped: a file extension, or the word "export".
        if (path.endswith((".ndjson", ".txt", ".csv", ".json")) or "export" in path)
        and role == "viewer"
    ]
    assert not suspicious, (
        f"export-shaped agent read routes with no declared read floor: "
        f"{suspicious}. Give each one the floor its JWT equivalent requires "
        "(dependencies=[Depends(agent_read_floor(...))])."
    )


def test_the_remediation_reads_take_their_pages_floor_from_one_constant():
    """The page's routes and the agents' are one router factory; the floor is
    ``remediation.READ_ROLE`` on both sides, for every read it has."""
    from app.api.v1.endpoints import remediation
    from fastapi.routing import APIRoute

    declared = _declared_read_floors()
    reads = [
        r.path for r in remediation.router.routes
        if isinstance(r, APIRoute) and "GET" in r.methods
    ]
    assert len(reads) >= 8
    for rel in reads:
        assert declared[_A + rel] == remediation.READ_ROLE.value, rel


@pytest.mark.parametrize("role", ["analyst", "auditor", "viewer"])
def test_the_gate_enforces_every_declared_floor(client, db_session, test_project, role):
    """The declaration is what the gate reads: each floored route refuses a
    role below it and lets the others through (to the route's own answer)."""
    from app.core.security import check_permissions

    user = _member(db_session, test_project, role)
    headers = {"X-API-Key": _key_for(db_session, test_project, user)}
    for template, floor in READ_FLOORS.items():
        path = (
            template.replace("{scope_id}", "999999").replace("{report_id}", "999999")
            .replace("{fmt}", "html").replace("{job_id}", "999999").replace("{host_id}", "999999")
        )
        resp = client.get(path, headers=headers, params={"contact_email": "a@example.com"})
        refused_by_gate = resp.status_code == 403 and "requires" in resp.text
        assert refused_by_gate == (not check_permissions(role, floor)), (
            f"{role} on {path} (floor {floor}): {resp.status_code} {resp.text[:160]}"
        )
