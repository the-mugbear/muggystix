"""An agent's upload that was cut short — the agent asks its operator first.

Owner, 2026-10-11: "If an agent attempts to upload a partial, can't it ask
the agent operator?  Can we modify the tool to surface the decision to the
user?"  An upload that fails as ``truncated_file`` can be kept by the agent
that uploaded it, after it asked: ``POST /agent/uploads/{job_id}/keep-truncated``
with ``{"operator_agreed": true}`` (MCP ``keep_truncated_upload``).

The same ask-first shape as a configured scanner's credentials
(``test_scanner_integrations_agent.py``), and the same limit: the server
cannot see the conversation, so nothing is called approved, enforced or
verified.  What is pinned:

* 422 with the text of what to ask when agreement is not stated;
* 404 for a job that is not this session's upload; 409 for a job that did
  not fail as cut short, is not failed any more, or whose file is gone;
* 403 for an operator who cannot write to the project;
* agreed: the job is re-queued through the people's path
  (``staged_import_service.queue_job``), completes PARTIAL with the complete
  records, the request is in the audit log, and Ingestion results shows a
  partial import;
* the rule has one source and both doors carry it.

Every import here runs under a real job (``ingestion_job_harness``).
"""
from __future__ import annotations

import json

import pytest

from app.db import models
from app.db.models_auth import AuditLog
from app.db.models_project import Project, ProjectRole
from app.services.agent_policy import (
    TRUNCATED_UPLOAD_ASK_FIRST,
    TRUNCATED_UPLOAD_CONSENT_MEANING,
    render_truncated_upload_rule,
)
from app.services.ingestion_service import KEEP_TRUNCATED_OPTION, IngestionService
from app.services.staged_import_service import TRUNCATED_UPLOAD_KEPT_ACTION
from tests.ingestion_job_harness import host_ips, job_row, run_next, scans_of
from tests.test_agent_role_route_matrix import _key_for, _member

UPLOADS = "/api/v1/agent/uploads"


def _keep_path(job_id) -> str:
    return f"{UPLOADS}/{job_id}/keep-truncated"


def nmap_xml(hosts: int, *, cut: bool = True) -> bytes:
    """An nmap run of ``hosts`` hosts; ``cut``: interrupted part-way into the
    host after them (no ``</nmaprun>``)."""
    body = "".join(
        f'<host><status state="up" reason="syn-ack"/><address addr="10.88.0.{i + 1}" addrtype="ipv4"/>'
        f'<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/>'
        f'<service name="ssh" method="probed" conf="10"/></port></ports></host>\n'
        for i in range(hosts)
    )
    head = '<?xml version="1.0"?>\n<nmaprun scanner="nmap" args="nmap -sV" start="1700000000" version="7.94">\n'
    if cut:
        return (head + body + '<host><status state="up"/><address addr="10.88.0.99" addrty').encode()
    return (head + body + '<runstats><finished time="1700000100"/></runstats></nmaprun>\n').encode()


CUT_SENTENCE = (
    "This Nmap XML file was cut short: it ends after 3 complete hosts, before the end of the "
    "document. Nothing from this file was imported."
)


def _headers(db_session, project, role: str = "analyst") -> dict:
    user = _member(db_session, project, ProjectRole(role))
    return {"X-API-Key": _key_for(db_session, project, user)}


def _upload(client, headers, data: bytes, name: str = "sweep.xml") -> int:
    resp = client.post(
        UPLOADS, headers=headers, files={"file": (name, data, "application/xml")},
        data={"tool_name": "nmap"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["job_id"]


def _failed_cut_upload(client, db_session, headers) -> int:
    job_id = _upload(client, headers, nmap_xml(3))
    run_next(db_session)
    polled = client.get(f"{UPLOADS}/{job_id}", headers=headers).json()
    assert (polled["status"], polled["failure_kind"]) == ("failed", "truncated_file"), polled
    assert polled["error_message"] == CUT_SENTENCE
    return job_id


def _kept_rows(db_session):
    db_session.expire_all()
    return db_session.query(AuditLog).filter_by(action=TRUNCATED_UPLOAD_KEPT_ACTION).all()


def _mcp(client, headers, method, params=None):
    resp = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{}, {"operator_agreed": False}], ids=["missing", "false"])
def test_a_request_that_does_not_state_agreement_is_refused_with_what_to_ask(
    client, db_session, test_project, body,
):
    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)

    resp = client.post(_keep_path(job_id), json=body, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == TRUNCATED_UPLOAD_ASK_FIRST
    assert "Ask the operator first" in resp.text and TRUNCATED_UPLOAD_CONSENT_MEANING in resp.text
    job = job_row(db_session, job_id)
    assert job.status == "failed" and KEEP_TRUNCATED_OPTION not in (job.options or {})
    assert _kept_rows(db_session) == []


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_agreement_is_the_literal_true_and_nothing_that_resembles_it(
    client, db_session, test_project, value,
):
    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)
    resp = client.post(_keep_path(job_id), json={"operator_agreed": value}, headers=headers)
    assert resp.status_code == 422, resp.text
    assert job_row(db_session, job_id).status == "failed" and _kept_rows(db_session) == []


