"""Agent evidence records: what an agent ran against a host, and what came back (v2.436.0).

Recorded directly and never changed — it is the audit trail (acceptance run
2026-09-30, R1: a structured result used to need a plan, an entry and an
execution run).  The raw output is kept in the row (``raw_output``, deferred,
so a list never loads it) with a short preview.  Until v2.439.0 it was a file
under ``uploads/evidence/``, which outlived a deleted host or project.
"""
from __future__ import annotations

from datetime import datetime
import ipaddress
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceOutcome, EvidenceRecord

#: Raw output kept per record — a tool's output, not a disk image.
RAW_OUTPUT_MAX_BYTES = 5 * 1024 * 1024
#: Characters of raw output carried inline on the record.
PREVIEW_CHARS = 2000

OUTCOMES = [o.value for o in EvidenceOutcome]


def record_evidence(
    db: Session,
    *,
    project_id: int,
    host_id: int,
    tool: str,
    outcome: str,
    summary: str,
    command: Optional[str] = None,
    raw_output: Optional[str] = None,
    observed_ip: Optional[str] = None,
    executed_at: Optional[datetime] = None,
    finding_id: Optional[int] = None,
    finding_host_id: Optional[int] = None,
    agent_session_id: Optional[int] = None,
    recorded_by_user_id: Optional[int] = None,
    agent_model: Optional[str] = None,
    agent_client: Optional[str] = None,
    host_test_id: Optional[int] = None,
    request_key: Optional[str] = None,
    server_timed: bool = False,
) -> EvidenceRecord:
    """Validate the targets belong to the project, store the raw output as a
    file, and add the row.  The caller commits.

    ``server_timed``: ``executed_at`` is the server's clock, not something the
    caller sent, so it is left out of the replay fingerprint — a retry of the
    same result is then recognised, and a changed one under the same key is
    refused, by the one check below."""
    from app.services.host_test_service import get_test, payload_hash
    from app.db.models_host_tests import TESTED_OUTCOMES
    test = get_test(db, project_id, host_test_id) if host_test_id is not None else None
    if test is not None and test.host_id != host_id:
        raise HTTPException(422, "Evidence and host test must refer to the same host")
    if observed_ip:
        try:
            observed_ip = str(ipaddress.ip_address(observed_ip))
        except ValueError as exc:
            raise HTTPException(422, "observed_ip must be an IPv4 or IPv6 literal") from exc
    if test is not None and not request_key:
        raise HTTPException(422, "Test evidence requires a stable request_key for retries")
    digest = payload_hash(dict(host_id=host_id, tool=tool, outcome=outcome, summary=summary,
        command=command, raw_output=raw_output, observed_ip=observed_ip,
        executed_at=None if server_timed else executed_at,
        finding_id=finding_id, finding_host_id=finding_host_id, host_test_id=host_test_id,
        recorded_by_user_id=recorded_by_user_id))
    if request_key:
        prior = db.query(EvidenceRecord).filter(EvidenceRecord.project_id == project_id,
                                               EvidenceRecord.request_key == request_key).first()
        if prior:
            if prior.request_hash != digest:
                raise HTTPException(409, "request_key already used for different evidence")
            return prior
    if outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {OUTCOMES}")
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project_id)
        .first()
    )
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")
    if finding_id is not None:
        finding = (
            db.query(Finding)
            .filter(Finding.id == finding_id, Finding.project_id == project_id)
            .first()
        )
        if finding is None:
            raise HTTPException(status_code=404, detail="Finding not found in this project")
    if finding_host_id is not None:
        fh = db.get(FindingHost, finding_host_id)
        if fh is None or fh.host_id != host_id or (finding_id is not None and fh.finding_id != finding_id):
            raise HTTPException(
                status_code=422,
                detail="finding_host_id must be an endpoint of this host (and of finding_id, when given)",
            )
        finding_id = finding_id or fh.finding_id

    # PostgreSQL text rejects NUL, which tool output does carry (the
    # ingestion rule, v2.420.0): one byte would fail the whole record.
    raw_output = _no_nul(raw_output) or None
    size = preview = None
    if raw_output:
        size = len(raw_output.encode("utf-8", errors="replace"))
        if size > RAW_OUTPUT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"raw_output is {size} bytes; the limit is {RAW_OUTPUT_MAX_BYTES}. "
                       "Trim it, or upload the file if it is a supported scanner format.",
            )
        preview = raw_output[:PREVIEW_CHARS]

    record = EvidenceRecord(
        project_id=project_id, host_id=host_id, finding_id=finding_id,
        finding_host_id=finding_host_id, tool=_no_nul(tool).strip()[:100], command=_no_nul(command),
        host_test_id=host_test_id, request_key=request_key, request_hash=digest if request_key else None,
        outcome=outcome, summary=_no_nul(summary).strip(), raw_output=raw_output,
        raw_output_bytes=size, raw_output_preview=preview, observed_ip=observed_ip,
        executed_at=executed_at, agent_session_id=agent_session_id,
        recorded_by_user_id=recorded_by_user_id,
        agent_model=(agent_model or None) and agent_model[:100],
        agent_client=(agent_client or None) and agent_client[:100],
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        prior = db.query(EvidenceRecord).filter(EvidenceRecord.project_id == project_id,
                                               EvidenceRecord.request_key == request_key).first() if request_key else None
        if prior is None:
            raise
        if prior.request_hash != digest:
            raise HTTPException(409, "request_key already used for different evidence")
        return prior
    if test and test.target_fqdn and observed_ip and outcome in TESTED_OUTCOMES:
        from app.services.dns_name_service import record_observation
        record_observation(db, project_id=project_id, name=test.target_fqdn,
                           record_type=models.DNS_OBS_TESTED, value=observed_ip,
                           evidence_record_id=record.id, observed_at=executed_at)
    return record


def list_evidence(
    db: Session,
    project_id: int,
    *,
    host_id: Optional[int] = None,
    finding_id: Optional[int] = None,
    agent_session_id: Optional[int] = None,
    host_test_id: Optional[int] = None,
    unlinked: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[EvidenceRecord], int]:
    q = db.query(EvidenceRecord).filter(EvidenceRecord.project_id == project_id)
    if unlinked:
        # Records that answer no test (the host page lists those on their own;
        # a test's records are shown under the test).
        q = q.filter(EvidenceRecord.host_test_id.is_(None))
    if host_id is not None:
        q = q.filter(EvidenceRecord.host_id == host_id)
    if finding_id is not None:
        q = q.filter(EvidenceRecord.finding_id == finding_id)
    if agent_session_id is not None:
        q = q.filter(EvidenceRecord.agent_session_id == agent_session_id)
    if host_test_id is not None:
        q = q.filter(EvidenceRecord.host_test_id == host_test_id)
    total = q.count()
    rows = (
        q.options(joinedload(EvidenceRecord.host), joinedload(EvidenceRecord.recorded_by))
        .order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
        .offset(offset).limit(limit).all()
    )
    return rows, total


def get_evidence(db: Session, project_id: int, evidence_id: int) -> EvidenceRecord:
    record = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.id == evidence_id, EvidenceRecord.project_id == project_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence record not found in this project")
    return record


def _no_nul(text: Optional[str]) -> Optional[str]:
    return text.replace("\x00", "") if text else text


def read_raw_output(record: EvidenceRecord) -> str:
    if not record.raw_output:
        raise HTTPException(status_code=404, detail="This evidence record has no stored raw output")
    return record.raw_output


def link_issue_evidence(db: Session, *, host_id: int, issue_key: Optional[str], finding_id: int) -> int:
    """Attach to a finding the results that showed its issue on this host
    (v2.445.0): evidence with outcome ``finding``, not yet on a finding,
    recorded for a test linked to that issue.  Called when the observation is
    promoted, so promoting from the weakness row and promoting from the test's
    result end in the same place.  Returns how many were linked."""
    from app.db.models_host_tests import HostTest

    if not issue_key:
        return 0
    test_ids = select(HostTest.id).where(HostTest.host_id == host_id, HostTest.issue_key == issue_key)
    return (
        db.query(EvidenceRecord)
        .filter(
            EvidenceRecord.host_test_id.in_(test_ids), EvidenceRecord.host_id == host_id,
            EvidenceRecord.outcome == "finding", EvidenceRecord.finding_id.is_(None),
        )
        .update({"finding_id": finding_id}, synchronize_session=False)
    )


def create_finding_from_evidence(
    db: Session, project_id: int, evidence_id: int, *,
    title: Optional[str], severity: Optional[str], status: str, actor_id: int,
) -> Tuple[Finding, bool]:
    """A person promotes an evidence record that showed an issue (v2.443.0).

    When the record's test confirms a scanner observation still on the host
    (v2.445.0), this IS that observation's promotion, on this host —
    ``promote_or_dismiss_vulnerability``, so it joins the issue's existing
    finding instead of making a second one for the same issue.  Otherwise it
    is ``FindingService.create_finding`` on the evidence's host (at the name
    the test was aimed at).  Either way the record is linked.  The row is
    locked: two clicks must not make two findings.  Returns ``(finding,
    joined_issue)``; the caller commits."""
    from app.db.models_host_tests import HostTest
    from app.db.models_vulnerability import Vulnerability
    from app.services.finding_actions import promote_or_dismiss_vulnerability
    from app.services.finding_service import FindingService

    record = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.id == evidence_id, EvidenceRecord.project_id == project_id)
        .with_for_update().populate_existing().first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence record not found in this project")
    if record.finding_id is not None:
        raise HTTPException(status_code=409, detail=f"This evidence already belongs to finding #{record.finding_id}")
    if record.outcome != "finding":
        raise HTTPException(status_code=422, detail="Only evidence whose outcome is 'finding' can be promoted")
    test = db.get(HostTest, record.host_test_id) if record.host_test_id else None
    vuln = None
    if test is not None and test.issue_key:
        vuln = (
            db.query(Vulnerability)
            .filter(Vulnerability.host_id == record.host_id, Vulnerability.issue_key == test.issue_key)
            .order_by(Vulnerability.id).first()
        )
    if vuln is not None:
        # The observation rates its own finding: the caller's severity is the
        # test's priority, which nobody chose as a severity (the linked-test
        # control shows no selector).  It is used only on the path below.
        finding = promote_or_dismiss_vulnerability(
            db, vuln=vuln, project_id=project_id, actor_id=actor_id, severity=None,
            status=status, scope="host", summary=f"Confirmed by test evidence #{record.id}",
        )
    else:
        if not (title or "").strip() or not severity:
            raise HTTPException(status_code=422, detail="title and severity are required for a new finding")
        svc = FindingService(db)
        finding = svc.create_finding(
            project_id=project_id, title=title, severity=severity, actor_id=actor_id,
            status=status, source="execution", summary=f"Created from evidence #{record.id}",
        )
        names = {record.host_id: test.name_id} if test is not None and test.name_id else None
        svc._attach_hosts(finding, [record.host_id], names_by_host=names)
    record.finding_id = finding.id
    db.flush()
    return finding, vuln is not None


def serialize_evidence(record: EvidenceRecord) -> dict:
    host = record.host
    return {
        "id": record.id,
        "host_id": record.host_id,
        "host_test_id": record.host_test_id,
        "host_ip": host.ip_address if host is not None else None,
        "finding_id": record.finding_id,
        "finding_host_id": record.finding_host_id,
        "tool": record.tool,
        "command": record.command,
        "outcome": record.outcome,
        "summary": record.summary,
        "raw_output_preview": record.raw_output_preview,
        "raw_output_bytes": record.raw_output_bytes,
        # Bytes against bytes: comparing the preview's characters with the
        # byte size flagged any short non-ASCII output as cut.
        "raw_output_truncated_in_preview": bool(
            record.raw_output_bytes and record.raw_output_preview is not None
            and len(record.raw_output_preview.encode("utf-8", errors="replace")) < record.raw_output_bytes
        ),
        "observed_ip": record.observed_ip,
        "executed_at": record.executed_at,
        "agent_session_id": record.agent_session_id,
        "recorded_by": (
            (record.recorded_by.full_name or record.recorded_by.username)
            if record.recorded_by is not None else None
        ),
        "agent_model": record.agent_model,
        "agent_client": record.agent_client,
        "created_at": record.created_at,
    }
