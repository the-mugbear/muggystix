"""The agent surface for a parse audit (v2.418.0).

An agent on a network whose files cannot leave it checks BlueStick's parse by
comparing the tool's line with what was read from it (`assist_list_host_access`)
and starts from the lines the parser did not interpret, as redacted shapes
(`assist_list_uninterpreted_lines`).
"""
from app.db import models
from app.db.models_confidence import NETEXEC_RAW_OUTPUT_LIMIT, NetexecResult


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return r.json()["api_key"]


def _mcp(client, key, name, **arguments):
    r = client.post("/api/v1/mcp", headers={"X-API-Key": key}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _host_with_access(db, project):
    host = models.Host(project_id=project.id, ip_address="10.93.0.4", state="up")
    db.add(host)
    db.flush()
    scan = models.Scan(filename="nxc.txt", tool_name="netexec", project_id=project.id)
    db.add(scan)
    db.flush()
    db.add_all([
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="rdp", port=3389,
                      raw_output="RDP 10.93.0.4 3389 WS01 [*] Windows 10 (name:WS01) (nla:False)"),
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", port=445, local_admin=True,
                      auth_success=True, username="svc", raw_output="L" * NETEXEC_RAW_OUTPUT_LIMIT),
    ])
    db.commit()
    return host


def test_access_results_are_an_mcp_tool_with_the_line(client, db_session, test_project):
    host = _host_with_access(db_session, test_project)
    key = _start(client, test_project)
    data = _mcp(client, key, "assist_list_host_access", host_id=host.id)["structuredContent"]
    assert data["total"] == 2 and data["has_more"] is False
    rdp = next(i for i in data["items"] if i["protocol"] == "rdp")
    assert "(nla:False)" in rdp["raw_output"] and rdp["auth_success"] is None
    smb = next(i for i in data["items"] if i["protocol"] == "smb")
    assert smb["local_admin"] is True and smb["raw_output_truncated"] is True
    # Project-scoped like every assist read.
    assert _mcp(client, key, "assist_list_host_access", host_id=host.id + 100000)["isError"] is True


def test_scanner_observations_name_their_catalog_check(client, db_session, test_project):
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
    host = _host_with_access(db_session, test_project)
    db_session.add(Vulnerability(
        host_id=host.id, scan_id=db_session.query(models.Scan).first().id, source=VulnerabilitySource.NETEXEC,
        severity=VulnerabilitySeverity.HIGH, title="VNC server does not require authentication",
        check_id="vnc_no_auth",
    ))
    db_session.commit()
    key = _start(client, test_project)
    data = _mcp(client, key, "assist_get_host_vulnerabilities", host_id=host.id)["structuredContent"]
    assert [f["check_id"] for f in data["findings"]] == ["vnc_no_auth"]


def test_uninterpreted_lines_are_listed_per_import(client, db_session, test_project):
    receipt = {"total": 2, "distinct": 1, "shapes": [
        {"kind": "text_only", "shape": "RDP <IP> 3389 <HOST> [*] (nla:False)", "count": 2}]}
    for name, lines in (("a.txt", receipt), ("b.txt", None)):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename=name, original_filename=name, storage_path="/x",
            status="completed", tool_name="netexec", final_file_type="netexec_txt", uninterpreted_lines=lines,
        ))
    db_session.commit()
    key = _start(client, test_project)
    data = _mcp(client, key, "assist_list_uninterpreted_lines")["structuredContent"]
    assert data["total"] == 1
    (item,) = data["items"]
    assert item["filename"] == "a.txt" and item["format"] == "netexec_txt"
    assert item["shapes"][0]["shape"] == "RDP <IP> 3389 <HOST> [*] (nla:False)"
    one = _mcp(client, key, "assist_list_uninterpreted_lines", job_id=item["job_id"])["structuredContent"]
    assert one["total"] == 1
