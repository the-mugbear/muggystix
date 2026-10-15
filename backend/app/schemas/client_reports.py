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
    """Engagement details — the project's defaults, or every value a report
    prints (`ReportOut.effective` / `followed`)."""
    # Owner, 2026-10-14 — the executive summary is the project's (Markdown):
    # a full report follows it unless it overrides it; an addendum's (its
    # "Summary of changes") is always its own.
    executive_summary: Optional[str] = Field(None, max_length=65536)
    client_name: Optional[str] = Field(None, max_length=255)
    classification: Optional[str] = Field(None, max_length=100)
    engagement_type: Optional[str] = Field(None, max_length=100)
    # No cap here: a project with no saved team prints every analyst and
    # admin, and more than 50 of them made every read of a draft a 500 (review
    # 2026-10-14, nitpick).  What a person SENDS is capped (`EngagementOverrides`).
    testers: List[Tester] = Field(default_factory=list)
    distribution: List[Recipient] = Field(default_factory=list, max_length=100)
    system_description: Optional[str] = Field(None, max_length=32768)
    # v2.382.0 — the template's other target lists ("if applicable"; Markdown).
    # Networks and domains come from the project's scope.
    applications: Optional[str] = Field(None, max_length=32768)
    thick_clients: Optional[str] = Field(None, max_length=32768)
    other_targets: Optional[str] = Field(None, max_length=32768)
    # v2.495.0 — who produced the report, and the disclaimer it closes with
    # (Markdown).  In the project's defaults, empty = the installation's
    # (`services/report_defaults`).
    author_name: Optional[str] = Field(None, max_length=255)
    disclaimer: Optional[str] = Field(None, max_length=32768)

    @field_validator(
        "executive_summary", "client_name", "classification", "engagement_type", "system_description",
        "applications", "thick_clients", "other_targets", "author_name", "disclaimer",
    )
    @classmethod
    def _blank_is_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


class EngagementOverrides(EngagementSettings):
    """A draft's OVERRIDES (`PATCH /client-reports/{id}` → `settings`): the
    details changed, each with its new value.  A key left out keeps what is
    stored; a text sent blank or null, or a list sent null, follows the
    project again; a list sent (even `[]`) is overridden as a whole.  A
    detail sent equal to what the draft follows follows it again.
    Read with `model_dump(exclude_unset=True)`: the defaults above would
    otherwise make every key look sent."""
    testers: Optional[List[Tester]] = Field(None, max_length=50)
    distribution: Optional[List[Recipient]] = Field(None, max_length=100)


class ReportProfilePatch(EngagementOverrides):
    """`PATCH /client-reports/profile`: only the keys SENT change (read with
    `exclude_unset`) — so a page that edits one detail
    never sends, and so never overwrites, the rest someone else may have
    changed meanwhile.  A text sent blank or null clears that default; a list
    sent null is an empty list."""
    template: Optional[str] = Field(None, max_length=100)


class ReportPromote(BaseModel):
    """`POST /client-reports/{id}/promote`: details of this draft to make the
    project's defaults.  `values` — the keys to promote, each with the value
    the page shows (the override, as edited); every key sent is written to the
    project's report defaults and removed from the draft's overrides, in one
    transaction, so the draft follows the project again.  A text sent blank is
    refused (there would be nothing to make the default); a list may be
    empty (not the team).

    `expected` — for every key in `values`, what the page showed the draft
    printing for it when it was read (the saved override, or the value it
    followed); a draft that no longer prints that — someone saved another
    value meanwhile — is a 409 and nothing changes (second review
    2026-10-14, H1: a stale page's push-up reverted another person's
    override and spread the old value to every following draft)."""
    values: EngagementOverrides
    expected: EngagementOverrides


class ReportProfileOut(EngagementSettings):
    template: Optional[str] = Field(None, max_length=100)
    updated_at: Optional[datetime] = None
    # v2.382.0 — no team is saved, so `testers` is the project's analysts and
    # admins: what a new draft will list.
    testers_from_project: bool = False
    # What a report of this project uses for an inherited detail the profile
    # leaves empty: the installation's (`author_name`, `disclaimer`).
    inherited: Dict[str, Optional[str]] = Field(default_factory=dict)


