"""Scanner observations grouped by issue, and their bulk promotion (v2.386.0).

``GET /scanner-observations`` lists each issue once across the project's hosts;
``GET /scanner-observations/hosts?issue_key=`` lists the hosts carrying one;
``GET /scanner-observations/detail?issue_key=`` is what a scanner wrote about it;
``POST /scanner-observations/promote`` promotes several issues in one call.
The logic is ``scanner_observation_service``; this module is the HTTP shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.deps import get_current_user
from app.core.security import log_audit_event
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.schemas import HostVulnerability
from app.services import scanner_observation_service as svc

router = APIRouter(dependencies=[Depends(get_current_user)])

# The service's ISSUE_SORTS and JUDGED_STATES as types, so OpenAPI (and the
# MCP tool derived from it) carries the enums; a test pins them equal.
IssueSort = Literal["severity", "hosts", "cvss"]
IssueJudged = Literal["any", "waiting", "none", "all"]


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
    # v2.415.0 — misconfiguration / vulnerability / informational.
    kind: str = "vulnerability"
    # A scanner reports an exploit for it on at least one host (a lead, not a
    # statement that it was exploited).
    exploitable: bool = False
    plugin_ids: List[str] = Field(default_factory=list, description="The scanners' check ids for it (the first 5)")
    cvss_score: Optional[float] = Field(None, description="The highest CVSS base score any of its rows gives")
    ports: List[int] = Field(default_factory=list, description="The ports it was seen on, the first 8 by number")
    port_count: int = Field(0, description="How many ports it was seen on (0: host-level only)")
    last_seen: Optional[datetime] = Field(None, description="When an import last recorded it, on any host")


# One declaration for both doors (the page's route and the agents').
ISSUE_SORT = Query(
    "severity",
    description="severity: most severe first (default); hosts: most widespread first; cvss: highest CVSS first",
)
ISSUE_JUDGED = Query(
    None,
    description="any: every issue; waiting (the default): a host not yet judged; none: no host judged; "
                "all: judged on every host",
)
INCLUDE_JUDGED = Query(False, description="The older spelling of judged=any")
DETAIL_SOURCE = Query(
    None, max_length=20,
    description="One scanner's words (a row's `sources`); omitted: the most severe row with a description",
)


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
    # Tests naming this issue on the host: still to do, and results recorded.
    tests_to_do: int = 0
    tests_recorded: int = 0


class IssueDetailOut(BaseModel):
    issue_key: str
    title: str
    # The scanner whose description this is.
    source: str
    description: Optional[str] = None
    solution: Optional[str] = None
    cvss_score: Optional[float] = None
    cvss_vector: Optional[str] = None
    # v2.508.0 — the same row's identifiers and its references (URLs, advisories).
    cve_id: Optional[str] = None
    plugin_id: Optional[str] = None
    plugin_name: Optional[str] = None
    references: List[str] = Field(default_factory=list)


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
    search: Optional[str] = Query(None, max_length=200, description="Title, CVE id or a scanner's check id contains this"),
    severity: Optional[str] = Query(None, max_length=20),
    judged: Optional[IssueJudged] = ISSUE_JUDGED,
    include_judged: bool = INCLUDE_JUDGED,
    min_hosts: int = Query(1, ge=1, le=100000),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    kind: Optional[str] = Query(None, max_length=20,
                                description="misconfiguration | vulnerability | informational"),
    sort: IssueSort = ISSUE_SORT,
    exploitable: bool = Query(False, description="Only issues a scanner reports an exploit for"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    try:
        page = svc.list_issues(
            db, project.id, search=search, severity=severity, judged=svc.resolve_judged(judged, include_judged),
            min_hosts=min_hosts, skip=skip, limit=limit, kind=kind, sort=sort, exploitable=exploitable,
        )
    except svc.ObservationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return IssuePageOut(items=[IssueRowOut(**vars(r)) for r in page.items], total=page.total)


@router.get("/scanner-observations/hosts", response_model=List[IssueHostOut], summary="The hosts carrying one issue")
def list_scanner_observation_hosts(
    issue_key: str = Query(..., min_length=1, max_length=600),
    limit: Optional[int] = Query(None, ge=1, le=5000, description="The first N hosts by address; omitted = all"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return [IssueHostOut(**vars(h)) for h in svc.issue_hosts(db, project.id, issue_key, limit=limit)]


@router.get(
    "/scanner-observations/detail",
    response_model=IssueDetailOut,
    summary="What a scanner wrote about one issue",
    responses={404: {"description": "No scanner observation in this project is that issue"}},
)
def get_scanner_observation_detail(
    issue_key: str = Query(..., min_length=1, max_length=600),
    source: Optional[str] = DETAIL_SOURCE,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    try:
        detail = svc.issue_detail(db, project.id, issue_key, source=source)
    except svc.ObservationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if detail is None:
        raise HTTPException(status_code=404, detail="No scanner observation in this project is that issue")
    return IssueDetailOut(**vars(detail))


@router.get(
    "/scanner-observations/host-output",
    response_model=List[HostVulnerability],
    summary="One issue's scanner rows on one host: port, scanner and what it printed there",
)
def get_scanner_observation_host_output(
    issue_key: str = Query(..., min_length=1, max_length=600),
    host_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The inspector's host row, opened (v2.508.0): the rows as the host
    page serializes them. Empty when the host carries no such row."""
    return svc.issue_host_rows(db, project.id, issue_key, host_id)


class DismissIn(BaseModel):
    issue_key: str = Field(..., min_length=1, max_length=600)
    # Omitted = every host carrying the issue.
    host_ids: Optional[List[int]] = Field(default=None, max_length=10000)
    # Asked for, never required — as on the host page.
    reason: Optional[str] = Field(default=None, max_length=2000)


class DismissOut(BaseModel):
    issue_key: str
    finding_id: int
    host_count: int


@router.post(
    "/scanner-observations/dismiss",
    response_model=DismissOut,
    summary="Mark one issue a false positive, on every host carrying it or on the named ones (analyst)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def dismiss_scanner_observation_issue(
    body: DismissIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """The host page's "Mark false positive", from the list: with no
    ``host_ids`` the issue is a false positive on every host; with them, those
    hosts' endpoints are and the finding's own status is left alone."""
    try:
        outcome = svc.dismiss_issue(
            db, project.id, current_user.id, body.issue_key,
            host_ids=body.host_ids, reason=(body.reason or "").strip() or None,
        )
    except svc.ObservationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    log_audit_event(
        db, user_id=current_user.id, action="scanner_observation_dismissed",
        resource_type="project", resource_id=str(project.id),
        details={
            "finding_id": outcome.finding_id, "hosts": outcome.host_count,
            "scope": "issue" if body.host_ids is None else "hosts",
        },
    )
    return DismissOut(**vars(outcome))


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
