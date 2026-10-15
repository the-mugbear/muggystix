"""A draft's preview says when its inputs changed (the report workspace,
owner 2026-10-14; the proposal's "Preview freshness", revised after its
second review).

* The worker records, on each preview job, the fingerprint of the inputs it
  rendered; `GET …/{id}/inputs` answers the fingerprint as it would be now.
  The same draft with nothing changed answers the same value.
* It changes with what a render reads from outside the draft too: a
  finding's text, a project default the draft follows, the template's files,
  the file of an image the dataset names (which file, never its bytes).
* Today's date is not an input: a preview rendered yesterday of an unchanged
  draft is current.
"""
from pathlib import Path

from app.db import models
from app.db.models import ReportJob
from app.db.models_findings import Finding, FindingHost
from app.db.models_reports import Report
from app.services import client_report_render
from tests.test_client_reports import _base, _create, _issue, template_dir  # noqa: F401 — the fixture


def _seed(db_session, project) -> Finding:
    host = models.Host(project_id=project.id, ip_address="10.50.0.7", hostname="files01", state="up")
    db_session.add(host)
    db_session.flush()
    f = Finding(project_id=project.id, title="SMB signing not required", severity="medium",
                status="confirmed", source="manual", description="Signing is optional.")
    db_session.add(f)
    db_session.flush()
    db_session.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db_session.commit()
    return f


def _now(client, project, rid) -> str:
    r = client.get(f"{_base(project)}/{rid}/inputs")
    assert r.status_code == 200, r.text
    return r.json()["fingerprint"]


def test_the_worker_records_what_the_preview_rendered_and_the_page_reads_the_same(
        client, db_session, test_project, template_dir, monkeypatch, tmp_path):
    _seed(db_session, test_project)
    draft = _create(client, test_project)

    def fake_render(db, report, dataset, formats, out_dir, basename, *, issued=False):
        out = Path(out_dir) / f"{basename}.html"
        out.write_text("<html></html>")
        return {"html": out}

    monkeypatch.setattr(client_report_render, "_render", fake_render)
    job = ReportJob(project_id=test_project.id, format="report-html", report_type="client",
                    filters={"report_id": draft["id"]}, status="processing")
    db_session.add(job)
    db_session.commit()
    client_report_render.run_client_job(db_session, job)
    assert job.inputs_fingerprint and len(job.inputs_fingerprint) == 64
    assert _now(client, test_project, draft["id"]) == job.inputs_fingerprint
    assert _now(client, test_project, draft["id"]) == job.inputs_fingerprint      # stable


def test_what_a_render_reads_from_outside_the_draft_changes_it(
        client, db_session, test_project, template_dir):
    finding = _seed(db_session, test_project)
    client.patch(f"{_base(test_project)}/profile", json={"classification": "Confidential", "template": "pentest"})
    draft = _create(client, test_project)
    first = _now(client, test_project, draft["id"])

    finding.description = "Signing is not required on any share."
    db_session.commit()
    after_finding = _now(client, test_project, draft["id"])
    assert after_finding != first

    client.patch(f"{_base(test_project)}/profile", json={"classification": "Restricted"})
    after_default = _now(client, test_project, draft["id"])
    assert after_default != after_finding

    (template_dir / "report.qmd").write_text("---\ntitle: y\n---\n")
    assert _now(client, test_project, draft["id"]) != after_default


def test_todays_date_is_not_an_input_and_an_images_file_is(db_session, test_project, monkeypatch, tmp_path):
    """An image is an input by WHICH FILE it is (an attachment's file is
    written once under a new name and never rewritten), never by reading its
    bytes on every check (review 2026-10-14, R2); a file gone is an input
    too."""
    report = Report(project_id=test_project.id, kind="full", title="Draft", template="pentest")
    image = tmp_path / "shot.png"
    image.write_bytes(b"one")
    files = {7: image}
    monkeypatch.setattr(client_report_render, "live_evidence_resolver",
                        lambda db, project_id: lambda item: files.get(item["attachment_id"]))
    dataset = {"report": {"date": "2026-10-13", "title": "Draft"},
               "findings": [{"images": [{"attachment_id": 7, "caption": "A share"}]}]}
    yesterday = client_report_render.inputs_fingerprint(db_session, report, dataset)
    today = client_report_render.inputs_fingerprint(
        db_session, report, {**dataset, "report": {"date": "2026-10-14", "title": "Draft"}})
    assert today == yesterday

    # The image's file is another one now (a replaced upload is a new file).
    other = tmp_path / "other.png"
    other.write_bytes(b"two")
    files[7] = other
    replaced = client_report_render.inputs_fingerprint(db_session, report, dataset)
    assert replaced != yesterday
    other.unlink()
    assert client_report_render.inputs_fingerprint(db_session, report, dataset) not in (yesterday, replaced)
    # The bytes are never read.
    files[7] = image
    real_read = Path.read_bytes

    def read_bytes(self):
        assert self != image, "an image's bytes were read"
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert client_report_render.inputs_fingerprint(db_session, report, dataset) == yesterday


def test_an_issued_report_has_no_inputs_to_compare(client, db_session, test_project, template_dir):
    _seed(db_session, test_project)
    draft = _create(client, test_project)
    _issue(client, test_project, draft["id"])
    assert client.get(f"{_base(test_project)}/{draft['id']}/inputs").status_code == 409
