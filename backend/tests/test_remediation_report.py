"""One contact's remediation list (v2.463.0): a COPY of the penetration test
template with its own kind, filled from a client report's dataset narrowed to
the contact, rendered as a job — never a ``reports`` row, never a template a
client report can be created with, and the report's own template untouched."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models import ReportJob
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership
from app.db.models_remediation import RemediationEvent, RemediationPolicy
from app.db.models_reports import Report
from app.main import app
from app.services import quarto_render, remediation_report
from app.services.client_report_render import file_slug
from app.services import report_template_service as templates

HERE = Path(__file__).resolve()
ROOT = next(
    (p for p in (Path("/app/report-templates"), HERE.parents[2] / "report-templates")
     if (p / "contact-report" / "template.json").is_file()),
    None,
)
needs_templates = pytest.mark.skipif(ROOT is None, reason="report-templates/ is not mounted here")
needs_quarto = pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is not installed here")
TODAY = datetime.now(timezone.utc).date()


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def base(project):
    return f"/api/v1/projects/{project.id}/remediation"


@pytest.fixture(autouse=True)
def installation(db_session, monkeypatch):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, due_soon_days=7))
    db_session.commit()
    if ROOT is not None:
        monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(ROOT))


def _host(db, project, ip, hostname=None):
    row = models.Host(project_id=project.id, ip_address=ip, hostname=hostname, state="up")
    db.add(row)
    db.flush()
    return row


def _finding(db, project, title, severity, hosts, *, status="confirmed", recommendation=None):
    finding = Finding(project_id=project.id, title=title, severity=severity, status=status, source="manual",
                      recommendation=recommendation)
    db.add(finding)
    db.flush()
    links = []
    for host in hosts:
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(link)
        links.append(link)
    db.flush()
    return finding, [link.id for link in links]


@pytest.fixture
def world(client, db_session, test_project):
    """Roger: SMB on A (overdue) and B (closed), TLS on A (due soon).
    Jane: SMB on C.  A finding still under investigation on A is Roger's too."""
    a = _host(db_session, test_project, "10.60.0.1", "files01")
    b = _host(db_session, test_project, "10.60.0.2")
    c = _host(db_session, test_project, "10.60.0.3")
    smb, (smb_a, smb_b, smb_c) = _finding(db_session, test_project, "SMB signing not required", "high", [a, b, c],
                                          recommendation="Require SMB signing by group policy.")
    tls, (tls_a,) = _finding(db_session, test_project, "TLS 1.0 enabled", "medium", [a],
                             recommendation="Disable TLS 1.0 and 1.1.")
    open_one, (open_a,) = _finding(db_session, test_project, "Not yet confirmed", "low", [a], status="open")
    db_session.commit()
    roger = {"contact_email": "roger@testdomain.com", "contact_name": "Roger Smith"}
    r = client.post(f"{base(test_project)}/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": smb_a, **roger, "notified_on": day(42), "team": "Platform"},
        {"finding_host_id": smb_b, **roger, "notified_on": day(42), "status": "closed", "closed_on": day(3)},
        {"finding_host_id": tls_a, **roger, "notified_on": day(85)},
        {"finding_host_id": open_a, **roger, "notified_on": day(1)},
        {"finding_host_id": smb_c, "contact_email": "jane@testdomain.com", "notified_on": day(1)},
    ]})
    assert r.status_code == 200, r.text
    return {"a": a.id, "smb": smb.id, "tls": tls.id}


# --- a template of its own kind ------------------------------------------------------

