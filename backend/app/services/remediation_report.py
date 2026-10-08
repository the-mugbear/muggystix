"""The remediation list prepared for ONE contact (v2.463.0).

What a project admin hands the person who must fix things: the findings
assigned to them in this project.  It is rendered ON DEMAND through the Quarto
templates as a job on the report worker — never issued, never frozen, no
number, no addenda, and the file expires with its job like any export.

**It is printed in the penetration test report's format, from a template of
its own** (owner, 2026-10-08: use that format; do not change the report's own
template; its own, so admins restyle it like the others).  That template is
``report-templates/contact-report`` — ``"kind": "contact"`` in its
``template.json``, named by ``REMEDIATION_REPORT_TEMPLATE``.  It EXTENDS
``pentest`` (``"extends"``): it holds only the files that differ and takes
the rest from the report's template, whose folder is not changed.  It prints each
finding as the report does (description, impact, steps, how it was confirmed,
evidence, recommendation), with a short front part in place of the project
information, executive summary and scope, and each system's deadline in the
affected-systems table.

The data is ``ClientReportService.build`` of a report that is never stored, so
the written text and placed images are the client report's; this module keeps
the findings and systems that are the contact's and still work, and adds the
deadlines.  Nothing here writes a ``reports`` row.  Three things of a finding
are narrowed with its systems (``_build``): a reference (F-01…) is printed
only once an ISSUED report has used it; test results only for the systems
listed; and an image that no written section places only when every system of
the finding is this contact's.

A system is matched to its remediation row by address: a client report lists a
finding's systems by address, and an address is unique in a project.  When a
finding is on several ports of one host with different deadlines, the most
urgent is the one printed.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import worker_loop
from app.core.config import settings
from app.db.models import ReportJob
from app.db.models_project import Project
from app.db.models_remediation import RemediationEvent
from app.db.models_reports import Report, ReportKind, ReportStatus
from app.services import client_report_render, quarto_render
from app.services import remediation_policy as deadlines
from app.services import remediation_service
from app.services import report_template_service as templates



def contact_template() -> templates.ReportTemplate:
    """The template the list is printed with, or ``TemplateError`` when it is
    not installed or is not a contact's template."""
    template = templates.get_template(settings.REMEDIATION_REPORT_TEMPLATE)
    if template.kind != "contact":
        raise templates.TemplateError(
            f"'{template.name}' is a client-report template, not a contact's remediation list "
            '(its template.json has no "kind": "contact").')
    return template


#: Job format → the Quarto format it renders.
CONTACT_JOB_FORMATS = {"contact-html": "html", "contact-docx": "docx"}
#: ``report_jobs.report_type`` of these jobs.
REPORT_TYPE = "remediation"
#: The most rows one contact's list holds; beyond it the call is refused
#: rather than a document silently cut.
MAX_ROWS = 2000
#: What is work for the contact: every state but closed.
_LISTED = tuple(state for state in deadlines.STATES if state != "closed")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def deadline_text(row: dict) -> str:
    """A row's deadline in words — the page's Deadline cell, for print."""
    state, left = row["state"], row["days_left"]
    if state == "overdue":
        return f"{_plural(-left, 'day', 'days')} overdue"
    if state in ("due_soon", "on_track"):
        return "Due today" if left == 0 else f"Due in {_plural(left, 'day', 'days')}"
    return {"deferred": "Deferred", "no_deadline": "No deadline", "not_assigned": "Not assigned"}.get(state, "")


def timeline_text(policy: deadlines.Policy) -> str:
    """"Critical and High 30 days, Medium 90 days, …" — severities sharing a
    number are named together."""
    names = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Informational"}
    groups: list[tuple[Optional[int], list[str]]] = []
    for severity in deadlines.TIMELINE_SEVERITIES:
        days = policy.days.get(severity)
        if groups and groups[-1][0] == days:
            groups[-1][1].append(names[severity])
        else:
            groups.append((days, [names[severity]]))
    return ", ".join(
        f"{' and '.join(group)} {'no deadline' if days is None else _plural(days, 'day', 'days')}"
        for days, group in groups
    )


