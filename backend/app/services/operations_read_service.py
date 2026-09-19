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
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, distinct, false, func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import Annotation, FollowStatus, HostFollow, NoteStatus
from app.db.models_agent import AgentSession, TestPlan, TestPlanEntry
from app.db.models_auth import User
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
from app.db.models_project import Project
from app.services.vulnerability_service import VulnerabilityService

# Findings still demanding work — excludes the terminal dispositions
# (false_positive / accepted_risk / remediated).  Mirrors the host
# finding_count badge so the two surfaces agree on "active".
ACTIVE_FINDING_STATUSES = ("open", "confirmed", "retest")

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

# ---------------------------------------------------------------------------
# Investigation queue (v2.347.0; design review item 2)
# ---------------------------------------------------------------------------

class InvestigateReason(BaseModel):
    kind: str
    text: str


class InvestigateEvidence(BaseModel):
    sources: List[str] = Field(default_factory=list)
    last_seen: Optional[datetime] = None
    # What backs the reasons.  Untouched hosts have no finding or test by
    # definition today; the field exists so the row can say so honestly and
    # so a later "touched but stalled" queue can reuse the shape.
    confirmation: str = "scanner"


class InvestigateAction(BaseModel):
    kind: str  # inspect | collect | plan
    text: str


class InvestigateRow(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    tier: int
    tier_label: str
    reasons: List[InvestigateReason] = Field(default_factory=list)
    evidence: InvestigateEvidence = Field(default_factory=InvestigateEvidence)
    next_action: InvestigateAction


class InvestigationQueueResponse(BaseModel):
    items: List[InvestigateRow] = Field(default_factory=list)
    untouched_total: int = 0
    queue_total: int = 0
    tiers: List[str] = Field(default_factory=list)


# The ordering is a stated tier, not a weighted score (the risk-scoring
# post-mortem: every number visible, nothing opaque).  A host takes the
# first tier it qualifies for; within a tier, most recently seen first.
INVESTIGATE_TIERS: List[tuple] = [
    (1, "Exploitable critical"),
    (2, "Critical vulnerability"),
    (3, "Exploit available"),
    (4, "High-value service, new or changed"),
    (5, "Scans disagree"),
]
_NEW_HOST_DAYS = 7


def compute_investigation_queue(
    db: Session, project: Project, limit: int = 25,
) -> InvestigationQueueResponse:
    """Hosts nobody has touched that carry an observed weakness or a
    relevant change — "what should I investigate next?" before anyone has
    created work for it.

    Untouched = no HostFollow (review or assignment), no note, no test-plan
    entry, no finding.  Reasons come from what the inventory already knows:
    vulnerability severity and exploitability, high-value open ports, a
    host first seen this week or changed at its latest scan, and conflicts
    between scans.  Every row carries its reasons, its evidence (which tools
    saw it, when) and a next action; the tier is named, never scored.
    """
    from app.db.models_confidence import ConflictHistory
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
    from app.services.ports_of_interest import ports_by_number

    followed = db.query(HostFollow.host_id)
    noted = db.query(Annotation.host_id).filter(Annotation.host_id.isnot(None))
    planned = db.query(TestPlanEntry.host_id)
    found = db.query(FindingHost.host_id)
    untouched = (
        db.query(
            models.Host.id, models.Host.ip_address, models.Host.hostname,
            models.Host.first_seen, models.Host.last_seen,
        )
        .filter(
            models.Host.project_id == project.id,
            ~models.Host.id.in_(followed),
            ~models.Host.id.in_(noted),
            ~models.Host.id.in_(planned),
            ~models.Host.id.in_(found),
        )
        .all()
    )
    tiers = [label for _, label in INVESTIGATE_TIERS]
    if not untouched:
        return InvestigationQueueResponse(untouched_total=0, queue_total=0, tiers=tiers)
    ids = [row.id for row in untouched]

    # --- signals, one grouped query each -----------------------------------
    crit: Dict[int, int] = {}
    high: Dict[int, int] = {}
    exploit: Dict[int, int] = {}
    crit_exploit: Dict[int, int] = {}
    any_vuln: set = set()
    for hid, sev, expl, cnt in (
        db.query(
            Vulnerability.host_id, Vulnerability.severity, Vulnerability.exploitable,
            func.count(Vulnerability.id),
        )
        .filter(Vulnerability.host_id.in_(ids))
        .group_by(Vulnerability.host_id, Vulnerability.severity, Vulnerability.exploitable)
        .all()
    ):
        any_vuln.add(hid)
        if sev == VulnerabilitySeverity.CRITICAL:
            crit[hid] = crit.get(hid, 0) + cnt
            if expl:
                crit_exploit[hid] = crit_exploit.get(hid, 0) + cnt
        elif sev == VulnerabilitySeverity.HIGH:
            high[hid] = high.get(hid, 0) + cnt
        if expl:
            exploit[hid] = exploit.get(hid, 0) + cnt

    poi = ports_by_number()
    high_value: Dict[int, List[str]] = {}
    for hid, port in (
        db.query(models.Port.host_id, models.Port.port_number)
        .filter(
            models.Port.host_id.in_(ids),
            models.Port.state == "open",
            models.Port.port_number.in_(list(poi.keys())),
        )
        .distinct()
        .all()
    ):
        high_value.setdefault(hid, []).append(poi[port].label)

    conflicts: Dict[int, int] = dict(
        db.query(ConflictHistory.host_id, func.count(ConflictHistory.id))
        .filter(ConflictHistory.host_id.in_(ids))
        .group_by(ConflictHistory.host_id)
        .all()
    )

    # "Changed at its latest scan" is the same derivation the Hosts list
    # badge uses.  Only hosts that could land in tier 4 need it (a
    # high-value port open, no vulnerability tier), which keeps the window
    # query small on a large untouched set.
    from app.services.host_change_service import hosts_changed_since_prior_scan
    tier4_candidates = [
        hid for hid in high_value
        if hid not in crit and hid not in exploit
    ]
    changed = hosts_changed_since_prior_scan(db, tier4_candidates)

    now = datetime.now(timezone.utc)

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _plural(n: int, one: str, many: Optional[str] = None) -> str:
        return f"{n} {one if n == 1 else (many or one + 's')}"

    candidates: List[tuple] = []
    for row in untouched:
        hid = row.id
        reasons: List[InvestigateReason] = []
        tier: Optional[int] = None
        ce, c, e = crit_exploit.get(hid, 0), crit.get(hid, 0), exploit.get(hid, 0)
        if ce:
            tier = 1
            reasons.append(InvestigateReason(
                kind="critical_exploitable",
                text=f"{_plural(ce, 'critical vulnerability', 'critical vulnerabilities')} with a known public exploit",
            ))
            if c > ce:
                reasons.append(InvestigateReason(kind="critical", text=f"{c - ce} more critical"))
        elif c:
            tier = 2
            reasons.append(InvestigateReason(
                kind="critical", text=_plural(c, "critical vulnerability", "critical vulnerabilities"),
            ))
            if e:
                reasons.append(InvestigateReason(
                    kind="exploitable", text=f"{_plural(e, 'lower-severity vulnerability', 'lower-severity vulnerabilities')} with a known public exploit",
                ))
        elif e:
            tier = 3
            reasons.append(InvestigateReason(
                kind="exploitable", text=f"{_plural(e, 'vulnerability', 'vulnerabilities')} with a known public exploit",
            ))
        h = high.get(hid, 0)
        if h and tier is not None:
            reasons.append(InvestigateReason(kind="high", text=f"{h} high"))

        hv = high_value.get(hid)
        first_seen = _aware(row.first_seen)
        is_new = first_seen is not None and (now - first_seen).days < _NEW_HOST_DAYS
        is_changed = hid in changed
        if hv:
            reasons.append(InvestigateReason(kind="high_value", text=f"{', '.join(sorted(hv))} open"))
        if is_new:
            days = (now - first_seen).days if first_seen else 0
            reasons.append(InvestigateReason(
                kind="new_host", text="First seen today" if days == 0 else f"First seen {_plural(days, 'day')} ago",
            ))
        if is_changed:
            reasons.append(InvestigateReason(kind="changed", text="Changed at its latest scan (state or a new port)"))
        if tier is None and hv and (is_new or is_changed):
            tier = 4
        cf = conflicts.get(hid, 0)
        if cf:
            reasons.append(InvestigateReason(
                kind="conflicts", text=f"{_plural(cf, 'value')} scans disagree on",
            ))
            if tier is None:
                tier = 5
        if tier is None:
            continue
        candidates.append((tier, -(_aware(row.last_seen) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(), row, reasons))

    candidates.sort(key=lambda t: (t[0], t[1], t[2].id))
    queue_total = len(candidates)
    chosen = candidates[:limit]

    sources: Dict[int, List[str]] = {}
    if chosen:
        chosen_ids = [t[2].id for t in chosen]
        for hid, tool in (
            db.query(models.HostScanHistory.host_id, models.Scan.tool_name)
            .join(models.Scan, models.Scan.id == models.HostScanHistory.scan_id)
            .filter(models.HostScanHistory.host_id.in_(chosen_ids))
            .distinct()
            .all()
        ):
            if tool:
                sources.setdefault(hid, []).append(tool)

    tier_label = dict(INVESTIGATE_TIERS)
    items: List[InvestigateRow] = []
    for tier, _neg_seen, row, reasons in chosen:
        hid = row.id
        # The primary action is to take the host: mark it In Review under
        # the caller, which moves it out of this queue and into their
        # personal one (nobody else can be reviewing it — the queue only
        # lists hosts with no follow row at all).  A host with an exposed
        # service and no vulnerability data first needs evidence collected.
        if tier == 4 and hid not in any_vuln:
            action = InvestigateAction(
                kind="collect",
                text="No vulnerability data on this host — run a vulnerability scan against it and upload the result, or take it into review.",
            )
        elif tier <= 3:
            action = InvestigateAction(
                kind="review",
                text=f"Take it into review: {tier_label[tier].lower()} on a host nobody has looked at.",
            )
        elif tier == 5:
            action = InvestigateAction(
                kind="review", text="Take it into review and reconcile what the scans disagree on.",
            )
        else:
            action = InvestigateAction(
                kind="review", text="Take it into review: check the exposed service and decide whether it needs a test.",
            )
        items.append(InvestigateRow(
            host_id=hid,
            ip_address=row.ip_address,
            hostname=row.hostname,
            tier=tier,
            tier_label=tier_label[tier],
            reasons=reasons,
            evidence=InvestigateEvidence(
                sources=sorted(sources.get(hid, [])),
                last_seen=row.last_seen,
                confirmation="scanner",
            ),
            next_action=action,
        ))

    return InvestigationQueueResponse(
        items=items,
        untouched_total=len(untouched),
        queue_total=queue_total,
        tiers=tiers,
    )


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


# ---------------------------------------------------------------------------
# My Notes — annotation threads assigned to the caller
# ---------------------------------------------------------------------------

class MyNoteItem(BaseModel):
    """One assigned note thread — a durable unit of work the caller owns.

    ``host_id`` is the thread's target host (notes are host-scoped in the
    UI); the client deep-links to ``/hosts/{host_id}#note-{note_id}`` so the
    analyst lands back in the exact discussion."""
    note_id: int
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    host_hostname: Optional[str] = None
    body_preview: str
    note_type: Optional[str] = None  # observation|finding|question|decision|action|handoff
    status: str
    due_at: Optional[datetime] = None
    is_overdue: bool = False
    updated_at: Optional[datetime] = None


class MyNotesResponse(BaseModel):
    items: List[MyNoteItem] = Field(default_factory=list)
    total_open: int = 0
    handoff_count: int = 0
    overdue_count: int = 0


def compute_my_assigned_notes(
    db: Session, current_user: User, project: Project, limit: int = 15,
) -> MyNotesResponse:
    """Return the caller's assigned, unresolved note threads in this project.

    Scoped to host-targeted thread roots (``parent_id IS NULL`` — the work
    fields assignee/due/type live on the root): notes are assigned via the
    host inspector, so host-scoping covers the real workflow.  Ordered
    overdue-first, then by soonest due date (nulls last), then most recently
    updated — the resume signal an analyst actually triages on.
    """
    now = datetime.now(timezone.utc)
    base = (
        db.query(Annotation, models.Host)
        .join(models.Host, Annotation.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            Annotation.parent_id.is_(None),
            Annotation.assignee_id == current_user.id,
            Annotation.status != NoteStatus.RESOLVED,
        )
    )

    overdue_cond = and_(Annotation.due_at.isnot(None), Annotation.due_at < now)
    # Compute is_overdue in the DB (not Python) so it's dialect-correct and
    # consistent with the ordering/counts — and so a tz-naive value stored by
    # SQLite doesn't blow up a naive-vs-aware Python comparison.
    rows = (
        base.add_columns(overdue_cond.label("is_overdue"))
        .order_by(
            desc(overdue_cond),                         # overdue first
            Annotation.due_at.is_(None),                # then dated before undated
            Annotation.due_at.asc(),                    # soonest due first
            desc(Annotation.updated_at),
        )
        .limit(limit)
        .all()
    )

    items = [
        MyNoteItem(
            note_id=note.id,
            host_id=host.id,
            host_ip=host.ip_address,
            host_hostname=host.hostname,
            body_preview=(note.body or "").strip().splitlines()[0][:120] if (note.body or "").strip() else "",
            note_type=note.note_type,
            status=getattr(note.status, "value", note.status),
            due_at=note.due_at,
            is_overdue=bool(is_overdue),
            updated_at=note.updated_at,
        )
        for note, host, is_overdue in rows
    ]

    # Totals (independent of limit) in one grouped pass.
    counts = (
        db.query(
            func.count(Annotation.id).label("total"),
            func.count(case((Annotation.note_type == "handoff", Annotation.id))).label("handoff"),
            func.count(case((overdue_cond, Annotation.id))).label("overdue"),
        )
        .join(models.Host, Annotation.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            Annotation.parent_id.is_(None),
            Annotation.assignee_id == current_user.id,
            Annotation.status != NoteStatus.RESOLVED,
        )
        .one()
    )
    return MyNotesResponse(
        items=items,
        total_open=counts.total or 0,
        handoff_count=counts.handoff or 0,
        overdue_count=counts.overdue or 0,
    )


# ---------------------------------------------------------------------------
# My Recent Notes — the caller's latest authored notes ("what was I just doing?")
# ---------------------------------------------------------------------------

class MyRecentNoteItem(BaseModel):
    """One note the caller recently authored — host-targeted, newest first."""
    note_id: int
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    body_preview: str
    note_type: Optional[str] = None
    created_at: Optional[datetime] = None


class MyRecentNotesResponse(BaseModel):
    items: List[MyRecentNoteItem] = Field(default_factory=list)


def compute_my_recent_notes(
    db: Session, current_user: User, project: Project, limit: int = 8,
) -> MyRecentNotesResponse:
    """The caller's most recently authored notes in this project — a "what was
    I just working on?" view, independent of assignment/status/resolution.

    Distinct from ``compute_my_assigned_notes`` (the work *queue*): this is the
    analyst's latest *activity*, so it includes thread replies and resolved
    notes, ordered newest-first by authorship time.  Host-targeted only (the
    dominant case); ``user_id`` is the author column.
    """
    rows = (
        db.query(Annotation, models.Host)
        .join(models.Host, Annotation.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            Annotation.user_id == current_user.id,
        )
        .order_by(desc(Annotation.created_at))
        .limit(limit)
        .all()
    )
    items = [
        MyRecentNoteItem(
            note_id=note.id,
            host_id=host.id,
            host_ip=host.ip_address,
            body_preview=(note.body or "").strip().splitlines()[0][:120] if (note.body or "").strip() else "",
            note_type=note.note_type,
            created_at=note.created_at,
        )
        for note, host in rows
    ]
    return MyRecentNotesResponse(items=items)


# ---------------------------------------------------------------------------
# My Findings — active findings the caller owns
# ---------------------------------------------------------------------------

class MyFindingItem(BaseModel):
    """One active finding the caller owns.  ``host_id`` is a representative
    affected host (for a host-context link); ``evidence_annotation_id`` lets
    the client deep-link to the originating note thread when present."""
    finding_id: int
    title: str
    severity: str
    status: str
    host_id: Optional[int] = None
    host_count: int = 0
    evidence_annotation_id: Optional[int] = None
    updated_at: Optional[datetime] = None


class MyFindingsResponse(BaseModel):
    items: List[MyFindingItem] = Field(default_factory=list)
    total_open: int = 0


def compute_my_findings(
    db: Session, current_user: User, project: Project, limit: int = 15,
) -> MyFindingsResponse:
    """Return the caller's owned, active findings in this project, severity-
    ranked.  One representative affected host + host_count are resolved in a
    single grouped query (no per-finding N+1)."""
    severity_rank = case(
        (Finding.severity == "critical", 0),
        (Finding.severity == "high", 1),
        (Finding.severity == "medium", 2),
        (Finding.severity == "low", 3),
        (Finding.severity == "info", 4),
        else_=5,
    )
    base_filters = (
        Finding.project_id == project.id,
        Finding.owner_id == current_user.id,
        Finding.status.in_(ACTIVE_FINDING_STATUSES),
    )
    findings = (
        db.query(Finding)
        .filter(*base_filters)
        .order_by(severity_rank, desc(Finding.updated_at))
        .limit(limit)
        .all()
    )
    finding_ids = [f.id for f in findings]

    # host_count per finding + one representative host_id, in one query.
    host_count: dict = {}
    rep_host: dict = {}
    if finding_ids:
        for fid, cnt, min_host in (
            db.query(
                FindingHost.finding_id,
                func.count(FindingHost.host_id),
                func.min(FindingHost.host_id),
            )
            .filter(FindingHost.finding_id.in_(finding_ids))
            .group_by(FindingHost.finding_id)
            .all()
        ):
            host_count[fid] = int(cnt)
            rep_host[fid] = min_host

    items = [
        MyFindingItem(
            finding_id=f.id,
            title=f.title,
            severity=f.severity,
            status=f.status,
            host_id=rep_host.get(f.id),
            host_count=host_count.get(f.id, 0),
            evidence_annotation_id=f.evidence_annotation_id,
            updated_at=f.updated_at,
        )
        for f in findings
    ]
    total_open = (
        db.query(func.count(Finding.id)).filter(*base_filters).scalar() or 0
    )
    return MyFindingsResponse(items=items, total_open=total_open)


# ---------------------------------------------------------------------------
# My recent activity (§27) — a unified personal work history across entities,
# answering "what did I do?" better than the authored-notes-only Recent Notes.
# Each source is user-attributed + timestamped; we normalise to one event shape,
# merge, and take the newest.  User-scoped + per-source limited, so it stays
# cheap regardless of project size.
# ---------------------------------------------------------------------------
class ActivityEvent(BaseModel):
    kind: str  # note | finding_created | finding_status | host_reviewed | session
    at: datetime
    summary: str
    host_id: Optional[int] = None
    note_id: Optional[int] = None
    finding_id: Optional[int] = None
    severity: Optional[str] = None
    # Server-computed in-app path for sources with no entity-id mapping on the
    # client (agent runs). The client prefers this when present.
    link: Optional[str] = None


class MyActivityResponse(BaseModel):
    items: List[ActivityEvent] = Field(default_factory=list)


ACTIVITY_KINDS = {"note", "finding_created", "finding_status", "host_reviewed", "session"}

# Agent-run workflows surfaced in the activity feed, with their summary verb.
# assist is omitted (no detail page).  v2.340.1 — the deep link is resolved
# from the phase tables in ``_session_links`` rather than off the session row:
# the previous lambdas read ``s.plan_id`` (a column ``agent_sessions`` never
# had — every legacy plan-generation session 500'd the whole feed) and used the
# session id where the recon-run / execution-run detail pages take the run id.
# ``project`` sessions (every session since v2.337.0) were not listed at all.
_SESSION_WORKFLOWS = {
    "recon": "Ran a recon session",
    "execution": "Ran an execution",
    "plan_generation": "Generated a test plan",
    "project": "Ran an agent session",
}


def _session_links(db: Session, sessions: list) -> Dict[int, Optional[str]]:
    """``{agent_session_id: in-app path}`` for the sessions given.

    A session's run / plan lives in its phase table, linked back through
    ``agent_session_id``.  A legacy per-workflow session has exactly one; a
    project session may have several, so it links to the Agent Runs timeline
    where all of them are listed.  Three grouped queries, no per-row lookups.
    """
    from app.db.models_agent import ExecutionSession, ReconSession
    ids_by_kind: Dict[str, List[int]] = {}
    for s in sessions:
        ids_by_kind.setdefault(s.workflow, []).append(s.id)
    links: Dict[int, Optional[str]] = {s.id: None for s in sessions}
    for sid in ids_by_kind.get("project", []):
        links[sid] = "/agent-activity"
    lookups = (
        ("recon", ReconSession, "/recon/runs/{}"),
        ("execution", ExecutionSession, "/executions/{}"),
        ("plan_generation", TestPlan, "/test-plans/{}"),
    )
    for kind, model, pattern in lookups:
        ids = ids_by_kind.get(kind)
        if not ids:
            continue
        rows = (
            db.query(model.agent_session_id, func.min(model.id))
            .filter(model.agent_session_id.in_(ids))
            .group_by(model.agent_session_id)
            .all()
        )
        for sid, detail_id in rows:
            links[sid] = pattern.format(detail_id)
    return links


def compute_my_activity(
    db: Session, current_user: User, project: Project, limit: int = 20,
    kinds: Optional[set] = None, days: Optional[int] = None,
    search: Optional[str] = None,
) -> MyActivityResponse:
    """The caller's recent work history.  Optional filters (§27 recall):
    ``kinds`` restricts the event types, ``days`` bounds how far back, and
    ``search`` is a case-insensitive substring matched per-source (note body,
    finding title, or host ip/hostname)."""
    uid = current_user.id
    pid = project.id
    want = (kinds & ACTIVITY_KINDS) if kinds else ACTIVITY_KINDS
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days and days > 0 else None
    )
    needle = f"%{search.strip()}%" if search and search.strip() else None
    events: List[ActivityEvent] = []

    # Notes authored by the caller.
    if "note" in want:
        q = (
            db.query(Annotation, models.Host)
            .join(models.Host, Annotation.host_id == models.Host.id)
            .filter(models.Host.project_id == pid, Annotation.user_id == uid)
        )
        if cutoff is not None:
            q = q.filter(Annotation.created_at >= cutoff)
        if needle is not None:
            q = q.filter(or_(
                Annotation.body.ilike(needle),
                models.Host.ip_address.ilike(needle),
                models.Host.hostname.ilike(needle),
            ))
        for note, host in q.order_by(desc(Annotation.created_at)).limit(limit).all():
            body = (note.body or "").strip()
            preview = body.splitlines()[0][:80] if body else ""
            events.append(ActivityEvent(
                kind="note", at=note.created_at,
                summary=f"Noted {host.ip_address}" + (f" — {preview}" if preview else ""),
                host_id=host.id, note_id=note.id,
            ))

    # Findings the caller created / promoted.
    if "finding_created" in want:
        q = db.query(Finding).filter(Finding.project_id == pid, Finding.created_by_id == uid)
        if cutoff is not None:
            q = q.filter(Finding.created_at >= cutoff)
        if needle is not None:
            q = q.filter(Finding.title.ilike(needle))
        for f in q.order_by(desc(Finding.created_at)).limit(limit).all():
            verb = "Created" if f.source == "manual" else "Promoted"
            events.append(ActivityEvent(
                kind="finding_created", at=f.created_at,
                summary=f"{verb} finding: {f.title}", finding_id=f.id, severity=f.severity,
            ))

    # Finding dispositions the caller made (real transitions, not the create row).
    if "finding_status" in want:
        q = (
            db.query(FindingStatusHistory, Finding)
            .join(Finding, FindingStatusHistory.finding_id == Finding.id)
            .filter(
                Finding.project_id == pid,
                FindingStatusHistory.changed_by_id == uid,
                FindingStatusHistory.from_status.isnot(None),
            )
        )
        if cutoff is not None:
            q = q.filter(FindingStatusHistory.created_at >= cutoff)
        if needle is not None:
            q = q.filter(Finding.title.ilike(needle))
        for hist, f in q.order_by(desc(FindingStatusHistory.created_at)).limit(limit).all():
            events.append(ActivityEvent(
                kind="finding_status", at=hist.created_at,
                summary=f"Marked {f.title} {hist.to_status.replace('_', ' ')}",
                finding_id=f.id, severity=f.severity,
            ))

    # Hosts the caller marked Reviewed.  A fresh follow row has updated_at=NULL
    # (it's onupdate-only), so fall back to created_at for the event time.
    if "host_reviewed" in want:
        review_ts = func.coalesce(HostFollow.updated_at, HostFollow.created_at)
        q = (
            db.query(HostFollow, models.Host)
            .join(models.Host, HostFollow.host_id == models.Host.id)
            .filter(
                models.Host.project_id == pid,
                HostFollow.user_id == uid,
                HostFollow.status == FollowStatus.REVIEWED,
            )
        )
        if cutoff is not None:
            q = q.filter(review_ts >= cutoff)
        if needle is not None:
            q = q.filter(or_(
                models.Host.ip_address.ilike(needle),
                models.Host.hostname.ilike(needle),
            ))
        for follow, host in q.order_by(desc(review_ts)).limit(limit).all():
            events.append(ActivityEvent(
                kind="host_reviewed", at=(follow.updated_at or follow.created_at),
                summary=f"Reviewed {host.ip_address}", host_id=host.id,
            ))

    # Agent runs the caller started (recon / execution / plan generation).
    # No free-text title, so they're omitted from a `search` query.
    if "session" in want and needle is None:
        session_ts = func.coalesce(AgentSession.started_at, AgentSession.created_at)
        q = (
            db.query(AgentSession)
            .filter(
                AgentSession.project_id == pid,
                AgentSession.started_by_id == uid,
                AgentSession.workflow.in_(list(_SESSION_WORKFLOWS)),
            )
        )
        if cutoff is not None:
            q = q.filter(session_ts >= cutoff)
        sessions = q.order_by(desc(session_ts)).limit(limit).all()
        links = _session_links(db, sessions)
        for s in sessions:
            events.append(ActivityEvent(
                kind="session", at=(s.started_at or s.created_at),
                summary=f"{_SESSION_WORKFLOWS[s.workflow]} ({s.status})",
                link=links.get(s.id),
            ))

    events = [e for e in events if e.at is not None]
    events.sort(key=lambda e: e.at, reverse=True)
    return MyActivityResponse(items=events[:limit])
