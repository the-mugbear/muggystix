"""Review 2026-10-01 — the report findings (R13, R15, R16, R17, N2, N5), test
results in the client report (B8) and re-rated findings in an addendum (B17).

The Quarto side — the process-group timeout (R14) and the hostile-text
contract with a hostile command and output — is in test_quarto_render.py.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment, ReportJob
from app.db.models_agent import AgentSession
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_proposals import EvidenceRecord
from app.db.models_reports import Report
from app.main import app
from app.services import client_report_service as crs
from app.services import csv_utils, cvss_service, quarto_render, report_scope
from app.services import report_template_service as templates
from app.services.client_report_service import ClientReportService
from app.services.report_generator import ReportGenerator


@pytest.fixture(autouse=True)
def template_dir(tmp_path, monkeypatch):
    """Two templates: ``pentest`` prints how findings were confirmed, ``brief``
    does not ask for it."""
    root = tmp_path / "report-templates"
    for name, extra in (("pentest", {"evidence_records": True}), ("brief", {})):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "template.json").write_text(json.dumps({
            "title": name, "entry": "report.qmd", "formats": ["html", "docx"], **extra,
        }))
        (folder / "report.qmd").write_text(PRINTS_EVERYTHING)
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    return root


# A report's data holds what its template PRINTS (S2): a template that asks
# for nothing gets no test results and prints no image.  This one asks for
# every finding's written text, test results and trailing images.
PRINTS_EVERYTHING = (
    "---\ntitle: x\n---\n"
    "<% for f in findings %>\n"
    '<% for field in ("description", "impact", "steps_to_reproduce", "recommendation", "references") %>'
    "<< md(f, field) >><% endfor %>\n"
    '<% for c in f.get("confirmations") or [] %><< code(c, "command") >><< code(c, "output") >><% endfor %>\n'
    "<% for e in f.evidence %><< image(e) >><% endfor %>\n"
    "<% endfor %>\n"
)


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
        host, host_status = h if isinstance(h, tuple) else (h, "open")
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


def _build(db_session, rid):
    db_session.expire_all()
    return ClientReportService(db_session).build(db_session.get(Report, rid))


class _Statements:
    """Every SQL statement run while the block is open."""

    def __enter__(self):
        self.sql = []
        event.listen(Engine, "before_cursor_execute", self._record)
        return self

    def _record(self, _conn, _cursor, statement, _params, _context, _many):
        self.sql.append(" ".join(statement.split()))

    def __exit__(self, *exc):
        event.remove(Engine, "before_cursor_execute", self._record)


def _postgres(db_session) -> bool:
    return db_session.get_bind().dialect.name == "postgresql"


# --- R13: a draft's PATCH and DELETE wait for an issue ---------------------------

def test_changing_or_discarding_a_draft_locks_its_row_before_the_status_check(client, db_session, test_project):
    if not _postgres(db_session):
        pytest.skip("FOR UPDATE is only rendered on PostgreSQL")
    rid = _create(client, test_project)["id"]

    def locks(statements):
        return [s for s in statements if "FROM reports" in s and "FOR UPDATE" in s]

    with _Statements() as seen:
        assert client.patch(f"{_base(test_project)}/{rid}", json={"title": "Renamed"}).status_code == 200
    assert locks(seen.sql), "PATCH read the draft without a lock"
    # The lock is on the report row alone: no join for the files in it.
    assert all("JOIN" not in s for s in locks(seen.sql))

    with _Statements() as seen:
        assert client.delete(f"{_base(test_project)}/{rid}").status_code == 204
    assert locks(seen.sql), "DELETE read the draft without a lock"


def test_an_issued_report_still_refuses_a_change_and_keeps_its_files_list(client, db_session, test_project):
    rid = _create(client, test_project)["id"]
    _issue(client, test_project, rid)
    assert client.patch(f"{_base(test_project)}/{rid}", json={"title": "Late"}).status_code == 409
    assert client.delete(f"{_base(test_project)}/{rid}").status_code == 409
    assert client.patch(f"{_base(test_project)}/999999", json={"title": "x"}).status_code == 404


def test_the_fingerprint_is_taken_from_the_row_the_issue_holds_locked(client, db_session, test_project, test_user):
    rid = _create(client, test_project)["id"]
    seen = []

    def fingerprint(locked):
        seen.append((locked.id, locked.template, locked.status))
        return "f" * 64

    report = ClientReportService(db_session).issue(
        rid, test_project.id, user_id=test_user.id, fingerprint=fingerprint,
    )
    db_session.commit()
    assert seen == [(rid, "pentest", "draft")]      # the locked draft, before anything is written
    assert report.template_fingerprint == "f" * 64


def test_issuing_records_the_fingerprint_of_the_template_the_report_has_now(client, db_session, test_project):
    """The route used to compute it from a read taken BEFORE the lock."""
    rid = _create(client, test_project)["id"]
    assert client.patch(f"{_base(test_project)}/{rid}", json={"template": "brief"}).status_code == 200
    issued = _issue(client, test_project, rid)
    assert issued["template"] == "brief"
    assert issued["template_fingerprint"] == templates.fingerprint(templates.get_template("brief"))
    assert issued["template_fingerprint"] != templates.fingerprint(templates.get_template("pentest"))


# --- R15: the host report says which filters narrowed it --------------------------

def test_every_applied_filter_is_printed_escaped():
    gen = ReportGenerator.__new__(ReportGenerator)
    out = gen._format_filters_html({
        "q": 'port:445 AND NOT tag:"<b>x</b>"', "sites": "London DC", "tags": "3,7",
        "weaknesses": "smb_unsigned,weak_tls", "has_critical_vulns": True, "has_open_ports": False,
        "orgs": ["Google, LLC", "<script>"], "state": None, "search": "", "brand_new_filter": "v",
    })
    assert "None" not in out
    assert "Query: port:445 AND NOT tag:&quot;&lt;b&gt;x&lt;/b&gt;&quot;" in out
    assert "Sites: London DC" in out and "Tag ids: 3,7" in out
    assert "Weaknesses: smb_unsigned,weak_tls" in out
    # A flag is its label alone; a flag set to false says so.
    assert "Has critical vulnerabilities," in out or out.rstrip("</p>").endswith("Has critical vulnerabilities")
    assert "Has critical vulnerabilities:" not in out
    assert "Has open ports: no" in out
    assert "Organisations: Google, LLC, &lt;script&gt;" in out
    # A filter nobody labelled is still reported.
    assert "Brand new filter: v" in out
    assert "<script>" not in out and "<b>" not in out
    assert "Host state" not in out and "Search" not in out      # empty ones are not listed


def test_no_filter_reads_none():
    gen = ReportGenerator.__new__(ReportGenerator)
    assert "None" in gen._format_filters_html({})
    assert "None" in gen._format_filters_html({"state": None, "q": ""})


def test_every_hosts_filter_has_a_readable_label():
    from app.api.v1.endpoints.hosts import HostFilterParams
    import inspect
    params = [p for p in inspect.signature(HostFilterParams.__init__).parameters if p != "self"]
    assert len(params) >= 31
    missing = [p for p in params if p not in ReportGenerator._FILTER_LABELS]
    assert missing == []


# --- R16: the ledger and the image lookups -----------------------------------------

def test_a_draft_build_fetches_only_the_reported_key_of_issued_snapshots(client, db_session, test_project):
    a = _host(db_session, test_project, "10.60.0.1")
    _finding(db_session, test_project, "First", "high", hosts=[a], description="x" * 5000)
    _issue(client, test_project, _create(client, test_project)["id"])
    _finding(db_session, test_project, "Second", "low", hosts=[a])
    rid = _create(client, test_project)["id"]
    report = db_session.get(Report, rid)
    with _Statements() as seen:
        dataset, _, _ = ClientReportService(db_session).build(report)
    # The ledger still knows the first report's reference.
    assert [f["ref"] for f in dataset["findings"]] == ["F-01", "F-02"]
    if _postgres(db_session):
        whole = [s for s in seen.sql if "reports.snapshot" in s and "->" not in s]
        assert whole == [], "a build selected a whole snapshot"
        assert any("reports.snapshot ->" in s for s in seen.sql)


def _images(db_session, project, user, count):
    """``count`` findings, each with a marked and an unmarked image on its
    own comment; the first also has a source-note thread with one."""
    host = _host(db_session, project, "10.61.0.1")
    root = Annotation(host_id=host.id, user_id=user.id, body="proof", note_type="observation")
    db_session.add(root)
    db_session.flush()
    out = []
    for i in range(count):
        f = _finding(db_session, project, f"Finding {i}", hosts=[host],
                     evidence_annotation_id=root.id if i == 0 else None)
        comment = Annotation(finding_id=f.id, user_id=user.id, body="more")
        db_session.add(comment)
        db_session.flush()
        for name, marked in ((f"marked-{i}.png", True), (f"unmarked-{i}.png", False)):
            db_session.add(NoteAttachment(
                annotation_id=comment.id, project_id=project.id, filename=name,
                content_type="image/png", size_bytes=1, storage_path=f"{comment.id}/{name}",
                include_in_report=marked,
            ))
        out.append(f)
    db_session.add(NoteAttachment(
        annotation_id=root.id, project_id=project.id, filename="thread.png", content_type="image/png",
        size_bytes=1, storage_path=f"{root.id}/thread.png", include_in_report=False,
    ))
    db_session.commit()
    return out


def test_the_image_lookup_is_one_statement_and_the_mark_is_one_argument(db_session, test_project, test_user):
    findings = _images(db_session, test_project, test_user, 6)
    pairs = [(f.id, f.evidence_annotation_id) for f in findings]
    with _Statements() as seen:
        every = crs.finding_image_attachments(db_session, pairs, marked_only=False)
    assert len(seen.sql) == 1
    names = {fid: [att.filename for att, _ in rows] for fid, rows in every.items()}
    assert names[findings[0].id] == ["marked-0.png", "unmarked-0.png", "thread.png"]
    assert names[findings[3].id] == ["marked-3.png", "unmarked-3.png"]
    marked = crs.finding_image_attachments(db_session, pairs, marked_only=True)
    assert [att.filename for att, _ in marked[findings[0].id]] == ["marked-0.png"]


def test_the_drafter_reads_its_captions_in_one_statement(db_session, test_project, test_user):
    from app.services.report_draft_service import ReportDraftService
    findings = _images(db_session, test_project, test_user, 6)
    rows = [{"id": f.id, "evidence_annotation_id": f.evidence_annotation_id} for f in findings]
    with _Statements() as seen:
        captions = ReportDraftService(db_session, test_user)._evidence_image_captions(rows)
    assert len(seen.sql) == 1            # was two per finding
    # Unchanged: every attached image, marked for the report or not.
    assert captions[findings[1].id] == ["marked-1.png", "unmarked-1.png"]
    assert "thread.png" in captions[findings[0].id]


def test_the_host_report_reads_its_images_in_one_statement(db_session, test_project, test_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    findings = _images(db_session, test_project, test_user, 6)
    for att in db_session.query(NoteAttachment).all():
        target = tmp_path / "note_attachments" / att.storage_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
    rows = [{"id": f.id, "evidence_annotation_id": f.evidence_annotation_id} for f in findings]
    gen = ReportGenerator(db_session, test_user, project_id=test_project.id)
    with _Statements() as seen:
        images = gen._finding_evidence_images(rows)
    assert len(seen.sql) == 1            # was two per finding: 12
    # The host report keeps every attached image (its mark filter is parked).
    assert [caption for _uri, caption in images[findings[2].id]] == ["marked-2.png", "unmarked-2.png"]
    assert [caption for _uri, caption in images[findings[0].id]] == ["marked-0.png", "unmarked-0.png", "thread.png"]
    assert images[findings[0].id][0][0].startswith("data:image/png;base64,")


# --- R17: changing a report job --------------------------------------------------

def _job(db, project_id, status, *, report_type="comprehensive", requested_by_id=None, **over):
    values = {"format": "json", "filters": {}, **over}
    job = ReportJob(
        project_id=project_id, report_type=report_type, status=status,
        requested_by_id=requested_by_id, **values,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.mark.parametrize("action,status", [("cancel", "queued"), ("retry", "failed"), ("dismiss", "completed")])
def test_an_auditor_cannot_change_someone_elses_job_and_an_analyst_can(
    client, db_session, test_project, test_user, act_as, action, status,
):
    auditor = _member(db_session, test_project, 501, "aud", ProjectRole.AUDITOR)
    analyst = _member(db_session, test_project, 502, "ana", ProjectRole.ANALYST)
    job = _job(db_session, test_project.id, status, requested_by_id=test_user.id)
    url = f"/api/v1/projects/{test_project.id}/reports/jobs/{job.id}/{action}"

    act_as(auditor)
    refused = client.post(url)
    assert refused.status_code == 403, refused.text
    # Reading stays at the auditor floor.
    assert client.get(f"/api/v1/projects/{test_project.id}/reports/jobs/{job.id}").status_code == 200
    db_session.expire_all()
    assert db_session.get(ReportJob, job.id).status == status

    act_as(analyst)
    assert client.post(url).status_code == 200


def test_an_auditor_still_manages_the_export_they_requested(client, db_session, test_project, act_as):
    auditor = _member(db_session, test_project, 501, "aud", ProjectRole.AUDITOR)
    job = _job(db_session, test_project.id, "queued", requested_by_id=auditor.id)
    act_as(auditor)
    res = client.post(f"/api/v1/projects/{test_project.id}/reports/jobs/{job.id}/cancel")
    assert res.status_code == 200 and res.json()["status"] == "cancelled"


@pytest.mark.parametrize("action,status", [("cancel", "queued"), ("retry", "failed"), ("dismiss", "completed")])
def test_a_client_report_job_is_refused_here_for_everyone(client, db_session, test_project, test_user, action, status):
    """An auditor could cancel a queued issue render: the report stayed
    PENDING with no job.  Even an admin goes through the Reports page."""
    job = _job(db_session, test_project.id, status, report_type="client",
               requested_by_id=test_user.id, format="report-issue", filters={"report_id": 1})
    res = client.post(f"/api/v1/projects/{test_project.id}/reports/jobs/{job.id}/{action}")
    assert res.status_code == 409, res.text
    assert "Reports page" in res.json()["detail"]
    db_session.expire_all()
    row = db_session.get(ReportJob, job.id)
    assert row.status == status and row.dismissed_at is None
    assert client.post(f"/api/v1/projects/{test_project.id}/reports/jobs/999999/{action}").status_code == 404


def test_the_reports_page_still_recovers_a_failed_issue_render(client, db_session, test_project):
    """What the job routes now refuse has its own route."""
    issued = _issue(client, test_project, _create(client, test_project)["id"])
    report = db_session.get(Report, issued["id"])
    report.render_status = "failed"
    for job in db_session.query(ReportJob).filter(ReportJob.report_type == "client"):
        job.status = "failed"
    db_session.commit()
    again = client.post(f"{_base(test_project)}/{issued['id']}/render")
    assert again.status_code == 200 and again.json()["render_status"] == "pending"


# --- N2: what the exports say about notes -----------------------------------------

def test_the_inventory_has_one_notes_column_and_rows_match_the_header(db_session, test_project, test_user):
    header = ReportGenerator.INVENTORY_CSV_HEADER
    assert "Open Notes" not in header and header.count("Notes") == 1
    host = _host(db_session, test_project, "10.62.0.1")
    for body in ("one", "two"):
        db_session.add(Annotation(host_id=host.id, user_id=test_user.id, body=body, note_type="observation"))
    db_session.commit()
    gen = ReportGenerator(db_session, test_user, project_id=test_project.id)
    rows = list(csv.reader(io.StringIO("".join(gen.iter_inventory_csv({})))))
    assert rows[0] == header
    assert len(rows) == 2 and len(rows[1]) == len(header)
    row = dict(zip(header, rows[1]))
    assert row["IP Address"] == "10.62.0.1" and row["Notes"] == "2"


def test_the_export_schema_advertises_no_note_status(db_session, test_user, test_project):
    gen = ReportGenerator(db_session, test_user, project_id=test_project.id)
    schema = gen._build_schema_reference()
    assert "note_status" not in schema["enums"]
    assert "note_status" not in json.dumps(schema)
    summary = gen._dossier_summary.__code__.co_consts
    assert "open_notes" not in summary


# --- N5: CVSS, validators, one formula rule ---------------------------------------

def test_a_cvss2_vector_without_impact_scores_zero_never_minus_zero():
    for vector in ("AV:L/AC:H/Au:M/C:N/I:N/A:N", "AV:N/AC:L/Au:N/C:N/I:N/A:N"):
        score = cvss_service.score_vector(vector)[1]
        assert score == 0.0 and math.copysign(1.0, score) == 1.0 and str(score) == "0.0"
    assert cvss_service.normalize_cvss("AV:L/AC:H/Au:M/C:N/I:N/A:N", None) == ("AV:L/AC:H/Au:M/C:N/I:N/A:N", 0.0)
    assert cvss_service.score_vector("AV:N/AC:L/Au:N/C:C/I:C/A:C")[1] == 10.0


def test_a_cvss3_vector_takes_the_standards_metrics_and_no_others():
    base = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert cvss_service.score_vector(base)[1] == 9.8
    # Temporal and environmental metrics are CVSS 3.x: accepted, base score unchanged.
    full = base + "/E:P/RL:O/RC:C/CR:H/IR:M/AR:L/MAV:A/MAC:H/MPR:L/MUI:R/MS:C/MC:N/MI:L/MA:X"
    assert cvss_service.score_vector(full) == (full, 9.8)
    for bad, message in (
        (base + "/ZZ:9", "ZZ is not a CVSS 3.1 metric"),
        (base + "/Au:N", "Au is not a CVSS 3.1 metric"),
        (base + "/E:Q", "E:Q is not a CVSS 3.1 value"),
        (base.replace("3.1", "3.0") + "/VC:H", "VC is not a CVSS 3.0 metric"),
    ):
        with pytest.raises(cvss_service.CvssError) as exc:
            cvss_service.score_vector(bad)
        assert message in str(exc.value)
    # Seeding from a scanner row drops the unreadable vector and keeps its score.
    assert cvss_service.normalize_cvss(base + "/ZZ:9", 7.5, strict=False) == (None, 7.5)


def test_the_render_validators_refuse_a_trailing_newline():
    for bad in ("evidence/1.png\n", "evidence/1.png\nx"):
        with pytest.raises(quarto_render.RenderError):
            quarto_render.image({"file": bad})
    with pytest.raises(quarto_render.RenderError):
        quarto_render.image({"file": "evidence/1.png"}, width="6in\n")
    with pytest.raises(quarto_render.RenderError):
        quarto_render.plain("2026-10-01\n")
    with pytest.raises(quarto_render.RenderError):
        quarto_render.md("executive_summary\n")
    with pytest.raises(quarto_render.TemplateAssetError):
        quarto_render.template_assets(None, {"assets": [{"id": "logo\n", "path": "img/logo.png"}]})
    assert quarto_render.plain("2026-10-01") == "2026-10-01"


def test_the_scope_file_uses_the_one_formula_rule():
    assert report_scope._cell is csv_utils.csv_safe
    assert not hasattr(report_scope, "_FORMULA_START")
    body = report_scope.scope_csv({"subnets": [{"cidr": "10.0.0.0/8", "site": "=HYPERLINK(1)", "description": "\tx"}],
                                   "domains": [{"domain": "@x.example", "include_subdomains": False}]}).decode("utf-8")
    assert "'=HYPERLINK(1)" in body and "'@x.example" in body and "'\tx" in body


# --- B8: how a finding was confirmed ----------------------------------------------

def _record(db_session, project, finding, host, *, outcome="finding", **kw):
    values = dict(
        project_id=project.id, host_id=host.id, finding_id=finding.id if finding else None,
        tool="nmap", command="nmap -p445 --script smb-vuln-ms17-010 10.63.0.1", outcome=outcome,
        summary="The service is vulnerable.", raw_output_preview="445/tcp open\nVULNERABLE",
        raw_output_bytes=len("445/tcp open\nVULNERABLE"),
        executed_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
    )
    values.update(kw)
    rec = EvidenceRecord(**values)
    db_session.add(rec)
    db_session.commit()
    return rec


def test_a_findings_test_results_are_in_the_report_data(client, db_session, test_project, test_user):
    a, b, c = (_host(db_session, test_project, ip) for ip in ("10.63.0.1", "10.63.0.2", "10.63.0.3"))
    f = _finding(db_session, test_project, "SMB RCE", "critical", hosts=[a, (b, "false_positive")])
    _finding(db_session, test_project, "Unrelated", "low", hosts=[a])
    session = AgentSession(workflow="project", project_id=test_project.id, started_by_id=test_user.id)
    db_session.add(session)
    db_session.flush()
    shown = _record(db_session, test_project, f, a, recorded_by_user_id=test_user.id)
    by_agent = _record(
        db_session, test_project, f, a, tool="nxc", agent_session_id=session.id,
        command="nxc smb 10.63.0.1 -u '' -p ''", executed_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        raw_output_preview="\x1b[32m[+]\x1b[0m 10.63.0.1 \x0bpwned\r\nline two\x07", raw_output_bytes=999_999,
    )
    _record(db_session, test_project, f, a, outcome="no_finding")       # not a confirmation
    _record(db_session, test_project, f, a, outcome="inconclusive")
    _record(db_session, test_project, f, b)                             # a false-positive endpoint
    _record(db_session, test_project, f, c)                             # a host the finding is not on
    _record(db_session, test_project, None, a)                          # linked to no finding

    rid = _create(client, test_project)["id"]
    dataset, reported, summary = _build(db_session, rid)
    by_title = {x["title"]: x for x in dataset["findings"]}
    entries = by_title["SMB RCE"]["confirmations"]
    assert [e["id"] for e in entries] == [shown.id, by_agent.id]          # in the order they were run
    first, second = entries
    index = dataset["findings"].index(by_title["SMB RCE"])
    assert first["_path"] == f"findings.{index}.confirmations.0"
    assert first == {
        "_path": first["_path"], "id": shown.id, "tool": "nmap", "host": "10.63.0.1", "outcome": "finding",
        "summary": "The service is vulnerable.",
        "command": "nmap -p445 --script smb-vuln-ms17-010 10.63.0.1",
        "output": "445/tcp open\nVULNERABLE", "output_truncated": False,
        "executed_at": "2026-09-18T10:00:00+00:00", "date": "2026-09-18",
        "by": "Test Admin", "by_agent": False,
    }
    # Terminal colour codes and control characters never reach the report;
    # a preview shorter than the stored output is marked as an excerpt.
    assert second["output"] == "[+] 10.63.0.1 pwned\nline two" and second["output_truncated"] is True
    assert second["by"] == "Test Admin" and second["by_agent"] is True
    assert by_title["Unrelated"]["confirmations"] == [] and by_title["Unrelated"]["confirmations_omitted"] == 0
    assert summary["evidence_records"] == 2 and summary["agent_evidence_records"] == 1
    # The addendum's comparison state carries none of it.
    assert set(reported[str(f.id)]) == {"ref", "title", "severity", "status", "endpoints"}
    assert json.dumps(dataset)      # plain JSON, as the snapshot and data.json need


def test_tool_output_and_the_number_of_results_are_capped(client, db_session, test_project):
    a = _host(db_session, test_project, "10.64.0.1")
    f = _finding(db_session, test_project, "Noisy", hosts=[a])
    long_output = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    for i in range(crs.CONFIRMATIONS_PER_FINDING + 3):
        _record(db_session, test_project, f, a, command="c" * 5000, summary="s" * 5000,
                raw_output_preview=long_output, raw_output_bytes=len(long_output),
                executed_at=datetime(2026, 9, 1, i, tzinfo=timezone.utc))
    dataset, _, summary = _build(db_session, _create(client, test_project)["id"])
    item = dataset["findings"][0]
    assert len(item["confirmations"]) == crs.CONFIRMATIONS_PER_FINDING
    assert item["confirmations_omitted"] == 3
    entry = item["confirmations"][0]
    assert len(entry["command"]) == crs.CONFIRMATION_COMMAND_CHARS
    assert len(entry["summary"]) == crs.CONFIRMATION_SUMMARY_CHARS
    assert len(entry["output"]) <= crs.CONFIRMATION_OUTPUT_CHARS
    assert entry["output"].count("\n") < crs.CONFIRMATION_OUTPUT_LINES
    assert entry["output_truncated"] is True
    assert summary["evidence_records"] == crs.CONFIRMATIONS_PER_FINDING


def test_a_template_that_does_not_ask_gets_no_test_results(client, db_session, test_project):
    a = _host(db_session, test_project, "10.65.0.1")
    f = _finding(db_session, test_project, "SMB RCE", hosts=[a])
    _record(db_session, test_project, f, a)
    rid = _create(client, test_project, template="brief")["id"]
    with _Statements() as seen:
        dataset, _, summary = _build(db_session, rid)
    assert dataset["findings"][0]["confirmations"] == []
    assert summary["evidence_records"] == 0
    assert not any("evidence_records" in s for s in seen.sql)      # not even read
    # The same draft on the template that asks.
    assert client.patch(f"{_base(test_project)}/{rid}", json={"template": "pentest"}).status_code == 200
    assert len(_build(db_session, rid)[0]["findings"][0]["confirmations"]) == 1


def test_a_finding_without_systems_keeps_its_results(client, db_session, test_project):
    a = _host(db_session, test_project, "10.66.0.1")
    f = _finding(db_session, test_project, "Policy gap", "medium")
    _record(db_session, test_project, f, a)
    entry = _build(db_session, _create(client, test_project)["id"])[0]["findings"][0]["confirmations"][0]
    assert entry["host"] == "10.66.0.1" and entry["by"] is None


def test_issuing_freezes_the_test_results(client, db_session, test_project, test_user):
    a = _host(db_session, test_project, "10.67.0.1")
    f = _finding(db_session, test_project, "SMB RCE", hosts=[a])
    _record(db_session, test_project, f, a, recorded_by_user_id=test_user.id)
    issued = _issue(client, test_project, _create(client, test_project)["id"])
    # Afterwards the record is unlinked and another is added: the issued
    # report says what it said.
    db_session.query(EvidenceRecord).update({EvidenceRecord.finding_id: None})
    db_session.commit()
    db_session.expire_all()
    report = db_session.get(Report, issued["id"])
    dataset, summary = ClientReportService(db_session).content(report)
    frozen = dataset["findings"][0]["confirmations"]
    assert [e["command"] for e in frozen] == ["nmap -p445 --script smb-vuln-ms17-010 10.63.0.1"]
    assert summary["evidence_records"] == 1
    assert issued["summary"]["evidence_records"] == 1
    # A new draft reads the live state.
    assert _build(db_session, _create(client, test_project)["id"])[0]["findings"][0]["confirmations"] == []


def test_test_results_are_counted_and_kept_only_where_the_report_prints_them(
    client, db_session, test_project, template_dir,
):
    """Branch review S2 — an addendum lists a finding the client already has
    in one line (here: every finding that is not ``new``), so the template
    never reads its test results.  They used to be counted as printed
    (``evidence_records``) and frozen into the report's data all the same."""
    (template_dir / "pentest" / "report.qmd").write_text(
        "---\ntitle: x\n---\n"
        '<% for f in findings if f.change in (none, "new") %>\n'
        '<< md(f, "description") >>\n'
        '<% for c in f.get("confirmations") or [] %><< code(c, "command") >><% endfor %>\n'
        "<% endfor %>\n"
        "<% for f in findings %>| << f.ref >> | << f.confirmations|length >> |\n<% endfor %>\n"
    )
    a, b = (_host(db_session, test_project, ip) for ip in ("10.68.0.1", "10.68.0.2"))
    known = _finding(db_session, test_project, "Known", "high", hosts=[a], description="d")
    _record(db_session, test_project, known, a)
    full = _create(client, test_project)
    assert _build(db_session, full["id"])[2]["evidence_records"] == 1          # a full report prints it
    assert _build(db_session, full["id"])[2]["evidence_records_not_printed"] == 0
    _issue(client, test_project, full["id"])

    # Since the baseline: the known finding is on a further system (with two
    # more results there), and a new finding has one.
    db_session.add(FindingHost(finding_id=known.id, host_id=b.id, host_status="open"))
    db_session.commit()
    _record(db_session, test_project, known, b)
    _record(db_session, test_project, known, b, agent_session_id=None, summary="again")
    new = _finding(db_session, test_project, "New", "low", hosts=[b], description="d")
    _record(db_session, test_project, new, b)

    add = _create(client, test_project, kind="addendum")
    dataset, _, summary = _build(db_session, add["id"])
    by_title = {f["title"]: f for f in dataset["findings"]}
    assert by_title["Known"]["change"] == "new_hosts" and by_title["New"]["change"] == "new"
    assert len(by_title["New"]["confirmations"]) == 1
    # Asking how many there are is not printing them.
    assert by_title["Known"]["confirmations"] == [] and by_title["Known"]["confirmations_omitted"] == 0
    assert summary["evidence_records"] == 1 and summary["evidence_records_not_printed"] == 3
    # Issued, the report's data holds what it printed.
    issued = _issue(client, test_project, add["id"])
    assert issued["summary"]["evidence_records"] == 1
    frozen = db_session.get(Report, add["id"]).snapshot["dataset"]["findings"]
    assert sorted(len(f["confirmations"]) for f in frozen) == [0, 1]


