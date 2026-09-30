"""Agent scope-read + upload surface (v2.433.0 — recon runs removed).

An agent reads a scope (subnets, domains, hosts), runs its own tools, and
uploads output to its project session.  These pin the boundaries: a
session-bound key reads a scope, uploads belong to the session, and key
auth (revoked / expired / unrecognised) fails closed.
"""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest


@pytest.fixture
def recon_scope(db_session, test_project):
    """A Scope with one Subnet so the scope reads return something."""
    from app.db import models
    scope = models.Scope(name="scope-smoke", description="fixture", project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    db_session.add(models.Subnet(scope_id=scope.id, cidr="10.99.0.0/24"))
    db_session.commit()
    db_session.refresh(scope)
    return scope


@pytest.fixture
def agent_session(db_session, test_project, test_agent):
    """An active project agent session."""
    from app.db.models_agent import AgentSessionWorkflow
    from app.services.agent_session_service import create_agent_session
    s = create_agent_session(
        db_session, workflow=AgentSessionWorkflow.PROJECT.value,
        project_id=test_project.id, agent_id=test_agent.id, started_by_id=None,
    )
    db_session.commit()
    db_session.refresh(s)
    return s


def _mint(db_session, *, test_agent, agent_session_id, raw, expires_at=None):
    from app.db.models_auth import APIKey
    db_session.add(APIKey(
        agent_id=test_agent.id,
        agent_session_id=agent_session_id,
        name=f"sessbound-{agent_session_id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(hours=24)),
    ))
    db_session.commit()
    return raw


@pytest.fixture
def recon_key(db_session, test_agent, agent_session):
    return _mint(db_session, test_agent=test_agent, agent_session_id=agent_session.id,
                 raw="nm_agent_scope_smoke_" + "r" * 28)


def _subnets(client, key, scope_id, **q):
    from urllib.parse import urlencode
    qs = ("?" + urlencode(q)) if q else ""
    return client.get(f"/api/v1/agent/scopes/{scope_id}/subnets{qs}", headers={"X-API-Key": key})


# --- scope reads -----------------------------------------------------------

def test_scope_subnets_read(client, recon_key, recon_scope):
    r = _subnets(client, recon_key, recon_scope.id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope_id"] == recon_scope.id
    assert body["subnets"] == ["10.99.0.0/24"]


def test_scope_domains_page(client, db_session, recon_key, recon_scope):
    from app.db import models
    for i in range(12):
        db_session.add(models.ScopeDomain(
            scope_id=recon_scope.id, domain=f"h{i:02d}.example.com", include_subdomains=False,
        ))
    db_session.commit()
    p1 = client.get(f"/api/v1/agent/scopes/{recon_scope.id}/domains?offset=0&limit=5",
                    headers={"X-API-Key": recon_key}).json()
    assert p1["returned"] == 5 and p1["has_more"] is True
    p3 = client.get(f"/api/v1/agent/scopes/{recon_scope.id}/domains?offset=10&limit=5",
                    headers={"X-API-Key": recon_key}).json()
    assert p3["returned"] == 2 and p3["has_more"] is False


def test_scope_read_unknown_scope_404(client, recon_key):
    assert _subnets(client, recon_key, 999999).status_code == 404


# --- key auth --------------------------------------------------------------

def test_revoked_key_is_401(client, db_session, recon_key, recon_scope):
    from app.db.models_auth import APIKey
    db_session.query(APIKey).update({"is_active": False})
    db_session.commit()
    assert _subnets(client, recon_key, recon_scope.id).status_code == 401


def test_expired_key_rejected_both_tz_forms(client, db_session, test_agent, agent_session, recon_scope):
    aware = _mint(db_session, test_agent=test_agent, agent_session_id=agent_session.id,
                  raw="nm_agent_expired_aware_" + "e" * 24,
                  expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    r = _subnets(client, aware, recon_scope.id)
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "key_expired"
    naive = _mint(db_session, test_agent=test_agent, agent_session_id=agent_session.id,
                  raw="nm_agent_expired_naive_" + "n" * 24,
                  expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None))
    assert _subnets(client, naive, recon_scope.id).status_code == 401


def test_unrecognized_workflow_denied(client, db_session, test_agent, recon_scope):
    from app.db.models_agent import AgentSession
    bogus = AgentSession(workflow="totally_bogus", project_id=recon_scope.project_id,
                         agent_id=test_agent.id, status="active")
    db_session.add(bogus)
    db_session.flush()
    raw = _mint(db_session, test_agent=test_agent, agent_session_id=bogus.id,
                raw="nm_agent_bogus_" + "b" * 24)
    assert _subnets(client, raw, recon_scope.id).status_code == 403


def test_environment_probe_roundtrips(client, recon_key):
    r = client.post("/api/v1/agent/session/environment",
                    headers={"X-API-Key": recon_key},
                    json={"os_family": "linux", "shell": "bash"})
    assert r.status_code in (200, 201), r.text


# --- uploads ---------------------------------------------------------------

_NMAP = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"><!-- %d --></nmaprun>\n'


def _upload(client, key, data, name, **form):
    return client.post("/api/v1/agent/uploads", headers={"X-API-Key": key},
                       files={"file": (name, data, "text/xml")}, data=form)


def test_upload_rejects_disallowed_extension(client, recon_key):
    r = _upload(client, recon_key, b"MZ\x90\x00not a scan", "payload.exe")
    assert r.status_code == 400, r.text
    assert "file type not allowed" in r.json()["detail"].lower()


def test_upload_belongs_to_session_and_polls(client, db_session, test_project, test_agent, recon_key, agent_session):
    from app.db import models
    from app.db.models_agent import AgentSessionWorkflow
    from app.services.agent_session_service import create_agent_session

    up = _upload(client, recon_key, _NMAP % 21, "own.xml", tool_name="nmap")
    assert up.status_code == 201, up.text
    job_id = up.json()["job_id"]
    job = db_session.get(models.IngestionJob, job_id)
    assert job.agent_session_id == agent_session.id

    polled = client.get(f"/api/v1/agent/uploads/{job_id}", headers={"X-API-Key": recon_key})
    assert polled.status_code == 200 and polled.json()["job_id"] == job_id

    other = create_agent_session(db_session, workflow=AgentSessionWorkflow.PROJECT.value,
                                 project_id=test_project.id, agent_id=test_agent.id, started_by_id=None)
    db_session.commit()
    other_key = _mint(db_session, test_agent=test_agent, agent_session_id=other.id,
                      raw="nm_agent_other_" + "o" * 25)
    assert client.get(f"/api/v1/agent/uploads/{job_id}",
                      headers={"X-API-Key": other_key}).status_code == 404


def test_upload_chunks_with_one_label_share_one_batch(client, db_session, recon_key, agent_session):
    from app.db import models
    a = _upload(client, recon_key, _NMAP % 1, "c1.xml", batch="sweep-a", tool_name="nmap")
    b = _upload(client, recon_key, _NMAP % 2, "c2.xml", batch="sweep-a")
    other = _upload(client, recon_key, _NMAP % 3, "c3.xml", batch="sweep-b")
    loose = _upload(client, recon_key, _NMAP % 4, "c4.xml")
    for r in (a, b, other, loose):
        assert r.status_code == 201, r.text
    assert a.json()["batch_id"] == b.json()["batch_id"] is not None
    assert other.json()["batch_id"] not in (None, a.json()["batch_id"])
    assert loose.json()["batch_id"] is None
    batch = db_session.get(models.ScanBatch, a.json()["batch_id"])
    assert batch.agent_session_id == agent_session.id


def test_upload_duplicate_refused(client, recon_key):
    first = _upload(client, recon_key, _NMAP % 9, "d1.xml", batch="dup")
    assert first.status_code == 201, first.text
    again = _upload(client, recon_key, _NMAP % 9, "d1-retry.xml", batch="dup")
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "duplicate_scan"


def test_upload_is_audited_to_the_session(client, db_session, recon_key, agent_session):
    from app.db.models_agent import AgentApiCall
    up = _upload(client, recon_key, _NMAP % 31, "aud.xml")
    assert up.status_code == 201, up.text
    row = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.path.like("%/agent/uploads"))
        .order_by(AgentApiCall.id.desc())
        .first()
    )
    assert row is not None and row.agent_session_id == agent_session.id
