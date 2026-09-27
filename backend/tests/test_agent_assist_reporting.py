"""v2.428.0 — the agent reads of the Findings hub's other two pages.

An agent could list and read findings but not answer "what did the scanners
find that nobody has judged yet?" or "what did we tell the client?", and it
could only be handed a path to an image, never shown one.  These pin:

* scanner observations — the agent gets exactly what the page's own route
  answers (same service, same numbers);
* client reports — the Reports page's floor (auditor), drafts read live and
  issued reports read as frozen, the report's own text per finding, files;
* ``assist_get_image`` — an image content block over MCP, refused above the
  inline cap, and never more than the download route it wraps would give.
"""
from __future__ import annotations

import base64

import pytest

from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment, NoteStatus
from app.db.models_reports import Report, ReportFile
from tests.test_agent_role_route_matrix import _key_for, _member
# The Reports page tests' template folder (autouse) and seeding helpers.
from tests.test_client_reports import _create, _finding, _issue, template_dir  # noqa: F401
from tests.test_scanner_observations import estate  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _assist_headers(client, project_id):
    resp = client.post(f"/api/v1/projects/{project_id}/assist/start", json={"purpose": "reporting reads"})
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["api_key"]}


def _mcp(client, headers, name, **arguments):
    resp = client.post("/api/v1/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _role_headers(db_session, project, role):
    return {"X-API-Key": _key_for(db_session, project, _member(db_session, project, role))}


# ---------------------------------------------------------------------------
# Scanner observations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    {}, {"min_hosts": 2}, {"severity": "low"}, {"search": "CVE-2023"},
    {"include_judged": True}, {"kind": "vulnerability"}, {"limit": 1, "skip": 1},
    {"sort": "hosts"},
])
def test_observations_are_the_pages_own_numbers(client, test_project, estate, params):
    page = client.get(f"/api/v1/projects/{test_project.id}/scanner-observations", params=params)
    agent = client.get(
        "/api/v1/agent/assist/scanner-observations", params=params,
        headers=_assist_headers(client, test_project.id),
    )
    assert page.status_code == agent.status_code == 200, agent.text
    assert agent.json() == page.json()


def test_sort_by_hosts_puts_the_most_widespread_first(client, test_project, estate):
    """MCP acceptance feedback #13: ranking by spread took four pages and a
    local sort."""
    rows = client.get(
        "/api/v1/agent/assist/scanner-observations", params={"sort": "hosts", "include_judged": True},
        headers=_assist_headers(client, test_project.id),
    ).json()["items"]
    spread = [r["host_count"] - r["judged_host_count"] for r in rows]
    assert spread == sorted(spread, reverse=True)


def test_observation_hosts_are_the_pages_own_rows(client, test_project, estate):
    key = "title:smb signing not required"
    page = client.get(f"/api/v1/projects/{test_project.id}/scanner-observations/hosts",
                      params={"issue_key": key})
    headers = _assist_headers(client, test_project.id)
    agent = client.get("/api/v1/agent/assist/scanner-observations/hosts",
                       params={"issue_key": key}, headers=headers)
    assert agent.json() == page.json()
    assert [h["ip_address"] for h in agent.json()] == ["10.9.0.1", "10.9.0.2", "10.9.0.3"]

    # And over MCP, as structured content.
    result = _mcp(client, headers, "assist_list_observation_hosts", issue_key=key)
    assert result["isError"] is False
    assert result["structuredContent"]["items"] == page.json()


def test_observations_are_a_viewer_read(client, db_session, test_project, estate):
    """The Findings page is a viewer page; so is its agent twin."""
    resp = client.get("/api/v1/agent/assist/scanner-observations",
                      headers=_role_headers(db_session, test_project, "viewer"))
    assert resp.status_code == 200 and resp.json()["total"] == 3


# ---------------------------------------------------------------------------
# Client reports
# ---------------------------------------------------------------------------

