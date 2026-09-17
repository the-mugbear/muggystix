"""External MCP review (2026-09-17) — the reviewer's reproduction cases, kept
as the acceptance tests for the remediation.

Each test reproduced a defect against the tree at 2.343.1; the remediation
makes them pass without loosening the assertions.  Findings, in the
reviewer's numbering:

1. an MCP path argument could redirect a tool to a different endpoint;
2. ``assist_list_findings(status="all")`` returned nothing, and the
   vocabulary advertised statuses the enum does not have;
3. finding detail omitted replies (and their attachments) on the evidence note;
4. finding detail dropped named-endpoint identity and counted endpoints as hosts;
5. two open recon runs left the MCP tools unable to select or complete either;
6. an auditor could not end their own session over MCP;
7. host notes were silently capped with no total or continuation;
8. ``create_test_plan`` (and other appends) advertised ``idempotentHint: true``.
"""
from app.db.models import Scope, Subnet
from app.db.models_agent import AgentSession, ReconSession
from app.db.models_auth import UserRole
from app.db.models_project import ProjectMembership, ProjectRole


def start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "isolated MCP review"})
    assert r.status_code == 201, r.text
    return r.json()["api_key"]


def call(client, key, name, **arguments):
    r = client.post("/api/v1/mcp", headers={"X-API-Key": key}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_note_host_id_cannot_redirect_to_session_end(client, db_session, test_project):
    key = start(client, test_project)
    response = call(client, key, "assist_add_note", host_id="../session/end#", body="probe")
    db_session.expire_all()
    session = db_session.query(AgentSession).filter_by(project_id=test_project.id).one()
    assert session.status == "active", response


def test_auditor_can_end_own_session(client, db_session, test_project, test_user):
    test_user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role=ProjectRole.AUDITOR.value))
    db_session.commit()
    key = start(client, test_project)
    response = call(client, key, "end_session", notes="done")
    assert not response.get("result", {}).get("isError"), response


def test_two_recon_runs_can_be_selected_and_completed(client, db_session, test_project):
    """Rewritten from the reviewer's reproduction, which asserted the BROKEN
    state (the explicit selector answered -32602 as an unknown argument, and
    neither run could be completed or the session ended).  This asserts the
    intended behaviour: with two runs open, an unselected call says which runs
    it could mean, the selector is accepted on every recon tool, each run can
    be completed by id, and the session can then end."""
    key = start(client, test_project)
    run_ids = []
    for n in (1, 2):
        scope = Scope(name=f"review-{n}", project_id=test_project.id)
        db_session.add(scope)
        db_session.flush()
        db_session.add(Subnet(scope_id=scope.id, cidr=f"10.98.{n}.0/24"))
        db_session.commit()
        response = call(client, key, "start_recon", scope_id=scope.id)
        assert not response.get("result", {}).get("isError"), response
        run_ids.append(response["result"]["structuredContent"]["recon_session_id"])
    assert len(set(run_ids)) == 2

    # No selector, two runs: a tool error that names both candidates — not a
    # protocol error, and not a silent pick.
    ambiguous = call(client, key, "recon_get_context")
    assert ambiguous["result"]["isError"] is True, ambiguous
    text = str(ambiguous["result"])
    assert "ambiguous_recon_run" in text and all(str(r) in text for r in run_ids), ambiguous

    # The selector is accepted and picks the run.
    for run_id in run_ids:
        explicit = call(client, key, "recon_get_context", recon_session_id=run_id)
        assert "error" not in explicit, explicit
        assert explicit["result"].get("isError") is not True, explicit
        assert explicit["result"]["structuredContent"]["recon_session_id"] == run_id
        summary = call(client, key, "recon_get_summary", recon_session_id=run_id)
        assert summary["result"]["structuredContent"]["recon_session_id"] == run_id

    # A selector for a run this session did not open is refused, not honoured.
    foreign = call(client, key, "recon_get_context", recon_session_id=max(run_ids) + 1000)
    assert foreign["result"]["isError"] is True

    # Each run completes by id (the selector rides the POST as a query param),
    # then the session can end because nothing is left open.
    assert call(client, key, "recon_complete", recon_session_id=run_ids[0], notes="one")["result"].get("isError") is not True
    # One run left: no selector needed any more.
    last = call(client, key, "recon_complete", notes="two")
    assert last["result"].get("isError") is not True, last
    assert last["result"]["structuredContent"]["recon_session_id"] == run_ids[1]
    ended = call(client, key, "end_session", notes="done")
    assert ended["result"].get("isError") is not True, ended
    db_session.expire_all()
    assert {s.status for s in db_session.query(ReconSession).filter(ReconSession.id.in_(run_ids))} == {"completed"}