def test_report_excerpt_cleans_and_cuts():
    assert crs.report_excerpt(None, max_chars=10) == (None, False)
    assert crs.report_excerpt(" \x00\x1b[0m\n", max_chars=10) == (None, False)
    assert crs.report_excerpt("a\r\nb\rc", max_chars=10) == ("a\nb\nc", False)
    assert crs.report_excerpt("abcdef", max_chars=3) == ("abc", True)
    assert crs.report_excerpt("1\n2\n3", max_chars=99, max_lines=2) == ("1\n2", True)
    assert crs.report_excerpt("tab\there", max_chars=99) == ("tab\there", False)


# --- B17: a re-rated finding in an addendum ----------------------------------------

def _addendum(client, db_session, project):
    add = _create(client, project, kind="addendum")
    dataset, reported, summary = _build(db_session, add["id"])
    return add, dataset, reported, summary


def test_an_addendum_shows_a_finding_whose_severity_changed(client, db_session, test_project):
    a, b = (_host(db_session, test_project, ip) for ip in ("10.70.0.1", "10.70.0.2"))
    tls = _finding(db_session, test_project, "Weak TLS", "medium", hosts=[a])
    same = _finding(db_session, test_project, "Unchanged", "high", hosts=[a])
    both = _finding(db_session, test_project, "Default creds", "low", hosts=[a])
    renamed = _finding(db_session, test_project, "Old title", "high", hosts=[a])
    fixed = _finding(db_session, test_project, "Patched", "high", hosts=[a])
    _issue(client, test_project, _create(client, test_project)["id"])

    tls.severity = "critical"                       # re-rated
    both.severity = "high"                          # re-rated AND on a further system
    db_session.add(FindingHost(finding_id=both.id, host_id=b.id, host_status="open"))
    renamed.title = "New title"                     # wording: not a change an addendum reports
    fixed.status = "remediated"                     # status: never (no remediation tracking)
    db_session.commit()

    add, dataset, reported, summary = _addendum(client, db_session, test_project)
    assert add["summary"]["delta"] == {
        "new_findings": 0, "findings_with_new_endpoints": 1,
        "findings_with_changed_severity": 2, "withdrawn": 0,
    }
    assert summary["delta"] == add["summary"]["delta"]
    shown = {f["title"]: f for f in dataset["findings"]}
    assert set(shown) == {"Weak TLS", "Default creds"}
    assert same.title not in shown
    rerated = shown["Weak TLS"]
    assert rerated["change"] == "severity_changed" and rerated["new_affected"] == []
    assert (rerated["previous_severity"], rerated["previous_severity_label"]) == ("medium", "Medium")
    assert (rerated["severity"], rerated["severity_label"]) == ("critical", "Critical")
    # Both at once: listed ONCE, as new systems, carrying the earlier severity.
    grown = shown["Default creds"]
    assert grown["change"] == "new_hosts" and [e["address"] for e in grown["new_affected"]] == ["10.70.0.2"]
    assert (grown["previous_severity"], grown["previous_severity_label"]) == ("low", "Low")
    assert [f["title"] for f in dataset["findings"]].count("Default creds") == 1
    assert dataset["delta"]["findings_with_changed_severity"] == 2
    # A re-rated finding is not a new one: the addendum's severity counts are of new findings.
    assert dataset["counts"]["total"] == 0
    # The next comparison is against the new rating.
    assert reported[str(tls.id)]["severity"] == "critical"

    _issue(client, test_project, add["id"])
    again = _create(client, test_project, kind="addendum")
    assert again["summary"]["delta"] == {
        "new_findings": 0, "findings_with_new_endpoints": 0,
        "findings_with_changed_severity": 0, "withdrawn": 0,
    }
    assert again["summary"]["findings_shown"] == 0


