"""
AI Agent and Test Plan Models

Models for AI agent identity, authentication, test plan management,
and test execution sessions.  Agents are project-scoped entities that
authenticate via API key and propose structured test plans for host-
level security testing.  Execution sessions track per-test results
when an agent (or human) runs the approved tests and records findings.
"""

import enum

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, Boolean,
    ForeignKey, JSON, UniqueConstraint, Index, CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestPlanStatus(str, enum.Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class TestEntryStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"  # terminal "not tested" — replaces former "skipped"


class TestEntryPriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class TestPhase(str, enum.Enum):
    RECONNAISSANCE = "reconnaissance"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"


class ActorType(str, enum.Enum):
    USER = "user"
    AGENT = "agent"


class TestPlanSourceKind(str, enum.Enum):
    """Discriminator for ``TestPlan.source_kind`` (v3 alpha.3).

    Tells the UI + the plan-generation flow what the plan was scoped
    against.  Only one of the four payload columns is populated per
    plan; the application layer (not the DB) enforces that.

    UNSPECIFIED is applied to all pre-alpha.3 rows by the
    ``c7e3f491a5d2`` migration and to any plan created without an
    explicit source — the UI renders it as "(provenance not recorded)".
    """
    RECON_SESSION = "recon_session"
    MANUAL_HOSTS = "manual_hosts"
    FILTER_SET = "filter_set"
    INHERITED = "inherited"
    UNSPECIFIED = "unspecified"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent(Base):
    """An AI agent owned by a user and scoped to a project.

    Each user may have one agent per project.  The agent authenticates via
    API key and inherits access to the owner's project data.
    """
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)
    rate_limit_rpm = Column(Integer, default=240, server_default="240", nullable=False)

    # Tracking
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_activity_at = Column(DateTime(timezone=True))

    # Relationships
    project = relationship("Project", foreign_keys=[project_id])
    owner = relationship("User", foreign_keys=[owner_id])
    test_plans = relationship("TestPlan", back_populates="agent", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "owner_id", name="uq_agent_per_user_project"),
    )


# ---------------------------------------------------------------------------
# Test Plan
# ---------------------------------------------------------------------------

class TestPlan(Base):
    """A structured test plan for a project.

    May be created by an AI agent (agent_id set) or directly by a user
    (created_by_user_id set).  At least one of the two should be populated.
    """
    __tablename__ = "test_plans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version = Column(Integer, nullable=False, default=1)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    status = Column(
        String(20),
        nullable=False,
        default=TestPlanStatus.DRAFT.value,
    )

    # Approval workflow
    approved_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True))
    rejected_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rejected_at = Column(DateTime(timezone=True))
    rejection_reason = Column(Text)
    filter_criteria = Column(JSON, nullable=True)

    # Generation provenance (v2.19.0).  Stamped by the agent during the
    # PATCH step of plan generation so a human reviewer can later see
    # *which* model / harness produced the plan and against which version
    # of the agent prompt.  All nullable — plans created before this and
    # plans where the agent skips the PATCH carry NULLs and the UI shows
    # "not recorded".
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    # Source provenance (v3 alpha.3).  Tells the UI what the plan was
    # scoped against — a recon run, a hand-picked host set, a filter
    # expression, or an earlier plan.  ``source_kind`` discriminates
    # which of the payload columns is populated.  See
    # ``TestPlanSourceKind`` for the enumerated values and the
    # ``c7e3f491a5d2`` migration for the contract.  The four payload
    # columns are mutually exclusive at the application layer.
    source_kind = Column(
        String(30),
        nullable=False,
        default=TestPlanSourceKind.UNSPECIFIED.value,
        server_default=TestPlanSourceKind.UNSPECIFIED.value,
    )
    source_recon_session_id = Column(
        Integer,
        ForeignKey("recon_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # JSON rather than postgresql.ARRAY(Integer) so SQLite test runs
    # work transparently and the column is portable.  Postgres ARRAY
    # gives no extra integrity (FKs aren't enforced on array elements
    # there either) — the application layer validates the IDs.
    source_host_ids = Column(JSON, nullable=True)
    source_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True))

    # Relationships
    project = relationship("Project", foreign_keys=[project_id])
    agent = relationship("Agent", back_populates="test_plans")
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    rejected_by = relationship("User", foreign_keys=[rejected_by_id])
    source_recon_session = relationship(
        "ReconSession", foreign_keys=[source_recon_session_id]
    )
    # ``remote_side`` makes the self-FK unambiguous: source_plan_id
    # points at the parent's id, not its own row.
    source_plan = relationship(
        "TestPlan", remote_side="TestPlan.id", foreign_keys=[source_plan_id]
    )
    entries = relationship(
        "TestPlanEntry", back_populates="test_plan", cascade="all, delete-orphan",
    )
    history = relationship(
        "TestPlanHistory", back_populates="test_plan", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_test_plan_project_status", "project_id", "status"),
        # Per-project version is monotonic and unique.  TestPlanService
        # .create_plan() retries on the unique violation if two callers
        # race to compute max(version)+1.
        UniqueConstraint("project_id", "version", name="uq_test_plan_project_version"),
    )


