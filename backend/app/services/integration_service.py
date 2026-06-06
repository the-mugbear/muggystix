"""
Integration Credential Service

Per-user CRUD for configured scanner integrations, plus a helper for
the recon prompt builder to look up credentials available to a given
(user, project) pair.  Re-uses the Fernet encryption helpers from
``llm_provider_service`` so there's exactly one place where the key
derivation lives.

Commit-boundary policy (audit #43):
    ``create`` / ``update`` / ``delete`` **commit internally**.  This
    matches ``LLMProviderService`` and the majority of BlueStick
    services — the operations are single-statement mutations that
    have no larger transaction to join, so the endpoint becomes a
    trivial pass-through.  Bundle services are the intentional
    exceptions (they compose within a larger transaction); see their
    module docstrings for the justification.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db.models_integrations import IntegrationCredential, IntegrationType
from app.services.llm_provider_service import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)


class IntegrationService:
    def __init__(self, db: Session):
        self.db = db

    def list_for_user(
        self,
        user_id: int,
        project_id: Optional[int] = None,
    ) -> List[IntegrationCredential]:
        """Return all integrations owned by the user.

        If ``project_id`` is given, filter to credentials that are
        either scoped to that project or user-global (project_id NULL).
        """
        q = self.db.query(IntegrationCredential).filter(
            IntegrationCredential.user_id == user_id,
        )
        if project_id is not None:
            q = q.filter(
                or_(
                    IntegrationCredential.project_id == project_id,
                    IntegrationCredential.project_id.is_(None),
                )
            )
        return q.order_by(IntegrationCredential.integration_type, IntegrationCredential.name).all()

    def get(self, integration_id: int, user_id: int) -> Optional[IntegrationCredential]:
        return (
            self.db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.id == integration_id,
                IntegrationCredential.user_id == user_id,
            )
            .first()
        )

    def create(
        self,
        *,
        user_id: int,
        name: str,
        integration_type: str,
        project_id: Optional[int],
        base_url: Optional[str],
        secret: Optional[str],
        secret2: Optional[str],
        extra_config: Optional[Dict[str, Any]],
        is_active: bool = True,
    ) -> IntegrationCredential:
        if integration_type not in {t.value for t in IntegrationType}:
            raise ValueError(f"Unknown integration_type {integration_type!r}")
        row = IntegrationCredential(
            user_id=user_id,
            project_id=project_id,
            name=name,
            integration_type=integration_type,
            base_url=base_url,
            secret_encrypted=encrypt_secret(secret) if secret else None,
            secret2_encrypted=encrypt_secret(secret2) if secret2 else None,
            extra_config=json.dumps(extra_config) if extra_config else None,
            is_active=is_active,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(
        self,
        *,
        integration_id: int,
        user_id: int,
        name: Optional[str] = None,
        project_id: Optional[int] = None,
        clear_project: bool = False,
        base_url: Optional[str] = None,
        secret: Optional[str] = None,
        clear_secret: bool = False,
        secret2: Optional[str] = None,
        clear_secret2: bool = False,
        extra_config: Optional[Dict[str, Any]] = None,
        is_active: Optional[bool] = None,
    ) -> IntegrationCredential:
        row = self.get(integration_id, user_id)
        if not row:
            raise ValueError("Integration not found")
        if name is not None:
            row.name = name
        if clear_project:
            row.project_id = None
        elif project_id is not None:
            row.project_id = project_id
        if base_url is not None:
            row.base_url = base_url
        if clear_secret:
            row.secret_encrypted = None
        elif secret:
            row.secret_encrypted = encrypt_secret(secret)
        if clear_secret2:
            row.secret2_encrypted = None
        elif secret2:
            row.secret2_encrypted = encrypt_secret(secret2)
        if extra_config is not None:
            row.extra_config = json.dumps(extra_config)
        if is_active is not None:
            row.is_active = bool(is_active)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, integration_id: int, user_id: int) -> None:
        row = self.get(integration_id, user_id)
        if not row:
            raise ValueError("Integration not found")
        self.db.delete(row)
        self.db.commit()


def decrypt_integration(row: IntegrationCredential) -> Dict[str, Any]:
    """Return a dict with decrypted secrets — for server-side use only.

    This is the payload the recon prompt builder embeds into the agent
    instructions and what ``/agent/integrations`` returns to the
    authenticated agent.  Never serialize this to a user-facing admin
    response; use the ``IntegrationResponse`` Pydantic model for that,
    which exposes only ``has_secret`` / ``has_secret2`` booleans.
    """
    extra = None
    if row.extra_config:
        try:
            extra = json.loads(row.extra_config)
        except ValueError:
            extra = None
    return {
        "id": row.id,
        "name": row.name,
        "integration_type": row.integration_type,
        "project_id": row.project_id,
        "base_url": row.base_url,
        "secret": decrypt_secret(row.secret_encrypted),
        "secret2": decrypt_secret(row.secret2_encrypted),
        "extra_config": extra,
        "is_active": bool(row.is_active),
    }
