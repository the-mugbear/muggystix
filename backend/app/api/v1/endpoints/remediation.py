"""Remediation tracking: the project admins' record of who was told about a
finding on a host, and where the fix stands.

Reads need a project auditor (the Reports page's floor); every write needs a
project admin.  The router is a factory so the agent surface can mount the
same contract.

The feature is per installation (v2.461.0): until a global admin turns it on
every route here answers 404.  ``account_router`` holds what is not about one
project — the installation's settings and the cross-project follow-up reads.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    enforce_agent_operator_access, get_client_info, get_current_project, get_current_user,
    require_project_role, require_role,
)
from app.api.v1.endpoints.agent_common import load_agent_session, require_project_host
from app.db.models_agent import Agent
from app.core.security import log_audit_event
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.schemas.remediation_schemas import (
    ApplyBody, ContactReportBody, FollowUpBody, Grouping, NoteCreate, NoteUpdate, OverdueBand, PolicyUpdate, RemediationState,
    RemediationStatus, Severity,
)
from app.services import remediation_policy, remediation_report
from app.services import remediation_service as remediation
from app.services.agent_session_service import note_agent_model
from app.services.proposal_service import Attribution


@dataclass
class Actor:
    project_id: int
    who: Attribution


def human_reader(project: Project = Depends(get_current_project),
                 user: User = Depends(require_project_role(ProjectRole.AUDITOR))):
    return Actor(project.id, Attribution(user_id=user.id))


def human_admin(project: Project = Depends(get_current_project),
                user: User = Depends(require_project_role(ProjectRole.ADMIN))):
    return Actor(project.id, Attribution(user_id=user.id))


def enabled(db: Session = Depends(get_db)) -> remediation_policy.Policy:
    """Every remediation route: 404 on an installation that did not opt in."""
    return remediation_policy.require_enabled(db)


_STATE = Query(None, description="Where the row stands against its deadline; repeat for several.")
_CONTACT = Query(None, min_length=1, max_length=254, description="Part of a contact's address or name.")
_TEAM = Query(None, min_length=1, max_length=100, description="Exactly this team (case does not matter).")
_BAND = Query(None, description="Only OVERDUE rows this many days past their deadline.")
_STALE = Query(None, ge=1, le=365, description=(
    "Only overdue and due-soon rows nobody recorded a follow-up for in this many days (or ever)."))
_TREND_DAYS = Query(90, ge=7, le=730, description="How many days of daily counts to return.")


def make_router(reader, admin):
    router = APIRouter(dependencies=[Depends(enabled)])

    @router.get("/remediation", summary="Findings on hosts with their remediation contact and status")
    def listing(status: Optional[RemediationStatus] = None,
                state: Optional[List[RemediationState]] = _STATE,
                contact: Optional[str] = _CONTACT,
                unassigned: bool = Query(False, description="Only rows with no contact."),
                host_id: Optional[int] = Query(None, gt=0), finding_id: Optional[int] = Query(None, gt=0),
                severity: Optional[Severity] = None, team: Optional[str] = _TEAM,
                overdue_band: Optional[OverdueBand] = _BAND, no_follow_up_days: Optional[int] = _STALE,
                group: Grouping = "host",
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        require_project_host(db, actor.project_id, host_id)
        return remediation.list_rows(
            db, [actor.project_id], status=status, state=state, contact=contact, unassigned=unassigned,
            host_id=host_id, finding_id=finding_id, severity=severity, team=team,
            overdue_band=overdue_band, no_follow_up_days=no_follow_up_days, group=group,
            limit=limit, offset=offset)

    @router.get("/remediation/contacts", summary="The contacts in use, with their counts")
    def contact_list(actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return {"items": remediation.contacts(db, [actor.project_id])}

    @router.get("/remediation/teams", summary="The teams that own fixes, most overdue first")
    def team_list(actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return {"items": remediation.teams(db, [actor.project_id])}

    @router.get("/remediation/trend", summary="Daily deadline counts and how closed rows ended per month")
    def trend(days: int = _TREND_DAYS, actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return remediation.trend(db, [actor.project_id], days=days)

    @router.get("/remediation/follow-up",
                summary="One contact's overdue and due-soon findings on hosts, and the message to send")
    def follow_up(contact_email: str = Query(..., min_length=3, max_length=254),
                  actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return remediation.follow_up(db, [actor.project_id], contact_email)

    @router.post("/remediation/follow-up", summary="Record that a contact was followed up with")
    def record_follow_up(body: FollowUpBody, actor: Actor = Depends(admin), db: Session = Depends(get_db)):
        result = remediation.record_follow_up(db, actor.project_id, body, actor.who)
        db.commit()
        return result

    # --- one contact's list as a document (rendered on the report worker) ---

    @router.post("/remediation/contact-report", status_code=202,
                 summary="Prepare one contact's remediation list as a Word or HTML document")
    def prepare_contact_report(body: ContactReportBody, actor: Actor = Depends(admin),
                               db: Session = Depends(get_db)):
        return remediation_report.serialize_job(remediation_report.enqueue(db, actor.project_id, body, actor.who))

    @router.get("/remediation/contact-report/{job_id}", summary="Whether a contact's list is ready")
    def contact_report_status(job_id: int, actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return remediation_report.serialize_job(remediation_report.job_for(db, actor.project_id, job_id))

    @router.get("/remediation/contact-report/{job_id}/download", summary="Download a prepared list")
    def contact_report_download(job_id: int, actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        job = remediation_report.job_for(db, actor.project_id, job_id)
        if job.status != "completed" or not job.result_path:
            raise HTTPException(409, f"The document is not ready (status: {job.status}).")
        if not Path(job.result_path).is_file():
            raise HTTPException(410, "The document has expired. Prepare it again.")
        return FileResponse(path=job.result_path, media_type=job.media_type or "application/octet-stream",
                            filename=job.result_filename or f"remediation_{job.id}")

    @router.post("/remediation/apply", summary="Set contact, dates and status on findings on hosts")
    def apply(body: ApplyBody, actor: Actor = Depends(admin), db: Session = Depends(get_db)):
        if actor.who.session and body.agent_model:
            note_agent_model(actor.who.session, body.agent_model)
        result = remediation.apply(db, actor.project_id, body, actor.who)
        if body.dry_run:
            db.rollback()
        else:
            db.commit()
        return result

    @router.get("/remediation/hosts/{host_id}/events", summary="A host's remediation timeline")
    def events(host_id: int, finding_host_id: Optional[int] = Query(None, gt=0),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return remediation.list_events(db, actor.project_id, host_id, actor.who.user_id,
                                       finding_host_id=finding_host_id, limit=limit, offset=offset)

    @router.post("/remediation/events", status_code=201, summary="Add a note to a host's timeline")
    def add_note(body: NoteCreate, response: Response, actor: Actor = Depends(admin),
                 db: Session = Depends(get_db)):
        row, created = remediation.add_note(db, actor.project_id, body, actor.who)
        data = remediation.serialize_event(row, actor.who.user_id)
        db.commit()
        if not created:
            response.status_code = 200
        return data

    @router.patch("/remediation/events/{event_id}", summary="Edit a note you wrote")
    def edit_note(event_id: int, body: NoteUpdate, actor: Actor = Depends(admin),
                  db: Session = Depends(get_db)):
        row = remediation.update_note(db, actor.project_id, event_id, body, actor.who.user_id)
        data = remediation.serialize_event(row, actor.who.user_id)
        db.commit()
        return data

    @router.delete("/remediation/events/{event_id}", status_code=204, summary="Remove a note you wrote")
    def remove_note(event_id: int, actor: Actor = Depends(admin), db: Session = Depends(get_db)):
        remediation.delete_note(db, actor.project_id, event_id, actor.who.user_id)
        db.commit()

    return router


router = make_router(human_reader, human_admin)


# --- the agent side: the same contract, as the session's operator -----------

def agent_reader(request: Request, agent: Agent = Depends(enforce_agent_operator_access),
                 db: Session = Depends(get_db)):
    """The auditor floor for these reads is in ``AGENT_READ_ROLE_OVERRIDES``."""
    session = load_agent_session(db, request)
    return Actor(agent.project_id, Attribution(user_id=session.started_by_id, session=session))


def agent_admin(request: Request, actor: Actor = Depends(agent_reader)):
    """A key writes here only when its operator is a project admin (or a
    global one).  The router-level gate lets any analyst's key write, which is
    right for notes and tests and too low for this."""
    state = request.state
    if not (getattr(state, "key_operator_is_admin", False)
            or getattr(state, "key_operator_role", None) == ProjectRole.ADMIN.value):
        raise HTTPException(
            status_code=403,
            detail=(
                "Remediation tracking is written by project administrators. This key "
                "acts for an operator who is not one, so it requires admin to change it."
            ),
        )
    return actor


agent_router = make_router(agent_reader, agent_admin)


# --- not about one project ---------------------------------------------------

account_router = APIRouter()


@account_router.get("/remediation-policy", summary="This installation's remediation settings")
def read_policy(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every signed-in user: the pages read it to know whether the feature
    exists here, and what the deadlines are."""
    return remediation_policy.load(db).as_dict()


