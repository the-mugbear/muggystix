"""Defects from the MCP acceptance run of 2026-09-30 (v2.434.1).

All three were a second copy of something the codebase already computes in
one place, drifting from it:

* H1 — host detail left ``exploitable_count`` / ``critical_exploitable_count``
  at their default 0 while the list filled them (``exploit_count_maps``);
* H3 — the shared note serializer dropped ``actor_type``, so the dossier
  export (and every other ``_serialize_note`` caller) called an agent's note
  human-written;
* H4 — the scan list returned start/end without their basis, so a UTC instant
  and a scanner's zone-less wall clock looked identical (``scan_time_for_api``).

H2 (MCP enums) is ``test_mcp_enum_contract.py``.
"""
import json
from datetime import datetime

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _key(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _host_with_exploitable_critical(db, project):
    scan = models.Scan(project_id=project.id, filename="n.xml", tool_name="nessus")
    host = models.Host(project_id=project.id, ip_address="10.61.0.1", state="up")
    db.add_all([scan, host])
    db.flush()
    db.add(Vulnerability(
        title="crit", severity=VulnerabilitySeverity.CRITICAL, source=VulnerabilitySource.MANUAL,
        host_id=host.id, scan_id=scan.id, exploitable=True,
    ))
    db.commit()
    return host


def test_host_detail_counts_exploits_as_the_list_does(client, db_session, test_project):
    host = _host_with_exploitable_critical(db_session, test_project)
    hdr = _key(client, test_project)

    rows = client.get("/api/v1/agent/assist/hosts", headers=hdr,
                      params={"q": "has:critical_exploit"}).json()
    rows = rows["items"]  # {items, total, has_more} since v2.440.0
    row = next(r for r in rows if r["id"] == host.id)
    detail = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=hdr).json()

    assert (row["exploitable_count"], row["critical_exploitable_count"]) == (1, 1)
    assert (detail["exploitable_count"], detail["critical_exploitable_count"]) == (1, 1)


def test_an_agent_note_stays_agent_written_in_the_export(client, db_session, test_project):
    from app.services.host_serialization import _serialize_note

    host = models.Host(project_id=test_project.id, ip_address="10.61.0.2", state="up")
    db_session.add(host)
    db_session.commit()
    hdr = _key(client, test_project)
    r = client.post(f"/api/v1/agent/hosts/{host.id}/notes", headers=hdr,
                    json={"body": "agent-written observation"})
    assert r.status_code in (200, 201), r.text
    note_id = r.json()["id"]

    db_session.expire_all()
    note = db_session.get(models.Annotation, note_id)
    assert note.actor_type == "agent"
    # The shared serializer (host page, reports, dossier)…
    assert _serialize_note(note).actor_type == "agent"
    # …and the dossier export an agent or report reads.
    lines = client.get("/api/v1/agent/assist/report-context.ndjson", headers=hdr).text
    record = next(json.loads(line) for line in lines.splitlines()
                  if line and json.loads(line)["host_id"] == host.id)
    assert '"actor_type": "agent"' in json.dumps(record)
    assert '"actor_type": "user"' not in json.dumps(record)


def test_the_scan_list_says_whether_a_time_is_an_instant(client, db_session, test_project):
    at = datetime(2024, 4, 1, 0, 0, 0)
    instant = models.Scan(project_id=test_project.id, filename="xml", tool_name="nmap",
                          start_time=at, time_source="tool_run")
    wall_clock = models.Scan(project_id=test_project.id, filename="gnmap", tool_name="nmap",
                             start_time=at, time_source="tool_clock")
    db_session.add_all([instant, wall_clock])
    db_session.commit()
    hdr = _key(client, test_project)

    for path in ("/api/v1/agent/assist/scans", "/api/v1/agent/scans"):
        rows = {r["id"]: r for r in client.get(path, headers=hdr).json()}
        assert rows[instant.id]["time_source"] == "tool_run"
        assert rows[instant.id]["start_time"].endswith(("Z", "+00:00")), path
        assert rows[wall_clock.id]["time_source"] == "tool_clock"
        assert rows[wall_clock.id]["start_time"] == "2024-04-01T00:00:00", path


def test_the_scan_list_pages_past_its_limit(client, db_session, test_project):
    """R4: there was no way past the newest 500. offset pages back; scans that
    share an import time are neither repeated nor skipped across pages."""
    scans = [models.Scan(project_id=test_project.id, filename=f"s{i}.xml", tool_name="nmap")
             for i in range(5)]
    db_session.add_all(scans)
    db_session.commit()
    hdr = _key(client, test_project)

    seen = []
    for offset in (0, 2, 4):
        page = client.get("/api/v1/agent/assist/scans", headers=hdr,
                          params={"limit": 2, "offset": offset}).json()
        seen += [r["id"] for r in page]
    assert sorted(seen) == sorted(s.id for s in scans)
    assert len(seen) == len(set(seen))