def test_only_this_sessions_upload_can_be_kept(client, db_session, test_project):
    """The scoping of ``GET /agent/uploads/{job_id}``: another session's job,
    another project's and a person's are all 404, agreement stated or not."""
    mine = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, mine)

    another_session = _headers(db_session, test_project)
    other_project = Project(name="elsewhere", slug="elsewhere", description="another project")
    db_session.add(other_project)
    db_session.commit()
    elsewhere = _headers(db_session, other_project)
    for headers in (another_session, elsewhere):
        resp = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
        assert resp.status_code == 404, resp.text
    assert client.post(_keep_path(999999), json={"operator_agreed": True}, headers=mine).status_code == 404

    job = job_row(db_session, job_id)
    assert job.status == "failed" and KEEP_TRUNCATED_OPTION not in (job.options or {})
    assert _kept_rows(db_session) == []


def test_an_upload_that_did_not_fail_as_cut_short_is_a_409_saying_which(
    client, db_session, test_project,
):
    headers = _headers(db_session, test_project)
    agreed = {"operator_agreed": True}

    # Failed for another reason: not a scan at all.
    invalid = _upload(client, headers, b'<?xml version="1.0"?><notes><note>not a scan</note></notes>\n', "broken.xml")
    run_next(db_session)
    assert job_row(db_session, invalid).status == "failed"
    resp = client.post(_keep_path(invalid), json=agreed, headers=headers)
    assert resp.status_code == 409, resp.text
    assert "did not fail as cut short" in resp.json()["detail"]

    # Completed.
    whole = _upload(client, headers, nmap_xml(2, cut=False), "whole.xml")
    run_next(db_session)
    assert job_row(db_session, whole).status == "completed"
    resp = client.post(_keep_path(whole), json=agreed, headers=headers)
    assert resp.status_code == 409, resp.text
    assert "This upload is completed, not failed" in resp.json()["detail"]

    # Still queued (nothing ran it yet).
    waiting = _upload(client, headers, nmap_xml(4), "waiting.xml")
    resp = client.post(_keep_path(waiting), json=agreed, headers=headers)
    assert resp.status_code == 409 and "This upload is queued, not failed" in resp.json()["detail"]
    run_next(db_session)

    assert _kept_rows(db_session) == []


def test_a_file_no_longer_kept_is_a_409(client, db_session, test_project):
    from pathlib import Path

    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)
    job = job_row(db_session, job_id)

    # Gone from the disk (the row does not know yet): refused under the row
    # lock by the one re-queue path, and the audit row goes with the rollback.
    Path(job.storage_path).unlink()
    resp = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert resp.status_code == 409, resp.text
    assert "no longer kept on the server" in resp.json()["detail"]
    assert job_row(db_session, job_id).status == "failed"
    assert _kept_rows(db_session) == []

    # Recorded as removed (the retention sweep's stamp): the same answer.
    from datetime import datetime, timezone
    job = job_row(db_session, job_id)
    job.file_removed_at = datetime.now(timezone.utc)
    db_session.commit()
    resp = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert resp.status_code == 409 and "no longer kept on the server" in resp.json()["detail"]
    assert _kept_rows(db_session) == []


@pytest.mark.parametrize("role", ["auditor", "viewer"])
def test_a_read_only_operators_agent_is_refused(client, db_session, test_project, role):
    """The gate's default for a POST that declares nothing: the operator must
    be able to write to the project.  Stating agreement changes nothing."""
    writer = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, writer)
    resp = client.post(
        _keep_path(job_id), json={"operator_agreed": True},
        headers=_headers(db_session, test_project, role),
    )
    assert resp.status_code == 403, resp.text
    assert job_row(db_session, job_id).status == "failed" and _kept_rows(db_session) == []


def test_the_route_declares_nothing_so_the_write_default_holds():
    from app.main import app
    from tests.agent_route_declarations import agent_route_declarations

    access = agent_route_declarations(app)[("POST", "/api/v1/agent/uploads/{job_id}/keep-truncated")]
    assert access.session_metadata_write is False


# ---------------------------------------------------------------------------
# Agreed: the partial import, and its record
# ---------------------------------------------------------------------------

