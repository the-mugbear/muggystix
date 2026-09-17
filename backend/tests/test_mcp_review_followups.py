"""External MCP review, second pass (2026-09-17) — two follow-ups on 2.343.2.

1. The recon download manifest named no run, so with two runs open the links
   answered 400 ``ambiguous_recon_run``, and once the selected run was
   completed while another stayed active the same link served the OTHER
   run's data.  Every URL and curl now carries ``?recon_session_id=``.
2. Host detail disclosed its web-interface cap but nothing could page past
   it.  ``GET /assist/hosts/{id}/web-interfaces`` (MCP
   ``assist_list_host_web_interfaces``) is the continuation.
"""
from app.db.models import Host, Scan, Scope, Subnet, WebInterface


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return r.json()["api_key"]


def _hdr(key):
    return {"X-API-Key": key}


def _mcp(client, key, name, **arguments):
    r = client.post("/api/v1/mcp", headers=_hdr(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert r.status_code == 200, r.text
    return r.json()


def _open_run(client, key, db, project, n):
    scope = Scope(name=f"fu-{n}", project_id=project.id)
    db.add(scope)
    db.flush()
    db.add(Subnet(scope_id=scope.id, cidr=f"10.97.{n}.0/24"))
    db.commit()
    r = client.post("/api/v1/agent/recon/start", headers=_hdr(key), json={"scope_id": scope.id})
    assert r.status_code == 201, r.text
    return r.json()["recon_session_id"]


# --- 1. download links name their run -------------------------------------

def test_download_manifest_names_the_run(client, db_session, test_project):
    key = _start(client, test_project)
    run = _open_run(client, key, db_session, test_project, 1)
    r = client.get("/api/v1/agent/recon/summary", headers=_hdr(key))
    assert r.status_code == 200, r.text
    dl = r.json()["downloads"]
    for name in ("hosts_ndjson", "live_hosts", "web_targets"):
        assert f"recon_session_id={run}" in dl[name]["url"], dl[name]
        assert f"recon_session_id={run}" in dl[name]["curl"], dl[name]


def test_download_links_work_with_two_runs_open_and_after_one_completes(client, db_session, test_project):
    key = _start(client, test_project)
    run_a = _open_run(client, key, db_session, test_project, 1)
    run_b = _open_run(client, key, db_session, test_project, 2)

    # Bare links are ambiguous with two runs open — that was the reported 400.
    r = client.get("/api/v1/agent/recon/live-hosts.txt", headers=_hdr(key))
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "ambiguous_recon_run"

    # The manifest's links, as handed out, resolve each run.
    summary_a = client.get(
        "/api/v1/agent/recon/summary", headers=_hdr(key), params={"recon_session_id": run_a},
    ).json()
    for name in ("hosts_ndjson", "live_hosts", "web_targets"):
        r = client.get(summary_a["downloads"][name]["url"], headers=_hdr(key))
        assert r.status_code == 200, (name, r.text)

    # Complete run A while B stays active: A's links still mean A (the bare
    # link would now silently resolve to B, the second reported consequence).
    r = client.post(
        "/api/v1/agent/recon/complete", headers=_hdr(key),
        params={"recon_session_id": run_a}, json={"notes": "done"},
    )
    assert r.status_code == 200, r.text
    r = client.get(summary_a["downloads"]["hosts_ndjson"]["url"], headers=_hdr(key))
    assert r.status_code == 200, r.text
    # And the bare link now points at B, which is exactly why the manifest
    # must never hand out a bare link.
    r = client.get("/api/v1/agent/recon/summary", headers=_hdr(key))
    assert r.json()["recon_session_id"] == run_b


# --- 2. web interfaces page past the cap -----------------------------------

def _host_with_interfaces(db, project, count):
    host = Host(project_id=project.id, ip_address="10.97.9.9", state="up")
    scan = Scan(project_id=project.id, filename="w.json", scan_type="httpx", tool_name="httpx")
    db.add_all([host, scan])
    db.flush()
    for i in range(count):
        db.add(WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=project.id, source="httpx",
            port=8000 + i, url=f"http://10.97.9.9:{8000 + i}/",
            screenshot_path="shots/x.png" if i == count - 1 else None,
        ))
    db.commit()
    return host


def test_web_interfaces_page_reaches_what_host_detail_capped(client, db_session, test_project):
    from app.api.v1.endpoints.agent_assist import _WEB_INTERFACE_CAP
    total = _WEB_INTERFACE_CAP + 3
    host = _host_with_interfaces(db_session, test_project, total)
    key = _start(client, test_project)

    detail = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=_hdr(key)).json()
    assert detail["web_interfaces_truncated"] is True

    seen = []
    offset = 0
    while True:
        r = client.get(
            f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=_hdr(key),
            params={"limit": _WEB_INTERFACE_CAP, "offset": offset},
        )
        assert r.status_code == 200, r.text
        page = r.json()
        assert page["total"] == total
        seen.extend(w["url"] for w in page["items"])
        if not page["has_more"]:
            break
        offset += len(page["items"])
    assert len(seen) == total and len(set(seen)) == total
    # The screenshotted interface sorts first and carries its download path.
    first = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=_hdr(key), params={"limit": 1},
    ).json()["items"][0]
    assert first["screenshot_download_path"].endswith("/screenshot")


def test_web_interfaces_page_is_an_mcp_tool(client, db_session, test_project):
    host = _host_with_interfaces(db_session, test_project, 3)
    key = _start(client, test_project)
    listed = client.post("/api/v1/mcp", headers=_hdr(key), json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert "assist_list_host_web_interfaces" in names
    out = _mcp(client, key, "assist_list_host_web_interfaces", host_id=host.id, limit=2)
    data = out["result"]["structuredContent"]
    assert data["total"] == 3 and data["has_more"] is True and len(data["items"]) == 2
    # Project-scoped like every assist read.
    other = _mcp(client, key, "assist_list_host_web_interfaces", host_id=host.id + 100000)
    assert other["result"]["isError"] is True
