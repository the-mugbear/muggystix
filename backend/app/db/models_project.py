"""
Project and Notification Models

Multi-project support for isolating pentest engagements, plus notification
infrastructure for @mentions and team coordination.
"""

from enum import Enum

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, ForeignKey,
    UniqueConstraint, Index, JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base


class ProjectRole(str, Enum):
    """Per-project membership role — the granular capability tier
    (v2.46.0).

    This is where the analyst/auditor/viewer vocabulary lives now;
    the global ``UserRole`` is binary (admin / member).  A user's
    rights ON A PROJECT come from their ``ProjectMembership.role``,
    checked via ``require_project_role``.  Hierarchy (high → low):
    ADMIN > ANALYST > AUDITOR > VIEWER.
    """
    ADMIN = "admin"        # Manage project membership + everything analyst can do
    ANALYST = "analyst"    # Read/write security data: scans, scopes, plans, recon
    AUDITOR = "auditor"    # Read-only, with audit-log visibility
    VIEWER = "viewer"      # Read-only access to scans and hosts


class Project(Base):
    """A project isolates an engagement's scans, hosts, scopes, and findings."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text)
    status = Column(String(20), nullable=False, default="active")  # active, in_progress, completed, archived
    is_default = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)  # kept for backward compat, derived from status
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    created_by = relationship("User", foreign_keys=[created_by_id])
    memberships = relationship(
        "ProjectMembership", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMembership(Base):
    """Per-project role assignment for a user."""
    __tablename__ = "project_memberships"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False, default=ProjectRole.VIEWER.value)  # see ProjectRole
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project = relationship("Project", back_populates="memberships")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_user"),
    )


class Notification(Base):
    """In-app notification for mentions, assignments, status changes, etc."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    type = Column(String(50), nullable=False)  # mention, status_change, assignment, system
    title = Column(String(255), nullable=False)
    body = Column(Text)
    source_type = Column(String(50))  # note, host, scan, project
    source_id = Column(Integer)       # polymorphic FK
    # v2.86.1 — ondelete=SET NULL so deleting the actor preserves the
    # notification (audit-trail) but nulls the actor link.  Without this,
    # deleting any user who has ever produced a notification fails with
    # FK violation; the user_id (recipient) FK already CASCADEs above,
    # which is correct for the recipient direction.
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_read = Column(Boolean, default=False, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("idx_notification_user_unread", "user_id", "is_read", "created_at"),
    )


class NoteMention(Base):
    """Records an @mention of a user in a host note."""
    __tablename__ = "note_mentions"

    id = Column(Integer, primary_key=True, index=True)
    note_id = Column(Integer, ForeignKey("host_notes.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    note = relationship("HostNote")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("note_id", "user_id", name="uq_note_mention_user"),
    )


class WebhookConfig(Base):
    """An outbound webhook for a project (v2.73.0).

    Fires a JSON POST (Slack-incoming-webhook compatible) on selected
    events — mentions, status changes, assignments.  ``events`` is a JSON
    list of event keys; an empty list means "all events".  Delivery is
    best-effort and fire-and-forget (see WebhookDispatcher).
    """
    __tablename__ = "webhook_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    url = Column(String(1000), nullable=False)
    # Optional HMAC-SHA256 signing secret, Fernet-encrypted at rest.
    secret_encrypted = Column(Text, nullable=True)
    # JSON list of event keys (empty = all).  See WEBHOOK_EVENTS.
    events = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
