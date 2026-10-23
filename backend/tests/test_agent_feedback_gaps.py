"""2.519.0 — the gaps prod agent feedback found (2026-10-20; owner: "address
the gaps you identified as real gaps"), apart from the guide's sections and the
compact proposal list (``test_docs_contract.py``, ``test_agent_proposals.py``).

* An agent replaces its OWN pending proposal (``replaces`` on every create
  route): the old one is ``superseded`` with ``replaced_by_id``, decided by
  nobody; another's, a decided one, or one about something else is refused
  and nothing is written.
* Evidence about no single host: an ``info`` record that answers no test and
  names no endpoint may leave ``host_id`` out — and only that (the service's
  422 and the database's check say the same).
* A raw output's SHA-256 is kept and answered on write; ``/raw-chunk`` reads
  it in pieces that join to the output exactly, on character boundaries.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal, EvidenceRecord
from app.main import app

AGENT = "/api/v1/agent"


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _host(db, project, ip="10.60.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _finding(client, project, host):
    r = client.post(f"/api/v1/projects/{project.id}/findings",
                    json={"title": "SMB signing not required", "severity": "medium", "host_ids": [host.id]})
    assert r.status_code == 201, r.text
    return r.json()


def _text(client, key, finding_id, fields, **extra):
    return client.post(f"{AGENT}/proposals/finding-text", headers=key,
                       json={"finding_id": finding_id, "fields": fields, **extra})


# --- a proposal replaced by its proposer --------------------------------------

def test_an_agent_replaces_its_own_pending_proposal(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(client, test_project, _host(db_session, test_project))
    first = _text(client, key, finding["id"], {"impact": "Wrong host named."}).json()["proposals"][0]["id"]

    r = _text(client, key, finding["id"], {"impact": "Relay to the file share.", "description": "SMB."},
              replaces=[first])
    assert r.status_code == 201, r.text
    new = {p["field"]: p["id"] for p in r.json()["proposals"]}

    old = client.get(f"{AGENT}/proposals/{first}", headers=key).json()
    assert (old["status"], old["replaced_by_id"], old["decided_by"]) == ("superseded", new["impact"], None)
    assert client.get(f"{AGENT}/proposals/{new['impact']}", headers=key).json()["replaces_ids"] == [first]
    # What waits for a person is the new text only.
    pending = client.get(f"{AGENT}/proposals", headers=key, params={"status": "pending"}).json()
    assert sorted(p["id"] for p in pending["items"]) == sorted(new.values())


def test_a_replacement_is_refused_whole_when_it_cannot_replace(client, db_session, test_project):
    key = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    impact = _text(client, key, finding["id"], {"impact": "i"}).json()["proposals"][0]["id"]
    before = db_session.query(AgentProposal).count()

    # About another section.
    r = _text(client, key, finding["id"], {"description": "d"}, replaces=[impact])
    assert r.status_code == 422 and f"#{impact}" in r.json()["detail"]
    # Another session's operator is the same person here, but a person's own
    # in-app draft is a different source: not the agent's to replace.
    draft = AgentProposal(project_id=test_project.id, kind="finding_text", source="llm_draft",
                          finding_id=finding["id"], field="impact", payload={"value": "x"},
                          proposed_by_user_id=db_session.get(AgentProposal, impact).proposed_by_user_id)
    db_session.add(draft)
    db_session.commit()
    r = _text(client, key, finding["id"], {"impact": "i2"}, replaces=[draft.id])
    assert r.status_code == 403, r.text
    # Already decided.
    client.post(f"/api/v1/projects/{test_project.id}/proposals/{impact}/reject", json={})
    r = _text(client, key, finding["id"], {"impact": "i3"}, replaces=[impact])
    assert r.status_code == 409, r.text
    # Not in this project.
    r = _text(client, key, finding["id"], {"impact": "i4"}, replaces=[999999])
    assert r.status_code == 404, r.text

    db_session.expire_all()
    assert db_session.query(AgentProposal).count() == before + 1  # only the draft added above
    assert db_session.get(AgentProposal, impact).replaced_by_id is None


def test_another_persons_agent_proposal_is_not_replaceable(client, db_session, test_project):
    key = _start(client, test_project)
    finding = _finding(client, test_project, _host(db_session, test_project))
    bob = User(id=611, username="gap-bob", email="gap-bob@example.com", full_name="Bob",
               hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db_session.add(bob)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=bob.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: bob
    try:
        bobs_key = _start(client, test_project)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    theirs = _text(client, bobs_key, finding["id"], {"impact": "Bob's"}).json()["proposals"][0]["id"]
    r = _text(client, key, finding["id"], {"impact": "mine"}, replaces=[theirs])
    assert r.status_code == 403 and "a person decides it" in r.json()["detail"]


def test_every_kind_can_be_replaced(client, db_session, test_project):
    key = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    fh = db_session.query(FindingHost).filter(FindingHost.finding_id == finding["id"]).one()

    made = client.post(f"{AGENT}/proposals/finding", headers=key, json={
        "title": "Weak TLS", "severity": "low", "host_ids": [host.id]}).json()["id"]
    r = client.post(f"{AGENT}/proposals/finding", headers=key, json={
        "title": "Weak TLS on 443", "severity": "medium", "host_ids": [host.id], "replaces": [made]})
    assert r.status_code == 201, r.text
    assert client.get(f"{AGENT}/proposals/{made}", headers=key).json()["replaced_by_id"] == r.json()["id"]

    ep = client.post(f"{AGENT}/proposals/endpoint-status", headers=key, json={
        "finding_id": finding["id"], "finding_host_id": fh.id, "host_status": "remediated"}).json()["id"]
    r = client.post(f"{AGENT}/proposals/endpoint-status", headers=key, json={
        "finding_id": finding["id"], "finding_host_id": fh.id, "host_status": "retest", "replaces": [ep]})
    assert r.status_code == 201, r.text
    assert client.get(f"{AGENT}/proposals/{ep}", headers=key).json()["status"] == "superseded"


# --- evidence about no single host --------------------------------------------

def test_context_about_no_host_is_recorded_and_only_that(client, db_session, test_project):
    key = _start(client, test_project)
    r = client.post(f"{AGENT}/evidence", headers=key, json={
        "tool": "dig", "outcome": "info", "summary": "The domain's MX records point off-site.",
        "command": "dig mx example.com", "raw_output": "example.com. MX 10 mail.elsewhere.net."})
    assert r.status_code == 201, r.text
    assert (r.json()["host_id"], r.json()["host_ip"]) == (None, None)

    for body in (
        {"outcome": "finding"},
        {"outcome": "info", "finding_host_id": 1},
    ):
        r = client.post(f"{AGENT}/evidence", headers=key, json={"tool": "dig", "summary": "x", **body})
        assert r.status_code == 422 and "host_id is required" in r.json()["detail"], r.text

    listed = client.get(f"{AGENT}/evidence", headers=key).json()
    assert listed["total"] == 1 and listed["items"][0]["host_id"] is None


def test_the_database_holds_the_same_rule(db_session, test_project):
    db_session.add(EvidenceRecord(project_id=test_project.id, host_id=None, tool="t",
                                  outcome="no_finding", summary="s"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- the raw output's hash, and reading it in pieces ---------------------------

def test_the_raw_output_is_hashed_and_read_back_in_pieces(client, db_session, test_project):
    key = _start(client, test_project)
    host = _host(db_session, test_project)
    # Multi-byte characters across every piece boundary.
    raw = ("é€😀 line\n" * 5000)
    data = raw.encode("utf-8")
    r = client.post(f"{AGENT}/evidence", headers=key, json={
        "host_id": host.id, "tool": "curl", "outcome": "info", "summary": "Banner.", "raw_output": raw})
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["raw_output_sha256"] == hashlib.sha256(data).hexdigest()
    assert record["raw_output_bytes"] == len(data)
    assert client.get(f"{AGENT}/evidence/{record['id']}", headers=key).json() == record

    pieces, offset = [], 0
    while offset is not None:
        piece = client.get(f"{AGENT}/evidence/{record['id']}/raw-chunk", headers=key,
                           params={"offset": offset, "length": 1000})
        assert piece.status_code == 200, piece.text
        body = piece.json()
        assert body["total_bytes"] == len(data) and body["length"] <= 1000
        pieces.append(body["text"])
        offset = body["next_offset"]
    assert "".join(pieces) == raw
    assert hashlib.sha256("".join(pieces).encode("utf-8")).hexdigest() == body["sha256"]
    assert len(pieces) > 40

    # A length shorter than the character at the offset still moves on: that
    # one character (feedback #80 — an empty piece answered its own offset).
    # 2-, 3- and 4-byte characters, lengths 1-3, read whole one piece at a time.
    for length in (1, 2, 3):
        pieces, offset = [], 0
        while offset is not None:
            body = client.get(f"{AGENT}/evidence/{record['id']}/raw-chunk", headers=key,
                              params={"offset": offset, "length": length}).json()
            assert body["text"] and (body["next_offset"] is None or body["next_offset"] > offset)
            pieces.append(body["text"])
            offset = body["next_offset"]
            if len(pieces) == 40:
                break
        assert "".join(pieces) == raw[:len("".join(pieces))]
        assert "".join(pieces).startswith("é€😀 line")

    # An offset inside a character is refused; past the end is an empty piece.
    assert client.get(f"{AGENT}/evidence/{record['id']}/raw-chunk", headers=key,
                      params={"offset": 1}).status_code == 422
    end = client.get(f"{AGENT}/evidence/{record['id']}/raw-chunk", headers=key,
                     params={"offset": len(data) + 5}).json()
    assert (end["text"], end["length"], end["next_offset"]) == ("", 0, None)
    # Not more than the cap in one read.
    assert client.get(f"{AGENT}/evidence/{record['id']}/raw-chunk", headers=key,
                      params={"length": 64 * 1024 + 1}).status_code == 422
