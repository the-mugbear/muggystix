"""A project's changelog: what its members — and the agents working for them —
did to the project's record (2.546.0).

Three things live here and nowhere else:

* ``record`` — the ONE writer of ``project_events``.  It adds a row to the
  caller's transaction and never commits, so a line exists if and only if the
  change it describes was committed.
* ``list_events`` — the ONE read, for project auditors and above.  No agent
  route reads it (decided 2026-11-18).
* the vocabulary: ``ACTIONS`` (every action a line may carry, with the words
  the page prints), ``ROUTE_ACTIONS`` (which write route records which) and
  ``NOT_RECORDED`` (the write routes that record nothing, each with its
  reason).  ``tests/test_project_events_coverage.py`` holds the three
  together: a new write route must be put in one of the two maps, and every
  action must be recorded somewhere under ``app/``.

**Who acted** is not threaded through every service.  The request's
dependencies leave an ``Actor`` on the request's own ``Session``
(``note_actor``: ``get_current_user`` for a person, the agent gate for an
agent, which names its operator and its session), and ``record`` reads it
there.  A context variable would not do — a synchronous dependency runs in a
worker thread and what it sets never reaches the handler; the ``Session`` is
the one object both hold.  Code that runs outside a request (a worker) passes
``actor=`` or records nothing.

**The one exception to "in the change's transaction".**  Three bulk routes
commit once per item by design — ``POST /proposals/bulk``,
``POST /upload/jobs/discard-staged`` and ``/upload/jobs/dismiss-superseded``
— so their ONE line is written after the loop, in a commit of its own: a
crash in that instant keeps the decisions and loses the line.  Everything
else is atomic with its change.

An agent's action is ALWAYS marked as an agent's (``by_agent``), under the
operator whose authority it used — never shown as if that person did it by
hand.  Accepting an agent's proposal is the accepter's own action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_auth import User
from app.db.models_project import ProjectEvent
from app.services.paging import page_envelope

_ACTOR_KEY = "bluestick_actor"


@dataclass(frozen=True)
class Actor:
    """Who a request acts as: a person, or an agent session's operator."""
    user_id: Optional[int]
    agent_session_id: Optional[int] = None
    by_agent: bool = False


def note_actor(db: Session, *, user_id: Optional[int], agent_session_id: Optional[int] = None,
               by_agent: bool = False) -> None:
    """Say who this request's session acts as (called by the two gates)."""
    db.info[_ACTOR_KEY] = Actor(user_id=user_id, agent_session_id=agent_session_id, by_agent=by_agent)


def actor_of(db: Session) -> Optional[Actor]:
    return db.info.get(_ACTOR_KEY)


# --- the vocabulary ----------------------------------------------------------
# action → (group, what the page prints).  The group is the page's "kind"
# filter.  Words are past tense and say what was done; the subject follows.

