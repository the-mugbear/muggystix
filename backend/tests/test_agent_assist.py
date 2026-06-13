"""
Smoke tests for the v2.64.0 agent-assist workflow.

Covers the four invariants the workflow boundary depends on:

  1. JWT user can start a session and receives a working API key.
  2. The assist key can read /agent/assist/* endpoints (project-
     scoped, read-only data).
  3. The assist key is rejected by /agent/test-plans/* and
     /agent/recon/*, and by writes on /agent/hosts/* — even though
     the underlying `Agent` row is the same as for the other
     workflows.
  4. Ending the session moves it to 'ended' and the key stops working
     (next request returns 410 from the active-status guard).
"""

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_session(client, project_id, purpose="Smoke-test assist v1"):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": purpose},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_start_assist_session_returns_key_and_instructions(client, test_project):
    body = _start_session(client, test_project.id)
    assert body["project_id"] == test_project.id
    assert body["assist_session_id"] > 0
    assert body["api_key"].startswith("nm_agent_")
    # Instructions must mention the assist surface so a misrouted
    # paste doesn't accidentally drive a recon/plan agent.
    assert "/agent/assist/" in body["instructions"]
    # And the prompt must surface the session id so the agent
    # POSTs the env probe to the right path.
    assert str(body["assist_session_id"]) in body["instructions"]


def test_start_assist_populates_unified_agent_session(client, test_project, db_session):
    """R5 expand-completion: starting an assist session now also creates the
    unified AgentSession base row and links both the detail row and the minted
    key to it (was left null for the backfill migration)."""
    from app.db.models_agent import AssistSession, AgentSession, AgentSessionWorkflow
    from app.db.models_auth import APIKey

    body = _start_session(client, test_project.id)
    sid = body["assist_session_id"]

    detail = db_session.query(AssistSession).filter(AssistSession.id == sid).first()
    assert detail.agent_session_id is not None

    base = db_session.query(AgentSession).filter(AgentSession.id == detail.agent_session_id).first()
    assert base is not None
    assert base.workflow == AgentSessionWorkflow.ASSIST.value
    assert base.project_id == test_project.id

    key = (
        db_session.query(APIKey)
        .filter(APIKey.assist_session_id == sid, APIKey.is_active.is_(True))
        .first()
    )
    assert key.agent_session_id == base.id


def test_assist_key_can_read_context_and_hosts(client, test_project):
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Context endpoint — should return project summary.
    ctx = client.get("/api/v1/agent/assist/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    data = ctx.json()
    assert data["project"]["id"] == test_project.id
    assert data["session"]["id"] == body["assist_session_id"]

    # Hosts endpoint — empty for a project with no hosts, but must not 401/403.
    hosts = client.get("/api/v1/agent/assist/hosts", headers=headers)
    assert hosts.status_code == 200, hosts.text
    assert isinstance(hosts.json(), list)


def test_assist_key_blocked_from_plan_and_recon_surfaces(client, test_project, test_plan):
    """The bedrock workflow-boundary guarantee: an assist key can't
    masquerade as a plan/recon key.  Both rejections come from the
    require_plan_scope / require_recon_scope deps explicitly checking
    request.state.scoped_assist_session_id.
    """
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Plan endpoint — should 403 (not 401), reasoned by scope mismatch.
    plan = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/context",
        headers=headers,
    )
    assert plan.status_code == 403, plan.text
    assert "assist" in plan.json()["detail"].lower()

    # Recon endpoint — same shape.
    recon = client.get("/api/v1/agent/recon/context", headers=headers)
    assert recon.status_code == 403, recon.text
    assert "assist" in recon.json()["detail"].lower()


def test_assist_key_cannot_write_notes_or_follow(client, test_project, db_session):
    """v1 assist is strictly read-only.  The two writes on agent_browse
    (notes + follow) must 403 even though the assist key authenticates
    as the agent the same way a plan/recon key does."""
    from app.db.models import Host
    host = Host(
        project_id=test_project.id,
        ip_address="10.99.0.1",
        state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    note = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers,
        json={"body": "should be rejected", "status": "info"},
    )
    assert note.status_code == 403, note.text
    assert "read-only" in note.json()["detail"].lower()

    follow = client.post(
        f"/api/v1/agent/hosts/{host.id}/follow",
        headers=headers,
        json={"status": "watching"},
    )
    assert follow.status_code == 403, follow.text
    assert "read-only" in follow.json()["detail"].lower()