def contact_rows(db: Session, project_id: int, contact_email: str, *,
                 policy: Optional[deadlines.Policy] = None, today: Optional[date] = None) -> dict:
    """The contact's rows in this project that are still work, and how many
    are already closed.  ``404`` when the contact has nothing here."""
    listing = remediation_service.list_rows(
        db, [project_id], contact_email=contact_email, state=_LISTED, group="due",
        limit=MAX_ROWS + 1, policy=policy, today=today)
    if listing["total"] + listing["state_counts"]["closed"] == 0:
        raise HTTPException(404, "This contact has no finding assigned in this project.")
    if listing["total"] > MAX_ROWS:
        raise HTTPException(422, f"This contact has more than {MAX_ROWS} open findings on hosts; "
                                 "a list that long cannot be prepared as one document.")
    return listing


def build_dataset(db: Session, project_id: int, contact_email: str, *,
                  today: Optional[date] = None) -> dict:
    """The contact's list as a template dataset (see the module docstring)."""
    return _build(db, project_id, contact_email, today=today)[0]


def _build(db: Session, project_id: int, contact_email: str, *,
           today: Optional[date] = None) -> Tuple[dict, List[int]]:
    """``(dataset, the ids of the hosts the document lists)``."""
    from app.services.client_report_service import ClientReportService

    policy = deadlines.load(db)
    today = today or policy.today()
    email = contact_email.strip().lower()
    listing = contact_rows(db, project_id, email, policy=policy, today=today)
    rows = listing["items"]
    name = next((row["contact_name"] for row in rows if row["contact_name"]), None)
    project = db.get(Project, project_id)

    # A client report's dataset, from a report that is never stored, with the
    # project's own engagement details (client name, classification — the
    # title page and the page header print them).
    service = ClientReportService(db)
    details = dict(service.settings_from_profile(project_id) or {})
    transient = Report(
        project_id=project_id, template=contact_template().name, kind=ReportKind.FULL, status=ReportStatus.DRAFT,
        title=f"Remediation list — {name or email}", settings=details, executive_summary=None,
    )
    dataset, _, _ = service.build(transient)
    # A reference is a finding's for good only once a report has been ISSUED
    # with it.  Before that the dataset numbers findings in today's order, and
    # the next confirmed finding can take the number — so a contact is given
    # only references the client report has already used; the rest are blank.
    issued_refs = {fid for fid, entry in service._ledger(project_id).items() if entry.get("ref")}

    # (finding, address) → the row to print and its place in the list; rows
    # arrive most urgent first, so the first one seen for a system is the one
    # whose deadline is printed.
    mine: dict[tuple[int, str], tuple[int, dict]] = {}
    for order, row in enumerate(rows):
        mine.setdefault((row["finding_id"], row["ip_address"]), (order, row))
    # The contact's systems that are already closed: theirs too, though not
    # listed (what decides whether a finding is on anyone else's system).
    closed = remediation_service.list_rows(
        db, [project_id], contact_email=email, state=("closed",), group="due",
        limit=MAX_ROWS + 1, policy=policy, today=today)
    theirs = set(mine) | {(row["finding_id"], row["ip_address"]) for row in closed["items"]}
    closed_complete = closed["total"] <= len(closed["items"])

    findings = []
    images_withheld = 0
    for finding in dataset["findings"]:
        affected = []
        for endpoint in finding["affected"]:
            found = mine.get((finding["id"], endpoint["address"]))
            if found is None:
                continue
            order, row = found
            affected.append((order, {
                **endpoint,
                "deadline_state": row["state"], "deadline": deadline_text(row), "due_on": row["due_on"],
                "assigned_on": row["notified_on"], "team": row["team"],
            }))
        if not affected:
            continue
        # Each finding's systems most urgent first; the first one's deadline
        # is the finding's in the summary table.
        affected = [entry for _, entry in sorted(affected, key=lambda pair: pair[0])]
        index = len(findings)
        # A finding is on several contacts' systems, and what was run against
        # another contact's system — its address, the command, the output —
        # is not this contact's to read: only the test results on the systems
        # listed here.  The count of further results is the whole finding's,
        # so it is printed only when every result could have been this
        # contact's.
        listed = {entry["address"] for entry in affected}
        all_theirs = closed_complete and all(
            (finding["id"], endpoint["address"]) in theirs for endpoint in finding["affected"])
        confirmations = [entry for entry in finding.get("confirmations") or [] if entry.get("host") in listed]
        # Images are the finding's, not a system's.  One the author placed in
        # the finding's text is part of that text; one that is only attached
        # (the trailing evidence block) may show any of the finding's systems,
        # so it is printed only when all of them are this contact's.  An image
        # the document's template prints nowhere stays as it is: it is neither
        # shown nor withheld.
        images = finding.get("images") or []
        if all_theirs:
            evidence = list(finding.get("evidence") or [])
        else:
            unplaced = [img for img in images if not img.get("placed_in") and img.get("printed") is not False]
            images_withheld += len(unplaced)
            images = [img for img in images if img not in unplaced]
            evidence = []
        # Written text and code are looked up by data path: the paths follow
        # the finding to its new place in the list.
        findings.append({
            **finding, "_path": f"findings.{index}", "affected": affected, "affected_count": len(affected),
            "new_affected": [], "change": None, "deadline": affected[0]["deadline"],
            "ref": finding["ref"] if str(finding["id"]) in issued_refs else "",
            "images": images, "evidence": evidence,
            "confirmations": [{**entry, "_path": f"findings.{index}.confirmations.{n}"}
                              for n, entry in enumerate(confirmations)],
            "confirmations_omitted": finding.get("confirmations_omitted", 0) if all_theirs else 0,
        })
    dataset["findings"] = findings
    dataset["delta"] = None
    # The severity table counts what this document lists.
    by_severity = {severity: 0 for severity in dataset["severity_order"]}
    for finding in findings:
        if finding["severity"] in by_severity:
            by_severity[finding["severity"]] += 1
    dataset["counts"] = {**by_severity, "total": sum(by_severity.values())}

    counts = listing["state_counts"]
    not_listed = listing["total"] - sum(len(f["affected"]) for f in findings)
    dataset["executive_summary"] = None
    dataset["report"].update({
        "draft": False, "heading": (project.name if project else "") or "",
        "contact": {"name": name, "email": email}, "as_of": today.isoformat(),
    })
    dataset["remediation"] = {
        "overdue": counts["overdue"], "due_soon": counts["due_soon"], "open": listing["total"],
        "closed": counts["closed"], "due_soon_days": policy.due_soon_days, "timeline": timeline_text(policy),
        # Rows of the contact that the document cannot show: their finding is
        # not one a client report includes (still under investigation).
        "not_listed": not_listed,
        # Evidence images left out because their finding is also on a system
        # that is not this contact's (see above) — for whoever prepares the
        # document; the template does not print it.
        "images_withheld": images_withheld,
    }
    printed = {(finding["id"], entry["address"]) for finding in findings for entry in finding["affected"]}
    host_ids = sorted({row["host_id"] for key, (_, row) in mine.items() if key in printed})
    return dataset, host_ids