ACTIONS: Dict[str, Tuple[str, str]] = {
    # findings
    "finding_created": ("findings", "created finding"),
    "finding_edited": ("findings", "edited finding"),
    "finding_status_changed": ("findings", "changed the status of finding"),
    "finding_deleted": ("findings", "deleted finding"),
    "finding_assigned": ("findings", "assigned finding"),
    "finding_endpoints_added": ("findings", "added affected systems to finding"),
    "finding_endpoints_removed": ("findings", "removed affected systems from finding"),
    "finding_endpoint_status_changed": ("findings", "changed the state of affected systems on finding"),
    "finding_retest_recorded": ("findings", "recorded a retest on finding"),
    "finding_comment_added": ("findings", "commented on finding"),
    "finding_comment_edited": ("findings", "edited a comment on finding"),
    "finding_comment_deleted": ("findings", "deleted a comment on finding"),
    # scanner observations
    "observation_promoted": ("observations", "promoted scanner observation"),
    "observation_dismissed": ("observations", "dismissed scanner observation"),
    # tests and evidence
    "host_test_added": ("tests", "added a test"),
    "host_test_updated": ("tests", "updated test"),
    "host_test_result_recorded": ("tests", "recorded the result of test"),
    "evidence_recorded": ("tests", "recorded evidence"),
    "evidence_promoted": ("tests", "made a finding from evidence"),
    # hosts
    "host_note_added": ("hosts", "added a note on host"),
    "host_note_edited": ("hosts", "edited a note on host"),
    "host_note_deleted": ("hosts", "deleted a note on host"),
    "note_image_changed": ("hosts", "changed an image on a note"),
    "host_review_changed": ("hosts", "changed the review status of"),
    "host_assigned": ("hosts", "assigned"),
    "host_unassigned": ("hosts", "unassigned"),
    "host_tags_changed": ("hosts", "changed tags on"),
    "host_corrected": ("hosts", "corrected host"),
    "default_view_changed": ("hosts", "changed the project's default Hosts view"),
    # scans and imports
    "scan_uploaded": ("scans", "uploaded"),
    "scan_import_started": ("scans", "started the import of"),
    "scan_reprocessed": ("scans", "re-processed"),
    "scan_upload_discarded": ("scans", "discarded upload"),
    "import_cancelled": ("scans", "cancelled the import of"),
    "import_dismissed": ("scans", "dismissed failed import"),
    "truncated_upload_kept": ("scans", "kept the readable part of cut-short upload"),
    "observations_recorded": ("scans", "recorded observations"),
    "scan_batch_named": ("scans", "renamed upload batch"),
    "scan_deleted": ("scans", "deleted scan"),
    "parse_error_changed": ("scans", "resolved a parse error"),
    # scope
    "scope_subnets_added": ("scope", "added to the scope"),
    "scope_subnet_changed": ("scope", "changed scope subnet"),
    "scope_subnet_removed": ("scope", "removed from the scope subnet"),
    "scope_domains_added": ("scope", "added to the scope"),
    "scope_domain_removed": ("scope", "removed from the scope domain"),
    "names_imported": ("scope", "imported names"),
    "name_deleted": ("scope", "deleted name"),
    "subnet_label_changed": ("scope", "changed subnet labels"),
    "site_changed": ("scope", "changed site"),
    # reports
    "report_created": ("reports", "created report"),
    "report_changed": ("reports", "changed report"),
    "report_deleted": ("reports", "deleted report draft"),
    "report_issued": ("reports", "issued report"),
    "report_revised": ("reports", "started a revision of report"),
    "report_defaults_changed": ("reports", "changed the report defaults"),
    "report_image_added": ("reports", "added a report image"),
    # proposals
    "proposal_made": ("proposals", "proposed"),
    "proposal_accepted": ("proposals", "accepted proposal"),
    "proposal_rejected": ("proposals", "rejected proposal"),
    # remediation (listed only while remediation tracking is on)
    "remediation_recorded": ("remediation", "recorded a remediation entry"),
    "remediation_changed": ("remediation", "changed a remediation entry"),
    "remediation_removed": ("remediation", "removed a remediation entry"),
    "remediation_applied": ("remediation", "applied remediation details"),
    "remediation_follow_up": ("remediation", "set a remediation follow-up"),
    # the project and its people
    "member_added": ("project", "added member"),
    "member_role_changed": ("project", "changed the role of member"),
    "member_removed": ("project", "removed member"),
    "project_updated": ("project", "changed the project's details"),
    "project_archived": ("project", "archived the project"),
    "project_unarchived": ("project", "unarchived the project"),
    "ingest_settings_changed": ("project", "changed the import settings"),
    "webhook_changed": ("project", "changed webhook"),
    # agent sessions
    "agent_session_started": ("agents", "started an agent session"),
    "agent_session_resumed": ("agents", "resumed agent session"),
    "agent_session_ended": ("agents", "ended agent session"),
    "scanner_credentials_shared": ("agents", "was given the credentials of scanner"),
}

#: The page's "kind" filter, in the order it lists them.
GROUPS: Tuple[str, ...] = (
    "findings", "observations", "tests", "hosts", "scans", "scope", "reports", "proposals",
    "remediation", "project", "agents",
)
REMEDIATION_GROUP = "remediation"

_SUBJECT_LABEL_MAX = 300
_SUMMARY_MAX = 500


