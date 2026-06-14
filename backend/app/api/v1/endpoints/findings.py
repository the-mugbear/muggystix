"""Finding spine endpoints (foundation phase 5).

Project-scoped CRUD + triage for findings, plus promote-from-annotation.
All routes authorise via get_current_project (ProjectMembership); writes
require analyst-or-better.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.db.models import Annotation, Host
from app.db.models_vulnerability import Vulnerability
from app.db.models_findings import Finding, FindingHost, FindingStatus, FindingStatusHistory
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.services.finding_service import FindingService, validate_severity
from app.services.host_serialization import _serialize_note
from app.services.note_attachment_service import store_image_attachment
from app.schemas.schemas import (
    Annotation as AnnotationSchema, AnnotationCreate, NoteAttachmentOut,
)
from app.schemas.findings import (
    FindingResponse, FindingHostInfo, FindingListResponse,
    PromoteAnnotationRequest, PromoteVulnerabilityRequest, PromoteVulnerabilityPreview,
    FindingCreateRequest, FindingUpdateRequest,
    FindingStatusUpdateRequest, FindingHostsRequest, FindingStatusHistoryEntry,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _serialize(finding: Finding) -> FindingResponse:
    hosts = [
        FindingHostInfo(
            host_id=fh.host_id,
            ip_address=fh.host.ip_address if fh.host else None,
            hostname=fh.host.hostname if fh.host else None,
            host_status=fh.host_status,
        )
        for fh in finding.hosts
    ]
    return FindingResponse(
        id=finding.id, project_id=finding.project_id, title=finding.title,
        severity=finding.severity, status=finding.status, source=finding.source,
        owner_id=finding.owner_id,
        owner_name=(finding.owner.full_name or finding.owner.username) if finding.owner else None,
        evidence_annotation_id=finding.evidence_annotation_id,
        vuln_id=finding.vuln_id, exec_result_id=finding.exec_result_id,
        host_count=len(hosts), hosts=hosts,
        created_at=finding.created_at, updated_at=finding.updated_at,
    )


def _load(db: Session, project: Project, finding_id: int) -> Finding:
    finding = (
        db.query(Finding)
        .options(selectinload(Finding.hosts).selectinload(FindingHost.host), selectinload(Finding.owner))
        .filter(Finding.id == finding_id, Finding.project_id == project.id)
        .first()
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@router.get("/findings", response_model=FindingListResponse)
def list_findings(
    status: Optional[str] = Query(
        None, description="A status, or a group: 'active' (open/confirmed/retest) | 'resolved' (terminal).",
    ),
    severity: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    unowned: bool = Query(False, description="Only findings with no owner (overrides owner_id)."),
    source: Optional[str] = Query(None),
    host_id: Optional[int] = Query(None, description="Only findings affecting this host."),
    sort: Optional[str] = Query(
        None, description="severity | status | title | host_count | source | created_at (default newest-first).",
    ),
    dir: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    svc = FindingService(db)
    rows, total = svc.list_findings(
        project_id=project.id, status=status, severity=severity,
        owner_id=owner_id, unowned=unowned, source=source, host_id=host_id,
        limit=limit, offset=offset, sort=sort, sort_dir=dir,
    )
    sev_counts = svc.severity_counts(
        project_id=project.id, status=status, owner_id=owner_id,
        unowned=unowned, source=source, host_id=host_id,
    )
    return FindingListResponse(
        items=[_serialize(f) for f in rows], total=total, severity_counts=sev_counts,
    )


@router.get("/findings/{finding_id}", response_model=FindingResponse)
def get_finding(
    finding_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return _serialize(_load(db, project, finding_id))


@router.post("/findings", response_model=FindingResponse, status_code=201)
def create_finding(
    body: FindingCreateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
):
    svc = FindingService(db)
    finding = svc.create_finding(
        project_id=project.id, title=body.title, severity=body.severity,
        status=body.status or FindingStatus.OPEN.value, owner_id=body.owner_id,
        host_ids=body.host_ids, actor_id=current_user.id,
    )
    db.commit()
    return _serialize(_load(db, project, finding.id))


@router.post(
    "/annotations/{annotation_id}/promote",
    response_model=FindingResponse, status_code=201,
)
def promote_annotation(
    annotation_id: int,
    body: PromoteAnnotationRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
):
    annotation = db.get(Annotation, annotation_id)
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")
    svc = FindingService(db)
    # Guard cross-tenant: a resolvable project that isn't ours → 404.  When
    # the project can't be resolved (scan/port/plan-targeted note), let the
    # service raise its clearer 422 ("promote a host-/scope-/project-scoped
    # note") rather than masking it as a 404 here.
    resolved = svc._project_id_for_annotation(annotation)
    if resolved is not None and resolved != project.id:
        raise HTTPException(status_code=404, detail="Annotation not found in this project")
    finding = svc.promote_annotation(
        annotation=annotation, severity=body.severity, title=body.title,
        status=body.status or FindingStatus.CONFIRMED.value, owner_id=body.owner_id,
        extra_host_ids=body.extra_host_ids, actor_id=current_user.id,
    )
    db.commit()
    return _serialize(_load(db, project, finding.id))


@router.get(
    "/vulnerabilities/{vuln_id}/promote-preview",
    response_model=PromoteVulnerabilityPreview,
)
def preview_promote_vulnerability(
    vuln_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Preview a vuln promotion's blast radius before committing (§11): how
    many project hosts share this plugin_id and so would be attached to the
    one canonical finding, plus whether it's already promoted."""
    vuln = (
        db.query(Vulnerability)
        .join(Host, Vulnerability.host_id == Host.id)
        .filter(Vulnerability.id == vuln_id, Host.project_id == project.id)
        .first()
    )
    if not vuln:
        raise HTTPException(status_code=404, detail="Vulnerability not found in this project")
    return PromoteVulnerabilityPreview(
        **FindingService(db).preview_vulnerability_promotion(vuln=vuln, project_id=project.id)
    )


