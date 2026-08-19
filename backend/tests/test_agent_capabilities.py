"""
Capability gate on the agent surface (v2.231.0).

Before this, "assist sessions are read-only" was held up by hand-written
deny guards copied into each write handler.  Forgetting one silently
granted write access to a read-only key.  Authority is now a positive,
fail-closed capability on the session, and the first test here pins the
property for endpoints that DON'T EXIST YET: every write route under
/agent/* must refuse a capability-less key unless it is on an explicit,
justified allowlist.

The rest covers the narrow write grant an operator can hand an assist
session — notes + review status on hosts assigned to that operator — and
the agent-authorship stamp those writes leave behind.
"""

from datetime import datetime, timezone

import pytest

from app.db.models import Host, HostFollow, FollowStatus, Annotation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_session(client, project_id, *, can_write=False):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": "capability tests", "can_write_assigned": can_write},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers(api_key: str) -> dict:
    return {"X-API-Key": api_key}


def _make_host(db_session, project_id, ip):
    host = Host(
        project_id=project_id,
        ip_address=ip,
        state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


def _assign(db_session, host_id, user_id):
    db_session.add(
        HostFollow(
            host_id=host_id,
            user_id=user_id,
            status=FollowStatus.IN_REVIEW,
            assigned_at=datetime.now(timezone.utc),
            assigned_by_id=user_id,
        )
    )
    db_session.commit()


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------

# Writes an assist key MAY perform, with the reason each is safe.  Anything
# not listed here must refuse a capability-less key.  Adding a new agent write
# route fails this test until someone decides which bucket it belongs in —
# that forced decision is the point.
ASSIST_PERMITTED_WRITES = {
    # The MCP transport (v2.268.0).  Mutation-capable — a tools/call can add a
    # note, set review status, or patch a host — but it makes no authority
    # decision of its own: every call loops back into the guarded /agent/*
    # endpoint below, so the capability gate runs there, on the real route,
    # against the same key.  Sending it a capability-less key therefore yields
    # a tool result carrying the endpoint's 403 rather than a bare HTTP 403,
    # which is why it can't satisfy the sweep below.  That behaviour is pinned
    # by test_mcp_assist.py::test_write_tool_blocked_for_readonly_key.
    ("POST", "/api/v1/mcp"),
    # MCP session termination.  DELETE by verb, but the server is stateless —
    # it stores no session, so this is a 204 no-op that touches no project data
    # and no key material.
    ("DELETE", "/api/v1/mcp"),
    # Records the operator's OS/shell onto the session's own row.  Session
    # metadata, not project data.
    ("POST", "/api/v1/agent/assist/sessions/{session_id}/environment"),
    # Agent feedback about the prompt itself; `source` is forced to "assist"
    # and agent_id/project_id come from the key, not the payload.
    ("POST", "/api/v1/agent/feedback"),
    # Records "I wanted a tool you don't approve" (v2.278.0).  Deliberately
    # ungated: the row lands as `suggested`, which no approval rule reads, so it
    # grants nothing — and gating it would silence exactly the sessions most
    # likely to hit the edge of the approved set, which is the one thing this
    # route exists to capture.  Attribution comes from the key, and the stored
    # rationale is length-capped so repeat asks can't grow a column without
    # bound.
    ("POST", "/api/v1/agent/tool-suggestions"),
}


MCP_TRANSPORT_PATH = "/api/v1/mcp"


def _agent_write_routes():
    """Every mutating route mounted under /api/v1/agent (plus the MCP transport).

    Enumerated from the OpenAPI schema, not by iterating ``app.routes``:
    FastAPI 0.141 stores an included router as a single ``_IncludedRouter``
    node instead of flattening its sub-routes into ``app.routes``, so the old
    flat iteration finds nothing. No agent route is hidden from the schema
    (none use ``include_in_schema=False``), so the schema is a complete source
    of every write route — which is exactly what this completeness check needs.
    """
    from app.main import app

    seen = []
    for path, operations in app.openapi().get("paths", {}).items():
        # v2.268.0 — the MCP transport is not under /agent but IS
        # mutation-capable (tools/call reaches the write endpoints), so it has
        # to face this completeness check like any other write surface.
        if not (path.startswith("/api/v1/agent") or path == MCP_TRANSPORT_PATH):
            continue
        for method in operations:
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                seen.append((method.upper(), path))
    return sorted(set(seen))


def test_every_agent_write_route_refuses_a_capability_less_key(client, test_project):
    """A read-only assist key must be refused by EVERY agent write route.

    This is the regression that the old per-handler deny guards could not
    prevent: a new write endpoint that forgets its guard.  Here, a new
    endpoint is refused by default (no capability declared -> no authority),
    and adding it to ASSIST_PERMITTED_WRITES is a deliberate act.
    """
    body = _start_session(client, test_project.id, can_write=False)
    headers = _headers(body["api_key"])

    routes = _agent_write_routes()
    assert routes, "expected to discover agent write routes"

    failures = []
    for method, path in routes:
        if (method, path) in ASSIST_PERMITTED_WRITES:
            continue
        # Fill path params with a plausible id; the auth/capability layer runs
        # as a dependency, so it answers before the handler sees the value.
        url = path
        for param in ("project_id", "plan_id", "host_id", "session_id",
                      "entry_id", "scope_id", "finding_id", "note_id",
                      "result_id", "job_id", "scan_id"):
            url = url.replace("{" + param + "}", "1")
        if "{" in url:
            failures.append(f"{method} {path}: unmapped path param")
            continue
        resp = client.request(method, url, headers=headers, json={})
        if resp.status_code != 403:
            failures.append(
                f"{method} {path}: expected 403 for a read-only assist key, "
                f"got {resp.status_code}"
            )

    assert not failures, (
        "Agent write routes reachable by a capability-less key:\n  "
        + "\n  ".join(failures)
        + "\n\nDeclare a capability on the route via require_capability(), or "
          "add it to ASSIST_PERMITTED_WRITES with a justification."
    )


# ---------------------------------------------------------------------------
# The write grant
# ---------------------------------------------------------------------------

def test_read_only_session_reports_no_capabilities(client, test_project):
    body = _start_session(client, test_project.id, can_write=False)
    assert body["capabilities"] == []
    assert body["capability_constraint"] is None
    # The prompt must keep its original read-only framing.
    assert "read-only" in body["instructions"].lower()
    assert "/agent/hosts/<host_id>/notes" not in body["instructions"]


def test_write_session_reports_scoped_capabilities(client, test_project):
    body = _start_session(client, test_project.id, can_write=True)
    assert sorted(body["capabilities"]) == ["write:follow", "write:host", "write:notes"]
    assert body["capability_constraint"] == "assigned"
    # And the prompt must tell the agent what it may write + where.
    assert "/agent/hosts/<host_id>/notes" in body["instructions"]
    assert "assigned:me" in body["instructions"]


def test_write_grant_allows_note_on_assigned_host(
    client, test_project, test_user, db_session
):
    host = _make_host(db_session, test_project.id, "10.99.1.1")
    _assign(db_session, host.id, test_user.id)

    body = _start_session(client, test_project.id, can_write=True)
    resp = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=_headers(body["api_key"]),
        json={"body": "Port 21 banner suggests vsftpd 2.3.4", "status": "open"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["actor_type"] == "agent"


def test_write_grant_refuses_note_on_unassigned_host(
    client, test_project, db_session
):
    """The constraint is the whole point: granted write does not mean
    project-wide write."""
    host = _make_host(db_session, test_project.id, "10.99.1.2")  # not assigned

    body = _start_session(client, test_project.id, can_write=True)
    resp = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=_headers(body["api_key"]),
        json={"body": "should be rejected", "status": "open"},
    )
    assert resp.status_code == 403, resp.text
    assert "not assigned" in resp.json()["detail"].lower()


def test_write_grant_allows_follow_on_assigned_host_only(
    client, test_project, test_user, db_session
):
    assigned = _make_host(db_session, test_project.id, "10.99.1.3")
    other = _make_host(db_session, test_project.id, "10.99.1.4")
    _assign(db_session, assigned.id, test_user.id)

    body = _start_session(client, test_project.id, can_write=True)
    headers = _headers(body["api_key"])

    ok = client.post(
        f"/api/v1/agent/hosts/{assigned.id}/follow",
        headers=headers,
        json={"status": "in_review"},
    )
    assert ok.status_code == 204, ok.text

    denied = client.post(
        f"/api/v1/agent/hosts/{other.id}/follow",
        headers=headers,
        json={"status": "in_review"},
    )
    assert denied.status_code == 403, denied.text


# ---------------------------------------------------------------------------
# Agent authorship
# ---------------------------------------------------------------------------

def test_agent_note_is_stamped_with_actor_and_session(
    client, test_project, test_user, db_session
):
    """``user_id`` stays the operator (the agent acts as them), so
    ``actor_type`` is the only thing that distinguishes an agent-written
    note from a hand-typed one — in the UI and in client reports."""
    from app.db.models_agent import AssistSession

    host = _make_host(db_session, test_project.id, "10.99.1.5")
    _assign(db_session, host.id, test_user.id)

    body = _start_session(client, test_project.id, can_write=True)
    resp = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=_headers(body["api_key"]),
        json={"body": "agent observation", "status": "open"},
    )
    assert resp.status_code == 201, resp.text

    note = db_session.query(Annotation).filter(Annotation.id == resp.json()["id"]).first()
    assert note.actor_type == "agent"
    assert note.user_id == test_user.id  # attributed to the operator
    detail = (
        db_session.query(AssistSession)
        .filter(AssistSession.id == body["assist_session_id"])
        .first()
    )
    assert note.agent_session_id == detail.agent_session_id


def test_human_note_is_stamped_user(client, test_project, test_user, db_session):
    """The default must stay 'user' so existing notes and the UI path are
    unaffected by the new column."""
    host = _make_host(db_session, test_project.id, "10.99.1.6")
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/notes",
        json={"body": "typed by a human", "status": "open"},
    )
    assert resp.status_code in (200, 201), resp.text
    note = db_session.query(Annotation).filter(Annotation.host_id == host.id).first()
    assert note.actor_type == "user"
    assert note.agent_session_id is None


def test_capabilities_visible_on_session_list_for_audit(client, test_project):
    """A reviewer must be able to see which sessions carried write authority."""
    _start_session(client, test_project.id, can_write=True)
    resp = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions")
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert sorted(row["capabilities"]) == ["write:follow", "write:host", "write:notes"]
    assert row["capability_constraint"] == "assigned"
