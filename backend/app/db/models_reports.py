"""Client reports (v2.380.0) — the findings-first deliverable rendered by Quarto.

Three tables:

* ``report_profiles`` — one per project: the engagement details every new
  report starts from (client, classification, testers, distribution list,
  system description, template).
* ``reports`` — a report is a DRAFT until a project admin ISSUES it.  A draft
  reads the live findings every time it is previewed; issuing freezes what the
  report says into ``snapshot`` (the full dataset, plus the reported state that
  the next addendum is compared against) and assigns the project's next report
  number.  An issued report is never edited: a correction is a new report with
  ``revision_of_id`` set, and issuing it marks the original ``superseded``.
  ``kind='addendum'`` reports only what changed since ``baseline_report_id``.
* ``report_files`` — the rendered files of an ISSUED report, kept for as long
  as the report exists (draft previews are ordinary ``report_jobs`` artifacts
  and expire with them).

``settings`` and ``snapshot`` are JSON on purpose (column-vs-blob policy): they
are read whole by the builder and the renderer, never filtered or aggregated.
"""
from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.sql import func

from app.db.session import Base


class ReportKind:
    FULL = "full"
    ADDENDUM = "addendum"
    ALL = (FULL, ADDENDUM)


class ReportStatus:
    DRAFT = "draft"
    ISSUED = "issued"
    SUPERSEDED = "superseded"


class RenderStatus:
    """Rendering of an ISSUED report's files (drafts render on demand)."""
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class ReportProfile(Base):
    __tablename__ = "report_profiles"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    client_name = Column(String(255), nullable=True)
    classification = Column(String(100), nullable=True)
    engagement_type = Column(String(100), nullable=True)
    # [{user_id?, name, role, email}] — picked from members, editable.
    testers = Column(JSON, nullable=True)
    # [{name, email}]
    distribution = Column(JSON, nullable=True)
    system_description = Column(Text, nullable=True)   # Markdown
    template = Column(String(100), nullable=True)      # report-templates/<name>
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default=ReportKind.FULL)
    status = Column(String(20), nullable=False, default=ReportStatus.DRAFT)
    title = Column(String(255), nullable=False)
    # The project's report number, assigned when the report is issued.
    number = Column(Integer, nullable=True)
    template = Column(String(100), nullable=False)

    # Addendum: the issued report it is compared against.
    baseline_report_id = Column(Integer, ForeignKey("reports.id", ondelete="SET NULL"), nullable=True)
    # Revision: the issued report it corrects (superseded when this is issued).
    revision_of_id = Column(Integer, ForeignKey("reports.id", ondelete="SET NULL"), nullable=True)

    # Engagement details for THIS report (copied from the profile when the
    # draft is created, then edited here): client_name, classification,
    # engagement_type, testers, distribution, system_description.
    settings = Column(JSON, nullable=True)
    executive_summary = Column(Text, nullable=True)   # Markdown, per report

    # Frozen at issue: {"dataset": {...}, "reported": {...}} (see
    # client_report_service).  NULL while a draft.
    # Deferred: it holds every finding's text, and lists / baseline lookups
    # never need it.
    snapshot = deferred(Column(JSON, nullable=True))
    template_fingerprint = Column(String(64), nullable=True)
    quarto_version = Column(String(40), nullable=True)
    render_status = Column(String(20), nullable=True)
    render_error = Column(Text, nullable=True)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issued_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    issued_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
    issued_by = relationship("User", foreign_keys=[issued_by_id])
    baseline = relationship("Report", foreign_keys=[baseline_report_id], remote_side=[id])
    revision_of = relationship("Report", foreign_keys=[revision_of_id], remote_side=[id])
    files = relationship(
        "ReportFile", back_populates="report", cascade="all, delete-orphan",
        order_by="ReportFile.format",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_report_project_number"),
        Index("idx_reports_project_status", "project_id", "status"),
    )


class ReportFile(Base):
    __tablename__ = "report_files"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
    format = Column(String(10), nullable=False)          # html | docx | pdf
    filename = Column(String(255), nullable=False)
    media_type = Column(String(100), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    storage_path = Column(String, nullable=False)        # relative to REPORT_FILES_DIR
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    report = relationship("Report", back_populates="files")

    __table_args__ = (
        UniqueConstraint("report_id", "format", name="uq_report_file_format"),
    )