def test_a_baseline_without_a_stored_severity_and_a_case_difference_are_unchanged(client, db_session, test_project):
    a = _host(db_session, test_project, "10.71.0.1")
    old = _finding(db_session, test_project, "From an old snapshot", "high", hosts=[a])
    cased = _finding(db_session, test_project, "Label case", "medium", hosts=[a])
    issued = _issue(client, test_project, _create(client, test_project)["id"])
    report = db_session.get(Report, issued["id"])
    snapshot = json.loads(json.dumps(report.snapshot))
    del snapshot["reported"][str(old.id)]["severity"]          # frozen before severity was stored
    snapshot["reported"][str(cased.id)]["severity"] = " Medium "
    report.snapshot = snapshot
    old.severity = "critical"
    db_session.commit()

    add, dataset, _, _ = _addendum(client, db_session, test_project)
    assert dataset["findings"] == []
    assert add["summary"]["delta"]["findings_with_changed_severity"] == 0
    assert _severity("High") == _severity(" high ") == "high" and _severity(None) is None


def _severity(value):
    return crs._severity_value(value)


def test_a_full_report_carries_no_previous_severity(client, db_session, test_project):
    a = _host(db_session, test_project, "10.72.0.1")
    _finding(db_session, test_project, "One", "high", hosts=[a])
    dataset, _, summary = _build(db_session, _create(client, test_project)["id"])
    item = dataset["findings"][0]
    assert item["change"] is None and item["previous_severity"] is None and item["previous_severity_label"] is None
    assert dataset["delta"] is None and summary["delta"] is None
