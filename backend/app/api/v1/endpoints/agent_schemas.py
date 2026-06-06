"""
Agent API — Pydantic schemas.

All request/response models for the agent-facing endpoints.  Split out
of agent_api.py so the route modules (agent_browse / agent_test_plans /
agent_execution / agent_recon) can share a single schema definition.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models_agent import (
    SanityCheckMethod,
    TestEntryPriority,
    TestEntryStatus,
    TestExecutionStatus,
    TestPhase,
)
from app.schemas.schemas import ProposedTest, ProposedTestItem

# Use ORM enums directly — Pydantic accepts enum values in JSON and validates membership
PriorityValue = TestEntryPriority
PhaseValue = TestPhase
EntryStatusValue = TestEntryStatus


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

    model_config = ConfigDict(from_attributes=True)


class HostDetail(HostBrief):
    ports: List[PortBrief] = Field(default_factory=list)


class PortTuple(BaseModel):
    port: int
    protocol: str
    state: str
    service: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None


class VulnBrief(BaseModel):
    title: str
    severity: str
    cve_id: Optional[str] = None


class CandidateHost(BaseModel):
    """One host the planning agent should consider for inclusion.

    Each candidate is a *grouped semantic view* of what the agent needs
    to make a selection decision: identity (id/ip/hostname/os), observed
    services (open_port_count/services/ports), vulnerability posture
    (vuln_summary/top_vulnerabilities), and an explicit server-side
    selection recommendation (meets_policy + inferred_service_hints).
    """

    # --- identity ---
    id: int = Field(..., description="Internal host id (use for /agent/hosts/{host_id} drill-down).")
    ip_address: str = Field(..., description="The host's primary IPv4/IPv6 address.")
    hostname: Optional[str] = Field(None, description="Reverse DNS or scan-derived hostname; null when no name was discovered.")
    os_name: Optional[str] = Field(None, description="OS family / version as detected by nmap or other scanners. May be a coarse family (e.g. 'Linux') or a specific build.")

    # --- observed services ---
    open_port_count: int = Field(0, description="Count of distinct open ports across all scans of this host. Derived; do not recompute from `ports`.")
    services: List[str] = Field(default_factory=list, description="Distinct service names observed on open ports (e.g. 'ssh', 'http', 'smb'). Empty when no service detection ran or all services were null.")
    ports: List[PortTuple] = Field(default_factory=list, description="Per-port detail (number, protocol, state, service). Concrete evidence the policy evaluator used.")

    # --- vulnerability posture ---
    vuln_summary: VulnCounts = Field(default_factory=VulnCounts, description="Counts per severity. Note: counts, not proof of exploitability — the agent should still review `top_vulnerabilities`.")
    top_vulnerabilities: List[VulnBrief] = Field(default_factory=list, description="Highest-severity vulnerabilities for triage; capped to keep payloads scannable.")

    # --- policy evaluation ---
    meets_policy: bool = Field(False, description="Server-side recommendation: true iff this host satisfies the project's selection policy. The agent should ordinarily create entries for every `meets_policy: true` host and skip the rest, deviating only with a stated reason.")
    inferred_service_hints: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Fallback service identities for high-value ports (SMB, RDP, databases, etc.) "
            "where nmap returned null/'unknown'.  Empty when every open port has a real "
            "service name OR no high-value port is open.  Lets agents justify policy "
            "decisions without re-implementing the port→service mapping.  Added v2.10.0."
        ),
    )


class PlanningContext(BaseModel):
    """The full bundle returned by `GET /agent/test-plans/{plan_id}/context`.

    Workflow: read `summary` to understand candidate counts and any
    `filter_criteria` already applied, read `selection_policy` for the
    rubric, then walk `candidate_hosts` creating entries for those with
    `meets_policy: true`.  `plan` is the plan's current state (title,
    description, status) so the agent can detect a re-fetch on a
    partially-populated plan.

    The ``entry_template`` / ``entry_batch_example`` / ``entry_schema``
    fields are the machine-readable contract for the follow-up
    ``POST /entries`` call — agents that previously had to infer the
    request shape from AGENTS.md examples can copy-paste from these
    directly.
    """

    plan: dict = Field(..., description="Current TestPlan state — id, title, description, status, version, filter_criteria. Echo the id back when posting entries.")
    filter_criteria: Optional[dict] = Field(None, description="The host filters the user picked at plan-generation time. `candidate_hosts` is already pre-filtered by these — do not re-apply them.")
    agent_name: str = Field(..., description="The provisioned agent's display name. Must appear in the `🤖 Agent-generated — {agent_name}` attribution prefix on the plan description.")
    selection_policy: str = Field(..., description="Human-readable rubric describing how `meets_policy` was computed. Quote it in the plan description to justify entry selection.")
    summary: dict = Field(..., description="Aggregate counts: `total_hosts`, `matching_filter`, `meets_policy_count`, plus vuln severity rollups. Report this to the user as the first thing after fetching context.")
    candidate_hosts: List[CandidateHost] = Field(..., description="Per-host candidate details. Already filtered by `filter_criteria`; apply the selection policy on top.")
    entry_template: dict = Field(
        ...,
        description=(
            "Concrete example of a single `EntryCreate` payload with all "
            "fields populated.  Replace `host_id` with one from "
            "`candidate_hosts[].id`, then adjust `priority`, `test_phase`, "
            "`proposed_tests`, `rationale`, and `notes` for the actual "
            "intent.  Pattern-match on this directly — no need to infer "
            "the shape from AGENTS.md examples."
        ),
    )
    entry_batch_example: dict = Field(
        ...,
        description=(
            "Concrete example of the `EntryBatch` body that "
            "`POST /agent/test-plans/{plan_id}/entries` actually accepts: "
            "`{\"entries\": [entry, …]}`, up to 500 entries per call."
        ),
    )
    entry_schema: dict = Field(
        ...,
        description=(
            "JSON Schema for `EntryCreate` (Pydantic-generated).  Use "
            "this if your agent harness can validate against schemas; "
            "otherwise the `entry_template` example is enough."
        ),
    )


class ScanBrief(BaseModel):
    id: int
    filename: str
    scan_type: Optional[str] = None
    tool_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ScopeBrief(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    subnets: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ProjectInfo(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    status: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    agent_name: Optional[str] = None


class AgentDashboard(BaseModel):
    host_count: int
    up_host_count: int
    open_port_count: int
    scan_count: int
    last_scan_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Schemas — test plans
# ---------------------------------------------------------------------------

class PlanCreate(BaseModel):
    title: str = Field(..., max_length=200, min_length=1)
    description: Optional[str] = None


class PlanUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200, min_length=1)
    description: Optional[str] = None
    # v2.19.0 — agent self-reports its own identity during plan generation
    # so the audit trail can later show *which* model/harness produced the
    # plan and against which prompt version.  All optional; the service
    # only writes them once (a re-PATCH with these fields null is a no-op
    # for them, so the agent can edit description later without erasing
    # provenance).  Values are free-form strings — agents pick the same
    # conventions the feedback endpoint already uses.
    generated_by_model: Optional[str] = Field(
        None, max_length=100,
        description='Model id, e.g. "claude-opus-4-7" or "gpt-5".',
    )
    generated_by_tool: Optional[str] = Field(
        None, max_length=100,
        description='Agent harness/CLI, e.g. "claude-code", "codex", "chatgpt".',
    )
    prompt_version: Optional[str] = Field(
        None, max_length=20,
        description='The PROMPT_VERSION the agent was instructed with — echo back the value from the instructions block.',
    )


class EntryCreate(BaseModel):
    host_id: int
    priority: PriorityValue
    test_phase: PhaseValue
    proposed_tests: List[ProposedTestItem]
    rationale: str
    notes: Optional[str] = None


class EntryBatch(BaseModel):
    entries: List[EntryCreate] = Field(..., max_length=500)


class AgentEntryUpdate(BaseModel):
    priority: Optional[PriorityValue] = None
    test_phase: Optional[PhaseValue] = None
    proposed_tests: Optional[List[ProposedTestItem]] = None
    rationale: Optional[str] = None
    status: Optional[EntryStatusValue] = None
    findings: Optional[str] = None
    results_data: Optional[dict] = None
    notes: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class PlanResponse(BaseModel):
    id: int
    version: int
    title: str
    description: Optional[str] = None
    status: str
    entry_count: int = 0
    completion_pct: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EntryResponse(BaseModel):
    """Agent-facing entry response (excludes hostname and assigned_to_id)."""
    id: int
    host_id: int
    host_ip: Optional[str] = None
    priority: str
    test_phase: str
    proposed_tests: List[ProposedTestItem]
    rationale: str
    status: str
    findings: Optional[str] = None
    results_data: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlanDetailResponse(PlanResponse):
    entries: List[EntryResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Schemas — host notes & follow (agent-facing)
# ---------------------------------------------------------------------------

class AgentNoteCreate(BaseModel):
    body: str = Field(..., min_length=1)
    status: str = Field("open")  # open | in_progress | resolved


class AgentNoteResponse(BaseModel):
    id: int
    host_id: int
    body: str
    status: str
    author_id: int
    parent_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AgentFollowRequest(BaseModel):
    status: str  # watching | in_review | reviewed


class EntryBatchResponse(BaseModel):
    """Wrapped response for batch entry creation.

    The agent guide documents the response as ``{"entries": [...]}``,
    not a bare array.  Wrapping is intentional so agents can call
    ``response.get("entries")`` without crashing.
    """
    entries: List[EntryResponse] = Field(default_factory=list)


class CoverageInfo(BaseModel):
    """Non-blocking coverage summary appended to validate response.

    v2.10.0: the single ``eligible_hosts_remaining`` number was
    confusing — it conflated hosts the agent *should* have included
    (policy-matching, true missed scope) with hosts the agent
    *correctly excluded* (open ports but not policy-matching, e.g.
    low-risk informational-only hosts).  Agent feedback flagged this
    as the biggest friction point in plan creation.

    The response now splits into two explicit counts:

    - ``policy_matching_remaining`` — hosts that meet the selection
      policy AND are not in the plan.  A non-zero value means the
      agent either missed scope or intentionally narrowed the plan.
      This is the number worth acting on.
    - ``non_policy_with_open_ports`` — hosts with open ports that the
      selection policy correctly skipped (no crit/high vulns, no
      qualifying medium).  A non-zero value here is *normal* and
      expected; it's the number of hosts the agent intentionally
      left out by following the rubric.

    ``eligible_hosts_remaining`` is kept for backwards compatibility
    with v2.9.x clients and equals the sum of both buckets.
    """
    entries_in_plan: int = 0
    eligible_hosts_remaining: int = 0  # deprecated — sum of the two below
    policy_matching_remaining: int = 0
    non_policy_with_open_ports: int = 0
    coverage_pct: float = 0.0
    note: Optional[str] = None


class PreSubmitReport(BaseModel):
    """Dry-run validation report returned before actual submission."""
    plan_id: int
    ready: bool
    total_entries: int
    by_priority: Dict[str, int]
    by_phase: Dict[str, int]
    warnings: List[str]
    coverage: Optional[CoverageInfo] = None


# ---------------------------------------------------------------------------
# Schemas — test execution
# ---------------------------------------------------------------------------

class ExecutionHostContext(BaseModel):
    entry_id: int
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    os_name: Optional[str] = None
    priority: str
    test_phase: str
    entry_status: str
    sanity_check_passed: Optional[bool] = None
    tests: List[Dict[str, Any]] = Field(default_factory=list)
    known_services: List[Dict[str, Any]] = Field(default_factory=list)


class ExecutionContextResponse(BaseModel):
    plan: dict
    session_id: int
    agent_name: str
    hosts: List[ExecutionHostContext] = Field(default_factory=list)
    # v2.23.0 — echo back what the agent reported via the probe endpoint.
    # None means no probe has been recorded for this session yet; the
    # agent should run one (see AGENTS.md § Environment probe) before
    # proposing commands so this field is populated for subsequent calls.
    environment: Optional["EnvironmentSummary"] = None


class SanityCheckRequest(BaseModel):
    # use_enum_values keeps ``body.method`` as the plain string ("banner_grab"
    # etc.) so it slots straight into the String(30) DB column without
    # calling .value, while Pydantic still rejects unknown methods.
    model_config = ConfigDict(use_enum_values=True)

    method: SanityCheckMethod
    target_ip: str
    port_checked: Optional[int] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    source_ip: Optional[str] = None
    dns_result: Optional[str] = None
    passed: bool
    details: Optional[str] = None


class TestResultRequest(BaseModel):
    # Same pattern as SanityCheckRequest — accept the enum's string value,
    # reject anything else.  Replaces the previous ``status: str``, which
    # let arbitrary unknown statuses reach the DB.
    model_config = ConfigDict(use_enum_values=True)

    test_index: int
    status: TestExecutionStatus
    command_run: Optional[str] = None
    raw_output: Optional[str] = None
    findings_summary: Optional[str] = None
    severity: Optional[str] = None
    is_finding: bool = False
    # v2.91.0 (code review #2) — required when there's no passing
    # HostSanityCheck on file for this entry.  Mirrors the
    # CompleteEntryRequest.override_reason shape (≤500 chars, free
    # text) so the audit invariant is symmetric across "record this
    # result" and "complete this entry."  Operators get to preserve
    # the result data (Option B over reject-outright) while the
    # reason carries the audit context for who bypassed sanity.
    sanity_override_reason: Optional[str] = Field(None, max_length=500)


class CompleteEntryRequest(BaseModel):
    # use_enum_values keeps ``body.overall_status`` as the plain string
    # ("completed" / "rejected") so it slots straight into the
    # String(20) DB column without calling .value, while Pydantic still
    # rejects unknown statuses (v2.25.0 — previously str-typed, which
    # let arbitrary statuses ("done", "complete", typos) reach the DB
    # and disappear from progress/reporting that recognises only the
    # canonical terminal states).
    model_config = ConfigDict(use_enum_values=True)

    findings_summary: Optional[str] = None
    overall_status: TestEntryStatus = TestEntryStatus.COMPLETED
    # v2.22.0: an entry can only complete with a *passing* sanity check
    # on file, OR an explicit override_reason explaining why one wasn't
    # possible (target down, scope change mid-run, etc.).  Audit-visible
    # so a human reviewer can flag overrides.  Free-form text; ≤500 chars
    # to keep audit rows scannable.
    override_reason: Optional[str] = Field(None, max_length=500)
    # v2.25.0 — completion also refuses when the entry has zero recorded
    # TestExecutionResult rows AND zero proposed_tests, OR when the
    # caller passes an explicit no_tests_run_reason to acknowledge that
    # they're closing without evidence.  The audit-trail invariant is
    # symmetric with override_reason: every "no result row" completion
    # carries a human-readable justification.
    no_tests_run_reason: Optional[str] = Field(None, max_length=500)


class ExecutionSessionCompleteRequest(BaseModel):
    """Body for ``POST /agent/execution-sessions/{id}/complete`` (v2.45.2).

    Mirrors the recon-side ``ReconCompleteRequest`` shape so the agent
    closure flow is symmetric across workflows.  ``overall_status``
    defaults to ``completed`` — set explicitly to ``failed`` when the
    agent is closing because the engagement broke (target offline
    mid-run, credentials revoked) rather than because all tests ran.
    ``abandoned`` is reserved for the JWT-side endpoint operators use
    when they're giving up; agents shouldn't self-abandon.
    """
    model_config = ConfigDict(use_enum_values=True)

    notes: Optional[str] = Field(
        None,
        max_length=8192,
        description=(
            "Free-form closing note appended to the session record. "
            "Use to summarize findings, coverage gaps, or environment "
            "issues that affected the run."
        ),
    )
    overall_status: Literal["completed", "failed"] = Field(
        "completed",
        description=(
            "Terminal status for the session.  'completed' = all "
            "planned entries ran (some may be skipped or failed at the "
            "entry level — that's normal).  'failed' = the session "
            "itself broke (auth lost, scope mismatch, etc.) and the "
            "agent is closing it as a failure rather than a success."
        ),
    )


class ExecutionSessionCompleteResponse(BaseModel):
    """Confirmation echo after ``/execution-sessions/{id}/complete``."""
    session_id: int
    test_plan_id: int
    status: str
    completed_at: datetime
    entries_total: int
    entries_completed: int
    entries_remaining: int
    tests_recorded: int
    findings_count: int


class ExecutionProgressResponse(BaseModel):
    plan_id: int
    session_id: int
    total_entries: int = 0
    entries_completed: int = 0
    entries_in_progress: int = 0
    entries_remaining: int = 0
    total_tests: int = 0
    tests_executed: int = 0
    tests_skipped: int = 0
    # v2.25.0 — the three statuses below were previously lumped into
    # ``tests_pending``, overstating remaining work the moment a
    # result landed in any terminal-or-blocked state.  Surfaced
    # explicitly now; ``tests_pending`` is derived from "proposed -
    # everything we have a row for" so a future enum addition doesn't
    # silently rejoin the pending bucket.
    tests_failed: int = 0
    tests_not_applicable: int = 0
    tests_pending_approval: int = 0
    tests_pending: int = 0
    findings_count: int = 0
    critical_findings: int = 0


# ---------------------------------------------------------------------------
# Schemas — environment probe (v2.23.0)
# ---------------------------------------------------------------------------
#
# Both workflows (recon + execution) carry a per-session probe so the
# agent's command-flavour choices are grounded in what is *actually*
# available on the operator's host.  The shape is intentionally loose:
# the agent reports a small fixed set of high-signal facts plus a free
# ``extras`` bag for anything else worth recording (kernel version,
# observed AV agent, custom toolbox).  See AGENTS.md § Environment probe
# for the agent-facing contract.

class EnvironmentSummary(BaseModel):
    """Result of the agent's environment probe.

    Per-session, per-user (the user who owns the agent that ran the
    probe). Plans describe test *intent*; the executing agent uses this
    summary to pick command flavour at runtime — same plan, different
    environment, different translation.
    """
    # Use Pydantic's default config + extra="allow" so the agent can
    # include observed facts beyond the fixed shape without us shipping
    # a new schema version each time.
    model_config = ConfigDict(extra="allow")

    # Host fingerprint
    os_family: str = Field(
        description="High-level OS family: 'windows', 'linux', 'darwin', 'bsd', 'other'."
    )
    os_release: Optional[str] = Field(
        None,
        description="Distribution + version when known ('Ubuntu 22.04', 'Windows 11 23H2', 'Kali rolling').",
    )
    arch: Optional[str] = Field(
        None,
        description="CPU architecture ('x86_64', 'arm64'). Used to pick prebuilt-binary flavours.",
    )

    # Shell + scripting capabilities
    shell: Optional[str] = Field(
        None,
        description="The shell the agent is talking to. 'pwsh' / 'powershell' / 'bash' / 'zsh' / 'cmd'.",
    )
    powershell_version: Optional[str] = Field(
        None,
        description="$PSVersionTable.PSVersion if PowerShell is available; otherwise null.",
    )
    powershell_execution_policy: Optional[str] = Field(
        None,
        description=(
            "Get-ExecutionPolicy result. Critical for choosing inline -Command vs "
            ".ps1 file. 'Restricted' / 'AllSigned' / 'RemoteSigned' / 'Unrestricted' / 'Bypass'."
        ),
    )
    python: Optional[str] = Field(
        None,
        description=(
            "Resolved Python interpreter path *or* the literal string "
            "'microsoft-store-stub' when `python` resolves to the Win10/11 "
            "Store stub (which is unusable). null when not present."
        ),
    )
    python_version: Optional[str] = Field(
        None,
        description="`python --version` output, or null when Python is unavailable.",
    )
    wsl_available: Optional[bool] = Field(
        None,
        description=(
            "Windows: did `wsl --status` succeed?  Lets the agent fall back to a "
            "Linux toolbox transparently.  null when the concept doesn't apply "
            "(non-Windows hosts) or the agent didn't check — semantically distinct "
            "from `false` ('WSL absent on a Windows host')."
        ),
    )

    # Tool inventory — bool per tool name.  Loose dict so the agent
    # can include any toolbox member without us iterating the schema.
    tools_available: Dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Map of tool-name → present-on-PATH for the agent's preferred "
            "toolbox. Names follow the AGENTS.md inventory: 'nmap', 'masscan', "
            "'httpx', 'dig', 'curl', 'jq', 'enum4linux', 'nxc', 'nikto', ..."
        ),
    )

    # Free-text notes the agent thinks the human reviewer should see —
    # AV product detected, sandbox/VM indicators, network egress
    # restrictions, etc.  Capped to keep the JSON row scannable.
    notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Free-form notes worth surfacing to a human reviewing the audit trail.",
    )


class EnvironmentProbeRequest(EnvironmentSummary):
    """The body of POST /agent/{recon,execution-sessions}/{id}/environment.

    v2.28.0 — optional executing-agent attribution fields ride along
    with the probe so a user comparing two execution sessions of the
    same plan can see which agent/model ran each.  These mirror the
    plan-generation provenance from v2.19.0; on execution sessions
    they persist to ``execution_sessions.generated_by_*`` columns.
    Recon sessions persist them into the environment JSON for now
    (recon doesn't have first-class attribution columns yet).
    """
    agent_model: Optional[str] = Field(
        None,
        max_length=100,
        description=(
            "Model id of the agent running this session "
            "(e.g. `claude-opus-4-7`, `gpt-5-codex`).  Optional."
        ),
    )
    agent_tool: Optional[str] = Field(
        None,
        max_length=100,
        description=(
            "Harness / CLI the agent runs inside "
            "(`claude-code`, `codex`, `chatgpt`, `manual-curl`).  Optional."
        ),
    )
    agent_prompt_version: Optional[str] = Field(
        None,
        max_length=20,
        description=(
            "PROMPT_VERSION the agent received from BlueStick "
            "for this session.  Echo back what the prompt told you."
        ),
    )


class EnvironmentProbeResponse(BaseModel):
    """Confirmation echo after a probe is persisted.

    Tells the agent what we stored (so it can verify the round-trip) and
    surfaces the audit fields we stamped on the row.
    """
    session_id: int
    # v2.65.0 — was `session_type: str` with a freehand comment listing
    # valid values.  Promoted to a typed Literal so the constraint is
    # compile-time visible (Pydantic v2 enforces) and so the OpenAPI
    # schema surfaces it as an enum for downstream consumers.
    session_type: Literal["recon", "execution", "assist"]
    probed_at: datetime
    probed_by_user_id: Optional[int] = None
    probed_from_ip: Optional[str] = None
    environment: EnvironmentSummary


# ---------------------------------------------------------------------------
# Schemas — agentic reconnaissance
# ---------------------------------------------------------------------------

class KnownHostsProbeHelper(BaseModel):
    """Ready-to-use command + target list for deepening on already-known hosts.

    Populated in ``/agent/recon/context`` whenever the scope already has
    hosts with open ports.  The ``recommended_sequence`` always leads
    with *comprehensive* discovery (v2.13.2 default flip — see feedback
    #5); this helper exists so an agent that the user explicitly asks
    to narrow to the already-known set has a pre-built command instead
    of having to query ``/agent/hosts`` and build ``live-hosts.txt``
    itself.
    """
    live_hosts: List[str] = Field(default_factory=list)  # IP literals
    live_hosts_file_content: str = ""  # newline-joined, ready to redirect to a file
    command: str = ""
    note: str = ""


class ReconContextResponse(BaseModel):
    recon_session_id: int
    scope_id: int
    scope_name: str
    # v2.45.4 — `scope_cidrs` is now a BOUNDED sample, not necessarily
    # the full list.  A scope with thousands of CIDRs would otherwise
    # bloat every /recon/context response (and the agent's context
    # window).  When `subnets_truncated` is true, `scope_cidrs` holds
    # only the first `len(scope_cidrs)` entries and the authoritative
    # complete list must be paged from GET /agent/recon/subnets.
    # `scope_cidrs_total` is always the true count.
    scope_cidrs: List[str]
    scope_cidrs_total: int = 0
    subnets_truncated: bool = False
    known_host_summary: Dict[str, Any]
    tool_catalog: List[Dict[str, Any]]
    session_status: str
    started_at: Optional[datetime] = None
    # v2.13.0 — scale awareness.  Agents previously picked the first
    # discovery entry from the catalog (nmap -sn) without doing address
    # math against the CIDR list, so a /16 scope could burn hours on a
    # sequential ICMP sweep.  These fields do the math server-side and
    # hand the agent a pre-computed recommendation.
    scope_size: Optional[Dict[str, Any]] = None
    recommended_sequence: Optional[List[Dict[str, Any]]] = None
    # v2.13.2 — helper for the "user narrows to known hosts" path.
    # None when the scope has no hosts with open ports yet.
    known_hosts_probe: Optional[KnownHostsProbeHelper] = None
    # v2.23.0 — echo back the recon environment probe so the agent's
    # next scan-flavour choice reflects this session's operator host.
    # None means no probe has been recorded yet; the agent should
    # POST /agent/recon/sessions/{id}/environment before scanning.
    environment: Optional["EnvironmentSummary"] = None


class ReconUploadResponse(BaseModel):
    job_id: int
    filename: str
    status: str
    message: str
    recon_session_id: int


class ReconJobStatus(BaseModel):
    job_id: int
    status: str
    message: Optional[str] = None
    error_message: Optional[str] = None
    scan_id: Optional[int] = None
    tool_name: Optional[str] = None
    parse_error_id: Optional[int] = None
    recon_session_id: Optional[int] = None
    last_error: Optional[str] = None


class ReconPortBrief(BaseModel):
    """One open port on a host, in the per-session breakdown.

    v2.13.2 — added after feedback #5 flagged that the per-host summary
    only carried service names, not port numbers, so agents had to hit
    ``/agent/hosts`` or parse raw XML to build a web-target list.
    """
    port: int
    protocol: str = "tcp"
    state: str = "open"
    service: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None


class ReconHostBrief(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    open_port_count: int = 0
    services: List[str] = Field(default_factory=list)
    # v2.13.2 — per-port detail so agents don't have to cross-reference
    # /agent/hosts or parse uploaded XML to build follow-up target lists.
    open_ports: List[ReconPortBrief] = Field(default_factory=list)


class WebTarget(BaseModel):
    """Pre-computed web-fingerprint target, derived from open HTTP/HTTPS ports.

    v2.13.2 — feedback #5: agents were manually building web-target
    lists by walking ``hosts[].services`` looking for "http"/"https".
    This helper encodes the port→scheme mapping server-side and hands
    the agent a ready-to-use URL list for httpx / eyewitness.
    """
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    port: int
    protocol: str  # "http" | "https"
    url: str


class ReconSummaryResponse(BaseModel):
    recon_session_id: int
    scope_id: int
    status: str
    uploads_submitted: int
    scans_ingested: int
    hosts_discovered: int
    ports_discovered: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Per-host breakdown of what's been found in this recon session.
    # v2.11.1 — the prompt had always promised this but the response
    # was only returning totals.  Populated from the same scan-history
    # joins that produce the aggregate counts, so it's consistent.
    hosts: List[ReconHostBrief] = Field(default_factory=list)
    # v2.13.2 — pre-computed web-fingerprint target list derived from
    # hosts[].open_ports.  Saves agents a round trip.
    web_targets: List[WebTarget] = Field(default_factory=list)


class ReconCompleteRequest(BaseModel):
    notes: Optional[str] = None
