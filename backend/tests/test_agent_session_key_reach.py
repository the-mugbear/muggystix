"""What one session key reaches, and when it stops working.

v2.337.0 made the key a project session's: the same key reads the inventory,
proposes tests on hosts and records evidence.  Until v2.442.0 this file pinned
the execution-run surface (a run belonged to the session that opened it); runs
are gone, and a host test is project data any session — or a person — may carry
on.  What survives: one key for every kind of work, attribution to the session
that actually did the work, and an expired key refused everywhere.
"""
from datetime import datetime, timedelta, timezone

import hashlib
import uuid

import pytest

from app.db import models
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.services.agent_session_service import create_agent_session, mint_session_key


def _hdr(key):
    return {"X-API-Key": key}


@pytest.fixture
def host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.46.0.1", state="up")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def session_ctx(db_session, test_project, test_agent, test_user):
    """A project session and its key.  Returns (key, session)."""
    session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id,
    )
    raw = mint_session_key(db_session, agent=test_agent, session=session)
    db_session.commit()
    return raw, session


def _spec(host):
    return dict(request_key=str(uuid.uuid4()), host_id=host.id, tool="curl",
                description="Check response headers", rationale="Observed web service")


def test_one_session_key_reaches_assist_reads_and_host_tests(client, db_session, session_ctx, host):
    """The cross-workflow block is gone: the key that proposes a test also
    reads the assist inventory and the tests already on the host."""
    key, session = session_ctx
    assert client.get("/api/v1/agent/assist/context", headers=_hdr(key)).status_code == 200

    r = client.post("/api/v1/agent/host-tests", headers=_hdr(key), json={"tests": [_spec(host)]})
    assert r.status_code == 201, r.text
    test_id = r.json()["items"][0]["id"]
    assert db_session.get(HostTest, test_id).agent_session_id == session.id

    listing = client.get("/api/v1/agent/host-tests", headers=_hdr(key), params={"host_id": host.id})
    assert listing.status_code == 200, listing.text
    assert [t["id"] for t in listing.json()["items"]] == [test_id]


def test_another_session_carries_a_test_on_and_is_the_one_attributed(
    client, db_session, session_ctx, host, test_project, test_agent, test_user,
):
    """A host test is project data, not session state: a second session may
    work a test the first proposed.  The record stays honest through
    attribution — the test keeps its proposer, the evidence names the session
    that ran it."""
    key_a, session_a = session_ctx
    test = client.post(
        "/api/v1/agent/host-tests", headers=_hdr(key_a), json={"tests": [_spec(host)]},
    ).json()["items"][0]

    session_b = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id,
    )
    key_b = mint_session_key(db_session, agent=test_agent, session=session_b)
    db_session.commit()

    r = client.patch(
        f"/api/v1/agent/host-tests/{test['id']}", headers=_hdr(key_b),
        json={"expected_revision": test["revision"], "status": "in_progress"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/agent/evidence", headers=_hdr(key_b), json={
        "host_id": host.id, "host_test_id": test["id"], "request_key": "b-ran-it",
        "tool": "curl", "outcome": "no_finding", "summary": "Headers present",
    })
    assert r.status_code == 201, r.text

    db_session.expire_all()
    assert db_session.get(HostTest, test["id"]).agent_session_id == session_a.id
    record = db_session.get(EvidenceRecord, r.json()["id"])
    assert record.agent_session_id == session_b.id
    assert record.host_test_id == test["id"]


def test_expired_key_rejected(client, db_session, session_ctx, test_agent):
    """A key past its expires_at 401s with the structured expired body — on
    the host-test surface as on every other."""
    from app.db.models_auth import APIKey
    key, _session = session_ctx
    db_session.query(APIKey).filter(
        APIKey.key_hash == hashlib.sha256(key.encode()).hexdigest()
    ).update({"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)})
    db_session.commit()
    r = client.get("/api/v1/agent/host-tests", headers=_hdr(key))
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["error"] == "key_expired"
