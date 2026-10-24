"""Presentation mode (2.530.0) — a shared, driven view of an ISSUED report.

One member starts a presentation of an issued report; members of the project
(auditor and above) join from their own browsers; one tab drives at a time and
any of them takes control in one click.  What is shown is the report's frozen
dataset only (``services/presentation_slides``).

Three tables:

* ``presentations`` — the shared state: which slide (``slide_key``) and which
  host filter, who drives and from which tab, and ``revision``, raised by a
  move, Take control and End — and by nothing else (presence has its own
  table).  At most one live presentation per report.
* ``presentation_viewers`` — presence, one row per tab: when it last read the
  presentation and, for a Display, the revision and position it last RENDERED
  (stored monotonically: an older acknowledgment never replaces a newer one).
* ``presentation_layout_reports`` — a slide that overflowed its estimate in
  someone's browser (the fit fallback): a defect in the estimator, kept so it
  can be fixed there.
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

#: The host filter's kinds (``presentation_slides`` reads them).
HOST_FILTER_KINDS = ("segment", "site", "text")
#: The kinds of tab (plan: Display, Controller, Browse).
VIEWER_KINDS = ("display", "controller", "browse")
#: Why a presentation ended.
END_REASONS = ("ended", "idle")

SLIDE_KEY_MAX = 200
FILTER_VALUE_MAX = 200
CLIENT_ID_MAX = 64

_FILTER_KINDS_SQL = ", ".join(f"'{k}'" for k in HOST_FILTER_KINDS)


class Presentation(Base):
    __tablename__ = "presentations"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    started_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Who drives (the person) AND from which tab — a move must match both.
    driver_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    driver_client_id = Column(String(CLIENT_ID_MAX), nullable=True)

    # The position — validated against the report's slide list for this filter.
    slide_key = Column(String(SLIDE_KEY_MAX), nullable=False)
    host_filter_kind = Column(String(10), nullable=True)
    host_filter_value = Column(String(FILTER_VALUE_MAX), nullable=True)

    # Raised by a move, Take control and End; nothing else.
    revision = Column(Integer, nullable=False, default=1, server_default=text("1"))

    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_move_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    ended_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ended_reason = Column(String(10), nullable=True)

    driver = relationship("User", foreign_keys=[driver_id])
    started_by = relationship("User", foreign_keys=[started_by_id])
    ended_by = relationship("User", foreign_keys=[ended_by_id])

    __table_args__ = (
        CheckConstraint(
            f"(host_filter_kind IS NULL AND host_filter_value IS NULL)"
            f" OR (host_filter_kind IN ({_FILTER_KINDS_SQL}) AND host_filter_value IS NOT NULL)",
            name="ck_presentations_host_filter",
        ),
        CheckConstraint(
            "(ended_at IS NULL AND ended_reason IS NULL)"
            " OR (ended_at IS NOT NULL AND ended_reason IN ('ended', 'idle'))",
            name="ck_presentations_ended",
        ),
        # One live presentation per report.
        Index(
            "uq_presentations_live_report", "report_id", unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_presentations_project_id", "project_id"),
    )


class PresentationViewer(Base):
    __tablename__ = "presentation_viewers"

    id = Column(Integer, primary_key=True)
    presentation_id = Column(Integer, ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False)
    # Presence is ephemeral: it goes with its person.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(String(CLIENT_ID_MAX), nullable=False)
    kind = Column(String(12), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # A Display's acknowledgment: what it last RENDERED (E1).
    rendered_revision = Column(Integer, nullable=True)
    rendered_slide_key = Column(String(SLIDE_KEY_MAX), nullable=True)
    rendered_host_filter_kind = Column(String(10), nullable=True)
    rendered_host_filter_value = Column(String(FILTER_VALUE_MAX), nullable=True)
    rendered_at = Column(DateTime(timezone=True), nullable=True)
    fitted = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("presentation_id", "client_id", name="uq_presentation_viewers_client"),
        CheckConstraint("kind IN ('display', 'controller', 'browse')", name="ck_presentation_viewers_kind"),
        Index("ix_presentation_viewers_user_id", "user_id"),
    )


class PresentationLayoutReport(Base):
    __tablename__ = "presentation_layout_reports"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    slide_key = Column(String(SLIDE_KEY_MAX), nullable=False)
    overflow_px = Column(Integer, nullable=False)
    font = Column(String(100), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_presentation_layout_reports_report_id", "report_id"),
    )