def record(
    db: Session,
    project_id: int,
    action: str,
    *,
    subject_type: Optional[str] = None,
    subject_id: Optional[int] = None,
    label: Optional[str] = None,
    summary: Optional[str] = None,
    count: int = 1,
    actor: Optional[Actor] = None,
) -> Optional[ProjectEvent]:
    """Add one line to the project's changelog, in the CALLER's transaction.

    Never commits and never flushes: a change that rolls back takes its line
    with it.  ``count`` is how many things the one action touched — a bulk
    action is ONE line.  ``count <= 0`` records nothing (a bulk action that
    changed nothing did nothing).  With no actor — code running outside a
    request that passed none — nothing is recorded either: a line that names
    nobody is not a changelog line.
    """
    if action not in ACTIONS:
        raise ValueError(f"{action!r} is not a changelog action (project_events.ACTIONS)")
    if count <= 0:
        return None
    who = actor or actor_of(db)
    if who is None:
        return None
    row = ProjectEvent(
        project_id=project_id,
        actor_user_id=who.user_id,
        agent_session_id=who.agent_session_id,
        by_agent=bool(who.by_agent),
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=(label or None) and str(label)[:_SUBJECT_LABEL_MAX],
        summary=(summary or None) and str(summary)[:_SUMMARY_MAX],
        count=int(count),
    )
    db.add(row)
    return row


def _person(user: Optional[User]) -> Optional[Dict[str, Any]]:
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "full_name": user.full_name or None}


