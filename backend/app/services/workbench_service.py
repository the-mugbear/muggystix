"""The Operations workbench — composition of the personal-work surface.

Moved out of the ``/workbench`` route in v2.428.0 so the agent read ``GET
/agent/assist/workbench`` returns the SAME sections, computed by the same
code, relative to the key's operator.  The section aggregations themselves
live in ``operations_read_service``; this module composes them and computes
the durable per-user "since your last visit" diff (``operations_cursors``).

Reading never moves the cursor: only ``POST /workbench/seen`` (a person
acknowledging what they were shown) does.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, String
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import OperationsCursor
from app.db.models_vulnerability import Vulnerability
from app.db.models_auth import User
from app.db.models_project import Project
from app.services import host_query_predicates as P
from app.services.operations_read_service import (
    compute_my_attention_queue,
    compute_my_tasks,
    compute_my_assigned_notes,
    compute_my_recent_notes,
    compute_my_findings,
    compute_team_review,
    compute_investigation_queue,
    compute_review_followups,
    compute_blockers,
    OperationsBlockers,
    InvestigationQueueResponse,
    ReviewFollowupsResponse,
    MyAttentionResponse,
    MyTasksResponse,
    MyNotesResponse,
    MyRecentNotesResponse,
    MyFindingsResponse,
    TeamReviewResponse,
)

logger = logging.getLogger(__name__)


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
    # v2.363.0 — hosts already known before the window that gained a port or
    # a scanner observation in it.  Disjoint from ``new_host_count``.
    changed_host_count: int = 0
    # Named "findings" since before the vocabulary was settled: these count
    # SCANNER OBSERVATIONS (raw vulnerability rows), not judged findings.  The
    # field names stay for older clients; the UI labels them correctly.
    new_critical_findings: int = 0
    new_high_findings: int = 0
    # How many hosts carry those observations — what the drill-down lists.
    new_critical_hosts: int = 0
    new_high_hosts: int = 0
    # When these counts were taken.  The client hands it back to
    # ``POST /workbench/seen`` so acknowledging covers the snapshot that was
    # displayed, not whatever arrived between the load and the click.
    as_of: Optional[datetime] = None

    @property
    def has_updates(self) -> bool:  # convenience, not serialized
        return bool(
            self.new_scan_count or self.new_host_count or self.changed_host_count
            or self.new_critical_findings or self.new_high_findings
        )


class WorkbenchResponse(BaseModel):
    my_queue: MyAttentionResponse = Field(default_factory=MyAttentionResponse)
    my_tasks: MyTasksResponse = Field(default_factory=MyTasksResponse)
    # P0 — My Work resume pass: assigned note threads + owned findings, so the
    # card surfaces the annotation/finding work an analyst owns, not just
    # in-review hosts and plan steps.
    my_notes: MyNotesResponse = Field(default_factory=MyNotesResponse)
    # "What was I just doing?" — the caller's latest authored notes, distinct
    # from my_notes (the assigned-work queue).
    recent_notes: MyRecentNotesResponse = Field(default_factory=MyRecentNotesResponse)
    my_findings: MyFindingsResponse = Field(default_factory=MyFindingsResponse)
    team_review: TeamReviewResponse = Field(default_factory=TeamReviewResponse)
    since_last_visit: SinceLastVisit = Field(default_factory=SinceLastVisit)
    # v2.347.0 — engagement-wide: untouched hosts worth a look (design
    # review item 2), beneath the personal queue on My Work.
    # v2.424.1 — ``None`` when the caller asked for the workbench without it
    # (``include_investigate=false``) and fetches ``GET /workbench/investigate``
    # separately: the queue is most of the time on a large project, and the
    # personal sections should not wait for it.
    investigate: Optional[InvestigationQueueResponse] = Field(default_factory=InvestigationQueueResponse)
    # True when the queue could not be computed: ``investigate`` is then an
    # empty placeholder and must read as "unavailable", never as "no work".
    investigate_unavailable: bool = False
    # v2.359.0 — reviewed hosts that are not done: concluded "needs more
    # evidence", or changed after the review.  Same failure contract as the
    # queue above: unavailable is said, never rendered as "nothing owed".
    followups: ReviewFollowupsResponse = Field(default_factory=ReviewFollowupsResponse)
    followups_unavailable: bool = False
    # v2.363.0 — work that has stopped and will not resume by itself: failed /
    # partial imports nobody dismissed, execution runs that are paused or whose
    # agent session ended.  Project-wide.  Same failure contract.
    blockers: OperationsBlockers = Field(default_factory=OperationsBlockers)
    blockers_unavailable: bool = False


def get_cursor(db: Session, user_id: int, project_id: int) -> Optional[OperationsCursor]:
    return (
        db.query(OperationsCursor)
        .filter(
            OperationsCursor.user_id == user_id,
            OperationsCursor.project_id == project_id,
        )
        .first()
    )


def compute_since_last_visit(
    db: Session, user: User, project: Project,
) -> SinceLastVisit:
    # Taken BEFORE the counts: anything landing mid-computation stays newer
    # than the acknowledged snapshot and resurfaces on the next load.
    as_of = datetime.now(timezone.utc)
    cursor = get_cursor(db, user.id, project.id)
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

    # v2.363.0 — the host and observation counts come from the SAME window
    # predicates the DSL fields `firstseen:` / `changedsince:` / `vulnsince:`
    # use, over the same (last_viewed, as_of] window, so each count on the
    # banner opens exactly the hosts it counted.
    host_base = db.query(func.count(models.Host.id)).filter(
        models.Host.project_id == project.id
    )
    sev_col = func.lower(Vulnerability.severity.cast(String))
    sev_base = (
        db.query(
            sev_col,
            func.count(Vulnerability.id),
            func.count(func.distinct(Vulnerability.host_id)),
        )
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id, sev_col.in_(("critical", "high")))
    )
    if last_viewed is not None:
        new_host_count = host_base.filter(
            P.first_seen_window_predicate(last_viewed, as_of)
        ).scalar() or 0
        # Existing targets that gained a port or an observation — kept apart
        # from new records: "12 new hosts" and "3 known hosts changed" are
        # different work.
        changed_host_count = host_base.filter(
            P.changed_window_predicate(db, project.id, last_viewed, as_of)
        ).scalar() or 0
        sev_base = sev_base.filter(P.vuln_window_condition(last_viewed, as_of))
    else:
        new_host_count = host_base.scalar() or 0
        changed_host_count = 0
    # Scanner observations by severity — ONE grouped query for critical +
    # high (review #7), now also counting the hosts that carry them.
    sev_rows = {row[0]: (int(row[1]), int(row[2])) for row in sev_base.group_by(sev_col).all()}

    return SinceLastVisit(
        last_viewed_at=last_viewed,
        is_first_visit=is_first,
        new_scan_count=new_scan_count,
        latest_scan_id=latest.id if latest else None,
        latest_scan_filename=latest.filename if latest else None,
        latest_scan_created_at=latest.created_at if latest else None,
        new_host_count=new_host_count,
        changed_host_count=changed_host_count,
        new_critical_findings=sev_rows.get("critical", (0, 0))[0],
        new_high_findings=sev_rows.get("high", (0, 0))[0],
        new_critical_hosts=sev_rows.get("critical", (0, 0))[1],
        new_high_hosts=sev_rows.get("high", (0, 0))[1],
        as_of=as_of,
    )


def compute_workbench(
    db: Session, current_user: User, project: Project, include_investigate: bool = True,
) -> WorkbenchResponse:
    """Batch the Operations personal-work surface into one response, for
    ``current_user``.  Each engagement-wide section that fails is reported as
    ``*_unavailable`` rather than rendered as an empty (``nothing to do``) one."""
    my_queue = compute_my_attention_queue(db, current_user, project, limit=10)
    my_tasks = compute_my_tasks(db, current_user, project, limit=15)
    my_notes = compute_my_assigned_notes(db, current_user, project, limit=15)
    recent_notes = compute_my_recent_notes(db, current_user, project, limit=8)
    my_findings = compute_my_findings(db, current_user, project, limit=15)
    team_review = compute_team_review(db, current_user, project, limit=500)
    since = compute_since_last_visit(db, current_user, project)
    investigate_unavailable = False
    investigate: Optional[InvestigationQueueResponse] = None
    try:
        if include_investigate:
            investigate = compute_investigation_queue(db, project, limit=25)
    except Exception:
        # The personal queue must not go down with the engagement-wide one —
        # but say so: an empty queue reads as "every host has been touched".
        logger.exception("investigation queue failed for project %s", project.id)
        db.rollback()
        investigate = InvestigationQueueResponse()
        investigate_unavailable = True
    followups_unavailable = False
    try:
        followups = compute_review_followups(db, current_user, project, limit=15)
    except Exception:
        logger.exception("review follow-ups failed for project %s", project.id)
        db.rollback()
        followups = ReviewFollowupsResponse()
        followups_unavailable = True
    blockers_unavailable = False
    try:
        blockers = compute_blockers(db, project)
    except Exception:
        # Same contract: a failure is said, never rendered as "nothing blocked".
        logger.exception("blockers failed for project %s", project.id)
        db.rollback()
        blockers = OperationsBlockers()
        blockers_unavailable = True

    return WorkbenchResponse(
        my_queue=my_queue,
        my_tasks=my_tasks,
        my_notes=my_notes,
        recent_notes=recent_notes,
        my_findings=my_findings,
        team_review=team_review,
        since_last_visit=since,
        investigate=investigate,
        investigate_unavailable=investigate_unavailable,
        followups=followups,
        followups_unavailable=followups_unavailable,
        blockers=blockers,
        blockers_unavailable=blockers_unavailable,
    )