@needs_templates
def test_the_contact_template_is_installed_and_is_not_a_client_report_template(client, test_project):
    template = templates.get_template("contact-report")
    assert (template.kind, template.formats) == ("contact", ("html", "docx"))
    assert "contact-report" not in [t.name for t in templates.list_templates()]
    assert "contact-report" in [t.name for t in templates.list_templates(kind=None)]
    assert all(t.kind == "client" for t in templates.list_templates())
    assert templates.template_problems() == []
    assert templates.default_template_name() == "pentest"
    # It is restyled like the others: the same uploadable files as the report's.
    assert {a["id"] for a in template.assets} == {a["id"] for a in templates.get_template("pentest").assets}
    # The page lists every template with its kind (the files section shows
    # them all; the choosers offer the client ones).
    listed = {t["name"]: t["kind"] for t in
              client.get(f"/api/v1/projects/{test_project.id}/client-reports/templates").json()}
    assert listed["contact-report"] == "contact" and listed["pentest"] == "client"
    # A project cannot make it its default client-report template either.
    profile = client.put(f"/api/v1/projects/{test_project.id}/client-reports/profile",
                         json={"template": "contact-report"})
    assert profile.status_code == 422
    refused = client.post(f"/api/v1/projects/{test_project.id}/client-reports",
                          json={"title": "x", "template": "contact-report"})
    assert refused.status_code == 422 and "not a client-report template" in refused.text


