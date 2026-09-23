"""Finding spine endpoints (foundation phase 5).

Project-scoped CRUD + triage for findings, plus promote-from-annotation.
All routes authorise via get_current_project (ProjectMembership); writes
require analyst-or-better.  Authored content — a finding's title, the finding
itself (delete), a comment's text — is further limited to its author (or a
project admin, for the finding); triage stays open to any analyst.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, File
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.db.models import Annotation, Host
from app.db.models_vulnerability import Vulnerability
from app.db.models_findings import Finding, FindingHost, FindingStatus, FindingStatusHistory
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role, resolve_project_assignee
from app.core.security import check_permissions, log_audit_event
from app.services.finding_service import FindingService, validate_severity
from app.services.host_follow_service import HostFollowService, NoteHasRepliesError
from app.services.host_serialization import _serialize_note, note_load_options
from app.services.note_attachment_service import purge_note_files, store_image_attachment
from app.schemas.schemas import (
    Annotation as AnnotationSchema, AnnotationCreate, NoteAttachmentOut,
)
from app.schemas.findings import (
    EndpointStatusUpdate,
    FindingResponse, FindingHostInfo, FindingListResponse,
    PromoteAnnotationRequest, PromoteVulnerabilityRequest, PromoteVulnerabilityPreview,
    FindingCreateRequest, FindingUpdateRequest, FindingNoteUpdate,
    FindingStatusUpdateRequest, FindingHostsRequest, FindingStatusHistoryEntry,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


@dataclass(frozen=True)
class _Viewer:
    """The caller, as far as authored-content rights go (v2.375.0)."""
    user_id: int
    is_project_admin: bool

    def may_modify(self, finding: Finding) -> bool:
        return self.is_project_admin or (
            finding.created_by_id is not None and finding.created_by_id == self.user_id
        )


def get_finding_viewer(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
) -> _Viewer:
    if current_user.role == UserRole.ADMIN:
        return _Viewer(user_id=current_user.id, is_project_admin=True)
    role = (
        db.query(ProjectMembership.role)
        .filter(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == current_user.id,
        )
        .scalar()
    )
    return _Viewer(
        user_id=current_user.id,
        is_project_admin=bool(role) and check_permissions(role, ProjectRole.ADMIN.value),
    )


def _require_modify(viewer: _Viewer, finding: Finding, what: str) -> None:
    if not viewer.may_modify(finding):
        raise HTTPException(
            status_code=403,
            detail=f"Only the finding's author or a project admin can {what}.",
        )


def _serialize(finding: Finding, viewer: Optional[_Viewer] = None) -> FindingResponse:
    hosts = [
        FindingHostInfo(
            id=fh.id,
            host_id=fh.host_id,
            ip_address=fh.host.ip_address if fh.host else None,
            hostname=fh.host.hostname if fh.host else None,
            name_id=fh.name_id,
            fqdn=fh.name.fqdn if fh.name else None,
            host_status=fh.host_status,
        )
        for fh in finding.hosts
    ]
    # v2.349.0 — per-endpoint states rolled up, so a list row can say
    # "open on 3 of 5 · 2 remediated" without implying one state everywhere.
    endpoint_status_counts: Dict[str, int] = {}
    for h in hosts:
        endpoint_status_counts[h.host_status] = endpoint_status_counts.get(h.host_status, 0) + 1
    return FindingResponse(
        endpoint_status_counts=endpoint_status_counts,
        id=finding.id, project_id=finding.project_id, title=finding.title,
        severity=finding.severity, status=finding.status, source=finding.source,
        owner_id=finding.owner_id,
        owner_name=(finding.owner.full_name or finding.owner.username) if finding.owner else None,
        evidence_annotation_id=finding.evidence_annotation_id,
        vuln_id=finding.vuln_id, exec_result_id=finding.exec_result_id,
        host_count=len(hosts), hosts=hosts,
        created_by_id=finding.created_by_id,
        created_by_name=(
            (finding.created_by.full_name or finding.created_by.username)
            if finding.created_by else None
        ),
        can_modify=viewer.may_modify(finding) if viewer is not None else False,
        created_at=finding.created_at, updated_at=finding.updated_at,
    )


def _load(db: Session, project: Project, finding_id: int) -> Finding:
    finding = (
        db.query(Finding)
        .options(
            selectinload(Finding.hosts).selectinload(FindingHost.host),
            selectinload(Finding.owner), selectinload(Finding.created_by),
        )
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
    search: Optional[str] = Query(None, max_length=200, description="Case-insensitive substring match on finding title."),
    sort: Optional[str] = Query(
        None, description="severity | status | title | host_count | source | created_at (default newest-first).",
    ),
    dir: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    svc = FindingService(db)
    rows, total = svc.list_findings(
        project_id=project.id, status=status, severity=severity,
        owner_id=owner_id, unowned=unowned, source=source, host_id=host_id,
        search=search, limit=limit, offset=offset, sort=sort, sort_dir=dir,
    )
    sev_counts = svc.severity_counts(
        project_id=project.id, status=status, owner_id=owner_id,
        unowned=unowned, source=source, host_id=host_id, search=search,
    )
    return FindingListResponse(
        items=[_serialize(f, viewer) for f in rows], total=total, severity_counts=sev_counts,
    )


@router.get("/findings/{finding_id}", response_model=FindingResponse)
def get_finding(
    finding_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    return _serialize(_load(db, project, finding_id), viewer)


@router.post("/findings", response_model=FindingResponse, status_code=201)
def create_finding(
    body: FindingCreateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    """Create a finding directly, without promoting an annotation.

    **API-only by design, not an orphan** (v2.244.0). In the product,
    findings arrive by promotion from an annotation, which is what carries the
    evidence trail; nothing in the UI calls this. It stays available for
    scripted/agent use and because closing it off would narrow the product
    without anyone asking for that. If a UI ever wants manual creation, this is
    the endpoint — do not "clean it up" as unreachable.
    """
    resolve_project_assignee(db, project.id, body.owner_id)
    svc = FindingService(db)
    finding = svc.create_finding(
        project_id=project.id, title=body.title, severity=body.severity,
        status=body.status or FindingStatus.OPEN.value, owner_id=body.owner_id,
        host_ids=body.host_ids, actor_id=current_user.id,
    )
    db.commit()
    return _serialize(_load(db, project, finding.id), viewer)


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
    viewer: _Viewer = Depends(get_finding_viewer),
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
    resolve_project_assignee(db, project.id, body.owner_id)
    finding = svc.promote_annotation(
        annotation=annotation, severity=body.severity, title=body.title,
        status=body.status or FindingStatus.CONFIRMED.value, owner_id=body.owner_id,
        extra_host_ids=body.extra_host_ids, actor_id=current_user.id,
    )
    db.commit()
    return _serialize(_load(db, project, finding.id), viewer)


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
    viewer: _Viewer = Depends(get_finding_viewer),
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
    resolve_project_assignee(db, project.id, body.owner_id)
    status = body.status or FindingStatus.CONFIRMED.value
    # v2.360.0 — a false-positive dismissal is about THIS host unless the
    # caller says the whole issue.  v2.366.0 — a PROMOTION may be too
    # (``scope: "host"``): "confirmed" is then recorded for the host that was
    # looked at, not for every host carrying the issue.  The API default for a
    # promotion is unchanged ("issue"), so agents and existing callers behave
    # as before; the inspector's dialog sends its choice explicitly.  Accepted
    # risk stays issue-wide: it is a decision about the issue, not a host.
    is_fp = status == FindingStatus.FALSE_POSITIVE.value
    scope = body.scope or ("host" if is_fp else "issue")
    if scope == "host" and status == FindingStatus.ACCEPTED_RISK.value:
        raise HTTPException(
            status_code=422,
            detail="scope='host' does not apply to accepted risk, which is a decision "
                   "about the issue on every host.",
        )
    svc = FindingService(db)
    if scope == "host" and is_fp:
        finding = svc.dismiss_vulnerability_on_host(
            vuln=vuln, project_id=project.id, actor_id=current_user.id,
            severity=body.severity, owner_id=body.owner_id, summary=body.summary,
        )
    else:
        finding = svc.promote_vulnerability(
            vuln=vuln, project_id=project.id, actor_id=current_user.id,
            severity=body.severity, status=status,
            owner_id=body.owner_id, summary=body.summary,
            only_this_host=(scope == "host"),
        )
    db.commit()
    return _serialize(_load(db, project, finding.id), viewer)


@router.patch("/findings/{finding_id}", response_model=FindingResponse)
def update_finding(
    finding_id: int,
    body: FindingUpdateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    # Only status transitions are audited (via finding_status_history);
    # title/severity/owner edits are not — they're attributes, not lifecycle.
    finding = _load(db, project, finding_id)
    if body.title is not None:
        # v2.375.0 — the title is authored content: its author (or a project
        # admin) renames it.  Severity and owner stay triage, open to any
        # analyst.  Validated before any other field is applied.
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="A finding's title cannot be empty.")
        if title != finding.title:
            _require_modify(viewer, finding, "rename it")
        finding.title = title[:500]
    if body.severity is not None:
        finding.severity = validate_severity(body.severity)
    # Owner: distinguish "field omitted" from "explicitly set to null" so
    # selecting Unassigned (owner_id: null) actually clears ownership instead of
    # being silently ignored. A non-null owner must be a valid project assignee.
    if "owner_id" in body.model_fields_set:
        finding.owner_id = resolve_project_assignee(db, project.id, body.owner_id)
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


@router.delete(
    "/findings/{finding_id}",
    status_code=204,
    summary="Delete a finding (its author or a project admin)",
)
def delete_finding(
    finding_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    """v2.375.0 — for a finding recorded in error.  Removes the finding, its
    endpoint rows, disposition history and comment thread; the evidence it
    pointed at survives (the source note can be promoted again, the scanner
    rows are untriaged observations again).  To say an issue does not apply,
    set a status instead — that keeps the record.  The deletion itself is
    written to the audit log, since the finding's own history goes with it."""
    finding = _load(db, project, finding_id)
    _require_modify(viewer, finding, "delete it")
    summary = {
        "project_id": project.id, "title": finding.title, "severity": finding.severity,
        "status": finding.status, "source": finding.source,
        "created_by_id": finding.created_by_id, "host_count": len(finding.hosts),
    }
    note_ids = FindingService(db).delete_finding(finding=finding)
    summary["comment_count"] = len(note_ids)
    # log_audit_event commits — the delete and its audit row land together.
    log_audit_event(
        db, user_id=current_user.id, action="finding_deleted",
        resource_type="finding", resource_id=str(finding_id), details=summary,
    )
    for nid in note_ids:
        purge_note_files(nid)
    return Response(status_code=204)


