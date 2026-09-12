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
    """An ACTIVE ReconSession bound to the fixture scope, with its AgentSession
    (the key's scope binding; recon_session ↔ agent_session is 1:1)."""
    from app.db.models_agent import (
        AgentSessionWorkflow, ReconSession, ReconSessionStatus,
    )
    from app.services.agent_session_service import create_agent_session
    base = create_agent_session(
        db_session, workflow=AgentSessionWorkflow.RECON.value,
        project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None, scope_id=recon_scope.id,
    )
    session = ReconSession(
        project_id=test_project.id,
        scope_id=recon_scope.id,
        agent_id=test_agent.id,
        status=ReconSessionStatus.ACTIVE.value,
        agent_session_id=base.id,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture
def recon_key(db_session, test_agent, recon_scope, recon_session_row):
    """A recon-scoped API key bound to this recon session via its AgentSession."""
    from app.db.models_auth import APIKey
    raw = "nm_agent_recon_smoke_" + "r" * 28
    db_session.add(APIKey(
        agent_id=test_agent.id,
        agent_session_id=recon_session_row.agent_session_id,
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
        "/api/v1/agent/session/environment",
        headers={"X-API-Key": recon_key},
        json={"os_family": "linux"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_type"] == "session"
    assert body["probed_at"] is not None


def test_one_session_key_reaches_recon_and_assist_and_plans(
    client, recon_key, test_plan,
):
    """v2.337.0 — the cross-workflow boundary is gone: a session that opened a
    recon run is one project session, so the SAME key also reads the assist
    inventory and its own test plans. (What a WRITE does is still the
    operator's role, decided per request — not the key's workflow.)"""
    headers = {"X-API-Key": recon_key}
    assert client.get("/api/v1/agent/recon/context", headers=headers).status_code == 200
    assert client.get("/api/v1/agent/assist/context", headers=headers).status_code == 200
    plan = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/context", headers=headers,
    )
    assert plan.status_code == 200, plan.text


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
            agent_session_id=recon_session_row.agent_session_id,
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
    # v2.304.0 — detail is structured now; branch on the field callers use.
    assert r.json()["detail"]["error"] == "key_expired"

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


def _mint_agent_key_for_session(db_session, *, test_agent, agent_session_id, raw):
    """Mint an API key bound to the given AgentSession (the key's only scope
    binding now — the legacy per-workflow columns were dropped)."""
    from app.db.models_auth import APIKey

    db_session.add(APIKey(
        agent_id=test_agent.id,
        agent_session_id=agent_session_id,
        name=f"sessbound-{agent_session_id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


def test_agent_session_bound_recon_key_authorizes(
    client, db_session, test_agent, recon_scope, recon_session_row,
):
    """A key bound to an AgentSession with workflow='recon' passes
    require_recon_scope and reads /agent/recon/context — the session resolves
    1:1 from the key's agent_session (there is no legacy-column fallback)."""
    raw = _mint_agent_key_for_session(
        db_session, test_agent=test_agent,
        agent_session_id=recon_session_row.agent_session_id,
        raw="nm_agent_sessbound_recon_" + "a" * 24,
    )
    resp = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": raw},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope_id"] == recon_scope.id


def test_unrecognized_agent_session_workflow_denied(
    client, db_session, test_agent, recon_scope,
):
    """Fail-closed regression: a key bound to an AgentSession whose workflow
    the auth code can't classify must be denied (403), NOT silently treated
    as an unscoped global key (the most-privileged outcome)."""
    from app.db.models_agent import AgentSession

    bogus = AgentSession(
        workflow="totally_bogus",
        project_id=recon_scope.project_id,
        agent_id=test_agent.id,
        status="active",
    )
    db_session.add(bogus)
    db_session.flush()
    raw = _mint_agent_key_for_session(
        db_session, test_agent=test_agent, agent_session_id=bogus.id,
        raw="nm_agent_sessbound_bogus_" + "b" * 24,
    )
    resp = client.get(
        "/api/v1/agent/recon/context", headers={"X-API-Key": raw},
    )
    assert resp.status_code == 403, resp.text
    assert "unrecognized session kind" in resp.json()["detail"].lower()


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


def test_recon_upload_rejected_on_terminal_session(
    client, db_session, recon_key, recon_session_row
):
    """v2.317.0 — a finalized recon session must not keep ingesting. The env
    probe and /recon/complete already 409 on a terminal session; upload skipped
    the check, so a completed session silently accepted more scanner output and
    mutated its final rollup. Uploads add host data, so the immutability the
    other two enforce applies here too."""
    from app.db.models_agent import ReconSessionStatus
    recon_session_row.status = ReconSessionStatus.COMPLETED.value
    db_session.commit()

    resp = client.post(
        "/api/v1/agent/recon/upload",
        headers={"X-API-Key": recon_key},
        files={"file": ("scan.xml", b"<nmaprun></nmaprun>", "text/xml")},
    )
    # A completed run is no longer this session's active recon phase, so the
    # upload has no run to ingest into: 409 (nothing mutates the final rollup).
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# v2.328.0 — name scope on the recon surface
# ---------------------------------------------------------------------------

def _declare_domains(db, scope, entries):
    from app.services import dns_name_service as svc
    added, _updated, invalid = svc.upsert_scope_domains(db, scope, entries)
    assert not invalid, invalid
    db.commit()
    return added


def test_recon_context_carries_scope_domains(client, db_session, recon_key, recon_scope):
    """The context lists declared domains (exact vs include_subdomains) next
    to the CIDRs, with the same truncation contract as scope_cidrs."""
    _declare_domains(db_session, recon_scope, [
        ("portal.example.com", False, None),
        ("*.lab.example.com", False, None),   # wildcard → base + include_subdomains
    ])
    body = client.get("/api/v1/agent/recon/context", headers={"X-API-Key": recon_key}).json()
    assert body["scope_domains_total"] == 2
    assert body["domains_truncated"] is False
    assert {(d["domain"], d["include_subdomains"]) for d in body["scope_domains"]} == {
        ("portal.example.com", False),
        ("lab.example.com", True),
    }
    # A subnet-only scope reports an empty, non-truncated list (no drift for
    # agents that never declared names).
    assert "10.99.0.0/24" in body["scope_cidrs"]


def test_recon_context_truncates_domains_past_the_cap(client, db_session, recon_key, recon_scope):
    _declare_domains(db_session, recon_scope, [(f"n{i}.example.com", False, None) for i in range(101)])
    body = client.get("/api/v1/agent/recon/context", headers={"X-API-Key": recon_key}).json()
    assert body["scope_domains_total"] == 101
    assert body["domains_truncated"] is True
    assert len(body["scope_domains"]) == 100


def test_recon_domains_endpoint_pages_like_subnets(client, db_session, recon_key, recon_scope):
    _declare_domains(db_session, recon_scope, [(f"p{i}.example.com", False, None) for i in range(7)])
    first = client.get(
        "/api/v1/agent/recon/domains?offset=0&limit=5", headers={"X-API-Key": recon_key},
    )
    assert first.status_code == 200, first.text
    b1 = first.json()
    assert b1["total"] == 7 and b1["returned"] == 5 and b1["has_more"] is True
    assert b1["scope_id"] == recon_scope.id
    assert all(set(d) == {"domain", "include_subdomains"} for d in b1["domains"])
    b2 = client.get(
        "/api/v1/agent/recon/domains?offset=5&limit=5", headers={"X-API-Key": recon_key},
    ).json()
    assert b2["returned"] == 2 and b2["has_more"] is False
    assert {d["domain"] for d in b1["domains"]} | {d["domain"] for d in b2["domains"]} == {
        f"p{i}.example.com" for i in range(7)
    }
    b3 = client.get(
        "/api/v1/agent/recon/domains?offset=10&limit=5", headers={"X-API-Key": recon_key},
    ).json()
    assert b3["domains"] == [] and b3["has_more"] is False
    assert "does not make the address" in b3["note"]


def test_recon_domains_rejects_a_key_that_is_not_recon_scoped(
    client, db_session, test_agent, recon_scope,
):
    """Same guard as /recon/subnets: the endpoint sits behind require_recon_scope."""
    from app.db.models_agent import AgentSession

    bogus = AgentSession(
        workflow="totally_bogus", project_id=recon_scope.project_id,
        agent_id=test_agent.id, status="active",
    )
    db_session.add(bogus)
    db_session.flush()
    raw = _mint_agent_key_for_session(
        db_session, test_agent=test_agent, agent_session_id=bogus.id,
        raw="nm_agent_sessbound_dom_" + "d" * 25,
    )
    resp = client.get("/api/v1/agent/recon/domains", headers={"X-API-Key": raw})
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# v2.335.0 — upload batches + duplicate refusal on the recon upload
# ---------------------------------------------------------------------------

_NMAP = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"><!-- %d --></nmaprun>\n'


def _recon_upload(client, key, data, name, **form):
    return client.post(
        "/api/v1/agent/recon/upload",
        headers={"X-API-Key": key},
        files={"file": (name, data, "text/xml")},
        data=form,
    )


def test_recon_chunks_with_one_label_share_one_batch(client, db_session, recon_key, recon_session_row):
    """Every chunk of a sweep sent with the same `batch` label lands in one
    batch (one /scans row); another label is another batch; no label, none."""
    from app.db import models
    a = _recon_upload(client, recon_key, _NMAP % 1, "chunk-001.xml", batch="nmap-tcp-top1000", tool_name="nmap")
    b = _recon_upload(client, recon_key, _NMAP % 2, "chunk-002.xml", batch="nmap-tcp-top1000")
    other = _recon_upload(client, recon_key, _NMAP % 3, "svc.xml", batch="nmap-svc-live")
    loose = _recon_upload(client, recon_key, _NMAP % 4, "one-off.xml")
    for r in (a, b, other, loose):
        assert r.status_code == 201, r.text

    assert a.json()["batch_id"] == b.json()["batch_id"] is not None
    assert a.json()["batch"] == "nmap-tcp-top1000"
    assert other.json()["batch_id"] not in (None, a.json()["batch_id"])
    assert loose.json()["batch_id"] is None
    batch = db_session.get(models.ScanBatch, a.json()["batch_id"])
    assert batch.recon_session_id == recon_session_row.id


def test_recon_duplicate_is_refused_and_not_counted(client, db_session, recon_key, recon_session_row):
    """An identical re-upload is a 409 naming the job it already is — and the
    session's upload counter does not move (it used to count every copy)."""
    first = _recon_upload(client, recon_key, _NMAP % 9, "chunk-009.xml", batch="nmap-tcp")
    assert first.status_code == 201, first.text

    again = _recon_upload(client, recon_key, _NMAP % 9, "chunk-009-retry.xml", batch="nmap-tcp")
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["code"] == "duplicate_scan"
    assert detail["job_id"] == first.json()["job_id"]
    db_session.refresh(recon_session_row)
    assert recon_session_row.uploads_submitted == 1
