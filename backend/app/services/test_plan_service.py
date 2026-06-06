"""
Test Plan Service

Business logic for creating, updating, and managing test plans and their
per-host entries.  Used by both agent-facing and user-facing endpoints.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Host
from app.db.models_agent import (
    TestPlan, TestPlanEntry, TestPlanHistory,
    TestPlanStatus, TestEntryStatus, TestExecutionResult,
    ExecutionSession,
)

logger = logging.getLogger(__name__)


class TestPlanService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Test Plan CRUD
    # ------------------------------------------------------------------

    def create_plan(
        self,
        project_id: int,
        agent_id: Optional[int],
        title: str,
        description: Optional[str] = None,
        actor_type: str = "agent",
        actor_id: int = 0,
        created_by_user_id: Optional[int] = None,
        filter_criteria: Optional[dict] = None,
        commit_after: bool = True,
        # v3 alpha.3: typed source-provenance.  Optional and mutually
        # exclusive — the API layer validates and passes exactly one
        # of (source_recon_session_id, source_host_ids, source_plan_id)
        # along with the matching ``source_kind``.  Callers that don't
        # set ``source_kind`` leave the plan as ``UNSPECIFIED`` (the
        # column default) and a ``filter_criteria``-only plan becomes
        # an implicit ``filter_set`` only when the caller explicitly
        # passes that kind.
        source_kind: Optional[str] = None,
        source_recon_session_id: Optional[int] = None,
        source_host_ids: Optional[List[int]] = None,
        source_plan_id: Optional[int] = None,
    ) -> TestPlan:
        """Create a new test plan with a per-project monotonically
        increasing version number.

        The version is computed as ``max(existing) + 1`` for the
        project.  Two concurrent calls can compute the same value, so
        the loop below retries on the unique constraint violation
        ``uq_test_plan_project_version`` (added by an idempotent
        migration in db/init.py).  In practice this almost never
        retries because plan creation is human-triggered and rare.

        Each attempt runs inside a SAVEPOINT (``db.begin_nested``) so a
        version collision rolls back only this attempt, not the caller's
        outer transaction.  This matters for ``/generate``, which adds a
        per-plan api_key row alongside the plan and needs the key insert
        to survive plan retries.

        ``commit_after=False`` leaves the session uncommitted so the
        caller can add more rows (e.g. the api_key) and commit them
        atomically with the plan.
        """
        # Cap retries so a runaway loop can't pin a worker forever.
        # Three attempts is enough to handle realistic concurrency
        # (two users hitting Generate within ms of each other) without
        # masking a real bug.
        plan: Optional[TestPlan] = None
        for attempt in range(3):
            max_version = (
                self.db.query(TestPlan.version)
                .filter(TestPlan.project_id == project_id)
                .order_by(TestPlan.version.desc())
                .first()
            )
            version = (max_version[0] + 1) if max_version else 1

            candidate = TestPlan(
                project_id=project_id,
                agent_id=agent_id,
                created_by_user_id=created_by_user_id,
                version=version,
                title=title,
                description=description,
                status=TestPlanStatus.DRAFT.value,
                filter_criteria=filter_criteria,
                # v3 alpha.3 — typed source-provenance.  Default
                # 'unspecified' is applied by the column default when
                # source_kind is None; otherwise stamp the discriminator
                # and the matching payload column.  No DB-side
                # exclusivity check; the API layer enforces it.
                **(
                    {"source_kind": source_kind}
                    if source_kind is not None
                    else {}
                ),
                source_recon_session_id=source_recon_session_id,
                source_host_ids=source_host_ids,
                source_plan_id=source_plan_id,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(candidate)
                    self.db.flush()
            except IntegrityError:
                # Savepoint rolled back this attempt; sibling pending
                # changes in the outer transaction are untouched.
                logger.info(
                    "Test plan version race in project %d (attempt %d/3) — retrying",
                    project_id, attempt + 1,
                )
                continue

            plan = candidate
            break

        if plan is None:
            raise RuntimeError(
                f"Could not allocate a unique version for a new test plan in "
                f"project {project_id} after 3 attempts.  This indicates very "
                f"high write contention or a missing uq_test_plan_project_version "
                f"index — check the schema migrations."
            )

        self._record_history(plan.id, None, actor_type, actor_id, "created")
        if commit_after:
            self.db.commit()
            self.db.refresh(plan)
        return plan

    def get_plan(self, plan_id: int, project_id: int) -> Optional[TestPlan]:
        return (
            self.db.query(TestPlan)
            .filter(TestPlan.id == plan_id, TestPlan.project_id == project_id)
            .first()
        )

    def list_plans(
        self,
        project_id: int,
        status_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TestPlan]:
        # v2.43.0 — UX review #6: server-side search + limit so the
        # CommandPalette doesn't have to fetch every plan and client-side
        # filter.  ILIKE used so the match is case-insensitive on
        # Postgres; SQLite (tests) folds it to plain LIKE.
        q = self.db.query(TestPlan).filter(TestPlan.project_id == project_id)
        if agent_id is not None:
            q = q.filter(TestPlan.agent_id == agent_id)
        if status_filter:
            q = q.filter(TestPlan.status == status_filter)
        if search and search.strip():
            needle = f"%{search.strip()}%"
            q = q.filter(TestPlan.title.ilike(needle))
        q = q.order_by(TestPlan.created_at.desc())
        if limit is not None and limit > 0:
            q = q.limit(limit)
        return q.all()

    def update_plan(
        self,
        plan: TestPlan,
        actor_type: str,
        actor_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        # v2.19.0: agent self-reported generation provenance.  Set once by
        # the agent during the plan-generation PATCH step; the service does
        # not over-write a previously-set value with NULL (the agent might
        # re-PATCH later for description-only edits).
        generated_by_model: Optional[str] = None,
        generated_by_tool: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> TestPlan:
        if title is not None and title != plan.title:
            self._record_history(
                plan.id, None, actor_type, actor_id, "updated",
                "title", plan.title, title,
            )
            plan.title = title
        if description is not None and description != plan.description:
            plan.description = description
        if status is not None and status != plan.status:
            self._record_history(
                plan.id, None, actor_type, actor_id, "status_changed",
                "status", plan.status, status,
            )
            plan.status = status
            if status == TestPlanStatus.COMPLETED.value:
                plan.completed_at = datetime.now(timezone.utc)
        # Provenance fields: only set when the caller explicitly supplied a
        # non-None value AND the plan doesn't already have one — never
        # clobber an existing stamp on a follow-up edit.
        if generated_by_model is not None and not plan.generated_by_model:
            plan.generated_by_model = generated_by_model
        if generated_by_tool is not None and not plan.generated_by_tool:
            plan.generated_by_tool = generated_by_tool
        if prompt_version is not None and not plan.prompt_version:
            plan.prompt_version = prompt_version
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def submit_plan(
        self, plan: TestPlan, actor_type: str, actor_id: int,
    ) -> TestPlan:
        if plan.status != TestPlanStatus.DRAFT.value:
            raise ValueError("Only draft plans can be submitted for approval")
        entry_count = (
            self.db.query(TestPlanEntry)
            .filter(TestPlanEntry.test_plan_id == plan.id)
            .count()
        )
        if entry_count == 0:
            raise ValueError("Cannot submit an empty test plan — add entries first")
        return self.update_plan(plan, actor_type, actor_id, status=TestPlanStatus.PROPOSED.value)

    def approve_plan(self, plan: TestPlan, user_id: int) -> TestPlan:
        if plan.status not in (TestPlanStatus.PROPOSED.value, TestPlanStatus.REJECTED.value):
            raise ValueError("Only proposed or rejected plans can be approved")
        plan.approved_by_id = user_id
        plan.approved_at = datetime.now(timezone.utc)
        plan.rejected_by_id = None
        plan.rejected_at = None
        plan.rejection_reason = None
        self._record_history(
            plan.id, None, "user", user_id, "approved",
            "status", plan.status, TestPlanStatus.APPROVED.value,
        )
        plan.status = TestPlanStatus.APPROVED.value
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def reject_plan(
        self, plan: TestPlan, user_id: int, reason: Optional[str] = None,
    ) -> TestPlan:
        if plan.status != TestPlanStatus.PROPOSED.value:
            raise ValueError("Only proposed plans can be rejected")
        plan.rejected_by_id = user_id
        plan.rejected_at = datetime.now(timezone.utc)
        plan.rejection_reason = reason
        self._record_history(
            plan.id, None, "user", user_id, "rejected",
            "status", plan.status, TestPlanStatus.REJECTED.value,
        )
        plan.status = TestPlanStatus.REJECTED.value
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def archive_plan(
        self, plan: TestPlan, user_id: int, reason: Optional[str] = None,
    ) -> TestPlan:
        """Abandon a plan — move any non-terminal plan to ARCHIVED.

        The recon-abandon analog: unlike ``reject`` (proposed-only,
        pre-approval), this works on approved/in-progress plans the
        operator no longer wants, without the destructive ``DELETE``.
        Who/when/from-status is captured in plan history; ``rejection_reason``
        is reused as the generic terminal reason.
        """
        terminal = (
            TestPlanStatus.COMPLETED.value,
            TestPlanStatus.REJECTED.value,
            TestPlanStatus.ARCHIVED.value,
        )
        if plan.status in terminal:
            raise ValueError(
                f"Plan is already in a terminal state ('{plan.status}') and cannot be abandoned"
            )
        old_status = plan.status
        if reason:
            plan.rejection_reason = reason
        self._record_history(
            plan.id, None, "user", user_id, "archived",
            "status", old_status, TestPlanStatus.ARCHIVED.value,
        )
        plan.status = TestPlanStatus.ARCHIVED.value
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def delete_plan(self, plan: TestPlan) -> None:
        self.db.delete(plan)
        self.db.commit()

    # ------------------------------------------------------------------
    # Test Plan Entries
    # ------------------------------------------------------------------

    def add_entries(
        self,
        plan: TestPlan,
        entries_data: List[Dict[str, Any]],
        actor_type: str,
        actor_id: int,
    ) -> List[TestPlanEntry]:
        """Batch-add entries to a plan.  Validates host_ids belong to plan's project.

        Concurrency: the application-level precheck below catches the
        common case (the same client submitting a duplicate batch) but
        the *real* invariant is the unique constraint
        ``uq_plan_host`` on ``(test_plan_id, host_id)``.  Two
        concurrent batches can both pass the precheck and then race on
        commit, which previously surfaced as a 500 IntegrityError.
        Each row is now inserted inside a SAVEPOINT so a constraint
        violation rolls back just that row instead of poisoning the
        whole batch.
        """
        if plan.status not in (
            TestPlanStatus.DRAFT.value,
            TestPlanStatus.PROPOSED.value,
            TestPlanStatus.APPROVED.value,
            TestPlanStatus.IN_PROGRESS.value,
        ):
            raise ValueError(f"Cannot add entries to a {plan.status} plan")

        host_ids = [e["host_id"] for e in entries_data]
        # Single LEFT JOIN returns both signals in one round-trip:
        #   * valid_hosts = host rows that belong to plan.project_id
        #   * existing    = subset of those already wired to this plan
        # Pre-v2.42.0 this was two separate filtered queries; on a 10k-host
        # bulk-add the redundant scan cost ~50ms per batch.  Fast-path
        # precheck remains an optimization (not the invariant — the
        # unique index uq_plan_host is what actually prevents duplicates),
        # so a JOIN that may briefly miss a concurrent insert is fine.
        valid_hosts: set = set()
        existing: set = set()
        rows = (
            self.db.query(Host.id, TestPlanEntry.host_id)
            .outerjoin(
                TestPlanEntry,
                (TestPlanEntry.host_id == Host.id)
                & (TestPlanEntry.test_plan_id == plan.id),
            )
            .filter(Host.id.in_(host_ids), Host.project_id == plan.project_id)
            .all()
        )
        for host_id, existing_entry_host_id in rows:
            valid_hosts.add(host_id)
            if existing_entry_host_id is not None:
                existing.add(host_id)

        created: List[TestPlanEntry] = []
        for data in entries_data:
            hid = data["host_id"]
            if hid not in valid_hosts:
                logger.warning("Skipping entry for host %d: not in project %d", hid, plan.project_id)
                continue
            if hid in existing:
                logger.warning("Skipping duplicate entry for host %d in plan %d", hid, plan.id)
                continue

            entry = TestPlanEntry(
                test_plan_id=plan.id,
                host_id=hid,
                priority=data["priority"],
                test_phase=data["test_phase"],
                proposed_tests=data["proposed_tests"],
                rationale=data["rationale"],
                notes=data.get("notes"),
            )
            # SAVEPOINT-per-row so a concurrent insert that races us
            # past the precheck rolls back just this entry, not the
            # whole batch.  begin_nested() emits a SAVEPOINT under the
            # outer transaction; on IntegrityError we ROLLBACK TO the
            # savepoint and continue with the next row.
            try:
                with self.db.begin_nested():
                    self.db.add(entry)
                    self.db.flush()
            except IntegrityError:
                logger.warning(
                    "Skipping host %d in plan %d: another writer inserted a "
                    "matching entry concurrently (uq_plan_host)",
                    hid, plan.id,
                )
                existing.add(hid)
                continue

            self._record_history(
                plan.id, entry.id, actor_type, actor_id, "created",
            )
            created.append(entry)
            existing.add(hid)

        self.db.commit()
        for e in created:
            self.db.refresh(e)
        return created

    # Plan states past drafting — execution may start at any time, so the
    # test_index positions must be stable from here on.
    _PROPOSED_TESTS_LOCKED_PLAN_STATES = (
        TestPlanStatus.APPROVED.value,
        TestPlanStatus.IN_PROGRESS.value,
        TestPlanStatus.COMPLETED.value,
    )

    def _proposed_tests_lock_reason(self, entry: TestPlanEntry) -> Optional[str]:
        """Return a human-readable reason if ``entry.proposed_tests`` is frozen,
        else ``None``.

        proposed_tests defines the ``test_index`` positions that
        ``TestExecutionResult`` rows reference.  Reordering/replacing the array
        once the plan can be (or is being) executed re-attributes recorded or
        in-flight evidence.  So it freezes as soon as the plan leaves drafting:
        the plan is approved/in_progress/completed (execution may start at any
        moment, and a snapshot — live context fetch or offline bundle — may
        already be in an agent's hands), an execution session exists, or a
        result has been recorded.  draft/proposed/rejected plans stay editable
        so plan-gen can revise freely.
        """
        plan_status = (
            self.db.query(TestPlan.status)
            .filter(TestPlan.id == entry.test_plan_id)
            .scalar()
        )
        if plan_status in self._PROPOSED_TESTS_LOCKED_PLAN_STATES:
            return f"the plan is {plan_status}"
        if (
            self.db.query(ExecutionSession.id)
            .filter(ExecutionSession.test_plan_id == entry.test_plan_id)
            .first()
            is not None
        ):
            return "an execution session exists for the plan"
        if (
            self.db.query(TestExecutionResult.id)
            .filter(TestExecutionResult.entry_id == entry.id)
            .first()
            is not None
        ):
            return "execution results exist for this entry"
        return None

    def update_entry(
        self,
        entry: TestPlanEntry,
        actor_type: str,
        actor_id: int,
        updates: Dict[str, Any],
        expected_updated_at: Optional[datetime] = None,
    ) -> TestPlanEntry:
        """Update a single entry with optimistic concurrency check."""
        if expected_updated_at is not None:
            actual = (
                entry.updated_at.astimezone(timezone.utc)
                if entry.updated_at and entry.updated_at.tzinfo
                else entry.updated_at
            )
            expected = (
                expected_updated_at.astimezone(timezone.utc)
                if expected_updated_at.tzinfo
                else expected_updated_at
            )
            if actual != expected:
                raise ValueError("Conflict: entry was modified by another actor")

        # CR4-1 / CR5-C1 — proposed_tests defines the test_index positions
        # that TestExecutionResult rows reference.  Reordering or replacing
        # the array after execution can begin silently re-attributes
        # recorded (or in-flight) evidence to a different test.  Freeze it
        # once the plan leaves drafting — see _proposed_tests_lock_reason.
        if (
            "proposed_tests" in updates
            and updates["proposed_tests"] != entry.proposed_tests
        ):
            reason = self._proposed_tests_lock_reason(entry)
            if reason:
                raise ValueError(
                    f"Cannot modify proposed_tests once {reason} — results "
                    "reference tests by position (test_index), so changing "
                    "the list would re-attribute evidence. Clone the plan to "
                    "revise the test list."
                )

        for field in (
            "priority", "test_phase", "proposed_tests", "rationale",
            "status", "findings", "results_data", "notes", "assigned_to_id",
        ):
            if field not in updates:
                continue
            new_val = updates[field]
            old_val = getattr(entry, field)
            if new_val == old_val:
                continue

            action = "status_changed" if field == "status" else "updated"
            self._record_history(
                entry.test_plan_id, entry.id, actor_type, actor_id,
                action, field,
                str(old_val) if old_val is not None else None,
                str(new_val) if new_val is not None else None,
            )
            setattr(entry, field, new_val)

            # Track lifecycle timestamps
            if field == "status":
                if new_val == TestEntryStatus.IN_PROGRESS.value and not entry.started_at:
                    entry.started_at = datetime.now(timezone.utc)
                elif new_val in (TestEntryStatus.COMPLETED.value, TestEntryStatus.REJECTED.value):
                    entry.completed_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_entry(
        self, entry_id: int, plan_id: int,
    ) -> Optional[TestPlanEntry]:
        return (
            self.db.query(TestPlanEntry)
            .filter(TestPlanEntry.id == entry_id, TestPlanEntry.test_plan_id == plan_id)
            .first()
        )

    # ------------------------------------------------------------------
    # Progress & History
    # ------------------------------------------------------------------

    def get_progress(self, plan_id: int) -> Dict[str, Any]:
        batch = self.get_progress_batch([plan_id])
        return batch.get(plan_id, self._empty_progress(plan_id))

    def get_progress_batch(self, plan_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Compute progress for multiple plans in three aggregate queries."""
        if not plan_ids:
            return {}

        # An entry is "done" once it reaches a terminal state — either it
        # was tested (completed) or the human decided not to test it
        # (rejected).  "approved" is mid-workflow ("queued for testing")
        # and intentionally does NOT advance completion.
        done_statuses = (TestEntryStatus.COMPLETED.value, TestEntryStatus.REJECTED.value)

        # Status counts per plan
        status_rows = (
            self.db.query(
                TestPlanEntry.test_plan_id,
                TestPlanEntry.status,
                func.count(TestPlanEntry.id),
            )
            .filter(TestPlanEntry.test_plan_id.in_(plan_ids))
            .group_by(TestPlanEntry.test_plan_id, TestPlanEntry.status)
            .all()
        )
        # Priority counts per plan
        priority_rows = (
            self.db.query(
                TestPlanEntry.test_plan_id,
                TestPlanEntry.priority,
                func.count(TestPlanEntry.id),
            )
            .filter(TestPlanEntry.test_plan_id.in_(plan_ids))
            .group_by(TestPlanEntry.test_plan_id, TestPlanEntry.priority)
            .all()
        )
        # Phase counts per plan
        phase_rows = (
            self.db.query(
                TestPlanEntry.test_plan_id,
                TestPlanEntry.test_phase,
                func.count(TestPlanEntry.id),
            )
            .filter(TestPlanEntry.test_plan_id.in_(plan_ids))
            .group_by(TestPlanEntry.test_plan_id, TestPlanEntry.test_phase)
            .all()
        )

        # Assemble per-plan dicts
        result: Dict[int, Dict[str, Any]] = {
            pid: self._empty_progress(pid) for pid in plan_ids
        }

        for pid, status, cnt in status_rows:
            result[pid]["by_status"][status] = cnt
            result[pid]["total_entries"] += cnt
            if status in done_statuses:
                result[pid]["hosts_tested"] += cnt

        for pid, priority, cnt in priority_rows:
            result[pid]["by_priority"][priority] = cnt

        for pid, phase, cnt in phase_rows:
            result[pid]["by_phase"][phase] = cnt

        for pid in plan_ids:
            total = result[pid]["total_entries"]
            done = result[pid]["hosts_tested"]
            result[pid]["completion_pct"] = round((done / total) * 100, 1) if total else 0.0
            result[pid]["hosts_remaining"] = total - done

        return result

    @staticmethod
    def _empty_progress(plan_id: int) -> Dict[str, Any]:
        return {
            "plan_id": plan_id,
            "total_entries": 0,
            "by_status": {},
            "by_priority": {},
            "by_phase": {},
            "completion_pct": 0.0,
            "hosts_tested": 0,
            "hosts_remaining": 0,
        }

    def get_history(
        self, plan_id: int, limit: int = 100,
    ) -> List[TestPlanHistory]:
        return (
            self.db.query(TestPlanHistory)
            .filter(TestPlanHistory.test_plan_id == plan_id)
            .order_by(TestPlanHistory.timestamp.desc())
            .limit(limit)
            .all()
        )

    def count_new_hosts_since_plan(self, plan: TestPlan) -> int:
        """Count hosts added to the project after the plan was created."""
        return (
            self.db.query(Host)
            .filter(
                Host.project_id == plan.project_id,
                Host.first_seen > plan.created_at,
            )
            .count()
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_history(
        self,
        plan_id: int,
        entry_id: Optional[int],
        actor_type: str,
        actor_id: int,
        action: str,
        field_changed: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
    ) -> None:
        record = TestPlanHistory(
            test_plan_id=plan_id,
            entry_id=entry_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            field_changed=field_changed,
            old_value=old_value,
            new_value=new_value,
        )
        self.db.add(record)
