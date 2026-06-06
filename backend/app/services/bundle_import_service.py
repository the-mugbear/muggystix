"""
Bundle Results Import Service

Parses and applies a results file produced by a remote agent executing
an exported test-plan bundle.  Paired with ``bundle_service.py`` which
produced the bundle in the first place.

Design:
- Idempotent: re-importing the same bundle updates existing rows rather
  than inserting duplicates (dedupe on ``(session_id, entry_id, test_index)``
  for results and ``(session_id, entry_id)`` for sanity checks).
- Partial-import-safe: the caller can submit interim result files
  multiple times.  ``is_final=True`` transitions the session to
  ``completed``; interim imports leave it ``active``.
- Feedback extraction: if the results file contains a top-level
  ``feedback`` object, an ``AgentFeedback`` row is created in the same
  transaction so the developer queue stays in sync.
- Parse errors are collected and returned; a single bad row doesn't
  block the rest of the file.

Commit-boundary policy (audit #43):
    ``import_results_file`` **does not commit**.  Results, sanity
    checks, feedback, and the ImportedResultFile audit row are all
    added via ``db.add`` / ``db.flush`` but the transaction is owned
    by the HTTP endpoint so it can roll back the entire import on a
    downstream failure (e.g. audit write error).  Matches the pattern
    used by ``bundle_service.build_export_bundle``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models_agent import (
    AgentFeedback, AgentFeedbackSource, AgentFeedbackStatus,
    ExecutionSession, ExecutionSessionStatus, ExecutionSessionMode,
    FindingSeverity, HostSanityCheck, ImportedResultFile,
    SanityCheckMethod, TestEntryStatus,
    TestExecutionResult, TestExecutionStatus,
    TestPlan, TestPlanEntry,
)


ALLOWED_RESULT_STATUSES = {s.value for s in TestExecutionStatus}
ALLOWED_SEVERITIES = {s.value for s in FindingSeverity} | {None, "", "null"}
ALLOWED_SANITY_METHODS = {m.value for m in SanityCheckMethod}


class BundleImportError(Exception):
    """Raised for unrecoverable import failures (not per-row errors)."""


# Audit finding H1: the previous implementation handed untrusted input
# straight to ``json.loads`` with no depth limit.  Pathologically nested
# inputs (``{"a":{"a":{"a":...}}}``) build the object tree in quadratic
# time before any validation runs.  We do a shallow byte-level pre-scan
# counting brace/bracket depth and reject anything beyond
# ``_MAX_JSON_DEPTH`` before ``json.loads``, catching the depth attack in
# O(n) with constant additional memory.
#
# NB: this guard bounds DEPTH, not total size — the input is already a
# fully-decoded str here.  Total payload size is bounded UPSTREAM: the
# upload endpoint (test_plans.upload results) rejects anything over its
# 10 MB cap before calling import_results_file, so the str handed in is
# already size-bounded.
_MAX_JSON_DEPTH = 20


def _assert_json_depth_ok(text: str) -> None:
    """Raise BundleImportError if the text appears to be deeply nested.

    Intentionally naive: counts ``{[`` opens and ``]}`` closes without
    tracking string literals, so a string containing ``{{{{`` will
    register as depth even though it isn't structural.  False
    positives (rejecting a borderline legitimate file) are acceptable
    here — the cap is 20 levels and no sane results.json gets anywhere
    near that.  False negatives (letting a malicious file through)
    would defeat the purpose, so we err on the side of over-counting.
    """
    depth = 0
    max_depth = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{' or ch == '[':
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth > _MAX_JSON_DEPTH:
                    raise BundleImportError(
                        f"Results file is nested too deeply "
                        f"(>{_MAX_JSON_DEPTH} levels). This is usually "
                        f"a sign of a malformed or malicious file; "
                        f"legitimate results rarely exceed 5-6 levels."
                    )
        elif ch == '}' or ch == ']':
            depth -= 1


def _load_file(file_bytes: bytes) -> Dict[str, Any]:
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleImportError(f"Results file is not valid UTF-8: {exc}")

    # Depth guard runs before json.loads so a pathological input is
    # rejected without ever building the object tree.  See
    # ``_assert_json_depth_ok`` for the threat model.
    _assert_json_depth_ok(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BundleImportError(f"Results file is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise BundleImportError("Results file must be a JSON object at the top level.")
    return data


def _truncate_output(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    limit = int(settings.TEST_OUTPUT_MAX_BYTES)
    if len(value.encode("utf-8")) <= limit:
        return value
    # Truncate by bytes, not chars, to match the docstring on the model.
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "\n... [truncated]"


def _ingest_sanity_checks(
    db: Session,
    *,
    session: ExecutionSession,
    entry_map: Dict[int, TestPlanEntry],
    items: List[Dict[str, Any]],
    errors: List[str],
) -> int:
    ingested = 0
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors.append(f"sanity_checks[{i}]: expected object, got {type(raw).__name__}")
            continue
        entry_id = raw.get("entry_id")
        if entry_id not in entry_map:
            errors.append(
                f"sanity_checks[{i}]: entry_id={entry_id} not found in this plan"
            )
            continue
        entry = entry_map[entry_id]
        method = raw.get("method")
        if method not in ALLOWED_SANITY_METHODS:
            errors.append(
                f"sanity_checks[{i}]: invalid method {method!r} "
                f"(expected one of {sorted(ALLOWED_SANITY_METHODS)})"
            )
            continue
        target_ip = raw.get("target_ip")
        if not target_ip:
            errors.append(f"sanity_checks[{i}]: target_ip is required")
            continue
        passed = raw.get("passed")
        if not isinstance(passed, bool):
            errors.append(f"sanity_checks[{i}]: passed must be boolean, got {type(passed).__name__}")
            continue

        # Idempotent upsert on (session_id, entry_id).
        existing = (
            db.query(HostSanityCheck)
            .filter(
                HostSanityCheck.execution_session_id == session.id,
                HostSanityCheck.entry_id == entry_id,
            )
            .first()
        )
        payload = dict(
            method=method,
            target_ip=target_ip,
            port_checked=raw.get("port_checked"),
            expected_value=raw.get("expected_value"),
            actual_value=raw.get("actual_value"),
            source_ip=raw.get("source_ip"),
            dns_result=raw.get("dns_result"),
            passed=passed,
            details=raw.get("details"),
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(HostSanityCheck(
                execution_session_id=session.id,
                entry_id=entry_id,
                host_id=entry.host_id,
                **payload,
            ))
        ingested += 1
    return ingested


def _ingest_results(
    db: Session,
    *,
    session: ExecutionSession,
    entry_map: Dict[int, TestPlanEntry],
    items: List[Dict[str, Any]],
    errors: List[str],
) -> Tuple[int, Dict[int, int]]:
    """Ingest per-test results.

    Returns (ingested_count, per_entry_completed_count) where the latter
    is used to optionally mark entries as completed when all their tests
    have a terminal status.
    """
    ingested = 0
    per_entry_completed: Dict[int, int] = {}
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors.append(f"results[{i}]: expected object, got {type(raw).__name__}")
            continue
        entry_id = raw.get("entry_id")
        if entry_id not in entry_map:
            errors.append(f"results[{i}]: entry_id={entry_id} not found in this plan")
            continue
        entry = entry_map[entry_id]
        test_index = raw.get("test_index")
        if not isinstance(test_index, int) or test_index < 0:
            errors.append(f"results[{i}]: test_index must be a non-negative integer")
            continue
        # Soft-check test_index against the entry's proposed_tests — a
        # mismatch is a warning, not a hard error (the agent may have
        # added ad-hoc tests with the user's approval).
        proposed_count = len(entry.proposed_tests or [])
        if test_index >= proposed_count:
            errors.append(
                f"results[{i}]: test_index={test_index} exceeds entry's "
                f"proposed_tests count ({proposed_count}) — accepted anyway"
            )
        status = raw.get("status")
        if status not in ALLOWED_RESULT_STATUSES:
            errors.append(
                f"results[{i}]: invalid status {status!r} "
                f"(expected one of {sorted(ALLOWED_RESULT_STATUSES)})"
            )
            continue
        severity = raw.get("severity")
        if severity == "":
            severity = None
        if severity not in ALLOWED_SEVERITIES:
            errors.append(f"results[{i}]: invalid severity {severity!r}")
            continue

        executed_at_raw = raw.get("executed_at")
        executed_at = None
        if executed_at_raw:
            try:
                executed_at = datetime.fromisoformat(str(executed_at_raw).replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"results[{i}]: invalid executed_at {executed_at_raw!r}")

        # Code review nitpick #2: strict identity check — ``bool("false")``
        # is True, which was letting a stringified "false" flip the
        # finding flag.  Require an actual boolean; anything else
        # (missing, string, int) is treated as not-a-finding.
        raw_is_finding = raw.get("is_finding")
        if raw_is_finding is not None and not isinstance(raw_is_finding, bool):
            errors.append(
                f"results[{i}]: is_finding must be a boolean, got "
                f"{type(raw_is_finding).__name__}"
            )
            raw_is_finding = False
        payload = dict(
            status=status,
            command_run=raw.get("command_run"),
            raw_output=_truncate_output(raw.get("raw_output")),
            findings_summary=raw.get("findings_summary"),
            severity=severity,
            is_finding=raw_is_finding is True,
            executed_at=executed_at,
        )

        existing = (
            db.query(TestExecutionResult)
            .filter(
                TestExecutionResult.execution_session_id == session.id,
                TestExecutionResult.entry_id == entry_id,
                TestExecutionResult.test_index == test_index,
            )
            .first()
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(TestExecutionResult(
                execution_session_id=session.id,
                entry_id=entry_id,
                test_index=test_index,
                **payload,
            ))
        ingested += 1
        if status in ("executed", "skipped", "failed", "not_applicable"):
            per_entry_completed[entry_id] = per_entry_completed.get(entry_id, 0) + 1

    return ingested, per_entry_completed


def _ingest_feedback(
    db: Session,
    *,
    plan: TestPlan,
    session: ExecutionSession,
    raw: Dict[str, Any],
) -> bool:
    """Persist a feedback block extracted from the results file.

    Returns True if a row was created.  Gracefully skips if the feedback
    object is missing any required shape — agents sometimes abbreviate.
    """
    if not isinstance(raw, dict):
        return False
    rating = raw.get("overall_rating")
    if rating is not None:
        try:
            rating = int(rating)
            if not (1 <= rating <= 5):
                rating = None
        except (TypeError, ValueError):
            rating = None
    row = AgentFeedback(
        project_id=plan.project_id,
        agent_id=session.agent_id,
        test_plan_id=plan.id,
        execution_session_id=session.id,
        source=AgentFeedbackSource.EXPORTED_EXECUTION.value,
        prompt_version=raw.get("prompt_version"),
        overall_rating=rating,
        api_critiques=raw.get("api_critiques") or [],
        tool_suggestions=raw.get("tool_suggestions") or [],
        friction_notes=raw.get("friction_notes"),
        agent_metrics=raw.get("agent_metrics") or {},
        status=AgentFeedbackStatus.NEW.value,
    )
    db.add(row)
    return True


def import_results_file(
    db: Session,
    *,
    plan_id: int,
    project_id: int,
    file_bytes: bytes,
    filename: Optional[str],
    imported_by_id: Optional[int],
) -> Dict[str, Any]:
    """Top-level entry point.  Parses, validates, and applies a results file.

    The caller (the endpoint) is expected to commit the transaction if
    this function returns without raising.  On success returns a summary
    dict with counts + error list.  On unrecoverable failure raises
    ``BundleImportError`` — the endpoint should return 400.
    """
    data = _load_file(file_bytes)

    bundle_id = data.get("bundle_id")
    if not bundle_id:
        raise BundleImportError("Results file is missing required field: bundle_id")

    # Code review critical #3: cross-validate the plan_id and session_id
    # declared INSIDE the file body against the URL path and the
    # session we resolve via bundle_id.  The previous implementation
    # only used bundle_id — a caller could upload a file claiming a
    # different plan/session and the importer would silently accept it.
    body_plan_id = data.get("plan_id")
    if body_plan_id is not None and body_plan_id != plan_id:
        raise BundleImportError(
            f"Results file plan_id={body_plan_id!r} does not match the "
            f"endpoint plan_id={plan_id!r}."
        )

    plan = (
        db.query(TestPlan)
        .filter(TestPlan.id == plan_id, TestPlan.project_id == project_id)
        .first()
    )
    if not plan:
        raise BundleImportError(f"Test plan {plan_id} not found in this project")

    # Resolve the execution session via bundle_id.  ``bundle_id`` is
    # the authoritative correlator because it was minted at export
    # time; we additionally verify the body's ``execution_session_id``
    # matches if present so a stale file can't sneak through.
    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan_id,
            ExecutionSession.bundle_id == bundle_id,
        )
        .first()
    )
    if not session:
        raise BundleImportError(
            f"No exported execution session found for bundle_id={bundle_id!r}. "
            f"Either this results file was for a different plan or the session "
            f"has been deleted."
        )
    if session.mode != ExecutionSessionMode.EXPORTED.value:
        raise BundleImportError(
            f"Session {session.id} is in {session.mode!r} mode — only "
            f"exported sessions accept imported results."
        )
    body_session_id = data.get("execution_session_id")
    if body_session_id is not None and body_session_id != session.id:
        raise BundleImportError(
            f"Results file execution_session_id={body_session_id!r} does not "
            f"match the session resolved via bundle_id (#{session.id}). "
            f"This is usually a stale or mismatched results file."
        )

    # Build entry_id → entry map once.
    entries = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan_id)
        .all()
    )
    entry_map = {e.id: e for e in entries}

    errors: List[str] = []

    sanity_items = data.get("sanity_checks") or []
    if not isinstance(sanity_items, list):
        errors.append("sanity_checks must be an array")
        sanity_items = []
    sanity_count = _ingest_sanity_checks(
        db,
        session=session,
        entry_map=entry_map,
        items=sanity_items,
        errors=errors,
    )

    result_items = data.get("results") or []
    if not isinstance(result_items, list):
        errors.append("results must be an array")
        result_items = []
    result_count, per_entry_completed = _ingest_results(
        db,
        session=session,
        entry_map=entry_map,
        items=result_items,
        errors=errors,
    )

    # Optional: mark entries as completed once all proposed tests have
    # terminal results.  This keeps the test-plan progress UI honest.
    for entry_id, completed in per_entry_completed.items():
        entry = entry_map[entry_id]
        proposed = len(entry.proposed_tests or [])
        if proposed and completed >= proposed and entry.status not in (
            TestEntryStatus.COMPLETED.value,
            TestEntryStatus.REJECTED.value,
        ):
            entry.status = TestEntryStatus.COMPLETED.value
            entry.completed_at = datetime.now(timezone.utc)

    # Feedback extraction
    feedback_extracted = False
    feedback_block = data.get("feedback")
    if isinstance(feedback_block, dict) and feedback_block:
        feedback_extracted = _ingest_feedback(
            db,
            plan=plan,
            session=session,
            raw=feedback_block,
        )

    # Code review critical #3: refuse to mark a session ``completed``
    # when the import is obviously incomplete.  The previous code
    # accepted any truthy ``is_final`` even if ``results=[]`` or
    # every row failed to parse, so a malformed upload could make
    # execution look complete while entries remained incomplete.
    is_final = data.get("is_final") is True  # strict identity, not coercion
    if is_final:
        if result_count == 0:
            raise BundleImportError(
                "Cannot mark this import as final: the results array is empty. "
                "Submit an interim import with the results you have, or set "
                "is_final=false and re-submit with the remaining results."
            )
        if errors:
            raise BundleImportError(
                f"Cannot mark this import as final: {len(errors)} parse "
                f"error(s) were collected. Fix the malformed entries and "
                f"re-submit, or set is_final=false for an interim import. "
                f"First error: {errors[0]}"
            )
        # Also refuse to finalize if any plan entry is still missing
        # a terminal result (no row in results[] for the entry at all).
        entries_with_any_result = {
            r["entry_id"] if isinstance(r, dict) else None
            for r in (data.get("results") or [])
        }
        missing = [
            e.id for e in entries
            if e.id not in entries_with_any_result
            and e.status not in (
                TestEntryStatus.COMPLETED.value,
                TestEntryStatus.REJECTED.value,
            )
        ]
        if missing:
            raise BundleImportError(
                f"Cannot mark this import as final: {len(missing)} plan "
                f"entr{'y' if len(missing) == 1 else 'ies'} still missing "
                f"results (entry id{'s' if len(missing) != 1 else ''}: "
                f"{', '.join(str(i) for i in missing[:5])}"
                f"{'…' if len(missing) > 5 else ''})."
            )

        session.status = ExecutionSessionStatus.COMPLETED.value
        session.completed_at = datetime.now(timezone.utc)
        # Transition the plan itself if every entry is terminal.
        all_entries_terminal = all(
            e.status in (
                TestEntryStatus.COMPLETED.value,
                TestEntryStatus.REJECTED.value,
            )
            for e in entries
        )
        if all_entries_terminal and plan.status == "in_progress":
            plan.status = "completed"
            plan.completed_at = datetime.now(timezone.utc)

    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    audit = ImportedResultFile(
        execution_session_id=session.id,
        test_plan_id=plan.id,
        bundle_id=bundle_id,
        imported_by_id=imported_by_id,
        filename=filename,
        file_sha256=file_sha256,
        results_count=result_count,
        sanity_checks_count=sanity_count,
        feedback_extracted=feedback_extracted,
        parse_errors=errors or None,
        is_final=is_final,
    )
    db.add(audit)

    return {
        "execution_session_id": session.id,
        "plan_id": plan.id,
        "bundle_id": bundle_id,
        "results_imported": result_count,
        "sanity_checks_imported": sanity_count,
        "feedback_extracted": feedback_extracted,
        "is_final": is_final,
        "session_status": session.status,
        "plan_status": plan.status,
        "parse_errors": errors,
    }