@account_router.put("/remediation-policy", summary="Turn remediation tracking on or off; set the timelines")
def write_policy(body: PolicyUpdate, request: Request,
                 user: User = Depends(require_role(UserRole.ADMIN)), db: Session = Depends(get_db)):
    before, after = remediation_policy.save(db, body, user.id)
    if before != after:
        log_audit_event(
            db, user_id=user.id, action="remediation_policy_updated", resource_type="system",
            resource_id="remediation_policy", details={"from": before.as_dict(), "to": after.as_dict()},
            commit=False, **get_client_info(request),
        )
    db.commit()
    return after.as_dict()


def administered_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                          _: remediation_policy.Policy = Depends(enabled)) -> List[Project]:
    """The projects whose remediation the caller follows up across: every
    project for a global admin, the ones they ADMINISTER for anyone else.
    Archived projects are included — testing has ended there, remediation
    runs until every finding is dispositioned."""
    query = db.query(Project)
    if user.role != UserRole.ADMIN:
        query = query.join(ProjectMembership, ProjectMembership.project_id == Project.id).filter(
            ProjectMembership.user_id == user.id, ProjectMembership.role == ProjectRole.ADMIN.value)
    return query.order_by(Project.name, Project.id).all()


def administered_project(project_id: int, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Actor:
    """One project the caller administers — ARCHIVED OR NOT.  The project
    routes answer 410 for an archived project (its inventory is closed), but
    remediation runs until every finding is dispositioned, so the same
    contract is mounted here for the cross-project page to read and write
    through."""
    if db.query(Project.id).filter(Project.id == project_id).first() is None:
        raise HTTPException(404, "Project not found")
    if user.role != UserRole.ADMIN and db.query(ProjectMembership.id).filter(
        ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id,
        ProjectMembership.role == ProjectRole.ADMIN.value,
    ).first() is None:
        raise HTTPException(403, "Remediation across projects is followed up by a project's administrators")
    return Actor(project_id, Attribution(user_id=user.id))


# /remediation-overview/projects/{project_id}/remediation[/apply|/follow-up|/events…]
account_router.include_router(
    make_router(administered_project, administered_project),
    prefix="/remediation-overview/projects/{project_id}",
)


def _selected(projects: List[Project], project_id: Optional[int]) -> List[int]:
    ids = [p.id for p in projects]
    if project_id is None:
        return ids
    if project_id not in ids:
        raise HTTPException(404, "Not a project whose remediation you follow up")
    return [project_id]


@account_router.get("/remediation-overview",
                    summary="Findings on hosts across the projects you administer, by deadline")
def overview(project_id: Optional[int] = Query(None, gt=0),
             status: Optional[RemediationStatus] = None,
             state: Optional[List[RemediationState]] = _STATE,
             contact: Optional[str] = _CONTACT,
             contact_email: Optional[str] = Query(None, min_length=3, max_length=254,
                                                  description="Exactly this contact."),
             unassigned: bool = False, severity: Optional[Severity] = None, team: Optional[str] = _TEAM,
             overdue_band: Optional[OverdueBand] = _BAND, no_follow_up_days: Optional[int] = _STALE,
             group: Grouping = "due",
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             projects: List[Project] = Depends(administered_projects), db: Session = Depends(get_db)):
    return remediation.list_rows(
        db, _selected(projects, project_id), status=status, state=state, contact=contact,
        contact_email=contact_email, unassigned=unassigned, severity=severity, team=team,
        overdue_band=overdue_band, no_follow_up_days=no_follow_up_days, group=group,
        limit=limit, offset=offset)


@account_router.get("/remediation-overview/projects",
                    summary="Each project you administer, with its findings on hosts by deadline state")
def overview_projects(projects: List[Project] = Depends(administered_projects),
                      policy: remediation_policy.Policy = Depends(enabled), db: Session = Depends(get_db)):
    today = remediation._today()
    counts = remediation.state_counts_by_project(db, [p.id for p in projects], policy, today)
    items = [{"project_id": p.id, "name": p.name, "archived": p.status == "archived",
              "states": counts[p.id]} for p in projects]
    totals = {name: sum(row["states"][name] for row in items) for name in remediation_policy.STATES}
    return {"items": items, "totals": totals, "as_of": today.isoformat(), "policy": policy.as_dict()}


@account_router.get("/remediation-overview/contacts",
                    summary="The contacts across the projects you administer, most overdue first")
def overview_contacts(project_id: Optional[int] = Query(None, gt=0),
                      projects: List[Project] = Depends(administered_projects),
                      db: Session = Depends(get_db)):
    return {"items": remediation.contacts(db, _selected(projects, project_id))}


@account_router.get("/remediation-overview/teams",
                    summary="The teams across the projects you administer, most overdue first")
def overview_teams(project_id: Optional[int] = Query(None, gt=0),
                   projects: List[Project] = Depends(administered_projects),
                   db: Session = Depends(get_db)):
    return {"items": remediation.teams(db, _selected(projects, project_id))}


@account_router.get("/remediation-overview/trend",
                    summary="Daily deadline counts and closed on time or late per month, across your projects")
def overview_trend(project_id: Optional[int] = Query(None, gt=0), days: int = _TREND_DAYS,
                   projects: List[Project] = Depends(administered_projects),
                   db: Session = Depends(get_db)):
    return remediation.trend(db, _selected(projects, project_id), days=days)


@account_router.get("/remediation-overview/follow-up",
                    summary="One contact's at-risk findings on hosts across your projects, and the message")
def overview_follow_up(contact_email: str = Query(..., min_length=3, max_length=254),
                       project_id: Optional[int] = Query(None, gt=0),
                       projects: List[Project] = Depends(administered_projects),
                       db: Session = Depends(get_db)):
    return remediation.follow_up(db, _selected(projects, project_id), contact_email)
