"""v2.428.0 — an agent can answer what the host inspector, the finding page and
the Collaboration feed show.

Each read below is compared against the JWT route the page itself calls
wherever the two overlap, so the agent and the page cannot state different
facts about the same host or finding.
"""
import json
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
        host_id=host.id, user_id=user.id, body="Looked at SMB",
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

def test_assist_notes_carry_threads_labels_and_attachments(
    client, db_session, test_project, test_user
):
    host = models.Host(project_id=test_project.id, ip_address="10.46.0.3", state="up")
    db_session.add(host)
    db_session.flush()
    root = models.Annotation(
        host_id=host.id, user_id=test_user.id, body="Default creds on the admin panel",
        note_type="question", pinned=True,
    )
    db_session.add(root)
    db_session.flush()
    root.thread_root_id = root.id
    reply = models.Annotation(
        host_id=host.id, user_id=test_user.id, body="Confirmed, screenshot attached", parent_id=root.id, thread_root_id=root.id,
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
    assert (by_id[root.id]["note_type"], by_id[root.id]["pinned"]) == ("question", True)
    # A note is discussion: no work state is offered to an agent either.
    assert not {"status", "assignee", "due_at", "resolution_summary"} & set(by_id[root.id])
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
    rows = client.get("/api/v1/agent/assist/scans", headers=headers).json()["items"]
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


def test_web_interfaces_carry_what_the_web_panel_reads_from_the_tls_record(
    client, db_session, test_project
):
    """MCP acceptance run 2: a row with the TLS record but no typed columns
    showed an issuer, expiry and SANs in the web panel and nothing to the agent."""
    host = models.Host(project_id=test_project.id, ip_address="10.20.0.6", state="up")
    scan = models.Scan(project_id=test_project.id, filename="httpx.json", tool_name="httpx")
    db_session.add_all([host, scan])
    db_session.flush()
    sans = [f"n{i}.example.com" for i in range(25)]
    db_session.add(models.WebInterface(
        project_id=test_project.id, host_id=host.id, scan_id=scan.id, url="https://10.20.0.6/",
        port=443, source="httpx",
        tls_info={"subject_cn": "portal.example.com", "subject_an": sans,
                  "issuer_dn": "CN=Example Issuing CA", "tls_version": "tls13"},
    ))
    db_session.commit()
    headers = _assist(client, test_project.id)
    item = client.get(f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=headers).json()["items"][0]
    assert item["tls_version"] == "tls13"
    assert item["cert_issuer"] == "CN=Example Issuing CA"
    assert item["cert_subject_cn"] == "portal.example.com"
    assert item["cert_sans"] == sans[:20] and item["cert_san_total"] == 25


# ---------------------------------------------------------------------------
# MCP acceptance run 2 (2.429.1)
# ---------------------------------------------------------------------------

def _vuln(db_session, host, scan, severity, exploitable):
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
    db_session.add(Vulnerability(
        host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
        severity=severity, title=f"{severity.value} {exploitable}", exploitable=exploitable,
    ))


def test_an_exploitable_critical_is_one_vulnerability_not_two(client, db_session, test_project):
    """``has:critical AND has:exploit`` matched a critical beside an exploitable
    low — 31 hosts where 7 had an exploitable critical.  has:critical_exploit
    and the rows' critical_exploitable_count are the Hosts page's same-row rule."""
    from app.db.models_vulnerability import VulnerabilitySeverity as Sev
    scan = models.Scan(project_id=test_project.id, filename="n.nessus", tool_name="nessus")
    db_session.add(scan)
    real = models.Host(project_id=test_project.id, ip_address="10.61.0.1", state="up")
    paired = models.Host(project_id=test_project.id, ip_address="10.61.0.2", state="up")
    db_session.add_all([real, paired])
    db_session.flush()
    _vuln(db_session, real, scan, Sev.CRITICAL, True)
    _vuln(db_session, paired, scan, Sev.CRITICAL, False)
    _vuln(db_session, paired, scan, Sev.CRITICAL, False)
    _vuln(db_session, paired, scan, Sev.LOW, True)
    db_session.commit()
    headers = _assist(client, test_project.id)

    def ips(q, **params):
        r = client.get("/api/v1/agent/assist/hosts", params={"q": q, **params}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    assert [h["ip_address"] for h in ips("has:critical_exploit")] == ["10.61.0.1"]
    both = ips("has:critical AND has:exploit")
    assert [h["ip_address"] for h in both] == ["10.61.0.1", "10.61.0.2"]
    counts = {h["ip_address"]: (h["exploitable_count"], h["critical_exploitable_count"]) for h in both}
    assert counts == {"10.61.0.1": (1, 1), "10.61.0.2": (1, 0)}

    # The Hosts page states the same counts for the same hosts.
    page = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "has:exploit"}).json()
    assert {h["ip_address"]: (h["exploitable_count"], h["critical_exploitable_count"])
            for h in page["items"]} == counts

    # Worst first is a parameter: the host with two criticals leads.
    worst = ips("has:critical", sort_by="critical_vulns", sort_order="desc")
    assert [h["ip_address"] for h in worst] == ["10.61.0.2", "10.61.0.1"]
    assert client.get("/api/v1/agent/assist/hosts", params={"sort_by": "nonsense"},
                      headers=headers).status_code == 422


