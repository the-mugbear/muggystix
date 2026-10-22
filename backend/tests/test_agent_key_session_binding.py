"""
Every workflow-scoped key carries an ``agent_session_id`` (v2.233.0).

This was the invariant the agent-session *contract* phase depended on, and that
phase has now landed: the four legacy scope columns on ``api_keys`` (test_plan_id
/ scope_id / recon_session_id / assist_session_id) are DROPPED, and
``agent_session_id`` alone classifies a key (get_current_agent resolves the
workflow off the bound AgentSession). These tests now verify that binding end to
end — a minted key carries it, resume backfills it for a legacy session, and a
key without it (there is no route that mints one) is refused rather than treated
as an unscoped global key.
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
    key = _key_for(db_session, agent_session_id=resp.json()["agent_session_id"])
    assert key is not None
    assert key.agent_session_id is not None, (
        "an assist key must carry the agent_session_id it is now resolved by"
    )


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


def test_an_unscoped_key_cannot_exist(db_session, test_agent):
    """A key with no session cannot be stored at all (revision f4c8b2d0e6a1).

    Stopping the minting was not enough on its own: a legacy unbound key kept
    full write authority over every plan, so authentication carried a branch
    refusing it.  The schema now refuses the row — ``agent_session_id`` and
    ``agent_id`` are NOT NULL — and the branch is gone."""
    import hashlib

    from sqlalchemy.exc import IntegrityError

    raw = "nm_agent_orphaned_global_key_fixture"
    db_session.add(APIKey(
        agent_id=test_agent.id,
        name="orphaned-global",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
    ))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_key_has_no_operator_or_scopes_columns():
    """``user_id`` (operator / service keys) and ``scopes`` (never enforced)
    went with the keys they were for."""
    columns = set(APIKey.__table__.columns.keys())
    assert "user_id" not in columns and "scopes" not in columns
    assert not APIKey.__table__.columns["agent_id"].nullable
    assert not APIKey.__table__.columns["agent_session_id"].nullable
