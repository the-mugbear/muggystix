"""v2.428.0 — an agent can answer what the host inspector, the finding page and
the Collaboration feed show.

Each read below is compared against the JWT route the page itself calls
wherever the two overlap, so the agent and the page cannot state different
facts about the same host or finding.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.db import models


def _assist(client, project_id):
    body = client.post(f"/api/v1/projects/{project_id}/assist/start", json={}).json()
    return {"X-API-Key": body["api_key"]}


@pytest.fixture
def count_queries(db_session):
    from tests.conftest import engine

    counter = {"n": 0}

    def _before(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    def _run(fn):
        counter["n"] = 0
        event.listen(engine, "before_cursor_execute", _before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return counter["n"]

    return _run


# ---------------------------------------------------------------------------
# Host detail
# ---------------------------------------------------------------------------

def test_assist_host_detail_lists_the_names_seen_at_the_address(
    client, db_session, test_project
):
    """Regression: AssistHostDetail inherited ``names`` and the handler never
    filled it, so every host read as having no names while /agent/hosts/{id}
    listed them."""
    from app.services import dns_name_service as svc

    host = models.Host(project_id=test_project.id, ip_address="203.0.113.44", state="up")
    db_session.add(host)
    db_session.flush()
    for fqdn in ("portal.example.com", "vpn.example.com"):
        svc.record_observation(
            db_session, project_id=test_project.id, name=fqdn, record_type="A",
            value="203.0.113.44",
        )
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers).json()
    assert sorted(body["names"]) == ["portal.example.com", "vpn.example.com"]
    browse = client.get(f"/api/v1/agent/hosts/{host.id}", headers=headers).json()
    assert sorted(browse["names"]) == sorted(body["names"])


def _seed_inspector_host(db_session, project, user):
    from app.db.models_confidence import ConflictHistory
    from app.db.models_findings import Finding, FindingHost

    scan_a = models.Scan(project_id=project.id, filename="first.xml", tool_name="nmap")
    scan_b = models.Scan(project_id=project.id, filename="second.xml", tool_name="nmap")
    db_session.add_all([scan_a, scan_b])
    db_session.flush()
    host = models.Host(
        project_id=project.id, ip_address="10.44.0.9", state="up",
        os_name="Windows Server 2012 R2", os_family="Windows", smb_signing="disabled",
        mac_address="00:11:22:33:44:55", netbios_name="FILESRV",
    )
    db_session.add(host)
    db_session.flush()
    port = models.Port(
        host_id=host.id, port_number=445, protocol="tcp", state="open",
        service_name="microsoft-ds",
    )
    db_session.add(port)
    db_session.flush()
    db_session.add(models.Script(
        port_id=port.id, scan_id=scan_a.id, script_id="smb2-security-mode",
        output="Message signing enabled but not required",
    ))
    db_session.add(models.Script(
        port_id=port.id, scan_id=scan_a.id, script_id="ssl-enum-ciphers",
        output="X" * 5000,
    ))
    db_session.add(models.HostScript(
        host_id=host.id, scan_id=scan_a.id, script_id="smb-os-discovery",
        output="OS: Windows Server 2012 R2",
    ))
    tag = models.HostTag(project_id=project.id, name="crown-jewel", color="red")
    db_session.add(tag)
    db_session.flush()
    db_session.add(models.HostTagAssignment(host_id=host.id, tag_id=tag.id))
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=user.id, status=models.FollowStatus.IN_REVIEW,
        assigned_at=datetime.now(timezone.utc), assigned_by_id=user.id,
    ))
    db_session.add(ConflictHistory(
        host_id=host.id, field_name="os_name",
        previous_value="Windows 10", new_value="Windows Server 2012 R2",
        previous_scan_id=scan_a.id, new_scan_id=scan_b.id,
    ))
    db_session.add(models.WebInterface(
        project_id=project.id, host_id=host.id, scan_id=scan_a.id,
        url="https://10.44.0.9/", port=443, source="httpx",
        cert_subject_org="Acme Corp", cert_self_signed=True,
        cert_not_after=datetime.now(timezone.utc) + timedelta(days=9),
    ))
    db_session.add(models.Annotation(
        host_id=host.id, user_id=user.id, body="Looked at SMB", status=models.NoteStatus.OPEN,
    ))
    finding = Finding(
        project_id=project.id, title="SMB signing not required", severity="medium",
        status="confirmed", source="manual", created_by_id=user.id,
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id))
    db_session.commit()
    return host


def test_assist_host_detail_states_what_the_inspector_shows(
    client, db_session, test_project, test_user
):
    host = _seed_inspector_host(db_session, test_project, test_user)
    headers = _assist(client, test_project.id)

    agent = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers).json()
    page = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}").json()

    # The facts both carry must be the same facts.
    for field in (
        "smb_signing", "mac_address", "netbios_name", "weakness_flags",
        "weakness_labels", "scope_membership", "cert_orgs",
    ):
        assert agent[field] == page[field], field
    assert [t["name"] for t in agent["tags"]] == [t["name"] for t in page["tags"]] == ["crown-jewel"]
    assert [a["user_id"] for a in agent["assignees"]] == [a["user_id"] for a in page["assignees"]]
    assert set(agent["assessment"]) == set(page["assessment"])
    assert [c["url"] for c in agent["cert_status"]] == [c["url"] for c in page["cert_status"]]

    assert "smb_unsigned" in agent["weakness_flags"]
    assert agent["note_count"] == 1
    assert agent["finding_count"] == 1

    conflicts = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/conflicts").json()
    assert agent["conflict_count"] == conflicts["conflict_count"] == 1
    c = agent["conflicts"][0]
    assert (c["field_name"], c["previous_scan_filename"], c["new_scan_filename"]) == (
        "os_name", "first.xml", "second.xml",
    )

    # NSE output is there, bounded, and says when it was cut.
    port = next(p for p in agent["ports"] if p["port_number"] == 445)
    by_id = {s["script_id"]: s for s in port["scripts"]}
    assert by_id["smb2-security-mode"]["output"].startswith("Message signing")
    assert by_id["smb2-security-mode"]["output_truncated"] is False
    assert by_id["ssl-enum-ciphers"]["output_truncated"] is True
    assert len(by_id["ssl-enum-ciphers"]["output"]) < 5000
    assert port["scripts_truncated"] is True
    assert agent["host_scripts"][0]["script_id"] == "smb-os-discovery"


def test_assist_host_detail_statement_count_does_not_grow_with_ports(
    client, db_session, test_project, count_queries
):
    """Every new block is a fixed number of statements — scripts and tags are
    selectin-loaded, not fetched per port."""
    scan = models.Scan(project_id=test_project.id, filename="q.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()

    def _host(ip, n_ports):
        h = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        for i in range(n_ports):
            p = models.Port(host_id=h.id, port_number=1000 + i, protocol="tcp", state="open")
            db_session.add(p)
            db_session.flush()
            db_session.add(models.Script(port_id=p.id, scan_id=scan.id, script_id="banner", output="x"))
        db_session.commit()
        return h

    small, large = _host("10.45.0.1", 3), _host("10.45.0.2", 40)
    headers = _assist(client, test_project.id)
    # Warm the request path (the key's first-use bookkeeping is not the
    # handler's cost and settles after the first couple of calls).
    for _ in range(2):
        client.get(f"/api/v1/agent/assist/hosts/{small.id}", headers=headers)
    n_small = count_queries(lambda: client.get(f"/api/v1/agent/assist/hosts/{small.id}", headers=headers))
    n_large = count_queries(lambda: client.get(f"/api/v1/agent/assist/hosts/{large.id}", headers=headers))
    # 37 more ports: a per-port query would add 37+.  One statement of slack
    # for the key's periodic last-used bookkeeping, which lands on whichever
    # request crosses its interval.
    assert n_large - n_small <= 1, (n_small, n_large)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def test_assist_context_carries_engagement_dates_and_members(
    client, db_session, test_project, test_user
):
    """Over MCP the dates were unreachable (only /agent/project had them, and it
    is not a tool), and "who is on this engagement" had no answer at all."""
    from app.db.models_project import ProjectMembership

    test_project.start_date = datetime(2026, 9, 1, tzinfo=timezone.utc)
    test_project.end_date = datetime(2026, 9, 30, tzinfo=timezone.utc)
    if not db_session.query(ProjectMembership).filter_by(
        project_id=test_project.id, user_id=test_user.id
    ).first():
        db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="analyst"))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/context", headers=headers).json()
    assert body["project"]["start_date"].startswith("2026-09-01")
    assert body["project"]["end_date"].startswith("2026-09-30")
    member = next(m for m in body["members"] if m["user_id"] == test_user.id)
    assert member["username"] == test_user.username
    assert member["role"]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def test_assist_notes_carry_threads_assignees_and_attachments(
    client, db_session, test_project, test_user
):
    host = models.Host(project_id=test_project.id, ip_address="10.46.0.3", state="up")
    db_session.add(host)
    db_session.flush()
    root = models.Annotation(
        host_id=host.id, user_id=test_user.id, body="Default creds on the admin panel",
        status=models.NoteStatus.OPEN, note_type="question",
        assignee_id=test_user.id, due_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    db_session.add(root)
    db_session.flush()
    root.thread_root_id = root.id
    reply = models.Annotation(
        host_id=host.id, user_id=test_user.id, body="Confirmed, screenshot attached",
        status=models.NoteStatus.OPEN, parent_id=root.id, thread_root_id=root.id,
    )
    db_session.add(reply)
    db_session.flush()
    db_session.add(models.NoteAttachment(
        annotation_id=reply.id, project_id=test_project.id, filename="panel.png",
        content_type="image/png", size_bytes=1234, storage_path=f"{reply.id}/panel.png",
        uploaded_by_id=test_user.id,
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    page = client.get(f"/api/v1/agent/assist/hosts/{host.id}/notes", headers=headers).json()
    by_id = {n["id"]: n for n in page["items"]}
    assert by_id[root.id]["parent_id"] is None
    assert by_id[root.id]["assignee"] == test_user.username
    assert by_id[root.id]["note_type"] == "question"
    assert by_id[root.id]["due_at"].startswith("2026-10-01")
    assert by_id[reply.id]["parent_id"] == root.id
    assert by_id[reply.id]["thread_root_id"] == root.id
    att = by_id[reply.id]["attachments"][0]
    assert att["filename"] == "panel.png"
    assert att["uploaded_by"] == test_user.username
    assert att["download_path"] == f"/api/v1/agent/assist/attachments/{att['id']}"


def test_recent_notes_name_a_non_host_target(client, db_session, test_project, test_user):
    """A finding comment read as a note about nothing: only host notes said
    where they were."""
    from app.db.models_findings import Finding

    finding = Finding(
        project_id=test_project.id, title="Weak TLS on the VPN", severity="medium",
        status="confirmed", source="manual", created_by_id=test_user.id,
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add(models.Annotation(
        finding_id=finding.id, user_id=test_user.id, body="Client asked for a retest",
        status=models.NoteStatus.OPEN,
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    notes = client.get("/api/v1/agent/assist/notes", headers=headers).json()
    note = next(n for n in notes if n["body"] == "Client asked for a retest")
    assert note["target"] == {"kind": "finding", "id": finding.id, "label": "Weak TLS on the VPN"}
    assert note["host_id"] is None


# ---------------------------------------------------------------------------
# Finding detail
# ---------------------------------------------------------------------------

def test_assist_finding_detail_carries_report_text_and_status_history(
    client, db_session, test_project, test_user
):
    from app.db.models_findings import Finding, FindingHost, FindingStatusHistory

    h1 = models.Host(project_id=test_project.id, ip_address="10.47.0.1", state="up")
    h2 = models.Host(project_id=test_project.id, ip_address="10.47.0.2", state="up")
    db_session.add_all([h1, h2])
    db_session.flush()
    finding = Finding(
        project_id=test_project.id, title="Outdated OpenSSH", severity="high",
        status="confirmed", source="manual", created_by_id=test_user.id,
        description="The SSH service runs **OpenSSH 7.2**.",
        recommendation="Upgrade to a supported release.",
        cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add_all([
        FindingHost(finding_id=finding.id, host_id=h1.id),
        FindingHost(finding_id=finding.id, host_id=h2.id, host_status="remediated"),
        FindingStatusHistory(
            finding_id=finding.id, from_status="open", to_status="confirmed",
            changed_by_id=test_user.id, summary="Banner and version check agree",
        ),
    ])
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=headers).json()
    page = client.get(f"/api/v1/projects/{test_project.id}/findings/{finding.id}").json()
    history = client.get(f"/api/v1/projects/{test_project.id}/findings/{finding.id}/history").json()

    assert body["report_text"] == page["report_text"]
    assert body["report_text"]["cvss_score_from_vector"] is True
    assert body["endpoint_status_counts"] == page["endpoint_status_counts"]
    assert body["endpoint_status_counts"]["remediated"] == 1
    assert [(e["from_status"], e["to_status"], e["summary"]) for e in body["status_history"]] == [
        (e["from_status"], e["to_status"], e["summary"]) for e in history
    ]
    assert body["status_history"][0]["changed_by"] == test_user.username


def test_scan_rows_name_their_import_job(client, db_session, test_project):
    """MCP acceptance feedback #18: an agent could not get from a scan to the
    job whose uninterpreted lines it wanted, and must not guess scan id = job id."""
    from app.db import models
    scan = models.Scan(project_id=test_project.id, filename="nxc.txt", tool_name="netexec")
    db_session.add(scan)
    db_session.flush()
    job = models.IngestionJob(project_id=test_project.id, filename="stored-nxc.txt", storage_path="/tmp/stored-nxc.txt", original_filename="nxc.txt",
                              status="completed", scan_id=scan.id)
    db_session.add(job)
    db_session.commit()
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "scans"})
    headers = {"X-API-Key": r.json()["api_key"]}
    rows = client.get("/api/v1/agent/assist/scans", headers=headers).json()
    row = next(x for x in rows if x["id"] == scan.id)
    assert row["ingestion_job_id"] == job.id


def test_web_interfaces_carry_their_certificate_facts(client, db_session, test_project):
    """MCP acceptance feedback #12: the TLS state of an HTTPS interface was not
    readable — the typed cert/TLS columns were never serialized."""
    from datetime import datetime, timezone
    from app.db import models
    host = models.Host(project_id=test_project.id, ip_address="10.20.0.5", state="up")
    db_session.add(host)
    db_session.flush()
    scan = models.Scan(project_id=test_project.id, filename="httpx.json", tool_name="httpx")
    db_session.add(scan)
    db_session.flush()
    wi = models.WebInterface(
        project_id=test_project.id, host_id=host.id, scan_id=scan.id, url="https://10.20.0.5/",
        port=443, source="httpx",
        cert_not_after=datetime(2025, 1, 1, tzinfo=timezone.utc), cert_self_signed=True,
        cert_subject_org="Acme", tls_weak_protocol=True,
    )
    db_session.add(wi)
    db_session.commit()
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "tls"})
    headers = {"X-API-Key": r.json()["api_key"]}
    page = client.get(f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=headers)
    assert page.status_code == 200, page.text
    item = page.json()["items"][0]
    assert item["cert_self_signed"] is True
    assert item["tls_weak_protocol"] is True
    assert item["cert_subject_org"] == "Acme"
    assert item["cert_not_after"].startswith("2025-01-01")