def _host(db_session, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db_session.add(host)
    db_session.flush()
    return host


@pytest.mark.parametrize("role,allowed", [("viewer", False), ("auditor", True), ("analyst", True)])
def test_client_reports_take_the_reports_pages_floor(client, db_session, test_project, role, allowed):
    report = _create(client, test_project)
    headers = _role_headers(db_session, test_project, role)
    for path in ("/api/v1/agent/assist/client-reports",
                 f"/api/v1/agent/assist/client-reports/{report['id']}"):
        resp = client.get(path, headers=headers)
        if allowed:
            assert resp.status_code == 200, resp.text
        else:
            assert resp.status_code == 403 and "requires auditor" in resp.text, resp.text
    # The JWT page answers the viewer the same way.
    assert _mcp(client, headers, "assist_list_client_reports")["isError"] is (not allowed)


def test_a_draft_reads_live_and_an_issued_report_reads_as_issued(client, db_session, test_project):
    host = _host(db_session, test_project, "10.60.0.1")
    f = _finding(db_session, test_project, "SQL injection", "critical", hosts=[host],
                 description="Before", impact="Data loss", recommendation="Parameterise")
    headers = _assist_headers(client, test_project.id)

    draft = _create(client, test_project)
    body = client.get(f"/api/v1/agent/assist/client-reports/{draft['id']}", headers=headers).json()
    assert body["status"] == "draft" and body["content_source"] == "draft_live"
    (item,) = body["findings"]
    assert (item["ref"], item["title"], item["description"], item["recommendation"]) == (
        "F-01", "SQL injection", "Before", "Parameterise")
    assert [e["address"] for e in item["affected"]] == ["10.60.0.1"]
    assert body["counts"]["critical"] == 1
    # The page's own summary, not a second computation.
    page = client.get(f"/api/v1/projects/{test_project.id}/client-reports/{draft['id']}").json()
    assert body["summary"] == page["summary"]

    _issue(client, test_project, draft["id"])
    f.description = "After"
    db_session.commit()
    issued = client.get(f"/api/v1/agent/assist/client-reports/{draft['id']}", headers=headers).json()
    assert issued["status"] == "issued" and issued["number"] == 1
    assert issued["content_source"] == "issued_snapshot"
    # What the client was told, not the finding's text today.
    assert issued["findings"][0]["description"] == "Before"

    listing = client.get("/api/v1/agent/assist/client-reports", headers=headers).json()
    assert listing["latest_issued_id"] == draft["id"]
    assert [r["id"] for r in listing["items"]] == [draft["id"]]


def test_report_evidence_is_an_attachment_id_and_files_download(client, db_session, test_project, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    host = _host(db_session, test_project, "10.60.0.2")
    f = _finding(db_session, test_project, "Default creds", "high", hosts=[host])
    note = Annotation(project_id=test_project.id, finding_id=f.id, body="proof", status=NoteStatus.OPEN)
    db_session.add(note)
    db_session.flush()
    att = NoteAttachment(annotation_id=note.id, project_id=test_project.id, filename="login.png",
                         content_type="image/png", size_bytes=len(PNG), storage_path=f"{note.id}/login.png",
                         include_in_report=True)
    db_session.add(att)
    db_session.commit()

    report = _create(client, test_project)
    stored = tmp_path / "client_reports" / "r.html"
    stored.parent.mkdir(parents=True)
    stored.write_text("<h1>Report</h1>")
    db_session.add(ReportFile(report_id=report["id"], format="html", filename="report.html",
                              media_type="text/html", size_bytes=15, sha256="0" * 64, storage_path="r.html"))
    db_session.commit()

    headers = _assist_headers(client, test_project.id)
    body = client.get(f"/api/v1/agent/assist/client-reports/{report['id']}", headers=headers).json()
    assert body["findings"][0]["evidence"] == [{"attachment_id": att.id, "caption": "login.png"}]
    (file,) = body["files"]
    assert file["download_path"] == f"/api/v1/agent/assist/client-reports/{report['id']}/files/html"
    download = client.get(file["download_path"], headers=headers)
    assert download.status_code == 200 and download.text == "<h1>Report</h1>"
    assert client.get(f"/api/v1/agent/assist/client-reports/{report['id']}/files/docx",
                      headers=headers).status_code == 404


def test_another_projects_report_does_not_exist(client, db_session, test_project):
    from app.db.models_project import Project
    other = Project(name="Elsewhere", slug="elsewhere-reports")
    db_session.add(other)
    db_session.commit()
    foreign = Report(project_id=other.id, kind="full", status="draft", title="Theirs", template="pentest")
    db_session.add(foreign)
    db_session.commit()
    resp = client.get(f"/api/v1/agent/assist/client-reports/{foreign.id}",
                      headers=_assist_headers(client, test_project.id))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# assist_get_image
# ---------------------------------------------------------------------------

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _attachment(db_session, project, uploads, data, name="shot.png"):
    note = Annotation(project_id=project.id, body="evidence", status=NoteStatus.OPEN)
    db_session.add(note)
    db_session.flush()
    folder = uploads / "note_attachments" / str(note.id)
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    att = NoteAttachment(annotation_id=note.id, project_id=project.id, filename=name,
                         content_type="image/png", size_bytes=len(data), storage_path=f"{note.id}/{name}")
    db_session.add(att)
    db_session.commit()
    return att


def test_an_attachment_comes_back_as_an_image_block(client, db_session, test_project, uploads):
    att = _attachment(db_session, test_project, uploads, PNG)
    result = _mcp(client, _assist_headers(client, test_project.id), "assist_get_image", attachment_id=att.id)
    assert result["isError"] is False
    text, image = result["content"]
    assert text["type"] == "text" and f"/attachments/{att.id}" in text["text"]
    assert image == {"type": "image", "mimeType": "image/png", "data": base64.b64encode(PNG).decode()}


def test_a_screenshot_comes_back_as_an_image_block(client, db_session, test_project, uploads):
    scan = models.Scan(project_id=test_project.id, filename="ew.zip", tool_name="eyewitness")
    db_session.add(scan)
    db_session.flush()
    iface = models.WebInterface(scan_id=scan.id, project_id=test_project.id, source="eyewitness",
                                url="https://10.61.0.1/", screenshot_path=f"{scan.id}/a.png")
    db_session.add(iface)
    db_session.commit()
    shots = uploads / "web_screenshots" / str(scan.id)
    shots.mkdir(parents=True)
    (shots / "a.png").write_bytes(PNG)

    result = _mcp(client, _assist_headers(client, test_project.id), "assist_get_image", interface_id=iface.id)
    assert result["isError"] is False
    assert result["content"][1]["data"] == base64.b64encode(PNG).decode()


def test_an_oversize_image_is_refused_with_its_download_path(client, db_session, test_project, uploads):
    att = _attachment(db_session, test_project, uploads, PNG + b"\x00" * (2 * 1024 * 1024), name="big.png")
    result = _mcp(client, _assist_headers(client, test_project.id), "assist_get_image", attachment_id=att.id)
    assert result["isError"] is True
    assert len(result["content"]) == 1
    assert f"/api/v1/agent/assist/attachments/{att.id}" in result["content"][0]["text"]


@pytest.mark.parametrize("arguments", [{}, {"attachment_id": 1, "interface_id": 1}])
def test_exactly_one_id(client, test_project, arguments):
    result = _mcp(client, _assist_headers(client, test_project.id), "assist_get_image", **arguments)
    assert result["isError"] is True and "exactly one" in result["content"][0]["text"]


def test_a_missing_image_is_the_download_routes_404(client, test_project):
    result = _mcp(client, _assist_headers(client, test_project.id), "assist_get_image", attachment_id=999999)
    assert result["isError"] is True and "HTTP 404" in result["content"][0]["text"]


@pytest.mark.parametrize("role", ["viewer", "auditor"])
def test_the_image_tool_answers_as_the_download_route_does(client, db_session, test_project, uploads, role):
    """The tool decides nothing: whatever the operator's role may download, it
    shows; whatever the route refuses, it refuses."""
    att = _attachment(db_session, test_project, uploads, PNG)
    headers = _role_headers(db_session, test_project, role)
    download = client.get(f"/api/v1/agent/assist/attachments/{att.id}", headers=headers)
    result = _mcp(client, headers, "assist_get_image", attachment_id=att.id)
    assert result["isError"] is (download.status_code != 200)
    if download.status_code == 200:
        assert result["content"][1]["data"] == base64.b64encode(download.content).decode()
