"""v2.381.0 — client-report jobs end to end on the report worker.

A draft preview renders the live findings as one format; issuing renders every
format from the frozen snapshot into report storage, where the download
endpoint serves them.  Needs Quarto (the report-worker image) and the
report-templates/ mount; skipped elsewhere.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from app.core.config import settings
from app.db import models
from app.db.models import ReportJob
from app.db.models_findings import Finding, FindingHost
from app.db.models_reports import Report
from app.services.client_report_render import run_client_job

TEMPLATES = next(
    (p for p in (Path("/app/report-templates"), Path(__file__).resolve().parents[2] / "report-templates")
     if (p / "pentest" / "template.json").is_file()),
    None,
)
pytestmark = [
    pytest.mark.skipif(TEMPLATES is None, reason="report-templates/ is not mounted here"),
    pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is only in the report-worker image"),
]


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(TEMPLATES))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    return tmp_path


def _seed(db_session, project):
    host = models.Host(project_id=project.id, ip_address="10.50.0.7", hostname="files01", state="up")
    db_session.add(host)
    db_session.flush()
    f = Finding(
        project_id=project.id, title="SMB signing not required", severity="medium",
        status="confirmed", source="manual", description="Signing is **optional**.",
        recommendation="Require signing.", impact="Relay attacks.",
        cvss_vector="CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N", cvss_score=5.9,
    )
    db_session.add(f)
    db_session.flush()
    db_session.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db_session.commit()


def _job(db_session, project, fmt, report_id):
    job = ReportJob(
        project_id=project.id, format=fmt, report_type="client",
        filters={"report_id": report_id}, status="processing",
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_preview_then_issue_then_download(client, db_session, test_project):
    _seed(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}/client-reports"
    draft = client.post(base, json={"kind": "full"}).json()

    data, media, name = run_client_job(db_session, _job(db_session, test_project, "report-html", draft["id"]))
    html = data.decode("utf-8")
    assert media == "text/html" and name.endswith(f"-draft-{draft['id']}.html")
    assert "SMB signing not required" in html and "<strong>optional</strong>" in html
    assert "DRAFT" in html

    issued = client.post(f"{base}/{draft['id']}/issue").json()
    assert issued["render_status"] == "pending"
    assert run_client_job(db_session, _job(db_session, test_project, "report-issue", draft["id"])) is None

    report = client.get(f"{base}/{draft['id']}").json()
    assert report["render_status"] == "done" and report["quarto_version"]
    # No PDF since v2.407.0: the Word report is exported to PDF from Word.
    assert {f["format"] for f in report["files"]} == {"html", "docx", "qmd"}
    docx = next(f for f in report["files"] if f["format"] == "docx")
    assert docx["filename"].endswith("-report-01.docx")

    r = client.get(f"{base}/{draft['id']}/files/docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert hashlib.sha256(r.content).hexdigest() == docx["sha256"]
    assert client.post(f"{base}/{draft['id']}/preview", json={"format": "pdf"}).status_code == 422
    # A PDF stored with a report issued before then still downloads: an
    # issued report never changes, and its files are served by row.
    from app.db.models_reports import ReportFile
    (Path(settings.REPORT_FILES_DIR) / "legacy.pdf").write_bytes(b"%PDF-1.7 legacy")
    db_session.add(ReportFile(report_id=draft["id"], format="pdf", filename="legacy.pdf",
                              media_type="application/pdf", size_bytes=15, sha256="0" * 64,
                              storage_path="legacy.pdf"))
    db_session.commit()
    r = client.get(f"{base}/{draft['id']}/files/pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.7 legacy"
    # An issued report has no previews.
    assert client.post(f"{base}/{draft['id']}/preview", json={"format": "html"}).status_code == 409


def test_a_failed_issue_render_is_recorded_and_can_be_retried(client, db_session, test_project):
    base = f"/api/v1/projects/{test_project.id}/client-reports"
    draft = client.post(base, json={"kind": "full"}).json()
    client.post(f"{base}/{draft['id']}/issue")
    report = db_session.get(Report, draft["id"])
    report.template = "no-such-template"
    db_session.commit()
    with pytest.raises(Exception):
        run_client_job(db_session, _job(db_session, test_project, "report-issue", draft["id"]))
    db_session.expire_all()
    failed = client.get(f"{base}/{draft['id']}").json()
    assert failed["render_status"] == "failed" and "no-such-template" in failed["render_error"]

    report = db_session.get(Report, draft["id"])
    report.template = "pentest"
    db_session.commit()
    retried = client.post(f"{base}/{draft['id']}/render")
    assert retried.status_code == 200 and retried.json()["render_status"] == "pending"
    run_client_job(db_session, _job(db_session, test_project, "report-issue", draft["id"]))
    assert client.get(f"{base}/{draft['id']}").json()["render_status"] == "done"