def test_create_plan_is_not_advertised_idempotent(client, test_project):
    key = start(client, test_project)
    first = call(client, key, "create_test_plan", title="review duplicate")
    second = call(client, key, "create_test_plan", title="review duplicate")
    assert first["result"]["structuredContent"]["id"] != second["result"]["structuredContent"]["id"]
    r = client.post("/api/v1/mcp", headers={"X-API-Key": key}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool = next(t for t in r.json()["result"]["tools"] if t["name"] == "create_test_plan")
    assert tool["annotations"]["idempotentHint"] is False, (first, second, tool["annotations"])


def test_all_status_includes_existing_findings(client, db_session, test_project):
    from app.db.models_findings import Finding
    db_session.add(Finding(project_id=test_project.id, title="existing finding", severity="high", status="open", source="manual"))
    db_session.commit()
    key = start(client, test_project)
    assert call(client, key, "assist_list_findings")["result"]["structuredContent"]["total"] == 1
    response = call(client, key, "assist_list_findings", status="all")
    assert response["result"]["structuredContent"]["total"] == 1, response


def test_vocabulary_matches_finding_statuses(client, test_project):
    from app.db.models_findings import FindingStatus
    response = call(client, start(client, test_project), "assist_get_vocabulary")
    assert set(response["result"]["structuredContent"]["finding_statuses"]) == {s.value for s in FindingStatus}, response


def test_finding_detail_includes_source_thread_reply_attachment(client, db_session, test_project, test_user):
    from app.db.models import Host, Annotation, NoteAttachment
    from app.db.models_findings import Finding
    host = Host(project_id=test_project.id, ip_address="10.98.0.3", state="up")
    db_session.add(host)
    db_session.flush()
    root = Annotation(host_id=host.id, user_id=test_user.id, body="initial observation", status="open")
    db_session.add(root)
    db_session.flush()
    root.thread_root_id = root.id
    reply = Annotation(host_id=host.id, user_id=test_user.id, body="critical qualification of evidence", parent_id=root.id, thread_root_id=root.id, status="open")
    db_session.add(reply)
    db_session.flush()
    attachment = NoteAttachment(annotation_id=reply.id, project_id=test_project.id, filename="proof.png", content_type="image/png", size_bytes=123, storage_path="review-does-not-exist.png")
    finding = Finding(project_id=test_project.id, title="source thread", severity="high", status="open", source="note", evidence_annotation_id=root.id)
    db_session.add_all([finding, attachment])
    db_session.commit()
    response = call(client, start(client, test_project), "assist_get_finding", finding_id=finding.id)
    assert "critical qualification of evidence" in str(response), response
    assert "proof.png" in str(response), response


def test_finding_keeps_named_endpoints_on_one_ip(client, db_session, test_project):
    from app.db.models import Host, DNSName
    from app.db.models_findings import Finding, FindingHost
    host = Host(project_id=test_project.id, ip_address="10.98.0.4", state="up")
    finding = Finding(project_id=test_project.id, title="two virtual hosts", severity="high", status="open", source="manual")
    db_session.add_all([host, finding])
    db_session.flush()
    for fqdn in ("first.example.test", "second.example.test"):
        name = DNSName(project_id=test_project.id, fqdn=fqdn)
        db_session.add(name)
        db_session.flush()
        db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, name_id=name.id, host_status="open"))
    db_session.commit()
    response = call(client, start(client, test_project), "assist_get_finding", finding_id=finding.id)
    assert "first.example.test" in str(response) and "second.example.test" in str(response), response
    assert response["result"]["structuredContent"]["host_count"] == 1, response


def test_host_notes_signal_truncation(client, db_session, test_project, test_user):
    from app.db.models import Host, Annotation
    host = Host(project_id=test_project.id, ip_address="10.98.0.5", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add_all([Annotation(host_id=host.id, user_id=test_user.id, body=f"review note {i}", status="open") for i in range(51)])
    db_session.commit()
    response = call(client, start(client, test_project), "assist_get_host_notes", host_id=host.id)
    data = response["result"]["structuredContent"]
    assert len(data["items"]) == 50
    assert data.get("has_more") or data.get("truncated") or data.get("total") == 51, response
