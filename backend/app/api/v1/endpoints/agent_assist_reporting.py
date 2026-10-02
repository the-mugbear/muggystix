"""Agent reads of the Findings hub's other two pages (v2.428.0).

An agent could list findings and read one, but not the two views beside them
that answer "what did the scanners find that nobody has judged yet?" and
"what did we tell the client?":

* ``GET /assist/scanner-observations`` (+ ``/hosts``) — the Findings page's
  *Scanner observations* view: each issue once across the project with its
  host count and how many of those a finding already covers.  Read-only;
  promotion stays a person's call.
* ``GET /assist/client-reports`` (+ ``/{id}``, ``/{id}/files/{fmt}``) — the
  Reports page: drafts and issued reports, what each one says, and its files.

Every read wraps the service the page itself uses (``scanner_observation_service``,
``ClientReportService`` and the page's own serializer), so an agent and the
page cannot disagree on a number (ASSIST_TOOLS.md, review rule 3).

Mounted under ``/agent`` with the same ``enforce_agent_operator_access`` gate
as every agent router; the client-report routes also take the Reports page's
floor, AUDITOR (``deps.AGENT_READ_ROLE_OVERRIDES``).
"""
from __future__ import annotations

from typing import Literal, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, defer, selectinload

from app.api.deps import check_agent_rate_limit
from app.api.v1.endpoints.agent_common import load_agent_session
from app.api.v1.endpoints.client_reports import report_file_response, report_scope_response
from app.api.v1.endpoints.scanner_observations import IssueHostOut, IssuePageOut, IssueRowOut
from app.db.models_agent import Agent
from app.db.models_project import ProjectRole
from app.db.models_reports import Report, ReportStatus
from app.db.session import get_db
from app.schemas.client_reports import ReportFileOut, ReportOut
from app.services import scanner_observation_service as observations
from app.services.client_report_service import ClientReportService
# The Reports page's own serializer and loader: the agent sees the report the
# page shows, field for field.
from app.services.client_report_views import load_report, serialize_report

router = APIRouter()


def _operator_role(request: Request) -> Optional[str]:
    """The operator's project role as ``enforce_agent_operator_access``
    resolved it for this request (a global admin reads as ``admin``)."""
    if getattr(request.state, "key_operator_is_admin", False):
        return ProjectRole.ADMIN.value
    role = getattr(request.state, "key_operator_role", None)
    return getattr(role, "value", role)



# ---------------------------------------------------------------------------
# Scanner observations — the Findings page's "Scanner observations" view
# ---------------------------------------------------------------------------

