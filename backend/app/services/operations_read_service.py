"""Operations read-model service (CR4-2).

The personal-work aggregations — **My Queue** (In Review hosts), **Team
Review** (the project review roster), and **My Tasks** (assigned +
in-review + triage test-plan entries) — used to live as route handlers in
``dashboard.py`` and were reused by ``workbench.py`` by *calling those
route functions directly*.  That made a service (the workbench composer)
depend on routers, and a router call another router's handler — FastAPI
``Depends`` defaults, response shapes, and import order all became load-
bearing for internal code.

This module owns the query logic and its DTOs.  Routers (``dashboard.py``,
``workbench.py``) now call ``compute_*`` and map the result to HTTP; the
DTOs are imported from here (dashboard re-exports them for back-compat with
any caller that imported the names from the router).

No router imports here — only models and sibling services — so there is no
cycle and the aggregations are unit-testable without a request.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, distinct, false, func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_agent import TestPlan, TestPlanEntry
from app.db.models_auth import User
from app.db.models_project import Project
from app.services.vulnerability_service import VulnerabilityService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# My Queue — the caller's In Review hosts
# ---------------------------------------------------------------------------

class MyAttentionHost(BaseModel):
    """One row of the dashboard's personal attention queue."""
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    follow_status: str  # always "in_review" — kept on the schema for client back-compat
    open_port_count: int = 0
    critical_vulns: int = 0
    high_vulns: int = 0
    last_viewed_at: Optional[datetime] = None
    follow_updated_at: Optional[datetime] = None


class MyAttentionResponse(BaseModel):
    """Personal attention queue payload for the dashboard widget."""
    items: List[MyAttentionHost] = Field(default_factory=list)
    in_review_count: int = 0
    # Kept on the schema as 0 for client back-compat — Watching is no
    # longer surfaced by the queue widget.
    watching_count: int = 0


def compute_my_attention_queue(
    db: Session, current_user: User, project: Project, limit: int = 10,
) -> MyAttentionResponse:
    """Return the current user's personal review queue for this project.

    Returns hosts the user has explicitly marked **In Review**, sorted
    by most recent follow update.  Watching hosts are *not* returned —
    Watching represents passive monitoring, not active work, and the
    queue widget is for "what do I need to do today?".
    """
    # Pull the user's in_review follow rows scoped to this project.
    follows = (
        db.query(HostFollow, models.Host)
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .filter(
            HostFollow.user_id == current_user.id,
            models.Host.project_id == project.id,
            HostFollow.status == FollowStatus.IN_REVIEW,
        )
        # Most recently touched first; NULL updated_at lands last.
        .order_by(
            desc(HostFollow.updated_at.is_(None)),  # NULLs last
            desc(HostFollow.updated_at),
            desc(HostFollow.created_at),
        )
        .limit(limit)
        .all()
    )

    if not follows:
        return MyAttentionResponse(items=[], in_review_count=0, watching_count=0)

    host_ids = [host.id for _, host in follows]

    # Batch vuln summary lookup — one query for all rows.
    vuln_service = VulnerabilityService(db)
    try:
        vuln_map = vuln_service.get_bulk_host_vulnerability_summaries(host_ids)
    except Exception:
        logger.exception("Failed to load vuln summaries for my-attention queue")
        vuln_map = {}

    # Open port counts in a single GROUP BY query.
    port_count_rows = (
        db.query(models.Port.host_id, func.count(models.Port.id))
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .group_by(models.Port.host_id)
        .all()
    )
    port_count_map = {hid: cnt for hid, cnt in port_count_rows}

    items: List[MyAttentionHost] = []
    for follow, host in follows:
        sev = (vuln_map.get(host.id) or {}).get("by_severity", {})
        items.append(MyAttentionHost(
            host_id=host.id,
            ip_address=host.ip_address,
            hostname=host.hostname,
            follow_status=follow.status.value if hasattr(follow.status, "value") else str(follow.status),
            open_port_count=port_count_map.get(host.id, 0),
            critical_vulns=sev.get("critical", 0),
            high_vulns=sev.get("high", 0),
            last_viewed_at=follow.last_viewed_at,
            follow_updated_at=follow.updated_at or follow.created_at,
        ))

    # Total in_review count across the user's queue (independent of
    # `limit`) so the widget can show "showing 10 of 23 in your queue".
    in_review_count = (
        db.query(func.count(HostFollow.id))
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .filter(
            HostFollow.user_id == current_user.id,
            models.Host.project_id == project.id,
            HostFollow.status == FollowStatus.IN_REVIEW,
        )
        .scalar()
    ) or 0
    return MyAttentionResponse(
        items=items,
        in_review_count=in_review_count,
        watching_count=0,
    )


# ---------------------------------------------------------------------------
# Team Review — the project-wide review roster.  My Queue is the caller's
# personal queue; this is the whole team's, grouped by reviewer, so
# operators can see coverage and avoid two people working the same host.
# ---------------------------------------------------------------------------