def test_end_session_revokes_key(client, test_project):
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Sanity: key works before end.
    pre = client.get("/api/v1/agent/assist/context", headers=headers)
    assert pre.status_code == 200, pre.text

    # End the session via the JWT-side endpoint.
    end = client.post(
        f"/api/v1/projects/{test_project.id}/assist/sessions/"
        f"{body['assist_session_id']}/end"
    )
    assert end.status_code == 204, end.text

    # Key should now be rejected — the API key row was deactivated, so
    # get_current_agent's `is_active.is_(True)` filter no longer matches.
    post = client.get("/api/v1/agent/assist/context", headers=headers)
    assert post.status_code == 401, post.text

    # Second end is idempotent in spirit but reports 409.
    second = client.post(
        f"/api/v1/projects/{test_project.id}/assist/sessions/"
        f"{body['assist_session_id']}/end"
    )
    assert second.status_code == 409, second.text


def test_environment_probe_returns_valid_response(client, test_project):
    """v2.64.1 regression — the initial v2.64.0 commit omitted
    session_type from EnvironmentProbeResponse, so Pydantic 500'd
    the response AFTER the DB write committed.  An agent saw a
    confusing 500 and retried, polluting the audit log.  Guard against
    a future schema bump that breaks the response again.
    """
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Minimal valid EnvironmentProbeRequest — os_family is the only
    # required field (everything else is shaped for richer probes).
    resp = client.post(
        f"/api/v1/agent/assist/sessions/{body['assist_session_id']}/environment",
        headers=headers,
        json={"os_family": "linux"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == body["assist_session_id"]
    assert data["session_type"] == "assist"
    assert data["probed_at"] is not None
    # environment echo back — empty input round-trips to an empty
    # EnvironmentSummary, not a 500.
    assert isinstance(data["environment"], dict)


def test_assist_session_listing_includes_started_session(client, test_project):
    body = _start_session(client, test_project.id, purpose="Listing smoke test")
    listing = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    matching = [r for r in rows if r["id"] == body["assist_session_id"]]
    assert len(matching) == 1
    assert matching[0]["purpose"] == "Listing smoke test"
    assert matching[0]["status"] == "active"


def test_assist_hosts_q_dsl_follow_resolves_to_operator(
    client, test_project, test_user, db_session
):
    """The headline gap-closer: ``/agent/assist/hosts?q=`` runs the full
    Hosts query DSL, and ``follow:`` resolves against the session operator —
    so an assist agent can answer "show me the hosts I have in review"
    (which the discrete filters could not express)."""
    from app.db.models import Host, HostFollow, FollowStatus

    reviewing = Host(
        project_id=test_project.id, ip_address="10.50.0.1", state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    other = Host(
        project_id=test_project.id, ip_address="10.50.0.2", state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db_session.add_all([reviewing, other])
    db_session.commit()
    db_session.refresh(reviewing)

    # The operator (the user the client authenticates as, == session.started_by)
    # has exactly one host in review.
    db_session.add(
        HostFollow(host_id=reviewing.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW)
    )
    db_session.commit()

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    in_review = client.get(
        "/api/v1/agent/assist/hosts?q=follow:in_review", headers=headers
    )
    assert in_review.status_code == 200, in_review.text
    assert {h["ip_address"] for h in in_review.json()} == {"10.50.0.1"}

    # The operator has nothing marked reviewed → empty, not an error.
    reviewed = client.get(
        "/api/v1/agent/assist/hosts?q=follow:reviewed", headers=headers
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json() == []


def test_assist_hosts_q_dsl_malformed_is_400(client, test_project):
    """A malformed DSL query is a clean 400 (DSLError), not a 500."""
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    resp = client.get("/api/v1/agent/assist/hosts?q=port:notaport", headers=headers)
    assert resp.status_code == 400, resp.text
