"""An expired key must not cost an agent its work.

The failure mode this exists for, in order:

  1. An operator starts a recon session.
  2. The agent launches nmap / masscan / Nessus and **blocks**, for hours.
  3. The key expires while it waits.
  4. The agent discovers this when it tries to **upload** — after the scanning
     is finished, at the exact moment the work is about to be delivered.

Prevention cannot cover this on its own. A blocked agent issues no requests, so
no heartbeat or activity-based extension can fire (the same reason idle-based
sliding expiry fails), and scan durations are not predictable, so "renew before
a long operation" can still be outlasted.

So the rule is: **expiry is recoverable while the session is active.** Renewal
accepts an already-expired key and returns the SAME token with a later
deadline, so the pending upload is simply retried. Ending the session — not
expiry — is the revocation control.
"""
from datetime import datetime, timedelta, timezone

import hashlib
import secrets

import pytest

from app.core.config import settings
from app.db.models_agent import (
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
)
from app.db.models_auth import APIKey


def _mint(db, project, agent, user, *, expires_in_hours, session_age_hours=0.0,
          session_status="active"):
    """A key bound to a session, with both clocks under the test's control."""
    now = datetime.now(timezone.utc)
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=session_status,
        started_at=now - timedelta(hours=session_age_hours),
    )
    db.add(base)
    db.flush()
    detail = AssistSession(
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status=AssistSessionStatus.ACTIVE, agent_session_id=base.id,
        purpose="renewal test",
    )
    db.add(detail)
    raw = "nm_agent_" + secrets.token_urlsafe(32)
    key = APIKey(
        agent_id=agent.id,
        name="renewal-test",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        agent_session_id=base.id,
        expires_at=now + timedelta(hours=expires_in_hours),
    )
    db.add(key)
    db.commit()
    return raw, key, base


def test_an_expired_key_can_renew_itself_and_retry(
    client, db_session, test_project, test_agent, test_user
):
    """The whole point. The key lapsed six hours ago while a scan ran; the
    session is still live; the agent recovers without losing anything."""
    raw, key, _base = _mint(
        db_session, test_project, test_agent, test_user,
        expires_in_hours=-6, session_age_hours=8,
    )
    headers = {"X-API-Key": raw}

    # The moment of discovery: a normal call fails.
    blocked = client.get("/api/v1/agent/identity", headers=headers)
    assert blocked.status_code == 401
    detail = blocked.json()["detail"]
    assert detail["recoverable"] is True, (
        "an agent holding hours of scan output was told nothing about whether "
        "retrying was worth anything"
    )
    assert detail["renew_path"] == "/api/v1/agent/session/renew"

    # Recovery, using the SAME (expired) key.
    renewed = client.post(detail["renew_path"], headers=headers)
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)

    # And the retry now works — same token throughout, nothing re-bootstrapped.
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200


def test_renewal_keeps_the_same_token(
    client, db_session, test_project, test_agent, test_user
):
    """Renewal, not rotation. A new secret would force a mid-run re-bootstrap,
    which is exactly what an agent part-way through a job cannot do."""
    raw, key, _ = _mint(
        db_session, test_project, test_agent, test_user, expires_in_hours=-1,
    )
    before = key.key_hash
    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    db_session.refresh(key)
    assert key.key_hash == before, "the secret changed — that is rotation, not renewal"


def test_renewal_cannot_outlive_the_session_cap(
    client, db_session, test_project, test_agent, test_user
):
    """Otherwise the cap is a formality any renewal could step past."""
    # Session already 167h old against a 168h cap: under an hour left.
    raw, key, base = _mint(
        db_session, test_project, test_agent, test_user,
        expires_in_hours=-1,
        session_age_hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS - 1,
    )
    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    body = resp.json()
    granted = datetime.fromisoformat(body["expires_at"])
    ceiling = datetime.fromisoformat(body["renewable_until"])
    assert granted <= ceiling, "renewal pushed the key past its session's lifetime"


def test_a_session_past_its_lifetime_cannot_renew(
    client, db_session, test_project, test_agent, test_user
):
    """Expiry has to become terminal eventually, or a forgotten key is
    immortal. Past the cap the answer is 'start a new session'."""
    raw, _key, _ = _mint(
        db_session, test_project, test_agent, test_user,
        expires_in_hours=-2,
        session_age_hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS + 1,
    )
    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 401
    assert resp.json()["detail"]["recoverable"] is False


def test_an_ended_session_cannot_renew(
    client, db_session, test_project, test_agent, test_user
):
    """Ending a session is the revocation control now that expiry is
    recoverable. It must not be routable around."""
    raw, _key, _ = _mint(
        db_session, test_project, test_agent, test_user,
        expires_in_hours=-1, session_status="ended",
    )
    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 401
    assert resp.json()["detail"]["recoverable"] is False


def test_a_revoked_key_cannot_renew(
    client, db_session, test_project, test_agent, test_user
):
    """``is_active`` is the operator's kill switch. Renewal accepting an expired
    key must not become a way around a deliberate revocation."""
    raw, key, _ = _mint(
        db_session, test_project, test_agent, test_user, expires_in_hours=-1,
    )
    key.is_active = False
    db_session.commit()
    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 401
    assert "revoked" in str(resp.json()["detail"]).lower()


def test_a_live_key_learns_where_to_renew_before_it_needs_to(
    client, db_session, test_project, test_agent, test_user
):
    """The cheap path: an agent about to block on a long scan renews FIRST,
    instead of finding out at upload time. /agent/identity carries both the
    deadline and where to extend it."""
    raw, _key, _ = _mint(
        db_session, test_project, test_agent, test_user, expires_in_hours=2,
    )
    body = client.get("/api/v1/agent/identity", headers={"X-API-Key": raw}).json()
    assert body["renew_path"] == "/api/v1/agent/session/renew"
    assert body["key_expires_at"] is not None
    assert body["renewable_until"] is not None


def test_a_renewal_is_recorded_in_the_audit_log(
    client, db_session, test_project, test_agent, test_user
):
    """A call that extends a credential has to be answerable after the fact.

    v2.307.0 — it was not. Renewal is mounted outside the normal dependency
    chain, and its authenticator stamped only ``agent_id`` and the key prefix.
    The audit middleware discards any non-5xx request lacking BOTH an agent id
    and a project id (the table's CHECK requires attribution or an error
    class), so renewals wrote **no row at all** — while the plan claimed every
    renewal was audited.
    """
    from app.db.models_agent import AgentApiCall

    raw, _key, base = _mint(
        db_session, test_project, test_agent, test_user, expires_in_hours=-1,
    )
    before = db_session.query(AgentApiCall).count()

    resp = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert resp.status_code == 200, resp.text

    rows = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.path.like("%/session/renew"))
        .all()
    )
    assert db_session.query(AgentApiCall).count() > before, (
        "renewing a key wrote no audit row — a credential-extending call has to "
        "be attributable"
    )
    assert rows, "the renewal row is missing its path"
    row = rows[-1]
    assert row.agent_id == test_agent.id
    assert row.project_id == test_project.id, (
        "no project attribution — this is the field whose absence made the "
        "audit writer discard the row entirely"
    )
    assert row.api_key_id is not None
