"""Client reports (v2.380.0) — the findings-first deliverable, its history and
its addenda.  Mounted at ``/projects/{id}/client-reports``.

Reading (the list, a report, its files) is for auditors and above — the same
line as every other report and export.  Drafts are written by analysts.
Issuing is a project admin's sign-off: it freezes the report
(``ClientReportService.issue``), numbers it, and queues the render of its
files on the report worker.  A draft's preview is an ordinary report job
(``report_type='client'``), polled and downloaded through ``/reports/jobs``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, defer, selectinload

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.core.security import check_permissions, log_audit_event
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportKind, ReportProfile, ReportStatus, RenderStatus
from app.db.session import get_db
from app.schemas.client_reports import (
    EngagementSettings, PreviewRequest, ReportCreate, ReportFileOut, ReportListOut, ReportOut,
    ReportProfileBody, ReportProfileOut, ReportRef, ReportTemplateOut, ReportUpdate, Tester,
)
from app.schemas.schemas import ReportJobSchema
from app.services.client_report_service import ClientReportService, ReportStateError
from app.services.report_job_service import ReportJobService
from app.services import report_template_service as templates

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[
    Depends(get_current_user),
    Depends(require_project_role(ProjectRole.AUDITOR)),
])

CLIENT_REPORT_JOB = "client"


def _role(db: Session, project: Project, user: User) -> str:
    if user.role == UserRole.ADMIN:
        return ProjectRole.ADMIN.value
    role = (
        db.query(ProjectMembership.role)
        .filter(ProjectMembership.project_id == project.id, ProjectMembership.user_id == user.id)
        .scalar()
    )
    return getattr(role, "value", role) or ""


def _is(role: str, required: ProjectRole) -> bool:
    return bool(role) and check_permissions(role, required.value)


def _name(user) -> Optional[str]:
    return (user.full_name or user.username) if user is not None else None


def _ref(report: Optional[Report]) -> Optional[ReportRef]:
    if report is None:
        return None
    return ReportRef(
        id=report.id, number=report.number, title=report.title, status=report.status,
        issued_at=report.issued_at,
    )


def _serialize(db: Session, report: Report, role: str, *, with_summary: bool) -> ReportOut:
    superseded_by = None
    if report.status == ReportStatus.SUPERSEDED:
        superseded_by = (
            db.query(Report)
            .options(defer(Report.snapshot))
            .filter(Report.revision_of_id == report.id, Report.status != ReportStatus.DRAFT)
            .order_by(Report.number.desc())
            .first()
        )
    summary = ClientReportService(db).summary(report) if with_summary else None
    is_draft = report.status == ReportStatus.DRAFT
    return ReportOut(
        id=report.id, project_id=report.project_id, kind=report.kind, status=report.status,
        title=report.title, number=report.number, template=report.template,
        baseline=_ref(report.baseline), revision_of=_ref(report.revision_of),
        superseded_by=_ref(superseded_by),
        settings=EngagementSettings(**(report.settings or {})),
        executive_summary=report.executive_summary,
        template_fingerprint=report.template_fingerprint, quarto_version=report.quarto_version,
        render_status=report.render_status, render_error=report.render_error,
        files=[
            ReportFileOut(
                format=f.format, filename=f.filename, media_type=f.media_type,
                size_bytes=f.size_bytes, sha256=f.sha256, created_at=f.created_at,
            )
            for f in report.files
        ],
        created_by_name=_name(report.created_by), issued_by_name=_name(report.issued_by),
        created_at=report.created_at, updated_at=report.updated_at, issued_at=report.issued_at,
        summary=summary,
        can_edit=is_draft and _is(role, ProjectRole.ANALYST),
        can_issue=is_draft and _is(role, ProjectRole.ADMIN),
    )


def _load(db: Session, project: Project, report_id: int) -> Report:
    report = (
        db.query(Report)
        .options(selectinload(Report.files))
        .filter(Report.id == report_id, Report.project_id == project.id)
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _template_or_422(name: Optional[str]) -> templates.ReportTemplate:
    try:
        return templates.get_template(name)
    except templates.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _renderable_template_or_409(name: Optional[str]) -> templates.ReportTemplate:
    """The template, refusing (409) when an image its manifest marks REQUIRED
    is not installed — the message names each file and where it goes.  An
    optional image that is missing never blocks: the template leaves it out."""
    template = _template_or_422(name)
    missing = template.missing_required_assets()
    if missing:
        raise HTTPException(
            status_code=409,
            detail=templates.quarto_render.missing_assets_message(template.name, missing),
        )
    return template


def _issued_baseline(db: Session, project: Project, baseline_id: Optional[int]) -> Report:
    svc = ClientReportService(db)
    if baseline_id is None:
        baseline = svc.latest_issued(project.id)
        if baseline is None:
            raise HTTPException(
                status_code=409,
                detail="An addendum reports what changed since an issued report; this project has none yet.",
            )
        return baseline
    baseline = (
        db.query(Report)
        .filter(Report.id == baseline_id, Report.project_id == project.id)
        .first()
    )
    if baseline is None:
        raise HTTPException(status_code=404, detail="Baseline report not found")
    if baseline.status == ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An addendum is compared against an ISSUED report, not a draft.")
    if baseline.status == ReportStatus.SUPERSEDED:
        raise HTTPException(
            status_code=409,
            detail="That report was superseded by a revision; compare against the current issue.",
        )
    return baseline


def _enqueue(db: Session, *, project: Project, user: User, fmt: str, report_id: int,
             commit: bool = True):
    """Queue a client-report job.  ``commit=False`` stages it in the caller's
    transaction; the caller commits and then wakes the worker with
    ``ReportJobService().enqueue_job``."""
    service = ReportJobService()
    job = service.create_job(
        db, project_id=project.id, requested_by_id=user.id,
        format=fmt, report_type=CLIENT_REPORT_JOB, filters={"report_id": report_id},
        commit=commit,
    )
    if commit:
        service.enqueue_job(job.id, db=db)
    return job


def _live_issue_job(db: Session, report: Report) -> bool:
    """Whether an issue render for ``report`` is queued or running."""
    from app.db.models import ReportJob
    from app.services.client_report_render import ISSUE_FORMAT

    jobs = (
        db.query(ReportJob.filters)
        .filter(
            ReportJob.project_id == report.project_id,
            ReportJob.report_type == CLIENT_REPORT_JOB,
            ReportJob.format == ISSUE_FORMAT,
            ReportJob.status.in_(("queued", "processing")),
        )
        .all()
    )
    return any((filters or {}).get("report_id") == report.id for (filters,) in jobs)


# ---------------------------------------------------------------------------
# Templates and the project's report profile
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=List[ReportTemplateOut])
def list_report_templates():
    return [ReportTemplateOut(**t.as_dict()) for t in templates.list_templates()]


def _profile_out(db: Session, project_id: int, profile: Optional[ReportProfile]) -> ReportProfileOut:
    """The defaults a new draft starts from — with the project's analysts and
    admins as the team when none is saved, flagged as such."""
    team = list(profile.testers or []) if profile is not None else []
    from_project = not team
    if from_project:
        team = ClientReportService(db).project_team(project_id)
    if profile is None:
        return ReportProfileOut(
            template=templates.default_template_name(), testers=team, testers_from_project=True,
        )
    return ReportProfileOut(
        client_name=profile.client_name, classification=profile.classification,
        engagement_type=profile.engagement_type, testers=team,
        distribution=profile.distribution or [], system_description=profile.system_description,
        applications=profile.applications, thick_clients=profile.thick_clients,
        other_targets=profile.other_targets,
        template=profile.template or templates.default_template_name(),
        updated_at=profile.updated_at, testers_from_project=from_project,
    )


@router.get("/profile", response_model=ReportProfileOut)
def get_report_profile(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return _profile_out(db, project.id, ClientReportService(db).get_profile(project.id))


@router.get("/team", response_model=List[Tester])
def get_project_team(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The project's analysts and admins as an assessment team (name, role
    line, email) — what "Add the project's members" fills in."""
    return [Tester(**t) for t in ClientReportService(db).project_team(project.id)]


