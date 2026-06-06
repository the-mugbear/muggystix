import re
from typing import List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from enum import Enum

_SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

class ScriptBase(BaseModel):
    script_id: str
    output: Optional[str] = None

class Script(ScriptBase):
    id: int
    port_id: int
    scan_id: int
    
    model_config = ConfigDict(from_attributes=True)

class HostScriptBase(BaseModel):
    script_id: str
    output: Optional[str] = None

class HostScript(HostScriptBase):
    id: int
    host_id: int
    scan_id: int
    
    model_config = ConfigDict(from_attributes=True)

class PortBase(BaseModel):
    port_number: int
    protocol: str
    state: Optional[str] = None
    reason: Optional[str] = None
    service_name: Optional[str] = None
    service_product: Optional[str] = None
    service_version: Optional[str] = None
    service_extrainfo: Optional[str] = None
    service_method: Optional[str] = None
    service_conf: Optional[int] = None

class Port(PortBase):
    id: int
    host_id: int
    last_updated_scan_id: Optional[int] = None
    scripts: List[Script] = []
    
    model_config = ConfigDict(from_attributes=True)

class HostBase(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    state: Optional[str] = None
    state_reason: Optional[str] = None
    os_name: Optional[str] = None
    os_family: Optional[str] = None
    os_generation: Optional[str] = None
    os_type: Optional[str] = None
    os_vendor: Optional[str] = None
    os_accuracy: Optional[int] = None


class FollowStatus(str, Enum):
    watching = "watching"
    in_review = "in_review"
    reviewed = "reviewed"


class NoteStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class HostFollowInfo(BaseModel):
    status: FollowStatus
    last_viewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class HostNoteBase(BaseModel):
    # Cap free-form text at 16 KB.  A realistic triage note is under
    # 1 KB; the ceiling exists so a copy-pasted pentest report or a
    # stuck client loop can't insert multi-megabyte rows into the DB.
    body: str = Field(..., max_length=16384)
    status: NoteStatus = NoteStatus.open


class HostNote(HostNoteBase):
    id: int
    author_id: Optional[int] = Field(None, validation_alias="user_id")
    author_name: Optional[str] = None
    parent_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Audit finding H3: optional warning populated by the
    # host-notes endpoint when the note itself was written
    # successfully but mention / status-change notifications
    # could not be delivered.  Frontend clients that don't
    # know about this field ignore it silently; clients that do
    # should display it as a non-blocking toast.  Null on the
    # happy path.
    mention_warning: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NoteActivityEntry(BaseModel):
    note_id: int
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    status: NoteStatus
    preview: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class ReviewProgress(BaseModel):
    total_hosts: int
    not_reviewed: int
    watching: int
    in_review: int
    reviewed: int

class NoteActivitySummary(BaseModel):
    total_notes: int
    active_host_count: int
    following_count: int
    review_progress: Optional[ReviewProgress] = None
    recent_notes: List[NoteActivityEntry] = []


class HostFollowUpdate(BaseModel):
    status: FollowStatus


class HostNoteCreate(HostNoteBase):
    status: NoteStatus = NoteStatus.open
    parent_id: Optional[int] = None


class HostNoteUpdate(BaseModel):
    body: Optional[str] = Field(None, max_length=16384)
    status: Optional[NoteStatus] = None

class HostVulnerabilitySummary(BaseModel):
    total_vulnerabilities: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class HostVulnerability(BaseModel):
    id: int
    plugin_id: Optional[str] = None
    title: Optional[str] = None
    severity: Optional[str] = None
    source: Optional[str] = None
    cvss_score: Optional[float] = None
    cvss_vector: Optional[str] = None
    cve_id: Optional[str] = None
    scan_id: Optional[int] = None
    port_id: Optional[int] = None
    port_number: Optional[int] = None
    protocol: Optional[str] = None
    service_name: Optional[str] = None
    exploitable: Optional[bool] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    solution: Optional[str] = None
    # v2.45.6 — previously stored but never returned to the client.
    description: Optional[str] = None
    references: List[str] = []
    source_plugin_name: Optional[str] = None

    @field_validator("references", mode="before")
    @classmethod
    def _normalize_references(cls, v):
        """Coerce the ORM's ``references`` value into a ``List[str]``.

        The DB column is ``Text`` (raw JSON), and rows pre-dating the
        references-rendering feature have ``NULL`` there.  Without this
        normaliser, FastAPI's response validation rejected every host
        with a NULL-references vuln on ``GET /hosts/scan/{id}`` with a
        500.  Accepts:

        * ``None`` / empty string → ``[]``  (NULL column, never written)
        * JSON-text string        → parsed list (or ``[]`` on parse fail)
        * already-a-list          → passed through
        """
        if v is None or v == "":
            return []
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return []
            return parsed if isinstance(parsed, list) else []
        if isinstance(v, list):
            return v
        return []

    model_config = ConfigDict(from_attributes=True)

class HostDiscovery(BaseModel):
    scan_id: int
    scan_filename: Optional[str] = None
    scan_type: Optional[str] = None
    tool_name: Optional[str] = None
    discovered_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class HostReviewer(BaseModel):
    """Another user (not the caller) who has this host In Review.
    v4.9.1 — drives the Hosts-list "In review · <name>" indicator."""
    user_id: int
    name: str


class HostTagInfo(BaseModel):
    """A project tag attached to a host (v2.71.0)."""
    id: int
    name: str
    color: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class HostAssignee(BaseModel):
    """A user this host is assigned to (v2.71.0)."""
    user_id: int
    name: str
    assigned_at: Optional[datetime] = None
    assigned_by_id: Optional[int] = None


class Host(HostBase):
    id: int
    last_updated_scan_id: Optional[int] = None
    ports: List[Port] = []
    host_scripts: List[HostScript] = []
    vulnerability_summary: Optional[HostVulnerabilitySummary] = None
    vulnerabilities: List[HostVulnerability] = []
    follow: Optional[HostFollowInfo] = None
    notes: List[HostNote] = []
    note_count: int = 0
    test_plan_entry_count: int = 0
    # v2.81.0 — count of TestExecutionResult rows recorded against this
    # host (joined via TestPlanEntry.host_id).  Surfaced on the Hosts
    # list so a row can render a "tested" left-border accent when an
    # agentic execution has actually run against the host (distinct
    # from `test_plan_entry_count`, which only means "host is in a
    # plan" — i.e. planned, not necessarily executed).
    test_execution_count: int = 0
    # v4.9.1 — other users who have this host In Review (caller
    # excluded; the caller's own follow is on `follow`).  Empty for the
    # host-detail endpoint, populated by the list endpoint's batch query.
    other_reviewers: List[HostReviewer] = []
    # v2.12.0: count of unique web interfaces (httpx / eyewitness /
    # nikto rows) observed on this host.  Used by HostDetail.tsx to
    # show/hide the "Web Interfaces" card and by the Hosts list (phase
    # 2) to render a "Web" badge.  Populated by the host-detail
    # endpoint; the list endpoint can populate via a batch query.
    web_interface_count: int = 0
    # v2.45.7: count of NetExec credentialed-enumeration rows observed
    # on this host.  Gates the HostInspector NetExec card.
    netexec_result_count: int = 0
    # v2.71.0 — project tags attached to this host (selectin-loaded) and
    # the users it's assigned to (populated by the list/detail endpoints).
    tags: List[HostTagInfo] = []
    assignees: List[HostAssignee] = []
    discoveries: List[HostDiscovery] = []

    model_config = ConfigDict(from_attributes=True)


class HostListResponse(BaseModel):
    items: List[Host] = []
    total: Optional[int] = 0
    skip: int = 0
    limit: int = 100
    sort_by: str = "critical_vulns"
    sort_order: str = "desc"
    vulnerability_error: bool = False

class ScanInfoBase(BaseModel):
    type: Optional[str] = None
    protocol: Optional[str] = None
    numservices: Optional[int] = None
    services: Optional[str] = None

class ScanInfo(ScanInfoBase):
    id: int
    scan_id: int
    
    model_config = ConfigDict(from_attributes=True)

class ScanBase(BaseModel):
    filename: str
    scan_type: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    command_line: Optional[str] = None
    version: Optional[str] = None
    xml_output_version: Optional[str] = None

class Scan(ScanBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    hosts: List[Host] = []
    scan_info: List[ScanInfo] = []
    # v2.74.0 — accurate per-scan aggregates, populated by get_scan from
    # HostScanHistory (matches the /scans list badge).  Default 0 so other
    # producers of this schema stay valid.
    total_hosts: int = 0
    up_hosts: int = 0
    total_ports: int = 0
    open_ports: int = 0

    model_config = ConfigDict(from_attributes=True)

class ScanPortBreakdown(BaseModel):
    unique_ports: int = 0
    open_tcp_ports: int = 0
    open_udp_ports: int = 0


class ScanVulnerabilitySummary(BaseModel):
    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class ScanSummary(BaseModel):
    id: int
    filename: str
    scan_type: Optional[str] = None
    tool_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: datetime
    total_hosts: int
    up_hosts: int
    total_ports: int
    open_ports: int
    command_line: Optional[str] = None
    version: Optional[str] = None
    port_breakdown: Optional[ScanPortBreakdown] = None
    vulnerability_summary: Optional[ScanVulnerabilitySummary] = None

    model_config = ConfigDict(from_attributes=True)

class SubnetStats(BaseModel):
    id: int
    cidr: str
    scope_name: str
    description: Optional[str] = None
    host_count: int
    total_addresses: Optional[int] = None
    usable_addresses: Optional[int] = None
    utilization_percentage: Optional[float] = None
    risk_level: Optional[str] = None
    network_address: Optional[str] = None
    is_private: Optional[bool] = None
    
    model_config = ConfigDict(from_attributes=True)

class VulnerabilityStats(BaseModel):
    total_vulnerabilities: int
    critical: int
    high: int
    medium: int
    low: int
    info: int
    hosts_with_vulnerabilities: int

class DashboardStats(BaseModel):
    total_scans: int
    total_hosts: int
    total_ports: int
    up_hosts: int
    open_ports: int
    total_subnets: int
    recent_scans: List[ScanSummary]
    subnet_stats: List[SubnetStats]
    vulnerability_stats: Optional[VulnerabilityStats] = None
    note_activity: Optional[NoteActivitySummary] = None


class PortOfInterestSummary(BaseModel):
    port: int
    protocol: str
    label: str
    category: str
    weight: int
    open_host_count: int
    rationale: str
    recommended_action: str


class PortOfInterestHostEntry(BaseModel):
    port: int
    protocol: str
    label: str
    service: str
    weight: int
    category: str


class HostRiskExposure(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    ports_of_interest: List[PortOfInterestHostEntry]
    critical: int
    high: int
    medium: int
    low: int
    risk_score: int
    port_score: int
    vulnerability_score: int


class VulnerabilityHotspot(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    critical: int
    high: int
    medium: int
    low: int
    risk_score: int


class PortsOfInterestInsights(BaseModel):
    summary: List[PortOfInterestSummary]
    top_hosts: List[HostRiskExposure]


class RiskInsightResponse(BaseModel):
    ports_of_interest: PortsOfInterestInsights
    vulnerability_hotspots: List[VulnerabilityHotspot]

class FileUploadResponse(BaseModel):
    job_id: int
    filename: str
    status: str
    message: str
    scan_id: Optional[int] = None
    parse_error_id: Optional[int] = None


class IngestionJobSchema(BaseModel):
    id: int
    filename: str
    original_filename: str
    status: str
    message: Optional[str] = None
    error_message: Optional[str] = None
    tool_name: Optional[str] = None
    file_size: Optional[int] = None
    scan_id: Optional[int] = None
    parse_error_id: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    progress: Optional[str] = None
    # Dead-letter columns — surfaced so the UI can render a
    # "previously failed N times" indicator alongside the job row.
    retry_count: Optional[int] = None
    last_error: Optional[str] = None
    # v2.86.2 — operator-set dismissal timestamp for failed jobs.
    # When non-null the job stops appearing in the live queue list
    # (filterable back in via ?include_dismissed=true).  The frontend
    # uses this to disable the Dismiss button on rows already acked.
    dismissed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class SubnetLabelInfo(BaseModel):
    """A project-scoped subnet label as it appears on a Subnet response (v2.86.0).

    Parallel to ``HostTagInfo``.  The label-definition CRUD and
    assignment routes live in ``api/v1/endpoints/subnet_labels.py``;
    this schema is the embedded view rendered alongside subnets.
    """
    id: int
    name: str
    color: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SubnetBase(BaseModel):
    cidr: str = Field(..., max_length=64)
    description: Optional[str] = Field(None, max_length=1024)

class Subnet(SubnetBase):
    id: int
    scope_id: int
    created_at: datetime
    # v2.86.0 — subnet labels attached to this row, selectin-loaded via
    # Subnet.label_assignments → SubnetLabelAssignment.label.  Empty list
    # if no labels (the default once the feature ships; never null).
    labels: List[SubnetLabelInfo] = []

    model_config = ConfigDict(from_attributes=True)


class SubnetLabelCreate(BaseModel):
    """Payload for ``POST /projects/{project_id}/scopes/subnet-labels``."""
    name: str = Field(..., min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=20)


class SubnetLabelUpdate(BaseModel):
    """Payload for ``PATCH /projects/{project_id}/scopes/subnet-labels/{label_id}``."""
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=20)


class SubnetLabel(BaseModel):
    """A project-scoped subnet label as returned by the label CRUD endpoints.

    ``subnet_count`` is the number of subnets currently carrying this
    label (assignment rows); ``host_count`` is the distinct number of
    hosts those subnets cover.  Both populated by the list endpoint so
    the management UI can show "in use" stats without a second call.
    """
    id: int
    project_id: int
    name: str
    color: Optional[str] = None
    created_at: datetime
    subnet_count: int = 0
    host_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class SubnetLabelBulkAssign(BaseModel):
    """Payload for ``PUT /projects/{project_id}/scopes/subnets/{subnet_id}/labels`` —
    replaces the subnet's full label set with ``label_ids`` (any not
    listed are detached; any missing are attached)."""
    label_ids: List[int] = Field(default_factory=list)


class SubnetLabelBulkAssignMany(BaseModel):
    """Payload for ``POST /projects/{project_id}/scopes/subnet-labels/{label_id}/subnets`` —
    bulk-apply one label across many subnets in one request."""
    subnet_ids: List[int] = Field(..., min_length=1, max_length=1000)

class ScopeBase(BaseModel):
    name: str = Field(..., max_length=256)
    description: Optional[str] = Field(None, max_length=2048)

class ScopeCreate(ScopeBase):
    pass


class ScopeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=2048)


class SubnetCreate(SubnetBase):
    pass


class SubnetUpdate(BaseModel):
    cidr: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=1024)


class SubnetBatchCreate(BaseModel):
    subnets: List[SubnetCreate] = Field(..., max_length=500)

class Scope(ScopeBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    subnets: List[Subnet] = []
    # v2.85.0 — paginate the subnets list.  ``subnets_total`` is the
    # full row count regardless of the slice the caller got back; null
    # ``subnets_skip`` / ``subnets_limit`` mean "the full list" (pre-
    # v2.85.0 default).  Frontend opts in to pagination by passing
    # subnets_limit and uses subnets_total to drive load-more on
    # 6000-subnet projects.
    subnets_total: int = 0
    subnets_skip: Optional[int] = None
    subnets_limit: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class ScopeSummary(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    subnet_count: int
    
    model_config = ConfigDict(from_attributes=True)


class ScopeCoverageHost(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_scan_id: Optional[int] = None
    last_scan_filename: Optional[str] = None


class TopTechnology(BaseModel):
    name: str
    host_count: int


class ScopeCoverageSummary(BaseModel):
    total_scopes: int
    total_subnets: int
    total_hosts: int
    scoped_hosts: int
    out_of_scope_hosts: int
    coverage_percentage: float
    has_scope_configuration: bool
    recent_out_of_scope_hosts: List[ScopeCoverageHost]
    # v2.12.1: top N technologies observed across scoped hosts (by
    # distinct host count).  Empty when no web_interfaces rows exist
    # yet.  Used by the Scopes page to give a quick read on what the
    # network is running at a glance.
    top_technologies: List[TopTechnology] = []

class HostSubnetMapping(BaseModel):
    id: int
    host_id: int
    subnet_id: int
    created_at: datetime
    subnet: Subnet
    
    model_config = ConfigDict(from_attributes=True)

class SubnetFileUploadResponse(BaseModel):
    message: str
    scope_id: int
    subnets_added: int
    filename: str

class EyewitnessResultBase(BaseModel):
    url: str
    protocol: Optional[str] = None
    port: Optional[int] = None
    ip_address: Optional[str] = None
    title: Optional[str] = None
    server_header: Optional[str] = None
    content_length: Optional[int] = None
    screenshot_path: Optional[str] = None
    response_code: Optional[int] = None
    page_text: Optional[str] = None

class EyewitnessResult(EyewitnessResultBase):
    id: int
    scan_id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class DNSRecordBase(BaseModel):
    domain: str
    record_type: str
    value: str
    ttl: Optional[int] = None

class DNSRecord(DNSRecordBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class OutOfScopeHostBase(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    ports: Optional[dict] = None
    tool_source: Optional[str] = None
    reason: Optional[str] = None

class OutOfScopeHost(OutOfScopeHostBase):
    id: int
    scan_id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ParseErrorBase(BaseModel):
    filename: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    error_type: str
    error_message: str
    error_details: Optional[dict] = None
    file_preview: Optional[str] = None
    user_message: Optional[str] = None
    status: Optional[str] = "unresolved"

class ParseErrorCreate(ParseErrorBase):
    pass

class ParseError(ParseErrorBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class ParseErrorSummary(BaseModel):
    id: int
    filename: str
    file_type: Optional[str] = None
    error_type: str
    user_message: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Proposed Test — shared by agent_api.py and test_plans.py
# ---------------------------------------------------------------------------

class ProposedTest(BaseModel):
    """Structured test specification. Agents should use this format."""
    tool: str
    description: str
    command: Optional[str] = None
    expected_result: Optional[str] = None
    references: Optional[List[str]] = None

    @field_validator("references", mode="before")
    @classmethod
    def _sanitize_references(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        safe = []
        for url in v:
            if isinstance(url, str) and _SAFE_URL_RE.match(url.strip()):
                safe.append(url.strip())
            # Silently drop non-http(s) URLs (javascript:, data:, etc.)
        return safe if safe else None


ProposedTestItem = Union[str, ProposedTest]
