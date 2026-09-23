"""AI-assisted report drafting endpoint (v2.246.0).

Operator-facing (JWT). Kept in its own file rather than reports.py so the
LLM-drafting concern (per-user provider creds, prompt assembly, non-determinism)
stays separate from the deterministic export renderers — a genuine seam, not a
line-count split. Mounted under the same ``/reports`` prefix, so the path is
``POST /projects/{project_id}/reports/draft``.
"""
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project
from app.api.v1.endpoints.auth import get_current_user
from app.api.v1.endpoints.findings import get_finding_viewer
from app.api.deps import get_current_project, require_project_role
from app.api.deps import ProjectRole
from app.services.client_report_service import REQUIRED_TEXT
from app.services.report_draft_service import ReportDraftService

FindingTextField = Literal["description", "impact", "recommendation", "steps_to_reproduce", "references"]
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


class FindingTextDraftRequest(BaseModel):
    finding_id: int
    # The same names as report_draft_service.FINDING_TEXT_FIELDS (pinned by
    # test_report_drafts).
    fields: Optional[List[FindingTextField]] = Field(
        None, description="Sections to draft; omit for the empty ones a report requires "
                          "(description, impact, recommendation).",
    )
    provider_id: Optional[int] = Field(None, description="LLM provider; omit for your default.")


class FindingTextDraftResponse(BaseModel):
    suggestions: Dict[str, str]
    provider_id: int
    provider_type: str
    model_id: Optional[str] = None
    usage: Optional[dict] = None


@router.post(
    "/draft/finding-text",
    response_model=FindingTextDraftResponse,
    summary="Suggest report text for one finding's empty sections via the LLM",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def draft_finding_text(
    body: FindingTextDraftRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
    viewer=Depends(get_finding_viewer),
):
    """Suggestions for the finding's report text — the report's "missing
    text" to-do list, drafted (review 2026-09-23 B-Ops-5).  Nothing is
    written: the author reviews and saves them through the finding update,
    so only someone who may edit that text (its author or a project admin)
    may ask for a draft."""
    finding = (
        db.query(Finding)
        .options(selectinload(Finding.hosts).selectinload(FindingHost.host))
        .filter(Finding.id == body.finding_id, Finding.project_id == project.id)
        .first()
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    if not viewer.may_modify(finding):
        raise HTTPException(
            status_code=403,
            detail="Only the finding's author or a project admin can write its report text.",
        )
    fields = list(dict.fromkeys(body.fields)) if body.fields else [
        k for k in REQUIRED_TEXT if not (getattr(finding, k) or "").strip()
    ]
    if not fields:
        raise HTTPException(status_code=400, detail="Every section a report needs is already written.")
    try:
        result = ReportDraftService(db, current_user).draft_finding_text(
            finding, fields, provider_id=body.provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError:
        logger.exception("Finding text draft failed", extra={"project_id": project.id, "finding_id": finding.id})
        raise HTTPException(
            status_code=502,
            detail=(
                "The LLM provider failed or its answer could not be read. "
                "Check the provider on the LLM Providers page and try again."
            ),
        )
    return FindingTextDraftResponse(**result)