@needs_templates
def test_a_template_kind_that_does_not_exist_is_a_named_problem(tmp_path, monkeypatch):
    folder = tmp_path / "odd"
    shutil.copytree(ROOT / "contact-report", folder)
    shutil.copytree(ROOT / "pentest", tmp_path / "pentest")      # the template it extends
    manifest = json.loads((folder / "template.json").read_text())
    manifest["kind"] = "newsletter"
    (folder / "template.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(tmp_path))
    (problem,) = templates.template_problems()
    assert problem["name"] == "odd" and "kind must be one of client, contact" in problem["error"]


# --- the data -------------------------------------------------------------------------

@needs_templates
def test_the_list_holds_only_the_contacts_open_rows_with_their_deadlines(db_session, test_project, world):
    data = remediation_report.build_dataset(db_session, test_project.id, "Roger@TestDomain.com")
    assert data["report"]["contact"] == {"name": "Roger Smith", "email": "roger@testdomain.com"}
    assert data["report"]["draft"] is False and data["report"]["as_of"] == TODAY.isoformat()
    by_title = {f["title"]: f for f in data["findings"]}
    assert set(by_title) == {"SMB signing not required", "TLS 1.0 enabled"}     # the unconfirmed one is not reportable
    (smb,) = by_title["SMB signing not required"]["affected"]                    # B is closed, C is Jane's
    assert (smb["address"], smb["deadline_state"], smb["deadline"], smb["team"]) == (
        "10.60.0.1", "overdue", "12 days overdue", "Platform")
    assert smb["due_on"] == day(12)
    (tls,) = by_title["TLS 1.0 enabled"]["affected"]
    assert (tls["deadline_state"], tls["deadline"]) == ("due_soon", "Due in 5 days")
    # Written text is looked up by path: the paths follow the narrowed list.
    assert [f["_path"] for f in data["findings"]] == ["findings.0", "findings.1"]
    summary = data["remediation"]
    assert (summary["overdue"], summary["due_soon"], summary["open"], summary["closed"]) == (1, 1, 3, 1)
    assert summary["not_listed"] == 1                                            # the finding under investigation
    assert summary["timeline"] == "Critical and High 30 days, Medium 90 days, Low 120 days, Informational no deadline"
    # Nothing was stored: this is not a client report.
    assert db_session.query(Report).count() == 0


@needs_templates
def test_the_filled_template_is_the_contacts_systems_and_nobody_elses(db_session, test_project, world):
    data = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    text = quarto_render.render_source(ROOT / "contact-report", "report.qmd", data)
    assert "| Contact | Roger Smith" in text and "testdomain\\.com" in text
    assert "| Past the deadline | 1 |" in text and "| Due within 7 days | 1 |" in text
    assert "| Still open | 3 " in text and "1 more already reported fixed, not listed" in text
    # The table's label (owner, 2026-10-14: a template table has no caption).
    assert '::: {custom-style="Table Label"}\n**Findings assigned to you, most urgent first**\n:::' in text
    assert "bs-table-caption" not in text
    assert "1 more assigned to you is still being investigated" in text
    # The report's format: every finding with its sections, and the deadline
    # on each of its systems.
    assert text.count("Recommendations") == 2 and text.count("bs-md") == 2      # each recommendation, through the filter
    # A section with nothing written prints no heading (the report prints a
    # TODO for its author; this reader is not the author).
    assert "Description" not in text and "Impact" not in text and "Steps to reproduce" not in text
    assert "| 10\\.60\\.0\\.1 | files01 |  | " + day(12).replace("-", "\\-") + " | 12 days overdue |" in text
    assert "Due in 5 days" in text
    assert "10\\.60\\.0\\.2" not in text and "10\\.60\\.0\\.3" not in text and "jane" not in text
    # No author's TODO reaches the contact, and nothing of the report's front part.
    assert "{.bs-todo}" not in text and "Executive summary" not in text and "Project information" not in text


@needs_templates
def test_the_sample_fills_and_a_contact_with_nothing_open_is_told_so():
    sample = json.loads((ROOT / "contact-report" / "sample-data.json").read_text())
    text = quarto_render.render_source(ROOT / "contact-report", "report.qmd", sample)
    assert "Roger Smith" in text and "12 days overdue" in text and "{.bs-todo}" not in text
    sample["findings"] = []
    assert "Nothing assigned to you is open in this project." in quarto_render.render_source(
        ROOT / "contact-report", "report.qmd", sample)


# --- the job ------------------------------------------------------------------------------

@needs_templates
def test_preparing_it_queues_a_job_and_the_timeline_is_written_when_it_completes(
        client, db_session, test_project, world, test_user):
    r = client.post(f"{base(test_project)}/contact-report", json={"contact_email": "Roger@testdomain.com"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["status"], body["format"], body["contact_email"], body["ready"]) == (
        "queued", "contact-docx", "roger@testdomain.com", False)
    job = db_session.get(ReportJob, body["id"])
    assert (job.report_type, job.filters, job.requested_by_id) == (
        "remediation", {"contact_email": "roger@testdomain.com"}, test_user.id)
    # Queued is not prepared: nothing is on the timeline until there is a document.
    assert db_session.query(RemediationEvent).filter_by(kind="report").count() == 0
    # What the worker does on completion, in the completing transaction.
    job.filters = {**job.filters, "listed_host_ids": [world["a"]]}
    assert remediation_report.record_prepared(db_session, job) == 1
    db_session.commit()
    (event,) = db_session.query(RemediationEvent).filter_by(kind="report").all()
    assert (event.host_id, event.new_value, event.body, event.author_id) == (
        world["a"], "roger@testdomain.com", "Remediation list prepared (docx)", test_user.id)
    timeline = client.get(f"{base(test_project)}/hosts/{world['a']}/events").json()["items"]
    assert timeline[0]["kind"] == "report" and timeline[0]["can_modify"] is False

    status = client.get(f"{base(test_project)}/contact-report/{job.id}").json()
    assert status["status"] == "queued"
    assert client.get(f"{base(test_project)}/contact-report/{job.id}/download").status_code == 409
    # Another kind of export is not reachable through these routes.
    other = ReportJob(project_id=test_project.id, format="json", report_type="comprehensive", filters={}, status="queued")
    db_session.add(other)
    db_session.commit()
    assert client.get(f"{base(test_project)}/contact-report/{other.id}").status_code == 404


@needs_templates
def test_who_may_prepare_it_and_what_is_refused(client, db_session, test_project, world):
    assert client.post(f"{base(test_project)}/contact-report",
                       json={"contact_email": "nobody@testdomain.com"}).status_code == 404
    assert client.post(f"{base(test_project)}/contact-report",
                       json={"contact_email": "roger@testdomain.com", "format": "contact-pdf"}).status_code == 422
    auditor = User(id=8801, username="rep-auditor", email="rep-auditor@example.com", full_name="A",
                   hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True,
                   created_at=datetime.now(timezone.utc))
    db_session.add(auditor)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=auditor.id, role="auditor"))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: auditor
    try:
        refused = client.post(f"{base(test_project)}/contact-report", json={"contact_email": "roger@testdomain.com"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert refused.status_code == 403
    assert db_session.query(ReportJob).count() == 0


@needs_templates
@needs_quarto
def test_the_worker_renders_the_document(db_session, test_project, world, test_user):
    job = ReportJob(project_id=test_project.id, format="contact-html", report_type="remediation",
                    filters={"contact_email": "roger@testdomain.com"}, status="running",
                    requested_by_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    from app.services.client_report_render import run_client_job
    data, media_type, filename = run_client_job(db_session, job)
    html = data.decode("utf-8")
    # The project is in the name: a contact with findings in several projects
    # is handed one document per project, prepared on the same day.
    assert media_type == "text/html" and filename.endswith(".html")
    assert filename.startswith(f"remediation-{file_slug(test_project.name)}-roger-")
    assert "Roger Smith" in html and "12 days overdue" in html and "Require SMB signing by group policy." in html
    assert "10.60.0.3" not in html and "TODO" not in html
    # What the page says before the document is sent: every finding has its recommendation.
    assert (job.filters["findings"], job.filters["without_recommendation"]) == (2, 0)


# --- what the 2026-10-12 walkthrough asked for (2.494.0) -----------------------------

@needs_templates
def test_the_findings_are_listed_most_urgent_first_not_in_the_reports_order(
        client, db_session, test_project, world):
    """The report orders by severity: SMB (high) before TLS (medium).  Here
    that is also the urgent order — so a critical finding that is merely on
    track is added, which the report would print first and this list last."""
    host = _host(db_session, test_project, "10.60.0.9")
    _f, (link,) = _finding(db_session, test_project, "Default credentials", "critical", [host])
    db_session.commit()
    r = client.post(f"{base(test_project)}/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": link, "contact_email": "roger@testdomain.com", "notified_on": day(1)}]})
    assert r.status_code == 200, r.text
    data = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    assert [f["title"] for f in data["findings"]] == [
        "SMB signing not required", "TLS 1.0 enabled", "Default credentials"]
    assert [f["_path"] for f in data["findings"]] == ["findings.0", "findings.1", "findings.2"]
    assert data["remediation"]["without_recommendation"] == 1          # the new one has none written