class TeamReviewHostRow(BaseModel):
    """One in-review host under a reviewer."""
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    follow_updated_at: Optional[datetime] = None


class TeamReviewerGroup(BaseModel):
    """A reviewer and the hosts they currently have In Review."""
    user_id: int
    username: str
    full_name: Optional[str] = None
    host_count: int = 0
    hosts: List[TeamReviewHostRow] = Field(default_factory=list)


class TeamReviewResponse(BaseModel):
    reviewers: List[TeamReviewerGroup] = Field(default_factory=list)
    # Distinct hosts in review across the whole team (a host counts
    # once even if two reviewers both have it).
    total_hosts_in_review: int = 0


def compute_team_review(
    db: Session, current_user: User, project: Project, limit: int = 500,
) -> TeamReviewResponse:
    """Project-wide review roster, grouped by reviewer.

    Every host any user has marked **In Review** in this project, so
    the team can see who is working what and plan coverage.  Reviewers
    are ordered by host count (busiest first); each reviewer's hosts
    are newest-touched first.  Includes the caller — it's a roster,
    not a "other people" list.

    ``total_hosts_in_review`` is computed in SQL and is unaffected by
    ``limit``, so the widget can still surface a correct "showing N of T"
    figure even when the roster overflows the row cap.
    """
    # SQL-side distinct count — independent of the row cap below so the
    # widget surfaces an honest "showing N of T" figure even when many
    # in-review hosts overflow the cap.
    total_hosts_in_review = (
        db.query(func.count(distinct(HostFollow.host_id)))
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            HostFollow.status == FollowStatus.IN_REVIEW,
        )
        .scalar()
        or 0
    )

    rows = (
        db.query(HostFollow, models.Host, User)
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .join(User, HostFollow.user_id == User.id)
        .filter(
            models.Host.project_id == project.id,
            HostFollow.status == FollowStatus.IN_REVIEW,
        )
        # Newest-touched first so each reviewer's host list reads
        # most-recent-first as rows are appended below.
        .order_by(
            desc(HostFollow.updated_at.is_(None)),  # NULLs last
            desc(HostFollow.updated_at),
            desc(HostFollow.created_at),
        )
        .limit(limit)
        .all()
    )

    groups: dict = {}
    for follow, host, user in rows:
        group = groups.get(user.id)
        if group is None:
            group = TeamReviewerGroup(
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                host_count=0,
                hosts=[],
            )
            groups[user.id] = group
        group.hosts.append(TeamReviewHostRow(
            host_id=host.id,
            ip_address=host.ip_address,
            hostname=host.hostname,
            follow_updated_at=follow.updated_at or follow.created_at,
        ))
        group.host_count += 1

    reviewers = sorted(
        groups.values(),
        key=lambda g: (-g.host_count, g.username.lower()),
    )
    return TeamReviewResponse(
        reviewers=reviewers,
        total_hosts_in_review=total_hosts_in_review,
    )


# ---------------------------------------------------------------------------
# Personal "My Tasks" — the authoritative personal work queue.
#
# The UNION of three buckets, each a non-terminal entry on an accepted
# plan in this project, tagged with WHY it's in your queue:
#   - "assigned"  — TestPlanEntry.assigned_to_id == me (authoritative).
#   - "in_review" — entry sits on a host I marked In Review (my implicit
#                   investigation scope).
#   - "triage"    — UNASSIGNED critical/high entry; a shared triage queue
#                   so high-severity work nobody owns is still visible.
# A single entry can carry multiple reasons (e.g. assigned AND in_review).
# ---------------------------------------------------------------------------

class MyTaskItem(BaseModel):
    """One row of the dashboard's personal task list — a single test
    plan entry, tagged with the reason(s) it lands in the caller's queue."""
    entry_id: int
    plan_id: int
    plan_title: str
    plan_status: str
    host_id: int
    host_ip: str
    host_hostname: Optional[str] = None
    priority: str
    test_phase: str
    entry_status: str  # proposed | approved | in_progress
    proposed_test_count: int
    rationale: Optional[str] = None
    updated_at: Optional[datetime] = None
    # Why this entry is in your queue: subset of {assigned, in_review, triage}.
    reasons: List[str] = Field(default_factory=list)
    assigned_to_id: Optional[int] = None


class MyTasksReasonCounts(BaseModel):
    """Per-bucket counts (independent of `limit`).  Buckets overlap — an
    entry can be both assigned and in_review — so these do NOT sum to
    `total_open` (which is the deduped union)."""
    assigned: int = 0
    in_review: int = 0
    triage: int = 0


class MyTasksResponse(BaseModel):
    items: List[MyTaskItem] = Field(default_factory=list)
    total_open: int = 0
    reason_counts: MyTasksReasonCounts = Field(default_factory=MyTasksReasonCounts)