def test_host_detail_by_address_is_the_same_answer(client, db_session, test_project):
    """'What's on 10.0.0.5?' took a list call to find the id first."""
    host = models.Host(project_id=test_project.id, ip_address="10.62.0.7", state="up")
    db_session.add(host)
    db_session.commit()
    headers = _assist(client, test_project.id)
    by_id = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers).json()
    by_ip = client.get("/api/v1/agent/assist/hosts/by-ip/10.62.0.7", headers=headers)
    assert by_ip.status_code == 200, by_ip.text
    assert by_ip.json() == by_id
    assert client.get("/api/v1/agent/assist/hosts/by-ip/10.62.0.8", headers=headers).status_code == 404

    mcp = client.post("/api/v1/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_host", "arguments": {"ip": "10.62.0.7"}},
    }).json()["result"]
    assert mcp["isError"] is False
    assert mcp["structuredContent"]["id"] == host.id


def test_scans_narrow_to_one_tool(client, db_session, test_project):
    """'The last two nmap scans' meant reading every scan."""
    for name, tool in (("a.xml", "nmap"), ("b.nessus", "nessus"), ("c.xml", "nmap")):
        db_session.add(models.Scan(project_id=test_project.id, filename=name, tool_name=tool))
    db_session.commit()
    headers = _assist(client, test_project.id)
    page = client.get("/api/v1/agent/assist/scans", params={"tool": "nmap"}, headers=headers).json()
    rows = page["items"]
    assert page["total"] == 2, "the total is the filtered count, not the project's"
    assert sorted(r["filename"] for r in rows) == ["a.xml", "c.xml"]


# ---------------------------------------------------------------------------
# diag 4 (2026-09-30): "how many hosts expose VNC?" disagreed with the page
# ---------------------------------------------------------------------------

def _vnc_hosts(db_session, project):
    """Five hosts the two old definitions of "vnc" disagreed about."""
    rows = {
        "10.70.0.1": (5900, "open", "vnc"),        # both: standard port, identified
        "10.70.0.2": (5900, "open", None),         # masscan-only: open, no service name
        "10.70.0.3": (5800, "open", "vnc-http"),   # VNC off the standard ports
        "10.70.0.4": (5901, "closed", "vnc"),      # not open
        "10.70.0.5": (22, "open", "ssh"),          # nothing to do with it
    }
    for ip, (port, state, name) in rows.items():
        host = models.Host(project_id=project.id, ip_address=ip, state="up")
        db_session.add(host)
        db_session.flush()
        db_session.add(models.Port(host_id=host.id, port_number=port, protocol="tcp",
                                   state=state, service_name=name))
    db_session.commit()


