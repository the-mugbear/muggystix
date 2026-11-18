"""
LLM Provider Endpoints

Per-user CRUD for configured LLM providers (OpenAI, Anthropic, Ollama,
etc).  All endpoints are scoped to the authenticated user — a provider
row's ``user_id`` comes from the JWT, never from the request body.

API keys are write-only: accepted on create/update, never returned in
responses.  The client shows a masked placeholder instead.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User
from app.db.models_llm import LLMProvider, LLMProviderType
from app.api.deps import can_write_in_some_project, get_current_user
from app.services.llm_provider_service import (
    LLMProviderService, test_connection, end_read_before_provider_call,
)
from app.services.url_validator import require_public_http_url

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

#: Why an account that writes nowhere is refused (owner, 2026-10-11).
PROVIDER_NEEDS_A_WRITER = (
    "An LLM provider is used to draft report text, and drafting needs the analyst "
    "role on a project. This account is not an analyst or administrator of any project, "
    "so it cannot add, change, test or use a provider. It can still list and delete its own."
)


def require_drafting_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Adding, changing, testing or using a provider is for an account that
    can write in at least one project (``deps.can_write_in_some_project``).

    Providers stay per-user and self-service — this is NOT the global admin
    role.  But a provider's only use is drafting, a project analyst's action,
    and each of these calls makes the server send an HTTP request to an
    address the caller chose (an Ollama server may legitimately sit on a
    private network), so an account that could never draft does not get to
    make them.  Listing and deleting one's own providers stays open: someone
    whose role was lowered can still remove theirs."""
    if not can_write_in_some_project(db, current_user):
        raise HTTPException(status_code=403, detail=PROVIDER_NEEDS_A_WRITER)
    return current_user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LLMProviderResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: Optional[str]
    model_id: Optional[str]
    has_api_key: bool
    extra_config: Optional[Dict[str, Any]] = None
    is_default: bool
    created_at: Any
    updated_at: Any

    model_config = ConfigDict(from_attributes=True)


def _to_response(row: LLMProvider) -> LLMProviderResponse:
    extra = None
    if row.extra_config:
        try:
            extra = json.loads(row.extra_config)
        except ValueError:
            extra = None
    return LLMProviderResponse(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        model_id=row.model_id,
        has_api_key=bool(row.api_key_encrypted),
        extra_config=extra,
        is_default=bool(row.is_default),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class LLMProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider_type: str
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    api_key: Optional[str] = None   # plaintext, never stored
    extra_config: Optional[Dict[str, Any]] = None
    is_default: bool = False


class LLMProviderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    api_key: Optional[str] = None   # plaintext; if omitted the stored key is kept
    clear_api_key: bool = False     # set to True to remove the stored key
    extra_config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None


class TestConnectionResponse(BaseModel):
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=List[LLMProviderResponse],
    summary="List the current user's configured LLM providers",
)
def list_llm_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = LLMProviderService(db)
    return [_to_response(r) for r in svc.list_for_user(current_user.id)]


@router.post(
    "/",
    response_model=LLMProviderResponse,
    status_code=201,
    summary="Add a new LLM provider configuration",
    dependencies=[Depends(require_drafting_account)],
)
def create_llm_provider(
    body: LLMProviderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Audit finding C2 — SSRF: validate the base_url before storing
    # it.  Ollama is the only provider type where private IPs are
    # legitimate (users run ``ollama serve`` on localhost or a LAN
    # host); everything else must resolve to a public address.
    if body.base_url:
        try:
            require_public_http_url(
                body.base_url,
                allow_private=body.provider_type == "ollama",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base_url: {exc}")

    svc = LLMProviderService(db)
    try:
        row = svc.create(
            user_id=current_user.id,
            name=body.name,
            provider_type=body.provider_type,
            base_url=body.base_url,
            model_id=body.model_id,
            api_key_plaintext=body.api_key,
            extra_config=body.extra_config,
            is_default=body.is_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(row)


@router.patch(
    "/{provider_id}",
    response_model=LLMProviderResponse,
    dependencies=[Depends(require_drafting_account)],
)
def update_llm_provider(
    body: LLMProviderUpdate,
    provider_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = LLMProviderService(db)
    # Need the existing row to know the provider_type so the Ollama
    # carve-out applies correctly on update.  Fetch it before calling
    # update() so the SSRF check runs against the right policy.
    if body.base_url:
        existing = svc.get(provider_id, current_user.id)
        if not existing:
            raise HTTPException(status_code=404, detail="Provider not found")
        try:
            require_public_http_url(
                body.base_url,
                allow_private=existing.provider_type == "ollama",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base_url: {exc}")

    try:
        row = svc.update(
            provider_id=provider_id,
            user_id=current_user.id,
            name=body.name,
            base_url=body.base_url,
            model_id=body.model_id,
            api_key_plaintext=body.api_key,
            clear_api_key=body.clear_api_key,
            extra_config=body.extra_config,
            is_default=body.is_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _to_response(row)


@router.delete(
    "/{provider_id}",
    status_code=204,
)
def delete_llm_provider(
    provider_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = LLMProviderService(db)
    try:
        svc.delete(provider_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None


@router.post(
    "/{provider_id}/test",
    response_model=TestConnectionResponse,
    summary="Verify credentials / reachability for a configured provider",
    dependencies=[Depends(require_drafting_account)],
)
def test_llm_provider(
    provider_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = LLMProviderService(db)
    row = svc.get(provider_id, current_user.id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    # No transaction is held while the provider answers (or does not).
    end_read_before_provider_call(db, row)
    result = test_connection(row)
    return TestConnectionResponse(**result)


@router.get(
    "/types",
    summary="List supported provider types (for the UI picker)",
)
def list_provider_types():
    return [
        {"value": p.value, "label": p.value.replace("_", " ").title()}
        for p in LLMProviderType
    ]


# There is no generic completion route (review 2026-11-17, A7).  ``POST
# /{provider_id}/complete`` was built for in-app agent runs, which were
# removed; nothing called it, and it relayed any prompt to an address its
# caller chose while holding the request's transaction for the provider's
# whole timeout.  The server calls a provider only to draft report text
# (``report_draft_service``) and to test a connection (above).