@needs_templates
def test_a_document_narrowed_like_the_reminder_lists_that_and_says_so(client, db_session, test_project, world):
    whole = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    assert (whole["remediation"]["selection"], whole["remediation"]["listed"]) == ("", 3)
    # A deferral leaves "overdue or due soon": TLS is deferred, SMB stays.
    tls_row = next(f for f in whole["findings"] if f["title"] == "TLS 1.0 enabled")
    review = (TODAY + timedelta(days=20)).isoformat()
    r = client.post(f"{base(test_project)}/apply", json={"overwrite": True, "rows": [
        {"finding_id": tls_row["id"], "host_id": world["a"], "status": "deferred", "deferred_review_on": review,
         "notes": [{"body": "Waiting for the vendor"}]}]})
    assert r.status_code == 200, r.text
    whole = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    (tls,) = next(f for f in whole["findings"] if f["title"] == "TLS 1.0 enabled")["affected"]
    assert (tls["deadline"], tls["due_on"]) == (f"Deferred, review on {review}", None)

    narrowed = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com", upcoming_days=0)
    assert [f["title"] for f in narrowed["findings"]] == ["SMB signing not required"]
    summary = narrowed["remediation"]
    assert (summary["selection"], summary["listed"], summary["open"]) == ("Past the deadline or due soon", 1, 3)
    text = quarto_render.render_source(templates.get_template("contact-report").path, "report.qmd", narrowed)
    assert "| This document lists | Past the deadline or due soon: 1 of the 3 |" in text
    assert "This document lists" not in quarto_render.render_source(
        templates.get_template("contact-report").path, "report.qmd", whole)

    # The request carries it to the worker, and a window with nothing in it is refused in words.
    queued = client.post(f"{base(test_project)}/contact-report",
                         json={"contact_email": "roger@testdomain.com", "upcoming_days": 0})
    assert queued.status_code == 202, queued.text
    assert db_session.get(ReportJob, queued.json()["id"]).filters == {
        "contact_email": "roger@testdomain.com", "upcoming_days": 0}
    r = client.post(f"{base(test_project)}/apply", json={"overwrite": True, "rows": [
        {"finding_id": world["smb"], "host_id": world["a"], "notified_on": TODAY.isoformat()}]})
    assert r.status_code == 200, r.text
    empty = client.post(f"{base(test_project)}/contact-report",
                        json={"contact_email": "roger@testdomain.com", "upcoming_days": 0})
    assert empty.status_code == 409 and "choose a wider selection" in empty.text
    assert client.post(f"{base(test_project)}/contact-report",
                       json={"contact_email": "roger@testdomain.com", "upcoming_days": 400}).status_code == 422


