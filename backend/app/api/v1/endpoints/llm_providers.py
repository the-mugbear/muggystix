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
from app.api.v1.endpoints.auth import get_current_user
from app.services.llm_provider_service import (
    LLMProviderService, test_connection, chat_completion,
)
from app.services.url_validator import require_public_http_url
from app.services.prompt_sanitizer import sanitize_for_llm

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


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


# ---------------------------------------------------------------------------
# Chat completion (used by in-app agent runtime)
# ---------------------------------------------------------------------------

class CompletionRequest(BaseModel):
    system: Optional[str] = None
    prompt: str = Field(..., min_length=1)
    max_tokens: int = Field(2048, ge=1, le=16384)
    temperature: float = Field(0.3, ge=0.0, le=2.0)


class CompletionResponse(BaseModel):
    provider_id: int
    provider_name: str
    model_id: Optional[str]
    content: str
    raw_metadata: Dict[str, Any]  # tokens/finish reason, not full raw payload


@router.post(
    "/{provider_id}/complete",
    response_model=CompletionResponse,
    summary="Send a chat completion to a configured provider",
)
def complete(
    body: CompletionRequest,
    provider_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generic chat completion — the building block for in-app agent runs.

    Deliberately stateless: the endpoint does NOT spawn tool calls or
    persist a session.  The caller supplies the full prompt (including
    any tool-use instructions) and gets back the raw text response.
    Approval and execution of proposed commands happen in the browser,
    preserving the "agents are coordinators" architectural rule.
    """
    svc = LLMProviderService(db)
    provider = svc.get(provider_id, current_user.id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Audit finding H2 — server-side prompt sanitization.
    # The frontend ``promptSanitizer.ts`` already strips API keys and
    # inlined credentials before posting here, but that protection
    # only covers the happy path.  A caller hitting this endpoint
    # directly (curl, scripted client, compromised session) could
    # deliberately include a real secret in ``prompt`` or ``system``
    # to leak it to the LLM provider's request log.  Re-run the
    # same sanitization on the server side as the enforcement point.
    # Keep the client-side version as defense in depth.
    sanitized_prompt = sanitize_for_llm(body.prompt)
    sanitized_system = sanitize_for_llm(body.system)

    try:
        result = chat_completion(
            provider,
            system=sanitized_system,
            messages=[{"role": "user", "content": sanitized_prompt}],
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
    except RuntimeError as exc:
        # Configuration error (missing base_url, missing API key for a
        # provider that needs one, etc).  These are user-facing.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        # Code review nitpick #1: don't leak upstream exception text to
        # the client.  Log the detail server-side and return a stable
        # user-facing message so error responses don't carry provider
        # internals (stack frames, API keys accidentally echoed, etc.).
        logger.exception(
            "Upstream LLM call failed",
            extra={"provider_id": provider.id, "provider_type": provider.provider_type},
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "The LLM provider rejected the request or was unreachable. "
                "Use the Test Connection button on the LLM Providers page to "
                "check credentials and network reachability."
            ),
        )

    # Extract a minimal metadata payload — token counts when available,
    # but not the full raw response (which could be large and include
    # provider-specific debug info we don't need in the UI).
    raw = result.get("raw") or {}
    meta: Dict[str, Any] = {}
    usage = raw.get("usage") or {}
    if isinstance(usage, dict):
        meta["usage"] = usage
    if "model" in raw:
        meta["model"] = raw["model"]
    if "stop_reason" in raw:
        meta["stop_reason"] = raw["stop_reason"]
    if "finish_reason" in raw:
        meta["finish_reason"] = raw["finish_reason"]

    return CompletionResponse(
        provider_id=provider.id,
        provider_name=provider.name,
        model_id=provider.model_id,
        content=result.get("content") or "",
        raw_metadata=meta,
    )
