"""Shared request/response schemas for the test-plan endpoints.

Extracted from ``app/api/v1/endpoints/test_plans.py`` (CLAUDE.md file-size
policy — that router was 2,487 LOC).  These are the reused schemas the router
shares across multiple handlers; single-use response models that are defined
immediately before their one endpoint stay inline in the router, co-located
with their sole consumer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.schemas import ProposedTestItem
from app.db.models_agent import TestEntryPriority, TestPhase, TestEntryStatus


class TestPlanEntryResponse(BaseModel):
    id: int
    host_id: int
    host_ip: Optional[str] = None
    host_hostname: Optional[str] = None
    priority: str
    test_phase: str
    proposed_tests: List[ProposedTestItem]
    rationale: str
    status: str
    findings: Optional[str] = None
    results_data: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    assigned_to_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestPlanSummary(BaseModel):
    id: int
    project_id: int
    version: int
    title: str
    description: Optional[str] = None
    status: str
    agent_name: Optional[str] = None
    created_by_username: Optional[str] = None
    entry_count: int = 0
    completion_pct: float = 0.0
    approved_by_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    rejected_by_id: Optional[int] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    # Generation provenance (v2.19.0).  Stamped by the agent during plan
    # generation; null on plans created before the feature or where the
    # agent skipped the PATCH step.
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    # Source provenance (v3 alpha.3).  Tells the UI where the plan came
    # from — a recon run, a manual host set, or a filter expression.
    # Pre-alpha.3 plans land as ``'unspecified'`` and the UI renders
    # "(provenance not recorded)".  Only one of the *_id / *_ids fields
    # is populated, discriminated by source_kind.
    source_kind: str = "unspecified"
    source_recon_session_id: Optional[int] = None
    source_host_ids: Optional[List[int]] = None
    source_plan_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    # Timestamp of the most recent plan-generation agent call against
    # this plan (from the agent_api_calls audit log), or None if the
    # agent has never called in.  Filter excludes execution calls so
    # this reflects generation activity specifically.  Mirrors the
    # execution + recon session fields of the same name.
    last_activity_at: Optional[datetime] = None
    # Server-side "looks interrupted" judgment for plan generation —
    # true when status is ``draft`` AND the agent has been silent for
    # the 15-minute threshold.  Plans in any post-draft status (the
    # human's turn, or execution has begun) never report stale.
    is_stale: bool = False


class ApiKeyStatus(BaseModel):
    """Per-plan API-key status surfaced on TestPlanDetail.

    Lets the UI show the user whether the agent key is still alive and
    how long it has left, and gate a "Regenerate key" affordance behind
    expiry.  ``has_key`` is false on plans that were created without an
    agent (manual plans) or whose key rows were never persisted.
    """
    has_key: bool = False
    is_active: bool = False
    expires_at: Optional[datetime] = None
    # Seconds until expiry; negative once expired.  Easier for the UI
    # than re-deriving it from `expires_at` + current time.
    expires_in_seconds: Optional[int] = None
    key_prefix: Optional[str] = None  # first 14 chars; not the secret


class ExecutionSessionSummary(BaseModel):
    """Snapshot of an ExecutionSession for a plan (v2.28.0).

    A plan can be executed multiple times — different users, different
    agent models, different terminal hosts — so this is one row in a
    list.  TestPlanDetail surfaces the latest one inline plus a count
    of total sessions; full list is at
    ``GET /test-plans/{plan_id}/execution-sessions``.
    """
    id: int
    status: str
    mode: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Who started this session — UI uses it to disambiguate when two
    # users have each run the same plan from their own agent keys.
    started_by_username: Optional[str] = None
    agent_name: Optional[str] = None
    # Executing-agent attribution (v2.28.0) — stamped by the agent
    # via the environment-probe call.  Lets the UI distinguish runs
    # by model/tool so a user can compare claude-opus vs gpt-5-codex
    # on the same plan.
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    # Environment probe summary, when one has been recorded.
    environment_os_family: Optional[str] = None
    environment_shell: Optional[str] = None
    environment_probed_at: Optional[datetime] = None
    # Timestamp of the most recent agent API call against this session
    # (from the agent_api_calls audit log), or None if the agent never
    # called in.  An `active` session with no recent activity is likely
    # interrupted — the UI uses this to surface a Resume affordance.
    last_activity_at: Optional[datetime] = None
    # Server-side "looks interrupted" judgment — true when the session is
    # ``active`` AND has been silent for ``_STALE_THRESHOLD_SECONDS``.
    # Computed server-side specifically so it doesn't drift against the
    # browser clock: a client computing ``Date.now() - started_at`` was
    # subject to operator clock skew, which could push the threshold
    # crossing minutes off the real elapsed time (in either direction).
    is_stale: bool = False


class ExecutionSessionList(BaseModel):
    """Wrapper response for ``/test-plans/{id}/execution-sessions`` (v2.28.0).

    Wrapped (rather than a bare ``List[...]``) so the UI can grow a
    paging cursor or rollup counts later without breaking the contract.
    """
    plan_id: int
    sessions: List[ExecutionSessionSummary] = Field(default_factory=list)
    total: int = 0


class TestPlanDetail(TestPlanSummary):
    entries: List[TestPlanEntryResponse] = Field(default_factory=list)
    # v2.85.0 — entries pagination.  ``entries_total`` is always the full
    # row count so the frontend can decide whether to fetch more pages.
    # ``entries_skip`` / ``entries_limit`` echo back the slice the caller
    # actually got; both null when the caller did not pass entries_limit
    # (legacy behaviour: full entry list returned).
    entries_total: int = 0
    entries_skip: Optional[int] = None
    entries_limit: Optional[int] = None
    new_hosts_since_creation: int = 0
    # The filters the user picked when generating the plan — surfaced so a
    # reviewer can see how the candidate host set was narrowed.  Null on
    # manual plans (no filters) and plans created before the column existed.
    filter_criteria: Optional[Dict[str, Any]] = None
    # Per-plan agent-key status.  Driven by the most-recent APIKey row
    # bound to this plan; used by the UI to show TTL and offer a rotate.
    api_key: ApiKeyStatus = Field(default_factory=ApiKeyStatus)
    # v2.28.0 — latest execution session metadata so TestPlanDetail can
    # show "your agent ran from a Kali host 2 hours ago" without a
    # follow-up fetch.  None when the plan has never been /execute'd.
    latest_execution_session: Optional[ExecutionSessionSummary] = None
    # v2.28.0 — total execution sessions for this plan (a plan can be
    # run multiple times by different users / agents / models).  Drives
    # the UI's "N executions recorded" affordance + session picker on
    # the Test Results panel.  Zero when never executed.
    execution_session_count: int = 0


class TestPlanProgress(BaseModel):
    plan_id: int
    total_entries: int
    by_status: Dict[str, int]
    by_priority: Dict[str, int]
    by_phase: Dict[str, int]
    completion_pct: float
    hosts_tested: int
    hosts_remaining: int


class TestPlanHistoryItem(BaseModel):
    id: int
    entry_id: Optional[int] = None
    actor_type: str
    actor_id: int
    action: str
    field_changed: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPlanCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=4096)


class PlanMetadataUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=4096)


class RejectRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=2048)


class ArchiveRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=2048)


class EntryCreate(BaseModel):
    host_id: int
    priority: TestEntryPriority
    test_phase: TestPhase
    proposed_tests: List[ProposedTestItem]
    rationale: str = Field(..., max_length=4096)
    notes: Optional[str] = Field(None, max_length=8192)


class EntryBatch(BaseModel):
    entries: List[EntryCreate] = Field(..., max_length=500)


class EntryUpdate(BaseModel):
    priority: Optional[TestEntryPriority] = None
    test_phase: Optional[TestPhase] = None
    proposed_tests: Optional[List[ProposedTestItem]] = None
    rationale: Optional[str] = Field(None, max_length=4096)
    status: Optional[TestEntryStatus] = None
    findings: Optional[str] = Field(None, max_length=16384)
    results_data: Optional[dict] = None
    notes: Optional[str] = Field(None, max_length=8192)
    assigned_to_id: Optional[int] = None
    expected_updated_at: Optional[datetime] = None
