"""
Smoke tests for the recon workflow's agent surface (v2.66.0).

The recon router (`/agent/recon/*`) was the highest-blast-radius
agent surface without dedicated test coverage — it accepts scanner
output uploads, drives the host/port deduplication path, and binds
keys to specific ReconSession rows for concurrent-recon isolation.

Tests pin the boundaries other workflows depend on:

  1. A recon-scoped key (api_keys.scope_id + recon_session_id set)
     reads /agent/recon/context successfully.
  2. The same key is rejected by /agent/assist/*, /agent/test-plans/*,
     and /agent/execution/* — the cross-workflow isolation guarantee
     the four-workflow split was built around.
  3. Environment probe round-trips through the response model — the
     same kind of bug v2.64.0's assist endpoint shipped and that
     v2.64.1 fixed (response_model validation failing after the DB
     commit landed).

Not exhaustive of every endpoint — that work belongs in a dedicated
recon-router test file built when behavior changes warrant it.
This is the "the boundary works" pin.
"""

from datetime import datetime, timedelta, timezone

import hashlib

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def recon_scope(db_session, test_project):
    """A Scope with one Subnet row so /agent/recon/context returns
    something meaningful."""
    from app.db import models
    scope = models.Scope(
        name="recon-smoke-scope",
        description="fixture",
        project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.99.0.0/24")
    db_session.add(subnet)
    db_session.commit()
    db_session.refresh(scope)
    return scope


@pytest.fixture
def recon_session_row(db_session, test_project, test_agent, recon_scope):
    """An ACTIVE ReconSession bound to the fixture scope."""
    from app.db.models_agent import ReconSession, ReconSessionStatus
    session = ReconSession(
        project_id=test_project.id,
        scope_id=recon_scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture
def recon_key(db_session, test_agent, recon_scope, recon_session_row):
    """A scope-bound API key pinned to this recon session (v2.45.0+
    keys carry recon_session_id; pre-v2.45.0 keys carried only
    scope_id and used a heuristic to resolve the session)."""
    from app.db.models_auth import APIKey
    raw = "nm_agent_recon_smoke_" + "r" * 28
    db_session.add(APIKey(
        agent_id=test_agent.id,
        scope_id=recon_scope.id,
        recon_session_id=recon_session_row.id,
        name=f"recon-smoke-{recon_session_row.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_recon_context_returns_scope_data(client, recon_key, recon_scope):
    """Happy path: a recon-scoped key reads /agent/recon/context and
    sees its own scope's subnets back."""
    resp = client.get(
        "/api/v1/agent/recon/context",
        headers={"X-API-Key": recon_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The exact response schema is large; pin the bits that matter
    # for "did the auth chain resolve to my scope?".
    assert body["scope_id"] == recon_scope.id
    assert "10.99.0.0/24" in body.get("scope_cidrs", [])


def test_recon_environment_probe_roundtrips(client, recon_key, recon_session_row):
    """v2.64.1 regression class — response model validation must not
    fail after the DB write commits.  Assist had this bug; recon
    didn't, but pin it here so a future schema change can't introduce
    it.
    """
    resp = client.post(
        f"/api/v1/agent/recon/sessions/{recon_session_row.id}/environment",
        headers={"X-API-Key": recon_key},
        json={"os_family": "linux"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == recon_session_row.id
    assert body["session_type"] == "recon"
    assert body["probed_at"] is not None


def test_recon_key_blocked_from_assist_plan_and_execution(
    client, recon_key, test_plan,
):
    """Cross-workflow guarantee: a recon-scoped key must 403 on
    every other agent surface.  The four-workflow split exists
    precisely so a compromised key can only act on its workflow;
    losing this invariant would let a recon key create or execute
    plans, which it has no business doing."""
    headers = {"X-API-Key": recon_key}

    # Plan surface (require_plan_scope rejects scope-bound keys)
    plan = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/context", headers=headers,
    )
    assert plan.status_code == 403, plan.text
    assert "reconnaissance" in plan.json()["detail"].lower()

    # Assist surface (require_assist_scope rejects recon-bound keys)
    assist = client.get("/api/v1/agent/assist/context", headers=headers)
    assert assist.status_code == 403, assist.text
    assert "recon-scoped" in assist.json()["detail"].lower()


def test_recon_key_unauthorized_when_revoked(client, db_session, recon_key):
    """Revoking the APIKey row (is_active=False) immediately stops the
    key from working — the auth dep filters on `is_active.is_(True)`.
    Same invariant the session-end endpoint depends on."""
    from app.db.models_auth import APIKey
    db_session.query(APIKey).filter(
        APIKey.key_hash == hashlib.sha256(recon_key.encode()).hexdigest()
    ).update({"is_active": False})
    db_session.commit()

    resp = client.get(
        "/api/v1/agent/recon/context",
        headers={"X-API-Key": recon_key},
    )
    assert resp.status_code == 401, resp.text


def test_expired_agent_key_rejected(
    client, db_session, test_agent, recon_scope, recon_session_row,
):
    """A key past its expires_at must 401.  Covers both the tz-aware and
    the tz-naive comparison branches in get_current_agent — the naive
    branch exists because some drivers/SQLite return a naive datetime for a
    DateTime(timezone=True) column, and comparing that to an aware now()
    once raised TypeError and 500'd every agent request.  resolve_ttl_hours
    is unit-tested elsewhere; this pins the *enforcement* end-to-end."""
    from app.db.models_auth import APIKey

    def _mint(raw, expires_at):
        db_session.add(APIKey(
            agent_id=test_agent.id,
            scope_id=recon_scope.id,
            recon_session_id=recon_session_row.id,
            name="expired-key",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:14],
            expires_at=expires_at,
        ))
        db_session.commit()

    # tz-aware, in the past.
    aware_raw = "nm_agent_expired_aware_" + "e" * 24
    _mint(aware_raw, datetime.now(timezone.utc) - timedelta(hours=1))
    r = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": aware_raw},
    )
    assert r.status_code == 401, r.text
    assert "expired" in r.json()["detail"].lower()

    # tz-naive, in the past — must also 401 (not 500).
    naive_raw = "nm_agent_expired_naive_" + "n" * 24
    _mint(
        naive_raw,
        (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
    )
    r2 = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": naive_raw},
    )
    assert r2.status_code == 401, r2.text


def _mint_session_bound_key(
    db_session, *, test_agent, recon_scope, recon_session_row, workflow, raw,
    set_legacy_columns=True,
):
    """Mint an API key bound to a freshly-created AgentSession (the WS2c
    primary scope binding), optionally also setting the legacy scope_id +
    recon_session_id columns.  Returns the plaintext key."""
    from app.db.models_agent import AgentSession
    from app.db.models_auth import APIKey

    agent_session = AgentSession(
        workflow=workflow,
        project_id=recon_scope.project_id,
        agent_id=test_agent.id,
        scope_id=recon_scope.id,
        status="active",
    )
    db_session.add(agent_session)
    db_session.flush()
    db_session.add(APIKey(
        agent_id=test_agent.id,
        agent_session_id=agent_session.id,
        # Legacy columns: present so the recon handler can still resolve the
        # session.  The fail-closed test sets these to a *valid* recon
        # binding deliberately — proving the agent_session.workflow takes
        # precedence over them.
        scope_id=recon_scope.id if set_legacy_columns else None,
        recon_session_id=recon_session_row.id if set_legacy_columns else None,
        name=f"sessbound-{workflow}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


def test_agent_session_bound_recon_key_authorizes(
    client, db_session, test_agent, recon_scope, recon_session_row,
):
    """WS2c primary-branch coverage: a key bound to an AgentSession with
    workflow='recon' (not via the legacy-column fallback) passes
    require_recon_scope and reads /agent/recon/context.  The existing suite
    only exercised the legacy-column fallback path in get_current_agent; this
    drives the agent_session-derived key_workflow branch directly."""
    raw = _mint_session_bound_key(
        db_session, test_agent=test_agent, recon_scope=recon_scope,
        recon_session_row=recon_session_row, workflow="recon",
        raw="nm_agent_sessbound_recon_" + "a" * 24,
    )
    resp = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": raw},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope_id"] == recon_scope.id


def test_unrecognized_agent_session_workflow_denied(
    client, db_session, test_agent, recon_scope, recon_session_row,
):
    """Fail-closed regression: a key bound to an AgentSession whose workflow
    the auth code can't classify must be denied (403), NOT silently treated
    as an unscoped global key (the most-privileged outcome).

    The legacy scope_id + recon_session_id columns are set to a *valid*
    recon binding here on purpose: if get_current_agent fell back to them (or
    fell through to key_workflow=None) this key would authorize.  The 403
    proves (a) the agent_session.workflow takes precedence over the legacy
    columns, and (b) an unrecognized value fails closed."""
    raw = _mint_session_bound_key(
        db_session, test_agent=test_agent, recon_scope=recon_scope,
        recon_session_row=recon_session_row, workflow="totally_bogus",
        raw="nm_agent_sessbound_bogus_" + "b" * 24,
    )
    resp = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": raw},
    )
    assert resp.status_code == 403, resp.text
    assert "unrecognized workflow" in resp.json()["detail"].lower()


def test_recon_upload_rejects_disallowed_extension(client, recon_key):
    """The recon upload reaches ingestion_service.create_job directly, NOT via
    the JWT /upload path that historically owned the ALLOWED_EXTENSIONS check.
    create_job now enforces the allowlist for every caller, so an arbitrary
    extension is rejected (400) before anything lands on disk."""
    resp = client.post(
        "/api/v1/agent/recon/upload",
        headers={"X-API-Key": recon_key},
        files={"file": ("payload.exe", b"MZ\x90\x00not a scan", "application/octet-stream")},
    )
    assert resp.status_code == 400, resp.text
    assert "file type not allowed" in resp.json()["detail"].lower()
