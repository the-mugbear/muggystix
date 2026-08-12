"""Test-plan offline-bundle endpoints.

The remote/offline execution workflow: export an approved plan as a ZIP the
operator hands to an air-gapped agent, then import the agent's results.json
back. Carved out of ``test_plans.py`` (CLAUDE.md file-size policy) as a
self-contained sub-surface — it depends only on bundle_service /
bundle_import_service, not on the plan-management or session helpers.

Registered under the same ``/test-plans`` prefix as the main router, so the
route paths are unchanged.
"""
import logging
from typing import List

from fastapi import (
    APIRouter, Depends, File, HTTPException, Path, Request, UploadFile,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.models_agent import Agent, TestPlan
from app.api.deps import get_current_project, require_project_role, read_upload_capped

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Export Bundle — package an approved plan for an offline remote agent
# ---------------------------------------------------------------------------

@router.post(
    "/{plan_id}/export-bundle",
    summary="Export an approved test plan as a remote-execution bundle",
    responses={
        200: {
            "description": "ZIP bundle containing manifest.json, plan.json, "
                           "instructions.md, and results_schema.json. "
                           "The X-Bundle-Id header echoes the bundle id so "
                           "the UI can display it without parsing the ZIP.",
            "content": {"application/zip": {}},
        },
        400: {"description": "Plan is empty, not approved, or in an invalid state"},
        404: {"description": "Plan not found"},
    },
)
def export_test_plan_bundle(
    plan_id: int = Path(..., gt=0),
    request: Request = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Package an approved plan into a downloadable ZIP bundle.

    Creates a new execution session in ``exported`` mode tied to this
    bundle, pausing any existing active session for the plan.  The
    remote agent runs tests offline, then the operator uploads the
    resulting ``results.json`` via ``POST /test-plans/{id}/import-results``.
    """
    plan = (
        db.query(TestPlan)
        .filter(TestPlan.id == plan_id, TestPlan.project_id == project.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    # Reuse the user's project agent if one exists so feedback / sessions
    # are attributable to the same logical agent as the live-execution
    # flow.  If none exists, leave it None — bundle exports don't need
    # an agent row for auth (no API key is minted).
    agent = (
        db.query(Agent)
        .filter(Agent.project_id == project.id, Agent.owner_id == current_user.id)
        .first()
    )

    from app.services.bundle_service import build_export_bundle
    try:
        bundle = build_export_bundle(
            db=db,
            request=request,
            plan=plan,
            started_by_id=current_user.id,
            agent_id=agent.id if agent else None,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    db.commit()

    return Response(
        content=bundle["zip_bytes"],
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={bundle['filename']}",
            "X-Bundle-Id": bundle["bundle_id"],
            "X-Execution-Session-Id": str(bundle["execution_session_id"]),
        },
    )


# ---------------------------------------------------------------------------
# Import Results — ingest a results.json from a remote agent
# ---------------------------------------------------------------------------

class ImportResultsResponse(BaseModel):
    execution_session_id: int
    plan_id: int
    bundle_id: str
    results_imported: int
    sanity_checks_imported: int
    feedback_extracted: bool
    is_final: bool
    session_status: str
    plan_status: str
    parse_errors: List[str] = Field(default_factory=list)


@router.post(
    "/{plan_id}/import-results",
    response_model=ImportResultsResponse,
    summary="Import a remote agent's results file",
)
async def import_test_plan_results(
    plan_id: int = Path(..., gt=0),
    file: UploadFile = File(..., description="The results.json file produced by the remote agent"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Apply a remote agent's results file to its execution session.

    The file must be a ``results.json`` matching the schema in the
    original bundle (``results_schema.json``).  It is correlated to the
    correct execution session by ``bundle_id`` — do NOT upload a file
    from a different plan, it will be rejected.

    Re-importing the same file is idempotent; rows are updated in place.
    Partial imports (interim results) are supported — set
    ``is_final: true`` in the results file when the agent is done to
    transition the session to ``completed``.
    """
    # Hard cap at 10 MB to prevent accidental log-dump uploads from blowing out
    # the DB. Read is bounded (see read_upload_capped) so an oversize file is
    # rejected before it materializes in memory, not after.
    MAX_BYTES = 10 * 1024 * 1024
    try:
        file_bytes = await read_upload_capped(
            file, MAX_BYTES, detail=f"Results file too large (max {MAX_BYTES:,} bytes)."
        )
    except HTTPException:
        raise  # the 413 cap rejection — surface it as-is
    except Exception:  # noqa: BLE001
        # Don't leak the raw read exception (tempfile paths, low-level OS
        # errors) to the client. Log details server-side.
        logger.exception("Failed to read uploaded results file", extra={"plan_id": plan_id})
        raise HTTPException(
            status_code=400,
            detail="Could not read the uploaded file. Try uploading again.",
        )
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    from app.services.bundle_import_service import import_results_file, BundleImportError

    try:
        summary = import_results_file(
            db,
            plan_id=plan_id,
            project_id=project.id,
            file_bytes=file_bytes,
            filename=file.filename,
            imported_by_id=current_user.id,
        )
    except BundleImportError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    db.commit()
    return ImportResultsResponse(**summary)
