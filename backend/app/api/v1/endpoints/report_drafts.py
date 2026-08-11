"""AI-assisted report drafting endpoint (v2.246.0).

Operator-facing (JWT). Kept in its own file rather than reports.py so the
LLM-drafting concern (per-user provider creds, prompt assembly, non-determinism)
stays separate from the deterministic export renderers — a genuine seam, not a
line-count split. Mounted under the same ``/reports`` prefix, so the path is
``POST /projects/{project_id}/reports/draft``.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.api.deps import ProjectRole
from app.services.report_draft_service import ReportDraftService
import logging

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


class ReportDraftRequest(BaseModel):
    provider_id: Optional[int] = Field(
        None, description="LLM provider to use; omit to use your default provider."
    )
    audience: Optional[str] = Field(
        None, max_length=200,
        description="Who the report is for (e.g. 'client technical team', 'executives').",
    )
    instructions: Optional[str] = Field(
        None, max_length=2000,
        description="Extra drafting guidance (tone, sections to emphasise, etc.).",
    )
    severities: Optional[List[str]] = Field(
        None, description="Restrict to these finding severities (default: all)."
    )
    statuses: Optional[List[str]] = Field(
        None, description="Restrict to these finding statuses (default: all)."
    )


class ReportDraftResponse(BaseModel):
    content: str
    provider_id: int
    provider_type: str
    model_id: Optional[str] = None
    finding_total: int
    severity_counts: dict
    usage: Optional[dict] = None


@router.post(
    "/draft",
    response_model=ReportDraftResponse,
    summary="Draft a report from this project's findings + evidence via the LLM",
    # Report drafting is an analyst-level action (same as generating any report).
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def draft_report(
    body: ReportDraftRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return an editable Markdown draft built from the project's promoted
    findings, their evidence-note threads, and attached-image captions.

    The draft is a starting point for a human to review and edit — it is never
    final. Configuration errors (no provider, no findings) return 400; upstream
    provider failures return 502 without leaking the provider's response body.
    """
    svc = ReportDraftService(db, current_user)
    try:
        result = svc.generate(
            project.id,
            provider_id=body.provider_id,
            audience=body.audience,
            instructions=body.instructions,
            severities=body.severities,
            statuses=body.statuses,
        )
    except ValueError as exc:
        # User-fixable: no provider configured, no findings to draft from.
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError:
        # Provider/transport failure — detail already logged in the service /
        # llm_provider_service; keep the client message generic (no provider body).
        logger.exception("Report draft LLM call failed", extra={"project_id": project.id})
        raise HTTPException(
            status_code=502,
            detail=(
                "The LLM provider rejected the request or was unreachable. "
                "Check the provider on the LLM Providers page and try again."
            ),
        )
    return ReportDraftResponse(**result)