def test_an_agents_service_filter_counts_what_the_hosts_page_counts(client, db_session, test_project):
    """services=vnc on the agent surface expanded to "5900-5905 open", so it
    counted the masscan-only port and missed VNC on 5800 — the Hosts page
    matches the identified service on an open port.  One definition now: the
    list, the count, the stream and the page agree."""
    _vnc_hosts(db_session, test_project)
    headers = _assist(client, test_project.id)
    page = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"services": "vnc"}).json()
    page_ips = {h["ip_address"] for h in page["items"]}
    assert page_ips == {"10.70.0.1", "10.70.0.3"}

    listed = client.get("/api/v1/agent/assist/hosts", params={"services": "vnc"}, headers=headers).json()
    assert {h["ip_address"] for h in listed["items"]} == page_ips
    count = client.get("/api/v1/agent/assist/hosts/count", params={"services": "vnc"}, headers=headers).json()
    dsl = client.get("/api/v1/agent/assist/hosts/count", params={"q": "service:vnc"}, headers=headers).json()
    assert count["count"] == dsl["count"] == listed["total"] == len(page_ips)
    stream = client.get("/api/v1/agent/assist/hosts.ndjson", params={"services": "vnc"}, headers=headers)
    assert {json.loads(line)["ip_address"] for line in stream.text.splitlines() if line} == page_ips

    # A name no port map knows is a real query with a true (empty) answer —
    # it used to drop the filter and count every host.
    assert client.get("/api/v1/agent/assist/hosts/count", params={"services": "rfb"},
                      headers=headers).json()["count"] == 0


def test_a_ports_value_that_is_not_a_port_is_refused_not_ignored(client, db_session, test_project):
    """ports=5900-5905 was skipped as non-numeric, which dropped the filter:
    "how many hosts have 5900-5905 open?" answered with every host."""
    _vnc_hosts(db_session, test_project)
    headers = _assist(client, test_project.id)
    for bad in ("5900-5905", "445/tcp", "vnc", "70000"):
        r = client.get("/api/v1/agent/assist/hosts/count", params={"ports": bad}, headers=headers)
        assert r.status_code == 422, (bad, r.text)
        assert "not understood" in r.text
    ok = client.get("/api/v1/agent/assist/hosts/count", params={"ports": "5900,5901"}, headers=headers)
    assert ok.json()["count"] == 2  # 10.70.0.1 and the masscan-only 10.70.0.2 (5901 is closed)


def test_a_host_page_carries_its_total_so_a_page_is_not_read_as_the_answer(client, db_session, test_project):
    """The list was a bare 500-row page; "how many?" was answered with its
    length.  It now says how many match and whether more remain."""
    _vnc_hosts(db_session, test_project)
    headers = _assist(client, test_project.id)
    first = client.get("/api/v1/agent/assist/hosts", params={"limit": 2}, headers=headers).json()
    assert (len(first["items"]), first["total"], first["has_more"]) == (2, 5, True)
    last = client.get("/api/v1/agent/assist/hosts", params={"limit": 2, "offset": 4}, headers=headers).json()
    assert (len(last["items"]), last["total"], last["has_more"], last["offset"]) == (1, 5, False, 4)


# ---------------------------------------------------------------------------
# Agent feedback #26 / #27 (acceptance run, 2026-10-02)
# ---------------------------------------------------------------------------

def test_scanner_rows_say_how_the_finding_stands_on_this_host(client, db_session, test_project):
    """#27 — ``assist_get_host_vulnerabilities`` rows carried no finding state,
    so an agent could not tell a row judged on this host from one whose issue
    has a finding on OTHER hosts only.  The rows now carry what the host
    inspector's rows carry, from the same rule."""
    from tests.test_finding_spine import _shared_issue

    hosts, vulns = _shared_issue(db_session, test_project)
    pid = test_project.id
    promoted = client.post(
        f"/api/v1/projects/{pid}/vulnerabilities/{vulns[0].id}/promote",
        json={"vuln_id": vulns[0].id, "scope": "host"},
    )
    assert promoted.status_code == 201, promoted.text
    finding_id = promoted.json()["id"]
    headers = _assist(client, pid)

    def agent_row(host, vuln):
        body = client.get(f"/api/v1/agent/assist/hosts/{host.id}/vulnerabilities", headers=headers).json()
        assert "findings" not in body, "scanner rows are items, never findings"
        return next(r for r in body["items"] if r["id"] == vuln.id)

    def page_row(host, vuln):
        body = client.get(f"/api/v1/projects/{pid}/hosts/{host.id}").json()
        return next(r for r in body["vulnerabilities"] if r["id"] == vuln.id)

    keys = ("finding_id", "finding_status", "finding_on_this_host", "finding_endpoint_status")
    here, elsewhere = agent_row(hosts[0], vulns[0]), agent_row(hosts[1], vulns[1])
    assert [here[k] for k in keys] == [finding_id, "confirmed", True, "open"]
    # The issue has a finding, but this host is not on it: unjudged HERE.
    assert [elsewhere[k] for k in keys] == [finding_id, "confirmed", False, None]
    for host, vuln, row in ((hosts[0], vulns[0], here), (hosts[1], vulns[1], elsewhere)):
        page = page_row(host, vuln)
        assert [row[k] for k in keys] == [page[k] for k in keys], "the agent and the inspector disagree"


