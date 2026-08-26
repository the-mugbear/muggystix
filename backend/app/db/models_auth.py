"""
Authentication and User Management Models

Models for user accounts, roles, and session management for security intelligence platform.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base
from enum import Enum
import datetime


class UserRole(str, Enum):
    """Global account role — deliberately binary (v2.46.0).

    A user's *capabilities* are scoped per-project via
    ``ProjectMembership.role`` (see ``ProjectRole`` in
    ``models_project``).  The global role answers one question only:
    is this account a system administrator?  ``require_role`` is used
    exclusively to gate ``ADMIN`` (user management, system settings,
    audit log).

    Pre-2.46.0 the global role carried the four-tier analyst/auditor/
    viewer vocabulary, but no endpoint ever gated a non-admin global
    tier — every granular check goes through ``require_project_role``.
    The extra tiers were dead weight, so the global role collapsed to
    ADMIN / MEMBER and the four-tier vocabulary moved to ``ProjectRole``.
    """
    ADMIN = "admin"          # Full system access: user management, settings, audit
    MEMBER = "member"        # Standard account; capabilities come from project memberships


class User(Base):
    """User accounts for the security platform"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=False)

    # User profile
    full_name = Column(String(100))
    role = Column(String(20), nullable=False, default=UserRole.MEMBER)

    # Account status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    last_login = Column(DateTime(timezone=True))

    # Activity tracking

    # Security settings
    password_changed_at = Column(DateTime(timezone=True), server_default=func.now())
    must_change_password = Column(Boolean, default=False, nullable=False, server_default="false")
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True))

    # Two-factor authentication (TOTP, RFC 6238).  The base32 secret is stored
    # as Fernet ciphertext under a TOTP-dedicated key (app.services.totp_service)
    # — separate from the credential-encryption key so the two can't be
    # confused.  The secret is written on /2fa/setup but ``totp_enabled`` stays
    # False until the user confirms a code (/2fa/enable); only then does login
    # require a second factor.  Users may IMPORT an existing secret (their PAM
    # machine-login seed) so the same authenticator entry works here too.
    totp_secret_encrypted = Column(Text, nullable=True)
    totp_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    totp_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # Audit fields
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    created_by = relationship("User", remote_side=[id])
    # CASCADE — sessions and personal API keys die with the user.
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    # v2.86.2 — audit_logs no longer orphan-cascades.  Audit-trail rows
    # exist precisely so a deleted user's actions remain on record;
    # CASCADE was the wrong policy (and the DB-level FK now SET NULLs
    # the user_id alongside, matching the policy).
    audit_logs = relationship("AuditLog", back_populates="user")
    # foreign_keys pins this to HostFollow.user_id — HostFollow also has
    # an assigned_by_id FK to users (v2.71.0), so the path is ambiguous
    # without it.  Assignment cascade is intentionally NOT mirrored here:
    # deleting the assigner shouldn't delete the assignee's follow row.
    host_follows = relationship(
        "HostFollow",
        foreign_keys="HostFollow.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # v2.86.2 — host_notes no longer orphan-cascades.  Notes are shared
    # project annotations; preserving them as "by deleted user" matches
    # the policy of every other audit-shape column in v2.86.2.  DB FK
    # is SET NULL + nullable=True (see host_notes.user_id).
    # foreign_keys pins this to Annotation.user_id — Annotation also has
    # assignee_id (a second FK to users), so the path must be explicit.
    annotations = relationship(
        "Annotation", back_populates="author", foreign_keys="Annotation.user_id",
    )
    # One-time TOTP recovery codes — die with the user (and are cleared on
    # /2fa/disable and regenerated on demand).
    recovery_codes = relationship(
        "UserRecoveryCode", back_populates="user", cascade="all, delete-orphan",
    )


class UserRecoveryCode(Base):
    """A one-time backup code for TOTP 2FA.

    Stored as a SHA-256 hash (the codes are high-entropy random, so a fast
    hash is appropriate — same rationale as agent API keys).  ``used_at`` is
    stamped when a code is consumed at login so it can't be replayed.
    """
    __tablename__ = "user_recovery_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)  # sha256 hex
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="recovery_codes")


class UserSession(Base):
    """Active user sessions for token management"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_jti = Column(String(36), unique=True, nullable=False, index=True)  # JWT ID

    # Session metadata
    ip_address = Column(String(45))  # IPv6 compatible
    user_agent = Column(Text)

    # Session lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True))
    revoked_reason = Column(String(100))

    # Relationships
    user = relationship("User", back_populates="sessions")


class AuditLog(Base):
    """Security audit logging for compliance and monitoring"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    # v2.86.2 — SET NULL so a deleted user's audit trail survives them.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    # Action details
    action = Column(String(50), nullable=False, index=True)  # login, logout, view_host, upload_scan, etc.
    resource_type = Column(String(50))  # host, scan, user, etc.
    resource_id = Column(String(50))

    # Event metadata
    ip_address = Column(String(45))
    user_agent = Column(Text)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Additional context
    details = Column(JSON)  # Flexible field for action-specific data
    success = Column(Boolean, default=True)
    error_message = Column(Text)

    # Relationships
    user = relationship("User", back_populates="audit_logs")


class APIKey(Base):
    """API keys for service-to-service or agent authentication"""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    # CASCADE — operator-owned API keys die with the operator; agent
    # API keys carry agent_id (the other branch) and are independent.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    # v2.116.0 — the key's scope binding: it points at exactly one AgentSession,
    # whose ``workflow`` discriminator (plan_generation / execution / recon /
    # assist) + ``plan_id`` / ``scope_id`` carry everything the four legacy
    # per-workflow FK columns used to. Those four —
    #   test_plan_id, scope_id, recon_session_id, assist_session_id
    # — were DROPPED in the contract phase (migration d4e9f1c72a6b) once deps +
    # minting resolved scope from this instead. ``get_current_agent`` now
    # REQUIRES it for an agent key (a null binding on an agent key is refused);
    # it stays nullable at the DB level only for operator/service keys
    # (``agent_id`` NULL), which carry no workflow session.
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Key details
    name = Column(String(100), nullable=False)  # Human-readable name
    key_hash = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(20), nullable=False, index=True)  # First few chars for identification

    # Permissions and scope
    scopes = Column(JSON)  # List of allowed operations
    # `allowed_ips` was removed in v2.240.4. It was declared here as an "IP
    # whitelist" and appeared NOWHERE else in the backend — nothing set it,
    # nothing read it, nothing enforced it. A security control that exists
    # only as a column is worse than no column: it reads like key access is
    # IP-pinned when it never was. If key IP-pinning is wanted, it needs an
    # enforcement point in the auth dependency, not a field.

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    last_used = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User")
    agent = relationship("Agent", foreign_keys=[agent_id])
    agent_session = relationship("AgentSession", foreign_keys=[agent_session_id])


class SystemIdentity(Base):
    """Per-deployment identity for agent provenance verification.

    One row per instance.  Generated on first boot with a random UUID
    and never rewritten unless the DB volume is wiped.  Exposed via
    the unauthenticated ``GET /.well-known/networkmapper.json``
    endpoint and embedded in every agent prompt so agents can confirm
    they're talking to the same instance that generated the prompt.

    v2.11.0 — introduced to let hesitant agents run a one-time
    identity check before acting on their instructions.  See the
    provenance block in ``agent_prompt_service.build_provenance_block``.
    """
    __tablename__ = "system_identity"

    id = Column(Integer, primary_key=True)
    instance_id = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