@router.post("/findings/{finding_id}/status", response_model=FindingResponse)
def set_finding_status(
    finding_id: int,
    body: FindingStatusUpdateRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    finding = _load(db, project, finding_id)
    FindingService(db).set_status(
        finding=finding, status=body.status, actor_id=current_user.id, summary=body.summary,
    )
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


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
    viewer: _Viewer = Depends(get_finding_viewer),
):
    finding = _load(db, project, finding_id)
    svc = FindingService(db)
    if body.host_ids:
        svc.add_hosts(finding=finding, host_ids=body.host_ids)
    for ep in body.endpoints:
        svc.restore_endpoint(finding=finding, host_id=ep.host_id, name_id=ep.name_id, host_status=ep.host_status)
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


@router.delete(
    "/findings/{finding_id}/hosts/{host_id}",
    response_model=FindingResponse,
    summary="Detach EVERY endpoint on a host from the finding",
)
def remove_finding_host(
    finding_id: int,
    host_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    """Removes all affected-endpoint rows for ``host_id`` (named and
    unnamed).  To detach ONE named endpoint use
    ``DELETE /findings/{id}/endpoints/{finding_host_id}``."""
    finding = _load(db, project, finding_id)
    FindingService(db).remove_host(finding=finding, host_id=host_id)
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


@router.patch(
    "/findings/{finding_id}/endpoints/{finding_host_id}",
    response_model=FindingResponse,
    summary="Set one affected endpoint's own state (open / remediated / retest)",
)
def set_finding_endpoint_status(
    finding_id: int,
    finding_host_id: int,
    body: EndpointStatusUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    current_user: User = Depends(get_current_user),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    """v2.349.0 — the finding's status is the issue's; each endpoint keeps
    its own (design review item 7).  Confirming or remediating on one host
    never implies it on the others; the move is written to the finding's
    history with the endpoint named."""
    finding = _load(db, project, finding_id)
    FindingService(db).set_endpoint_status(
        finding=finding, finding_host_id=finding_host_id,
        host_status=body.host_status, actor_id=current_user.id,
    )
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


@router.delete(
    "/findings/{finding_id}/endpoints/{finding_host_id}",
    response_model=FindingResponse,
    summary="Detach one affected endpoint (a FindingHost row) from the finding",
)
def remove_finding_endpoint(
    finding_id: int,
    finding_host_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
    viewer: _Viewer = Depends(get_finding_viewer),
):
    """v2.325.0 — a host may carry several endpoint rows (one per vhost);
    this removes exactly the one addressed, leaving its siblings.  The
    response is the finding as it stands; the caller can restore the removed
    row (name and status) via ``POST /hosts`` with ``endpoints``."""
    finding = _load(db, project, finding_id)
    removed = FindingService(db).remove_endpoint(finding=finding, finding_host_id=finding_host_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Endpoint is not attached to this finding")
    db.commit()
    return _serialize(_load(db, project, finding_id), viewer)


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


@router.patch(
    "/findings/{finding_id}/notes/{note_id}",
    response_model=AnnotationSchema,
    summary="Edit a comment's text (its author only)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def update_finding_note(
    finding_id: int,
    note_id: int,
    payload: FindingNoteUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """v2.375.0 — a comment is its author's words; nobody else rewrites
    them, admins included (the host-note rule).  Attachments are managed
    separately and are unaffected."""
    _load(db, project, finding_id)  # 404s + enforces project scope
    note = FindingService(db).get_finding_note(finding_id=finding_id, note_id=note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if note.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit a comment")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="A comment cannot be empty")
    note.body = body
    db.commit()
    note = (
        db.query(Annotation).options(*note_load_options())
        .filter(Annotation.id == note_id).populate_existing().one()
    )
    return _serialize_note(note)


@router.delete(
    "/findings/{finding_id}/notes/{note_id}",
    status_code=204,
    summary="Delete a comment (its author only; not while it has replies)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def delete_finding_note(
    finding_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """v2.375.0 — same rules as a host note: author only, and a comment that
    others have replied to stays (409) so the replies keep their context."""
    _load(db, project, finding_id)  # 404s + enforces project scope
    if FindingService(db).get_finding_note(finding_id=finding_id, note_id=note_id) is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    try:
        # Checks authorship and replies, deletes, commits, purges the files.
        HostFollowService(db).delete_note(note_id, current_user.id)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the author can delete a comment")
    except NoteHasRepliesError:
        raise HTTPException(
            status_code=409,
            detail="This comment has replies; it stays so they keep their context. "
                   "Edit its text instead.",
        )
    return Response(status_code=204)


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