def compute_my_tasks(
    db: Session, current_user: User, project: Project, limit: int = 15,
) -> MyTasksResponse:
    """Return the caller's authoritative personal task queue.

    Every row is a non-terminal entry (status in proposed/approved/
    in_progress) on an accepted plan (approved/in_progress/completed) in
    this project, matching at least one of:
      - assigned to the caller (`assigned_to_id`),
      - on a host the caller marked In Review,
      - unassigned and critical/high (shared triage).

    Order: strongest reason (assigned → in_review → triage), then
    priority (critical → info), then most recently updated.
    """
    # Materialize the caller's In Review host_ids once — used both in the
    # SQL filter and for Python-side reason tagging.
    in_review_host_ids = {
        hid
        for (hid,) in (
            db.query(HostFollow.host_id)
            .join(models.Host, HostFollow.host_id == models.Host.id)
            .filter(
                HostFollow.user_id == current_user.id,
                models.Host.project_id == project.id,
                HostFollow.status == FollowStatus.IN_REVIEW,
            )
            .all()
        )
    }

    assigned_cond = TestPlanEntry.assigned_to_id == current_user.id
    in_review_cond = (
        TestPlanEntry.host_id.in_(in_review_host_ids) if in_review_host_ids
        else false()
    )
    triage_cond = and_(
        TestPlanEntry.assigned_to_id.is_(None),
        TestPlanEntry.priority.in_(("critical", "high")),
    )

    base_filters = (
        TestPlan.project_id == project.id,
        TestPlan.status.in_(("approved", "in_progress", "completed")),
        TestPlanEntry.status.in_(("proposed", "approved", "in_progress")),
    )

    # Rank in SQL so the LIMIT keeps the TRUE top rows.  CASE order matches
    # the reason precedence (assigned → in_review → triage); `in_review_cond`
    # is already a false() literal when the caller has no In Review hosts.
    reason_rank_case = case(
        (assigned_cond, 0),
        (in_review_cond, 1),
        else_=2,
    )
    priority_rank_case = case(
        (TestPlanEntry.priority == "critical", 0),
        (TestPlanEntry.priority == "high", 1),
        (TestPlanEntry.priority == "medium", 2),
        (TestPlanEntry.priority == "low", 3),
        (TestPlanEntry.priority == "info", 4),
        else_=5,
    )

    ordered = (
        db.query(TestPlanEntry, TestPlan, models.Host)
        .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
        .join(models.Host, TestPlanEntry.host_id == models.Host.id)
        .filter(*base_filters, or_(assigned_cond, in_review_cond, triage_cond))
        .order_by(
            reason_rank_case,
            priority_rank_case,
            desc(TestPlanEntry.updated_at.is_(None)),  # NULLs last
            desc(TestPlanEntry.updated_at),
        )
        .limit(limit)
        .all()
    )

    def reasons_for(entry) -> List[str]:
        out: List[str] = []
        if entry.assigned_to_id == current_user.id:
            out.append("assigned")
        if entry.host_id in in_review_host_ids:
            out.append("in_review")
        if entry.assigned_to_id is None and entry.priority in ("critical", "high"):
            out.append("triage")
        return out

    items = [
        MyTaskItem(
            entry_id=entry.id,
            plan_id=plan.id,
            plan_title=plan.title,
            plan_status=plan.status,
            host_id=host.id,
            host_ip=host.ip_address,
            host_hostname=host.hostname,
            priority=entry.priority,
            test_phase=entry.test_phase,
            entry_status=entry.status,
            proposed_test_count=len(entry.proposed_tests or []),
            rationale=entry.rationale,
            updated_at=entry.updated_at,
            reasons=reasons_for(entry),
            assigned_to_id=entry.assigned_to_id,
        )
        for entry, plan, host in ordered
    ]

    # Deduped union total + per-bucket counts in ONE query via conditional
    # aggregation — ``count(distinct(case((cond, id))))`` counts the matching
    # ids per bucket (case → NULL when false; count ignores NULLs).
    counts_row = (
        db.query(
            func.count(distinct(TestPlanEntry.id)).label("total"),
            func.count(distinct(case((assigned_cond, TestPlanEntry.id)))).label("assigned"),
            func.count(distinct(case((in_review_cond, TestPlanEntry.id)))).label("in_review"),
            func.count(distinct(case((triage_cond, TestPlanEntry.id)))).label("triage"),
        )
        .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
        .filter(*base_filters, or_(assigned_cond, in_review_cond, triage_cond))
        .one()
    )
    total_open = counts_row.total or 0
    reason_counts = MyTasksReasonCounts(
        assigned=counts_row.assigned or 0,
        in_review=counts_row.in_review or 0,
        triage=counts_row.triage or 0,
    )

    return MyTasksResponse(
        items=items, total_open=total_open, reason_counts=reason_counts,
    )
