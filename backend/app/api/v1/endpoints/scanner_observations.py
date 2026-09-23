"""Scanner observations grouped by issue, and their bulk promotion (v2.386.0).

``GET /scanner-observations`` lists each issue once across the project's hosts;
``GET /scanner-observations/hosts?issue_key=`` lists the hosts carrying one;
``POST /scanner-observations/promote`` promotes several issues in one call.
The logic is ``scanner_observation_service``; this module is the HTTP shape.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.core.security import log_audit_event
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services import scanner_observation_service as svc

router = APIRouter(dependencies=[Depends(get_current_user)])


class IssueRowOut(BaseModel):
    issue_key: str
    title: str
    severity: str
    cve_id: Optional[str] = None
    sources: List[str] = Field(default_factory=list)
    host_count: int
    judged_host_count: int
    finding_id: Optional[int] = None
    finding_status: Optional[str] = None


class IssuePageOut(BaseModel):
    items: List[IssueRowOut]
    total: int


class IssueHostOut(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    severity: str
    ports: List[int] = Field(default_factory=list)
    judged: bool
    endpoint_status: Optional[str] = None


class PromoteItemIn(BaseModel):
    issue_key: str = Field(..., min_length=1, max_length=600)
    # Omitted = every host carrying the issue.
    host_ids: Optional[List[int]] = Field(default=None, max_length=10000)


class PromoteRequest(BaseModel):
    items: List[PromoteItemIn] = Field(..., min_length=1, max_length=svc.PROMOTE_ISSUE_CAP)


class PromoteOutcomeOut(BaseModel):
    issue_key: str
    finding_id: int
    created: bool
    host_count: int


class PromoteResponse(BaseModel):
    results: List[PromoteOutcomeOut]


@router.get("/scanner-observations", response_model=IssuePageOut, summary="Scanner observations, one row per issue")
def list_scanner_observation_issues(
    search: Optional[str] = Query(None, max_length=200),
    severity: Optional[str] = Query(None, max_length=20),
    include_judged: bool = Query(False, description="Also list issues a finding already covers on every host"),
    min_hosts: int = Query(1, ge=1, le=100000),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    try:
        page = svc.list_issues(
            db, project.id, search=search, severity=severity, include_judged=include_judged,
            min_hosts=min_hosts, skip=skip, limit=limit,
        )
    except svc.ObservationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return IssuePageOut(items=[IssueRowOut(**vars(r)) for r in page.items], total=page.total)


@router.get("/scanner-observations/hosts", response_model=List[IssueHostOut], summary="The hosts carrying one issue")
def list_scanner_observation_hosts(
    issue_key: str = Query(..., min_length=1, max_length=600),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return [IssueHostOut(**vars(h)) for h in svc.issue_hosts(db, project.id, issue_key)]


@router.post(
    "/scanner-observations/promote",
    response_model=PromoteResponse,
    summary="Promote several issues to findings, each on all or some of its hosts (analyst)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def promote_scanner_observation_issues(
    body: PromoteRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    try:
        outcomes = svc.promote_issues(
            db, project.id, current_user.id,
            [svc.PromoteItem(issue_key=i.issue_key, host_ids=i.host_ids) for i in body.items],
        )
    except svc.ObservationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    log_audit_event(
        db, user_id=current_user.id, action="scanner_observations_promoted",
        resource_type="project", resource_id=str(project.id),
        details={
            "issues": len(outcomes),
            "created": sum(1 for o in outcomes if o.created),
            "hosts": sum(o.host_count for o in outcomes),
            "finding_ids": [o.finding_id for o in outcomes],
        },
    )
    return PromoteResponse(results=[PromoteOutcomeOut(**vars(o)) for o in outcomes])
