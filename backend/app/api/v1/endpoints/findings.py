"""Finding spine endpoints (foundation phase 5).

Project-scoped CRUD + triage for findings, plus promote-from-annotation.
All routes authorise via get_current_project (ProjectMembership); writes
require analyst-or-better.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.db.models import Annotation
from app.db.models_findings import Finding, FindingHost, FindingStatus
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.services.finding_service import FindingService
from app.schemas.findings import (
    FindingResponse, FindingHostInfo, FindingListResponse,
    PromoteAnnotationRequest, FindingCreateRequest, FindingUpdateRequest,
    FindingStatusUpdateRequest, FindingHostsRequest,
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
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    host_id: Optional[int] = Query(None, description="Only findings affecting this host."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    rows, total = FindingService(db).list_findings(
        project_id=project.id, status=status, severity=severity,
        owner_id=owner_id, source=source, host_id=host_id, limit=limit, offset=offset,
    )
    return FindingListResponse(items=[_serialize(f) for f in rows], total=total)


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
    # Guard cross-tenant: the resolved project must match the path project.
    if svc._project_id_for_annotation(annotation) != project.id:
        raise HTTPException(status_code=404, detail="Annotation not found in this project")
    finding = svc.promote_annotation(
        annotation=annotation, severity=body.severity, title=body.title,
        status=body.status or FindingStatus.CONFIRMED.value, owner_id=body.owner_id,
        extra_host_ids=body.extra_host_ids, actor_id=current_user.id,
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
    finding = _load(db, project, finding_id)
    if body.title is not None:
        finding.title = body.title[:500]
    if body.severity is not None:
        from app.services.finding_service import _validate_severity
        finding.severity = _validate_severity(body.severity)
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