def test_a_findings_endpoints_carry_the_id_a_proposal_needs(client, db_session, test_project):
    """#26 — ``propose_endpoint_status`` takes ``finding_host_id`` and no agent
    read returned it."""
    from app.db.models_findings import FindingHost
    from tests.test_finding_spine import _shared_issue

    _hosts, vulns = _shared_issue(db_session, test_project)
    pid = test_project.id
    finding_id = client.post(
        f"/api/v1/projects/{pid}/vulnerabilities/{vulns[0].id}/promote", json={"vuln_id": vulns[0].id},
    ).json()["id"]
    headers = _assist(client, pid)

    detail = client.get(f"/api/v1/agent/assist/findings/{finding_id}", headers=headers).json()
    stored = {
        fh.id: fh.host_id for fh in db_session.query(FindingHost).filter(FindingHost.finding_id == finding_id)
    }
    assert len(stored) > 1
    assert {h["finding_host_id"]: h["host_id"] for h in detail["hosts"]} == stored

    endpoint = detail["hosts"][0]
    proposed = client.post("/api/v1/agent/proposals/endpoint-status", headers=headers, json={
        "finding_id": finding_id, "finding_host_id": endpoint["finding_host_id"], "host_status": "retest",
        "rationale": "from the id the read returned",
    })
    assert proposed.status_code == 201, proposed.text


# ---------------------------------------------------------------------------
# Agent feedback #30 (acceptance run, session 81, 2026-10-02)
# ---------------------------------------------------------------------------

def test_an_unknown_filter_value_is_refused_not_answered_with_nothing(client, db_session, test_project):
    """``status=bogus`` (and the retired ``watching``) answered ``total: 0`` — an
    ordinary empty result for a value that means nothing."""
    host = models.Host(project_id=test_project.id, ip_address="10.71.0.1", state="up")
    db_session.add(host)
    db_session.commit()
    headers = _assist(client, test_project.id)

    for value in ("bogus", "watching"):
        r = client.get("/api/v1/agent/assist/findings", params={"status": value}, headers=headers)
        assert r.status_code == 422 and value in r.text and "confirmed" in r.text, r.text
    for value in ("all", "open", "CONFIRMED", "active", "resolved"):
        assert client.get(
            "/api/v1/agent/assist/findings", params={"status": value}, headers=headers,
        ).status_code == 200, value

    path = f"/api/v1/agent/assist/hosts/{host.id}/vulnerabilities"
    bad = client.get(path, params={"severity": "critical,bogus"}, headers=headers)
    assert bad.status_code == 422 and "bogus" in bad.text
    assert client.get(path, params={"severity": "critical,high"}, headers=headers).status_code == 200


def test_a_list_filtered_by_a_host_that_is_not_here_is_not_found(client, db_session, test_project):
    """``host_id`` of another project's host, or of none, answered 200 with no
    rows on three lists while the host reads answered 404."""
    from app.db.models_project import Project

    other = Project(name="elsewhere", slug="elsewhere-fb30")
    db_session.add(other)
    db_session.flush()
    foreign = models.Host(project_id=other.id, ip_address="10.71.0.2", state="up")
    mine = models.Host(project_id=test_project.id, ip_address="10.71.0.2", state="up")
    db_session.add_all([foreign, mine])
    db_session.commit()
    headers = _assist(client, test_project.id)

    for path in ("/api/v1/agent/assist/findings", "/api/v1/agent/host-tests", "/api/v1/agent/evidence"):
        for host_id in (foreign.id, 999_999):
            r = client.get(path, params={"host_id": host_id}, headers=headers)
            assert r.status_code == 404, (path, host_id, r.text)
        assert client.get(path, params={"host_id": mine.id}, headers=headers).status_code == 200, path
    # The page's own route is the same handler.
    page = client.get(f"/api/v1/projects/{test_project.id}/host-tests", params={"host_id": foreign.id})
    assert page.status_code == 404


def test_the_evidence_gap_tool_advertises_exactly_the_domains():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.services.evidence_service import EVIDENCE_DOMAINS

    advertised = TOOLS["assist_list_evidence_gaps"]["input_schema"]["properties"]["domain"]["enum"]
    assert advertised == [d["key"] for d in EVIDENCE_DOMAINS]