@router.put(
    "/profile", response_model=ReportProfileOut,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def put_report_profile(
    body: ReportProfileBody,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """The engagement details every NEW report starts from.  Existing drafts
    keep their own copy; issued reports never change."""
    if body.template:
        _template_or_422(body.template)
    profile = ClientReportService(db).get_profile(project.id)
    if profile is None:
        profile = ReportProfile(project_id=project.id)
        db.add(profile)
    data = body.model_dump()
    profile.client_name = data["client_name"]
    profile.classification = data["classification"]
    profile.engagement_type = data["engagement_type"]
    profile.testers = data["testers"]
    profile.distribution = data["distribution"]
    profile.system_description = data["system_description"]
    profile.applications = data["applications"]
    profile.thick_clients = data["thick_clients"]
    profile.other_targets = data["other_targets"]
    profile.template = data["template"]
    profile.updated_by_id = current_user.id
    db.commit()
    db.refresh(profile)
    return _profile_out(db, project.id, profile)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@router.get("", response_model=ReportListOut)
def list_reports(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Every report of the project: drafts first, then issued ones by number."""
    role = _role(db, project, current_user)
    rows = (
        db.query(Report)
        .options(defer(Report.snapshot), selectinload(Report.files))
        .filter(Report.project_id == project.id)
        .all()
    )
    rows.sort(key=lambda r: (r.status != ReportStatus.DRAFT, -(r.number or 0), -(r.id)))
    latest = ClientReportService(db).latest_issued(project.id)
    return ReportListOut(
        items=[_serialize(db, r, role, with_summary=False) for r in rows],
        latest_issued_id=latest.id if latest else None,
        can_create=_is(role, ProjectRole.ANALYST),
        can_issue=_is(role, ProjectRole.ADMIN),
    )


@router.post(
    "", response_model=ReportOut, status_code=201,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def create_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    svc = ClientReportService(db)
    profile = svc.get_profile(project.id)
    template_name = body.template or (profile.template if profile else None) or templates.default_template_name()
    if not template_name:
        raise HTTPException(status_code=422, detail="No report templates are installed (report-templates/ is empty or not mounted).")
    _template_or_422(template_name)
    baseline = None
    if body.kind == ReportKind.ADDENDUM:
        baseline = _issued_baseline(db, project, body.baseline_report_id)
    title = (body.title or "").strip()
    if not title:
        title = (
            f"Addendum to report #{baseline.number}" if baseline is not None
            else f"{project.name} — security assessment report"
        )
    # v2.404.0 — an addendum continues its baseline: same client, team and
    # distribution as issued (its "Summary of changes" is new text, so the
    # summary starts empty).  A new full report starts from the defaults.
    report = Report(
        project_id=project.id, kind=body.kind, status=ReportStatus.DRAFT, title=title[:255],
        template=template_name, baseline_report_id=baseline.id if baseline else None,
        settings=(
            svc.settings_for_addendum(baseline) if baseline is not None
            else svc.settings_from_profile(project.id)
        ),
        created_by_id=current_user.id,
    )
    db.add(report)
    db.commit()
    return _serialize(db, _load(db, project, report.id), _role(db, project, current_user), with_summary=True)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True)


@router.patch(
    "/{report_id}", response_model=ReportOut,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def update_report(
    report_id: int,
    body: ReportUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    report = _load(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report cannot be changed; start a revision instead.")
    sent = body.model_fields_set
    if "title" in sent:
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="A report needs a title.")
        report.title = title[:255]
    if "template" in sent and body.template:
        _template_or_422(body.template)
        report.template = body.template
    if "baseline_report_id" in sent:
        if report.kind != ReportKind.ADDENDUM:
            raise HTTPException(status_code=422, detail="Only an addendum has a baseline.")
        report.baseline_report_id = _issued_baseline(db, project, body.baseline_report_id).id
    if "executive_summary" in sent:
        report.executive_summary = (body.executive_summary or "").strip() or None
    if "settings" in sent and body.settings is not None:
        report.settings = body.settings.model_dump()
    db.commit()
    db.expire_all()
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True)


@router.delete(
    "/{report_id}", status_code=204,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def delete_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Discard a DRAFT (its creator or a project admin).  Issued reports are
    the record and stay."""
    report = _load(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report is part of the record and cannot be deleted.")
    role = _role(db, project, current_user)
    if report.created_by_id not in (None, current_user.id) and not _is(role, ProjectRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only the person who started this draft or a project admin can discard it.")
    db.delete(report)
    db.commit()
    return Response(status_code=204)


@router.post(
    "/{report_id}/preview", response_model=ReportJobSchema, status_code=202,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def preview_report(
    report_id: int,
    body: PreviewRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Render a DRAFT from the live findings, as one format.  Returns a report
    job: poll ``/reports/jobs/{id}`` and download from there."""
    report = _load(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report has its files already.")
    template = _renderable_template_or_409(report.template)
    if body.format not in template.formats:
        raise HTTPException(status_code=422, detail=f"The '{template.name}' template does not produce {body.format}.")
    return _enqueue(db, project=project, user=current_user, fmt=f"report-{body.format}", report_id=report.id)


@router.post(
    "/{report_id}/issue", response_model=ReportOut,
    dependencies=[Depends(require_project_role(ProjectRole.ADMIN))],
)
def issue_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """A project admin's sign-off: freeze what the report says, number it, and
    render its files.  After this the report never changes."""
    report = _load(db, project, report_id)
    template = _renderable_template_or_409(report.template)
    try:
        report = ClientReportService(db).issue(
            report.id, project.id, user_id=current_user.id,
            fingerprint=templates.fingerprint(template),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Report not found")
    except ReportStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    # The issue, its audit row and its render job commit TOGETHER.  They used
    # to be three commits (log_audit_event and create_job each committed): a
    # failure after the first left a PENDING report with no job, which
    # nothing could recover (review 2026-09-23 C4).
    log_audit_event(
        db, user_id=current_user.id, action="report_issued", resource_type="report",
        resource_id=str(report.id),
        details={
            "project_id": project.id, "number": report.number, "kind": report.kind,
            "title": report.title, "template": report.template,
            "template_fingerprint": report.template_fingerprint,
            "revision_of_id": report.revision_of_id, "baseline_report_id": report.baseline_report_id,
        },
        commit=False,
    )
    job = _enqueue(db, project=project, user=current_user, fmt="report-issue", report_id=report.id,
                   commit=False)
    db.commit()
    ReportJobService().enqueue_job(job.id, db=db)
    db.expire_all()
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True)


@router.post(
    "/{report_id}/render", response_model=ReportOut,
    dependencies=[Depends(require_project_role(ProjectRole.ADMIN))],
)
def rerender_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Render an issued report's files again from its frozen data — after a
    failed render.  The data is the snapshot; only the files are rebuilt.

    Only when there is no good set of files and nothing rendering: a FAILED
    render, or a PENDING one with no live job (a render lost before
    v2.390.4).  Rendered files are never replaced — an issued report does not
    change (review 2026-09-23 C4)."""
    _load(db, project, report_id)
    # Check-and-enqueue under the row lock: two retries (or a retry racing
    # the worker's publication) must not both pass the checks below.
    report = (
        db.query(Report).filter(Report.id == report_id)
        .with_for_update().populate_existing().one()
    )
    if report.status == ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="A draft is previewed, not rendered.")
    if report.render_status == RenderStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail="This report's files are rendered, and an issued report does not change. "
                   "Revise it to issue a corrected version.",
        )
    if report.render_status == RenderStatus.PENDING and _live_issue_job(db, report):
        raise HTTPException(status_code=409, detail="The files are being rendered.")
    _renderable_template_or_409(report.template)
    report.render_status = RenderStatus.PENDING
    report.render_error = None
    job = _enqueue(db, project=project, user=current_user, fmt="report-issue", report_id=report.id,
                   commit=False)
    db.commit()
    ReportJobService().enqueue_job(job.id, db=db)
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True)


@router.post(
    "/{report_id}/revise", response_model=ReportOut, status_code=201,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def revise_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Start a correction of an issued report: a new draft with the same
    engagement details and summary.  Issuing it supersedes the original."""
    original = _load(db, project, report_id)
    if original.status != ReportStatus.ISSUED:
        raise HTTPException(status_code=409, detail="Only the current issue of a report can be revised.")
    draft = Report(
        project_id=project.id, kind=original.kind, status=ReportStatus.DRAFT,
        title=original.title, template=original.template,
        baseline_report_id=original.baseline_report_id, revision_of_id=original.id,
        settings=ClientReportService.settings_from_issued(original),
        executive_summary=original.executive_summary,
        created_by_id=current_user.id,
    )
    db.add(draft)
    db.commit()
    return _serialize(db, _load(db, project, draft.id), _role(db, project, current_user), with_summary=True)


@router.get("/{report_id}/files/{fmt}")
def download_report_file(
    report_id: int,
    fmt: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    report = _load(db, project, report_id)
    record = next((f for f in report.files if f.format == fmt), None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"This report has no {fmt} file.")
    root = Path(settings.REPORT_FILES_DIR).resolve()
    try:
        target = (root / record.storage_path).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Report file path invalid")
    if not target.is_file():
        raise HTTPException(status_code=410, detail="The file is missing from report storage.")
    return FileResponse(path=str(target), media_type=record.media_type, filename=record.filename)
