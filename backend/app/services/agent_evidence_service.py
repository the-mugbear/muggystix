"""Agent evidence records: what an agent ran against a host, and what came back (v2.436.0).

Recorded directly and never changed — it is the audit trail (acceptance run
2026-09-30, R1: a structured result used to need a plan, an entry and an
execution run).  The raw output is kept in the row (``raw_output``, deferred,
so a list never loads it) with a short preview.  Until v2.439.0 it was a file
under ``uploads/evidence/``, which outlived a deleted host or project.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import ipaddress
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, defer, joinedload
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceOutcome, EvidenceRecord

from app.schemas.host_test_schemas import RAW_OUTPUT_MAX_BYTES  # noqa: F401  (5 MB; one constant, also the schemas' cap)

#: Characters of raw output carried inline on the record.
PREVIEW_CHARS = 2000

OUTCOMES = [o.value for o in EvidenceOutcome]


def record_evidence(
    db: Session,
    *,
    project_id: int,
    host_id: Optional[int],
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
    """Validate the targets belong to the project and add the row (the raw
    output is kept in it).  The caller commits.

    ``server_timed``: ``executed_at`` is the server's clock, not something the
    caller sent, so it is left out of the replay fingerprint — a retry of the
    same result is then recognised, and a changed one under the same key is
    refused, by the one check below."""
    return record_evidence_once(
        db, project_id=project_id, host_id=host_id, tool=tool, outcome=outcome, summary=summary,
        command=command, raw_output=raw_output, observed_ip=observed_ip, executed_at=executed_at,
        finding_id=finding_id, finding_host_id=finding_host_id, agent_session_id=agent_session_id,
        recorded_by_user_id=recorded_by_user_id, agent_model=agent_model, agent_client=agent_client,
        host_test_id=host_test_id, request_key=request_key, server_timed=server_timed,
    )[0]


def record_evidence_once(
    db: Session,
    *,
    project_id: int,
    host_id: Optional[int],
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
) -> Tuple[EvidenceRecord, bool]:
    """:func:`record_evidence`, saying also whether THIS call stored the
    record: ``(record, created)``.  ``created`` is False for a replay of the
    key — found up front, or lost to a concurrent request with the same key
    (review 2026-10-01 N8) — so a caller that goes on to write something else
    for a new record knows not to."""
    from app.services.host_test_service import get_test, payload_hash
    from app.db.models_host_tests import TESTED_OUTCOMES

    # Review 2026-10-01 R10 — measured before anything else touches it: the
    # schemas cap the length, but this is the one rule for every caller, and
    # the fingerprint below used to hash an oversize output before the check.
    # A character is at least a byte, so the cheap test settles most refusals
    # without encoding the string.
    if raw_output and (
        len(raw_output) > RAW_OUTPUT_MAX_BYTES
        or len(raw_output.encode("utf-8", errors="replace")) > RAW_OUTPUT_MAX_BYTES
    ):
        raise HTTPException(
            status_code=413,
            detail=f"raw_output is larger than the limit of {RAW_OUTPUT_MAX_BYTES} bytes. "
                   "Trim it, or upload the file if it is a supported scanner format.",
        )
    if request_key and "\x00" in request_key:
        # A NUL reached Postgres as a query parameter and failed with a 500.
        raise HTTPException(422, "request_key: NUL is not allowed")
    if host_id is None and (outcome != EvidenceOutcome.INFO.value or host_test_id is not None
                            or finding_host_id is not None):
        # 2.519.0 — context about no single host (a domain-wide lookup, a
        # scope note) needs no host; a test's result or a check against an
        # endpoint is about one (the check constraint says the same).
        raise HTTPException(
            422, "host_id is required unless outcome is info and the record answers no host test "
                 "and names no endpoint (finding_host_id).",
        )
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
            return prior, False
    if outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {OUTCOMES}")
    if host_id is not None and (
        db.query(models.Host.id)
        .filter(models.Host.id == host_id, models.Host.project_id == project_id)
        .first()
    ) is None:
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
    size = preview = sha = None
    if raw_output:
        # Within the limit: checked on entry, before the fingerprint (R10).
        encoded = raw_output.encode("utf-8", errors="replace")
        size = len(encoded)
        preview = raw_output[:PREVIEW_CHARS]
        # What a reader of the output in pieces checks the whole against
        # (2.519.0); the bytes `read_raw_chunk` serves.
        sha = hashlib.sha256(encoded).hexdigest()

    record = EvidenceRecord(
        project_id=project_id, host_id=host_id, finding_id=finding_id,
        finding_host_id=finding_host_id, tool=_no_nul(tool).strip()[:100], command=_no_nul(command),
        host_test_id=host_test_id, request_key=request_key, request_hash=digest if request_key else None,
        outcome=outcome, summary=_no_nul(summary).strip(), raw_output=raw_output,
        raw_output_bytes=size, raw_output_preview=preview, raw_output_sha256=sha, observed_ip=observed_ip,
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
        return prior, False
    if test and test.target_fqdn and observed_ip and outcome in TESTED_OUTCOMES:
        from app.services.dns_name_service import record_observation
        record_observation(db, project_id=project_id, name=test.target_fqdn,
                           record_type=models.DNS_OBS_TESTED, value=observed_ip,
                           evidence_record_id=record.id, observed_at=executed_at)
    return record, True


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
    with_preview: bool = True,
) -> Tuple[List[EvidenceRecord], int]:
    """``with_preview=False`` leaves the 2,000-character preview unread (the
    agents' ``preview=false``: a list of 50 records was up to 100 KB of
    previews nobody asked for — review 2026-10-10 stream, D8); serialize such
    rows with ``serialize_evidence(row, preview=False)``."""
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
    options = [joinedload(EvidenceRecord.host), joinedload(EvidenceRecord.recorded_by)]
    if not with_preview:
        options.append(defer(EvidenceRecord.raw_output_preview))
    rows = (
        q.options(*options)
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


#: A raw-output piece: the default and the most one read returns, in bytes.
RAW_CHUNK_DEFAULT = 16 * 1024
RAW_CHUNK_MAX = 64 * 1024


def read_raw_chunk(record: EvidenceRecord, offset: int, length: int) -> dict:
    """One piece of the raw output, by BYTE offset into its UTF-8 (2.519.0;
    prod agent feedback 2026-10-20: an MCP client had no way to read an
    output whole, and nothing to check it against).

    A piece ends on a character boundary (it may be a few bytes short of
    ``length``), so the pieces' texts joined are the output exactly, and
    ``sha256`` of their bytes is the record's ``raw_output_sha256``.  An
    ``offset`` inside a character is a 422; past the end is an empty piece."""
    data = read_raw_output(record).encode("utf-8", errors="replace")
    total = len(data)
    if offset < total and (data[offset] & 0xC0) == 0x80:
        raise HTTPException(422, "offset falls inside a character; use the next_offset a read returned.")
    end = min(offset + length, total)
    while end < total and end > offset and (data[end] & 0xC0) == 0x80:
        end -= 1
    piece = data[offset:end] if offset < total else b""
    return {
        "evidence_id": record.id,
        "offset": offset,
        "length": len(piece),
        "total_bytes": total,
        "next_offset": end if end < total else None,
        "sha256": record.raw_output_sha256,
        "text": piece.decode("utf-8"),
    }


def link_issue_evidence(
    db: Session, *, host_ids: Sequence[int], issue_key: Optional[str], finding_id: int,
) -> int:
    """Attach to a finding the results that showed its issue on these hosts
    (v2.445.0): evidence with outcome ``finding``, not yet on a finding,
    recorded for a test linked to that issue.  Called by
    ``FindingService.promote_vulnerability`` for the hosts it attaches, so
    every way of promoting an observation — the weakness row, the test's
    result, the scanner-observations list — ends in the same place.  Returns
    how many were linked."""
    ids = lock_issue_evidence(db, host_ids=host_ids, issue_key=issue_key)
    if not ids:
        return 0
    return (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.id.in_(ids), EvidenceRecord.finding_id.is_(None))
        .update({"finding_id": finding_id}, synchronize_session=False)
    )


def lock_issue_evidence(
    db: Session, *, host_ids: Sequence[int], issue_key: Optional[str],
    also: Union[int, Sequence[int], None] = None,
) -> List[int]:
    """Lock, IN ID ORDER, the unlinked finding-outcome evidence of the tests
    that confirm this issue on these hosts (plus the record or records
    ``also``); returns the ids.

    Review 2026-10-01 N8: promoting one result locked ITS record and then
    updated its siblings, so two people promoting two results of one issue
    each held the row the other wanted — a deadlock.  Every path now takes
    the whole set in one ordered statement, so the second waits for the first
    and then finds the records linked.  The evidence is locked BEFORE the
    issue's finding is looked up or inserted, on every path."""
    from app.db.models_host_tests import HostTest

    conditions = []
    hosts = sorted({hid for hid in host_ids if hid is not None})
    if issue_key and hosts:
        test_ids = select(HostTest.id).where(HostTest.host_id.in_(hosts), HostTest.issue_key == issue_key)
        conditions.append(
            EvidenceRecord.host_test_id.in_(test_ids) & EvidenceRecord.host_id.in_(hosts)
            & (EvidenceRecord.outcome == "finding") & EvidenceRecord.finding_id.is_(None)
        )
    extra = [also] if isinstance(also, int) else sorted(set(also or []))
    if extra:
        conditions.append(EvidenceRecord.id.in_(extra))
    if not conditions:
        return []
    return [
        rid for (rid,) in db.query(EvidenceRecord.id)
        .filter(or_(*conditions))
        .order_by(EvidenceRecord.id)
        .with_for_update()
    ]