# ---------------------------------------------------------------------------
# Agent feedback #31 — the agent's numbers against the pages' (2026-10-02)
# ---------------------------------------------------------------------------

def test_expired_staged_uploads_are_not_failed_imports(client, db_session, test_project):
    """The agent said 48 imports failed where Ingestion Results said 16 failed
    and 32 expired: a staged upload nobody started is written ``failed``."""
    from app.services.staged_import_service import DISCARDED_MESSAGE, EXPIRED_MESSAGE_PREFIX

    def job(name, message):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename=name, original_filename=name,
            storage_path=f"/tmp/{name}", status="failed", error_message=message,
        ))

    job("broke.xml", "Parser error: not well-formed")
    job("late-1.xml", f"{EXPIRED_MESSAGE_PREFIX} not started within 24 hours")
    job("late-2.xml", f"{EXPIRED_MESSAGE_PREFIX} not started within 24 hours")
    job("dropped.xml", DISCARDED_MESSAGE)
    db_session.commit()

    agent = client.get("/api/v1/agent/assist/ingestion-issues", headers=_assist(client, test_project.id)).json()
    page = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results").json()
    summary = page.get("summary") or page
    assert (agent["failed"], agent["expired"], agent["discarded"]) == (1, 2, 1)
    assert (agent["failed"], agent["expired"], agent["discarded"]) == (
        summary["total_failed"], summary["total_expired"], summary["total_discarded"])
    assert agent["needs_attention"] == summary["total_needs_attention"]
    kinds = sorted(i["kind"] for i in agent["issues"])
    assert kinds == ["expired", "expired", "failed"], "a discarded upload is not an issue"


def test_posture_carries_the_scanner_rows_by_severity(client, db_session, test_project):
    from app.db.models_vulnerability import VulnerabilitySeverity

    scan = models.Scan(project_id=test_project.id, filename="n.nessus", tool_name="nessus")
    a = models.Host(project_id=test_project.id, ip_address="10.72.0.1", state="up")
    b = models.Host(project_id=test_project.id, ip_address="10.72.0.2", state="up")
    db_session.add_all([scan, a, b])
    db_session.flush()
    _vuln(db_session, a, scan, VulnerabilitySeverity.CRITICAL, True)
    _vuln(db_session, a, scan, VulnerabilitySeverity.CRITICAL, False)
    _vuln(db_session, b, scan, VulnerabilitySeverity.HIGH, False)
    _vuln(db_session, b, scan, VulnerabilitySeverity.INFO, False)
    db_session.commit()

    agent = client.get("/api/v1/agent/assist/posture", headers=_assist(client, test_project.id)).json()
    page = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats").json()["vulnerability_stats"]
    rows = agent["scanner_observations"]
    # The page's headline excludes informational rows; so does `total`, which
    # is also the posture headline's own count.
    assert page["total_vulnerabilities"] == 4 and rows["informational"] == 1 == page["info"]
    assert rows["total"] == 3 == agent["headline"]["detected_exposure"]["vuln_count"]
    assert (rows["by_severity"]["critical"], rows["by_severity"]["high"]) == (2, 1) == (page["critical"], page["high"])
    assert rows["hosts_by_severity"]["critical"] == 1 == page["hosts_by_severity"]["critical"]


def test_context_names_the_view_the_hosts_page_opens_on(client, db_session, test_project, test_user):
    headers = _assist(client, test_project.id)
    assert client.get("/api/v1/agent/assist/context", headers=headers).json()["default_host_view"] is None
    db_session.add(models.HostFilterView(
        user_id=test_user.id, project_id=test_project.id, name="FTP",
        filter_json={"filters": {"ports": ["21"]}}, is_project_default=True,
    ))
    db_session.commit()
    view = client.get("/api/v1/agent/assist/context", headers=headers).json()["default_host_view"]
    assert view == {"name": "FTP", "filters": {"ports": ["21"]}}


# ---------------------------------------------------------------------------
# Prod feedback, diagnostics bundle 2026-10-07
# ---------------------------------------------------------------------------