def list_events(
    db: Session,
    project_id: int,
    *,
    actor_id: Optional[int] = None,
    by_agent: Optional[bool] = None,
    group: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    include_remediation: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """A page of the project's changelog, newest first, in the paging
    envelope, with ``people`` (everyone who has a line, for the filter) and
    ``trimmed`` (older lines were removed at the cap).

    ``include_remediation`` is the installation's switch: with it off the
    remediation lines are neither listed nor counted (they are still stored —
    the switch may be turned on later)."""
    base = db.query(ProjectEvent).filter(ProjectEvent.project_id == project_id)
    hidden = [name for name, (grp, _words) in ACTIONS.items() if grp == REMEDIATION_GROUP]
    if not include_remediation:
        base = base.filter(ProjectEvent.action.notin_(hidden))
    people_q = base
    if actor_id is not None:
        base = base.filter(ProjectEvent.actor_user_id == actor_id)
    if by_agent is not None:
        base = base.filter(ProjectEvent.by_agent.is_(bool(by_agent)))
    if group is not None:
        base = base.filter(ProjectEvent.action.in_([n for n, (grp, _w) in ACTIONS.items() if grp == group]))
    if action is not None:
        base = base.filter(ProjectEvent.action == action)
    if since is not None:
        base = base.filter(ProjectEvent.created_at >= since)
    if until is not None:
        base = base.filter(ProjectEvent.created_at < until)

    total = base.order_by(None).with_entities(func.count(ProjectEvent.id)).scalar() or 0
    rows = (
        db.query(ProjectEvent, User)
        .outerjoin(User, User.id == ProjectEvent.actor_user_id)
        .filter(ProjectEvent.id.in_(
            base.with_entities(ProjectEvent.id)
            .order_by(ProjectEvent.created_at.desc(), ProjectEvent.id.desc())
            .offset(offset).limit(limit)
        ))
        .order_by(ProjectEvent.created_at.desc(), ProjectEvent.id.desc())
        .all()
    )
    items = [
        {
            "id": event.id,
            "created_at": event.created_at,
            "action": event.action,
            "group": ACTIONS.get(event.action, ("", ""))[0],
            "words": ACTIONS.get(event.action, ("", event.action))[1],
            "actor": _person(user),
            "by_agent": event.by_agent,
            "agent_session_id": event.agent_session_id,
            "subject_type": event.subject_type,
            "subject_id": event.subject_id,
            "subject_label": event.subject_label,
            "summary": event.summary,
            "count": event.count,
        }
        for event, user in rows
    ]
    people = [
        _person(user) for user in (
            db.query(User)
            .filter(User.id.in_(people_q.with_entities(ProjectEvent.actor_user_id).distinct()))
            .order_by(User.username)
            .all()
        )
    ]
    answer = page_envelope(items, total=int(total), limit=limit, offset=offset)
    answer["people"] = people
    answer["groups"] = [g for g in GROUPS if include_remediation or g != REMEDIATION_GROUP]
    answer["trimmed"] = was_trimmed(db, project_id)
    return answer


# --- the cap -----------------------------------------------------------------

def was_trimmed(db: Session, project_id: int) -> bool:
    """Whether the project holds as many lines as the cap allows — older ones
    have been (or are about to be) removed."""
    cap = settings.PROJECT_EVENTS_MAX_PER_PROJECT
    if cap <= 0:
        return False
    held = (
        db.query(func.count(ProjectEvent.id)).filter(ProjectEvent.project_id == project_id).scalar() or 0
    )
    return int(held) >= cap


def trim_to_cap(db: Session) -> int:
    """Remove each project's oldest lines beyond ``PROJECT_EVENTS_MAX_PER_PROJECT``
    (0 = no cap).  One statement per project over the cap; the caller commits.
    Run by the API's housekeeping loop."""
    cap = settings.PROJECT_EVENTS_MAX_PER_PROJECT
    if cap <= 0:
        return 0
    removed = 0
    over = (
        db.query(ProjectEvent.project_id)
        .group_by(ProjectEvent.project_id)
        .having(func.count(ProjectEvent.id) > cap)
        .all()
    )
    for (project_id,) in over:
        # The id of the cap-th newest line: everything older goes.
        keep_from = (
            db.query(ProjectEvent.id)
            .filter(ProjectEvent.project_id == project_id)
            .order_by(ProjectEvent.created_at.desc(), ProjectEvent.id.desc())
            .offset(cap - 1).limit(1)
            .scalar()
        )
        if keep_from is None:
            continue
        cut = db.query(ProjectEvent.created_at).filter(ProjectEvent.id == keep_from).scalar()
        removed += (
            db.query(ProjectEvent)
            .filter(
                ProjectEvent.project_id == project_id,
                (ProjectEvent.created_at < cut)
                | ((ProjectEvent.created_at == cut) & (ProjectEvent.id < keep_from)),
            )
            .delete(synchronize_session=False)
        )
    return removed


# --- which write route records what -----------------------------------------
# Keys are (METHOD, path) with the mount's prefix removed (``route_key``):
# ``/projects/{project_id}``, ``/agent`` and
# ``/remediation-overview/projects/{project_id}`` mount the same handlers.

_PREFIXES: Sequence[str] = (
    "/remediation-overview/projects/{project_id}", "/projects/{project_id}", "/agent",
)


def route_key(method: str, path: str) -> Tuple[str, str]:
    """``(METHOD, path under its mount)`` for a published route path (with or
    without ``/api/v1``)."""
    if path.startswith("/api/v1"):
        path = path[len("/api/v1"):]
    for prefix in _PREFIXES:
        if path == prefix:
            return method.upper(), "/"
        if path.startswith(prefix + "/"):
            return method.upper(), path[len(prefix):]
    return method.upper(), path


ROUTE_ACTIONS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    # findings
    ("POST", "/findings"): ("finding_created",),
    ("PATCH", "/findings/{finding_id}"): ("finding_edited",),
    ("DELETE", "/findings/{finding_id}"): ("finding_deleted",),
    ("POST", "/findings/{finding_id}/status"): ("finding_status_changed",),
    ("POST", "/findings/bulk/status"): ("finding_status_changed",),
    ("POST", "/findings/bulk/assign"): ("finding_assigned",),
    ("POST", "/findings/{finding_id}/hosts"): ("finding_endpoints_added",),
    ("DELETE", "/findings/{finding_id}/hosts/{host_id}"): ("finding_endpoints_removed",),
    ("DELETE", "/findings/{finding_id}/endpoints/{finding_host_id}"): ("finding_endpoints_removed",),
    ("PATCH", "/findings/{finding_id}/endpoints"): ("finding_endpoint_status_changed",),
    ("PATCH", "/findings/{finding_id}/endpoints/{finding_host_id}"): ("finding_endpoint_status_changed",),
    ("POST", "/findings/{finding_id}/endpoints/{finding_host_id}/retest"): ("finding_retest_recorded",),
    ("POST", "/findings/{finding_id}/notes"): ("finding_comment_added",),
    ("PATCH", "/findings/{finding_id}/notes/{note_id}"): ("finding_comment_edited",),
    ("DELETE", "/findings/{finding_id}/notes/{note_id}"): ("finding_comment_deleted",),
    ("POST", "/findings/{finding_id}/notes/{note_id}/attachments"): ("note_image_changed",),
    # scanner observations
    ("POST", "/vulnerabilities/{vuln_id}/promote"): ("observation_promoted", "observation_dismissed"),
    ("POST", "/scanner-observations/promote"): ("observation_promoted",),
    ("POST", "/scanner-observations/dismiss"): ("observation_dismissed",),
    # tests and evidence
    ("POST", "/host-tests"): ("host_test_added",),
    ("PATCH", "/host-tests/{test_id}"): ("host_test_updated",),
    ("POST", "/host-tests/{test_id}/result"): ("host_test_result_recorded",),
    ("POST", "/evidence"): ("evidence_recorded",),
    ("POST", "/evidence/{evidence_id}/finding"): ("evidence_promoted",),
    # hosts
    ("POST", "/hosts/{host_id}/notes"): ("host_note_added",),
    ("PATCH", "/hosts/{host_id}/notes/{note_id}"): ("host_note_edited",),
    ("DELETE", "/hosts/{host_id}/notes/{note_id}"): ("host_note_deleted",),
    ("POST", "/hosts/{host_id}/notes/{note_id}/attachments"): ("note_image_changed",),
    ("PATCH", "/hosts/notes/attachments/{attachment_id}"): ("note_image_changed",),
    ("DELETE", "/hosts/notes/attachments/{attachment_id}"): ("note_image_changed",),
    ("POST", "/hosts/{host_id}/follow"): ("host_review_changed",),
    ("PATCH", "/hosts/{host_id}/follow"): ("host_review_changed",),
    ("DELETE", "/hosts/{host_id}/follow"): ("host_review_changed",),
    ("POST", "/hosts/bulk/follow"): ("host_review_changed",),
    ("POST", "/operations/followups/still-reviewed"): ("host_review_changed",),
    ("POST", "/hosts/bulk/assign"): ("host_assigned",),
    ("POST", "/hosts/bulk/unassign"): ("host_unassigned",),
    ("POST", "/hosts/bulk/tags"): ("host_tags_changed",),
    ("PATCH", "/hosts/tags/{tag_id}"): ("host_tags_changed",),
    ("DELETE", "/hosts/tags/{tag_id}"): ("host_tags_changed",),
    ("PATCH", "/hosts/{host_id}"): ("host_corrected",),
    ("POST", "/hosts/views/{view_id}/promote"): ("default_view_changed",),
    ("DELETE", "/hosts/default-view"): ("default_view_changed",),
    # scans and imports
    ("POST", "/upload/"): ("scan_uploaded",),
    ("POST", "/uploads"): ("scan_uploaded",),
    ("POST", "/upload/jobs/{job_id}/start"): ("scan_import_started",),
    ("POST", "/upload/jobs/{job_id}/retry"): ("scan_import_started",),
    ("POST", "/upload/jobs/{job_id}/reprocess"): ("scan_reprocessed",),
    ("POST", "/upload/jobs/{job_id}/discard"): ("scan_upload_discarded",),
    ("POST", "/upload/jobs/discard-staged"): ("scan_upload_discarded",),
    ("POST", "/upload/jobs/{job_id}/cancel"): ("import_cancelled",),
    ("POST", "/upload/jobs/{job_id}/dismiss"): ("import_dismissed",),
    ("POST", "/upload/jobs/dismiss-superseded"): ("import_dismissed",),
    ("POST", "/uploads/{job_id}/keep-truncated"): ("truncated_upload_kept",),
    ("POST", "/observations"): ("observations_recorded",),
    ("PATCH", "/scans/batches/{batch_id}"): ("scan_batch_named",),
    ("DELETE", "/scans/{scan_id}"): ("scan_deleted",),
    ("PUT", "/parse-errors/{error_id}/status"): ("parse_error_changed",),
    ("DELETE", "/parse-errors/{error_id}"): ("parse_error_changed",),
    # scope
    ("POST", "/scopes/subnets"): ("scope_subnets_added",),
    ("POST", "/scopes/upload-subnets"): ("scope_subnets_added",),
    ("PATCH", "/scopes/subnets/{subnet_id}"): ("scope_subnet_changed",),
    ("DELETE", "/scopes/subnets/{subnet_id}"): ("scope_subnet_removed",),
    ("POST", "/scopes/domains"): ("scope_domains_added",),
    ("DELETE", "/scopes/domains/{domain_id}"): ("scope_domain_removed",),
    ("POST", "/names/import"): ("names_imported",),
    ("DELETE", "/names/{name_id}"): ("name_deleted",),
    ("POST", "/scopes/subnet-labels"): ("subnet_label_changed",),
    ("PATCH", "/scopes/subnet-labels/{label_id}"): ("subnet_label_changed",),
    ("DELETE", "/scopes/subnet-labels/{label_id}"): ("subnet_label_changed",),
    ("POST", "/scopes/subnet-labels/{label_id}/subnets"): ("subnet_label_changed",),
    ("PUT", "/scopes/subnets/{subnet_id}/labels"): ("subnet_label_changed",),
    ("PATCH", "/sites/{site_id}"): ("site_changed",),
    # reports
    ("POST", "/client-reports"): ("report_created",),
    ("PATCH", "/client-reports/{report_id}"): ("report_changed",),
    ("DELETE", "/client-reports/{report_id}"): ("report_deleted",),
    ("POST", "/client-reports/{report_id}/issue"): ("report_issued",),
    ("POST", "/client-reports/{report_id}/revise"): ("report_revised",),
    ("POST", "/client-reports/{report_id}/promote"): ("report_defaults_changed",),
    ("PATCH", "/client-reports/profile"): ("report_defaults_changed",),
    ("POST", "/client-reports/images"): ("report_image_added",),
    # proposals
    ("POST", "/proposals/finding"): ("proposal_made",),
    ("POST", "/proposals/finding-text"): ("proposal_made",),
    ("POST", "/proposals/observation"): ("proposal_made",),
    ("POST", "/proposals/endpoint-status"): ("proposal_made",),
    ("POST", "/proposals/subnet-details"): ("proposal_made",),
    ("POST", "/proposals/report-details"): ("proposal_made",),
    ("POST", "/reports/draft/finding-text"): ("proposal_made",),
    ("POST", "/reports/draft/report-details"): ("proposal_made",),
    ("POST", "/proposals/{proposal_id}/accept"): ("proposal_accepted",),
    ("POST", "/proposals/{proposal_id}/reject"): ("proposal_rejected",),
    ("POST", "/proposals/bulk"): ("proposal_accepted", "proposal_rejected"),
    # remediation
    ("POST", "/remediation/events"): ("remediation_recorded",),
    ("PATCH", "/remediation/events/{event_id}"): ("remediation_changed",),
    ("DELETE", "/remediation/events/{event_id}"): ("remediation_removed",),
    ("POST", "/remediation/apply"): ("remediation_applied",),
    ("POST", "/remediation/assign-from-report"): ("remediation_applied",),
    ("POST", "/remediation/follow-up"): ("remediation_follow_up",),
    # the project and its people
    ("PUT", "/"): ("project_updated", "project_archived", "project_unarchived"),
    ("PATCH", "/ingest-settings"): ("ingest_settings_changed",),
    ("POST", "/members"): ("member_added",),
    ("PUT", "/members/{user_id}"): ("member_role_changed",),
    ("DELETE", "/members/{user_id}"): ("member_removed",),
    ("POST", "/webhooks"): ("webhook_changed",),
    ("PATCH", "/webhooks/{webhook_id}"): ("webhook_changed",),
    ("DELETE", "/webhooks/{webhook_id}"): ("webhook_changed",),
    # agent sessions
    ("POST", "/assist/start"): ("agent_session_started",),
    ("POST", "/agent-sessions/{session_id}/resume"): ("agent_session_resumed",),
    ("POST", "/agent-sessions/{session_id}/end"): ("agent_session_ended",),
    ("POST", "/session/end"): ("agent_session_ended",),
    ("POST", "/scanner-integrations/{integration_id}/credentials"): ("scanner_credentials_shared",),
}

