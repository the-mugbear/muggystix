"""Operations workbench — one batched call for the personal-work surface.

Refactor P2.  Operations previously fired four independent requests
(my-attention, my-tasks, team-review, plus a localStorage-only
"new scans" cursor) and stitched them together client-side.  This
endpoint composes them server-side into a single response and adds a
durable **per-user/per-project "since your last visit"** diff backed by
the ``operations_cursors`` table — so "what changed while I was away?"
survives across devices instead of living in one browser's localStorage.

The three section aggregations live in ``operations_read_service`` and are
called as plain functions by both this composer and the standalone
dashboard widgets — one implementation, so the two surfaces can never
drift, with no router-to-router dependency (CR4-2).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import OperationsCursor
from app.db.cursor_upsert import upsert_user_project_cursor
from app.db.models_auth import User
from app.db.models_project import Project
from app.api.deps import get_current_user
from app.api.deps import get_current_project
# CR4-2 — depend on services, not other routers' handlers.  The composition
# (and "since last visit") lives in workbench_service (v2.428.0) so the agent
# read GET /agent/assist/workbench is the same code.
from app.services.address_terrain_service import AddressTerrainResponse, compute_address_terrain
from app.services.operations_read_service import (
    compute_my_activity,
    compute_investigation_queue,
    InvestigationQueueResponse,
    MyActivityResponse,
)
from app.services.workbench_service import WorkbenchResponse, compute_workbench

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MarkSeenRequest(BaseModel):
    # ``SinceLastVisit.as_of`` of the snapshot being acknowledged.  Omitted
    # (first-visit bootstrap, older clients) means "now".
    as_of: Optional[datetime] = None


class MarkSeenResponse(BaseModel):
    last_viewed_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=WorkbenchResponse,
    summary="Operations workbench — personal queue, tasks, team roster, and since-last-visit diff in one call",
)
def get_workbench(
    include_investigate: bool = Query(
        True,
        description=(
            "Include the engagement-wide 'Worth a look' queue. Operations passes "
            "false and loads it from GET /workbench/investigate so the personal "
            "sections are not held up by it (v2.424.1)."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Batch the Operations personal-work surface into one response.

    Reuses the read-service aggregations so the standalone widgets and the
    workbench cannot drift.  ``since_last_visit`` reflects the durable
    per-user cursor; advance it with ``POST /workbench/seen``.
    """
    return compute_workbench(db, current_user, project, include_investigate=include_investigate)


@router.get(
    "/investigate",
    response_model=InvestigationQueueResponse,
    summary="The 'Worth a look' queue alone — untouched hosts with a reason, in stated tier order",
)
def get_investigation_queue(
    tier: Optional[int] = Query(None, ge=1, le=5, description="Only this tier's hosts (1 = exploitable critical … 5 = scans disagree); the totals stay whole-queue"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The same queue ``GET /workbench`` embeds, on its own request (v2.424.1).

    ``tier`` narrows the rows to one tier (v2.427.0); ``queue_total`` and
    ``tier_counts`` still describe the whole queue.

    A failure is a 503 that says so — never an empty queue, which would read
    as "every host has been touched".
    """
    try:
        return compute_investigation_queue(db, project, limit=25, tier=tier)
    except Exception:
        logger.exception("investigation queue failed for project %s", project.id)
        db.rollback()
        raise HTTPException(status_code=503, detail="The 'Worth a look' queue could not be computed.")


@router.get(
    "/terrain",
    response_model=AddressTerrainResponse,
    summary="Hosts by address block (/24, IPv6 /64), counted by how far the team has taken them",
)
def get_address_terrain(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The Operations terrain (v2.426.0): per block, hosts tested / planned /
    worked / untouched (exclusive, adding up to ``hosts``) and the critical
    exposure nobody has touched.  503 on failure — never an empty map."""
    try:
        return compute_address_terrain(db, project)
    except Exception:
        logger.exception("address terrain failed for project %s", project.id)
        db.rollback()
        raise HTTPException(status_code=503, detail="The address terrain could not be computed.")


@router.post(
    "/seen",
    response_model=MarkSeenResponse,
    summary="Advance the caller's Operations 'since last visit' cursor to the acknowledged snapshot",
)
def mark_workbench_seen(
    body: Optional[MarkSeenRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upsert the caller's cursor for this project.

    With ``as_of`` the cursor moves to the snapshot the caller was shown, so
    changes that arrived after the summary loaded are not acknowledged unseen;
    without it, to the current time.  Never later than now, never backwards
    (the upsert is monotonic).  Idempotent: one row per (user, project).
    """
    now = datetime.now(timezone.utc)
    target = now
    if body is not None and body.as_of is not None:
        as_of = body.as_of
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        target = min(as_of, now)
    # Race-safe upsert (review #9) — concurrent first-time visits across
    # tabs/devices would otherwise collide on the unique constraint.
    upsert_user_project_cursor(
        db, OperationsCursor,
        user_id=current_user.id, project_id=project.id,
        ts_column="last_viewed_at", ts_value=target,
    )
    return MarkSeenResponse(last_viewed_at=target)


@router.get(
    "/my-activity",
    response_model=MyActivityResponse,
    summary="The caller's recent work history across notes, findings, and reviews",
)
def get_my_activity(
    limit: int = 20,
    kinds: Optional[str] = Query(
        None, description="Comma list of event kinds to include: note, finding_created, finding_status, host_reviewed.",
    ),
    days: Optional[int] = Query(None, ge=1, le=3650, description="Only events within the last N days."),
    search: Optional[str] = Query(None, max_length=200, description="Case-insensitive substring (note body, finding title, or host ip/hostname)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """§27 personal work history — a unified, newest-first feed of what the
    caller did (notes authored, findings created/promoted/dispositioned, hosts
    reviewed), with optional kind / date-range / search filters. Separate from
    the batched workbench so it can grow without bloating that call."""
    limit = max(1, min(limit, 100))
    kind_set = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None
    return compute_my_activity(
        db, current_user, project, limit=limit, kinds=kind_set, days=days, search=search,
    )
