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


def test_global_agent_key_is_deliberately_unbound(client, db_session, test_project):
    """The one key that legitimately has no session — this is what makes
    ``agent_session_id IS NULL`` a usable definition of 'unscoped'."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/agents/",
        json={"name": "binding-test-agent"},
    )
    assert resp.status_code == 201, resp.text
    key = _key_for(db_session, agent_id=resp.json()["id"])
    assert key is not None
    assert key.agent_session_id is None
    assert key.test_plan_id is None
    assert key.scope_id is None
    assert key.recon_session_id is None
    assert key.assist_session_id is None