# --- the job -------------------------------------------------------------------

def enqueue(db: Session, project_id: int, body, who) -> ReportJob:
    """Queue the render.  ``409`` when the template cannot be used.  That a
    list was prepared is written on each host's timeline when the job
    COMPLETES (``record_prepared``): a render that fails prepared nothing."""
    from app.services.report_job_service import ReportJobService

    try:
        template = contact_template()
    except templates.TemplateError as exc:
        raise HTTPException(409, str(exc))
    fmt = CONTACT_JOB_FORMATS[body.format]
    if fmt not in template.formats:
        raise HTTPException(409, f"The '{template.name}' template does not produce {fmt}.")
    missing = template.missing_required_assets()
    if missing:
        raise HTTPException(409, quarto_render.missing_assets_message(template.name, missing))
    email = body.contact_email
    listing = contact_rows(db, project_id, email)
    if listing["total"] == 0:
        raise HTTPException(409, "Everything assigned to this contact has been reported fixed: "
                                 "there is nothing to list.")

    service = ReportJobService()
    filters = {"contact_email": email}
    session = getattr(who, "session", None)
    if session is not None:
        # Whose agent asked: the timeline entries name it.
        filters["agent_session_id"] = session.id
    job = service.create_job(
        db, project_id=project_id, requested_by_id=who.user_id, format=body.format,
        report_type=REPORT_TYPE, filters=filters,
    )
    service.enqueue_job(job.id, db=db)
    return job


