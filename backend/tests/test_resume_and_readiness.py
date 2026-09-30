"""Targeted tests for the workflow-resume + host-readiness paths.

Minting a key for a session REVOKES every prior active key for that session
(the fix that closed the two-agents-one-session hole) and leaves other
sessions' keys alone.

v2.434.0 — the host-readiness tests (``build_tool_readiness``) went with the
recon planning service and the environment probe: BlueStick no longer checks
an operator's machine against a tool catalogue.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models_auth import APIKey
from app.services.agent_session_service import create_agent_session, mint_session_key


# ---------------------------------------------------------------------------
# One live key per SESSION (v2.337.0 — keys bind to a session, not a plan).
# ---------------------------------------------------------------------------


def test_mint_session_key_revokes_prior_active_key_for_the_session(
    db_session, test_project, test_agent
):
    """A prior active key on the same session is revoked when a new key is
    minted — one live key per session, so a resumed session's orphaned key
    cannot keep writing beside the new one."""
    session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    prior = APIKey(
        agent_id=test_agent.id,
        agent_session_id=session.id,
        name="prior",
        key_hash=hashlib.sha256(b"prior").hexdigest(),
        key_prefix="nm_agent_prio",
        is_active=True,
    )
    db_session.add(prior)
    db_session.commit()

    raw_key = mint_session_key(db_session, agent=test_agent, session=session)
    db_session.commit()
    db_session.refresh(prior)

    assert prior.is_active is False, "prior session key must be revoked on re-mint"
    assert raw_key.startswith("nm_agent_")
    active = (
        db_session.query(APIKey)
        .filter(APIKey.agent_session_id == session.id, APIKey.is_active.is_(True))
        .all()
    )
    assert len(active) == 1
    assert active[0].key_hash == hashlib.sha256(raw_key.encode()).hexdigest()


def test_mint_session_key_leaves_other_sessions_keys_alone(
    db_session, test_project, test_agent
):
    """The revoke is scoped to one session — a second concurrent session's
    key is untouched, so two operators stay isolated."""
    other = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    other_key = APIKey(
        agent_id=test_agent.id,
        agent_session_id=other.id,
        name="other-session-key",
        key_hash=hashlib.sha256(b"other").hexdigest(),
        key_prefix="nm_agent_othe",
        is_active=True,
    )
    db_session.add(other_key)
    db_session.commit()

    mine = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    mint_session_key(db_session, agent=test_agent, session=mine)
    db_session.commit()
    db_session.refresh(other_key)

    assert other_key.is_active is True, "minting for one session must not revoke another's key"
