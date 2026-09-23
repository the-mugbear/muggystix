"""v2.380.0 — client reports: drafts, issuing, revisions and addenda.

What a report includes (confirmed, accepted risk and remediated findings;
false-positive endpoints dropped), who may do what (auditors read, analysts
draft, project admins issue), that issuing freezes the report, and that an
addendum's delta is computed by finding AND endpoint against the baseline's
frozen state — new findings, new endpoints, withdrawals, never remediation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment, ReportJob
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_reports import Report
from app.main import app


@pytest.fixture(autouse=True)
def template_dir(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    folder = root / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx", "pdf"],
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    return folder


def _member(db_session, project, user_id, username, role):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


@pytest.fixture
def act_as():
    def _act(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


@pytest.fixture
def people(db_session, test_project):
    return {
        "analyst": _member(db_session, test_project, 401, "ana", ProjectRole.ANALYST),
        "analyst2": _member(db_session, test_project, 402, "ben", ProjectRole.ANALYST),
        "auditor": _member(db_session, test_project, 403, "aud", ProjectRole.AUDITOR),
        "admin": _member(db_session, test_project, 404, "adm", ProjectRole.ADMIN),
    }


def _host(db_session, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db_session.add(host)
    db_session.flush()
    return host


def _finding(db_session, project, title, severity="high", status="confirmed", hosts=(), **kw):
    f = Finding(project_id=project.id, title=title, severity=severity, status=status, source="manual", **kw)
    db_session.add(f)
    db_session.flush()
    for h in hosts:
        if isinstance(h, tuple):
            host, host_status = h
        else:
            host, host_status = h, "open"
        db_session.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=host_status))
    db_session.commit()
    return f


def _base(project):
    return f"/api/v1/projects/{project.id}/client-reports"


def _create(client, project, **body):
    r = client.post(_base(project), json=body or {"kind": "full"})
    assert r.status_code == 201, r.text
    return r.json()


def _issue(client, project, rid):
    r = client.post(f"{_base(project)}/{rid}/issue")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_draft_reports_what_was_judged_and_says_what_was_left_out(client, db_session, test_project):
    a, b, c = (_host(db_session, test_project, ip) for ip in ("10.40.0.1", "10.40.0.2", "10.40.0.3"))
    _finding(db_session, test_project, "SQL injection", "critical", hosts=[a], description="d", impact="i", recommendation="r")
    _finding(db_session, test_project, "Weak TLS", "medium", status="accepted_risk", hosts=[a, (b, "false_positive")])
    _finding(db_session, test_project, "Default creds", "high", status="remediated", hosts=[(c, "remediated")])
    _finding(db_session, test_project, "Still looking", "high", status="open", hosts=[a])
    _finding(db_session, test_project, "Retesting", "low", status="retest", hosts=[a])
    _finding(db_session, test_project, "Scanner misfire", "high", status="false_positive", hosts=[a])
    _finding(db_session, test_project, "Every host a false positive", "high", hosts=[(a, "false_positive")])
    _finding(db_session, test_project, "Banner", "info", hosts=[b])

    report = _create(client, test_project)
    assert report["status"] == "draft" and report["can_issue"] is True  # the fixture user is a global admin
    summary = report["summary"]
    assert summary["counts"] == {"critical": 1, "high": 1, "medium": 1, "low": 0, "info": 1, "total": 4}
    assert summary["under_investigation"] == 2
    # Only the SQL injection has its text.
    assert {m["title"] for m in summary["missing_text"]} == {"Weak TLS", "Default creds", "Banner"}

    dataset, _, _ = __import__("app.services.client_report_service", fromlist=["x"]).ClientReportService(
        db_session).build(db_session.get(Report, report["id"]))
    by_title = {f["title"]: f for f in dataset["findings"]}
    assert [f["ref"] for f in dataset["findings"]] == ["F-01", "F-02", "F-03", "F-04"]
    assert by_title["Weak TLS"]["status_note"] == "Risk accepted"
    assert [e["address"] for e in by_title["Weak TLS"]["affected"]] == ["10.40.0.1"]
    assert by_title["Default creds"]["status_note"] == "Remediated during the assessment"
    assert by_title["Default creds"]["affected"][0]["state"] == "Remediated"
    assert by_title["Banner"]["severity_label"] == "Informational"


def test_roles_auditors_read_analysts_draft_admins_issue(client, db_session, test_project, people, act_as):
    act_as(people["auditor"])
    listing = client.get(_base(test_project))
    assert listing.status_code == 200 and listing.json()["can_create"] is False
    assert client.post(_base(test_project), json={"kind": "full"}).status_code == 403

    act_as(people["analyst"])
    report = _create(client, test_project)
    assert report["can_edit"] is True and report["can_issue"] is False
    r = client.patch(f"{_base(test_project)}/{report['id']}", json={"executive_summary": "  Two criticals.  "})
    assert r.status_code == 200 and r.json()["executive_summary"] == "Two criticals."
    assert client.post(f"{_base(test_project)}/{report['id']}/issue").status_code == 403

    act_as(people["admin"])
    issued = _issue(client, test_project, report["id"])
    assert issued["status"] == "issued" and issued["number"] == 1
    assert issued["render_status"] == "pending"
    assert issued["template_fingerprint"] and len(issued["template_fingerprint"]) == 64
    assert db_session.query(AuditLog).filter_by(action="report_issued", resource_id=str(report["id"])).count() == 1

    act_as(people["auditor"])
    assert client.get(f"{_base(test_project)}/{report['id']}").json()["summary"]["counts"]["total"] == 0

    act_as(people["analyst"])
    assert client.patch(f"{_base(test_project)}/{report['id']}", json={"title": "x"}).status_code == 409
    assert client.delete(f"{_base(test_project)}/{report['id']}").status_code == 409
    # Issuing twice is refused.
    act_as(people["admin"])
    assert client.post(f"{_base(test_project)}/{report['id']}/issue").status_code == 409


def test_issuing_freezes_the_report(client, db_session, test_project):
    host = _host(db_session, test_project, "10.41.0.1")
    f = _finding(db_session, test_project, "Weak TLS", hosts=[host], description="Before")
    report = _create(client, test_project)
    client.patch(f"{_base(test_project)}/{report['id']}", json={"settings": {"client_name": "Example Corp"}})
    _issue(client, test_project, report["id"])

    f.description = "After"
    f.severity = "low"
    db_session.commit()
    stored = db_session.get(Report, report["id"])
    db_session.refresh(stored)
    dataset = stored.snapshot["dataset"]
    assert dataset["findings"][0]["description"] == "Before"
    assert dataset["findings"][0]["severity"] == "high"
    assert dataset["engagement"]["client_name"] == "Example Corp"
    assert dataset["report"]["number"] == 1 and dataset["report"]["draft"] is False
    assert client.get(f"{_base(test_project)}/{report['id']}").json()["summary"]["counts"]["high"] == 1


def test_a_new_draft_starts_from_the_profile(client, test_project):
    r = client.put(f"{_base(test_project)}/profile", json={
        "client_name": "Example Corp", "classification": "Confidential",
        "testers": [{"name": "Ana", "role": "Lead"}], "distribution": [{"name": "CISO", "email": "ciso@example.com"}],
        "template": "pentest",
    })
    assert r.status_code == 200, r.text
    report = _create(client, test_project)
    assert report["settings"]["client_name"] == "Example Corp"
    assert report["settings"]["testers"][0]["name"] == "Ana"
    # Changing the profile afterwards does not rewrite the draft.
    client.put(f"{_base(test_project)}/profile", json={"client_name": "Renamed"})
    assert client.get(f"{_base(test_project)}/{report['id']}").json()["settings"]["client_name"] == "Example Corp"
    assert client.put(f"{_base(test_project)}/profile", json={"template": "../etc"}).status_code == 422


def test_an_addendum_needs_an_issued_report(client, test_project):
    r = client.post(_base(test_project), json={"kind": "addendum"})
    assert r.status_code == 409, r.text


def test_the_addendum_delta_is_by_finding_and_endpoint_and_never_remediation(client, db_session, test_project):
    a, b, c = (_host(db_session, test_project, ip) for ip in ("10.42.0.1", "10.42.0.2", "10.42.0.3"))
    sqli = _finding(db_session, test_project, "SQL injection", "critical", hosts=[a])
    tls = _finding(db_session, test_project, "Weak TLS", "medium", hosts=[a, b])
    creds = _finding(db_session, test_project, "Default creds", "high", hosts=[b])
    smb = _finding(db_session, test_project, "SMB signing", "low", hosts=[c])
    full = _create(client, test_project)
    issued = _issue(client, test_project, full["id"])
    refs = {f["title"]: f["ref"] for f in db_session.get(Report, issued["id"]).snapshot["dataset"]["findings"]}

    # After the report: a new finding; a reported finding reaches a new host;
    # one is judged a false positive; one endpoint is dismissed; one finding is
    # remediated (not something an addendum reports).
    new = _finding(db_session, test_project, "Anonymous LDAP", "high", hosts=[c])
    db_session.add(FindingHost(finding_id=sqli.id, host_id=c.id, host_status="open"))
    creds.status = "false_positive"
    db_session.query(FindingHost).filter_by(finding_id=tls.id, host_id=b.id).one().host_status = "false_positive"
    smb.status = "remediated"
    db_session.commit()

    add = _create(client, test_project, kind="addendum")
    assert add["baseline"]["number"] == 1
    assert add["title"] == "Addendum to report #1"
    assert add["summary"]["delta"] == {"new_findings": 1, "findings_with_new_endpoints": 1, "withdrawn": 2}

    dataset, reported, _ = __import__("app.services.client_report_service", fromlist=["x"]).ClientReportService(
        db_session).build(db_session.get(Report, add["id"]))
    shown = {f["title"]: f for f in dataset["findings"]}
    assert set(shown) == {"Anonymous LDAP", "SQL injection"}
    assert shown["Anonymous LDAP"]["change"] == "new" and shown["Anonymous LDAP"]["ref"] == "F-05"
    assert shown["SQL injection"]["change"] == "new_hosts" and shown["SQL injection"]["ref"] == refs["SQL injection"]
    assert [e["address"] for e in shown["SQL injection"]["new_affected"]] == ["10.42.0.3"]
    withdrawn = {w["title"]: w for w in dataset["delta"]["withdrawn"]}
    assert withdrawn["Default creds"]["reason"] == "The finding was judged a false positive."
    assert withdrawn["Weak TLS"]["endpoints"] == ["10.42.0.2"]
    assert "SMB signing" not in withdrawn
    # The addendum's reported state is cumulative: the next one compares with everything.
    assert set(reported) == {str(sqli.id), str(tls.id), str(smb.id), str(new.id)}

    # Issuing it, then deleting the new finding, shows up in the NEXT addendum.
    _issue(client, test_project, add["id"])
    db_session.delete(db_session.get(Finding, new.id))
    db_session.commit()
    second = _create(client, test_project, kind="addendum")
    assert second["baseline"]["number"] == 2
    assert second["summary"]["delta"] == {"new_findings": 0, "findings_with_new_endpoints": 0, "withdrawn": 1}


def test_a_revision_supersedes_the_original(client, db_session, test_project):
    first = _issue(client, test_project, _create(client, test_project)["id"])
    rev = client.post(f"{_base(test_project)}/{first['id']}/revise")
    assert rev.status_code == 201, rev.text
    rev = rev.json()
    assert rev["revision_of"]["id"] == first["id"] and rev["status"] == "draft"
    issued = _issue(client, test_project, rev["id"])
    assert issued["number"] == 2
    original = client.get(f"{_base(test_project)}/{first['id']}").json()
    assert original["status"] == "superseded" and original["superseded_by"]["id"] == rev["id"]
    # Only the current issue can be revised or used as a baseline.
    assert client.post(f"{_base(test_project)}/{first['id']}/revise").status_code == 409
    assert client.get(_base(test_project)).json()["latest_issued_id"] == rev["id"]


def test_two_drafts_revising_one_report_cannot_both_be_issued(client, test_project):
    first = _issue(client, test_project, _create(client, test_project)["id"])
    one = client.post(f"{_base(test_project)}/{first['id']}/revise").json()
    two = client.post(f"{_base(test_project)}/{first['id']}/revise").json()
    _issue(client, test_project, one["id"])
    assert client.post(f"{_base(test_project)}/{two['id']}/issue").status_code == 409


def test_drafts_are_discarded_by_their_creator_or_an_admin(client, test_project, people, act_as):
    act_as(people["analyst"])
    draft = _create(client, test_project)
    act_as(people["analyst2"])
    assert client.delete(f"{_base(test_project)}/{draft['id']}").status_code == 403
    act_as(people["admin"])
    assert client.delete(f"{_base(test_project)}/{draft['id']}").status_code == 204


def test_a_preview_is_a_client_job_kept_out_of_the_export_tray(client, db_session, test_project):
    draft = _create(client, test_project)
    r = client.post(f"{_base(test_project)}/{draft['id']}/preview", json={"format": "docx"})
    assert r.status_code == 202, r.text
    job = db_session.get(ReportJob, r.json()["id"])
    assert (job.format, job.report_type, job.filters) == ("report-docx", "client", {"report_id": draft["id"]})
    tray = client.get(f"/api/v1/projects/{test_project.id}/reports/jobs").json()
    assert all(j["id"] != job.id for j in tray)


def test_only_images_marked_for_the_report_go_in(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.43.0.1")
    root = Annotation(host_id=host.id, user_id=test_user.id, body="proof", note_type="finding")
    db_session.add(root)
    db_session.flush()
    f = _finding(db_session, test_project, "Anonymous FTP", hosts=[host], evidence_annotation_id=root.id)
    comment = Annotation(finding_id=f.id, user_id=test_user.id, body="more")
    db_session.add(comment)
    db_session.flush()
    for ann, name, ctype, marked in (
        (root, "listing.png", "image/png", True),
        (root, "desktop.png", "image/png", False),
        (comment, "upload.jpg", "image/jpeg", True),
        (comment, "modern.webp", "image/webp", True),
    ):
        db_session.add(NoteAttachment(
            annotation_id=ann.id, project_id=test_project.id, filename=name, content_type=ctype,
            size_bytes=1, storage_path=f"{ann.id}/{name}", include_in_report=marked,
        ))
    db_session.commit()
    report = _create(client, test_project)
    assert report["summary"]["images"] == 2 and report["summary"]["images_skipped"] == 1
    dataset, _, _ = __import__("app.services.client_report_service", fromlist=["x"]).ClientReportService(
        db_session).build(db_session.get(Report, report["id"]))
    captions = [e["caption"] for e in dataset["findings"][0]["evidence"]]
    assert captions == ["listing.png", "upload.jpg"]
    assert dataset["findings"][0]["evidence"][0]["file"].startswith("evidence/")
