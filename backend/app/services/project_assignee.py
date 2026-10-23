"""Who work in a project may be handed to — the ONE rule.

``load_project_assignee`` is every assigning route's check: a finding's owner
(create / promote / update), the findings' and the hosts' bulk assign, and a
host test's assignee (review 2026-10-10 R1c: ``host_test_service`` kept a copy
that skipped the ``is_active`` check, so a deactivated analyst could still be
given a test).  It lives in a service so ``host_test_service`` can call it;
``app.api.deps`` re-exports it under the name every route imports.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership

NOT_ACTIVE = (400, "Assignee is not an active user")
NOT_MEMBER = (400, "Assignee is not a member of this project")


def load_project_assignee(
    db: Session,
    project_id: int,
    assignee_user_id: int,
    *,
    unknown: Tuple[int, str] = NOT_ACTIVE,
    refused: Tuple[int, str] = NOT_MEMBER,
    roles: Optional[Sequence[str]] = None,
) -> User:
    """The user ``assignee_user_id`` names, if work in this project may be
    handed to them.

    A valid target is an ACTIVE user who is a member of the project — with
    one of ``roles`` when the work needs more than membership (a host test is
    worked by an analyst or admin) — or an active global admin, who needs no
    membership (they can open and write to every project).  A user who does
    not exist or is inactive is refused with ``unknown`` (status, detail); an
    active one who is not a (qualifying) member with ``refused``.
    """
    assignee = (
        db.query(User)
        .filter(User.id == assignee_user_id, User.is_active.is_(True))
        .first()
    )
    if not assignee:
        raise HTTPException(status_code=unknown[0], detail=unknown[1])
    if assignee.role != UserRole.ADMIN:
        membership = db.query(ProjectMembership.id).filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == assignee.id,
        )
        if roles is not None:
            membership = membership.filter(ProjectMembership.role.in_(tuple(roles)))
        if not membership.first():
            raise HTTPException(status_code=refused[0], detail=refused[1])
    return assignee
