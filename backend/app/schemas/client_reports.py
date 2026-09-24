"""Schemas for client reports (v2.380.0)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Tester(BaseModel):
    user_id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=200)
    role: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = Field(None, max_length=254)


class Recipient(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = Field(None, max_length=254)


class EngagementSettings(BaseModel):
    """Engagement details — the profile's defaults, or one report's copy."""
    client_name: Optional[str] = Field(None, max_length=255)
    classification: Optional[str] = Field(None, max_length=100)
    engagement_type: Optional[str] = Field(None, max_length=100)
    testers: List[Tester] = Field(default_factory=list, max_length=50)
    distribution: List[Recipient] = Field(default_factory=list, max_length=100)
    system_description: Optional[str] = Field(None, max_length=32768)
    # v2.382.0 — the template's other target lists ("if applicable"; Markdown).
    # Networks and domains come from the project's scope.
    applications: Optional[str] = Field(None, max_length=32768)
    thick_clients: Optional[str] = Field(None, max_length=32768)
    other_targets: Optional[str] = Field(None, max_length=32768)

    @field_validator(
        "client_name", "classification", "engagement_type", "system_description",
        "applications", "thick_clients", "other_targets",
    )
    @classmethod
    def _blank_is_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


class ReportProfileBody(EngagementSettings):
    template: Optional[str] = Field(None, max_length=100)


class ReportProfileOut(ReportProfileBody):
    updated_at: Optional[datetime] = None
    # v2.382.0 — no team is saved, so `testers` is the project's analysts and
    # admins: what a new draft will list.
    testers_from_project: bool = False


class ReportTemplateAssetOut(BaseModel):
    """An image the template itself expects (logo, cover art), from its
    template.json — never finding evidence.  ``path`` is relative to
    ``report-templates/<name>/``."""
    id: str
    path: str
    label: str
    description: str = ""
    note: str = ""
    required: bool = False
    formats: List[str] = []
    # When installed, used in place of this template file (e.g. reference.docx).
    replaces: Optional[str] = None
    present: bool = False


class ReportTemplateOut(BaseModel):
    name: str
    title: str
    description: str
    formats: List[str]
    assets: List[ReportTemplateAssetOut] = []


class ReportFileOut(BaseModel):
    format: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: Optional[datetime] = None


class ReportRef(BaseModel):
    id: int
    number: Optional[int] = None
    title: str
    status: str
    issued_at: Optional[datetime] = None


class ReportOut(BaseModel):
    id: int
    project_id: int
    kind: str
    status: str
    title: str
    number: Optional[int] = None
    template: str
    baseline: Optional[ReportRef] = None
    revision_of: Optional[ReportRef] = None
    superseded_by: Optional[ReportRef] = None
    settings: EngagementSettings
    executive_summary: Optional[str] = None
    template_fingerprint: Optional[str] = None
    quarto_version: Optional[str] = None
    render_status: Optional[str] = None
    render_error: Optional[str] = None
    files: List[ReportFileOut] = []
    created_by_name: Optional[str] = None
    issued_by_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    # What the report contains (live for a draft, frozen for an issued one).
    summary: Optional[Dict[str, Any]] = None
    # What the CALLER may do.
    can_edit: bool = False
    can_issue: bool = False


class ReportListOut(BaseModel):
    items: List[ReportOut]
    # The report an addendum is compared against by default.
    latest_issued_id: Optional[int] = None
    can_create: bool = False
    can_issue: bool = False


class ReportCreate(BaseModel):
    kind: Literal["full", "addendum"] = "full"
    title: Optional[str] = Field(None, max_length=255)
    template: Optional[str] = Field(None, max_length=100)
    # Addendum only; defaults to the latest issued report.
    baseline_report_id: Optional[int] = None


class ReportUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    template: Optional[str] = Field(None, max_length=100)
    baseline_report_id: Optional[int] = None
    executive_summary: Optional[str] = Field(None, max_length=65536)
    settings: Optional[EngagementSettings] = None


class PreviewRequest(BaseModel):
    format: Literal["html", "docx", "qmd"] = "html"
