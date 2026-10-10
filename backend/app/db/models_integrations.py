"""
Integration Credentials

The installation's configured scanners — vulnerability scanners (Nessus,
OpenVAS), template runners (Nuclei), proxy tools (Burp), or a generic API
token for anything else.  ONE list for the whole installation (owner decision
of 2026-10-10: "Integrations should be application wide"): no row belongs to
a user or to a project.

Secrets are encrypted at rest with the same Fernet key derivation used by
``llm_provider_service`` (HKDF from ``CREDENTIAL_ENCRYPTION_KEY``).  They are
in nothing a person or an agent reads by default: the people's routes answer
``has_secret`` flags, the agents' list answers name, type and address, and a
decrypted secret leaves the server only through
``POST /agent/scanner-integrations/{id}/credentials`` — one integration, on a
request that is recorded (``integration_service.share_credentials_with_agent``).
"""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class IntegrationType(str, enum.Enum):
    NESSUS = "nessus"
    OPENVAS = "openvas"
    NUCLEI = "nuclei"
    BURP = "burp"
    # Anything else: a single string secret + a free-form base URL.
    GENERIC_API = "generic_api"


class IntegrationCredential(Base):
    """One configured scanner of the installation.

    ``created_by_id`` is provenance only — who configured it.  Deleting that
    account keeps the integration (``SET NULL``).  Names are not unique: two
    people may have configured a scanner under one name before ownership came
    off (revision ``c8f2d5a9e3b1``), and a global administrator removes the
    duplicate.
    """
    __tablename__ = "integration_credentials"

    id = Column(Integer, primary_key=True, index=True)
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
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
    # JSON for per-type extras (a Nessus licence cap, the GMP port).
    extra_config = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    created_by = relationship("User", foreign_keys=[created_by_id])
