"""Review 2026-10-08, the reports stream.

* A contact's remediation list: test results and loose images are narrowed
  with the finding's systems; a reference is printed only once issued; a
  non-ASCII address renders; "prepared" is written when the job completes.
* Templates: ``"extends"`` — a template is its base with its own files over it.
* An issued report renders from its own copy of the template, and serves the
  scope file whose hash it prints.
* The build loads the template once; the worker claims an issue render first.
* Uploaded template files: metadata and bytes are always a pair.

Tests that need the shipped templates or Quarto skip in the plain backend
image and run in the report-worker image (``scripts/check.sh``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.db import models
from app.db.models import ReportJob
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceRecord
from app.db.models_remediation import RemediationEvent, RemediationPolicy
from app.db.models_reports import Report
from app.services import client_report_render, quarto_render, remediation_report, report_scope
from app.services import client_report_service as crs
from app.services import report_template_service as templates
from app.services import template_asset_store as store
from app.services.client_report_service import ClientReportService
from app.services.report_job_service import (
    ISSUE_RENDER_FORMAT, PREVIEW_JOB_FORMATS, ReportJobService,
)

HERE = Path(__file__).resolve()
SHIPPED = next(
    (p for p in (Path("/app/report-templates"), HERE.parents[2] / "report-templates")
     if (p / "contact-report" / "template.json").is_file()),
    None,
)
needs_templates = pytest.mark.skipif(SHIPPED is None, reason="report-templates/ is not mounted here")
needs_quarto = pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is only in the report-worker image")
TODAY = datetime.now(timezone.utc).date()


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def png(width: int = 680, height: int = 200, shade: int = 0) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\x00" + bytes([shade]) * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _host(db, project, ip):
    row = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(row)
    db.flush()
    return row


def _finding(db, project, title, severity, hosts, **fields):
    finding = Finding(project_id=project.id, title=title, severity=severity, status="confirmed",
                      source="manual", **fields)
    db.add(finding)
    db.flush()
    links = []
    for host in hosts:
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(link)
        links.append(link)
    db.flush()
    return finding, [link.id for link in links]


def _record(db, project, finding, host, marker):
    db.add(EvidenceRecord(
        project_id=project.id, host_id=host.id, finding_id=finding.id, tool="nmap",
        command=f"nmap --script smb2 {marker}", outcome="finding", summary=f"Summary {marker}",
        raw_output_preview=f"OUTPUT-{marker}", raw_output_bytes=10,
        executed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ))
    db.flush()


# =============================================================================
# A, B, C — one contact's remediation list
# =============================================================================

@pytest.fixture
def remediation_on(db_session, monkeypatch):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, due_soon_days=7))
    db_session.commit()
    if SHIPPED is not None:
        monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(SHIPPED))


@pytest.fixture
def two_contacts(client, db_session, test_project, remediation_on):
    """SMB is on A (alice) and B (bob); TLS is on A and C, both alice's — C
    already closed."""
    a, b, c = (_host(db_session, test_project, f"10.61.0.{n}") for n in (1, 2, 3))
    smb, (smb_a, smb_b) = _finding(db_session, test_project, "SMB signing not required", "high", [a, b],
                                   description="Signing is optional.", recommendation="Require signing.")
    tls, (tls_a, tls_c) = _finding(db_session, test_project, "TLS 1.0 enabled", "medium", [a, c],
                                   description="Old protocol.", recommendation="Disable it.")
    _record(db_session, test_project, smb, a, "ON-ALICE-HOST")
    _record(db_session, test_project, smb, b, "ON-BOB-HOST")
    db_session.commit()
    alice, bob = {"contact_email": "alice@testdomain.com"}, {"contact_email": "bob@testdomain.com"}
    r = client.post(f"/api/v1/projects/{test_project.id}/remediation/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": smb_a, **alice, "notified_on": day(10)},
        {"finding_host_id": smb_b, **bob, "notified_on": day(10)},
        {"finding_host_id": tls_a, **alice, "notified_on": day(10)},
        {"finding_host_id": tls_c, **alice, "notified_on": day(10), "status": "closed", "closed_on": day(1)},
    ]})
    assert r.status_code == 200, r.text
    return {"a": a.id, "b": b.id, "smb": smb.id, "tls": tls.id}


@needs_templates
def test_a_contact_reads_only_the_test_results_on_their_own_systems(db_session, test_project, two_contacts):
    data = remediation_report.build_dataset(db_session, test_project.id, "alice@testdomain.com")
    smb = next(f for f in data["findings"] if f["id"] == two_contacts["smb"])
    assert [c["host"] for c in smb["confirmations"]] == ["10.61.0.1"]
    # The data paths follow the narrowed list (the command is looked up by path).
    assert smb["confirmations"][0]["_path"] == f"{smb['_path']}.confirmations.0"
    assert "ON-ALICE-HOST" in smb["confirmations"][0]["command"]
    # Nothing of the other contact's system is in the data the document is rendered from.
    blob = json.dumps(data)
    assert "ON-BOB-HOST" not in blob and "10.61.0.2" not in blob
    text = quarto_render.render_source(SHIPPED / "contact-report", "report.qmd", data)
    assert "against 10\\.61\\.0\\.1" in text and "10\\.61\\.0\\.2" not in text
    # Bob's document is the mirror image.
    bob = remediation_report.build_dataset(db_session, test_project.id, "bob@testdomain.com")
    assert [c["host"] for f in bob["findings"] for c in f["confirmations"]] == ["10.61.0.2"]


@needs_templates
@needs_quarto
def test_the_rendered_document_holds_nothing_run_against_another_contacts_system(
        db_session, test_project, two_contacts, test_user):
    job = ReportJob(project_id=test_project.id, format="contact-html", report_type="remediation",
                    filters={"contact_email": "alice@testdomain.com"}, status="processing",
                    requested_by_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    data, _media, _name = client_report_render.run_client_job(db_session, job)
    html = data.decode("utf-8")
    assert "ON-ALICE-HOST" in html and "OUTPUT-ON-ALICE-HOST" in html
    assert "ON-BOB-HOST" not in html and "10.61.0.2" not in html
    assert job.filters["listed_host_ids"] == [two_contacts["a"]]


@needs_templates
def test_a_loose_image_is_printed_only_when_every_system_is_the_contacts(
        db_session, test_project, two_contacts, monkeypatch):
    def entry(att_id, placed_in):
        return {"attachment_id": att_id, "file": f"evidence/{att_id}.png", "caption": f"image {att_id}",
                "placed_in": placed_in}

    def evidence(self, findings):
        out = {}
        for f in findings:
            placed, loose = entry(f.id * 10 + 1, ["description"]), entry(f.id * 10 + 2, [])
            out[f.id] = {"images": [placed, loose], "evidence": [dict(loose)],
                         "placed": {"description": {str(placed["attachment_id"]): dict(placed)}}}
        return out, 0, 0

    monkeypatch.setattr(ClientReportService, "_evidence", evidence)
    data = remediation_report.build_dataset(db_session, test_project.id, "alice@testdomain.com")
    by_id = {f["id"]: f for f in data["findings"]}
    # SMB is also on bob's system: the image its text places stays, the loose one is withheld.
    smb = by_id[two_contacts["smb"]]
    assert [img["placed_in"] for img in smb["images"]] == [["description"]] and smb["evidence"] == []
    assert list(smb["placed"]["description"]) == [str(smb["id"] * 10 + 1)]
    # TLS is on alice's systems only (one of them already closed): nothing is withheld.
    tls = by_id[two_contacts["tls"]]
    assert len(tls["images"]) == 2 and [e["attachment_id"] for e in tls["evidence"]] == [tls["id"] * 10 + 2]
    assert data["remediation"]["images_withheld"] == 1


@needs_templates
def test_a_finding_no_report_has_issued_prints_no_reference(client, db_session, test_project, two_contacts):
    data = remediation_report.build_dataset(db_session, test_project.id, "alice@testdomain.com")
    assert [f["ref"] for f in data["findings"]] == ["", ""]
    text = quarto_render.render_source(SHIPPED / "contact-report", "report.qmd", data)
    assert "| — | [SMB signing not required]" in text and "F\\-0" not in text
    assert "A finding without a reference has not been in an issued report yet" in text

    # Issued: the references are the report's, for good.
    base = f"/api/v1/projects/{test_project.id}/client-reports"
    draft = client.post(base, json={"kind": "full"}).json()
    assert client.post(f"{base}/{draft['id']}/issue").status_code == 200
    issued = {f["id"]: f["ref"] for f in db_session.get(Report, draft["id"]).snapshot["dataset"]["findings"]}
    # A Critical confirmed afterwards sorts first — and would have taken F-01.
    host = db_session.query(models.Host).filter_by(id=two_contacts["a"]).one()
    late, (late_a,) = _finding(db_session, test_project, "Default credentials", "critical", [host])
    db_session.commit()
    client.post(f"/api/v1/projects/{test_project.id}/remediation/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": late_a, "contact_email": "alice@testdomain.com", "notified_on": day(1)}]})
    data = remediation_report.build_dataset(db_session, test_project.id, "alice@testdomain.com")
    refs = {f["id"]: f["ref"] for f in data["findings"]}
    assert refs[late.id] == ""
    assert refs[two_contacts["smb"]] == issued[two_contacts["smb"]] != ""
    assert refs[two_contacts["tls"]] == issued[two_contacts["tls"]] != ""
    text = quarto_render.render_source(SHIPPED / "contact-report", "report.qmd", data)
    assert "| — | [Default credentials]" in text and "A finding without a reference" in text


def _queue_contact_report(client, project, email, fmt="contact-html"):
    r = client.post(f"/api/v1/projects/{project.id}/remediation/contact-report",
                    json={"contact_email": email, "format": fmt})
    assert r.status_code == 202, r.text
    return r.json()["id"]


@needs_templates
def test_a_render_that_fails_leaves_no_prepared_entry(client, db_session, test_project, two_contacts, monkeypatch):
    job_id = _queue_contact_report(client, test_project, "alice@testdomain.com")
    assert db_session.query(RemediationEvent).filter_by(kind="report").count() == 0

    def broken(*args, **kwargs):
        raise quarto_render.RenderError("Quarto failed to render html (exit 1)")

    monkeypatch.setattr(quarto_render, "render", broken)
    assert ReportJobService().poll_and_run_one() is True
    db_session.expire_all()
    job = db_session.get(ReportJob, job_id)
    assert job.status == "failed"
    assert db_session.query(RemediationEvent).filter_by(kind="report").count() == 0
    assert "listed_host_ids" not in (job.filters or {})


@needs_templates
@needs_quarto
def test_a_contact_whose_address_is_not_ascii_gets_a_document_and_then_the_entry(
        client, db_session, test_project, remediation_on, test_user):
    host = _host(db_session, test_project, "10.62.0.1")
    _finding_row, (link,) = _finding(db_session, test_project, "SMB signing not required", "high", [host],
                                     recommendation="Require signing.")
    db_session.commit()
    r = client.post(f"/api/v1/projects/{test_project.id}/remediation/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": link, "contact_email": "jörg@testdomain.com", "notified_on": day(3)}]})
    assert r.status_code == 200, r.text
    job_id = _queue_contact_report(client, test_project, "jörg@testdomain.com")
    service = ReportJobService()
    assert service.poll_and_run_one() is True
    db_session.expire_all()
    job = db_session.get(ReportJob, job_id)
    assert job.status == "completed", job.error_message
    assert job.result_filename.startswith("remediation-j-rg-") and job.result_filename.isascii()
    (event,) = db_session.query(RemediationEvent).filter_by(kind="report").all()
    assert (event.host_id, event.new_value, event.author_id) == (host.id, "jörg@testdomain.com", test_user.id)
    status = client.get(f"/api/v1/projects/{test_project.id}/remediation/contact-report/{job_id}").json()
    assert status["ready"] is True and status["images_withheld"] == 0
    service._remove_artifact(job)


def test_a_file_name_part_is_ascii_whatever_the_text():
    assert client_report_render.file_slug("jörg") == "j-rg"
    assert client_report_render.file_slug("Ünïcödé Ltd.") == "n-c-d-ltd"
    assert client_report_render.file_slug("日本語", "contact") == "contact"
    assert client_report_render.file_slug(None) == "report"


def test_there_is_one_live_evidence_resolver():
    assert not hasattr(remediation_report, "_evidence_resolver")
    assert not hasattr(client_report_render, "_evidence_resolver")
    assert callable(client_report_render.live_evidence_resolver)


# =============================================================================
# D — a template that extends another
# =============================================================================

LOGO = {"id": "logo", "path": "img/logo.png", "label": "Company logo"}
STYLES = {"id": "reference-docx", "path": "branding/reference.docx", "replaces": "reference.docx",
          "label": "Word styles"}


@pytest.fixture
def family(tmp_path, monkeypatch):
    """``base`` stands alone; ``child`` extends it with one partial of its own."""
    root = tmp_path / "report-templates"
    base, child = root / "base", root / "child"
    for folder in (base / "partials", base / "scripts", base / "img", child / "partials"):
        folder.mkdir(parents=True)
    (base / "template.json").write_text(json.dumps({
        "title": "Base report", "description": "The base.", "entry": "report.qmd", "formats": ["html", "docx"],
        "evidence_records": True, "scope_inline_max": 7,
        "postprocess": {"docx": "scripts/post.py"}, "assets": [LOGO, STYLES],
    }))
    (base / "report.qmd").write_text("---\ntitle: x\n---\nBASE-ENTRY\n")
    (base / "partials" / "_a.qmd").write_text("BASE-A\n")
    (base / "partials" / "_b.qmd").write_text("BASE-B\n")
    (base / "reference.docx").write_bytes(b"base styles")
    (base / "scripts" / "post.py").write_text("# post\n")
    (base / "img" / "logo.png").write_bytes(png())            # the base's own branding, installed
    (child / "template.json").write_text(json.dumps({
        "extends": "base", "kind": "contact", "title": "Child list", "entry": "report.qmd",
    }))
    (child / "report.qmd").write_text(
        '---\ntitle: x\n---\nCHILD-ENTRY\n<% include "partials/_a.qmd" %><% include "partials/_b.qmd" %>'
        '[<< asset("logo") >>]\n'
    )
    (child / "partials" / "_b.qmd").write_text("CHILD-B\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    return root


def test_a_template_is_its_base_with_its_own_files_over_it(family, tmp_path):
    assert templates.template_problems() == []
    child = templates.get_template("child")
    assert (child.extends, child.kind, child.title, child.description) == ("base", "contact", "Child list", "")
    # template.json keys it leaves out are the base's …
    assert child.formats == ("html", "docx") and child.postprocess == {"docx": "scripts/post.py"}
    assert child.evidence_records is True and child.scope_inline_max == 7
    assert [a["id"] for a in child.assets] == ["logo", "reference-docx"]
    # … and so is every file it does not have, in the fill and in the render's copy.
    text = quarto_render.render_source(child.path, child.entry, {})
    assert "CHILD-ENTRY" in text and "BASE-A" in text and "CHILD-B" in text
    assert "BASE-ENTRY" not in text and "BASE-B" not in text
    work = tmp_path / "work"
    quarto_render.materialize(child.path, work)
    assert (work / "partials" / "_a.qmd").read_text() == "BASE-A\n"
    assert (work / "partials" / "_b.qmd").read_text() == "CHILD-B\n"
    assert (work / "reference.docx").read_bytes() == b"base styles" and (work / "scripts" / "post.py").is_file()
    # The copy stands alone: its manifest states everything and extends nothing.
    flat = json.loads((work / "template.json").read_text())
    assert "extends" not in flat and flat["kind"] == "contact" and flat["postprocess"] == {"docx": "scripts/post.py"}
    alone = templates.load_folder(work)
    assert (alone.extends, alone.postprocess, alone.formats) == (None, child.postprocess, child.formats)
    assert templates.get_template("base").extends is None
    assert quarto_render.printed_parts(child.path, child.entry, {"findings": []})["findings"] == []


def test_branding_is_never_inherited(family, tmp_path, client, test_project):
    """The base's installed logo is the base's: the child prints none until
    it has its own — installed in its folder, or uploaded for it."""
    child = templates.get_template("child")
    logo = next(a for a in child.assets if a["id"] == "logo")
    assert (logo["installed"], logo["present"]) == (False, False)
    assert next(a for a in templates.get_template("base").assets if a["id"] == "logo")["installed"] is True
    assert "[]" in quarto_render.render_source(child.path, child.entry, {})
    work = tmp_path / "work"
    quarto_render.materialize(child.path, work)
    assert not (work / "img" / "logo.png").exists()

    # Uploaded for the child: the child's, not the base's.
    mine = png(shade=9)
    r = client.put(f"/api/v1/projects/{test_project.id}/client-reports/templates/child/assets/logo",
                   files={"file": ("logo.png", mine, "image/png")})
    assert r.status_code == 200, r.text
    assert set(store.uploads("child")) == {"logo"} and store.uploads("base") == {}
    child = templates.get_template("child")
    assert next(a for a in child.assets if a["id"] == "logo")["source"] == "uploaded"
    assert "[img/logo.png]" in quarto_render.render_source(child.path, child.entry, {}, templates.asset_files(child))
    work2 = tmp_path / "work2"
    assert templates.freeze(child, work2) == templates.folder_digest(work2)
    assert (work2 / "img" / "logo.png").read_bytes() == mine
    # The page's thumbnail of an installed file comes from wherever the renderer takes it.
    preview = client.get(f"/api/v1/projects/{test_project.id}/client-reports/templates/base/assets/logo/preview")
    assert preview.status_code == 200 and preview.content == png()


def test_the_fingerprint_of_an_extending_template_follows_its_base(family):
    child = templates.get_template("child")
    before = templates.fingerprint(child)
    assert before == templates.fingerprint(templates.get_template("child"))
    (family / "base" / "partials" / "_b.qmd").write_text("BASE-B changed\n")          # shadowed by the child's
    assert templates.fingerprint(templates.get_template("child")) == before
    (family / "base" / "partials" / "_a.qmd").write_text("BASE-A changed\n")          # used by the child
    after = templates.fingerprint(templates.get_template("child"))
    assert after != before
    manifest = json.loads((family / "base" / "template.json").read_text())
    manifest["scope_inline_max"] = 9                                                   # an inherited key
    (family / "base" / "template.json").write_text(json.dumps(manifest))
    assert templates.fingerprint(templates.get_template("child")) != after
    assert Path("partials/_a.qmd") in templates.template_files(child)
    assert Path("img/logo.png") not in templates.template_files(child)


@pytest.mark.parametrize("extends, said", [
    ("gone", "there is no template called 'gone'"),
    ("child", "cannot extend itself"),
    ("middle", "itself extends another template"),
    ("../base", "must be the folder name of another template"),
    (7, "must be the folder name of another template"),
])
def test_a_base_that_cannot_be_used_is_a_named_problem(family, extends, said):
    middle = family / "middle"
    middle.mkdir()
    (middle / "template.json").write_text(json.dumps({"extends": "base", "title": "Middle"}))
    manifest = json.loads((family / "child" / "template.json").read_text())
    manifest["extends"] = extends
    (family / "child" / "template.json").write_text(json.dumps(manifest))
    (problem,) = templates.template_problems()
    assert problem["name"] == "child" and said in problem["error"]
    assert "child" not in [t.name for t in templates.list_templates(kind=None)]
    with pytest.raises(quarto_render.RenderError):
        quarto_render.render_source(family / "child", "report.qmd", {})


@needs_templates
def test_the_contact_template_holds_only_what_differs_from_the_report(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(SHIPPED))
    contact, pentest = templates.get_template("contact-report"), templates.get_template("pentest")
    assert contact.extends == "pentest" and pentest.extends is None
    assert contact.postprocess == pentest.postprocess == {"docx": "scripts/fix-docx-report.py"}
    assert [a["id"] for a in contact.assets] == [a["id"] for a in pentest.assets]
    own = {p.relative_to(contact.path).as_posix() for p in contact.path.rglob("*") if p.is_file()}
    own -= {a["path"] for a in contact.assets}                 # an operator's installed branding
    for rel in sorted(own - {".gitignore"}):
        twin = pentest.path / rel
        assert not (twin.is_file() and twin.read_bytes() == (contact.path / rel).read_bytes()), (
            f"contact-report/{rel} is the same file as pentest's: delete it, the template extends pentest")
    for rel in ("reference.docx", "scripts/fix-docx-report.py", "filters/spacers.lua",
                "partials/_confirmations.qmd", "partials/_table_caption.qmd"):
        assert quarto_render.template_file(contact.path, rel) == pentest.path / rel


# =============================================================================
# E, F — an issued report keeps its template and its scope file
# =============================================================================

@pytest.fixture
def tiny(tmp_path, monkeypatch):
    """One small real template (Quarto can render it) with an uploadable logo
    and a scope cutoff of 3."""
    root = tmp_path / "report-templates"
    folder = root / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Tiny", "entry": "report.qmd", "formats": ["html"], "scope_inline_max": 3,
        "images": {"fields": [], "trailing": False}, "assets": [LOGO],
    }))
    (folder / "report.qmd").write_text(
        "---\ntitle: \"Tiny\"\nengine: markdown\nformat:\n  html:\n    embed-resources: true\n"
        "    html-math-method: plain\n---\n\n"
        '<% if asset("logo") %>![logo](<< asset("logo") >>)<% endif %>\n\n'
        "<% for f in findings %><< f.title >>\n\n<% endfor %>\n"
    )
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    return folder


def _reports(project):
    return f"/api/v1/projects/{project.id}/client-reports"


def _upload_logo(client, project, data):
    r = client.put(f"{_reports(project)}/templates/pentest/assets/logo", files={"file": ("logo.png", data, "image/png")})
    assert r.status_code == 200, r.text


def _issue(client, db_session, project):
    host = _host(db_session, project, "10.63.0.1")
    _finding(db_session, project, "A confirmed finding", "high", [host])
    db_session.commit()
    draft = client.post(_reports(project), json={"kind": "full"}).json()
    issued = client.post(f"{_reports(project)}/{draft['id']}/issue")
    assert issued.status_code == 200, issued.text
    return issued.json()


def _issue_job(db_session, project, report_id):
    job = ReportJob(project_id=project.id, format="report-issue", report_type="client",
                    filters={"report_id": report_id}, status="processing")
    db_session.add(job)
    db_session.commit()
    return job


def test_issuing_keeps_the_template_as_it_was_used(client, db_session, test_project, tiny):
    first, second = png(shade=1), png(shade=2)
    _upload_logo(client, test_project, first)
    issued = _issue(client, db_session, test_project)
    report = db_session.get(Report, issued["id"])
    frozen = report.snapshot["template"]
    folder = Path(settings.REPORT_FILES_DIR) / str(test_project.id) / str(report.id) / "template" / "pentest"
    assert frozen == {"name": "pentest", "sha256": templates.folder_digest(folder)}
    assert (folder / "img" / "logo.png").read_bytes() == first and (folder / "report.qmd").is_file()

    # The logo is replaced and the template folder edited AFTER the issue:
    # the report's copy is what it was.
    _upload_logo(client, test_project, second)
    (tiny / "report.qmd").write_text("---\ntitle: changed\n---\nCHANGED\n")
    assert templates.fingerprint(templates.get_template("pentest")) != report.template_fingerprint
    assert crs.frozen_template_dir(report) == folder
    assert (folder / "img" / "logo.png").read_bytes() == first and "CHANGED" not in (folder / "report.qmd").read_text()
    # A copy that is no longer the one issued is refused, loudly.
    (folder / "report.qmd").write_text("tampered")
    with pytest.raises(ValueError, match="own copy of its template"):
        crs.frozen_template_dir(report)


@needs_quarto
def test_an_issued_report_renders_after_its_templates_logo_was_replaced(client, db_session, test_project, tiny):
    first, second = png(shade=1), png(shade=2)
    _upload_logo(client, test_project, first)
    issued = _issue(client, db_session, test_project)
    # Before the worker gets to it: another logo, and the folder changes too.
    _upload_logo(client, test_project, second)
    (tiny / "report.qmd").write_text((tiny / "report.qmd").read_text() + "\nADDED-AFTER-ISSUE\n")

    assert client_report_render.run_client_job(db_session, _issue_job(db_session, test_project, issued["id"])) is None
    done = client.get(f"{_reports(test_project)}/{issued['id']}").json()
    assert done["render_status"] == "done", done["render_error"]
    html = client.get(f"{_reports(test_project)}/{issued['id']}/files/html").content.decode("utf-8")
    assert base64.b64encode(first).decode() in html and base64.b64encode(second).decode() not in html
    assert "A confirmed finding" in html and "ADDED-AFTER-ISSUE" not in html


def test_a_report_issued_before_it_kept_a_copy_still_refuses_a_changed_template(
        client, db_session, test_project, tiny):
    issued = _issue(client, db_session, test_project)
    report = db_session.get(Report, issued["id"])
    # As issued before this: no copy, nothing in the snapshot about one.
    report.snapshot = {k: v for k, v in report.snapshot.items() if k != "template"}
    db_session.commit()
    shutil.rmtree(Path(settings.REPORT_FILES_DIR) / str(test_project.id) / str(report.id) / "template")
    assert crs.frozen_template_dir(report) is None
    _upload_logo(client, test_project, png(shade=3))
    with pytest.raises(ValueError, match="has changed since this report was issued"):
        client_report_render.run_client_job(db_session, _issue_job(db_session, test_project, issued["id"]))


def test_discarding_a_draft_removes_what_a_failed_issue_copied(client, db_session, test_project, tiny, monkeypatch):
    for i in range(5):
        scope = models.Scope(project_id=test_project.id, name=f"s{i}")
        db_session.add(scope)
        db_session.flush()
        db_session.add(models.Subnet(scope_id=scope.id, cidr=f"10.64.{i}.0/24"))
    db_session.commit()
    draft = client.post(_reports(test_project), json={"kind": "full"}).json()
    report_dir = Path(settings.REPORT_FILES_DIR) / str(test_project.id) / str(draft["id"])
    # An issue that copies everything and then does not commit.
    svc = ClientReportService(db_session)
    report = svc.issue(draft["id"], test_project.id, user_id=None, fingerprint="x")
    assert (report_dir / "template" / "pentest" / "template.json").is_file()
    assert (report_dir / "scope" / "scope.csv").is_file()
    assert "template" in report.snapshot
    db_session.rollback()
    assert (report_dir / "template").is_dir()            # left: the lock is gone (the images' rule)
    assert client.delete(f"{_reports(test_project)}/{draft['id']}").status_code == 204
    assert not report_dir.exists()


def _external_scope(db_session, project, n=5):
    scope = models.Scope(project_id=project.id, name="s")
    db_session.add(scope)
    db_session.flush()
    for i in range(n):
        db_session.add(models.Subnet(scope_id=scope.id, cidr=f"10.65.{i}.0/24", site="HQ"))
    db_session.commit()


def test_an_issued_reports_scope_file_is_the_one_whose_hash_it_prints(
        client, db_session, test_project, tiny, monkeypatch):
    _external_scope(db_session, test_project)
    issued = _issue(client, db_session, test_project)
    rid, printed = issued["id"], issued["summary"]["scope_external"]["file"]["sha256"]
    stored = Path(settings.REPORT_FILES_DIR) / str(test_project.id) / str(rid) / "scope" / "scope.csv"
    kept = stored.read_bytes()
    assert hashlib.sha256(kept).hexdigest() == printed
    url = f"{_reports(test_project)}/{rid}/scope.csv"

    # The bytes kept at issue are what is served — whatever today's code would write.
    as_written_then = report_scope.scope_csv
    monkeypatch.setattr(report_scope, "scope_csv", lambda scope: b"kind,value\r\nwritten by newer code\r\n")
    assert client.get(url).content == kept

    # A report issued before the bytes were kept builds the file again — and
    # is refused when that is no longer the file the report names.
    stored.unlink()
    refused = client.get(url)
    assert refused.status_code == 500 and "no longer matches the SHA-256" in refused.json()["detail"]
    with pytest.raises(ValueError, match=printed):          # what the log line says
        crs.issued_scope_file(db_session.get(Report, rid), db_session.get(Report, rid).snapshot["dataset"]["scope"])
    monkeypatch.setattr(report_scope, "scope_csv", as_written_then)
    assert client.get(url).content == kept

    # A kept file that was altered is not served either.
    stored.write_bytes(kept + b"network,10.99.0.0/24,,,\r\n")
    assert client.get(url).status_code == 500


def test_a_draft_and_a_report_that_lists_its_scope_are_served_as_before(client, db_session, test_project, tiny):
    _external_scope(db_session, test_project, n=2)              # under the cutoff: no file is named
    draft = client.post(_reports(test_project), json={"kind": "full"}).json()
    assert client.get(f"{_reports(test_project)}/{draft['id']}/scope.csv").status_code == 200
    issued = _issue(client, db_session, test_project)
    assert issued["summary"]["scope_external"] is None
    assert not (Path(settings.REPORT_FILES_DIR) / str(test_project.id) / str(issued["id"]) / "scope").exists()
    assert client.get(f"{_reports(test_project)}/{issued['id']}/scope.csv").content.count(b"network,") == 2


# =============================================================================
# G — one template load per build
# =============================================================================

def test_a_build_loads_the_template_once(client, db_session, test_project, tiny, monkeypatch):
    host = _host(db_session, test_project, "10.66.0.1")
    _finding(db_session, test_project, "A confirmed finding", "high", [host])
    db_session.commit()
    draft = client.post(_reports(test_project), json={"kind": "full"}).json()
    loads = []
    real = templates.get_template
    monkeypatch.setattr(templates, "get_template", lambda name: loads.append(name) or real(name))
    dataset, _reported, summary = ClientReportService(db_session).build(db_session.get(Report, draft["id"]))
    assert loads == ["pentest"]
    # … and everything that reads it still did.
    assert dataset["scope"]["inline_max"] == 3 and summary["template_images"] == {"fields": [], "trailing": False}
    # A template that is gone is still a build (and a summary), as before.
    report = db_session.get(Report, draft["id"])
    report.template = "no-such-template"
    assert ClientReportService(db_session).build(report)[2]["images_printed"] is None


# =============================================================================
# H — the worker
# =============================================================================

def _queued(db, project, fmt, report_id, *, minutes_ago, user_id=None):
    job = ReportJob(project_id=project.id, format=fmt, report_type="client", filters={"report_id": report_id},
                    status="queued", requested_by_id=user_id,
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    db.add(job)
    db.flush()
    return job


def test_an_issue_render_is_claimed_before_the_previews_queued_ahead_of_it(db_session, test_project):
    first = _queued(db_session, test_project, "report-html", 1, minutes_ago=30)
    second = _queued(db_session, test_project, "report-docx", 2, minutes_ago=20)
    issue = _queued(db_session, test_project, "report-issue", 3, minutes_ago=1)
    db_session.commit()
    order = []
    for _ in range(3):
        job_id, claimed_at = ReportJobService._claim_next(db_session, message="Generating report")
        db_session.commit()
        job = db_session.get(ReportJob, job_id)
        db_session.refresh(job)
        assert (job.status, job.started_at, job.last_heartbeat) == ("processing", claimed_at, claimed_at)
        order.append(job_id)
    assert order == [issue.id, first.id, second.id]
    assert ReportJobService._claim_next(db_session, message="x") is None


def test_a_repeated_preview_is_superseded_by_the_same_persons_newer_one(db_session, test_project, test_user):
    old = _queued(db_session, test_project, "report-html", 7, minutes_ago=9, user_id=test_user.id)
    new = _queued(db_session, test_project, "report-html", 7, minutes_ago=1, user_id=test_user.id)
    other_format = _queued(db_session, test_project, "report-docx", 7, minutes_ago=8, user_id=test_user.id)
    other_report = _queued(db_session, test_project, "report-html", 8, minutes_ago=8, user_id=test_user.id)
    someone_else = _queued(db_session, test_project, "report-html", 7, minutes_ago=8, user_id=None)
    issue_twice = [_queued(db_session, test_project, "report-issue", 7, minutes_ago=m, user_id=test_user.id)
                   for m in (6, 5)]
    db_session.commit()
    assert ReportJobService._supersede_repeated_previews(db_session) == 1
    db_session.commit()
    for job in (old, new, other_format, other_report, someone_else, *issue_twice):
        db_session.refresh(job)
    assert (old.status, old.message) == ("cancelled", "Superseded by a newer preview of the same report")
    assert {j.status for j in (new, other_format, other_report, someone_else, *issue_twice)} == {"queued"}


def test_the_queues_format_names_are_the_renderers():
    assert PREVIEW_JOB_FORMATS == tuple(client_report_render.PREVIEW_FORMATS)
    assert ISSUE_RENDER_FORMAT == client_report_render.ISSUE_FORMAT


def test_a_render_says_it_is_alive_before_each_format_and_post_processor(tmp_path):
    folder = tmp_path / "tpl"
    (folder / "scripts").mkdir(parents=True)
    (folder / "report.qmd").write_text("---\ntitle: x\n---\nbody\n")
    (folder / "scripts" / "ok.py").write_text("pass\n")
    quarto = tmp_path / "quarto"
    quarto.write_text('#!/bin/sh\ncase "$4" in html) echo x > report.html;; docx) echo x > report.docx;; esac\n')
    quarto.chmod(0o755)
    beats = []
    files = quarto_render.render(
        folder, "report.qmd", {}, ["html", "docx", "qmd"], tmp_path / "out", timeout=30, quarto=str(quarto),
        postprocess={"docx": "scripts/ok.py"}, on_progress=lambda: beats.append(1),
    )
    assert set(files) == {"html", "docx", "qmd"}
    assert len(beats) == 4                       # three formats, one post-processor


def test_the_report_renders_touch_the_workers_liveness_file(monkeypatch, tmp_path):
    """Both callers hand the renderer the worker's heartbeat."""
    from app import worker_loop

    seen = {}

    def fake_render(*args, **kwargs):
        seen["on_progress"] = kwargs.get("on_progress")
        raise quarto_render.RenderError("stop here")

    monkeypatch.setattr(quarto_render, "render", fake_render)
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({"title": "T", "formats": ["html"]}))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(folder.parent))
    report = Report(id=1, project_id=1, template="pentest")
    with pytest.raises(quarto_render.RenderError):
        client_report_render._render(None, report, {"findings": []}, ["html"], tmp_path / "out", "x")
    assert seen["on_progress"] is worker_loop.touch_heartbeat