@dataclass
class EvidencePromotion:
    """What ``promote_evidence`` did: the finding; ``joined_issue`` — the
    record confirmed a scanner observation, so this was that observation's
    promotion; ``created`` — this call made the finding (False: it joined the
    issue's existing one)."""
    finding: Finding
    joined_issue: bool
    created: bool


def create_finding_from_evidence(
    db: Session, project_id: int, evidence_id: int, *,
    title: Optional[str], severity: Optional[str], status: str, actor_id: int,
) -> Tuple[Finding, bool]:
    """A person's "Create finding" on a result: ``promote_evidence`` for that
    one record.  Returns ``(finding, joined_issue)``; the caller commits."""
    made = promote_evidence(
        db, project_id, evidence_id, title=title, severity=severity, status=status, actor_id=actor_id,
    )
    return made.finding, made.joined_issue


def promote_evidence(
    db: Session, project_id: int, evidence_id: int, *,
    title: Optional[str], severity: Optional[str], status: str, actor_id: int,
    more_host_ids: Sequence[int] = (), more_evidence_ids: Sequence[int] = (),
    provenance: Optional[str] = None,
) -> EvidencePromotion:
    """Promote an evidence record that showed an issue (v2.443.0) — the ONE
    implementation behind a person's "Create finding" on a result and an
    accepted agent ``finding_create`` proposal that cites such a result
    (review 2026-10-23, H-TR1: the accept made the finding but never linked
    the record, so the test still offered "Create finding" and a second
    finding followed).

    ``more_host_ids`` (the proposal's other hosts) are attached too, and
    ``more_evidence_ids`` (its other cited records) are linked when they
    showed an issue, are on nobody's finding, sit on a host the finding now
    has and belong to no other issue.  ``provenance`` goes on the history
    line ("— from agent proposal #N").  Every record is locked, with the
    issue's, in one statement BEFORE the finding (the one lock order).

    When the record's test confirms a scanner observation still on the host
    (v2.445.0), this IS that observation's promotion, on this host —
    ``promote_or_dismiss_vulnerability``, so it joins the issue's existing
    finding instead of making a second one for the same issue.  Otherwise it
    is ``FindingService.create_finding`` on the evidence's host (at the name
    the test was aimed at).  Either way the record is linked.  The row is
    locked: two clicks must not make two findings.  The caller commits.

    Joining never re-judges the issue (review 2026-10-01 R9): this is one
    host's result, so an existing finding moves only from open / retest to
    confirmed.  A finding the team concluded — accepted risk, remediated,
    false positive — keeps its status, and ``confirmed`` is never taken back
    to ``open``; the host and the evidence are attached either way."""
    from app.db.models_host_tests import HostTest
    from app.db.models_vulnerability import Vulnerability
    from app.services.finding_actions import promote_or_dismiss_vulnerability
    from app.services.finding_service import FindingService

    # Read first, unlocked, only to learn which records the promotion will
    # touch (immutable facts: host, test); then lock that whole set in id
    # order and re-read the record under its lock (N8, see the helper).
    target = (
        db.query(EvidenceRecord.host_id, EvidenceRecord.host_test_id)
        .filter(EvidenceRecord.id == evidence_id, EvidenceRecord.project_id == project_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Evidence record not found in this project")
    test = db.get(HostTest, target.host_test_id) if target.host_test_id else None
    issue_key = test.issue_key if test is not None else None
    others = sorted(set(more_evidence_ids) - {evidence_id})
    lock_issue_evidence(db, host_ids=[target.host_id], issue_key=issue_key, also=[evidence_id, *others])
    record = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.id == evidence_id, EvidenceRecord.project_id == project_id)
        .populate_existing().first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence record not found in this project")
    if record.finding_id is not None:
        raise HTTPException(status_code=409, detail=f"This evidence already belongs to finding #{record.finding_id}")
    if record.outcome != "finding":
        raise HTTPException(status_code=422, detail="Only evidence whose outcome is 'finding' can be promoted")
    vuln = None
    if test is not None and test.issue_key:
        vuln = (
            db.query(Vulnerability)
            .filter(Vulnerability.host_id == record.host_id, Vulnerability.issue_key == test.issue_key)
            .order_by(Vulnerability.id).first()
        )
    said = f" — {provenance}" if provenance else ""
    extra_hosts = [hid for hid in dict.fromkeys(more_host_ids) if hid is not None and hid != record.host_id]
    svc = FindingService(db)
    if vuln is not None:
        # Made by this call, or the issue's existing one joined (read after
        # the evidence lock, as the promotion's own lookup is).
        created = svc.issue_finding(vuln, project_id) is None
        # The observation rates its own finding: the caller's severity is the
        # test's priority, which nobody chose as a severity (the linked-test
        # control shows no selector).  It is used only on the path below.
        finding = promote_or_dismiss_vulnerability(
            db, vuln=vuln, project_id=project_id, actor_id=actor_id, severity=None,
            status=status, scope="host", summary=f"Confirmed by test evidence #{record.id}{said}",
            on_join="confirm",
        )
        if extra_hosts:
            svc.add_hosts(finding=finding, host_ids=extra_hosts, actor_id=actor_id)
    else:
        if not (title or "").strip() or not severity:
            raise HTTPException(status_code=422, detail="title and severity are required for a new finding")
        created = True
        finding = svc.create_finding(
            project_id=project_id, title=title, severity=severity, actor_id=actor_id,
            status=status, source="execution", summary=f"Created from evidence #{record.id}{said}",
            # The person who confirmed it owns it, as a promoted observation's
            # finding does (`promote_vulnerability`): unowned, a finding made
            # from a test result was on nobody's Operations list.
            owner_id=actor_id,
        )
        names = {record.host_id: test.name_id} if test is not None and test.name_id else None
        svc._attach_hosts(finding, [record.host_id, *extra_hosts], names_by_host=names)
    record.finding_id = finding.id
    db.flush()
    if others:
        _link_cited_evidence(db, project_id, others, finding, issue_key)
    return EvidencePromotion(finding=finding, joined_issue=vuln is not None, created=created)


def _link_cited_evidence(
    db: Session, project_id: int, evidence_ids: Sequence[int], finding: Finding, issue_key: Optional[str],
) -> None:
    """Link the other records a promotion cites (already locked by it) that
    showed an issue, are on no finding, sit on a host the finding has, and
    were not recorded for a test of a DIFFERENT issue — that one's result
    stays to promote on its own."""
    from app.db.models_host_tests import HostTest

    attached = {hid for (hid,) in db.query(FindingHost.host_id).filter(FindingHost.finding_id == finding.id)}
    rows = (
        db.query(EvidenceRecord, HostTest.issue_key)
        .outerjoin(HostTest, HostTest.id == EvidenceRecord.host_test_id)
        .filter(EvidenceRecord.id.in_(evidence_ids), EvidenceRecord.project_id == project_id)
        .populate_existing()
        .all()
    )
    for row, its_issue in rows:
        if (row.finding_id is None and row.outcome == "finding" and row.host_id in attached
                and (its_issue is None or its_issue == issue_key)):
            row.finding_id = finding.id
    db.flush()


def serialize_evidence(record: EvidenceRecord, *, preview: bool = True) -> dict:
    """One record.  ``preview=False`` answers ``raw_output_preview: null``
    without reading it, and ``raw_output_truncated_in_preview`` then says
    whether there is any output to read whole (``/raw``, ``/raw-chunk``)."""
    host = record.host
    if not preview:
        return {
            **_serialize_evidence(record, host, None),
            "raw_output_truncated_in_preview": bool(record.raw_output_bytes),
        }
    return _serialize_evidence(record, host, record.raw_output_preview)


def _serialize_evidence(record: EvidenceRecord, host, raw_output_preview) -> dict:
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
        "raw_output_preview": raw_output_preview,
        "raw_output_bytes": record.raw_output_bytes,
        "raw_output_sha256": record.raw_output_sha256,
        # Bytes against bytes: comparing the preview's characters with the
        # byte size flagged any short non-ASCII output as cut.
        "raw_output_truncated_in_preview": bool(
            record.raw_output_bytes and raw_output_preview is not None
            and len(raw_output_preview.encode("utf-8", errors="replace")) < record.raw_output_bytes
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
