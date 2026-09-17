"""External MCP review (2026-09-17), findings 2 / 3 / 4 / 7 — the behaviour
behind the acceptance cases in ``test_mcp_review.py``, pinned in detail.

2. "all" (and "", "any") mean no filter on the findings list; the vocabulary
   is the canonical enum.
3. Finding detail carries the whole source thread — replies on the evidence
   note, their attachments, and who (person or agent) wrote each.
4. Named endpoints on one IP keep their identity; hosts are counted as
   distinct addresses, endpoints as rows.
7. Host notes are a page with a total and a continuation; host detail says
   when its web-interface (and technology) lists were capped.
"""
from app.db.models import Annotation, DNSName, Host, NoteAttachment, Scan, WebInterface
from app.db.models_findings import Finding, FindingHost, FindingSeverity, FindingStatus


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


# --- finding 2 -------------------------------------------------------------

def test_findings_list_treats_all_empty_and_any_as_no_filter(client, db_session, test_project):
    db_session.add_all([
        Finding(project_id=test_project.id, title="open one", severity="high", status="open", source="manual"),
        Finding(project_id=test_project.id, title="retest one", severity="low", status="retest", source="manual"),
    ])
    db_session.commit()
    h = _start(client, test_project)
    for value in ("all", "", "any", "ALL"):
        r = client.get("/api/v1/agent/assist/findings", headers=h, params={"status": value, "severity": value, "source": value})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 2, (value, r.json())
        assert r.json()["severity_counts"], value
    # A real status still filters.
    r = client.get("/api/v1/agent/assist/findings", headers=h, params={"status": "retest"})
    assert [f["title"] for f in r.json()["findings"]] == ["retest one"]


def test_vocabulary_statuses_and_severities_are_the_enums(client, test_project):
    r = client.get("/api/v1/agent/assist/vocabulary", headers=_start(client, test_project))
    assert r.status_code == 200, r.text
    assert r.json()["finding_statuses"] == [s.value for s in FindingStatus]
    assert r.json()["severities"] == [s.value for s in FindingSeverity]
    assert "triaged" not in r.json()["finding_statuses"]
    assert "accepted_risk" in r.json()["finding_statuses"]


# --- finding 3 -------------------------------------------------------------

def test_finding_detail_carries_the_evidence_thread_with_attachments_and_authorship(
    client, db_session, test_project, test_user,
):
    host = Host(project_id=test_project.id, ip_address="10.99.0.3", state="up")
    db_session.add(host)
    db_session.flush()
    root = Annotation(host_id=host.id, user_id=test_user.id, body="root observation", status="open")
    db_session.add(root)
    db_session.flush()
    root.thread_root_id = root.id
    reply_human = Annotation(
        host_id=host.id, user_id=test_user.id, body="only on the staging vhost",
        parent_id=root.id, thread_root_id=root.id, status="open",
    )
    reply_agent = Annotation(
        host_id=host.id, user_id=test_user.id, body="agent re-checked the banner",
        parent_id=root.id, thread_root_id=root.id, status="open", actor_type="agent",
    )
    unrelated = Annotation(host_id=host.id, user_id=test_user.id, body="different thread", status="open")
    db_session.add_all([reply_human, reply_agent, unrelated])
    db_session.flush()
    db_session.add(NoteAttachment(
        annotation_id=reply_human.id, project_id=test_project.id, filename="staging.png",
        content_type="image/png", size_bytes=10, storage_path="nope.png",
    ))
    finding = Finding(
        project_id=test_project.id, title="thread", severity="high", status="open",
        source="note", evidence_annotation_id=root.id,
    )
    db_session.add(finding)
    db_session.commit()

    r = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=_start(client, test_project))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evidence_note"]["id"] == root.id
    assert body["evidence_note"]["actor_type"] == "user"
    thread = body["evidence_thread"]
    assert [n["body"] for n in thread] == ["only on the staging vhost", "agent re-checked the banner"]
    assert [n["actor_type"] for n in thread] == ["user", "agent"]
    assert thread[0]["attachments"][0]["filename"] == "staging.png"
    assert thread[0]["attachments"][0]["download_path"].endswith("/assist/attachments/" + str(
        thread[0]["attachments"][0]["id"]))
    # A note on the same host but another thread is not part of the evidence.
    assert "different thread" not in str(thread)
    # The finding's own comment thread is still separate.
    assert body["comments"] == []