# ---------------------------------------------------------------------------
# Test Plan Entry (per-host)
# ---------------------------------------------------------------------------

class TestPlanEntry(Base):
    """A single host-level entry within a test plan."""
    __tablename__ = "test_plan_entries"

    id = Column(Integer, primary_key=True, index=True)
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host_id = Column(
        Integer,
        ForeignKey("hosts_v2.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Test specification
    priority = Column(String(20), nullable=False)          # critical/high/medium/low/info
    test_phase = Column(String(30), nullable=False)        # reconnaissance/enumeration/...
    proposed_tests = Column(JSON, nullable=False)           # list of technique names
    rationale = Column(Text, nullable=False)

    # Status and results
    status = Column(
        String(20),
        nullable=False,
        default=TestEntryStatus.PROPOSED.value,
    )
    findings = Column(Text)
    results_data = Column(JSON)                             # structured results
    notes = Column(Text)

    # Assignment
    assigned_to_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Lifecycle
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    test_plan = relationship("TestPlan", back_populates="entries")
    host = relationship("Host", foreign_keys=[host_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])

    __table_args__ = (
        UniqueConstraint("test_plan_id", "host_id", name="uq_plan_host"),
        Index("idx_entry_plan_status", "test_plan_id", "status"),
        # v2.85.0 — the host-detail "tests against this host" panel
        # filters by host_id then status.  The existing
        # (test_plan_id, status) composite doesn't cover that query;
        # this one does.
        Index("idx_entry_host_status", "host_id", "status"),
    )


# ---------------------------------------------------------------------------
# Test Plan History (audit trail)
# ---------------------------------------------------------------------------

class TestPlanHistory(Base):
    """Audit trail for changes to test plans and their entries."""
    __tablename__ = "test_plan_history"

    id = Column(Integer, primary_key=True, index=True)
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_id = Column(
        Integer,
        ForeignKey("test_plan_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Actor (polymorphic: agent or user)
    actor_type = Column(String(10), nullable=False)         # 'agent' or 'user'
    actor_id = Column(Integer, nullable=False)               # agents.id or users.id

    # Change details
    action = Column(String(30), nullable=False)             # created/updated/approved/rejected/status_changed
    field_changed = Column(String(50))
    old_value = Column(Text)
    new_value = Column(Text)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    test_plan = relationship("TestPlan", back_populates="history")

    __table_args__ = (
        Index("idx_history_plan_time", "test_plan_id", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Execution Sessions + Per-Test Results + Sanity Checks
# ---------------------------------------------------------------------------
#
# These three tables support the agent-driven test execution workflow.
# An execution session is created when a user clicks "Execute with AI"
# on an approved plan — it mints an API key + instructions block just
# like plan generation, then the agent works through the entries host
# by host, recording a sanity check per host and a result per test.
#
# Design decision (confirmed 2026-04-10): the approval gate lives at
# the user's terminal (Claude Code / Codex tool approval, or the user
# manually running commands).  BlueStick's role is providing the
# instructions template + recording the audit trail.
#
# Results from abandoned / interrupted sessions are KEPT and annotated
# with the session's terminal status so consumers know the data came
# from an incomplete pass.

class ExecutionSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    # v2.43.3 (AUD-N1): added FAILED to the enum so the contract matches
    # reality.  The column is a free string and execution_sessions.py was
    # already accepting "failed" as a terminal value (with a comment
    # noting it wasn't enum-defined); declaring it here makes the value
    # discoverable to docs, frontend filters, and external API consumers.
    FAILED = "failed"
    ABANDONED = "abandoned"


class ExecutionSessionMode(str, enum.Enum):
    """Distinguishes live in-session execution from offline bundle export.

    ``in_session`` — the agent runs live against ``/agent/`` endpoints with
    a time-limited API key.  Results flow in as the agent works.
    ``exported`` — the plan was packaged into a ZIP bundle and handed off
    to a remote agent.  Results come back via ``/test-plans/{id}/import-results``
    (offline import), not via the live API.  No API key is minted.
    """
    IN_SESSION = "in_session"
    EXPORTED = "exported"


class TestExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    EXECUTED = "executed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class FindingSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    NONE = "none"


class SanityCheckMethod(str, enum.Enum):
    BANNER_GRAB = "banner_grab"
    REVERSE_DNS = "reverse_dns"
    PING = "ping"
    NETWORK_CONTEXT = "network_context"


class AgentSessionWorkflow(str, enum.Enum):
    """The four agent workflows a key can be scoped to.  Replaces the four
    mutually-exclusive scope FKs that used to live on ``api_keys``."""
    PLAN_GENERATION = "plan_generation"
    EXECUTION = "execution"
    RECON = "recon"
    ASSIST = "assist"


class AgentSession(Base):
    """Unified base row for every agent-workflow session (v2.116.0).

    One row per plan-generation / execution / recon / assist session.  An
    agent API key points at exactly one ``AgentSession``
    (``api_keys.agent_session_id``), and the ``workflow`` discriminator
    replaces the four mutually-exclusive scope FKs + the per-workflow
    deny-matrix that used to live on ``api_keys`` / ``deps.py``.

    Shared lifecycle state (status, timestamps, environment probe, agent/
    model attribution, notes, owner attribution) lives here.  Workflow-
    specific state stays in the detail tables (:class:`ExecutionSession`,
    :class:`ReconSession`, :class:`AssistSession`), each 1:1 with its base
    row via ``agent_session_id`` — composition, not inheritance, so the
    detail PKs (referenced by child rows like TestExecutionResult) stay
    stable.  The detail classes proxy the moved columns through to this
    base for attribute access; query filters reference the base directly.

    ``plan_id`` / ``scope_id`` are kept as real typed FKs (CASCADE) rather
    than one polymorphic ``target_id`` so DB-level referential integrity
    survives the collapse.
    """
    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True, index=True)
    workflow = Column(String(20), nullable=False, index=True)  # AgentSessionWorkflow

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    started_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    # Typed targets — plan_id for plan_generation + execution; scope_id for
    # recon; both null for assist (project-scoped only).
    #
    # use_alter on plan_id: test_plans already FKs back to recon_sessions
    # (plan provenance), and recon_sessions now FKs to agent_sessions, so a
    # plain agent_sessions→test_plans FK closes a cycle that create_all/
    # drop_all can't order.  use_alter emits this one FK as a separate ALTER
    # so metadata DDL (the test suite's create_all) can sort the rest.
    plan_id = Column(
        Integer,
        ForeignKey(
            "test_plans.id", ondelete="CASCADE",
            use_alter=True, name="fk_agent_sessions_plan_id",
        ),
        nullable=True,
        index=True,
    )
    scope_id = Column(
        Integer,
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    status = Column(String(20), nullable=False, default="active")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    # Unified completion timestamp (Assist's "ended_at" maps here).
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Environment probe (shared) — see AGENTS.md § Environment probe.
    environment = Column(JSON, nullable=True)
    environment_probed_at = Column(DateTime(timezone=True), nullable=True)
    environment_probed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    environment_probed_from_ip = Column(String(45), nullable=True)

    # Executing-agent attribution (shared).
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    notes = Column(Text, nullable=True)

    # Relationships
    project = relationship("Project")
    agent = relationship("Agent")
    started_by = relationship("User", foreign_keys=[started_by_id])
    environment_probed_by = relationship(
        "User", foreign_keys=[environment_probed_by_user_id]
    )
    plan = relationship("TestPlan")
    scope = relationship("Scope")

    __table_args__ = (
        Index("idx_agent_session_project", "project_id"),
        Index("idx_agent_session_workflow_status", "workflow", "status"),
        # Workflow/target invariant (R5 contract): a plan workflow must carry a
        # plan_id, recon must carry a scope_id, assist neither.  Constrains only
        # KNOWN workflows — an unrecognised workflow passes the CHECK and is
        # rejected at the auth layer (get_current_agent fails closed), so the
        # DB and the app agree without making that defence-in-depth unreachable.
        CheckConstraint(
            "(workflow NOT IN ('execution','plan_generation') OR plan_id IS NOT NULL) "
            "AND (workflow <> 'recon' OR scope_id IS NOT NULL) "
            "AND (workflow <> 'assist' OR (plan_id IS NULL AND scope_id IS NULL))",
            name="ck_agent_sessions_workflow_target",
        ),
    )


class ExecutionSession(Base):
    """One run of test execution against an approved plan.

    Created by the "Execute with AI" button, which also mints a
    time-limited API key for the agent.  At most one session per plan
    may be `active` at a time — creating a new one pauses the old.
    """
    __tablename__ = "execution_sessions"

    id = Column(Integer, primary_key=True, index=True)
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(
        String(20),
        nullable=False,
        default=ExecutionSessionStatus.ACTIVE.value,
    )
    mode = Column(
        String(20),
        nullable=False,
        default=ExecutionSessionMode.IN_SESSION.value,
    )
    bundle_id = Column(String(64), nullable=True, index=True)
    # v2.116.0 — 1:1 link to the unified AgentSession base.  Nullable during
    # the expand phase (backfilled by the migration); becomes the
    # authoritative home for the shared lifecycle columns in the contract
    # phase.
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Environment probe (v2.23.0).  Filled by the agent on first contact
    # via POST /agent/execution-sessions/{id}/environment so subsequent
    # /context responses can echo it back and the agent picks command
    # flavour from what is actually available on this operator's host.
    # Per-session by design: the same user running from a different
    # machine re-probes.  See AGENTS.md § Environment probe.
    environment = Column(JSON, nullable=True)
    environment_probed_at = Column(DateTime(timezone=True), nullable=True)
    environment_probed_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment_probed_from_ip = Column(String(45), nullable=True)

    # Executing-agent attribution (v2.28.0).  Stamped by the agent on
    # the same call that records the environment probe so users can
    # compare runs across agents/models on the same plan — e.g. the
    # same TestPlan executed by claude-opus-4-7 (claude-code) vs
    # gpt-5-codex.  Plan-generation provenance already lives on
    # ``TestPlan`` (v2.19.0); this is the symmetric session-level
    # surface.  All three nullable because pre-2.28 sessions and
    # bundle-exported runs never report them.
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    # Free-form notes — written by the operator-driven Abandon endpoint
    # (v4 beta.7) so the audit line "[Abandoned by <user> on <ts>]: ..."
    # lives with the row.  Mirrors the same field on ReconSession.
    notes = Column(Text, nullable=True)

    # Relationships
    test_plan = relationship("TestPlan")
    agent = relationship("Agent")
    started_by = relationship("User", foreign_keys=[started_by_id])
    environment_probed_by = relationship(
        "User", foreign_keys=[environment_probed_by_user_id]
    )
    agent_session = relationship("AgentSession")
    test_results = relationship(
        "TestExecutionResult",
        back_populates="execution_session",
        cascade="all, delete-orphan",
    )
    sanity_checks = relationship(
        "HostSanityCheck",
        back_populates="execution_session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_exec_session_plan", "test_plan_id"),
    )


class TestExecutionResult(Base):
    """One test's execution output within an execution session.

    Each row corresponds to one entry in the `proposed_tests` JSON
    array on a TestPlanEntry, identified by `test_index`.  The agent
    records the result after the user approves and runs the command.

    `raw_output` is capped to `TEST_OUTPUT_MAX_BYTES` (default 100KB,
    configurable via .env) to prevent unbounded storage growth from
    verbose tools.

    Results from abandoned sessions are kept — the `execution_session`
    relationship gives consumers access to the session's terminal
    status so they can distinguish complete from incomplete passes.
    """
    __tablename__ = "test_execution_results"

    id = Column(Integer, primary_key=True, index=True)
    execution_session_id = Column(
        Integer,
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_id = Column(
        Integer,
        ForeignKey("test_plan_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_index = Column(Integer, nullable=False)

    status = Column(
        String(20),
        nullable=False,
        default=TestExecutionStatus.PENDING.value,
    )
    command_run = Column(Text)          # actual command (may differ from proposed)
    raw_output = Column(Text)           # capped to TEST_OUTPUT_MAX_BYTES
    findings_summary = Column(Text)
    severity = Column(String(20))       # FindingSeverity value or null
    is_finding = Column(Boolean, nullable=False, default=False)

    executed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # v2.91.0 (code review #2, Option B) — when a result is recorded
    # against an entry that has no passing HostSanityCheck on file,
    # the agent must supply an override_reason at result-record time.
    # The reason is persisted here so the audit trail shows WHICH
    # results were captured without a verified target.  Indexed for
    # the "show me every result that bypassed sanity" query.  Empty
    # for the common case where sanity was verified first.
    sanity_override_reason = Column(String(500), nullable=True, index=True)

    # Relationships
    execution_session = relationship("ExecutionSession", back_populates="test_results")
    entry = relationship("TestPlanEntry")

    __table_args__ = (
        UniqueConstraint(
            "execution_session_id", "entry_id", "test_index",
            name="uq_exec_result_session_entry_test",
        ),
        Index("idx_test_result_entry", "entry_id"),
    )


class HostSanityCheck(Base):
    """Per-host target verification before test execution begins.

    The agent performs a sanity check (reverse DNS, banner grab, etc.)
    on each host before running any tests, and records the result here.
    If `passed` is False, the agent should stop and ask the user for
    guidance rather than proceeding against a potentially wrong target.
    """
    __tablename__ = "host_sanity_checks"

    id = Column(Integer, primary_key=True, index=True)
    execution_session_id = Column(
        Integer,
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_id = Column(
        Integer,
        ForeignKey("test_plan_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    host_id = Column(
        Integer,
        ForeignKey("hosts_v2.id", ondelete="CASCADE"),
        nullable=False,
    )

    method = Column(String(30), nullable=False)   # SanityCheckMethod value
    target_ip = Column(String(45), nullable=False)
    port_checked = Column(Integer)
    expected_value = Column(Text)
    actual_value = Column(Text)
    source_ip = Column(String(45))
    dns_result = Column(String(255))
    passed = Column(Boolean, nullable=False)
    details = Column(Text)

    checked_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    execution_session = relationship("ExecutionSession", back_populates="sanity_checks")
    entry = relationship("TestPlanEntry")
    host = relationship("Host", foreign_keys=[host_id])

    __table_args__ = (
        # One row per (session, entry, method).  The execution workflow
        # explicitly records multiple verification methods per host
        # (network_context, reverse_dns, banner_grab, ...), so the unique
        # key MUST include ``method`` — a (session, entry)-only constraint
        # would 500 the second method recorded for any host.
        UniqueConstraint(
            "execution_session_id", "entry_id", "method",
            name="uq_sanity_check_session_entry_method",
        ),
        Index("idx_sanity_check_session", "execution_session_id"),
    )


# ---------------------------------------------------------------------------
# Imported Result Files (offline bundle execution)
# ---------------------------------------------------------------------------

class ImportedResultFile(Base):
    """Audit row for each results file imported from a remote agent.

    When a test plan is exported as a bundle, the remote agent runs the
    tests offline and returns a results JSON file.  The user uploads it
    via ``POST /test-plans/{id}/import-results``; this table records who
    uploaded it, the bundle id it claimed, and the parse outcome.
    """
    __tablename__ = "imported_result_files"

    id = Column(Integer, primary_key=True, index=True)
    execution_session_id = Column(
        Integer,
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    bundle_id = Column(String(64), nullable=False, index=True)
    imported_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename = Column(String(255))
    file_sha256 = Column(String(64))
    results_count = Column(Integer, nullable=False, default=0)
    sanity_checks_count = Column(Integer, nullable=False, default=0)
    feedback_extracted = Column(Boolean, nullable=False, default=False)
    parse_errors = Column(JSON, nullable=True)
    is_final = Column(Boolean, nullable=False, default=False)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Agent Feedback
# ---------------------------------------------------------------------------

class AgentFeedbackSource(str, enum.Enum):
    PLAN_GENERATION = "plan_generation"
    RECONNAISSANCE = "reconnaissance"
    IN_SESSION_EXECUTION = "in_session_execution"
    EXPORTED_EXECUTION = "exported_execution"
    # v2.85.0 — assist sessions now invite feedback.  Pre-v2.85.0 the
    # assist prompt deliberately omitted the feedback block because the
    # enum lacked an ASSIST value and the read-only "ask a question"
    # shape didn't fit the plan/recon/execution lifecycle.  Now that
    # AgentFeedback carries assist_session_id, the assist prompt closes
    # the same way the others do.
    ASSIST = "assist"


class AgentFeedbackStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class AgentFeedback(Base):
    """Structured feedback submitted by an agent at the end of a prompt.

    Every agent-facing prompt ends with a feedback-request block asking
    the agent to POST one of these.  The record stamps the prompt_version
    so we can compare feedback across prompt revisions.  Entries are
    also extracted from imported bundle result files — in that case
    ``source = exported_execution`` and the record is created during
    import, not by a live ``/agent/feedback`` call.
    """
    __tablename__ = "agent_feedback"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    execution_session_id = Column(
        Integer,
        ForeignKey("execution_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # v2.85.0 — recon / assist linkage.  Pre-v2.85.0 the recon prompt
    # passed ``recon_session_id`` to /agent/feedback but the schema
    # silently dropped it (Pydantic ignored unknown keys), so feedback
    # from the recon and assist workflows could not be filtered by
    # session.  Both columns are nullable — plan-generation feedback
    # uses test_plan_id, execution uses execution_session_id, recon uses
    # recon_session_id, assist uses assist_session_id; the four are
    # mutually exclusive by workflow but the schema enforces nothing.
    recon_session_id = Column(
        Integer,
        ForeignKey("recon_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    assist_session_id = Column(
        Integer,
        ForeignKey("assist_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    source = Column(String(40), nullable=False)
    prompt_version = Column(String(20))
    overall_rating = Column(Integer)   # 1..5, nullable

    api_critiques = Column(JSON)        # list of {endpoint, issue, suggestion}
    tool_suggestions = Column(JSON)     # list of {name, category, rationale}
    friction_notes = Column(Text)
    agent_metrics = Column(JSON)        # {agent_name, model, tokens, ...}

    status = Column(String(20), nullable=False, default=AgentFeedbackStatus.NEW.value)
    reviewed_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = Column(DateTime(timezone=True))
    reviewer_notes = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project = relationship("Project", foreign_keys=[project_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    test_plan = relationship("TestPlan", foreign_keys=[test_plan_id])
    execution_session = relationship("ExecutionSession", foreign_keys=[execution_session_id])
    recon_session = relationship("ReconSession", foreign_keys=[recon_session_id])
    assist_session = relationship("AssistSession", foreign_keys=[assist_session_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])

    __table_args__ = (
        Index("idx_agent_feedback_status", "status"),
        Index("idx_agent_feedback_source", "source"),
        Index("idx_agent_feedback_created_desc", "created_at"),
    )


# ---------------------------------------------------------------------------
# Reconnaissance sessions
# ---------------------------------------------------------------------------
# Added in v2.11.0 to decouple recon from test plan generation.  Before
# this release, clicking "Start Agentic Recon" on the Scopes page created
# a TestPlan and told the agent to fill it with entries — but recon's job
# is to populate *host data*, not a list of things-to-test.  Test plans
# come after recon, once the DB actually knows what's in scope.
#
# A ReconSession tracks one recon run against a scope:
#   - mints a scope-bound agent API key (scope_id on api_keys)
#   - the agent uploads raw scanner output (nmap XML, masscan, gnmap,
#     nessus, eyewitness, etc.) via POST /agent/recon/upload, which
#     wraps the regular ingestion pipeline
#   - the session counts uploads + distinct hosts landed in the scope
#   - the session terminates via POST /agent/recon/complete (optional
#     chain into plan generation)
#
# ReconSession is intentionally separate from ExecutionSession even
# though both represent "an agent is running a workflow and producing
# results over time".  ExecutionSession is tied to test plan entries
# and per-test results; recon has neither.  Forcing them into one
# table would add nullable FKs in both directions and obscure the
# semantics.

class ReconSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ReconSession(Base):
    """One agentic reconnaissance run against a registered scope.

    Created by POST /projects/{id}/scopes/{scope_id}/recon/start, which
    also mints a scope-bound agent API key.  The agent uses the key to
    call /agent/recon/* endpoints — context (what to scan), upload
    (submit tool output), summary (what's been found), complete
    (terminal).
    """
    __tablename__ = "recon_sessions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_id = Column(
        Integer,
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    status = Column(
        String(20),
        nullable=False,
        default=ReconSessionStatus.ACTIVE.value,
    )

    # v2.116.0 — 1:1 link to the unified AgentSession base (see
    # ExecutionSession.agent_session_id).  Nullable during the expand phase.
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Counters updated as uploads succeed.  Not authoritative — the
    # authoritative source is the scan_history rows tagged with this
    # session.  These exist for cheap summary queries.
    uploads_submitted = Column(Integer, default=0, nullable=False)
    scans_ingested = Column(Integer, default=0, nullable=False)
    hosts_discovered = Column(Integer, default=0, nullable=False)
    ports_discovered = Column(Integer, default=0, nullable=False)

    # Free-form notes from the agent at completion time (summary of
    # what it ran, what it found, any manual interventions).  Capped
    # by the API schema; no max_length here because other text fields
    # in this table also omit it and rely on API-layer caps.
    notes = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Environment probe (v2.23.0).  Same shape and intent as
    # ExecutionSession.environment — filled by the agent on first
    # contact via POST /agent/recon/sessions/{id}/environment so
    # /context can echo it and the agent picks scan flavour from what
    # is actually available.  Recon and execution probes live on their
    # own session rows because the operator may run them from
    # different machines (e.g. recon from Kali, execution from
    # Windows).
    environment = Column(JSON, nullable=True)
    environment_probed_at = Column(DateTime(timezone=True), nullable=True)
    environment_probed_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment_probed_from_ip = Column(String(45), nullable=True)

    # Executing-agent attribution (v2.30.0).  Mirrors v2.28.0's
    # addition to ExecutionSession so cross-workflow comparison
    # ("everything claude-opus-4-7 did on this project") sees
    # symmetric data across recon and execution.  Stamped by the
    # agent on the same call that records the environment probe.
    # Nullable because pre-2.30 sessions and any agent that doesn't
    # report attribution leave them empty.
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    # Relationships
    project = relationship("Project", foreign_keys=[project_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    started_by = relationship("User", foreign_keys=[started_by_id])
    environment_probed_by = relationship(
        "User", foreign_keys=[environment_probed_by_user_id]
    )
    agent_session = relationship("AgentSession")

    __table_args__ = (
        Index("idx_recon_session_scope", "scope_id"),
        Index("idx_recon_session_project", "project_id"),
        Index("idx_recon_session_status", "status"),
    )


# ---------------------------------------------------------------------------
# Assist sessions (v2.64.0)
# ---------------------------------------------------------------------------
#
# Fourth agent surface — read-only, project-scoped, short-TTL.  The
# recon/plan/execution workflows assume an operator wants to commit to
# a full pipeline.  Assist sessions are the lightweight alternative
# for senior testers who want to ask custom questions ("which hosts
# expose FTP?", "summarize my critical findings for project X") without
# minting a plan key and triggering plan-approval ceremony.
#
# Scope: read-only via /agent/assist/* endpoints.  No execution
# authority, no test-plan creation, no host follow mutation in v1
# (those are tracked as future work — see CHANGELOG).
#
# Audit: like recon/execution sessions, every /agent/assist/* call is
# captured in agent_api_calls; ``assist_session_id`` is the new
# attribution column for filtering.

class AssistSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"
    EXPIRED = "expired"


class AssistSession(Base):
    """One interactive assist session against a project.

    Created by POST /projects/{id}/assist/start, which also mints a
    project-bound, read-only agent API key.  The agent uses the key
    to call /agent/assist/* endpoints — host queries, project
    context, single-host detail.  Recon, plan, and execution keys
    are all rejected by /agent/assist/* (and vice-versa).
    """
    __tablename__ = "assist_sessions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    status = Column(
        String(20),
        nullable=False,
        default=AssistSessionStatus.ACTIVE.value,
    )

    # v2.116.0 — 1:1 link to the unified AgentSession base (see
    # ExecutionSession.agent_session_id).  Nullable during the expand phase.
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Free-form description from the human at start time — "looking
    # for FTP exposure", "writing up critical findings", etc.  Useful
    # for the audit log so a reviewer can see why each assist session
    # was opened.
    purpose = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # Refreshed by the audit middleware on every successful assist
    # API call.  Lets the UI show "session has been idle for 47m"
    # without scanning agent_api_calls.
    last_activity_at = Column(DateTime(timezone=True), nullable=True)

    # Environment probe — same shape as ReconSession.environment.
    # Optional: the assist agent's commands are read-only API calls,
    # not shell invocations, so probe matters less than for execution
    # or recon.  Kept for symmetry with the other workflows and
    # because future assist features (bulk follow, scan-from-filter)
    # may need it.
    environment = Column(JSON, nullable=True)
    environment_probed_at = Column(DateTime(timezone=True), nullable=True)
    environment_probed_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment_probed_from_ip = Column(String(45), nullable=True)

    # Executing-agent attribution — parallel to ReconSession.
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    # Relationships
    project = relationship("Project", foreign_keys=[project_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    started_by = relationship("User", foreign_keys=[started_by_id])
    environment_probed_by = relationship(
        "User", foreign_keys=[environment_probed_by_user_id]
    )
    agent_session = relationship("AgentSession")

    __table_args__ = (
        Index("idx_assist_session_project", "project_id"),
        Index("idx_assist_session_status", "status"),
    )


# ---------------------------------------------------------------------------
# Agent API call log (v2.24.0)
# ---------------------------------------------------------------------------
#
# Every HTTP request that hits /agent/* with a valid agent API key is
# recorded here so a human reviewer can answer "what did the agent
# actually do, in what order, against which hosts?".  Written from a
# Starlette middleware AFTER the response is sent, so this never adds
# latency to the agent's request loop.
#
# Captured: method, resolved path, query, status, duration, the
# referenced host_ids / entry_ids / target_ips parsed out of the path
# + query + body.  Bodies are captured for mutations only (GET/HEAD
# skip), capped to keep storage bounded, and never include the raw API
# key (we strip Authorization + X-API-Key before storing).
#
# NOT captured: response bodies (size only).  Adding response-body
# digests for high-signal endpoints is a follow-up.

class AgentApiCall(Base):
    """One inbound agent API request, captured for audit + debug review.

    Indexed by (agent_id, created_at), (test_plan_id, created_at), and
    (recon_session_id, created_at) for fast per-workflow timelines.
    """
    __tablename__ = "agent_api_calls"

    id = Column(BigInteger, primary_key=True)

    # Who/where (only populated when the request authenticated as an agent).
    # v2.44.5 — nullable so we can record agent-path 5xx that crashed
    # before/during auth (the request never had an agent_id to record);
    # `error_class` distinguishes these from rows with NULL agent_id
    # for some other reason.
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    api_key_id = Column(
        Integer,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_prefix = Column(String(16), nullable=True)  # nm_agent_xxxx — never the raw key
    source_ip = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    # Workflow association — populated from the scoped key + parsed path.
    # Nullable (v2.44.5) for the same pre-auth 5xx case as agent_id.
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    test_plan_id = Column(
        Integer,
        ForeignKey("test_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_session_id = Column(
        Integer,
        ForeignKey("execution_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_id = Column(
        Integer,
        ForeignKey("scopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    recon_session_id = Column(
        Integer,
        ForeignKey("recon_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # v2.64.0 — assist-session attribution.  Parallel to the columns
    # above; populated by the audit middleware from
    # request.state.scoped_assist_session_id on /agent/assist/* calls.
    # Nullable for the same reason as the other workflow columns
    # (only one is set per row).
    assist_session_id = Column(
        Integer,
        ForeignKey("assist_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The call itself
    method = Column(String(8), nullable=False)        # GET / POST / PATCH / DELETE
    path = Column(Text, nullable=False)               # /api/v1/agent/test-plans/12/context
    path_template = Column(Text, nullable=True)       # /agent/test-plans/{plan_id}/context
    path_params = Column(JSON, nullable=True)         # {"plan_id": 12}
    query_params = Column(JSON, nullable=True)        # {"detail_level": "brief"}
    request_body_summary = Column(JSON, nullable=True)  # only for non-GET, ≤8KB
    status_code = Column(Integer, nullable=False)
    response_bytes = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=False)
    # v2.44.5 — populated by the audit middleware for 5xx responses
    # when the global exception handler stashed an exception class
    # on request.state.  NULL for 2xx/4xx (no exception) and for
    # 5xx that occurred before/after the global handler ran (rare).
    # Lets operators `WHERE error_class IS NOT NULL` to grep the
    # audit log for crash-cased requests SQL-side.
    error_class = Column(String(64), nullable=True, index=True)

    # Host-touched index — the answer to "did the agent query the right
    # hosts?".  Parsed from the path params, query, and request body by
    # the middleware.  Arrays so a single multi-host call (e.g. /context
    # with ?host_ids=1,2,3) tags all of them.  ARRAY(Integer) only works
    # on Postgres; on SQLite we use JSON for the test suite.
    referenced_host_ids = Column(JSON, nullable=True)
    referenced_entry_ids = Column(JSON, nullable=True)
    referenced_target_ips = Column(JSON, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    agent = relationship("Agent", foreign_keys=[agent_id])
    api_key = relationship("APIKey", foreign_keys=[api_key_id])

    __table_args__ = (
        Index("idx_agent_api_call_agent_created", "agent_id", "created_at"),
        Index("idx_agent_api_call_plan_created", "test_plan_id", "created_at"),
        Index("idx_agent_api_call_recon_created", "recon_session_id", "created_at"),
        Index("idx_agent_api_call_exec_created", "execution_session_id", "created_at"),
        Index("idx_agent_api_call_project_created", "project_id", "created_at"),
        # v2.50.1 — enforce the agent_id+project_id-or-error_class
        # contract at the DB level.  The columns were relaxed to
        # nullable in f9e2d471a8c6 to record pre-auth 5xx (the request
        # crashed before we knew which agent/project it belonged to);
        # ``error_class`` is the discriminator that says "this row is a
        # pre-auth failure row, not a regular agent call with a
        # missing FK".  Without this CHECK, a future code path that
        # forgets to populate either pair produces orphan rows the
        # activity-tab can't filter.
        CheckConstraint(
            "(agent_id IS NOT NULL AND project_id IS NOT NULL) "
            "OR error_class IS NOT NULL",
            name="ck_agent_api_calls_attribution_or_error",
        ),
    )
