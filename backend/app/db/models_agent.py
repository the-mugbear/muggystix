"""
AI Agent Models

Models for AI agent identity, sessions, feedback and the API-call audit log.
Agents are project-scoped entities that authenticate via API key.

The test plan, plan entry, plan history, execution session, execution result,
sanity check and imported-result-file models lived here until v2.442.0, when
host tests (``models_host_tests``) and evidence records (``models_proposals``)
replaced them (migration ``f4b8d2a6c917`` drops the tables).
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

class ActorType(str, enum.Enum):
    USER = "user"
    AGENT = "agent"


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

    __table_args__ = (
        UniqueConstraint("project_id", "owner_id", name="uq_agent_per_user_project"),
    )


class AgentSessionWorkflow(str, enum.Enum):
    """What kind of session a row is.

    v2.337.0 — ``PROJECT`` is the only kind new sessions get.  One
    project-scoped session lets the same key query the inventory, upload
    scanner output, propose host tests and record evidence (v2.433.0 removed
    recon runs; v2.442.0 plans and execution runs).  The legacy values remain as
    LABELS on rows minted before the consolidation so history still reads
    correctly — nothing gates on them any more (see ``deps.get_current_agent``).
    """
    PROJECT = "project"
    PLAN_GENERATION = "plan_generation"
    EXECUTION = "execution"
    ASSIST = "assist"


# v2.309.0 — the capability vocabulary is gone: ``AgentCapability``,
# ``AgentCapabilityConstraint``, ``LEGACY_WRITE_CAPABILITIES`` and
# ``ASSIST_GRANTABLE_CAPABILITIES`` all lived here.
#
# They were a second authorization model beside the product's RBAC, and only
# assist consulted them — the other three workflows resolved to
# ``LEGACY_WRITE_CAPABILITIES`` unconditionally, which is the tell that the
# model was grandfathered rather than chosen. An agent key now does what its
# operator may do, checked per request against the same project roles a person
# is checked against.
#
# The deliberate consequence: an operator can no longer start a read-only
# assist session. Read-only was the *default* rather than a choice, nothing was
# left to drain (every assist session was already ended), and auditors and
# viewers still get read-only agents — because that is what they can do.


class AgentSession(Base):
    """The operator's agent session — one key, one project (v2.337.0).

    An agent API key points at exactly one ``AgentSession``
    (``api_keys.agent_session_id``).  The session is bound to a project and
    to the operator who started it; everything the key may do is that
    operator's project role, checked per request (``enforce_agent_operator_access``).

    What the agent *did* is recorded on the rows it wrote, each linked back
    here through ``agent_session_id``: an upload is an ``IngestionJob``, a
    proposed test a ``HostTest``, what it ran an ``EvidenceRecord``, a change
    it suggests an ``AgentProposal``, a note an ``Annotation``.  ``workflow``
    is ``project`` for every session minted since the consolidation; the
    legacy per-workflow values survive on older rows as labels.

    Shared lifecycle state (status, timestamps, the agent/model attribution,
    the operator's stated purpose, notes) lives here.  A host test and an
    evidence record keep their own copy of the attribution, snapshotted when
    written (a session can switch models).  The environment probe columns
    went in v2.434.0.

    **This id is the session's only id** (v2.449.0).  Until then every start
    also wrote an ``assist_sessions`` row whose id the review routes were keyed
    by; migration ``b8e2a5c7d1f3`` folded that table in and dropped it.
    ``legacy_assist_session_id`` keeps the old row's id for the sessions that
    had one, read only to send an old ``/assist-sessions/{id}`` link to its
    session — nothing is written to it and nothing else is keyed by it.
    """
    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True, index=True)
    workflow = Column(String(20), nullable=False)  # AgentSessionWorkflow; indexed via idx_agent_session_workflow_status

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )  # indexed via idx_agent_session_project
    agent_id = Column(
        Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    started_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    status = Column(String(20), nullable=False, default="active")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    # Unified completion timestamp (Assist's "ended_at" maps here).
    completed_at = Column(DateTime(timezone=True))
    # v2.343.0 — HOW the session ended: 'agent' (POST /agent/session/end),
    # 'operator' (End on Agent Runs / the sessions panel), or 'lapsed' (the
    # hourly sweep, after the key expired past the renewal window).  A typed
    # column rather than a parse of ``notes``: the whole point is to count
    # clean exits against abandoned ones, and a count belongs on a column
    # (CLAUDE.md column-vs-blob policy).  NULL on an active row.
    end_reason = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # v2.337.0 — what the operator said they were opening the session for
    # ("looking for FTP exposure", "recon the DMZ then draft a plan").  Shown
    # on the sessions page so a reviewer sees why, not only what.  Moved here
    # from ``assist_sessions.purpose``.
    purpose = Column(Text, nullable=True)
    # Refreshed by the audit middleware on every authenticated call, so the
    # UI can show "idle for 47m" without scanning agent_api_calls.
    last_activity_at = Column(DateTime(timezone=True), nullable=True)

    # v2.309.0 — ``capabilities`` and ``capability_constraint`` dropped
    # (migration f1a6c92d4b70). A session's authority is its operator's project
    # role, resolved per request, so there is no per-session grant to store.

    # Executing-agent attribution (v2.434.0): the model the agent last
    # self-reported, its client from the MCP handshake (User-Agent fallback),
    # and the prompt version the server issued.
    generated_by_model = Column(String(100), nullable=True)
    generated_by_tool = Column(String(100), nullable=True)
    prompt_version = Column(String(20), nullable=True)

    notes = Column(Text, nullable=True)

    # The id this session's ``assist_sessions`` row had (dropped in
    # b8e2a5c7d1f3).  NULL on every session started since.
    legacy_assist_session_id = Column(Integer, nullable=True)

    # Relationships
    project = relationship("Project")
    agent = relationship("Agent")
    started_by = relationship("User", foreign_keys=[started_by_id])

    __table_args__ = (
        Index("idx_agent_session_project", "project_id"),
        Index("idx_agent_session_workflow_status", "workflow", "status"),
        Index("uq_agent_session_legacy_assist", "legacy_assist_session_id", unique=True),
    )


# ---------------------------------------------------------------------------
# Agent Feedback
# ---------------------------------------------------------------------------

class AgentFeedbackStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class AgentFeedback(Base):
    """Feedback an agent files at the moment something gets in its way.

    The session prompt and the MCP opening both ask for it.  The record
    stamps the prompt_version so feedback can be compared across prompt
    revisions.  A row carries no label for the kind of work it is about:
    sessions have no kinds, and the ``source`` column that held one was
    dropped by revision ``a6d3b1e8c5f7``.
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
    # v2.337.0 — the session the feedback came from, stamped from the key
    # rather than the body.  The one session link (``assist_session_id`` was
    # folded into it by b8e2a5c7d1f3; ``test_plan_id`` /
    # ``execution_session_id`` went with their tables in v2.442.0).
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

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
    agent_session = relationship("AgentSession", foreign_keys=[agent_session_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])

    __table_args__ = (
        Index("idx_agent_feedback_status", "status"),
        Index("idx_agent_feedback_created_desc", "created_at"),
    )


# ---------------------------------------------------------------------------
# (The ``assist_sessions`` table and its ``AssistSession`` model lived here
# from v2.64.0.  Migration b8e2a5c7d1f3 folded each row into its
# ``AgentSession`` and dropped the table — see ``legacy_assist_session_id``.)
# ---------------------------------------------------------------------------


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
# referenced host_ids / target_ips parsed out of the path
# + query + body.  Bodies are captured for mutations only (GET/HEAD
# skip), capped to keep storage bounded, and never include the raw API
# key (we strip Authorization + X-API-Key before storing).
#
# NOT captured: response bodies (size only).  Adding response-body
# digests for high-signal endpoints is a follow-up.

class AgentApiCall(Base):
    """One inbound agent API request, captured for audit + debug review.

    Indexed by (agent_id, created_at), (agent_session_id, created_at) and
    (project_id, created_at) for fast timelines.
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

    # Workflow association — populated from the key's session + parsed path.
    # Nullable (v2.44.5) for the same pre-auth 5xx case as agent_id.
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # v2.337.0 — the session the key belongs to.  The one attribution every
    # authenticated call carries; the phase ids below are filled when the
    # call's path or body names a phase (or the session has exactly one
    # active phase of that kind).
    agent_session_id = Column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )  # indexed via idx_agent_api_call_session_created
    # (``test_plan_id`` / ``execution_session_id`` went with their tables in
    # v2.442.0; the path and its parameters still say what a call was about.)
    scope_id = Column(
        Integer,
        ForeignKey("scopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    # (``assist_session_id`` — the v2.64.0 assist attribution — was folded into
    # ``agent_session_id`` by b8e2a5c7d1f3: one session column.)

    # The call itself
    method = Column(String(8), nullable=False)        # GET / POST / PATCH / DELETE
    path = Column(Text, nullable=False)               # /api/v1/agent/host-tests/12
    path_template = Column(Text, nullable=True)       # /agent/host-tests/{test_id}; NULL = no route matched
    path_params = Column(JSON, nullable=True)         # {"test_id": 12}
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

    # v2.331.0 — True when the call arrived through the MCP transport's
    # in-process loopback, False for direct HTTP (curl / a script), NULL for
    # rows written before the column existed.  Set from a server-side
    # contextvar, never a header, so a direct client cannot claim it.  This is
    # what the assist-session list reads to say "connected over MCP" from an
    # observed authenticated call rather than from the environment probe.
    via_mcp = Column(Boolean, nullable=True)

    # Host-touched index — the answer to "did the agent query the right
    # hosts?".  Parsed from the path params, query, and request body by
    # the middleware.  Arrays so a single multi-host call (e.g. /context
    # with ?host_ids=1,2,3) tags all of them.  ARRAY(Integer) only works
    # on Postgres; on SQLite we use JSON for the test suite.
    # (``referenced_entry_ids`` — test-plan entry ids — was dropped by
    # f5c2a0d7b4e6: test plans went in v2.442.0.)
    referenced_host_ids = Column(JSON, nullable=True)
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
        Index("idx_agent_api_call_project_created", "project_id", "created_at"),
        # The session page's feed and its per-session counts: one session's
        # calls, newest first.
        Index("idx_agent_api_call_session_created", "agent_session_id", "created_at"),
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


class McpToolCall(Base):
    """One MCP request, recorded at the transport layer (v2.275.0).

    ``agent_api_calls`` only sees requests that reach ``/agent/*``, so everything
    the MCP layer rejects — an unknown tool, arguments that don't fit the
    schema, a refused batch, a bad protocol version — left **no trace anywhere**.
    That blind spot hid a real defect: for two releases the environment-probe
    tool rejected the very fields the assist prompt tells agents to send, which
    blocked every conforming agent, and nothing in the system could show it.

    This is the telemetry that answers the questions the agent-feedback loop can
    only ask about: which tools are never called, which fail repeatedly, which
    arguments agents keep getting wrong, and which clients are connecting.

    **Deliberately FK-free.**  It is transport diagnostics, not project data: it
    has to survive the session, key, and project it describes (the most useful
    question is "why were agents failing last week", asked after the fact).
    Attribution is by ``api_key_prefix``, which maps to a session in practice
    without holding key material or a reference that cascades away.
    """
    __tablename__ = "mcp_tool_calls"

    id = Column(BigInteger, primary_key=True)

    # JSON-RPC method: initialize / tools/list / tools/call / ping / …
    rpc_method = Column(String(64), nullable=True, index=True)
    # Populated for tools/call only — including calls naming a tool that does
    # not exist, which is exactly the signal we want to see.
    tool_name = Column(String(64), nullable=True, index=True)

    # ok            — the tool ran and returned success
    # tool_error    — the tool ran and the endpoint refused it (401/403/404/…)
    # protocol_error— malformed request, unknown tool, bad arguments (-326xx)
    # rejected      — refused by a transport guard before dispatch
    #                 (origin, body cap, batch cap, protocol version)
    outcome = Column(String(16), nullable=False, index=True)
    # JSON-RPC error code, or the HTTP status for transport rejections.
    error_code = Column(Integer, nullable=True)
    detail = Column(String(500), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Who — prefix only, never the raw key.  NULL for unauthenticated requests,
    # which are themselves signal (a client that never got a working key).
    api_key_prefix = Column(String(16), nullable=True, index=True)
    source_ip = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    # From initialize's clientInfo — which client, on which protocol revision.
    client_name = Column(String(128), nullable=True)
    client_version = Column(String(64), nullable=True)
    protocol_version = Column(String(32), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # "What went wrong lately" and "which tool is failing" are the two
        # queries this table exists to answer.
        Index("idx_mcp_tool_call_outcome_created", "outcome", "created_at"),
        Index("idx_mcp_tool_call_tool_created", "tool_name", "created_at"),
    )


class AgentRateBucket(Base):
    """Per-agent request counter for one fixed rate-limit window.

    v2.300.0.  Rate limiting used to take ``max()`` of two counts, neither of
    which could enforce a shared limit:

    * a COUNT over ``agent_api_calls``, whose rows are written by a
      **post-response** BackgroundTask — so the count lagged every request
      currently in flight, and read 0 outright if the background writer was
      failing, i.e. it failed open exactly when it mattered;
    * an in-process deque, which exists only inside one Uvicorn worker.

    Production runs four workers, so a burst spread across them could exceed
    ``rate_limit_rpm`` before a single audit row landed — and adding workers
    widened the gap, meaning the limit weakened as the deployment scaled.

    This row is the shared state that fixes it.  Admission does one
    ``INSERT … ON CONFLICT DO UPDATE … RETURNING count``, which is atomic
    across workers: Postgres serializes concurrent upserts of the same row, so
    every caller gets a distinct, increasing count and capacity is *reserved*
    at admission rather than inferred afterwards.  Enforcement no longer
    depends on the audit log, which goes back to being purely an audit log.

    Fixed window rather than sliding: a sliding window needs either per-request
    timestamps (the deque — per-worker again) or a second bucket plus
    interpolation.  The trade is the standard one — up to 2x ``rate_limit_rpm``
    across a window boundary — which is an acceptable abuse ceiling on a
    trusted-operator surface, and is *bounded*, unlike what it replaces.
    """
    __tablename__ = "agent_rate_buckets"

    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: Start of the window this row counts, truncated to the window size.
    #: Part of the key, so a new window is a new row rather than a
    #: read-then-reset (which would be a race of its own).
    window_start = Column(DateTime(timezone=True), primary_key=True)
    count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        # Housekeeping deletes by age; without this the sweep scans the table.
        Index("idx_agent_rate_bucket_window", "window_start"),
    )
