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
    # v2.382.0 — the other target lists (Markdown, "if applicable").
    applications = Column(Text, nullable=True)
    thick_clients = Column(Text, nullable=True)
    other_targets = Column(Text, nullable=True)
    template = Column(String(100), nullable=True)      # report-templates/<name>
    # v2.495.0 — who produced the report, and the disclaimer it closes with.
    # NULL = the installation's (``report_defaults``).
    author_name = Column(String(255), nullable=True)
    disclaimer = Column(Text, nullable=True)           # Markdown
    # Revision c9e4a7b2d6f1 (owner, 2026-10-14) — the executive summary every
    # full report of the project prints unless it overrides it (Markdown).
    executive_summary = Column(Text, nullable=True)
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

    # The engagement details THIS report OVERRIDES (revision f3a9c1e7d5b2,
    # owner 2026-10-14): only those keys; a key absent follows the project's
    # profile (``services/report_defaults``).  A list key present as [] is
    # overridden to empty.
    # (The executive summary is one of them since revision c9e4a7b2d6f1 —
    # the project's, unless the report overrides it; ``reports.executive_summary``
    # is gone.)
    settings = Column(JSON, nullable=True)

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
    images = relationship(
        "ReportImage", back_populates="report", cascade="all, delete-orphan",
        order_by="ReportImage.attachment_id",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_report_project_number"),
        Index("idx_reports_project_status", "project_id", "status"),
    )


class ReportFile(Base):
    __tablename__ = "report_files"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
    format = Column(String(10), nullable=False)          # html | docx | qmd (pdf: issued before v2.407.0)
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


class ReportImage(Base):
    """An ISSUED report's own copy of an evidence image.

    Issuing copies the bytes of every image the report's data names into
    ``REPORT_FILES_DIR/<project>/<report>/evidence/`` and records each here,
    so the issued report renders from what was signed off whatever happens to
    the finding's attachment afterwards (it may be deleted, or un-ticked).

    ``attachment_id`` is the ``note_attachments.id`` the frozen dataset names
    (``evidence/<id>.png``) — deliberately NOT a foreign key: it is the key
    the snapshot is read by, and must survive the attachment's deletion.
    """
    __tablename__ = "report_images"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id = Column(Integer, nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    storage_path = Column(String, nullable=False)        # relative to REPORT_FILES_DIR
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    report = relationship("Report", back_populates="images")

    __table_args__ = (
        UniqueConstraint("report_id", "attachment_id", name="uq_report_image_attachment"),
    )


class ReportDefault(Base):
    """The installation's own report details — who produces its reports and
    the disclaimer they close with — one row per key, written by a global
    admin.  A project's report defaults override them, and a report its
    project's.  No row = not set.  ``services/report_defaults`` is the one
    reader and writer, and names the keys."""
    __tablename__ = "report_defaults"

    key = Column(String(40), primary_key=True)
    text = Column(Text, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    updated_by = relationship("User")


class ReportWritingGuidance(Base):
    """The installation's instructions for drafting a finding's report text:
    one row per key (``general`` and each section), written by a global admin.
    No row is the shipped default.  ``services/report_writing_guidance`` is the
    one reader and writer, and names the keys."""
    __tablename__ = "report_writing_guidance"

    key = Column(String(40), primary_key=True)
    text = Column(Text, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    updated_by = relationship("User")