def test_a_contact_says_where_they_still_have_work_and_a_reminder_what_it_leaves_out(
        client, db_session, test_project, world):
    (roger,) = [c for c in client.get(f"{base(test_project)}/contacts").json()["items"]
                if c["contact_email"] == "roger@testdomain.com"]
    assert roger["work_project_ids"] == [test_project.id]
    # Everything of Roger's here that is at risk is in the reminder: no project is left out.
    reminder = client.get(f"{base(test_project)}/follow-up", params={"contact_email": "roger@testdomain.com"}).json()
    assert (reminder["project_ids"], reminder["other_projects"]) == ([test_project.id], 0)
    # Jane has one open finding, on track: her reminder names no project, and says one has work.
    jane = client.get(f"{base(test_project)}/follow-up", params={"contact_email": "jane@testdomain.com"}).json()
    assert (jane["project_ids"], jane["other_projects"]) == ([], 1)


@needs_templates
def test_a_finding_prints_only_the_sections_that_have_text(db_session, test_project, world):
    """Seen in the first Word file: bare "Impact", "Steps to reproduce" and
    "Recommendations" headings on every page, and a key/value table one word
    wide."""
    finding = db_session.query(Finding).filter_by(title="TLS 1.0 enabled").one()
    finding.description, finding.impact, finding.recommendation = "Old protocol.", "Traffic can be read.", None
    db_session.commit()
    data = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    text = quarto_render.render_source(ROOT / "contact-report", "report.qmd", data)
    tls = text[text.index("TLS 1\\.0 enabled (Medium)"):]
    assert "Description" in tls and "Impact" in tls and "Steps to reproduce" not in tls
    assert "Evidence / proof of concept" not in tls
    assert "No recommendation has been written for this finding yet." in tls
    # The value column of the front table is the wide one.
    label, value = next(line for line in text.splitlines() if line.startswith("|:--") and line.count("|") == 3).strip("|").split("|")
    assert len(value) >= 2 * len(label)


def test_the_penetration_test_template_itself_is_untouched():
    """The owner's condition: the contact's document is a copy; the report's
    template knows nothing about contacts or deadlines."""
    if ROOT is None:
        pytest.skip("report-templates/ is not mounted here")
    for path in sorted((ROOT / "pentest").rglob("*")):
        if path.is_file() and path.suffix in (".qmd", ".json") and path.name != "sample-data.json":
            text = path.read_text(encoding="utf-8")
            for marker in ('report.get("contact")', "report.contact", "due_on", "deadline", "remediation."):
                assert marker not in text, (path.name, marker)


@needs_templates
@needs_quarto
def test_what_a_person_typed_about_the_contact_stays_text(tmp_path):
    hostile = "{{< env HOME >}} <script>alert(1)</script> [x](javascript:alert(2)) `tick` | pipe *bold*"
    data = json.loads((ROOT / "contact-report" / "sample-data.json").read_text())
    data["report"]["contact"] = {"name": hostile, "email": hostile}
    data["remediation"]["timeline"] = hostile
    for finding in data["findings"]:
        finding["deadline"] = hostile
        for endpoint in finding["affected"]:
            endpoint["deadline"], endpoint["due_on"], endpoint["team"] = hostile, hostile, hostile
    manifest = quarto_render.template_manifest(ROOT / "contact-report")     # with what it takes from pentest
    files = quarto_render.render(ROOT / "contact-report", "report.qmd", data, ["html"], tmp_path,
                                 postprocess=manifest.get("postprocess"), timeout=240,
                                 resolve_evidence=lambda item: None)
    html = files["html"].read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html and 'href="javascript' not in html
    assert "{{&lt; env HOME &gt;}}" in html and "bs-report-" not in html
