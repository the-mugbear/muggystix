"""
Integration Credentials Endpoints

Two surfaces:
  1. User-facing admin CRUD at ``/integrations/`` (JWT, self-service).
     Secrets are write-only — responses return ``has_secret``/``has_secret2``
     booleans rather than plaintext.
  2. Agent-facing read-only at ``/agent/integrations`` (API-key auth).
     Returns decrypted secrets so the agent can call scanner APIs
     directly.  Scoped to the agent's project.
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_integrations import IntegrationCredential, IntegrationType
from app.db.models_project import ProjectMembership
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.services.integration_service import IntegrationService
from app.services.url_validator import (
    require_public_http_url,
    is_integration_private_allowed,
)


# v2.90.4 (code review #4) — integration project_id must reference a
# project the caller actually belongs to.  Pre-fix any user could
# bind their credential to any project_id, leaving orphan credentials
# attached to projects the owner has no membership in.  Secrets stay
# owned by current_user so this isn't a leak, but the project
# boundary was weakened; this gate restores it.  Global admins bypass
# the membership check (consistent with the rest of the auth model).
def _assert_project_member_or_admin(
    db: Session, user: User, project_id: Optional[int],
) -> None:
    if project_id is None:
        return
    if user.role == UserRole.ADMIN:
        return
    membership = (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of that project.",
        )


router = APIRouter(dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IntegrationResponse(BaseModel):
    id: int
    name: str
    integration_type: str
    project_id: Optional[int]
    base_url: Optional[str]
    has_secret: bool
    has_secret2: bool
    extra_config: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: Any
    updated_at: Any

    model_config = ConfigDict(from_attributes=True)


def _to_response(row: IntegrationCredential) -> IntegrationResponse:
    extra = None
    if row.extra_config:
        try:
            extra = json.loads(row.extra_config)
        except ValueError:
            extra = None
    return IntegrationResponse(
        id=row.id,
        name=row.name,
        integration_type=row.integration_type,
        project_id=row.project_id,
        base_url=row.base_url,
        has_secret=bool(row.secret_encrypted),
        has_secret2=bool(row.secret2_encrypted),
        extra_config=extra,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class IntegrationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    integration_type: str
    project_id: Optional[int] = None
    base_url: Optional[str] = None
    secret: Optional[str] = None
    secret2: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: bool = True


class IntegrationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    project_id: Optional[int] = None
    clear_project: bool = False
    base_url: Optional[str] = None
    secret: Optional[str] = None
    clear_secret: bool = False
    secret2: Optional[str] = None
    clear_secret2: bool = False
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


# ---------------------------------------------------------------------------
# User-facing admin endpoints (JWT)
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=List[IntegrationResponse],
    summary="List the current user's integration credentials",
)
def list_integrations(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = IntegrationService(db)
    return [_to_response(r) for r in svc.list_for_user(current_user.id, project_id=project_id)]


@router.get(
    "/types",
    summary="List supported integration types (for the UI picker)",
)
def list_integration_types():
    return [
        {"value": t.value, "label": t.value.replace("_", " ").title()}
        for t in IntegrationType
    ]


# ---------------------------------------------------------------------------
# Pre-save connection test (v2.49.4)
# ---------------------------------------------------------------------------

class IntegrationTestRequest(BaseModel):
    """Verifies a configuration before persisting.  Shape matches
    ``IntegrationCreate`` so the create modal can hand its current
    form values straight through.  Secrets are accepted plaintext on
    this hop because they're never persisted and never logged —
    ``integration_test_service`` enforces both."""
    integration_type: str
    base_url: Optional[str] = None
    secret: Optional[str] = None
    secret2: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None


class IntegrationTestResponse(BaseModel):
    ok: Optional[bool] = Field(
        None,
        description=(
            "True = probe authenticated; False = probe failed (see "
            "``message``); null = no concrete probe is implemented for "
            "this integration type yet."
        ),
    )
    integration_type: str
    message: str
    http_status: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    duration_ms: int


@router.post(
    "/test",
    response_model=IntegrationTestResponse,
    status_code=200,
    summary="Test an integration configuration without persisting it",
)
def test_integration(
    body: IntegrationTestRequest,
    # Admin-only: this endpoint dials an operator-supplied URL, so a lower-priv
    # member could otherwise use it as an internal-network port/timing oracle
    # (the scanner/LLM types intentionally allow private addresses).  URL
    # validation still blocks cloud-metadata / link-local regardless; gating
    # to admin removes the viewer/auditor probe vector. (Create still allows
    # project members via _assert_project_member_or_admin — tightening that to
    # match is a follow-up.)
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Verify URL + credentials before saving.

    Always returns 200 — the result's ``ok`` field carries the
    outcome.  This keeps the UI's failure-rendering simple and the
    audit log honest (every test attempt is one log line at
    ``app.services.integration_test_service``).
    """
    from app.services.integration_test_service import test_integration_config
    result = test_integration_config(
        integration_type=body.integration_type,
        base_url=body.base_url,
        secret=body.secret,
        secret2=body.secret2,
        extra_config=body.extra_config,
        user_id=current_user.id,
    )
    return IntegrationTestResponse(
        ok=result.ok,
        integration_type=result.integration_type,
        message=result.message,
        http_status=result.http_status,
        details=result.details,
        duration_ms=result.duration_ms,
    )


