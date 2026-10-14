"""A client report as the Reports page shows it: the loader and the serializer.

These lived in ``endpoints/client_reports.py`` as ``_load`` / ``_serialize``
until the 2026-10-01 review (B4): the agents' report reads
(``agent_assist_reporting.py``) imported the private helpers from the router
file.  One implementation, so an agent sees the report the page shows, field
for field (CLAUDE.md, "Agent parity with the pages").
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session, defer, selectinload

from app.core.security import check_permissions
from app.db.models_project import ProjectRole
from app.db.models_reports import Report, ReportStatus
from app.schemas.client_reports import EngagementSettings, ReportFileOut, ReportOut, ReportRef
from app.services import report_defaults
from app.services.client_report_service import ClientReportService


def role_allows(role: str, required: ProjectRole) -> bool:
    """Does the project role ``role`` ("" for none) reach ``required``?"""
    return bool(role) and check_permissions(role, required.value)


def _name(user) -> Optional[str]:
    return (user.full_name or user.username) if user is not None else None


def _ref(report: Optional[Report]) -> Optional[ReportRef]:
    if report is None:
        return None
    return ReportRef(
        id=report.id, number=report.number, title=report.title, status=report.status,
        issued_at=report.issued_at,
    )


def _details(
    db: Session, report: Report, is_draft: bool,
) -> Tuple[EngagementSettings, Optional[EngagementSettings], Optional[Dict[str, Optional[str]]]]:
    """``(effective, followed, followed_from)`` — see ``ReportOut``.  A draft:
    its overrides over what it follows, as the project stands
    (``report_defaults``'s order — one read of the defaults for both); an
    issued report: what it was ISSUED with (its frozen dataset — a detail a
    report issued before it existed is empty), and nothing followed."""
    if not is_draft:
        frozen = (((report.snapshot or {}).get("dataset") or {}).get("engagement")) or {}
        return EngagementSettings.model_validate(
            {key: frozen.get(key) for key in report_defaults.DETAIL_KEYS if frozen.get(key) is not None},
        ), None, None
    followed, sources = ClientReportService(db).followed_details_with_sources(report.project_id)
    effective = report_defaults.apply_overrides(followed, report.settings)
    return EngagementSettings.model_validate(effective), EngagementSettings.model_validate(followed), sources


def may_discard(report: Report, role: str, user_id: Optional[int]) -> bool:
    """Whether the caller may discard this report: a DRAFT, by the analyst
    who started it (or a draft nobody is recorded as starting) or a project
    admin — the one rule the DELETE route applies and ``can_discard`` says."""
    if report.status != ReportStatus.DRAFT or not role_allows(role, ProjectRole.ANALYST):
        return False
    return report.created_by_id is None or report.created_by_id == user_id or role_allows(role, ProjectRole.ADMIN)


def serialize_report(
    db: Session, report: Report, role: str, *, with_summary: bool, user_id: Optional[int] = None,
) -> ReportOut:
    superseded_by = None
    if report.status == ReportStatus.SUPERSEDED:
        superseded_by = (
            db.query(Report)
            .options(defer(Report.snapshot))
            .filter(Report.revision_of_id == report.id, Report.status != ReportStatus.DRAFT)
            .order_by(Report.number.desc())
            .first()
        )
    summary = ClientReportService(db).summary(report) if with_summary else None
    is_draft = report.status == ReportStatus.DRAFT
    # Only where the page shows the details (one report): a list row does not
    # pay for the reads.
    effective, followed, followed_from = _details(db, report, is_draft) if with_summary else (None, None, None)
    return ReportOut(
        id=report.id, project_id=report.project_id, kind=report.kind, status=report.status,
        title=report.title, number=report.number, template=report.template,
        baseline=_ref(report.baseline), revision_of=_ref(report.revision_of),
        superseded_by=_ref(superseded_by),
        settings=report_defaults.overrides(report.settings),
        executive_summary=report.executive_summary,
        template_fingerprint=report.template_fingerprint, quarto_version=report.quarto_version,
        render_status=report.render_status, render_error=report.render_error,
        files=[
            ReportFileOut(
                format=f.format, filename=f.filename, media_type=f.media_type,
                size_bytes=f.size_bytes, sha256=f.sha256, created_at=f.created_at,
            )
            for f in report.files
        ],
        created_by_name=_name(report.created_by), issued_by_name=_name(report.issued_by),
        created_at=report.created_at, updated_at=report.updated_at, issued_at=report.issued_at,
        summary=summary, effective=effective, followed=followed, followed_from=followed_from,
        can_edit=is_draft and role_allows(role, ProjectRole.ANALYST),
        can_issue=is_draft and role_allows(role, ProjectRole.ADMIN),
        # Only a person's read names the person (an agent has no discard).
        can_discard=user_id is not None and may_discard(report, role, user_id),
    )


def load_report(db: Session, project_id: int, report_id: int) -> Report:
    """The project's report with its files, or a 404 — another project's
    report is "not found", never "forbidden"."""
    report = (
        db.query(Report)
        .options(selectinload(Report.files))
        .filter(Report.id == report_id, Report.project_id == project_id)
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report
