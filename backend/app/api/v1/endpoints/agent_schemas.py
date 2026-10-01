"""
Agent API — Pydantic schemas.

All request/response models for the agent-facing endpoints.  Split out
of agent_api.py so the route modules (agent_browse / agent_recon /
agent_assist…) can share a single schema definition.  The test-plan and
execution schemas went with those routes in v2.442.0: a test is a host test
(``app/schemas/host_test_schemas.py``), its results are evidence records.
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.scan_time import scan_time_for_api


# ---------------------------------------------------------------------------
# Schemas — data reads
# ---------------------------------------------------------------------------

class PortBrief(BaseModel):
    id: int
    port_number: int
    protocol: str
    state: Optional[str] = None
    service_name: Optional[str] = None
    service_product: Optional[str] = None
    service_version: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class VulnCounts(BaseModel):
    """Per-severity vulnerability counts for a host.

    Counts only — not proof of exploitability.  Some scans report
    informational findings as `low` that the agent should weight far
    below true criticals; treat the breakdown as a triage hint, not a
    risk score.
    """
    critical: int = Field(0, description="Count of critical-severity vulnerabilities.")
    high: int = Field(0, description="Count of high-severity vulnerabilities.")
    medium: int = Field(0, description="Count of medium-severity vulnerabilities.")
    low: int = Field(0, description="Count of low/info-severity vulnerabilities.")


class HostBrief(BaseModel):
    """Lightweight host summary for list views.  See `HostDetail` for ports."""
    id: int = Field(..., description="Internal host id; use for /agent/hosts/{host_id} drill-down.")
    ip_address: str = Field(..., description="Primary IPv4/IPv6 address.")
    hostname: Optional[str] = Field(None, description="Reverse DNS or scan-derived hostname; null when none was discovered.")
    state: Optional[str] = Field(None, description="'up' or 'down' (lowercase normalised) — null when no scan reported a state.")
    os_name: Optional[str] = Field(None, description="OS as detected (specific name/version).")
    os_family: Optional[str] = Field(None, description="OS family bucket (e.g. 'Linux', 'Windows', 'BSD').")
    first_seen: Optional[datetime] = Field(None, description="Timestamp of the first scan to record this host.")
    last_seen: Optional[datetime] = Field(None, description="Timestamp of the most recent scan to see this host.")
    open_port_count: int = Field(0, description="Distinct open-port count across all scans of this host.")
    vuln_summary: Optional[VulnCounts] = Field(None, description="Per-severity vuln counts; null when no vulnerability scan has run.")
    # v2.429.1 (MCP acceptance run 2) — the Hosts page's Attention counts.
    exploitable_count: int = Field(0, description="Scanner vulnerabilities on this host flagged exploitable (any severity).")
    critical_exploitable_count: int = Field(0, description=(
        "CRITICAL vulnerabilities that are themselves flagged exploitable — severity and exploit "
        "on the same row (has:critical_exploit). Not critical count × exploit count."))
    # Agent feedback (v1.44.0): to avoid clobbering a human's review state on a
    # follow write, the agent had to run three DSL queries per host. This is the
    # SESSION OPERATOR's follow status on the host — 'watching' / 'in_review' /
    # 'reviewed', or null when they don't follow it (equivalent to follow:none).
    follow: Optional[str] = Field(None, description="The assist session operator's follow status on this host (watching/in_review/reviewed), or null if they don't follow it. Check before writing follow state so you don't overwrite a human review.")

    model_config = ConfigDict(from_attributes=True)


class HostBriefPage(BaseModel):
    """One page of ``GET /agent/assist/hosts`` (v2.440.0).  It was a bare
    list, and an agent asked "how many hosts expose VNC?" counted the rows of
    a 500-row page (diag 4).  ``total`` is every matching host, so the length
    of ``items`` is never mistaken for the answer."""
    items: List[HostBrief]
    total: int = Field(..., description="Every host matching the filters — the answer to 'how many'.")
    has_more: bool = Field(..., description="True when hosts remain past this page (raise offset by limit).")
    limit: int
    offset: int


class HostDetail(HostBrief):
    ports: List[PortBrief] = Field(default_factory=list)
    # v2.323.0 — every name observed at this address (current A/AAAA, HTTP,
    # certificate, scanner, PTR evidence), most-recently-seen first.  The
    # `hostname` field is the ONE display name; a load balancer carries many.
    # Use one of these as an entry's `target_fqdn`.
    names: List[str] = Field(default_factory=list, description=(
        "Names observed at this address (evidence-backed). Valid values for a plan entry's target_fqdn."
    ))


class AssistFinding(BaseModel):
    """One vulnerability/finding on a host, for evidence-rich assist reporting.
    Added on agent feedback (v1.45.0): assist previously exposed only severity
    counts, so a report could cite numbers but not the actual CVEs/evidence."""
    id: int = Field(..., description="Vulnerability row id.")
    severity: str = Field(..., description="critical / high / medium / low / info (lowercase).")
    title: str = Field(..., description="Finding title / plugin name.")
    cve_id: Optional[str] = Field(None, description="CVE id when the finding carries one.")
    plugin_id: Optional[str] = Field(None, description="Scanner plugin id (e.g. Nessus).")
    cvss_score: Optional[float] = Field(None, description="CVSS base score 0–10, when scored.")
    source: str = Field(..., description="Scanner that reported it (nessus / openvas / …).")
    exploitable: bool = Field(False, description="Flagged exploitable by the scanner.")
    port_number: Optional[int] = Field(None, description="Affected port; null for host-level findings.")
    service_name: Optional[str] = Field(None, description="Service on the affected port, when known.")
    description: Optional[str] = Field(None, description="Finding description (may be truncated).")
    solution: Optional[str] = Field(None, description="Remediation guidance, when the scanner provided it.")
    evidence: Optional[str] = Field(None, description="Scanner plugin output / evidence (may be truncated).")
    check_id: Optional[str] = Field(None, description=(
        "The misconfiguration-catalog check this row is (e.g. vnc_no_auth, "
        "smb_signing_not_required), whichever tool reported it; null for a "
        "scanner's own finding (v2.418.0)."
    ))


class AssistFindingsResponse(BaseModel):
    """Paginated host findings. total/has_more let an agent report complete
    coverage without guessing (unlike the bare host arrays)."""
    host_id: int
    total: int
    has_more: bool
    findings: List[AssistFinding] = Field(default_factory=list)


class ScanBrief(BaseModel):
    id: int
    filename: str
    scan_type: Optional[str] = None
    tool_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # v2.428.5 — the import that produced this scan: the job_id that
    # assist_list_uninterpreted_lines and the ingestion reads take (MCP
    # acceptance feedback #18 — a scan id is NOT a job id).  None for a scan
    # with no recorded import (seeded, or its job was cleaned up).
    ingestion_job_id: Optional[int] = None
    # v2.434.1 (acceptance run H4) — what start/end MEAN: ``tool_clock`` is
    # the scanner's zone-less wall clock (returned without an offset, never
    # convert it); anything else is an instant (returned in UTC with an
    # offset).  Both came out as bare "2024-04-01T00:00:00" before, so an
    # agent could not tell an instant from a local clock reading.
    time_source: Optional[str] = Field(
        None,
        description=(
            "tool_run / tool_records: start/end are instants (UTC). tool_clock: the "
            "scanner's local wall clock, zone unknown — do not treat as UTC. Null: "
            "the output carried no run time."
        ),
    )

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _time_basis(self) -> "ScanBrief":
        # The one rule every surface uses (services/scan_time.py).
        self.start_time = scan_time_for_api(self.start_time, self.time_source)
        self.end_time = scan_time_for_api(self.end_time, self.time_source)
        return self


class ScopeDomainBrief(BaseModel):
    """One declared domain-scope entry (v2.330.0).  ``include_subdomains``
    False = exactly this name; True = this name and every name under it."""
    domain: str
    include_subdomains: bool = False


class ScopeBrief(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    subnets: List[str] = Field(default_factory=list)
    # ``subnets`` is capped (assist context budget); these let the agent tell a
    # truncated CIDR list from a complete one. ``subnet_total`` is the true
    # count; ``subnets_truncated`` is True when the list was clipped.
    subnet_total: int = 0
    subnets_truncated: bool = False
    # v2.330.0 — domain scope alongside subnet scope, capped the same way.
    # Name scope is INDEPENDENT of subnet scope: a name in scope does not put
    # the address it resolves to in scope, and vice versa.
    domains: List[ScopeDomainBrief] = Field(default_factory=list)
    domain_total: int = 0
    domains_truncated: bool = False
    # Distinct names in the project's inventory covered by ANY domain entry
    # (deduplicated across nested entries).  Project-wide — a project has one
    # conceptual scope — so every ScopeBrief in a project carries the same value.
    names_in_scope_total: int = 0

    model_config = ConfigDict(from_attributes=True)


class AssistNameRow(BaseModel):
    """One named asset as the assist surface sees it (v2.330.0).  A name is an
    identity, not an address: ``current_ips`` is DERIVED from the latest
    A/AAAA observation batch, never stored."""
    id: int
    fqdn: str
    kind: str                       # 'fqdn' | 'wildcard'
    # Covered by a declared domain-scope entry.  Does NOT make current_ips
    # subnet-in-scope.
    in_scope: bool
    # Addresses the name currently resolves to (capped at 10; ``current_ip_total``
    # is the true count).  Empty = unresolved (imported / seen in a cert or
    # HTTP evidence, but no A/AAAA answer recorded yet).
    current_ips: List[str] = Field(default_factory=list)
    current_ip_total: int = 0
    last_seen: Optional[datetime] = None
    # Observation kinds recorded for the name (A, AAAA, CNAME, PTR, IMPORT,
    # HTTP, CERT, SCANNER, TESTED, ...).
    sources: List[str] = Field(default_factory=list)


class AssistNamesResponse(BaseModel):
    items: List[AssistNameRow] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 0
    returned: int = 0
    has_more: bool = False


class ProjectInfo(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    status: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    agent_name: Optional[str] = None


class AgentIdentityOperator(BaseModel):
    id: int
    username: Optional[str] = None
    # v2.311.0 — the operator's role in this project, and whether they are a
    # global admin (who bypasses project roles entirely).  Naming the operator
    # without naming their authority left the agent exactly where the removed
    # capability list left it: able to learn what it may write only by writing
    # and reading the 403.  ``project_role`` is None for a global admin, who has
    # no membership row to report.
    project_role: Optional[str] = None
    is_global_admin: bool = False


class AgentIdentity(BaseModel):
    """What this API key is, answerable by *any* agent key.

    Every other self-introspection endpoint is behind a workflow gate
    (``/agent/assist/session`` needs an assist key, planning context needs a
    plan key), so a caller holding an unknown key could only discover what it
    was by trying surfaces until one stopped returning 403.  That is fine for a
    human with the UI open and useless for a client that has to decide, before
    its first call, which tools to even offer — which is exactly what the MCP
    server does at ``tools/list`` time.
    """
    # ``project`` for every session minted since v2.337.0; the legacy
    # per-workflow labels survive on older rows.
    workflow: Optional[str] = None
    session_id: Optional[int] = None
    # (``plan_id`` / ``execution_session_id`` / ``open_phases`` went with test
    # plans and execution runs in v2.442.0: a session has no phases.)
    project_id: int
    project_name: Optional[str] = None
    agent_id: int
    agent_name: Optional[str] = None
    # v2.309.0 — `capabilities` / `capability_constraint` removed. A key's
    # authority is its operator's project role, so `operator` is the answer to
    # "what may I do here" and there is no second list to reconcile it against.
    operator: Optional[AgentIdentityOperator] = None
    # v2.311.0 — the one bit an agent actually plans around, precomputed rather
    # than left as an inference from `operator.project_role`.  It is the same
    # predicate `enforce_agent_operator_access` applies to a project write
    # (ANALYST or above, or global admin), so a `false` here and a 403 on the
    # first write can never disagree.  Session-metadata writes — key
    # renewal, feedback, ending the session — are not project writes and stay
    # available to a read-only operator.
    can_write_project_data: bool = False
    # Agent keys are short-lived (24h for plan keys). An agent that knows when
    # its credential dies can finish or hand back cleanly instead of failing
    # mid-run on a 401 it has no way to anticipate.
    key_expires_at: Optional[datetime] = None
    # v2.304.0 — where to extend it, and how long extending stays possible.
    # Surfaced here so an agent about to block on a long scan can renew FIRST,
    # rather than discovering the lapse at upload time with the work already
    # done. Renewal also works after expiry (see /agent/session/renew), so this
    # is the cheap path, not the only one.
    renew_path: Optional[str] = None
    renewable_until: Optional[datetime] = None


class AgentToolSuggestionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rationale: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="What you used or needed it for, and why it belongs in the catalogue.",
    )
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)


class AgentToolSuggestionResponse(BaseModel):
    name: str
    status: str
    # True when the "suggestion" names a tool already in the catalogue.
    already_catalogued: bool = False
    message: str


class AgentDashboard(BaseModel):
    host_count: int
    up_host_count: int
    open_port_count: int
    scan_count: int
    last_scan_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Schemas — host notes & follow (agent-facing)
# ---------------------------------------------------------------------------

class AgentNoteCreate(BaseModel):
    body: str = Field(..., min_length=1)


class AgentNoteResponse(BaseModel):
    id: int
    host_id: int
    body: str
    author_id: int
    parent_id: Optional[int] = None
    # 'agent' for anything written through this API — echoed back so the
    # agent can confirm its note is attributed as machine-authored.
    actor_type: str = "agent"
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AgentFollowRequest(BaseModel):
    status: str  # watching | in_review | reviewed


class AgentHostUpdate(BaseModel):
    """Operator-curated host corrections an agent may apply after investigation.

    Only the two attributes a human would fix by hand are editable — scan-
    derived facts (ports, services, vulns) are never mutated here. Pass a field
    to set it; omit it to leave it unchanged; pass an empty string to clear it.
    """

    hostname: Optional[str] = Field(None, max_length=255)
    os_name: Optional[str] = Field(None, max_length=255)


class AgentHostUpdateResponse(BaseModel):
    id: int
    ip_address: str
    hostname: Optional[str] = None
    os_name: Optional[str] = None
    changed: List[str] = Field(default_factory=list)



# ---------------------------------------------------------------------------
# Schemas — scope reads and uploads
# ---------------------------------------------------------------------------

class ReconUploadResponse(BaseModel):
    job_id: int
    filename: str
    status: str
    message: str
    # v2.335.0 — the batch this file joined (the upload's `batch` label).
    batch_id: Optional[int] = None
    batch: Optional[str] = None


class ReconJobStatus(BaseModel):
    job_id: int
    status: str
    message: Optional[str] = None
    error_message: Optional[str] = None
    scan_id: Optional[int] = None
    tool_name: Optional[str] = None
    parse_error_id: Optional[int] = None
    last_error: Optional[str] = None
    # Derived timing so the agent can see where an upload spent its time
    # without polling repeatedly.  Both are None until the relevant
    # transition has happened: ``queue_age_s`` (created -> started) once the
    # worker has picked the job up, ``parse_s`` (started -> completed) once
    # parsing has finished.
    queue_age_s: Optional[float] = None
    parse_s: Optional[float] = None


# The scope target shapes (host / port briefs, web targets) moved to
# ``app.services.scope_targets_service`` in v2.433.1: their only user.
