"""The installation's own report details (v2.495.0): who produces its reports
and the disclaimer they close with.

Not about one project: read by every signed-in user (a report page shows what
an empty field inherits) and written by a global admin in System settings.  A
project's report defaults override them and a report its project's —
``services/report_defaults`` is the one reader of that order.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_client_info, get_current_user, require_role
from app.core.security import log_audit_event
from app.db.models_auth import User, UserRole
from app.db.session import get_db
from app.services import report_defaults as defaults

router = APIRouter()


class ReportDefaultsUpdate(BaseModel):
    values: Dict[str, Optional[str]] = Field(
        ..., description=(
            "Key → the new text. Keys: " + ", ".join(defaults.KEYS) + ". A key left out is "
            "not touched; null or a blank text removes it (reports then use their project's, "
            "or for the disclaimer their template's own)."
        ),
    )


@router.get("/report-defaults", summary="The installation's report author and disclaimer")
def read_report_defaults(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return defaults.as_dict(db)


@router.put("/report-defaults", summary="Set the installation's report author and disclaimer")
def write_report_defaults(body: ReportDefaultsUpdate, request: Request,
                          user: User = Depends(require_role(UserRole.ADMIN)), db: Session = Depends(get_db)):
    try:
        changes = defaults.save(db, body.values, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if changes:
        log_audit_event(
            db, user_id=user.id, action="report_defaults_updated", resource_type="system",
            resource_id="report_defaults", details={"changed": changes},
            commit=False, **get_client_info(request),
        )
    db.commit()
    return defaults.as_dict(db)
