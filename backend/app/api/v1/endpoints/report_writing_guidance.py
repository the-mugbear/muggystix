"""The installation's report writing guidance (v2.469.0).

How a model is told to draft each section of a finding's report text.  Not
about one project: one record, read by every signed-in user (an analyst can
see what the drafter is told) and written by a global admin in System
settings.  ``services/report_writing_guidance`` is the one reader; the agents'
copy is on ``GET /agent/assist/findings/{id}``.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_client_info, get_current_user, require_role
from app.core.security import log_audit_event
from app.db.models_auth import User, UserRole
from app.db.session import get_db
from app.services import report_writing_guidance as guidance

router = APIRouter()


class GuidanceUpdate(BaseModel):
    sections: Dict[str, Optional[str]] = Field(
        ..., description=(
            "Key → the new text. Keys: " + ", ".join(guidance.KEYS) + ". A key left out is "
            "not touched; null or a blank text puts that key back on its shipped default."
        ),
    )

    @field_validator("sections")
    @classmethod
    def known_keys_within_length(cls, value):
        for key, text in value.items():
            if key not in guidance.DEFAULTS:
                raise ValueError(f"Unknown section '{key}'. Valid: {', '.join(guidance.KEYS)}")
            if text is not None and len(text.strip()) > guidance.MAX_CHARS:
                raise ValueError(f"{key}: at most {guidance.MAX_CHARS} characters")
        return value


@router.get("/report-writing-guidance", summary="How report text is to be drafted on this installation")
def read_guidance(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return guidance.load(db).as_dict()


@router.put("/report-writing-guidance", summary="Set the drafting instructions for report text")
def write_guidance(body: GuidanceUpdate, request: Request,
                   user: User = Depends(require_role(UserRole.ADMIN)), db: Session = Depends(get_db)):
    try:
        before, after = guidance.save(db, body.sections, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    changes = before.changes(after)
    if changes:
        log_audit_event(
            db, user_id=user.id, action="report_writing_guidance_updated", resource_type="system",
            resource_id="report_writing_guidance", details={"changed": changes},
            commit=False, **get_client_info(request),
        )
    db.commit()
    return guidance.load(db).as_dict()