@router.post(
    "/vulnerabilities/{vuln_id}/promote",
    response_model=FindingResponse, status_code=201,
)
def promote_vulnerability(
    vuln_id: int,
    body: PromoteVulnerabilityRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
):
    """Promote (or dismiss) a scanner vulnerability as a Finding. The finding
    references the vuln (vuln_id), severity defaults to the vuln's own, and a
    terminal status dismisses it (false_positive / accepted_risk). Idempotent
    per vuln. The path id and body.vuln_id must agree and belong to this
    project (joined through the host — cross-tenant 404s)."""
    if body.vuln_id != vuln_id:
        raise HTTPException(status_code=400, detail="vuln_id in path and body must match")
    vuln = (
        db.query(Vulnerability)
        .join(Host, Vulnerability.host_id == Host.id)
        .filter(Vulnerability.id == vuln_id, Host.project_id == project.id)
        .first()
    )
    if not vuln:
        raise HTTPException(status_code=404, detail="Vulnerability not found in this project")
    finding = FindingService(db).promote_vulnerability(
        vuln=vuln, project_id=project.id, actor_id=current_user.id,
        severity=body.severity, status=body.status or FindingStatus.CONFIRMED.value,
        owner_id=body.owner_id, summary=body.summary,
    )
    db.commit()
    return _serialize(_load(db, project, finding.id))