class ReportTextImageOut(BaseModel):
    """An image of the project's report text (the system description)."""
    id: int
    filename: str
    caption: Optional[str] = None
    content_type: str
    size_bytes: int
    uploaded_by_id: Optional[int] = None
    created_at: Optional[datetime] = None
    # Where the project's CURRENT text places it: "defaults" (the project's
    # report defaults) and/or "report <id>" for each draft.
    placed_in: List[str] = []
    can_modify: bool = False


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
    # present = installed on the server OR uploaded (v2.431.0).
    present: bool = False
    installed: bool = False
    # "uploaded" (wins), "installed" (the server's template folder), or None.
    source: Optional[str] = None
    # What an upload must be: kind (png, jpeg, docx; svg/gif/webp are server
    # only), and the template's size / shape guidance.
    kind: str = ""
    uploadable: bool = False
    max_bytes: Optional[int] = None
    min_width: Optional[int] = None
    min_height: Optional[int] = None
    aspect: Optional[str] = None
    upload: Optional["ReportTemplateAssetUploadOut"] = None


class ReportTemplateAssetUploadOut(BaseModel):
    """The current upload of an asset: what it is and who put it there."""
    kind: Optional[str] = None
    size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    sha256: Optional[str] = None
    original_filename: Optional[str] = None
    uploaded_at: Optional[str] = None
    uploaded_by: Optional[str] = None


class ReportTemplateOut(BaseModel):
    name: str
    title: str
    description: str
    formats: List[str]
    assets: List[ReportTemplateAssetOut] = []
    # Whether this template prints how each finding was confirmed.
    evidence_records: bool = False
    # What the template declares it prints of a finding's images
    # (`template.json` → `images`): `{"fields": [...], "trailing": bool}`.
    images: Optional[Dict[str, Any]] = None
    # `client` (a client report's template) or `contact` (the remediation
    # list prepared for one contact — never chosen for a client report).
    kind: str = "client"


ReportTemplateAssetOut.model_rebuild()


class ReportTemplateAssetChangeOut(BaseModel):
    """The template after an upload or removal, and what to know about it."""
    template: ReportTemplateOut
    warnings: List[str] = []


class ReportTemplateProblemOut(BaseModel):
    """A folder under report-templates/ that is not offered, and why."""
    name: str
    error: str


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
    # The details this report OVERRIDES — only those keys, as stored
    # (`report_defaults.overrides`): a text key with text, a list key present
    # (`[]` = overridden to empty).  A key absent follows the project.
    settings: Dict[str, Any] = Field(default_factory=dict)
    # The summary it prints (`effective.executive_summary`; a single read
    # only, None on list rows): a draft's override, else the project's (a
    # full report); an issued report's as issued.
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
    # A single read only (None on list rows).  `effective`: every detail the
    # report prints — a draft's overrides over what it follows, as the
    # project stands now; an issued report's as it was issued (its frozen
    # dataset).  `followed`: a DRAFT's value for each detail were it not
    # overridden (the project's default, else the installation's author /
    # disclaimer, else the project's analysts and admins as the team) — what
    # "Revert to the project's" returns to; None for an issued report.
    effective: Optional[EngagementSettings] = None
    followed: Optional[EngagementSettings] = None
    # A single DRAFT read: where each `followed` value comes from —
    # `project` (the project's report defaults), `installation` (author,
    # disclaimer: the installation's), `project_team` (the team: the
    # project's analysts and admins), or null (set nowhere).  None for an
    # issued report and for list rows.
    followed_from: Optional[Dict[str, Optional[str]]] = None
    # What the CALLER may do.
    can_edit: bool = False
    can_issue: bool = False
    # Discard this draft: its creator or a project admin (the DELETE route's
    # rule), on list rows too.
    can_discard: bool = False


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
    # The details changed in this save, each with its new override (the
    # executive summary is one of its keys); see `EngagementOverrides`.  A
    # detail not sent keeps what is stored.
    settings: Optional[EngagementOverrides] = None


class PreviewRequest(BaseModel):
    format: Literal["html", "docx", "qmd"] = "html"


class ReportInputsOut(BaseModel):
    """A draft's inputs as a preview would render them now; compared with a
    preview job's `inputs_fingerprint`.  Null when the draft cannot be built."""
    fingerprint: Optional[str] = None
