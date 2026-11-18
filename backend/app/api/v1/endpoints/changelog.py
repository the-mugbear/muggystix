"""The project's changelog — what its members, and the agents working for
them, did to its record (2.546.0).

One read: ``GET /projects/{id}/changelog``, for project AUDITORS and above
(decided 2026-11-18: not viewers).  It wraps ``project_events.list_events``,
the one reader of ``project_events``.  There is no write route — a line is
written by the change it describes — and no agent route.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user, require_project_role
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services import project_events, remediation_policy
from app.services.instants import parse_instant

router = APIRouter(dependencies=[Depends(get_current_user)])


class ChangelogPerson(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None


class ChangelogLine(BaseModel):
    id: int
    created_at: datetime
    # One of ``project_events.ACTIONS``; ``group`` is the page's kind filter
    # and ``words`` what the page prints between the person and the subject.
    action: str
    group: str
    words: str
    # Whose authority the action used; null once that account is deleted.
    actor: Optional[ChangelogPerson] = None
    # True when an agent did it for ``actor`` — never shown as if they did it
    # by hand.  The session is null once the session row is gone.
    by_agent: bool
    agent_session_id: Optional[int] = None
    # What it was done to: a type and an id (the subject may be gone — a
    # deletion is a line too) and what it was called at the time.
    subject_type: Optional[str] = None
    subject_id: Optional[int] = None
    subject_label: Optional[str] = None
    summary: Optional[str] = None
    # How many things the one action touched (a bulk action is one line).
    count: int = 1


class ChangelogPage(BaseModel):
    items: List[ChangelogLine]
    total: int
    limit: int
    offset: int
    returned: int
    has_more: bool
    # Everyone with a line in the project (the person filter's choices).
    people: List[ChangelogPerson] = []
    # The kinds the page can filter by (no ``remediation`` unless the
    # installation tracks remediation).
    groups: List[str] = []
    # The project holds as many lines as the installation keeps: older ones
    # were removed.
    trimmed: bool = False


def _instant(name: str, value: Optional[str]) -> Optional[datetime]:
    if value is None or not value.strip():
        return None
    try:
        return parse_instant(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name}: not a date or time ({value!r}).")


@router.get(
    "/changelog",
    response_model=ChangelogPage,
    dependencies=[Depends(require_project_role(ProjectRole.AUDITOR))],
    summary="The project's changelog: who did what, newest first",
)
def get_changelog(
    actor_id: Optional[int] = Query(None, ge=1, description="Only this person's actions (and their agents')."),
    by_agent: Optional[bool] = Query(None, description="true: only agents' actions; false: only people's."),
    group: Optional[str] = Query(None, description="One of the answer's `groups`."),
    since: Optional[str] = Query(None, description="At or after this date or time (no offset = UTC)."),
    until: Optional[str] = Query(None, description="Before this date or time."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    tracked = remediation_policy.load(db).enabled
    groups = [g for g in project_events.GROUPS if tracked or g != project_events.REMEDIATION_GROUP]
    if group is not None and group not in groups:
        # A filter value that cannot be understood is refused, never dropped.
        raise HTTPException(status_code=422, detail=f"group: one of {', '.join(groups)}.")
    return project_events.list_events(
        db, project.id,
        actor_id=actor_id, by_agent=by_agent, group=group,
        since=_instant("since", since), until=_instant("until", until),
        include_remediation=tracked, limit=limit, offset=offset,
    )