# =============================================================================
# I — an uploaded template file and its metadata are a pair
# =============================================================================

def test_an_upload_is_named_by_its_content_and_replaces_the_one_before(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    asset = {"id": "logo", "path": "img/logo.png", "label": "Logo", "kind": "png", "uploadable": True}
    first, second = png(shade=1), png(shade=2)
    store.save("pentest", asset, first, uploaded_by="a")
    one = store.uploads("pentest")["logo"]
    assert one["file"].name == f"logo.{hashlib.sha256(first).hexdigest()[:16]}.png" == one["stored"]
    store.save("pentest", asset, second, uploaded_by="a")
    two = store.uploads("pentest")["logo"]
    assert two["file"].read_bytes() == second and two["sha256"] == hashlib.sha256(second).hexdigest()
    assert not one["file"].exists()                           # nothing names it any more
    folder = two["file"].parent
    assert {p.name for p in folder.iterdir() if not p.name.startswith(".")} == {"logo.json", two["file"].name}


def test_a_reader_between_the_two_writes_never_pairs_old_metadata_with_new_bytes(tmp_path, monkeypatch):
    """The replacement publishes the file, then the metadata.  A reader that
    comes in between sees the OLD upload whole — its metadata and its bytes."""
    import os

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    asset = {"id": "logo", "path": "img/logo.png", "label": "Logo", "kind": "png", "uploadable": True}
    first, second = png(shade=1), png(shade=2)
    store.save("pentest", asset, first, uploaded_by="a")
    seen = []
    real_replace = os.replace

    def watched(src, dst):
        real_replace(src, dst)
        meta = store.uploads("pentest")["logo"]
        seen.append((meta["sha256"], hashlib.sha256(meta["file"].read_bytes()).hexdigest()))

    monkeypatch.setattr(store.os, "replace", watched)
    store.save("pentest", asset, second, uploaded_by="a")
    assert len(seen) == 2 and all(named == actual for named, actual in seen)
    assert seen[0][0] == hashlib.sha256(first).hexdigest() and seen[1][0] == hashlib.sha256(second).hexdigest()


def test_an_upload_stored_before_the_name_carried_the_hash_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    folder = store.root() / "pentest"
    folder.mkdir(parents=True)
    data = png()
    (folder / "logo.png").write_bytes(data)
    (folder / "logo.json").write_text(json.dumps({"kind": "png", "size": len(data),
                                                  "sha256": hashlib.sha256(data).hexdigest()}))
    assert store.overrides("pentest") == {"logo": folder / "logo.png"}
    # A name the metadata could not have given is not followed out of the folder.
    (folder / "logo.json").write_text(json.dumps({"kind": "png", "stored": "../../etc/passwd"}))
    assert store.overrides("pentest") == {"logo": folder / "logo.png"}
    # Replacing it removes the old file.
    asset = {"id": "logo", "path": "img/logo.png", "label": "Logo", "kind": "png", "uploadable": True}
    store.save("pentest", asset, png(shade=5), uploaded_by="a")
    assert not (folder / "logo.png").exists() and store.remove("pentest", "logo") is not None
    assert store.uploads("pentest") == {}
