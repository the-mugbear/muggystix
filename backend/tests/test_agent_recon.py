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
    scope = models.Scope(description="fixture", project_id=test_project.id)
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


def _subnets(client, key, **q):
    from urllib.parse import urlencode
    qs = ("?" + urlencode(q)) if q else ""
    return client.get(f"/api/v1/agent/scope/subnets{qs}", headers={"X-API-Key": key})


# --- scope reads -----------------------------------------------------------

def test_scope_subnets_read(client, recon_key, recon_scope):
    r = _subnets(client, recon_key)
    assert r.status_code == 200, r.text
    body = r.json()
    # A project has one scope, so the answer does not name one.
    assert "scope_id" not in body
    assert body["items"] == ["10.99.0.0/24"]
    assert (body["total"], body["returned"], body["has_more"]) == (1, 1, False)


def test_scope_domains_page(client, db_session, recon_key, recon_scope):
    from app.db import models
    for i in range(12):
        db_session.add(models.ScopeDomain(
            scope_id=recon_scope.id, domain=f"h{i:02d}.example.com", include_subdomains=False,
        ))
    db_session.commit()
    p1 = client.get("/api/v1/agent/scope/domains?offset=0&limit=5",
                    headers={"X-API-Key": recon_key}).json()
    assert p1["returned"] == 5 and p1["has_more"] is True
    assert "scope_id" not in p1
    p3 = client.get("/api/v1/agent/scope/domains?offset=10&limit=5",
                    headers={"X-API-Key": recon_key}).json()
    assert p3["returned"] == 2 and p3["has_more"] is False


def test_scope_reads_of_a_project_with_no_scope_are_empty(client, db_session, test_project, recon_key):
    """There is no scope id to get wrong any more, so there is no 404: a
    project that declares no scope answers empty — and the read does not
    create the scope."""
    from app.db import models
    headers = {"X-API-Key": recon_key}
    r = _subnets(client, recon_key)
    assert r.status_code == 200, r.text
    assert (r.json()["total"], r.json()["items"], r.json()["has_more"]) == (0, [], False)
    d = client.get("/api/v1/agent/scope/domains", headers=headers)
    assert d.status_code == 200, d.text
    assert (d.json()["total"], d.json()["items"], d.json()["has_more"]) == (0, [], False)
    for name in ("hosts.ndjson", "live-hosts.txt", "web-targets.txt", "named-targets.ndjson"):
        f = client.get(f"/api/v1/agent/scope/{name}", headers=headers)
        assert f.status_code == 200, (name, f.text)
        assert f.text == "", name
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 0


# --- key auth --------------------------------------------------------------

def test_revoked_key_is_401(client, db_session, recon_key, recon_scope):
    from app.db.models_auth import APIKey
    db_session.query(APIKey).update({"is_active": False})
    db_session.commit()
    assert _subnets(client, recon_key).status_code == 401


def test_expired_key_rejected_both_tz_forms(client, db_session, test_agent, agent_session, recon_scope):
    aware = _mint(db_session, test_agent=test_agent, agent_session_id=agent_session.id,
                  raw="nm_agent_expired_aware_" + "e" * 24,
                  expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    r = _subnets(client, aware)
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "key_expired"
    # One active key per session (uq_api_key_agent_session_active): revoke,
    # then mint, as the application does.
    from app.db.models_auth import APIKey
    db_session.query(APIKey).filter(APIKey.agent_session_id == agent_session.id).update({"is_active": False})
    db_session.commit()
    naive = _mint(db_session, test_agent=test_agent, agent_session_id=agent_session.id,
                  raw="nm_agent_expired_naive_" + "n" * 24,
                  expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None))
    assert _subnets(client, naive).status_code == 401


def test_unrecognized_workflow_denied(client, db_session, test_agent, recon_scope):
    from app.db.models_agent import AgentSession
    bogus = AgentSession(workflow="totally_bogus", project_id=recon_scope.project_id,
                         agent_id=test_agent.id, status="active")
    db_session.add(bogus)
    db_session.flush()
    raw = _mint(db_session, test_agent=test_agent, agent_session_id=bogus.id,
                raw="nm_agent_bogus_" + "b" * 24)
    assert _subnets(client, raw).status_code == 403


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


# --- observations as JSON (2.537.0) -----------------------------------------

_OBSERVATIONS = {
    "tool": "homebrew-scanner",
    "observations": [
        {"host": "10.93.0.1", "hostname": "web01.example.test", "port": 443, "service": "https",
         "title": "Outdated TLS library", "severity": "high", "cves": ["CVE-2024-0001", "CVE-2024-0002"],
         "cvss": {"score": 9.8, "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}, "exploitable": True},
        {"host": "10.93.0.2", "title": "Default community string", "port": 161, "protocol": "udp"},
        {"host": "10.93.0.2", "check_id": "smb_signing_not_required", "port": 445, "evidence": "signing: False"},
        {"hostname": "name-only.example.test", "title": "Cannot be placed"},
    ],
}


def _observe(client, key, body):
    return client.post("/api/v1/agent/observations", headers={"X-API-Key": key}, json=body)


def test_observations_as_json_go_through_the_upload_path_to_the_same_parser(
    client, db_session, test_project, recon_key, agent_session,
):
    """The route is the observation format without a file: the SAME parser,
    job, attribution and skipping as an uploaded .jsonl."""
    from app.db import models
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
    from tests.ingestion_job_harness import job_row, run_next

    up = _observe(client, recon_key, _OBSERVATIONS)
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["message"] == "4 observations queued for import"
    job = db_session.get(models.IngestionJob, body["job_id"])
    assert job.agent_session_id == agent_session.id
    assert (job.options or {}).get("format_override") == "observations_jsonl"
    assert job.original_filename.endswith(".jsonl")

    assert run_next(db_session)
    job = job_row(db_session, body["job_id"])
    assert job.status in ("completed", "partial"), (job.status, job.error_message)
    assert "no host address" in (job.parser_warnings or ""), job.parser_warnings

    rows = {v.title: v for v in db_session.query(Vulnerability).join(models.Host).filter(
        models.Host.project_id == test_project.id)}
    assert set(rows) == {"Outdated TLS library", "Default community string", "SMB signing not required"}
    tls = rows["Outdated TLS library"]
    assert (tls.source.value, tls.severity, tls.exploitable, tls.cvss_version) == ("external", VulnerabilitySeverity.HIGH, True, "3.1")
    assert rows["Default community string"].severity == VulnerabilitySeverity.UNKNOWN
    assert rows["Default community string"].exploitable is None
    scan = db_session.get(models.Scan, tls.scan_id)
    assert scan.tool_name == "homebrew-scanner"

    # The same body again is the same file: the upload's duplicate guard answers.
    again = _observe(client, recon_key, _OBSERVATIONS)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "duplicate_scan"


def test_observations_as_json_refuse_an_empty_or_oversized_list(client, recon_key):
    assert _observe(client, recon_key, {"observations": []}).status_code == 422
    assert _observe(client, recon_key, {"observations": [{"host": "10.93.1.1", "title": "x"}] * 501}).status_code == 422
    assert _observe(client, recon_key, {"observations": ["not an object"]}).status_code == 422