def test_a_hosts_scanner_rows_narrow_to_one_issue(client, db_session, test_project):
    """An agent after one plugin's evidence on a host downloaded the host's
    whole page (161 rows) to find it.  ``cve``, ``plugin_id`` and ``search``
    narrow the rows, and ``total`` is the narrowed count."""
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity as Sev, VulnerabilitySource

    scan = models.Scan(project_id=test_project.id, filename="n.nessus", tool_name="nessus")
    host = models.Host(project_id=test_project.id, ip_address="10.73.0.1", state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    for plugin, cve, title in (
        ("100", "CVE-2021-44228", "Apache Log4j RCE"),
        ("200", None, "Apache httpd 100% outdated"),
        ("300", "CVE-2020-0796", "SMBGhost"),
    ):
        db_session.add(Vulnerability(
            host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
            severity=Sev.HIGH, title=title, plugin_id=plugin, cve_id=cve,
        ))
    db_session.commit()
    headers = _assist(client, test_project.id)
    url = f"/api/v1/agent/assist/hosts/{host.id}/vulnerabilities"

    def titles(**params):
        body = client.get(url, params=params, headers=headers).json()
        assert body["total"] == len(body["items"])
        return sorted(r["title"] for r in body["items"])

    assert len(titles()) == 3
    assert titles(cve="cve-2021-44228") == ["Apache Log4j RCE"]
    assert titles(plugin_id="300") == ["SMBGhost"]
    assert titles(search="apache") == ["Apache Log4j RCE", "Apache httpd 100% outdated"]
    # The text is matched literally: % is not a wildcard.
    assert titles(search="100%") == ["Apache httpd 100% outdated"]
    assert titles(search="apache", plugin_id="100") == ["Apache Log4j RCE"]
    assert titles(cve="CVE-1999-0001") == []


def test_the_scope_list_counts_the_subnets_it_lists(client, db_session, test_project):
    """``GET /agent/scopes`` answered ``subnet_total: 0`` beside 447 subnets:
    the route lists every subnet and never set the total."""
    scope = models.Scope(project_id=test_project.id, name="Internal")
    db_session.add(scope)
    db_session.flush()
    db_session.add_all([models.Subnet(scope_id=scope.id, cidr=f"10.74.{i}.0/24") for i in range(3)])
    db_session.commit()
    (row,) = client.get("/api/v1/agent/scopes", headers=_assist(client, test_project.id)).json()
    assert (len(row["subnets"]), row["subnet_total"], row["subnets_truncated"]) == (3, 3, False)


def test_a_findings_scanner_rows_are_looked_up_by_id(client, db_session, test_project):
    """The finding detail found its scanner rows with ``id IN (linked) OR id =
    <vuln_id>``, which Postgres answers by scanning every scanner row (300 ms
    of the route at 524k rows).  Both reads now name one id set."""
    from tests.test_finding_spine import _shared_issue

    _hosts, vulns = _shared_issue(db_session, test_project)
    pid = test_project.id
    finding_id = client.post(
        f"/api/v1/projects/{pid}/vulnerabilities/{vulns[0].id}/promote", json={"vuln_id": vulns[0].id},
    ).json()["id"]
    headers = _assist(client, pid)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    from tests.conftest import engine

    event.listen(engine, "before_cursor_execute", record)
    try:
        detail = client.get(f"/api/v1/agent/assist/findings/{finding_id}", headers=headers).json()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    from app.db.models_findings import Finding, FindingVulnerability

    # A row linked through finding_vulnerabilities and the promoted row itself
    # (here a second, unlinked row is made the finding's own to cover the union).
    linked = {r.vuln_id for r in db_session.query(FindingVulnerability).filter_by(finding_id=finding_id)}
    assert linked
    spare = next(v.id for v in vulns if v.id not in linked) if len(linked) < len(vulns) else None
    if spare is not None:
        db_session.get(Finding, finding_id).vuln_id = spare
        db_session.commit()
        statements.clear()
        event.listen(engine, "before_cursor_execute", record)
        try:
            detail = client.get(f"/api/v1/agent/assist/findings/{finding_id}", headers=headers).json()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        linked.add(spare)
    assert detail["scanner_evidence_total"] == len(linked)
    assert {r["vuln_id"] for r in detail["scanner_evidence"]} == linked
    over_rows = [s for s in statements if "FROM vulnerabilities" in s and "finding_vulnerabilities" in s]
    assert over_rows and all(" OR vulnerabilities.id = " not in s for s in over_rows)
