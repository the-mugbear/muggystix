"""
Rotate / renew must act only on the UNSCOPED global agent key (v2.232.0).

All four agent workflows resolve to the same ``Agent`` row (one agent per
(user, project)), so the only thing separating a global key from a session
key is its scope binding.  Rotate previously filtered on ``test_plan_id``
alone — recon and assist keys leave that NULL, so rotating the global key
hard-401'd every in-flight recon and assist session for that user.  Renew
filtered on ``test_plan_id`` + ``scope_id``, which still matched assist keys
and, being ordered newest-first, reliably renewed an assist key instead of
the global one — extending a deliberately 4-hour read-only credential.

These tests hold all four key kinds at once, which is the configuration
neither endpoint was ever exercised against.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_auth import APIKey
from app.db.models_agent import (
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
    ReconSession,
    ReconSessionStatus,
)
from app.db.models import Scope


def _mint(db, agent, name, **scope_columns):
    key = APIKey(
        agent_id=agent.id,
        name=name,
        key_hash=f"hash-{name}",
        key_prefix=f"nm_agent_{name}"[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        **scope_columns,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


@pytest.fixture
def four_keys(db_session, test_project, test_agent, test_user, test_plan):
    """One key of each kind on the SAME agent — the untested configuration."""
    scope = Scope(project_id=test_project.id, name="rotation-fixture-scope")
    db_session.add(scope)
    db_session.commit()
    db_session.refresh(scope)

    def _session(workflow, **kw):
        s = AgentSession(
            workflow=workflow,
            project_id=test_project.id,
            agent_id=test_agent.id,
            started_by_id=test_user.id,
            status="active",
            **kw,
        )
        db_session.add(s)
        db_session.commit()
        db_session.refresh(s)
        return s

    plan_session = _session(
        AgentSessionWorkflow.PLAN_GENERATION.value, plan_id=test_plan.id
    )
    recon_base = _session(AgentSessionWorkflow.RECON.value, scope_id=scope.id)
    assist_base = _session(AgentSessionWorkflow.ASSIST.value)

    recon_session = ReconSession(
        project_id=test_project.id,
        scope_id=scope.id,
        agent_id=test_agent.id,
        started_by_id=test_user.id,
        status=ReconSessionStatus.ACTIVE.value,
        agent_session_id=recon_base.id,
    )
    assist_session = AssistSession(
        project_id=test_project.id,
        agent_id=test_agent.id,
        started_by_id=test_user.id,
        status=AssistSessionStatus.ACTIVE.value,
        agent_session_id=assist_base.id,
    )
    db_session.add_all([recon_session, assist_session])
    db_session.commit()
    db_session.refresh(recon_session)
    db_session.refresh(assist_session)

    return {
        "global": _mint(db_session, test_agent, "global"),
        "plan": _mint(
            db_session, test_agent, "plan",
            test_plan_id=test_plan.id, agent_session_id=plan_session.id,
        ),
        "recon": _mint(
            db_session, test_agent, "recon",
            scope_id=scope.id, recon_session_id=recon_session.id,
            agent_session_id=recon_base.id,
        ),
        "assist": _mint(
            db_session, test_agent, "assist",
            assist_session_id=assist_session.id, agent_session_id=assist_base.id,
        ),
    }


def test_rotate_revokes_only_the_global_key(
    client, db_session, test_project, test_agent, four_keys
):
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/agents/{test_agent.id}/rotate-key"
    )
    assert resp.status_code in (200, 201), resp.text

    for kind in ("plan", "recon", "assist"):
        db_session.refresh(four_keys[kind])
        assert four_keys[kind].is_active is True, (
            f"rotating the global key revoked the {kind} key — an in-flight "
            f"{kind} session would 401 mid-run"
        )

    db_session.refresh(four_keys["global"])
    assert four_keys["global"].is_active is False, "the old global key must be revoked"


def test_renew_extends_the_global_key_not_the_newest_assist_key(
    client, db_session, test_project, test_agent, four_keys
):
    """The assist key is created LAST, so a newest-first query without a
    proper scope filter picks it."""
    assist_before = four_keys["assist"].expires_at
    global_before = four_keys["global"].expires_at

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/agents/{test_agent.id}/renew-key"
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(four_keys["assist"])
    db_session.refresh(four_keys["global"])

    assert four_keys["assist"].expires_at == assist_before, (
        "renew extended the assist key — a 4-hour read-only credential must "
        "not inherit the global key's TTL"
    )
    assert four_keys["global"].expires_at > global_before, (
        "renew did not extend the global key it was asked to renew"
    )


def test_renew_404s_when_only_scoped_keys_exist(
    client, db_session, test_project, test_agent, four_keys
):
    """With the global key gone, renew must report that there is nothing to
    renew rather than silently grabbing a session key."""
    four_keys["global"].is_active = False
    db_session.commit()

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/agents/{test_agent.id}/renew-key"
    )
    assert resp.status_code == 404, resp.text

    for kind in ("plan", "recon", "assist"):
        db_session.refresh(four_keys[kind])
        assert four_keys[kind].is_active is True
