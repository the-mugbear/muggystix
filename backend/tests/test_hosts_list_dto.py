"""RV-8 — the Hosts LIST returns a row-weight payload, not drill-down graphs.

The list must omit NSE script bodies (port scripts + host_scripts) and cap
discoveries, while the detail endpoint keeps the full payload.  ``client``
authenticates as a global admin.
"""
from __future__ import annotations

from app.db import models


def test_list_windows_notes_and_discoveries(client, db_session, test_project, test_user):
    """Review #5 — the list returns the 3 newest notes (with a full
    note_count) and ≤6 distinct-scan discoveries, without loading every
    child row."""
    host = models.Host(project_id=test_project.id, ip_address="10.4.5.1", state="up")
    db_session.add(host)
    db_session.flush()
    # 5 notes — only the 3 newest should come back, but note_count == 5.
    for i in range(5):
        db_session.add(models.HostNote(
            host_id=host.id, user_id=test_user.id, body=f"note {i}",
            status=models.NoteStatus.OPEN,
        ))
    # 9 distinct scans / history rows — discoveries cap at 6.
    for i in range(9):
        s = models.Scan(project_id=test_project.id, filename=f"d{i}.xml")
        db_session.add(s)
        db_session.flush()
        db_session.add(models.HostScanHistory(host_id=host.id, scan_id=s.id))
    db_session.flush()

    item = next(
        h for h in client.get(f"/api/v1/projects/{test_project.id}/hosts/").json()["items"]
        if h["id"] == host.id
    )
    assert item["note_count"] == 5
    assert len(item["notes"]) == 3
    assert len(item["discoveries"]) == 6
    assert len({d["scan_id"] for d in item["discoveries"]}) == 6  # distinct scans


def _seed_host_with_scripts(db_session, project_id, ip="10.4.0.1"):
    scan = models.Scan(project_id=project_id, filename="s.xml")
    host = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    port = models.Port(host_id=host.id, port_number=22, protocol="tcp", state="open",
                       service_name="ssh")
    db_session.add(port)
    db_session.flush()
    db_session.add_all([
        models.Script(port_id=port.id, script_id="ssh-hostkey",
                      output="X" * 5000, scan_id=scan.id),
        models.HostScript(host_id=host.id, script_id="smb-os-discovery",
                          output="Y" * 5000, scan_id=scan.id),
    ])
    # Several scan-history rows → several discoveries (cap is 6).
    for i in range(9):
        s = models.Scan(project_id=project_id, filename=f"s{i}.xml")
        db_session.add(s)
        db_session.flush()
        db_session.add(models.HostScanHistory(host_id=host.id, scan_id=s.id))
    db_session.flush()
    return host


def test_list_omits_script_bodies_and_caps_discoveries(client, db_session, test_project):
    host = _seed_host_with_scripts(db_session, test_project.id)

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/")
    assert r.status_code == 200, r.text
    item = next(h for h in r.json()["items"] if h["id"] == host.id)

    # NSE bodies are absent from the list payload.
    assert item["host_scripts"] == []
    assert item["ports"], "port should still be present"
    assert all(p["scripts"] == [] for p in item["ports"])
    # Port service columns are still there (the row renders them).
    assert item["ports"][0]["service_name"] == "ssh"
    # Discoveries capped.
    assert len(item["discoveries"]) <= 6


def test_detail_still_includes_script_bodies(client, db_session, test_project):
    host = _seed_host_with_scripts(db_session, test_project.id, ip="10.4.0.2")

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    # Detail keeps the full payload.
    assert any(p.get("scripts") for p in body["ports"])
    assert body["host_scripts"]