def test_an_agreed_request_imports_what_the_file_holds_and_is_recorded(
    client, db_session, test_project,
):
    pid = test_project.id
    user = _member(db_session, test_project, ProjectRole.ANALYST)
    headers = {"X-API-Key": _key_for(db_session, test_project, user)}
    job_id = _failed_cut_upload(client, db_session, headers)
    assert scans_of(db_session, pid) == [] and host_ips(db_session, pid) == []

    resp = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    # The job's new state, in the shape the poll gives.
    answer = resp.json()
    assert answer["job_id"] == job_id and answer["status"] == "queued"
    assert answer["failure_kind"] is None and answer["scan_id"] is None
    assert set(answer) == set(client.get(f"{UPLOADS}/{job_id}", headers=headers).json())

    # Re-queued by the one path, with one attempt's instruction.
    queued = job_row(db_session, job_id)
    assert queued.status == "queued" and queued.options[KEEP_TRUNCATED_OPTION] is True
    assert queued.parse_error_id is None and "import what the cut-short file holds" in queued.message

    # The record: who, which job, how much — never the file's content.
    (audit,) = _kept_rows(db_session)
    session_id = client.get("/api/v1/agent/identity", headers=headers).json()["session_id"]
    assert audit.user_id == user.id and audit.success is True
    assert (audit.resource_type, audit.resource_id) == ("ingestion_job", str(job_id))
    assert audit.details == {
        "job_id": job_id, "project_id": pid, "agent_session_id": session_id,
        "file_type": "nmap_xml", "complete_records": 3,
    }
    assert "10.88.0." not in json.dumps(audit.details)

    # A second request while it waits is refused, and is not a second record.
    again = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert again.status_code == 409 and "This upload is queued, not failed" in again.json()["detail"]
    assert len(_kept_rows(db_session)) == 1

    run_next(db_session)
    polled = client.get(f"{UPLOADS}/{job_id}", headers=headers).json()
    assert polled["status"] == "completed" and polled["failure_kind"] is None
    job = job_row(db_session, job_id)
    assert job.partial is True
    assert "it ends after 3 complete hosts" in job.parser_warnings
    assert "hosts after this point are MISSING, not down" in job.parser_warnings
    assert host_ips(db_session, pid) == ["10.88.0.1", "10.88.0.2", "10.88.0.3"]
    (scan,) = scans_of(db_session, pid)
    assert polled["scan_id"] == scan.id == job.scan_id

    # The people's page shows it as a partial import that needs someone.
    page = client.get(
        f"/api/v1/projects/{pid}/parse-errors/ingestion-results",
        params={"status": "needs_attention", "limit": 200},
    )
    assert page.status_code == 200, page.text
    (row,) = [item for item in page.json()["items"] if item["id"] == job_id]
    assert (row["status"], row["partial"], row["failure_kind"]) == ("completed", True, None)
    issues = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers).json()["issues"]
    (issue,) = [i for i in issues if i["job_id"] == job_id]
    assert issue["kind"] == "partial" and issue["needs_attention"] is True

    # Completed: nothing more to keep.
    done = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert done.status_code == 409 and len(_kept_rows(db_session)) == 1


def test_the_instruction_is_that_attempts_only(client, db_session, test_project):
    """A cancel during the kept attempt cleans up, and the upload is then a
    cut-short failure again when a person retries it plainly."""
    from tests.ingestion_job_harness import on_heartbeat

    pid = test_project.id
    headers = _headers(db_session, test_project)
    job_id = _upload(client, headers, nmap_xml(250))
    run_next(db_session)
    assert client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers).status_code == 200

    svc = IngestionService()
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)) as beats:
        run_next(db_session, svc)
    assert len(beats) >= 2
    job = job_row(db_session, job_id)
    assert job.status == "failed" and job.error_message == "Cancelled by user"
    assert scans_of(db_session, pid) == [] and host_ips(db_session, pid) == []
    # A cancel is not a cut-short failure: there is nothing to keep by it.
    refused = client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers)
    assert refused.status_code == 409 and "did not fail as cut short" in refused.json()["detail"]

    assert client.post(f"/api/v1/projects/{pid}/upload/jobs/{job_id}/retry").status_code == 200
    assert KEEP_TRUNCATED_OPTION not in job_row(db_session, job_id).options
    run_next(db_session)
    polled = client.get(f"{UPLOADS}/{job_id}", headers=headers).json()
    assert (polled["status"], polled["failure_kind"]) == ("failed", "truncated_file")
    assert host_ips(db_session, pid) == []


