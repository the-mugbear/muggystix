"""
Integration Credentials

Per-user, project-scoped (optional) credentials for external security
tools the agent may invoke — vulnerability scanners (Nessus, OpenVAS),
template runners (Nuclei), proxy tools (Burp), or a generic API token
for anything else the user wants to hand the agent.

Secrets are encrypted at rest with the same Fernet key derivation used
by ``llm_provider_service`` (HKDF from ``SECRET_KEY``).  Decrypted
credentials are returned to the agent via the ``/agent/integrations``
endpoint and embedded into the recon prompt when the user has
configured them for the project.
"""

import enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class IntegrationType(str, enum.Enum):
    NESSUS = "nessus"
    OPENVAS = "openvas"
    NUCLEI = "nuclei"
    BURP = "burp"
    # Escape hatch for anything the user wants to hand their agent:
    # a single string secret + a free-form base URL.  The recon prompt
    # surfaces these as "misc API credentials" so the agent can ask the
    # user how to use them if it doesn't already know.
    GENERIC_API = "generic_api"


class IntegrationCredential(Base):
    """One configured integration owned by a user.

    ``project_id`` is optional — NULL means "available in every project"
    (a user-global credential), set means "only surfaces in this
    project's recon prompt."  This mirrors how pentesting engagements
    separate tools licensed for specific clients.
    """
    __tablename__ = "integration_credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = Column(String(100), nullable=False)
    integration_type = Column(String(32), nullable=False)
    base_url = Column(String(255), nullable=True)
    # Primary secret — Nessus access key, Burp API key, etc.  Encrypted.
    secret_encrypted = Column(Text, nullable=True)
    # Some integrations need a second secret (Nessus has access + secret
    # key, OpenVAS has username + password).  Also encrypted.
    secret2_encrypted = Column(Text, nullable=True)
    # JSON blob for per-type extras (scan policy IDs, target lists, etc).
    extra_config = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    project = relationship("Project", foreign_keys=[project_id])

    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "name", name="uq_integration_user_project_name"),
    )