_PERSONAL = "the reader's own state, not the project's record"
_NOTHING_CHANGES = "produces a file or an answer; changes nothing in the project"
_JOB_UPKEEP = "upkeep of a report job's row, not of the project's record"
_LIVE = "a live presentation's moment-to-moment state (it has its own audit lines)"
_INSTALLATION = "the installation's, not this project's (and audited there)"

NOT_RECORDED: Dict[Tuple[str, str], str] = {
    ("DELETE", "/"): "the project is deleted and its changelog with it; the audit log keeps the deletion",
    # personal state
    ("POST", "/hosts/views"): _PERSONAL,
    ("PATCH", "/hosts/views/{view_id}"): _PERSONAL,
    ("DELETE", "/hosts/views/{view_id}"): _PERSONAL,
    ("POST", "/hosts/query/history"): _PERSONAL,
    ("DELETE", "/hosts/query/history"): _PERSONAL,
    ("DELETE", "/hosts/query/history/{entry_id}"): _PERSONAL,
    ("DELETE", "/query/history"): _PERSONAL,
    ("DELETE", "/query/history/{entry_id}"): _PERSONAL,
    ("POST", "/queries/saved"): _PERSONAL,
    ("PATCH", "/queries/saved/{query_id}"): _PERSONAL,
    ("DELETE", "/queries/saved/{query_id}"): _PERSONAL,
    ("POST", "/hosts/{host_id}/view"): _PERSONAL,
    ("POST", "/operations/seen"): _PERSONAL,
    # answers and files
    ("POST", "/hosts/query/validate"): _NOTHING_CHANGES,
    ("POST", "/client-reports/{report_id}/preview"): _NOTHING_CHANGES,
    ("POST", "/client-reports/{report_id}/render"): _NOTHING_CHANGES,
    ("POST", "/reports/draft"): _NOTHING_CHANGES,
    ("POST", "/reports/jobs"): _NOTHING_CHANGES,
    ("POST", "/remediation/contact-report"): _NOTHING_CHANGES,
    ("POST", "/webhooks/{webhook_id}/test"): _NOTHING_CHANGES,
    ("POST", "/scopes/correlate-all"): "recomputes which subnet each host is in from the scope as it stands",
    ("POST", "/scans/batches"): "made by an upload of several files, which is recorded as the upload",
    # job upkeep
    ("POST", "/reports/jobs/{job_id}/cancel"): _JOB_UPKEEP,
    ("POST", "/reports/jobs/{job_id}/dismiss"): _JOB_UPKEEP,
    ("POST", "/reports/jobs/{job_id}/retry"): _JOB_UPKEEP,
    ("POST", "/webhooks/deliveries/{delivery_id}/retry"): "re-sends a notification; changes nothing in the project",
    # presentations
    ("POST", "/client-reports/{report_id}/presentation"): _LIVE,
    ("POST", "/client-reports/{report_id}/presentation/layout-reports"): _LIVE,
    ("POST", "/client-reports/{report_id}/presentation/agendas"): _LIVE,
    ("PUT", "/client-reports/{report_id}/presentation/agendas/{agenda_id}"): _LIVE,
    ("DELETE", "/client-reports/{report_id}/presentation/agendas/{agenda_id}"): _LIVE,
    ("POST", "/presentations/{presentation_id}/heartbeat"): _LIVE,
    ("POST", "/presentations/{presentation_id}/move"): _LIVE,
    ("POST", "/presentations/{presentation_id}/spotlight"): _LIVE,
    ("POST", "/presentations/{presentation_id}/take-control"): _LIVE,
    ("POST", "/presentations/{presentation_id}/end"): _LIVE,
    # the installation's
    ("PUT", "/client-reports/templates/{name}/assets/{asset_id}"): _INSTALLATION,
    ("DELETE", "/client-reports/templates/{name}/assets/{asset_id}"): _INSTALLATION,
    ("POST", "/tool-suggestions"): _INSTALLATION,
    ("POST", "/feedback"): "feedback about BlueStick to its administrators, not a change to the project",
    ("POST", "/session/renew"): "extends the agent's own key; the session's start and end are recorded",
}