@router.post(
    "/",
    response_model=IntegrationResponse,
    status_code=201,
    summary="Add a new integration credential",
)
def create_integration(
    body: IntegrationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # v2.90.4 (code review #4) — gate project_id by membership.
    _assert_project_member_or_admin(db, current_user, body.project_id)

    # Audit finding C2 — SSRF: validate the base URL resolves to a
    # public address before storing it.  Ollama has a loopback
    # carve-out because users legitimately run it at localhost.
    if body.base_url:
        try:
            require_public_http_url(
                body.base_url,
                allow_private=is_integration_private_allowed(body.integration_type),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base_url: {exc}")

    svc = IntegrationService(db)
    try:
        row = svc.create(
            user_id=current_user.id,
            name=body.name,
            integration_type=body.integration_type,
            project_id=body.project_id,
            base_url=body.base_url,
            secret=body.secret,
            secret2=body.secret2,
            extra_config=body.extra_config,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(row)


@router.patch(
    "/{integration_id}",
    response_model=IntegrationResponse,
)
def update_integration(
    body: IntegrationUpdate,
    integration_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # v2.90.4 (code review #4) — same gate as create.  Only triggers
    # when the body actually sets project_id; clear_project moves the
    # row OFF a project and doesn't need a membership check.
    if body.project_id is not None:
        _assert_project_member_or_admin(db, current_user, body.project_id)

    svc = IntegrationService(db)
    # Validate the new base_url against the existing row's type so
    # the Ollama carve-out applies correctly on update.  We need the
    # row to know the integration_type — fetch it first for the
    # validation, then let the service perform the actual update.
    if body.base_url:
        existing = svc.get(integration_id, current_user.id)
        if not existing:
            raise HTTPException(status_code=404, detail="Integration not found")
        try:
            require_public_http_url(
                body.base_url,
                allow_private=is_integration_private_allowed(existing.integration_type),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base_url: {exc}")

    try:
        row = svc.update(
            integration_id=integration_id,
            user_id=current_user.id,
            name=body.name,
            project_id=body.project_id,
            clear_project=body.clear_project,
            base_url=body.base_url,
            secret=body.secret,
            clear_secret=body.clear_secret,
            secret2=body.secret2,
            clear_secret2=body.clear_secret2,
            extra_config=body.extra_config,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _to_response(row)


@router.delete("/{integration_id}", status_code=204)
def delete_integration(
    integration_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = IntegrationService(db)
    try:
        svc.delete(integration_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None


# ---------------------------------------------------------------------------
# Agent-facing read endpoint REMOVED in v2.9.5 (code review critical #2).
# ---------------------------------------------------------------------------
#
# The previous ``/agent/integrations`` endpoint returned decrypted
# scanner credentials to any authenticated agent key for the owner +
# project — no per-plan binding, no capability check, no audit.  A
# leaked plan-generation key could pull every active Nessus/OpenVAS/
# etc. credential for the project.
#
# The endpoint was also redundant in practice: the reconnaissance
# prompt builder (``agent_prompt_service._integration_block``) already
# inlines the credentials directly into the recon instructions when
# the user clicks "Start Agentic Recon", so a terminal-side agent has
# everything it needs without a second round trip.
#
# If a future workflow needs programmatic access to integration
# credentials from an agent context, it should come back as a
# capability-scoped endpoint: require the key to be minted for a
# specific reconnaissance plan, require an explicit per-integration
# grant on that plan, log each retrieval, and consider returning a
# short-lived proxy token rather than the reusable plaintext.
