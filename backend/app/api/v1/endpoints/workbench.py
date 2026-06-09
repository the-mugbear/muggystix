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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, String
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import OperationsCursor
from app.db.cursor_upsert import upsert_user_project_cursor
from app.db.models_vulnerability import Vulnerability
from app.db.models_auth import User
from app.db.models_project import Project
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
# CR4-2 — depend on the read service, not the dashboard route handlers.
# Previously this composed the workbench by *calling* dashboard's route
# functions, making one router depend on another's handlers.
from app.services.operations_read_service import (
    compute_my_attention_queue,
    compute_my_tasks,
    compute_my_assigned_notes,
    compute_my_findings,
    compute_team_review,
    MyAttentionResponse,
    MyTasksResponse,
    MyNotesResponse,
    MyFindingsResponse,
    TeamReviewResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SinceLastVisit(BaseModel):
    """What changed in this project since the caller last marked Operations
    seen.  ``last_viewed_at`` is null on a first-ever visit, in which case
    everything counts as new and ``is_first_visit`` is True (the client
    should suppress a noisy "all N hosts are new" banner)."""
    last_viewed_at: Optional[datetime] = None
    is_first_visit: bool = True
    new_scan_count: int = 0
    latest_scan_id: Optional[int] = None
    latest_scan_filename: Optional[str] = None
    latest_scan_created_at: Optional[datetime] = None
    new_host_count: int = 0
    new_critical_findings: int = 0
    new_high_findings: int = 0

    @property
    def has_updates(self) -> bool:  # convenience, not serialized
        return bool(
            self.new_scan_count or self.new_host_count
            or self.new_critical_findings or self.new_high_findings
        )


class WorkbenchResponse(BaseModel):
    my_queue: MyAttentionResponse = Field(default_factory=MyAttentionResponse)
    my_tasks: MyTasksResponse = Field(default_factory=MyTasksResponse)
    # P0 — My Work resume pass: assigned note threads + owned findings, so the
    # card surfaces the annotation/finding work an analyst owns, not just
    # in-review hosts and plan steps.
    my_notes: MyNotesResponse = Field(default_factory=MyNotesResponse)
    my_findings: MyFindingsResponse = Field(default_factory=MyFindingsResponse)
    team_review: TeamReviewResponse = Field(default_factory=TeamReviewResponse)
    since_last_visit: SinceLastVisit = Field(default_factory=SinceLastVisit)


class MarkSeenResponse(BaseModel):
    last_viewed_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cursor(db: Session, user_id: int, project_id: int) -> Optional[OperationsCursor]:
    return (
        db.query(OperationsCursor)
        .filter(
            OperationsCursor.user_id == user_id,
            OperationsCursor.project_id == project_id,
        )
        .first()
    )


def _compute_since_last_visit(
    db: Session, user: User, project: Project,
) -> SinceLastVisit:
    cursor = _get_cursor(db, user.id, project.id)
    last_viewed = cursor.last_viewed_at if cursor else None
    is_first = last_viewed is None

    # Scans — latest row + total count in ONE query via a COUNT() window
    # (was a separate count() + first(); review #6).
    scan_q = db.query(
        models.Scan, func.count().over().label("total"),
    ).filter(models.Scan.project_id == project.id)
    if last_viewed is not None:
        scan_q = scan_q.filter(models.Scan.created_at > last_viewed)
    latest_row = scan_q.order_by(models.Scan.created_at.desc()).first()
    if latest_row is None:
        latest, new_scan_count = None, 0
    else:
        latest, new_scan_count = latest_row[0], int(latest_row[1])

    # Hosts (first_seen is the discovery timestamp)
    host_q = db.query(func.count(models.Host.id)).filter(
        models.Host.project_id == project.id
    )
    if last_viewed is not None:
        host_q = host_q.filter(models.Host.first_seen > last_viewed)
    new_host_count = host_q.scalar() or 0

    # Findings by severity — ONE grouped query for critical + high (was two
    # separate scans; review #7).  Cast the PG enum to text before lower(),
    # same approach portfolio.py uses to avoid an enum type mismatch.
    sev_col = func.lower(Vulnerability.severity.cast(String))
    sev_q = (
        db.query(sev_col, func.count(Vulnerability.id))
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            sev_col.in_(("critical", "high")),
        )
    )
    if last_viewed is not None:
        sev_q = sev_q.filter(Vulnerability.created_at > last_viewed)
    sev_counts = dict(sev_q.group_by(sev_col).all())

    return SinceLastVisit(
        last_viewed_at=last_viewed,
        is_first_visit=is_first,
        new_scan_count=new_scan_count,
        latest_scan_id=latest.id if latest else None,
        latest_scan_filename=latest.filename if latest else None,
        latest_scan_created_at=latest.created_at if latest else None,
        new_host_count=new_host_count,
        new_critical_findings=int(sev_counts.get("critical", 0)),
        new_high_findings=int(sev_counts.get("high", 0)),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=WorkbenchResponse,
    summary="Operations workbench — personal queue, tasks, team roster, and since-last-visit diff in one call",
)
def get_workbench(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Batch the Operations personal-work surface into one response.

    Reuses the read-service aggregations so the standalone widgets and the
    workbench cannot drift.  ``since_last_visit`` reflects the durable
    per-user cursor; advance it with ``POST /workbench/seen``.
    """
    my_queue = compute_my_attention_queue(db, current_user, project, limit=10)
    my_tasks = compute_my_tasks(db, current_user, project, limit=15)
    my_notes = compute_my_assigned_notes(db, current_user, project, limit=15)
    my_findings = compute_my_findings(db, current_user, project, limit=15)
    team_review = compute_team_review(db, current_user, project, limit=500)
    since = _compute_since_last_visit(db, current_user, project)

    return WorkbenchResponse(
        my_queue=my_queue,
        my_tasks=my_tasks,
        my_notes=my_notes,
        my_findings=my_findings,
        team_review=team_review,
        since_last_visit=since,
    )


@router.post(
    "/seen",
    response_model=MarkSeenResponse,
    summary="Advance the caller's Operations 'since last visit' cursor to now",
)
def mark_workbench_seen(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upsert the caller's cursor for this project to the current time.

    Idempotent: one row per (user, project); subsequent calls just move
    the timestamp forward.
    """
    now = datetime.now(timezone.utc)
    # Race-safe upsert (review #9) — concurrent first-time visits across
    # tabs/devices would otherwise collide on the unique constraint.
    upsert_user_project_cursor(
        db, OperationsCursor,
        user_id=current_user.id, project_id=project.id,
        ts_column="last_viewed_at", ts_value=now,
    )
    return MarkSeenResponse(last_viewed_at=now)
