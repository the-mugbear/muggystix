"""Client reports (v2.380.0) — the findings-first deliverable, its history and
its addenda.  Mounted at ``/projects/{id}/client-reports``.

Reading (the list, a report, its files) is for auditors and above — the same
line as every other report and export.  Drafts are written by analysts.
Issuing is a project admin's sign-off: it freezes the report
(``ClientReportService.issue``), numbers it, and queues the render of its
files on the report worker.  A draft's preview is a report job
(``report_type='client'``), polled and downloaded through its report
(``/{id}/previews/{job}``); the generic ``/reports/jobs`` routes do not serve
it (``app/api/report_jobs.py``).
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, defer, selectinload

from app.api.deps import get_current_project, require_project_role
from app.api.deps import get_current_user
from app.api.downloads import attachment
from app.api.report_jobs import job_file_response, job_or_404
from app.core.security import log_audit_event
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportKind, ReportProfile, ReportStatus, RenderStatus
from app.db.session import get_db
from app.schemas.client_reports import (
    PreviewRequest, ReportCreate, ReportInputsOut, ReportListOut, ReportOut,
    ReportProfileOut, ReportProfilePatch, ReportPromote,
    ReportTemplateAssetChangeOut, ReportTemplateOut,
    ReportTemplateProblemOut, ReportTextImageOut, ReportUpdate, Tester,
)
from app.schemas.schemas import ReportJobSchema
from app.services.client_report_service import (
    ClientReportService, ReportStateError, discard_report_copies, issued_scope_file, stored_file_path,
)
from app.services.client_report_views import (
    load_report,
    may_discard,
    role_allows as _is,
    serialize_report as _serialize,
)
from app.services.report_job_service import ReportJobService
from app.services import client_report_render, report_defaults, report_images, report_scope
from app.services.note_attachment_service import store_image_attachment
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


def _template_or_422(name: Optional[str], client: bool = False) -> templates.ReportTemplate:
    """``client=True`` where a client report takes the template (a project's
    default, a new draft, a draft's change): a contact's remediation list is
    not one.  The template FILES routes take any kind."""
    try:
        return templates.client_template(name) if client else templates.get_template(name)
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
    """Every template, each with its ``kind``: the page's choosers offer the
    ``client`` ones; its template-files section lists them all, so an admin
    restyles the contact's remediation list like any other."""
    return [ReportTemplateOut(**t.as_dict()) for t in templates.list_templates(kind=None)]


@router.get("/templates/problems", response_model=List[ReportTemplateProblemOut])
def list_report_template_problems(current_user: User = Depends(get_current_user)):
    """Folders under report-templates/ that are not offered, and why — so a
    template with a mistake says so instead of simply not appearing.  Global
    administrators only: they are who can put a folder on the server, and a
    problem's detail can name a path there."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only a global administrator is told which template folders could not be offered.",
        )
    return [ReportTemplateProblemOut(**p) for p in templates.template_problems()]


# --- a template's own images, uploaded (v2.431.0) -------------------------------
# Templates are shared by every project, so an upload is instance-wide
# branding: global administrators only, audited.  Storage and validation:
# app/services/template_asset_store.py.

def _declared_asset(name: str, asset_id: str) -> tuple:
    """The template and its declared asset.  The path must name the template
    exactly — ``get_template`` forgives surrounding space, a folder on disk
    does not — and the routes go on with ``template.name``, never the path
    parameter (review 2026-10-23, R-RPT3: ``pentest%0A`` made an upload
    folder named with a newline)."""
    template = _template_or_422(name)
    if template.name != name:
        raise HTTPException(status_code=404, detail="There is no template of that name.")
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
    next preview or issue on; issued reports keep theirs (each renders from
    its own copy of the template, made when it was issued)."""
    _require_global_admin(current_user)
    template, asset = _declared_asset(name, asset_id)
    name = template.name
    limit = int(asset.get("max_bytes") or 0) or asset_store_module.MAX_DOCX_UNCOMPRESSED
    data = await file.read(limit + 1)  # every await BEFORE any database write

    def store() -> ReportTemplateAssetChangeOut:
        # The image checks, the file write under its lock and the audit
        # commit block: on a worker thread, never the event loop (review
        # 2026-10-16, N5 — the loop serves /health too).
        try:
            meta, warnings = asset_store_module.save(
                name, asset, data, uploaded_by=current_user.username, original_filename=file.filename,
            )
        except asset_store_module.AssetUploadError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        log_audit_event(
            db, user_id=current_user.id, action="report_template_asset_uploaded", resource_type="report_template",
            resource_id=name,
            details={"project_id": project.id, "template": name, "asset": asset_id, "sha256": meta["sha256"],
                     "size": meta["size"], "width": meta["width"], "height": meta["height"]},
        )
        return ReportTemplateAssetChangeOut(
            template=ReportTemplateOut(**_template_or_422(name).as_dict()), warnings=warnings,
        )

    return await run_in_threadpool(store)


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
    name = _declared_asset(name, asset_id)[0].name
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
    uploaded = asset_store_module.overrides(template.name).get(asset_id)
    # Installed in the template's folder (or, for a template that extends
    # another, wherever the renderer takes the file from).
    path = uploaded if uploaded is not None else (
        templates.quarto_render.template_file(template.path, asset["path"]) if asset.get("installed") else None
    )
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
    """The project's report defaults — what a draft follows — with the
    project's analysts and admins as the team when none is saved, flagged as
    such."""
    # The team as a draft follows it — the one merge decides the fallback to
    # the project's analysts and admins (review 2026-10-14, R8).
    # The installation's defaults read once, for the merge and the answer
    # (third review 2026-10-15, R7).
    inherited = report_defaults.installation(db)
    followed, sources = report_defaults.followed_with_sources(
        db, profile, lambda: ClientReportService(db).project_team(project_id), base=inherited)
    team = followed["testers"]
    from_project = sources["testers"] != report_defaults.FROM_PROJECT
    if profile is None:
        return ReportProfileOut(
            template=templates.default_template_name(), testers=team, testers_from_project=True,
            inherited=inherited,
        )
    return ReportProfileOut(
        author_name=profile.author_name, disclaimer=profile.disclaimer, inherited=inherited,
        client_name=profile.client_name, classification=profile.classification,
        engagement_type=profile.engagement_type, testers=team,
        distribution=profile.distribution or [], system_description=profile.system_description,
        applications=profile.applications, thick_clients=profile.thick_clients,
        other_targets=profile.other_targets, executive_summary=profile.executive_summary,
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


@router.patch(
    "/profile", response_model=ReportProfileOut,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def patch_report_profile(
    body: ReportProfilePatch,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """The project's engagement details — what every draft prints for a
    detail it does not override, so a change here reaches every such draft
    at once (issued reports never change).  The ONE write of them from a
    page (`/reports/defaults`): only the keys SENT change, so a page that
    read the profile a while ago cannot overwrite someone else's change to
    another detail.  (The whole-profile `PUT` went — review 2026-10-14, R1.)"""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="Send at least one report default to change.")
    profile = _write_profile(db, project.id, data, current_user.id)
    db.commit()
    db.refresh(profile)
    return _profile_out(db, project.id, profile)


def _write_profile(
    db: Session, project_id: int, data: dict, user_id: int,
    check: Optional[Callable[[ReportProfile], None]] = None,
) -> ReportProfile:
    """Write ``data``'s keys (already validated by the body's schema) into the
    project's report defaults, creating the row — the ONE writer of
    ``report_profiles`` for PATCH and a draft's promote.  A list sent
    null is empty; the system description's image references are
    normalised; a template is checked to be a client template.  ``check``
    runs on the LOCKED row before anything is written (and may refuse)."""
    if data.get("template"):
        _template_or_422(data["template"], client=True)
    # Create the row if there is none, THEN lock it: `FOR UPDATE` on a row
    # that does not exist locks nothing, so two first writes both inserted and
    # the second failed on the unique project (review 2026-10-14, H3).  A
    # concurrent insert makes this one wait for it, then do nothing.
    db.execute(
        pg_insert(ReportProfile).values(project_id=project_id)
        .on_conflict_do_nothing(index_elements=[ReportProfile.project_id])
    )
    profile = (
        db.query(ReportProfile).filter(ReportProfile.project_id == project_id)
        .with_for_update().populate_existing().one()
    )
    if check is not None:
        check(profile)
    for key, value in data.items():
        if key in report_defaults.LIST_KEYS:
            value = list(value or [])
        elif key in report_images.ENGAGEMENT_IMAGE_FIELDS:
            value = report_images.normalise_references(value)
        setattr(profile, key, value)
    profile.updated_by_id = user_id
    db.flush()
    return profile


# ---------------------------------------------------------------------------
# The report's own images (v2.495.0)
# ---------------------------------------------------------------------------

def _may_modify_image(att, role: str, user: User) -> bool:
    """The uploader, a project admin or a global admin — the rule the
    attachment's own caption and delete routes apply."""
    return att.uploaded_by_id in (None, user.id) or _is(role, ProjectRole.ADMIN)


def _text_image_out(att, placed: dict, role: str, user: User) -> ReportTextImageOut:
    return ReportTextImageOut(
        id=att.id, filename=att.filename, caption=att.caption, content_type=att.content_type,
        size_bytes=att.size_bytes, uploaded_by_id=att.uploaded_by_id, created_at=att.created_at,
        placed_in=placed.get(att.id, []), can_modify=_may_modify_image(att, role, user),
    )


@router.get("/images", response_model=List[ReportTextImageOut])
def list_report_text_images(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """The images of the project's own report text — what "Insert image" on
    the System description offers.  They belong to the project, so its report
    defaults and every draft place the same ones.  The bytes, the caption and
    the delete are the attachment's own routes (``/notes/attachments/{id}``)."""
    role = _role(db, project, current_user)
    placed = report_images.report_text_placements(db, project.id)
    return [
        _text_image_out(att, placed, role, current_user)
        for att in report_images.project_report_images(db, project.id)
    ]


@router.post(
    "/images", response_model=ReportTextImageOut, status_code=201,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def upload_report_text_image(
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Add an image the report's own text can place (a network diagram in the
    system description): PNG, JPEG or GIF, at most 10 MB, checked by its
    bytes.  ``caption`` is what the report prints under it."""
    try:
        caption = report_images.clean_caption(caption)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    att = store_image_attachment(
        db, note_id=None, project_id=project.id, uploaded_by_id=current_user.id, file=file, caption=caption,
    )
    return _text_image_out(att, {}, _role(db, project, current_user), current_user)


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
        items=[_serialize(db, r, role, with_summary=False, user_id=current_user.id) for r in rows],
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
    _template_or_422(template_name, client=True)
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
    # distribution as issued, held as its overrides (its "Summary of changes"
    # is new text, so the summary starts empty).  A new full report overrides
    # nothing: every detail follows the project's defaults until someone
    # overrides it (``report_defaults.resolve``).
    # An image the baseline printed that the project no longer has is
    # restored from the baseline's copy (`settings_carried_from`).
    # Its summary is its own (`report_defaults.OWN_IN_ADDENDUM`): the
    # baseline's executive summary is not carried.
    carried = (svc.settings_carried_from(svc.lock_issued_for_carry(baseline), current_user.id, with_summary=False)
               if baseline is not None else {})
    report = Report(
        project_id=project.id, kind=body.kind, status=ReportStatus.DRAFT, title=title[:255],
        template=template_name, baseline_report_id=baseline.id if baseline else None,
        settings=carried,
        created_by_id=current_user.id,
    )
    db.add(report)
    db.commit()
    return _serialize(db, _load(db, project, report.id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


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
        _template_or_422(body.template, client=True)
        report.template = body.template
    if "settings" in sent and body.settings is not None:
        # Only the details SENT change; every other stored override is kept
        # as it is, so a page that read the draft a while ago never reverts
        # someone else's override of a detail it did not edit (review
        # 2026-10-14, H1).  A text sent blank or null, or a list sent null,
        # follows the project again; a list sent (even empty) is an override.
        data = body.settings.model_dump(exclude_unset=True)
        for field in report_images.ENGAGEMENT_IMAGE_FIELDS:
            if field in data:
                data[field] = report_images.normalise_references(data[field])
        own = {key: value for key, value in report_defaults.overrides(report.settings).items() if key not in data}
        # Changed back to what it follows: it follows again — for the details
        # sent in this save only (`drop_followed` is given only those).
        follows, _ = ClientReportService(db).followed_details_with_sources(project.id, report.kind)
        own.update(report_defaults.drop_followed(report_defaults.overrides(data), follows))
        report.settings = own
    db.commit()
    db.expire_all()
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


@router.post(
    "/{report_id}/promote", response_model=ReportOut,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def promote_report_details(
    report_id: int,
    body: ReportPromote,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """"Make this the project's default": write each detail sent into the
    project's report defaults and drop it from this draft's overrides, in ONE
    transaction — the draft (and every other draft that follows the project)
    then prints it from the project.  The draft is locked first, then the
    profile (the order every writer of both takes).  The values are the page's
    (an override as edited, saved or not); a detail not sent is untouched."""
    data = body.values.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="Name at least one detail to make the project's default.")
    for key, value in data.items():
        if key not in report_defaults.LIST_KEYS and value is None:
            raise HTTPException(
                status_code=422,
                detail=f"'{key}' is empty here: there is nothing to make the project's default.",
            )
        if key in report_defaults.LIST_KEYS and value is None:
            raise HTTPException(status_code=422, detail=f"'{key}' must be a list (it may be empty).")
    if "testers" in data and not report_defaults.overrides(data).get("testers"):
        # The project's defaults cannot say "nobody" for the team: with none
        # saved a draft lists the project's analysts and admins, so promoting
        # an empty team made every draft print them (review 2026-10-14, H3).
        raise HTTPException(
            status_code=422,
            detail="The project's assessment team cannot be empty: with none saved, drafts list the "
                   "project's analysts and admins. Keep the empty team as this report's own.",
        )
    report = _load_for_change(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report cannot be changed; start a revision instead.")
    own = [key for key in data if key in report_defaults.OWN_IN_ADDENDUM] if report.kind == "addendum" else []
    if own:
        raise HTTPException(
            status_code=422,
            detail="An addendum's summary of changes is its own: it cannot become the project's executive summary.",
        )
    # Under the draft's lock: does it still print what the page showed?
    expected = body.expected.model_dump(exclude_unset=True)
    if set(expected) != set(data):
        raise HTTPException(
            status_code=422, detail="Send 'expected' for exactly the details in 'values'.",
        )
    saved = report_defaults.overrides(report.settings)
    follows, _ = ClientReportService(db).followed_details_with_sources(project.id, report.kind)
    changed = [
        key for key in data
        if not report_defaults.same(key, saved[key] if key in saved else follows.get(key), expected[key])
    ]
    if changed:
        labels = ", ".join(key.replace("_", " ") for key in changed)
        raise HTTPException(
            status_code=409,
            detail=f"This draft changed since you opened it ({labels}): reload it, then update the defaults.",
        )
    # Under the PROFILE's lock: is the project's value still the one the page
    # showed?  A value someone set on the defaults page while the dialog was
    # open was silently replaced (third review 2026-10-15, R9).
    expected_project = body.expected_project.model_dump(exclude_unset=True)

    def project_unchanged(profile: ReportProfile) -> None:
        now, _ = report_defaults.followed_with_sources(
            db, profile, lambda: ClientReportService(db).project_team(project.id), report.kind,
        )
        moved = [
            key for key in data
            if key in expected_project and not report_defaults.same(key, now.get(key), expected_project[key])
        ]
        if moved:
            labels = ", ".join(key.replace("_", " ") for key in moved)
            raise HTTPException(
                status_code=409,
                detail=f"The project's defaults changed since you opened this ({labels}): reload, then update them.",
            )

    _write_profile(db, project.id, data, current_user.id, check=project_unchanged)
    report.settings = {
        key: value for key, value in report_defaults.overrides(report.settings).items() if key not in data
    }
    db.commit()
    db.expire_all()
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


@router.get("/{report_id}/followers", response_model=Dict[str, int])
def report_detail_followers(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """For "Update project defaults…" on a draft (the report workspace): per
    engagement detail, how many OTHER drafts of the project follow the
    project's value — they print the new default once it is pushed up.  A
    draft follows a detail it does not override; an addendum never follows
    its summary (`report_defaults.OWN_IN_ADDENDUM`).  An estimate when read:
    the drafts may change before the push-up."""
    _load(db, project, report_id)
    counts = {key: 0 for key in report_defaults.DETAIL_KEYS}
    others = (
        db.query(Report.kind, Report.settings)
        .filter(Report.project_id == project.id, Report.status == ReportStatus.DRAFT, Report.id != report_id)
        .all()
    )
    for kind, settings in others:
        for key in report_defaults.follows(kind, settings):
            counts[key] += 1
    return counts


@router.get("/{report_id}/inputs", response_model=ReportInputsOut)
def report_inputs(
    report_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """A draft's inputs fingerprint as a preview would render it NOW
    (`client_report_render.inputs_fingerprint`): the page compares it with
    each preview's `inputs_fingerprint` and says one whose inputs changed
    since is out of date.  Null when the draft cannot be built (the reason is
    the summary's `error`)."""
    report = _load(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report has its files already.")
    try:
        dataset, _, _ = ClientReportService(db).build(report)
    except ReportStateError:
        return ReportInputsOut(fingerprint=None)
    return ReportInputsOut(fingerprint=client_report_render.inputs_fingerprint(db, report, dataset))


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
    if not may_discard(report, _role(db, project, current_user), current_user.id):
        raise HTTPException(status_code=403, detail="Only the person who started this draft or a project admin can discard it.")
    db.delete(report)
    db.commit()
    # An issue attempt that died between copying the images (and the
    # template, and the scope file) and its commit left this draft a folder
    # of copies (review 2026-10-01 M2); with the draft gone nothing would
    # ever remove it.  After the commit: the row is the record that the
    # folder is nobody's.
    discard_report_copies(project.id, report_id)
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
    job: poll ``/{report_id}/previews/{job_id}`` and download from there."""
    report = _load(db, project, report_id)
    if report.status != ReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="An issued report has its files already.")
    template = _renderable_template_or_409(report.template)
    if body.format not in template.formats:
        raise HTTPException(status_code=422, detail=f"The '{template.name}' template does not produce {body.format}.")
    return _enqueue(db, project=project, user=current_user, fmt=f"report-{body.format}", report_id=report.id)


def _report_job(db: Session, project: Project, report_id: int, job_id: int):
    """One of THIS report's render jobs — the Reports page's door to it (the
    generic ``/reports/jobs`` routes answer 404 for a client-report job)."""
    job = job_or_404(db, project.id, job_id, owner=CLIENT_REPORT_JOB)
    if (job.filters or {}).get("report_id") != report_id:
        raise HTTPException(status_code=404, detail="Report job not found")
    return job


@router.get("/{report_id}/previews/{job_id}", response_model=ReportJobSchema)
def get_report_preview(
    report_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """A preview job of this report: its status, progress, error and
    ``inputs_fingerprint``."""
    return _report_job(db, project, report_id, job_id)


@router.get("/{report_id}/previews/{job_id}/download")
def download_report_preview(
    report_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """A finished preview's file (409 until it is completed, 410 once it has
    expired)."""
    return job_file_response(_report_job(db, project, report_id, job_id))


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
        # The issue did not happen.  Its copies of the evidence images
        # (``freeze_report_images``) are deliberately LEFT: the rollback
        # released the report's lock, so another request may already have
        # issued this draft and written ITS copies into the same folder —
        # removing it here deleted an issued report's images (external review
        # 2026-10-02 H1).  The next issue attempt empties the folder under the
        # lock, and discarding the draft removes it.
        db.rollback()
        raise
    ReportJobService().enqueue_job(job.id, db=db)
    db.expire_all()
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


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
    if not (report.snapshot or {}).get("template"):
        # A report issued before it kept its own copy of the template renders
        # from the live one; a report with a copy needs nothing of it.
        _renderable_template_or_409(report.template)
    report.render_status = RenderStatus.PENDING
    report.render_error = None
    job = _enqueue(db, project=project, user=current_user, fmt="report-issue", report_id=report.id,
                   commit=False)
    db.commit()
    ReportJobService().enqueue_job(job.id, db=db)
    return _serialize(db, _load(db, project, report_id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


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
    svc = ClientReportService(db)
    original = svc.lock_issued_for_carry(_load(db, project, report_id))
    if original.status != ReportStatus.ISSUED:
        raise HTTPException(status_code=409, detail="Only the current issue of a report can be revised.")
    # An image the original printed that the project no longer has is
    # restored from the original's copy (`settings_carried_from`), in this
    # transaction: the draft and its images are committed together.
    carried = svc.settings_carried_from(original, current_user.id)
    draft = Report(
        project_id=project.id, kind=original.kind, status=ReportStatus.DRAFT,
        title=original.title, template=original.template,
        baseline_report_id=original.baseline_report_id, revision_of_id=original.id,
        settings=carried,
        created_by_id=current_user.id,
    )
    db.add(draft)
    db.commit()
    return _serialize(db, _load(db, project, draft.id), _role(db, project, current_user), with_summary=True,
                      user_id=current_user.id)


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
    """The scope file download — shared with the agent's route.

    An ISSUED report that names its scope file prints that file's SHA-256, so
    the download is the bytes kept at issue (or, for a report issued before
    they were kept, built again from the frozen scope) and is refused — 500,
    logged — when they are not the file the report names: a silently
    different file would fail the client's own check with no explanation.

    A draft's (or a report with nothing frozen) is its live scope block —
    the one ``build`` puts in the dataset, without building the findings."""
    frozen = report.status != ReportStatus.DRAFT and bool(report.snapshot)
    if frozen:
        dataset, summary = service.content(report)
        if dataset is None:
            raise HTTPException(status_code=409, detail=summary.get("error") or "This report cannot be built.")
        scope = dataset.get("scope") or {"subnets": [], "domains": []}
    else:
        scope = service.live_scope(report)
    name = ((scope.get("file") or {}).get("name")
            or report_scope.file_name(project_slug=None, number=report.number, report_id=report.id))
    if frozen and scope.get("file"):
        try:
            content = issued_scope_file(report, scope)
        except ValueError as exc:
            logger.error("Scope file refused: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="This report's scope file no longer matches the SHA-256 the report prints, so it "
                       "is not served. Restore the report's storage from a backup, or revise the report.",
            )
    else:
        content = report_scope.scope_csv(scope)
    return Response(
        content=content, media_type="text/csv; charset=utf-8",
        headers=attachment(name),
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
