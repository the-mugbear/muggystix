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

Stages B and C (2.531.0):

* ``presentation_agendas`` / ``presentation_agenda_versions`` — an ordered
  selection of the report's findings and sections (E3).  Items are ANCHORS
  (``overview``, ``summary``, ``scope``, ``finding:<ref>``,
  ``section:<ref>:<section>``) that the slide builder tags its slides with,
  never page keys, so pagination and the host filter cannot invalidate one.
  Every save is a new immutable version; a running presentation is pinned to
  one (the agenda columns on ``presentations``, part of the position).
* The spotlight (E6) — a point the driver clicked, on ``presentations`` with
  its own ordering (``spotlight_seq``); it never raises ``revision``.
"""
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text,
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
#: Following the agenda, or exploring the full report (the item index frozen).
AGENDA_MODES = ("agenda", "explore")

SLIDE_KEY_MAX = 200
FILTER_VALUE_MAX = 200
CLIENT_ID_MAX = 64
AGENDA_NAME_MAX = 200
#: The most items an agenda holds.
AGENDA_ITEMS_MAX = 200

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

    # The agenda state (E3) — part of the position, written only by navigation
    # writes: the pinned version, following it or exploring, and the ACTIVE
    # item (a slide can belong to two items, so the key cannot say which).
    agenda_id = Column(Integer, ForeignKey("presentation_agendas.id", name="fk_presentations_agenda_id"),
                       nullable=True)
    agenda_version = Column(Integer, nullable=True)
    agenda_mode = Column(String(10), nullable=True)
    agenda_item_index = Column(Integer, nullable=True)

    # The spotlight (E6): its own ordering; never raises ``revision``.
    spotlight_seq = Column(Integer, nullable=False, default=0, server_default=text("0"))
    spotlight_revision = Column(Integer, nullable=True)
    spotlight_x = Column(Integer, nullable=True)
    spotlight_y = Column(Integer, nullable=True)
    spotlight_at = Column(DateTime(timezone=True), nullable=True)

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
        CheckConstraint(
            "(agenda_id IS NULL AND agenda_version IS NULL AND agenda_mode IS NULL AND agenda_item_index IS NULL)"
            " OR (agenda_id IS NOT NULL AND agenda_version IS NOT NULL AND agenda_mode IN ('agenda', 'explore')"
            " AND agenda_item_index >= 0)",
            name="ck_presentations_agenda",
        ),
        Index("ix_presentations_agenda_id", "agenda_id"),
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


class PresentationAgenda(Base):
    __tablename__ = "presentation_agendas"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(AGENDA_NAME_MAX), nullable=False)
    # The newest of its versions; a save makes the next one.
    latest_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    created_by = relationship("User")

    __table_args__ = (
        Index("ix_presentation_agendas_report_id", "report_id"),
        Index("ix_presentation_agendas_project_id", "project_id"),
    )


class PresentationAgendaVersion(Base):
    """One save of an agenda — never changed afterwards."""
    __tablename__ = "presentation_agenda_versions"

    id = Column(Integer, primary_key=True)
    agenda_id = Column(Integer, ForeignKey("presentation_agendas.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    # The anchors, in order (``["overview", "finding:F-07", "section:F-07:recommendation", …]``).
    items = Column(JSON, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agenda_id", "version", name="uq_presentation_agenda_versions_version"),
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
