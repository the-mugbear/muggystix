"""Review 2026-10-23, client reports (H-RPT1, R-RPT1, R-RPT3 and the report
nits).  Each test fails on the code before its change."""
from __future__ import annotations

import pytest

from app.db.models import ReportJob
from app.db.models_remediation import RemediationPolicy
from app.db.models_reports import Report


def _policy(db, enabled: bool) -> None:
    db.add(RemediationPolicy(id=1, enabled=enabled, days_critical=30, days_high=30, days_medium=90,
                             days_low=120, days_info=None, due_soon_days=7))
    db.commit()


def _finished_job(db, project, tmp_path, *, report_type, fmt, filters, name):
    artifact = tmp_path / name
    artifact.write_bytes(b"the document")
    job = ReportJob(project_id=project.id, report_type=report_type, status="completed", format=fmt,
                    filters=filters, result_path=str(artifact), result_filename=name,
                    media_type="application/octet-stream")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _p(project):
    return f"/api/v1/projects/{project.id}"


# --- H-RPT1: a page-owned job is served only by its own page ------------------

@pytest.mark.parametrize("enabled", [False, True])
def test_a_contacts_document_is_served_only_by_the_remediation_routes_and_only_with_tracking_on(
    client, db_session, test_project, tmp_path, enabled,
):
    _policy(db_session, enabled)
    job = _finished_job(db_session, test_project, tmp_path, report_type="remediation", fmt="contact-docx",
                        filters={"contact_email": "roger@example.com"}, name="contact.docx")
    generic = f"{_p(test_project)}/reports/jobs/{job.id}"
    assert client.get(generic).status_code == 404
    assert client.get(f"{generic}/download").status_code == 404
    assert all(j["id"] != job.id for j in client.get(f"{_p(test_project)}/reports/jobs").json())

    own = f"{_p(test_project)}/remediation/contact-report/{job.id}"
    status, download = client.get(own), client.get(f"{own}/download")
    if enabled:
        assert status.status_code == 200 and status.json()["contact_email"] == "roger@example.com"
        assert download.status_code == 200 and download.content == b"the document"
    else:
        assert status.status_code == 404 and download.status_code == 404


def test_a_drafts_preview_is_polled_and_downloaded_through_its_report(client, db_session, test_project,
                                                                      test_user, tmp_path):
    report = Report(project_id=test_project.id, title="Draft", template="pentest", created_by_id=test_user.id)
    other = Report(project_id=test_project.id, title="Other", template="pentest", created_by_id=test_user.id)
    db_session.add_all([report, other])
    db_session.commit()
    job = _finished_job(db_session, test_project, tmp_path, report_type="client", fmt="report-html",
                        filters={"report_id": report.id}, name="preview.html")
    generic = f"{_p(test_project)}/reports/jobs/{job.id}"
    assert client.get(generic).status_code == 404
    assert client.get(f"{generic}/download").status_code == 404

    own = f"{_p(test_project)}/client-reports/{report.id}/previews/{job.id}"
    assert client.get(own).json()["id"] == job.id
    assert client.get(f"{own}/download").content == b"the document"
    # Another report's door does not find it.
    assert client.get(f"{_p(test_project)}/client-reports/{other.id}/previews/{job.id}").status_code == 404


def test_an_inventory_download_is_still_served_by_the_generic_routes(client, db_session, test_project, tmp_path):
    job = _finished_job(db_session, test_project, tmp_path, report_type="comprehensive", fmt="json",
                        filters={}, name="hosts.json")
    generic = f"{_p(test_project)}/reports/jobs/{job.id}"
    assert client.get(generic).status_code == 200
    assert client.get(f"{generic}/download").content == b"the document"
