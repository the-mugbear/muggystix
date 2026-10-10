"""
Integration Credential Service

The installation's configured scanners: ONE list, no user or project scoping
(owner decision of 2026-10-10).  CRUD for the people's routes, the
secret-free description an agent may read, and the one place a secret is
decrypted for an agent.  Re-uses the Fernet helpers of
``llm_provider_service`` so the key derivation lives in one place.

Who may call what is the routes' business (``endpoints/integrations.py``: the
list is every signed-in user's, writes are the global administrator's;
the agents' routes are gated like every agent route).

Commit-boundary policy (audit #43):
    ``create`` / ``update`` / ``delete`` **commit internally**.  This
    matches ``LLMProviderService`` and the majority of BlueStick
    services — the operations are single-statement mutations that
    have no larger transaction to join, so the endpoint becomes a
    trivial pass-through.  ``share_credentials_with_agent`` does NOT commit:
    its audit row lands in the request's own transaction, which the route
    commits.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from app.core.security import log_audit_event
from app.db.models_integrations import IntegrationCredential, IntegrationType
from app.services.llm_provider_service import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

#: The audit action written each time an agent is given a scanner's
#: credentials (``audit_logs.action``).
CREDENTIALS_SHARED_ACTION = "scanner_credentials_shared"

#: What each type's two stored secrets ARE, as the agent is handed them
#: (first secret, second secret or None).  A value that was never set is null.
_CREDENTIAL_FIELDS: Dict[str, tuple] = {
    IntegrationType.NESSUS.value: ("access_key", "secret_key"),
    IntegrationType.OPENVAS.value: ("username", "password"),
    IntegrationType.NUCLEI.value: ("pdcp_token", None),
    IntegrationType.BURP.value: ("api_key", None),
}
_GENERIC_CREDENTIAL_FIELDS = ("secret", None)


def extra_config_of(row: IntegrationCredential) -> Optional[Dict[str, Any]]:
    """The row's per-type extras (a Nessus licence cap, the GMP port) — never
    a secret: secrets have their own encrypted columns."""
    if not row.extra_config:
        return None
    try:
        extra = json.loads(row.extra_config)
    except ValueError:
        return None
    return extra if isinstance(extra, dict) else None


class IntegrationService:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self, *, active_only: bool = False) -> List[IntegrationCredential]:
        """Every integration of the installation, each with who configured it."""
        q = self.db.query(IntegrationCredential).options(
            joinedload(IntegrationCredential.created_by)
        )
        if active_only:
            q = q.filter(IntegrationCredential.is_active.is_(True))
        return q.order_by(
            IntegrationCredential.integration_type,
            IntegrationCredential.name,
            IntegrationCredential.id,
        ).all()

    def get(self, integration_id: int) -> Optional[IntegrationCredential]:
        return self.db.get(IntegrationCredential, integration_id)

    def create(
        self,
        *,
        created_by_id: Optional[int],
        name: str,
        integration_type: str,
        base_url: Optional[str],
        secret: Optional[str],
        secret2: Optional[str],
        extra_config: Optional[Dict[str, Any]],
        is_active: bool = True,
    ) -> IntegrationCredential:
        if integration_type not in {t.value for t in IntegrationType}:
            raise ValueError(f"Unknown integration_type {integration_type!r}")
        row = IntegrationCredential(
            created_by_id=created_by_id,
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
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        secret: Optional[str] = None,
        clear_secret: bool = False,
        secret2: Optional[str] = None,
        clear_secret2: bool = False,
        extra_config: Optional[Dict[str, Any]] = None,
        is_active: Optional[bool] = None,
    ) -> IntegrationCredential:
        row = self.get(integration_id)
        if not row:
            raise ValueError("Integration not found")
        if name is not None:
            row.name = name
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

    def delete(self, integration_id: int) -> None:
        row = self.get(integration_id)
        if not row:
            raise ValueError("Integration not found")
        self.db.delete(row)
        self.db.commit()


def describe_for_agent(row: IntegrationCredential) -> Dict[str, Any]:
    """What ANY agent session may read of a configured scanner: that it
    exists, its type and its address.  No secret, and no flag built from one.
    """
    return {
        "id": row.id,
        "name": row.name,
        "integration_type": row.integration_type,
        "base_url": row.base_url,
        "extra_config": extra_config_of(row),
    }


def share_credentials_with_agent(
    db: Session,
    row: IntegrationCredential,
    *,
    operator_id: Optional[int],
    project_id: Optional[int],
    agent_session_id: Optional[int],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """ONE integration with its secrets decrypted, for the agent that asked —
    and the record that it was shared.

    The only place a stored scanner secret is decrypted for anything but the
    administrator's own connection test.  The audit row is staged in the
    caller's transaction (``commit=False``): the route commits it before it
    answers, so credentials never leave without their record.  The row names
    the integration, the operator, the project and the session — never a
    secret.
    """
    log_audit_event(
        db,
        user_id=operator_id,
        action=CREDENTIALS_SHARED_ACTION,
        resource_type="integration",
        resource_id=str(row.id),
        details={
            "integration_id": row.id,
            "integration_name": row.name,
            "integration_type": row.integration_type,
            "project_id": project_id,
            "agent_session_id": agent_session_id,
        },
        ip_address=ip_address,
        user_agent=user_agent,
        commit=False,
    )
    first, second = _CREDENTIAL_FIELDS.get(row.integration_type, _GENERIC_CREDENTIAL_FIELDS)
    credentials = {first: decrypt_secret(row.secret_encrypted)}
    if second is not None:
        credentials[second] = decrypt_secret(row.secret2_encrypted)
    return {**describe_for_agent(row), "credentials": credentials}