@router.get(
    "/assist/scanner-observations",
    response_model=IssuePageOut,
    summary="Scanner observations, one row per issue across the project",
)
def list_assist_scanner_observations(
    request: Request,
    search: Optional[str] = Query(None, max_length=200),
    severity: Optional[str] = Query(None, max_length=20),
    include_judged: bool = Query(False, description="Also list issues a finding already covers on every host"),
    min_hosts: int = Query(1, ge=1, le=100000),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    kind: Optional[str] = Query(None, max_length=20,
                                description="misconfiguration | vulnerability | informational"),
    sort: Literal["severity", "hosts"] = Query(
        "severity", description="severity: most severe first (default); hosts: most widespread first"),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = load_agent_session(db, request)
    try:
        page = observations.list_issues(
            db, session.project_id, search=search, severity=severity, include_judged=include_judged,
            min_hosts=min_hosts, skip=skip, limit=limit, kind=kind, sort=sort,
        )
    except observations.ObservationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return IssuePageOut(items=[IssueRowOut(**vars(r)) for r in page.items], total=page.total)


class AssistIssueHostPage(BaseModel):
    """One page of an issue's hosts.  v2.429.1 (MCP acceptance run 2): the
    bare list stopped at the limit without saying so — 100 of an issue's 146
    hosts read as the whole list."""
    items: List[IssueHostOut]
    total: int = Field(..., description="Hosts carrying the issue — the page's rows are `limit` of them from `offset`")
    has_more: bool
    limit: int
    offset: int


@router.get(
    "/assist/scanner-observations/hosts",
    response_model=AssistIssueHostPage,
    summary="The hosts carrying one scanner issue, paged by address",
)
def list_assist_scanner_observation_hosts(
    request: Request,
    issue_key: str = Query(..., min_length=1, max_length=600),
    limit: int = Query(500, ge=1, le=5000, description="Hosts per page, by address"),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = load_agent_session(db, request)
    items = [
        IssueHostOut(**vars(h))
        for h in observations.issue_hosts(db, session.project_id, issue_key, limit=limit, offset=offset)
    ]
    total = observations.issue_host_total(db, session.project_id, issue_key)
    return AssistIssueHostPage(
        items=items, total=total, has_more=offset + len(items) < total, limit=limit, offset=offset,
    )


# ---------------------------------------------------------------------------
# Client reports — the Reports page
# ---------------------------------------------------------------------------



class AssistReportFile(ReportFileOut):
    download_path: str = Field(..., description="GET with the session's API key; saves the file")


class AssistReportFinding(BaseModel):
    """One finding as the report states it (its report text, reference and
    the endpoints it lists) — not the finding's current state."""
    id: Optional[int] = None
    ref: Optional[str] = None
    title: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    status_note: Optional[str] = None
    cvss_score: Optional[float] = None
    cvss_vector: Optional[str] = None
    description: Optional[str] = None
    impact: Optional[str] = None
    recommendation: Optional[str] = None
    references: Optional[Any] = None
    steps_to_reproduce: Optional[str] = None
    affected: List[Dict[str, Any]] = Field(default_factory=list)
    affected_count: int = 0
    # Addendum only: why it is in this document ("new" / "new_hosts" /
    # "severity_changed") and, for "new_hosts", the endpoints added since the
    # baseline; for a re-rated finding, the severity the baseline reported.
    change: Optional[str] = None
    new_affected: List[Dict[str, Any]] = Field(default_factory=list)
    previous_severity: Optional[str] = None
    previous_severity_label: Optional[str] = None
    # How the finding was confirmed, as the report prints it (templates that
    # opt in): tool, host, command, summary, a trimmed output excerpt, when
    # and by whom.  `confirmations_omitted` = further records not printed.
    confirmations: List[Dict[str, Any]] = Field(default_factory=list)
    confirmations_omitted: int = 0
    # Report images: fetch one with assist_get_image(attachment_id=…).
    # `images` is every image ticked for the report on this finding, each with
    # its caption and `placed_in` — the written sections whose Markdown places
    # it (`![caption](evidence:<attachment_id>)`).  `printed` says whether THIS
    # report's template prints it and `printed_in` in which of those sections
    # (a printed image with `printed_in: []` is in the trailing evidence
    # block); both are null when it could not be measured or the report was
    # issued before it was.  `evidence` is the images no section places — the
    # trailing block's, where the template has one.  A report issued before
    # images could be placed has `evidence` alone.
    images: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class AssistClientReportList(BaseModel):
    items: List[ReportOut]
    latest_issued_id: Optional[int] = None


class AssistClientReport(ReportOut):
    files: List[AssistReportFile] = []  # type: ignore[assignment]
    content_source: str = Field(..., description=(
        "issued_snapshot = the text as issued, frozen; draft_live = what the draft "
        "would say if previewed now"))
    counts: Dict[str, Any] = Field(default_factory=dict)
    findings: List[AssistReportFinding] = Field(default_factory=list)
    delta: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None


def _files_path(report_id: int, fmt: str) -> str:
    return f"/api/v1/agent/assist/client-reports/{report_id}/files/{fmt}"


@router.get(
    "/assist/client-reports",
    response_model=AssistClientReportList,
    summary="The project's client reports — drafts first, then issued ones by number",
)
def list_assist_client_reports(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = load_agent_session(db, request)
    role = _operator_role(request) or ""
    rows = (
        db.query(Report)
        .options(defer(Report.snapshot), selectinload(Report.files))
        .filter(Report.project_id == session.project_id)
        .all()
    )
    # The Reports page's order.
    rows.sort(key=lambda r: (r.status != ReportStatus.DRAFT, -(r.number or 0), -(r.id)))
    latest = ClientReportService(db).latest_issued(session.project_id)
    return AssistClientReportList(
        items=[serialize_report(db, r, role, with_summary=False) for r in rows],
        latest_issued_id=latest.id if latest else None,
    )


@router.get(
    "/assist/client-reports/{report_id}",
    response_model=AssistClientReport,
    summary="One client report: its details, and every finding as the report states it",
)
def get_assist_client_report(
    request: Request,
    report_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = load_agent_session(db, request)
    report = load_report(db, session.project_id, report_id)
    dataset, summary = ClientReportService(db).content(report)
    base = serialize_report(db, report, _operator_role(request) or "", with_summary=False)
    dataset = dataset or {}
    findings = []
    for item in dataset.get("findings") or []:
        row = {k: v for k, v in item.items() if not k.startswith("_")}
        # The render's file names mean nothing outside the render; keep the
        # attachment id (fetchable) and the caption.
        row["evidence"] = [
            {"attachment_id": e.get("attachment_id"), "caption": e.get("caption")}
            for e in (item.get("evidence") or [])
        ]
        row["images"] = [
            {"attachment_id": e.get("attachment_id"), "caption": e.get("caption"),
             "placed_in": list(e.get("placed_in") or []),
             # What the dataset measured (None = not measured, never "no").
             "printed": e.get("printed"),
             "printed_in": list(e["printed_in"]) if e.get("printed_in") is not None else None}
            for e in (item.get("images") or [])
        ]
        row.pop("placed", None)  # the renderer's map; `images[].placed_in` says the same
        # The report's own entries, minus the render's private keys.
        row["confirmations"] = [
            {k: v for k, v in c.items() if not k.startswith("_")}
            for c in (item.get("confirmations") or [])
        ]
        findings.append(AssistReportFinding(**row))
    return AssistClientReport(
        **{
            **base.model_dump(exclude={"files", "summary"}),
            "summary": summary,
            "files": [
                AssistReportFile(**f.model_dump(), download_path=_files_path(report.id, f.format))
                for f in base.files
            ],
            "content_source": "draft_live" if report.status == ReportStatus.DRAFT else "issued_snapshot",
            "counts": dataset.get("counts") or {},
            "findings": findings,
            "delta": dataset.get("delta"),
            "scope": dataset.get("scope"),
        }
    )


@router.get(
    "/assist/client-reports/{report_id}/scope.csv",
    summary="The report's complete scope as CSV — the file a report over its template's scope cutoff names",
)
def download_assist_client_report_scope(
    request: Request,
    report_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """v2.441.0 — the page's scope-file download, for agents (same builder,
    same bytes, same SHA-256 as the report prints)."""
    session = load_agent_session(db, request)
    report = load_report(db, session.project_id, report_id)
    return report_scope_response(ClientReportService(db), report)


@router.get(
    "/assist/client-reports/{report_id}/files/{fmt}",
    summary="Download one rendered file of a client report (html, docx, qmd)",
)
def download_assist_client_report_file(
    request: Request,
    report_id: int = Path(..., ge=1),
    fmt: str = Path(..., min_length=1, max_length=20),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
) -> FileResponse:
    session = load_agent_session(db, request)
    report = load_report(db, session.project_id, report_id)
    record = next((f for f in report.files if f.format == fmt), None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"This report has no {fmt} file.")
    return report_file_response(record)
