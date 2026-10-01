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
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, defer, selectinload

from app.api.deps import get_current_project, require_project_role
from app.api.deps import get_current_user
from app.core.security import log_audit_event
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportKind, ReportProfile, ReportStatus, RenderStatus
from app.db.session import get_db
from app.schemas.client_reports import (
    PreviewRequest, ReportCreate, ReportListOut, ReportOut,
    ReportProfileBody, ReportProfileOut, ReportTemplateAssetChangeOut, ReportTemplateOut,
    ReportTemplateProblemOut, ReportUpdate, Tester,
)
from app.schemas.schemas import ReportJobSchema
from app.services.client_report_service import (
    ClientReportService, ReportStateError, discard_report_images, stored_file_path,
)
from app.services.client_report_views import (
    load_report,
    role_allows as _is,
    serialize_report as _serialize,
)
from app.services.report_job_service import ReportJobService
from app.services import report_scope
from app.services import report_template_service as templates
from app.services import template_asset_store as asset_store_module

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


# The serializer and the loader are ``client_report_views`` (a service since
# the 2026-10-01 review, B4): the agents' report reads use the same two.
def _load(db: Session, project: Project, report_id: int) -> Report:
    return load_report(db, project.id, report_id)


def _load_for_change(db: Session, project: Project, report_id: int) -> Report:
    """The report under ``FOR UPDATE``, re-read — for a route that checks the
    status and then writes (review 2026-10-01 R13).

    ``issue()`` locks the row; a PATCH or DELETE that had read ``draft``
    without a lock then waited behind it and applied AFTER the issue
    committed: an issued report's title, settings or template no longer
    matched its snapshot, or a numbered report was deleted.  With the lock the
    route waits for the issue and then sees ``issued``.  ``populate_existing``
    because a row already in the session would be checked at its pre-lock
    status.  The files load by ``selectinload`` — a second statement, so the
    lock covers the report row only (a joined load under FOR UPDATE fails on
    Postgres)."""
    report = (
        db.query(Report)
        .options(selectinload(Report.files))
        .filter(Report.id == report_id, Report.project_id == project.id)
        .with_for_update(of=Report)
        .populate_existing()
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


@router.get("/templates/problems", response_model=List[ReportTemplateProblemOut])
def list_report_template_problems():
    """Folders under report-templates/ that are not offered, and why — so a
    template with a mistake says so instead of simply not appearing."""
    return [ReportTemplateProblemOut(**p) for p in templates.template_problems()]


# --- a template's own images, uploaded (v2.431.0) -------------------------------
# Templates are shared by every project, so an upload is instance-wide
# branding: global administrators only, audited.  Storage and validation:
# app/services/template_asset_store.py.

def _declared_asset(name: str, asset_id: str) -> tuple:
    template = _template_or_422(name)
    asset = next((a for a in template.assets if a["id"] == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"The '{name}' template declares no asset '{asset_id}'.")
    return template, asset


def _require_global_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Template files are shared by every project; only a global administrator can change them.",
        )


@router.put("/templates/{name}/assets/{asset_id}", response_model=ReportTemplateAssetChangeOut)
async def upload_report_template_asset(
    name: str,
    asset_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upload the file a template expects (a logo, the title page image, the
    Word styles file).  It is used in place of a server-installed one from the
    next preview or issue on; issued reports keep theirs (the template
    fingerprint includes it, so an issued report refuses to re-render)."""
    _require_global_admin(current_user)
    _template, asset = _declared_asset(name, asset_id)
    limit = int(asset.get("max_bytes") or 0) or asset_store_module.MAX_DOCX_UNCOMPRESSED
    data = await file.read(limit + 1)  # every await BEFORE any database write
    try:
        _meta, warnings = asset_store_module.save(
            name, asset, data, uploaded_by=current_user.username, original_filename=file.filename,
        )
    except asset_store_module.AssetUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log_audit_event(
        db, user_id=current_user.id, action="report_template_asset_uploaded", resource_type="report_template",
        resource_id=name,
        details={"project_id": project.id, "template": name, "asset": asset_id, "sha256": _meta["sha256"],
                 "size": _meta["size"], "width": _meta["width"], "height": _meta["height"]},
    )
    return ReportTemplateAssetChangeOut(template=ReportTemplateOut(**_template_or_422(name).as_dict()), warnings=warnings)


@router.delete("/templates/{name}/assets/{asset_id}", response_model=ReportTemplateAssetChangeOut)
def remove_report_template_asset(
    name: str,
    asset_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Remove an uploaded file; the template falls back to a server-installed
    one, or goes without (a required file then blocks preview and issue)."""
    _require_global_admin(current_user)
    _declared_asset(name, asset_id)
    removed = asset_store_module.remove(name, asset_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Nothing was uploaded for this file.")
    log_audit_event(
        db, user_id=current_user.id, action="report_template_asset_removed", resource_type="report_template",
        resource_id=name,
        details={"project_id": project.id, "template": name, "asset": asset_id, "sha256": removed.get("sha256")},
    )
    return ReportTemplateAssetChangeOut(template=ReportTemplateOut(**_template_or_422(name).as_dict()))


_PREVIEW_MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


@router.get("/templates/{name}/assets/{asset_id}/preview")
def preview_report_template_asset(name: str, asset_id: str):
    """The image the render would use for this asset (the upload, else the
    server-installed file), for the Reports page's thumbnail.  Raster images
    only — never an SVG (script) or the Word styles file."""
    template, asset = _declared_asset(name, asset_id)
    media = _PREVIEW_MEDIA.get(asset.get("kind") or "")
    if media is None:
        raise HTTPException(status_code=404, detail="This file has no image preview.")
    uploaded = asset_store_module.overrides(name).get(asset_id)
    path = uploaded if uploaded is not None else (template.path / asset["path"] if asset.get("installed") else None)
    if path is None or not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="This file is not installed.")
    return FileResponse(
        path, media_type=media,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


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
    report = _load_for_change(db, project, report_id)
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
    report = _load_for_change(db, project, report_id)
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
    _load(db, project, report_id)

    def fingerprint(locked: Report) -> str:
        # Computed from the row ``issue()`` holds locked (review 2026-10-01
        # R13): read before the lock, a PATCH that changed the template in
        # between stamped the report with the OLD template's fingerprint, and
        # its issue render then refused it as "changed since issue".
        return templates.fingerprint(_renderable_template_or_409(locked.template))

    try:
        report = ClientReportService(db).issue(
            report_id, project.id, user_id=current_user.id, fingerprint=fingerprint,
        )
    except HTTPException:
        db.rollback()
        raise
    except LookupError:
        raise HTTPException(status_code=404, detail="Report not found")
    except ReportStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    # The issue, its audit row and its render job commit TOGETHER.  They used
    # to be three commits (log_audit_event and create_job each committed): a
    # failure after the first left a PENDING report with no job, which
    # nothing could recover (review 2026-09-23 C4).
    try:
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
    except Exception:
        # The issue did not happen: its copies of the evidence images
        # (``freeze_report_images``) must not outlive it.
        db.rollback()
        discard_report_images(project.id, report_id)
        raise
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
    return report_file_response(record)


@router.get("/{report_id}/scope.csv", summary="The report's complete scope as CSV (the file an over-cutoff report names)")
def download_report_scope(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
) -> Response:
    """v2.441.0 — the scope as the report states it: a draft's live scope,
    an issued report's frozen one, so its SHA-256 is the one the issued
    report prints.  Offered for any report; the report names it only when
    the scope is over its template's cutoff."""
    report = _load(db, project, report_id)
    return report_scope_response(ClientReportService(db), report)


def report_scope_response(service: ClientReportService, report: Report) -> Response:
    """The scope file download — shared with the agent's route."""
    dataset, summary = service.content(report)
    if dataset is None:
        raise HTTPException(status_code=409, detail=summary.get("error") or "This report cannot be built.")
    scope = dataset.get("scope") or {"subnets": [], "domains": []}
    name = ((scope.get("file") or {}).get("name")
            or report_scope.file_name(project_slug=None, number=report.number, report_id=report.id))
    return Response(
        content=report_scope.scope_csv(scope), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def report_file_response(record) -> FileResponse:
    """One stored report file as a download — shared with the agent's route."""
    try:
        target = stored_file_path(record.storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    return FileResponse(path=str(target), media_type=record.media_type, filename=record.filename)
