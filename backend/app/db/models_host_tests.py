"""Individual host tests: proposed work, independent of evidence and sessions."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

ACTIVE_TEST_STATUSES = ("proposed", "in_progress")
TESTED_OUTCOMES = ("finding", "no_finding", "inconclusive")


class HostTest(Base):
    __tablename__ = "host_tests"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False)
    name_id = Column(Integer, ForeignKey("dns_names.id", ondelete="SET NULL"))
    target_fqdn = Column(String(253))  # intent survives deletion of the navigation link
    tool = Column(String(100))  # historical free-text tests may not name a tool
    description = Column(Text, nullable=False)
    command = Column(Text)
    rationale = Column(Text, nullable=False)
    expected_result = Column(Text)
    references = Column(JSON)
    priority = Column(String(20), nullable=False, default="medium")
    label = Column(String(255))
    # v2.445.0 — the weakness this test is meant to confirm: the ISSUE's
    # identity (``vuln_identity.issue_key``), not a vulnerability row id —
    # rows are re-created across scans and deleted with a scan, the issue on
    # this host is not.  ``issue_title`` is what it was called when linked.
    issue_key = Column(String(600))
    issue_title = Column(String(500))
    status = Column(String(20), nullable=False, default="proposed")
    assigned_to_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    tester_summary = Column(Text)
    source = Column(String(20), nullable=False)
    agent_session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="SET NULL"))
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    agent_model = Column(String(100))
    agent_client = Column(String(100))
    prompt_version = Column(String(20))
    dismissed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    dismissed_at = Column(DateTime(timezone=True))
    dismissed_reason = Column(Text)
    request_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    host = relationship("Host")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        CheckConstraint("status IN ('proposed','in_progress','done','dismissed')", name="ck_host_test_status"),
        CheckConstraint("priority IN ('critical','high','medium','low','info')", name="ck_host_test_priority"),
        CheckConstraint("source IN ('agent','person')", name="ck_host_test_source"),
        UniqueConstraint("project_id", "request_key", name="uq_host_test_request"),
        Index("ix_host_test_host_status", "host_id", "status"),
        Index("ix_host_test_project_status", "project_id", "status", "id"),
        Index("ix_host_test_assignee", "project_id", "assigned_to_id", "status"),
        Index("ix_host_test_label", "project_id", "label"),
        Index("ix_host_test_issue", "host_id", "issue_key"),
    )
