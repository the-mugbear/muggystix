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


def test_the_contact_document_and_a_preview_are_not_found_through_another_pages_door(
    client, db_session, test_project, test_user, tmp_path,
):
    _policy(db_session, True)
    report = Report(project_id=test_project.id, title="Draft", template="pentest", created_by_id=test_user.id)
    db_session.add(report)
    db_session.commit()
    preview = _finished_job(db_session, test_project, tmp_path, report_type="client", fmt="report-html",
                            filters={"report_id": report.id}, name="preview.html")
    contact = _finished_job(db_session, test_project, tmp_path, report_type="remediation", fmt="contact-docx",
                            filters={"report_id": report.id, "contact_email": "a@example.com"}, name="c.docx")
    assert client.get(f"{_p(test_project)}/remediation/contact-report/{preview.id}").status_code == 404
    assert client.get(f"{_p(test_project)}/client-reports/{report.id}/previews/{contact.id}").status_code == 404


# --- R-RPT1: the written-fields filter is frozen at issue ---------------------

@pytest.fixture
def template_root(tmp_path, monkeypatch):
    from app.core.config import settings

    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text('{"title": "t", "entry": "report.qmd", "formats": ["html"]}')
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(folder.parent))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    return folder


def _issued(template_root, frozen):
    return Report(id=7, project_id=3, title="r", template="pentest", status="issued",
                  snapshot={"dataset": {}, "template": frozen})


def test_issuing_freezes_the_field_filter_and_an_issued_render_reads_that_copy(template_root, tmp_path, monkeypatch,
                                                                              db_session):
    import hashlib

    from app.services import client_report_service as crs
    from app.services import quarto_render

    shipped = quarto_render.FIELDS_FILTER.read_bytes()
    report = _issued(template_root, None)
    frozen = crs.freeze_report_template(report)
    assert frozen["fields_sha256"] == hashlib.sha256(shipped).hexdigest()
    report.snapshot["template"] = frozen

    # An upgrade ships another filter: the issued report keeps its own.
    other = tmp_path / "other.lua"
    other.write_text("-- another release's filter\n")
    monkeypatch.setattr(quarto_render, "FIELDS_FILTER", other)
    folder = crs.frozen_template_dir(report)
    path = crs.frozen_fields_filter(report, folder)
    assert path == folder / quarto_render.FROZEN_FIELDS_FILTER and path.read_bytes() == shipped

    # What the render is handed (no Quarto here: the call is captured).
    from app.services import client_report_render as renderer

    seen = {}
    monkeypatch.setattr(renderer.quarto_render, "render", lambda *a, **kw: seen.update(kw) or {})
    renderer._render(db_session, report, {}, ["html"], tmp_path / "out", "b", issued=True)
    assert seen["fields_filter"].read_bytes() == shipped

    # A changed frozen filter is not the report that was issued.
    path.write_text("-- tampered\n")
    with pytest.raises(ValueError):
        crs.frozen_fields_filter(report, path.parents[1])


def test_a_report_issued_before_the_filter_was_frozen_renders_with_the_releases(template_root):
    from app.services import client_report_service as crs

    report = _issued(template_root, None)
    frozen = crs.freeze_report_template(report)
    frozen.pop("fields_sha256")
    report.snapshot["template"] = frozen
    assert crs.frozen_fields_filter(report, template_root) is None


# --- nit: a stale issue-render attempt's failure is fenced on its claim ------

@pytest.mark.parametrize("reclaimed", [False, True])
def test_a_stale_issue_render_attempt_does_not_mark_the_report_failed(
    db_session, test_project, test_user, monkeypatch, reclaimed,
):
    from datetime import datetime, timedelta, timezone

    from app.services import client_report_render as renderer

    report = Report(project_id=test_project.id, title="Issued", template="pentest", status="issued",
                    number=1, render_status="pending", snapshot={"dataset": {}}, created_by_id=test_user.id)
    db_session.add(report)
    db_session.commit()
    claimed = datetime.now(timezone.utc) - timedelta(minutes=30)
    job = ReportJob(project_id=test_project.id, report_type="client", format="report-issue",
                    filters={"report_id": report.id}, status="processing", started_at=claimed)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    def render_fails(db, _report):
        if reclaimed:
            # Meanwhile the reaper requeued the job and a peer claimed it.
            db.query(ReportJob).filter(ReportJob.id == job_id).update(
                {"started_at": datetime.now(timezone.utc)})
            db.commit()
        raise RuntimeError("quarto exited 1")

    monkeypatch.setattr(renderer, "_render_issued", render_fails)
    with pytest.raises(RuntimeError):
        renderer.run_client_job(db_session, job)
    db_session.expire_all()
    row = db_session.get(Report, report.id)
    if reclaimed:
        assert (row.render_status, row.render_error) == ("pending", None)
    else:
        assert (row.render_status, row.render_error) == ("failed", "quarto exited 1")


# --- nit: a draft's scope file is its scope block, not a whole build ----------

@pytest.mark.parametrize("subnets", [3, 30])      # listed in the report / named as a file
def test_a_drafts_scope_file_is_the_builds_scope_without_building_the_report(
    client, db_session, test_project, test_user, template_root, monkeypatch, subnets,
):
    from app.db.models import Subnet
    from app.services import report_scope
    from app.services.client_report_service import ClientReportService
    from app.services.default_scope import get_or_create_default_scope

    scope = get_or_create_default_scope(db_session, test_project.id)
    db_session.add_all([Subnet(scope_id=scope.id, cidr=f"10.{i}.0.0/16", description=f"=net {i}")
                        for i in range(subnets)])
    report = Report(project_id=test_project.id, title="Draft", template="pentest", created_by_id=test_user.id)
    db_session.add(report)
    db_session.commit()

    # Old: the bytes and the name of the scope a whole build holds.
    built = ClientReportService(db_session).build(report)[0]["scope"]
    assert bool(built.get("file")) == (subnets > 25)
    url = f"{_p(test_project)}/client-reports/{report.id}/scope.csv"
    old = client.get(url)
    assert old.status_code == 200 and old.content == report_scope.scope_csv(built)

    # New: the same file, and the report's findings are not built for it.
    def no_build(*_a, **_kw):
        raise AssertionError("the scope file built the whole report")

    monkeypatch.setattr(ClientReportService, "build", no_build)
    new = client.get(url)
    assert new.status_code == 200
    assert (new.content, new.headers["content-disposition"]) == (old.content, old.headers["content-disposition"])


def test_an_inventory_download_is_still_served_by_the_generic_routes(client, db_session, test_project, tmp_path):
    job = _finished_job(db_session, test_project, tmp_path, report_type="comprehensive", fmt="json",
                        filters={}, name="hosts.json")
    generic = f"{_p(test_project)}/reports/jobs/{job.id}"
    assert client.get(generic).status_code == 200
    assert client.get(f"{generic}/download").content == b"the document"
