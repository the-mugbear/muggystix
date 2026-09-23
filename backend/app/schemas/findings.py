"""Pydantic schemas for the Finding spine (foundation phase 5).

In its own module rather than the already-large schemas.py.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FindingHostInfo(BaseModel):
    # v2.325.0 — the affected-endpoint ROW id.  A host may carry several rows
    # (one per named endpoint); detach/restore address this id, not host_id.
    id: int
    host_id: int
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    # v2.323.0 — the named endpoint on this host the finding applies to
    # (inherited from the scanner row / plan entry); null = host-level.
    name_id: Optional[int] = None
    fqdn: Optional[str] = None
    host_status: str
    model_config = ConfigDict(from_attributes=True)


class FindingReportText(BaseModel):
    """v2.379.0 — what the client report says about the finding (Markdown).
    Returned on single-finding responses only; the list leaves it out."""
    description: Optional[str] = None
    impact: Optional[str] = None
    recommendation: Optional[str] = None
    references: Optional[str] = None
    steps_to_reproduce: Optional[str] = None
    cvss_vector: Optional[str] = None
    cvss_score: Optional[float] = None
    # True when the vector decides the score (3.x / 2.0) — the editor then
    # shows the score read-only.
    cvss_score_from_vector: bool = False


class FindingResponse(BaseModel):
    id: int
    project_id: int
    title: str
    severity: str
    status: str
    source: str
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    evidence_annotation_id: Optional[int] = None
    vuln_id: Optional[int] = None
    exec_result_id: Optional[int] = None
    host_count: int = 0
    hosts: List[FindingHostInfo] = []
    # v2.349.0 — {open: n, remediated: n, retest: n} over the endpoint rows.
    # The finding's ``status`` is the issue's; this says how each endpoint
    # stands, so confirmation on one is never read as confirmation on all.
    endpoint_status_counts: Dict[str, int] = {}
    # v2.375.0 — who recorded the finding (the promoter / creator), and whether
    # the CALLER may rename or delete it: its author, a project admin, or a
    # global admin.  Severity, owner and status stay open to any analyst — they
    # are triage, not authored content.
    created_by_id: Optional[int] = None
    created_by_name: Optional[str] = None
    can_modify: bool = False
    # v2.379.0 — the caller is a project (or global) admin: may mark ANY
    # evidence image for the report, not only their own uploads.
    viewer_is_project_admin: bool = False
    report_text: Optional[FindingReportText] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class EndpointStatusUpdate(BaseModel):
    """Body for PATCH /findings/{id}/endpoints/{finding_host_id}."""
    host_status: str


class FindingListResponse(BaseModel):
    items: List[FindingResponse]
    total: int
    # Per-severity counts for the rollup header (respects all filters except
    # severity; independent of pagination). Keys: critical/high/medium/low/info.
    severity_counts: dict = {}


class FindingStatusHistoryEntry(BaseModel):
    id: int
    from_status: Optional[str] = None
    to_status: str
    changed_by_id: Optional[int] = None
    changed_by_name: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime


class PromoteAnnotationRequest(BaseModel):
    # Severity is required — promotion is the deliberate structuring step.
    severity: str
    title: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = None  # defaults to 'confirmed' server-side
    owner_id: Optional[int] = None
    # Additional hosts this single finding also affects (cross-host dedup).
    extra_host_ids: List[int] = []


class PromoteVulnerabilityRequest(BaseModel):
    vuln_id: int
    # Severity defaults to the vulnerability's own severity server-side.
    severity: Optional[str] = None
    # Defaults to 'confirmed'; pass a terminal status (false_positive /
    # accepted_risk) to dismiss the vuln as a finding instead.
    status: Optional[str] = None
    owner_id: Optional[int] = None
    # Optional triage rationale — recorded on the finding's disposition
    # history (esp. for a false-positive/accepted-risk dismissal).
    summary: Optional[str] = None
    # v2.360.0 — how far a FALSE-POSITIVE dismissal reaches.  ``host`` (the
    # default for ``status='false_positive'``): this host's endpoint only —
    # the judgment was made in one host's inspector, about that host's
    # observation.  ``issue``: the whole issue, every host that carries it
    # (what every dismissal used to do).  Promotion and accepted-risk are
    # about the issue and take ``issue`` only.
    scope: Optional[Literal["host", "issue"]] = None


class PromoteVulnerabilityPreview(BaseModel):
    """Blast radius of a vuln promotion (read-only; §11).

    Every count here must match what ``promote_vulnerability`` actually does —
    both now share ``_issue_host_ids``.  A confirmation dialog that misstates
    the action is worse than no dialog at all.
    """
    plugin_id: Optional[str] = None
    # Scanner-agnostic issue identity the fan-out keys on. Surfaced so the
    # dialog can explain *why* hosts from a different scanner are included.
    issue_key: Optional[str] = None
    affected_host_count: int
    affected_host_sample: List[str] = []
    # Hosts not already attached to the existing finding — what this action
    # would actually change. Equals affected_host_count for a fresh promote.
    new_host_count: int = 0
    already_promoted: bool = False
    finding_id: Optional[int] = None
    finding_status: Optional[str] = None
    # v2.360.0 — the inspected host, for the "this host only" choice: its
    # address, and its endpoint state on the existing finding (null when the
    # finding does not include this host, or there is no finding yet).
    host_ip: Optional[str] = None
    host_endpoint_status: Optional[str] = None


class FindingCreateRequest(BaseModel):
    title: str = Field(..., max_length=500)
    severity: str
    status: Optional[str] = None
    owner_id: Optional[int] = None
    host_ids: List[int] = []


class FindingUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    severity: Optional[str] = None
    owner_id: Optional[int] = None
    # v2.379.0 — report text (authored content: author / project admin /
    # global admin).  Send a field to set it, ``null`` or "" to clear it;
    # omitted fields are untouched.
    description: Optional[str] = Field(None, max_length=32768)
    impact: Optional[str] = Field(None, max_length=32768)
    recommendation: Optional[str] = Field(None, max_length=32768)
    references: Optional[str] = Field(None, max_length=32768)
    steps_to_reproduce: Optional[str] = Field(None, max_length=32768)
    cvss_vector: Optional[str] = Field(None, max_length=200)
    cvss_score: Optional[float] = None


class FindingNoteUpdate(BaseModel):
    """Body for PATCH /findings/{id}/notes/{note_id} — the author's own text."""
    body: str = Field(..., min_length=1, max_length=16384)


class FindingStatusUpdateRequest(BaseModel):
    status: str
    summary: Optional[str] = None


class FindingEndpointRef(BaseModel):
    """One affected endpoint to (re)attach: a host, optionally AS a named
    endpoint, optionally with the per-endpoint status to restore (Undo)."""
    host_id: int
    name_id: Optional[int] = None
    host_status: Optional[str] = None


class FindingHostsRequest(BaseModel):
    # Plain hosts (unnamed, host-level associations) …
    host_ids: List[int] = []
    # … and/or explicit endpoints (v2.325.0).  Either may be empty.
    endpoints: List[FindingEndpointRef] = []
