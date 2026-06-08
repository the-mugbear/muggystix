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
    last_activity_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Security settings
    password_changed_at = Column(DateTime(timezone=True), server_default=func.now())
    must_change_password = Column(Boolean, default=False, nullable=False, server_default="false")
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True))

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
    # foreign_keys pins this to HostNote.user_id — HostNote also has
    # assignee_id (a second FK to users), so the path must be explicit.
    host_notes = relationship(
        "HostNote", back_populates="author", foreign_keys="HostNote.user_id",
    )


class UserSession(Base):
    """Active user sessions for token management"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_jti = Column(String(36), unique=True, nullable=False, index=True)  # JWT ID

    # Session metadata
    ip_address = Column(String(45))  # IPv6 compatible
    user_agent = Column(Text)
    device_info = Column(JSON)

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
    # When set, the key is scoped to a single test plan: auth requests that
    # target a different plan_id are rejected.  Lets two concurrent agents
    # own independent keys without sharing a fate when one is rotated.
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # v2.11.0: reconnaissance keys bind to a scope instead of a test
    # plan.  Mutually exclusive with test_plan_id — a key is either a
    # plan-scoped execution/generation key or a scope-scoped recon
    # key, never both.  Recon endpoints reject keys where scope_id is
    # NULL; plan endpoints reject keys where scope_id is set.
    scope_id = Column(
        Integer,
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # v2.45.0: recon keys also pin to a specific ReconSession.  Pre-fix,
    # two concurrent recons on the same scope collided because
    # `_load_recon_session` used a "newest active session" heuristic to
    # resolve the call's session from `scope_id` alone — so Agent A's
    # uploads to /agent/recon/upload silently landed on Agent B's
    # session (whichever was started most recently).  With the key
    # bound to a specific session, every endpoint that doesn't take a
    # session_id in the URL (/recon/context, /recon/upload,
    # /recon/summary, /recon/complete) resolves to the bound session
    # deterministically — no heuristic, no cross-agent collision.
    # Nullable for backwards compat with v2.44.5-and-older keys that
    # only have scope_id; the loader falls back to the heuristic for
    # those.
    recon_session_id = Column(
        Integer,
        ForeignKey("recon_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # v2.64.0 — assist-session keys are read-only, project-scoped, and
    # bound to a specific AssistSession row.  Mutually exclusive with
    # the other three scope columns above (a single key never spans
    # workflows; the four scope columns are XOR'd by the minting code,
    # not the DB).  /agent/assist/* endpoints require this column to
    # be set; /agent/test-plans/*, /agent/execution/*, and /agent/recon/*
    # all reject keys where it's populated.
    assist_session_id = Column(
        Integer,
        ForeignKey("assist_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # v2.116.0 — the unified scope binding that replaces the four
    # workflow-specific FKs above.  A key points at exactly one
    # AgentSession; its ``workflow`` discriminator + ``plan_id``/``scope_id``
    # carry what the four columns used to.  Nullable during the expand
    # phase (backfilled by the migration); the four legacy columns are
    # dropped in the contract phase once deps + minting read this instead.
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
    allowed_ips = Column(JSON)  # IP whitelist

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    last_used = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User")
    agent = relationship("Agent", foreign_keys=[agent_id])
    agent_session = relationship("AgentSession", foreign_keys=[agent_session_id])


class SecurityPolicy(Base):
    """System security policies and configuration"""
    __tablename__ = "security_policies"

    id = Column(Integer, primary_key=True, index=True)

    # Policy settings
    password_min_length = Column(Integer, default=12)
    password_require_uppercase = Column(Boolean, default=True)
    password_require_lowercase = Column(Boolean, default=True)
    password_require_numbers = Column(Boolean, default=True)
    password_require_symbols = Column(Boolean, default=True)
    password_expiry_days = Column(Integer, default=90)

    # Session security
    session_timeout_minutes = Column(Integer, default=480)  # 8 hours
    max_concurrent_sessions = Column(Integer, default=3)

    # Account lockout
    max_failed_login_attempts = Column(Integer, default=5)
    lockout_duration_minutes = Column(Integer, default=30)

    # Audit settings
    audit_retention_days = Column(Integer, default=365)
    require_audit_login = Column(Boolean, default=True)
    require_audit_data_access = Column(Boolean, default=True)

    # Policy metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    updated_by = relationship("User")


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