def record_prepared(db: Session, job: ReportJob) -> int:
    """Write, on each host the document lists, that a list was prepared for
    the contact — called by the report worker in the transaction that marks
    the job completed, so the timeline says "prepared" exactly when there is
    a document.  Returns the number of entries added (not committed)."""
    filters = job.filters or {}
    email, fmt = filters.get("contact_email"), CONTACT_JOB_FORMATS.get(job.format)
    host_ids = filters.get("listed_host_ids")
    if job.report_type != REPORT_TYPE or not email or fmt is None or not isinstance(host_ids, list):
        return 0
    now = datetime.now(timezone.utc)
    for host_id in sorted(set(host_ids)):
        db.add(RemediationEvent(
            project_id=job.project_id, host_id=host_id, kind="report", new_value=email,
            body=f"Remediation list prepared ({fmt})", occurred_at=now, created_at=now,
            author_id=job.requested_by_id, agent_session_id=filters.get("agent_session_id"),
        ))
    return len(set(host_ids))


def job_for(db: Session, project_id: int, job_id: int) -> ReportJob:
    job = (db.query(ReportJob)
           .filter(ReportJob.id == job_id, ReportJob.project_id == project_id,
                   ReportJob.report_type == REPORT_TYPE).first())
    if job is None:
        raise HTTPException(404, "Report not found")
    return job


def serialize_job(job: ReportJob) -> dict:
    return {
        "id": job.id, "status": job.status, "format": job.format, "message": job.message,
        "error": job.last_error if job.status == "failed" else None,
        "filename": job.result_filename, "contact_email": (job.filters or {}).get("contact_email"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        # Evidence images the finished document left out because their
        # finding is also on a system that is not this contact's.
        "images_withheld": (job.filters or {}).get("images_withheld", 0) if job.status == "completed" else 0,
        "ready": job.status == "completed" and bool(job.result_path) and Path(job.result_path).is_file(),
    }


def run_job(db: Session, job: ReportJob) -> Tuple[bytes, str, str]:
    """Render one contact's list on the report worker: ``(bytes, media type,
    file name)``, stored by the worker as the job's artifact."""
    email = (job.filters or {}).get("contact_email")
    fmt = CONTACT_JOB_FORMATS.get(job.format)
    if not email or fmt is None:
        raise ValueError(f"Unsupported contact report job: {job.format!r}")
    if not deadlines.load(db).enabled:
        raise ValueError(deadlines.NOT_ENABLED)
    try:
        dataset, host_ids = _build(db, job.project_id, email)
    except HTTPException as exc:
        raise ValueError(str(exc.detail))
    template = contact_template()
    # ASCII only: a name may hold any letter ("jörg@…"), a file name here not.
    slug = client_report_render.file_slug(email.split("@")[0], "contact")[:40].strip("-") or "contact"
    with tempfile.TemporaryDirectory(prefix="bs-contact-") as tmp:
        files = quarto_render.render(
            template.path, template.entry, dataset, [fmt], Path(tmp),
            basename=f"remediation-{slug}-{dataset['report']['as_of']}",
            resolve_evidence=client_report_render.live_evidence_resolver(db, job.project_id),
            postprocess=template.postprocess, timeout=settings.REPORT_RENDER_TIMEOUT_SECONDS,
            asset_files=templates.asset_files(template),
            on_progress=worker_loop.touch_heartbeat,
        )
        if fmt not in files:
            raise ValueError(f"The '{template.name}' template does not produce {fmt}.")
        path = files[fmt]
        data = path.read_bytes()
    # For the completion (``record_prepared``) and the page: the hosts the
    # document lists, and how many images it withheld.  Saved with the job's
    # completion, or not at all.
    job.filters = {
        **(job.filters or {}),
        "listed_host_ids": host_ids,
        "images_withheld": dataset["remediation"].get("images_withheld", 0),
    }
    return data, quarto_render.FORMATS[fmt][2], path.name