# --- finding 4 -------------------------------------------------------------

def test_finding_detail_keeps_endpoint_identity_and_counts_hosts_distinctly(client, db_session, test_project):
    host = Host(project_id=test_project.id, ip_address="10.99.0.4", state="up")
    other = Host(project_id=test_project.id, ip_address="10.99.0.5", state="up")
    finding = Finding(project_id=test_project.id, title="vhosts", severity="medium", status="open", source="manual")
    db_session.add_all([host, other, finding])
    db_session.flush()
    for fqdn in ("a.example.test", "b.example.test"):
        name = DNSName(project_id=test_project.id, fqdn=fqdn)
        db_session.add(name)
        db_session.flush()
        db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, name_id=name.id, host_status="open"))
    db_session.add(FindingHost(finding_id=finding.id, host_id=other.id, host_status="open"))
    db_session.commit()

    h = _start(client, test_project)
    r = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host_count"] == 2
    assert body["endpoint_count"] == 3
    named = sorted((x["ip_address"], x["fqdn"]) for x in body["hosts"])
    assert named == [("10.99.0.4", "a.example.test"), ("10.99.0.4", "b.example.test"), ("10.99.0.5", None)]
    assert all(x["name_id"] for x in body["hosts"] if x["fqdn"])

    # The list view counts the same way.
    r = client.get("/api/v1/agent/assist/findings", headers=h)
    row = next(f for f in r.json()["findings"] if f["id"] == finding.id)
    assert row["host_count"] == 2 and row["endpoint_count"] == 3
    assert row["hosts"] == ["10.99.0.4", "10.99.0.5"]


# --- finding 7 -------------------------------------------------------------

def test_host_notes_page_reports_total_and_continues_with_offset(client, db_session, test_project, test_user):
    host = Host(project_id=test_project.id, ip_address="10.99.0.6", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add_all([
        Annotation(host_id=host.id, user_id=test_user.id, body=f"n{i}", status="open") for i in range(7)
    ])
    db_session.commit()
    h = _start(client, test_project)

    first = client.get(f"/api/v1/agent/assist/hosts/{host.id}/notes", headers=h, params={"limit": 3}).json()
    assert first["total"] == 7 and first["has_more"] is True
    assert first["limit"] == 3 and first["offset"] == 0
    assert len(first["items"]) == 3

    last = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/notes", headers=h, params={"limit": 3, "offset": 6},
    ).json()
    assert len(last["items"]) == 1 and last["has_more"] is False

    seen = set()
    for offset in (0, 3, 6):
        page = client.get(
            f"/api/v1/agent/assist/hosts/{host.id}/notes", headers=h, params={"limit": 3, "offset": offset},
        ).json()
        seen.update(n["id"] for n in page["items"])
    assert len(seen) == 7


def test_host_detail_says_when_web_interfaces_were_capped(client, db_session, test_project):
    from app.api.v1.endpoints.agent_assist import _TECH_CAP, _WEB_INTERFACE_CAP
    host = Host(project_id=test_project.id, ip_address="10.99.0.7", state="up")
    scan = Scan(project_id=test_project.id, filename="w.json", scan_type="httpx", tool_name="httpx")
    db_session.add_all([host, scan])
    db_session.flush()
    for i in range(_WEB_INTERFACE_CAP + 2):
        db_session.add(WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=test_project.id, source="httpx",
            port=8000 + i, url=f"http://10.99.0.7:{8000 + i}/",
            technologies=[f"t{j}" for j in range(_TECH_CAP + 1)] if i == 0 else [],
        ))
    db_session.commit()

    r = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=_start(client, test_project))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["web_interfaces"]) == _WEB_INTERFACE_CAP
    assert body["web_interfaces_total"] == _WEB_INTERFACE_CAP + 2
    assert body["web_interfaces_truncated"] is True
    capped = next(w for w in body["web_interfaces"] if w["port"] == 8000)
    assert len(capped["technologies"]) == _TECH_CAP and capped["technologies_truncated"] is True
    assert next(w for w in body["web_interfaces"] if w["port"] == 8001)["technologies_truncated"] is False
