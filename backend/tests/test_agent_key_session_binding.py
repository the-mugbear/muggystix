"""
Every workflow-scoped key carries an ``agent_session_id`` (v2.233.0).

This is the invariant the agent-session *contract* phase depends on. The
four legacy scope columns on ``api_keys`` (test_plan_id / scope_id /
recon_session_id / assist_session_id) can only be dropped once
``agent_session_id`` alone can classify any key — and today nothing enforces
that: all three mint helpers declare ``agent_session_id: Optional[int] = None``,
so a caller that forgets it produces a key that looks *unscoped*, i.e. the
most-privileged classification.

The rotate/renew fix in v2.232.0 defends against this by also checking the
legacy columns. These tests are what let that defence be removed later.
"""

import pytest

from app.db.models_auth import APIKey


def _key_for(db, **filters):
    return (
        db.query(APIKey)
        .filter_by(is_active=True, **filters)
        .order_by(APIKey.created_at.desc())
        .first()
    )


def test_assist_key_is_bound_to_an_agent_session(client, db_session, test_project):
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "session-binding test"},
    )
    assert resp.status_code == 201, resp.text
    key = _key_for(db_session, assist_session_id=resp.json()["assist_session_id"])
    assert key is not None
    assert key.agent_session_id is not None, (
        "an assist key without agent_session_id would classify as an unscoped "
        "global key once the legacy columns are dropped"
    )


def test_recon_resume_backfills_a_missing_agent_session(
    client, db_session, test_project, test_user
):
    """A recon session predating the backfill must not resume into a key with
    a NULL agent_session_id — the exact hole that blocks the contract phase."""
    from app.db.models import Scope
    from app.db.models_agent import Agent, ReconSession, ReconSessionStatus

    scope = Scope(project_id=test_project.id, name="resume-binding-scope")
    db_session.add(scope)
    db_session.commit()
    db_session.refresh(scope)

    agent = Agent(
        name=f"{test_user.username}-agent",
        project_id=test_project.id,
        owner_id=test_user.id,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    # A pre-backfill session: no unified base row.
    legacy = ReconSession(
        project_id=test_project.id,
        scope_id=scope.id,
        agent_id=agent.id,
        started_by_id=test_user.id,
        status=ReconSessionStatus.ACTIVE.value,
        agent_session_id=None,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)
    assert legacy.agent_session_id is None

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}"
        f"/recon/sessions/{legacy.id}/resume",
    )
    if resp.status_code == 404:
        pytest.skip("recon resume route not mounted in this configuration")
    assert resp.status_code in (200, 201), resp.text

    db_session.refresh(legacy)
    assert legacy.agent_session_id is not None, "resume must backfill the base row"

    key = _key_for(db_session, recon_session_id=legacy.id)
    assert key is not None
    assert key.agent_session_id == legacy.agent_session_id


def test_no_route_mints_an_unscoped_key(client, db_session, test_project):
    """v2.295.0 — there is no longer an endpoint that produces an unbound key.

    ``POST /agents/`` and ``POST /agents/{id}/rotate-key`` were the only two,
    and the whole /agents router went with them.  Asserted against the live
    OpenAPI surface rather than by grepping imports, so re-registering the
    router anywhere would fail this."""
    paths = client.app.openapi()["paths"]
    offenders = [p for p in paths if "/agents/" in p or p.endswith("/agents")]
    assert offenders == [], (
        f"an /agents route is mounted again: {offenders}. That surface minted "
        "the unscoped global key, which reached every plan in the project."
    )


def test_an_unscoped_key_cannot_authenticate(client, db_session, test_project, test_agent):
    """Fail closed for a key that predates the removal.

    Stopping the minting is not enough on its own: a legacy unbound key would
    otherwise keep full write authority over every plan, with no UI and no API
    left to rotate or revoke it.  Authentication rejects it outright."""
    import hashlib
    from datetime import datetime, timedelta, timezone

    raw = "nm_agent_orphaned_global_key_fixture"
    db_session.add(APIKey(
        agent_id=test_agent.id,
        name="orphaned-global",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()

    resp = client.get("/api/v1/agent/project", headers={"X-API-Key": raw})
    assert resp.status_code == 403, resp.text
    assert "unscoped" in resp.json()["detail"].lower()
