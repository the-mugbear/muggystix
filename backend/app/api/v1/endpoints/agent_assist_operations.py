"""Agent API — the Operations and Evidence reads (v2.428.0).

An agent asked "what's worth a look?", "what's mine?", "what changed since I
was last here?", "what changed between these two scans?" or "what is still
unassessed in segment Y?" had no read that could answer: those live on the
Operations workbench, the scan compare and the Evidence gap lists, which were
JWT-only.  These routes are those pages for a key, and each one calls the SAME
service its page calls — never a second rollup (ASSIST_TOOLS.md review rule 3:
an agent and a page disagreeing on a number is worse than the agent not having
it).

* ``GET /assist/workbench``            → ``workbench_service.compute_workbench``
* ``GET /assist/workbench/investigate`` → ``operations_read_service.compute_investigation_queue``
* ``GET /assist/workbench/terrain``    → ``address_terrain_service.compute_address_terrain``
* ``GET /assist/evidence/gaps?domain=`` → ``evidence_service.evidence_gap_hosts``
* ``GET /assist/scans/compare``        → ``scan_diff_service.compute_scan_diff``

The personal sections are the key's OPERATOR's — the person the session acts
for — exactly as that person would see them.  Reading never acknowledges
anything: the "since your last visit" cursor moves only when the operator
marks Operations seen in the app, so an agent summarising the changes cannot
make them disappear from the operator's banner.

Gated like every ``/agent`` route (router-level ``enforce_agent_operator_access``):
the operator's current project membership, VIEWER for reads — the same floor
as the pages these mirror.
"""
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import check_agent_rate_limit
from app.api.v1.endpoints.agent_assist import _load_assist_session
from app.db.models_agent import Agent, AgentSession
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db
from app.services.address_terrain_service import TerrainBlock, compute_address_terrain
from app.services.evidence_service import DOMAIN_LABELS, evidence_gap_hosts
from app.services.operations_read_service import (
    InvestigationQueueResponse,
    compute_investigation_queue,
)
from app.services.scan_diff_service import ScanDiffResponse, ScanNotInProject, compute_scan_diff
from app.services.workbench_service import WorkbenchResponse, compute_workbench

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas (the service models where one exists; these only where the agent
# shape differs — paging or trimming)
# ---------------------------------------------------------------------------

class AgentTerrainResponse(BaseModel):
    """The Operations terrain, ordered and cut for an agent.  ``blocks_total``
    is every block the service returned; ``truncated`` is the service's own
    flag (more blocks than it draws), ``limited`` says this page left some out."""
    blocks: List[TerrainBlock]
    blocks_total: int = 0
    total_hosts: int = 0
    unplaced_hosts: int = 0
    truncated: bool = False
    limited: bool = False
    sort: str = "address"


class AgentEvidenceGapsResponse(BaseModel):
    """``GET /posture/evidence/{domain}/gaps`` as the agent receives it — the
    same service payload (hosts, the ports that made them eligible, the step
    that closes the gap, the scope caution)."""
    model_config = ConfigDict(extra="allow")
    domain: str
    label: str
    total: int
    items: List[Dict[str, Any]]
    action: Dict[str, str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_and_operator(db: Session, session: AgentSession) -> tuple:
    project = db.get(Project, session.project_id)
    if project is None:  # the operator gate already 404s/410s; belt and braces
        raise HTTPException(status_code=404, detail="Project not found")
    operator: Optional[User] = session.started_by
    return project, operator


# ---------------------------------------------------------------------------
# Workbench
# ---------------------------------------------------------------------------

@router.get(
    "/assist/workbench",
    response_model=WorkbenchResponse,
    summary="The operator's Operations workbench — my queue, tasks, notes, findings, since last visit, follow-ups, blockers",
)
def get_assist_workbench(
    request: Request,
    include_investigate: bool = Query(
        False,
        description=(
            "Embed the first 25 rows of the 'Worth a look' queue. Off by default: "
            "page it with GET /assist/workbench/investigate instead."
        ),
    ),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """What the session's operator sees on Operations → My work, computed by
    the same service: ``my_queue`` (hosts they are reviewing), ``my_tasks``
    (plan steps), ``my_notes`` (note threads assigned to them),
    ``recent_notes``, ``my_findings`` (findings they own), ``team_review``,
    ``since_last_visit`` (scans, new hosts, changed hosts, new critical/high
    scanner observations since the operator last marked Operations seen),
    ``followups`` ("needs another look") and ``blockers`` (stopped imports and
    runs).

    Read-only: this never moves the operator's "since last visit" cursor.  A
    section reported ``*_unavailable: true`` could not be computed — say so;
    it does NOT mean there is nothing there.
    """
    session = _load_assist_session(db, request)
    project, operator = _project_and_operator(db, session)
    if operator is None:
        raise HTTPException(
            status_code=400,
            detail="This session has no operator bound; the workbench is personal to one.",
        )
    return compute_workbench(db, operator, project, include_investigate=include_investigate)


@router.get(
    "/assist/workbench/investigate",
    response_model=InvestigationQueueResponse,
    summary="'Worth a look' — untouched hosts with a stated reason, in tier order",
)
def get_assist_investigation_queue(
    request: Request,
    tier: Optional[int] = Query(
        None, ge=1, le=5,
        description="Only this tier (1 exploitable critical, 2 critical vulnerability, 3 exploit available, 4 high-value service new or changed, 5 scans disagree). Totals stay whole-queue.",
    ),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The engagement-wide queue on Operations: hosts NOBODY has touched (no
    review, assignment, note, plan entry or finding) that carry an observed
    weakness or a relevant change.  Each row carries its reasons, evidence and
    next action; ``tier_counts`` (aligned with ``tiers``) and ``queue_total``
    describe the whole queue whatever ``tier``/``offset`` you pass.  The order
    is the stated tier, then most recently seen — never a score.

    503 when the queue cannot be computed — never an empty queue, which would
    read as "every host has been touched".
    """
    session = _load_assist_session(db, request)
    project, _ = _project_and_operator(db, session)
    try:
        return compute_investigation_queue(db, project, limit=limit, tier=tier, offset=offset)
    except Exception:
        logger.exception("investigation queue failed for project %s", project.id)
        db.rollback()
        raise HTTPException(status_code=503, detail="The 'Worth a look' queue could not be computed.")


@router.get(
    "/assist/workbench/terrain",
    response_model=AgentTerrainResponse,
    summary="Hosts per address block (/24, IPv6 /64): tested / planned / worked / untouched",
)
def get_assist_terrain(
    request: Request,
    sort: Literal["address", "untouched", "critical_untouched"] = Query(
        "address", description="Block order: by address (as drawn), or most untouched / most untouched critical first.",
    ),
    limit: int = Query(100, ge=1, le=1000),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The Operations terrain as numbers: per block, hosts ``tested`` /
    ``planned`` / ``worked`` / ``untouched`` (exclusive, adding up to
    ``hosts``), plus ``critical`` and ``critical_untouched``.  Answers "which
    ranges has nobody touched?".  503 on failure — never an empty map."""
    session = _load_assist_session(db, request)
    project, _ = _project_and_operator(db, session)
    try:
        terrain = compute_address_terrain(db, project)
    except Exception:
        logger.exception("address terrain failed for project %s", project.id)
        db.rollback()
        raise HTTPException(status_code=503, detail="The address terrain could not be computed.")
    blocks = list(terrain.blocks)
    if sort == "untouched":
        blocks.sort(key=lambda b: (-b.untouched, -b.hosts))
    elif sort == "critical_untouched":
        blocks.sort(key=lambda b: (-b.critical_untouched, -b.untouched))
    return AgentTerrainResponse(
        blocks=blocks[:limit],
        blocks_total=len(blocks),
        total_hosts=terrain.total_hosts,
        unplaced_hosts=terrain.unplaced_hosts,
        truncated=terrain.truncated,
        limited=len(blocks) > limit,
        sort=sort,
    )


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------

@router.get(
    "/assist/evidence/gaps",
    response_model=AgentEvidenceGapsResponse,
    summary="Hosts an assessment domain applies to that carry no evidence in it",
)
def get_assist_evidence_gaps(
    request: Request,
    # A query parameter, not a path segment as on the page: MCP path params
    # are ids (integers) by the transport's rule, and a domain is a word.
    domain: str = Query(..., max_length=64, description=f"One of: {', '.join(DOMAIN_LABELS)}"),
    segment: Optional[str] = Query(
        None, max_length=64,
        description="A segment key from assist_get_coverage's matrix — narrows to one cell.",
    ),
    limit: int = Query(200, ge=1, le=1000),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The Evidence page's gap list: the eligible-but-unassessed hosts for a
    domain (optionally one segment), the open ports that made each eligible,
    and the collection or planning step that closes the gap (``action``).
    ``total`` is exact; ``items`` is cut at ``limit``.  Read ``scope_caution``
    when present — hosts outside the declared scope must be confirmed in scope
    before anyone collects against them."""
    session = _load_assist_session(db, request)
    result = evidence_gap_hosts(db, session.project_id, domain, limit=limit, segment=segment)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown evidence domain or segment")
    return result


# ---------------------------------------------------------------------------
# Scan compare
# ---------------------------------------------------------------------------

@router.get(
    "/assist/scans/compare",
    response_model=ScanDiffResponse,
    summary="What changed between two scans — hosts and ports appeared, vanished, changed",
)
def get_assist_scan_compare(
    request: Request,
    a: int = Query(..., description="Baseline scan id (assist_list_scans)"),
    b: int = Query(..., description="Comparison scan id"),
    limit: int = Query(200, ge=1, le=500, description="Rows per list; `counts` stays exact."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The Scans page's compare: from per-scan history, which hosts are new in
    ``b`` / gone from ``b`` / changed state, which ports newly opened, which
    closed (observed not-open in ``b``) and which were ``not_observed`` (``b``
    never looked — NOT evidence of remediation).  Both ids are required: pick
    them from ``assist_list_scans`` (compare scans of the same targets and
    tool, or the difference is coverage, not change)."""
    session = _load_assist_session(db, request)
    try:
        return compute_scan_diff(db, session.project_id, a, b, row_cap=limit)
    except ScanNotInProject as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Scan(s) not found in project: {', '.join(str(x) for x in exc.missing)}",
        )
