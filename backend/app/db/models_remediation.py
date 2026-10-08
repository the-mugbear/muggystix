"""Remediation tracking: who was told about a finding on a host, and where
the fix stands (v2.457.0).

This is the client's progress, kept by a project admin.  It is deliberately
separate from the assessor's conclusions (``Finding.status``,
``FindingHost.host_status``) and never synced with them.  Nothing here feeds
Posture, Operations or the client report.

Two facts, one name each, wherever a person or an agent reads them:

* this record's ``closed`` is the CONTACT's claim — shown as "Reported fixed",
  never "Closed";
* the endpoint's ``remediated`` is the ASSESSOR's conclusion — "Remediated".

The stored values are unchanged (``open`` / ``closed`` / ``deferred``); only
the labels differ.  Where the two disagree the remediation pages say so, as
its own countable state (``remediation_policy.verification_expr``):
"Reported fixed, not retested" (closed here, the endpoint not remediated and
not a false positive) and "Remediated, record still open" (the endpoint
remediated, this record open, deferred or never written).

The whole feature is per INSTALLATION (v2.461.0, ``RemediationPolicy``): an
installation that has not turned it on shows nothing about remediation, and
has no deadline, no "overdue" and no alert.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

REMEDIATION_STATUSES = ("open", "closed", "deferred")
# The fields a change entry can name, in the order the page shows them.
TRACKED_FIELDS = ("contact_email", "contact_name", "team", "notified_on", "status", "closed_on")
# Severities a remediation timeline can be set for, and the days a new
# installation starts with (None: no deadline).
TIMELINE_SEVERITIES = ("critical", "high", "medium", "low", "info")
DEFAULT_TIMELINE_DAYS = {"critical": 30, "high": 30, "medium": 90, "low": 120, "info": None}
DEFAULT_DUE_SOON_DAYS = 7
DEFAULT_TIME_ZONE = "UTC"
EVENT_KINDS = ("note", "change", "follow_up", "report")


class RemediationPolicy(Base):
    """The installation's remediation settings: ONE row (id 1), written by a
    global admin.  No row is the defaults with the feature off.

    ``days_<severity>`` is how long a contact has from the day a finding on a
    host was assigned to them; NULL means that severity has no deadline."""
    __tablename__ = "remediation_policy"

    id = Column(Integer, primary_key=True, autoincrement=False)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    days_critical = Column(Integer)
    days_high = Column(Integer)
    days_medium = Column(Integer)
    days_low = Column(Integer)
    days_info = Column(Integer)
    due_soon_days = Column(Integer, nullable=False, default=DEFAULT_DUE_SOON_DAYS,
                           server_default=str(DEFAULT_DUE_SOON_DAYS))
    # IANA name of the zone whose calendar day is "today" for every deadline
    # state: assigned and closed dates are entered by hand, in local days.
    time_zone = Column(String(64), nullable=False, default=DEFAULT_TIME_ZONE,
                       server_default=DEFAULT_TIME_ZONE)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (CheckConstraint("id = 1", name="ck_remediation_policy_one_row"),)


class FindingHostRemediation(Base):
    """The tracked fields of one finding on one host.  A row exists once
    something was recorded; a finding on a host with no row is open and has
    no contact."""
    __tablename__ = "finding_host_remediation"

    id = Column(Integer, primary_key=True)
    finding_host_id = Column(
        Integer, ForeignKey("finding_hosts.id", ondelete="CASCADE"), nullable=False, unique=True,
    )
    # Repeated from the finding so a contact can be looked up across projects
    # without joining through every project's findings.
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # Trimmed and lowercased: the address IS the contact's identity, in this
    # project and across projects.
    contact_email = Column(String(254))
    contact_name = Column(String(200))
    # The group that owns the fix (a development team, an operations group):
    # free text, trimmed, compared without case.  A contact is a person; the
    # team is how a manager reads the backlog (v2.462.0).
    team = Column(String(100))
    notified_on = Column(Date)
    status = Column(String(20), nullable=False, default="open", server_default="open")
    closed_on = Column(Date)
    # The deadline in force on the day the row was closed (v2.461.0).  While a
    # row is open its deadline is DERIVED from the assigned date, the
    # finding's current severity and the installation's current timeline, so
    # nothing stored can drift; closing freezes it here so "closed late" never
    # changes afterwards.  NULL on a row that is not closed, and on a closed
    # row that had no deadline.
    closed_due_on = Column(Date)
    # The last day somebody recorded chasing the contact about this row.
    last_follow_up_on = Column(Date)
    # The deadline an alert was already raised for, so each deadline alerts
    # once per kind; cleared when the status or the assigned date changes.
    due_soon_alerted_for = Column(Date)
    overdue_alerted_for = Column(Date)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    finding_host = relationship("FindingHost")

    __table_args__ = (
        CheckConstraint("status IN ('open','closed','deferred')", name="ck_remediation_status"),
        # A closed date belongs to a closed row (the service decides the two
        # together; this is what stops any other writer).
        CheckConstraint("closed_on IS NULL OR status = 'closed'", name="ck_remediation_closed_date"),
        CheckConstraint("closed_due_on IS NULL OR status = 'closed'", name="ck_remediation_closed_due_date"),
        Index("ix_remediation_contact_status", "contact_email", "status"),
        Index("ix_remediation_project_status", "project_id", "status"),
    )


class RemediationDaily(Base):
    """One project's findings on hosts by deadline state on one day (v2.462.0)
    — the history a derived deadline does not have.  Written by the worker's
    sweep (``remediation_service.snapshot``): the day's row is replaced on
    every sweep, so it ends the day holding that day's last count.  History
    starts the day the installation turns remediation tracking on; nothing is
    ever back-filled, because yesterday's deadlines cannot be recomputed
    honestly from today's timeline and severities."""
    __tablename__ = "remediation_daily"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    day = Column(Date, nullable=False)
    overdue = Column(Integer, nullable=False, default=0, server_default="0")
    due_soon = Column(Integer, nullable=False, default=0, server_default="0")
    on_track = Column(Integer, nullable=False, default=0, server_default="0")
    not_assigned = Column(Integer, nullable=False, default=0, server_default="0")
    no_deadline = Column(Integer, nullable=False, default=0, server_default="0")
    deferred = Column(Integer, nullable=False, default=0, server_default="0")
    closed = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        Index("uq_remediation_daily_project_day", "project_id", "day", unique=True),
        Index("ix_remediation_daily_day", "day"),
    )