@router.patch("/findings/{finding_id}", response_model=FindingResponse)
def update_finding(
    finding_id: int,
    body: FindingUpdateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    # Only status transitions are audited (via finding_status_history);
    # title/severity/owner edits are not — they're attributes, not lifecycle.
    finding = _load(db, project, finding_id)
    if body.title is not None:
        finding.title = body.title[:500]
    if body.severity is not None:
        finding.severity = validate_severity(body.severity)
    if body.owner_id is not None:
        finding.owner_id = body.owner_id
    db.commit()
    return _serialize(_load(db, project, finding_id))


@router.post("/findings/{finding_id}/status", response_model=FindingResponse)
def set_finding_status(
    finding_id: int,
    body: FindingStatusUpdateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
):
    finding = _load(db, project, finding_id)
    FindingService(db).set_status(
        finding=finding, status=body.status, actor_id=current_user.id, summary=body.summary,
    )
    db.commit()
    return _serialize(_load(db, project, finding_id))


@router.get(
    "/findings/{finding_id}/history",
    response_model=List[FindingStatusHistoryEntry],
)
def get_finding_history(
    finding_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The finding's disposition trail (open → confirmed → remediated, …),
    newest first.  The rows were always written on each transition but had no
    read path — this surfaces who changed status, when, and why."""
    _load(db, project, finding_id)  # 404s + enforces project scope
    rows = (
        db.query(FindingStatusHistory)
        .options(selectinload(FindingStatusHistory.changed_by))
        .filter(FindingStatusHistory.finding_id == finding_id)
        .order_by(FindingStatusHistory.created_at.desc(), FindingStatusHistory.id.desc())
        .all()
    )
    return [
        FindingStatusHistoryEntry(
            id=r.id, from_status=r.from_status, to_status=r.to_status,
            changed_by_id=r.changed_by_id,
            changed_by_name=(r.changed_by.full_name or r.changed_by.username) if r.changed_by else None,
            summary=r.summary, created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/findings/{finding_id}/hosts", response_model=FindingResponse)
def add_finding_hosts(
    finding_id: int,
    body: FindingHostsRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    finding = _load(db, project, finding_id)
    FindingService(db).add_hosts(finding=finding, host_ids=body.host_ids)
    db.commit()
    return _serialize(_load(db, project, finding_id))


@router.delete("/findings/{finding_id}/hosts/{host_id}", response_model=FindingResponse)
def remove_finding_host(
    finding_id: int,
    host_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    finding = _load(db, project, finding_id)
    FindingService(db).remove_host(finding=finding, host_id=host_id)
    db.commit()
    return _serialize(_load(db, project, finding_id))


# ---------------------------------------------------------------------------
# Comment / evidence thread on a finding
# ---------------------------------------------------------------------------
# The notes→findings→reports flow: host notes capture issues, the finding is
# reviewed/refined here with discussion + screenshots, then the report renders
# that evidence.  Reuses the host-note Annotation machinery on a finding_id
# target (see FindingService.{list,create}_finding_note).

@router.get(
    "/findings/{finding_id}/notes",
    response_model=List[AnnotationSchema],
    summary="List the comment/evidence thread on a finding (oldest-first)",
)
def list_finding_notes(
    finding_id: int,
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    _load(db, project, finding_id)  # 404s + enforces project scope
    notes = FindingService(db).list_finding_notes(finding_id, limit=limit)
    return [_serialize_note(n) for n in notes]


@router.post(
    "/findings/{finding_id}/notes",
    response_model=AnnotationSchema,
    summary="Add a comment to a finding's evidence thread",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def create_finding_note(
    finding_id: int,
    payload: AnnotationCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    _load(db, project, finding_id)  # 404s + enforces project scope
    try:
        note = FindingService(db).create_finding_note(
            finding_id=finding_id, user_id=current_user.id,
            body=payload.body, parent_id=payload.parent_id,
        )
    except ValueError as exc:
        # parent_id validation failure (cross-finding threading attempt).
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize_note(note)


@router.post(
    "/findings/{finding_id}/notes/{note_id}/attachments",
    response_model=NoteAttachmentOut,
    summary="Attach an image/screenshot (evidence) to a finding comment",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def upload_finding_note_attachment(
    finding_id: int,
    note_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    # Scope the note to the finding + project so an attachment can't be hung
    # off another project's note via a tampered path.
    _load(db, project, finding_id)  # 404s + enforces project scope
    note = (
        db.query(Annotation)
        .filter(Annotation.id == note_id, Annotation.finding_id == finding_id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Comment not found")
    return store_image_attachment(
        db, note_id=note_id, project_id=project.id,
        uploaded_by_id=current_user.id, file=file,
    )