def test_the_request_over_mcp_is_the_same_route(client, db_session, test_project):
    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)

    refused = _mcp(client, headers, "tools/call", {
        "name": "keep_truncated_upload", "arguments": {"job_id": job_id},
    }).json()["result"]
    assert refused["isError"] is True
    assert "Ask the operator first" in refused["content"][0]["text"]
    assert _kept_rows(db_session) == []

    # An argument the tool does not take is refused, as for every tool.
    unknown = _mcp(client, headers, "tools/call", {
        "name": "keep_truncated_upload",
        "arguments": {"job_id": job_id, "operator_agreed": True, "keep_truncated": True},
    }).json()
    assert unknown["error"]["code"] == -32602, unknown
    assert _kept_rows(db_session) == []

    kept = _mcp(client, headers, "tools/call", {
        "name": "keep_truncated_upload", "arguments": {"job_id": job_id, "operator_agreed": True},
    }).json()["result"]
    assert kept["isError"] is False, kept
    assert kept["structuredContent"]["status"] == "queued"
    assert len(_kept_rows(db_session)) == 1

    run_next(db_session)
    polled = _mcp(client, headers, "tools/call", {
        "name": "get_upload_job", "arguments": {"job_id": job_id},
    }).json()["result"]["structuredContent"]
    assert polled["status"] == "completed"
    assert host_ips(db_session, test_project.id) == ["10.88.0.1", "10.88.0.2", "10.88.0.3"]


# ---------------------------------------------------------------------------
# What the agent is told — one source, both doors, no claim
# ---------------------------------------------------------------------------

#: Words that would say BlueStick confirmed the operator's answer.  It cannot:
#: the conversation is in the agent's client.
UNBACKED = ("approved", "enforced", "verified", "verifies", "gated", "BlueStick checks that")


def test_both_doors_carry_the_ask_first_rule(client, db_session, test_project):
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    prompt = started.json()["instructions"]
    assert render_truncated_upload_rule() in prompt
    assert "POST /agent/uploads/{job_id}/keep-truncated" in prompt

    opening = _mcp(client, {"X-API-Key": started.json()["api_key"]}, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    }).json()["result"]["instructions"]
    assert render_truncated_upload_rule(over_mcp=True) in opening
    assert "keep_truncated_upload" in opening and "get_upload_job" in opening
    assert "/agent/uploads/{job_id}/keep-truncated" not in opening  # named as a tool there

    for text in (prompt, opening):
        assert "failure_kind truncated_file" in text
        assert TRUNCATED_UPLOAD_CONSENT_MEANING in text
        assert "Only after they say to import it" in text and "every request is recorded" in text


def test_the_rule_and_the_refusal_claim_nothing_the_server_cannot_back():
    from app.api.v1.endpoints.mcp_tools import TOOLS

    texts = [
        render_truncated_upload_rule(), render_truncated_upload_rule(over_mcp=True),
        TRUNCATED_UPLOAD_ASK_FIRST, TOOLS["keep_truncated_upload"]["description"],
        TOOLS["get_upload_job"]["description"],
    ]
    for text in texts:
        for claim in UNBACKED:
            assert claim not in text, (claim, text)
    # What the operator is told before they answer: both outcomes.
    for text in texts[:3]:
        assert "marks the import partial" in text
        assert "nothing from it is imported" in text and "supply the complete file" in text


def test_the_tool_carries_the_rule_and_only_the_routes_arguments():
    from app.api.v1.endpoints.mcp_tools import TOOLS, tool_list_payload

    tool = TOOLS["keep_truncated_upload"]
    assert "ASK THE OPERATOR FIRST" in tool["description"]
    assert "recorded" in tool["description"] and "operator_agreed: true" in tool["description"]
    assert set(tool["input_schema"]["properties"]) == {"job_id", "operator_agreed"}
    assert tool["path"] == "/api/v1/agent/uploads/{job_id}/keep-truncated"
    # Reads to a client as a write to confirm, never as one to wave through.
    notes = {t["name"]: t["annotations"] for t in tool_list_payload()}["keep_truncated_upload"]
    assert notes["readOnlyHint"] is False and notes["destructiveHint"] is True
    assert notes["idempotentHint"] is False and not tool.get("additive")
    # The poll and the issues list point at it; neither says no call exists.
    for name in ("get_upload_job", "assist_list_ingestion_issues"):
        described = TOOLS[name]["description"]
        assert "keep_truncated_upload" in described
        assert "no tool makes it" not in described and "no agent call" not in described


def test_the_people_and_the_agents_share_the_one_requeue(monkeypatch, client, db_session, test_project):
    """Never a second implementation: the agents' route reaches
    ``staged_import_service.queue_job`` with the instruction, as the people's
    retry does."""
    from app.services import staged_import_service

    calls = []
    real = staged_import_service.queue_job

    def spy(db, job_id, **kwargs):
        calls.append((job_id, kwargs.get("allowed_from"), kwargs.get("keep_truncated")))
        return real(db, job_id, **kwargs)

    monkeypatch.setattr(staged_import_service, "queue_job", spy)
    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)
    assert client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers).status_code == 200
    assert calls == [(job_id, ("failed",), True)]
    assert db_session.query(models.IngestionJob).filter_by(id=job_id).one().status == "queued"
