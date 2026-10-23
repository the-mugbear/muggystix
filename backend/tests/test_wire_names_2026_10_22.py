"""Three legacy wire names renamed with NO alias (owner decision, 2026-10-22).

* The Operations page's data: ``/projects/{id}/workbench[/…]`` →
  ``/projects/{id}/operations[/…]``; the agents' mirror
  ``/agent/assist/workbench[/…]`` → ``/agent/assist/operations[/…]``; the MCP
  tool ``assist_get_workbench`` → ``assist_get_operations``.
* The Ingestion results view: ``/projects/{id}/parse-errors/ingestion-results[/…]``
  → ``/projects/{id}/ingestion-results[/…]``.  The parse-error routes keep
  ``/parse-errors``; their ``{error_id}`` is an integer, so the old view's
  path is a 404 there too, not a 422.
* ``GET /references/mcp-tools``: each tool's ``workflows`` key → ``groups``.

The old paths simply 404 on both doors, as the scope-id paths did in 2.489.0
("there are no old MCP clients to support").  Each test here fails on the
tree before the rename.
"""
from __future__ import annotations

import pytest

from app.db import models


def _start(client, project_id):
    resp = client.post(f"/api/v1/projects/{project_id}/assist/start", json={"purpose": "wire names"})
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["api_key"]}


def _job(db, project_id):
    job = models.IngestionJob(
        project_id=project_id, filename="scan.xml", original_filename="scan.xml",
        storage_path="/tmp/scan.xml", status="completed",
    )
    db.add(job)
    db.commit()
    return job


# (method, suffix under /projects/{id}, json body)
OPERATIONS_ROUTES = [
    ("GET", "", None),
    ("GET", "/findings", None),
    ("GET", "/hosts", None),
    ("GET", "/tests", None),
    ("GET", "/followups", None),
    ("GET", "/investigate", None),
    ("GET", "/terrain", None),
    ("POST", "/seen", {}),
    ("POST", "/followups/still-reviewed", {"host_ids": [1]}),
]


@pytest.mark.parametrize("method,suffix,body", OPERATIONS_ROUTES)
def test_the_peoples_operations_routes_moved_and_the_old_path_is_gone(
    client, test_project, method, suffix, body,
):
    base = f"/api/v1/projects/{test_project.id}"
    old = client.request(method, f"{base}/workbench{suffix}", json=body)
    assert old.status_code == 404, (suffix, old.status_code, old.text[:200])
    new = client.request(method, f"{base}/operations{suffix}", json=body)
    # still-reviewed on a host with no review of the caller's is its own 409;
    # everything else answers.
    assert new.status_code in (200, 409), (suffix, new.status_code, new.text[:200])


@pytest.mark.parametrize("suffix", ["", "/investigate", "/findings", "/terrain"])
def test_the_agents_operations_reads_moved_and_the_old_path_is_gone(client, test_project, suffix):
    headers = _start(client, test_project.id)
    old = client.get(f"/api/v1/agent/assist/workbench{suffix}", headers=headers)
    assert old.status_code == 404, (suffix, old.status_code, old.text[:200])
    new = client.get(f"/api/v1/agent/assist/operations{suffix}", headers=headers)
    assert new.status_code == 200, (suffix, new.status_code, new.text[:200])


def test_the_ingestion_results_view_moved_and_the_old_path_is_gone(client, db_session, test_project):
    base = f"/api/v1/projects/{test_project.id}"
    job = _job(db_session, test_project.id)

    assert client.get(f"{base}/parse-errors/ingestion-results").status_code == 404
    assert client.get(f"{base}/parse-errors/ingestion-results/{job.id}/uninterpreted").status_code == 404

    listed = client.get(f"{base}/ingestion-results")
    assert listed.status_code == 200, listed.text
    assert job.id in {row["id"] for row in listed.json()["items"]}
    shapes = client.get(f"{base}/ingestion-results/{job.id}/uninterpreted")
    assert shapes.status_code == 200, shapes.text
    assert shapes.json()["job_id"] == job.id

    # The parse-error routes stay where they were.
    assert client.get(f"{base}/parse-errors/").status_code == 200
    assert client.get(f"{base}/parse-errors/stats/summary").status_code == 200
    assert client.get(f"{base}/parse-errors/999999").status_code == 404


def test_no_route_keeps_an_old_name():
    from app.main import app

    paths = list(app.openapi()["paths"])
    assert not [p for p in paths if "workbench" in p]
    assert not [p for p in paths if "parse-errors/ingestion-results" in p]
    assert "/api/v1/projects/{project_id}/operations" in paths
    assert "/api/v1/projects/{project_id}/ingestion-results" in paths
    assert "/api/v1/agent/assist/operations" in paths


def test_the_mcp_tool_is_assist_get_operations_and_the_old_name_is_unknown(client, test_project):
    listed = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert "assist_get_operations" in names
    assert "assist_get_workbench" not in names
    assert not [n for n in names if "workbench" in n]

    headers = _start(client, test_project.id)

    def call(name):
        return client.post("/api/v1/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        }, headers=headers).json()

    old = call("assist_get_workbench")
    assert old["error"]["code"] == -32602, old
    new = call("assist_get_operations")
    assert new["result"]["isError"] is False, new
    assert "since_last_visit" in new["result"]["structuredContent"]


def test_the_tool_catalog_files_each_tool_under_groups(client):
    tools = client.get("/api/v1/references/mcp-tools").json()["tools"]
    assert tools
    for tool in tools:
        assert "workflows" not in tool, tool["name"]
        assert isinstance(tool["groups"], list) and tool["groups"], tool["name"]
    paths = {t["name"]: t["path"] for t in tools}
    assert paths["assist_get_operations"] == "/api/v1/agent/assist/operations"
    assert paths["assist_list_worth_a_look"] == "/api/v1/agent/assist/operations/investigate"
    assert paths["assist_list_my_findings"] == "/api/v1/agent/assist/operations/findings"
    assert paths["assist_get_terrain"] == "/api/v1/agent/assist/operations/terrain"
