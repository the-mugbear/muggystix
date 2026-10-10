"""
Integration Credentials Endpoints — the people's routes (``/integrations/``).

The installation has ONE list of configured scanners (owner decision of
2026-10-10).  Every signed-in user reads it, without secrets: a response
carries ``has_secret`` / ``has_secret2`` and who configured each, never
plaintext.  Creating, changing, testing and deleting one are the GLOBAL
administrator's, on any row.

What an agent may read of this list, and the one recorded request that hands
it a scanner's credentials, are on the agent surface
(``agent_assist.list_assist_scanner_integrations``,
``agent_browse.request_scanner_credentials``).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_integrations import IntegrationCredential, IntegrationType
from app.api.deps import get_current_user, require_role
from app.services.integration_service import IntegrationService, extra_config_of
from app.services.url_validator import (
    require_public_http_url,
    is_integration_private_allowed,
)


# Scanner integrations are system infrastructure: only global admins
# create/update/delete/test them.  Managing scanner secrets is an admin/ops
# action, and the test probe is a network-egress primitive that must not be
# reachable by lower-priv members.  This is an instance-level router: no
# project role applies here.
router = APIRouter(dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IntegrationResponse(BaseModel):
    id: int
    name: str
    integration_type: str
    base_url: Optional[str]
    has_secret: bool
    has_secret2: bool
    extra_config: Optional[Dict[str, Any]] = None
    is_active: bool
    created_by: Optional[str] = Field(
        None,
        description="Username of the account that configured it; null when that account is gone.",
    )
    created_at: Any
    updated_at: Any

    model_config = ConfigDict(from_attributes=True)


def _to_response(row: IntegrationCredential) -> IntegrationResponse:
    return IntegrationResponse(
        id=row.id,
        name=row.name,
        integration_type=row.integration_type,
        base_url=row.base_url,
        has_secret=bool(row.secret_encrypted),
        has_secret2=bool(row.secret2_encrypted),
        extra_config=extra_config_of(row),
        is_active=bool(row.is_active),
        created_by=row.created_by.username if row.created_by is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class IntegrationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    integration_type: str
    base_url: Optional[str] = None
    secret: Optional[str] = None
    secret2: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: bool = True


class IntegrationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
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
    summary="List the installation's scanner integrations (no secrets)",
)
def list_integrations(db: Session = Depends(get_db)):
    """Every signed-in user's read: each integration with whether a secret is
    stored and who configured it — never a secret."""
    return [_to_response(r) for r in IntegrationService(db).list_all()]


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
    # validation still blocks cloud-metadata / link-local regardless.  Matches
    # the admin gate on create/update/delete.
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
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
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
            created_by_id=current_user.id,
            name=body.name,
            integration_type=body.integration_type,
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
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    svc = IntegrationService(db)
    # Validate the new base_url against the existing row's type so
    # the Ollama carve-out applies correctly on update.  We need the
    # row to know the integration_type — fetch it first for the
    # validation, then let the service perform the actual update.
    if body.base_url:
        existing = svc.get(integration_id)
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
            name=body.name,
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
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    svc = IntegrationService(db)
    try:
        svc.delete(integration_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None
