"""Project outbound-webhook management (v2.73.0).

Admin-only CRUD for ``WebhookConfig`` plus a synchronous test-send.
Mounted under ``/projects/{id}/webhooks``.  Secrets are write-only
(accepted on create/update, never returned — the response exposes only
``has_secret``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole, WebhookConfig
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.services.llm_provider_service import encrypt_secret
from app.services.webhook_dispatcher import (
    WEBHOOK_EVENTS,
    WebhookDispatcher,
    is_valid_webhook_url,
)

router = APIRouter(dependencies=[Depends(get_current_user)])

_ADMIN = Depends(require_project_role(ProjectRole.ADMIN))


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., max_length=1000)
    secret: Optional[str] = Field(None, max_length=500)
    events: List[str] = Field(default_factory=list)
    is_active: bool = True


class WebhookUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    url: Optional[str] = Field(None, max_length=1000)
    # None = leave unchanged; "" = clear the secret; non-empty = replace.
    secret: Optional[str] = Field(None, max_length=500)
    events: Optional[List[str]] = None
    is_active: Optional[bool] = None


class WebhookResponse(BaseModel):
    id: int
    project_id: int
    name: str
    url: str
    has_secret: bool
    events: List[str]
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _serialize(cfg: WebhookConfig) -> WebhookResponse:
    return WebhookResponse(
        id=cfg.id,
        project_id=cfg.project_id,
        name=cfg.name,
        url=cfg.url,
        has_secret=bool(cfg.secret_encrypted),
        events=cfg.events or [],
        is_active=cfg.is_active,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _validate_events(events: List[str]) -> None:
    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown event(s): {', '.join(unknown)}")


def _config_or_404(db: Session, project_id: int, webhook_id: int) -> WebhookConfig:
    cfg = (
        db.query(WebhookConfig)
        .filter(WebhookConfig.id == webhook_id, WebhookConfig.project_id == project_id)
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return cfg


@router.get("/event-types", summary="Available webhook event keys")
def list_event_types():
    """The events a webhook can subscribe to (empty subscription = all)."""
    return [{"key": k, "description": v} for k, v in WEBHOOK_EVENTS.items()]


@router.get("", response_model=List[WebhookResponse], summary="List project webhooks")
def list_webhooks(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    rows = (
        db.query(WebhookConfig)
        .filter(WebhookConfig.project_id == project.id)
        .order_by(WebhookConfig.created_at.desc())
        .all()
    )
    return [_serialize(c) for c in rows]


@router.post("", response_model=WebhookResponse, status_code=201, summary="Create a webhook")
def create_webhook(
    payload: WebhookCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    if not is_valid_webhook_url(payload.url):
        raise HTTPException(status_code=422, detail="URL must be an absolute http(s) URL")
    _validate_events(payload.events)
    cfg = WebhookConfig(
        project_id=project.id,
        name=payload.name.strip(),
        url=payload.url.strip(),
        secret_encrypted=encrypt_secret(payload.secret) if payload.secret else None,
        events=payload.events,
        is_active=payload.is_active,
        created_by_id=current_user.id,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


@router.patch("/{webhook_id:int}", response_model=WebhookResponse, summary="Update a webhook")
def update_webhook(
    webhook_id: int,
    payload: WebhookUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    cfg = _config_or_404(db, project.id, webhook_id)
    if payload.name is not None:
        cfg.name = payload.name.strip()
    if payload.url is not None:
        if not is_valid_webhook_url(payload.url):
            raise HTTPException(status_code=422, detail="URL must be an absolute http(s) URL")
        cfg.url = payload.url.strip()
    if payload.events is not None:
        _validate_events(payload.events)
        cfg.events = payload.events
    if payload.is_active is not None:
        cfg.is_active = payload.is_active
    if payload.secret is not None:
        # "" clears the secret; any other value replaces it.
        cfg.secret_encrypted = encrypt_secret(payload.secret) if payload.secret else None
    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


@router.delete("/{webhook_id:int}", status_code=204, summary="Delete a webhook")
def delete_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    cfg = _config_or_404(db, project.id, webhook_id)
    db.delete(cfg)
    db.commit()
    return Response(status_code=204)


@router.post("/{webhook_id:int}/test", summary="Send a test event to a webhook")
def test_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    cfg = _config_or_404(db, project.id, webhook_id)
    return WebhookDispatcher(db).deliver_test(cfg)


# ---------------------------------------------------------------------------
# Delivery history (v2.235.0)
# ---------------------------------------------------------------------------
# The outbox keeps FAILED rows deliberately, so "did that alert ever go out?"
# is answerable — but nothing exposed them, which meant the answer lived only
# in psql. These make the retained data reachable, and let an operator push a
# settled failure back into the queue once the receiver is fixed.

class WebhookDeliveryRow(BaseModel):
    id: int
    webhook_config_id: int
    webhook_name: Optional[str] = None
    event: str
    status: str
    attempts: int
    max_attempts: int
    last_error: Optional[str] = None
    response_status: Optional[int] = None
    next_attempt_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/deliveries",
    response_model=List[WebhookDeliveryRow],
    summary="Recent webhook delivery attempts for this project",
)
def list_deliveries(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
    status: Optional[str] = None,
    limit: int = 100,
):
    """Newest first. ``status`` filters to pending / delivered / failed."""
    from app.db.models_project import WebhookDelivery

    limit = max(1, min(limit, 500))
    q = (
        db.query(WebhookDelivery, WebhookConfig.name)
        .outerjoin(WebhookConfig, WebhookConfig.id == WebhookDelivery.webhook_config_id)
        .filter(WebhookDelivery.project_id == project.id)
    )
    if status:
        q = q.filter(WebhookDelivery.status == status)
    rows = q.order_by(WebhookDelivery.created_at.desc()).limit(limit).all()
    return [
        WebhookDeliveryRow(
            id=d.id,
            webhook_config_id=d.webhook_config_id,
            webhook_name=name,
            event=d.event,
            status=d.status,
            attempts=d.attempts,
            max_attempts=d.max_attempts,
            last_error=d.last_error,
            response_status=d.response_status,
            next_attempt_at=d.next_attempt_at,
            created_at=d.created_at,
            delivered_at=d.delivered_at,
        )
        for d, name in rows
    ]


@router.post(
    "/deliveries/{delivery_id:int}/retry",
    response_model=WebhookDeliveryRow,
    summary="Requeue a failed delivery",
)
def retry_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _: object = _ADMIN,
):
    """Push a settled failure back into the queue after fixing the receiver.

    Resets the attempt counter — this is a fresh delivery decision by a human,
    not a continuation of the exhausted backoff, and leaving `attempts` at the
    cap would make the sweeper give up again on the first transient error.
    """
    from app.db.models_project import WebhookDelivery, WebhookDeliveryStatus

    row = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.project_id == project.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Delivery not found in this project")
    if row.status == WebhookDeliveryStatus.PENDING.value:
        raise HTTPException(
            status_code=409, detail="This delivery is already queued for retry.",
        )
    row.status = WebhookDeliveryStatus.PENDING.value
    row.attempts = 0
    row.last_error = None
    row.response_status = None
    row.delivered_at = None
    row.next_attempt_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return WebhookDeliveryRow(
        id=row.id,
        webhook_config_id=row.webhook_config_id,
        webhook_name=None,
        event=row.event,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        last_error=row.last_error,
        response_status=row.response_status,
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
        delivered_at=row.delivered_at,
    )
