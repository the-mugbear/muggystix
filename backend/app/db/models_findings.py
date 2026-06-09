"""The Finding spine (foundation phase 3).

One ``Finding`` is the settled *record* that everything rolls up by — it
unifies the three previously-disconnected result streams:

  * promoted annotations  (source='note', evidence -> the annotation thread)
  * scanner vulnerabilities (source='scanner', vuln_id reference)
  * execution results flagged is_finding (source='execution', exec_result_id)

It carries the lifecycle the old ``SecurityFinding`` never had (status +
owner) and a many-to-many to hosts (``finding_hosts``) so one finding —
"SMB signing disabled" — is managed once and attached to N hosts.

References, never copies: a scanner/execution-sourced finding points AT the
source row rather than duplicating it, so there's no second dedup problem.

Status/severity/source are ``String`` columns (not Postgres ENUM) so the
vocabulary can evolve without an ``ALTER TYPE`` migration; the enums below
are the canonical value sets, enforced in application code.
"""
import enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class FindingSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_RISK = "accepted_risk"
    REMEDIATED = "remediated"
    RETEST = "retest"


class FindingSource(str, enum.Enum):
    NOTE = "note"
    SCANNER = "scanner"
    EXECUTION = "execution"
    MANUAL = "manual"


class FindingHostStatus(str, enum.Enum):
    """Per-host disposition within a multi-host finding — drives retest
    deltas ('was open on this host, now remediated')."""
    OPEN = "open"
    REMEDIATED = "remediated"
    RETEST = "retest"


class Finding(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    title = Column(String(500), nullable=False)
    severity = Column(String(20), nullable=False, index=True)   # FindingSeverity
    status = Column(
        String(20), nullable=False, default=FindingStatus.OPEN.value, index=True,
    )  # FindingStatus
    source = Column(String(20), nullable=False)                 # FindingSource
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Evidence / source references — at most one of vuln_id / exec_result_id
    # is set (scanner / execution sources); evidence_annotation_id points at
    # the originating annotation THREAD ROOT for note-sourced findings.
    evidence_annotation_id = Column(
        Integer, ForeignKey("annotations.id", ondelete="SET NULL"), nullable=True,
    )
    vuln_id = Column(
        Integer, ForeignKey("vulnerabilities.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    exec_result_id = Column(
        Integer, ForeignKey("test_execution_results.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    # Application-level dedup for scanner-sourced findings (e.g.
    # source+plugin/title), so re-ingesting the same scan upserts.
    dedup_key = Column(String(255), nullable=True, index=True)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    project = relationship("Project")
    owner = relationship("User", foreign_keys=[owner_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    evidence_annotation = relationship("Annotation")
    hosts = relationship(
        "FindingHost", back_populates="finding", cascade="all, delete-orphan",
        lazy="selectin",
    )
    status_history = relationship(
        "FindingStatusHistory", back_populates="finding",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_finding_project_status", "project_id", "status"),
    )


class FindingHost(Base):
    """A host affected by a finding (the cross-host M2M).  Carries a per-host
    status so a finding remediated on one host but open on another reflects
    the truth, and retest deltas are per-host."""
    __tablename__ = "finding_hosts"

    id = Column(Integer, primary_key=True, index=True)
    finding_id = Column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    host_id = Column(
        Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    port_id = Column(Integer, ForeignKey("ports_v2.id", ondelete="SET NULL"), nullable=True)
    host_status = Column(String(20), nullable=False, default=FindingHostStatus.OPEN.value)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    finding = relationship("Finding", back_populates="hosts")
    host = relationship("Host")

    __table_args__ = (
        UniqueConstraint("finding_id", "host_id", name="uq_finding_host"),
    )


class FindingStatusHistory(Base):
    """Audit trail of finding disposition transitions.  Same shape as
    AnnotationStatusHistory so both share one status-transition recorder
    (app.services.status_history_service)."""
    __tablename__ = "finding_status_history"

    id = Column(Integer, primary_key=True, index=True)
    finding_id = Column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    finding = relationship("Finding", back_populates="status_history")
    changed_by = relationship("User", foreign_keys=[changed_by_id])