class RemediationEvent(Base):
    """One timeline entry on a host: a field change, a recorded follow-up or
    a remediation list prepared for a contact (``report``) — all written by
    the service, immutable — or a note (its author's).  ``occurred_at`` is when it happened
    and may be backdated; ``created_at`` is when it was recorded."""
    __tablename__ = "remediation_events"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False)
    # The finding the entry is about, when it is about one.  Both links go
    # null when the finding is deleted or detached from the host; the title
    # stays so the timeline still reads.
    finding_id = Column(Integer, ForeignKey("findings.id", ondelete="SET NULL"))
    finding_host_id = Column(Integer, ForeignKey("finding_hosts.id", ondelete="SET NULL"))
    finding_title = Column(String(500))
    kind = Column(String(20), nullable=False)
    field = Column(String(30))
    old_value = Column(Text)
    new_value = Column(Text)
    body = Column(Text)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    edited_at = Column(DateTime(timezone=True))
    author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    agent_session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="SET NULL"))
    # A note written by a re-runnable caller: the same key is the same note.
    request_key = Column(String(100))

    author = relationship("User", foreign_keys=[author_id])

    __table_args__ = (
        CheckConstraint("kind IN ('note','change','follow_up','report')", name="ck_remediation_event_kind"),
        Index("ix_remediation_event_host", "host_id", "occurred_at"),
        Index("ix_remediation_event_finding_host", "finding_host_id"),
        Index(
            "uq_remediation_event_request", "project_id", "request_key", unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
            sqlite_where=text("request_key IS NOT NULL"),
        ),
    )
