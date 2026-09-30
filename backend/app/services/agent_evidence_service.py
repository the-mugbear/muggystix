"""Agent evidence records: what an agent ran against a host, and what came back (v2.436.0).

Recorded directly and never changed — it is the audit trail (acceptance run
2026-09-30, R1: a structured result used to need a plan, an entry and an
execution run).  The raw output is kept as a file under
``uploads/evidence/<project>/``; the row carries a short preview so a list
never ships megabytes.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceOutcome, EvidenceRecord

#: Raw output kept per record — a tool's output, not a disk image.
RAW_OUTPUT_MAX_BYTES = 5 * 1024 * 1024
#: Characters of raw output carried inline on the record.
PREVIEW_CHARS = 2000

OUTCOMES = [o.value for o in EvidenceOutcome]


def _evidence_dir(project_id: int) -> str:
    return os.path.join(settings.UPLOAD_DIR, "evidence", str(project_id))


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
) -> EvidenceRecord:
    """Validate the targets belong to the project, store the raw output as a
    file, and add the row.  The caller commits."""
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

    path = size = preview = None
    if raw_output:
        data = raw_output.encode("utf-8", errors="replace")
        if len(data) > RAW_OUTPUT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"raw_output is {len(data)} bytes; the limit is {RAW_OUTPUT_MAX_BYTES}. "
                       "Trim it, or upload the file if it is a supported scanner format.",
            )
        directory = _evidence_dir(project_id)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{uuid.uuid4().hex}.txt")
        with open(path, "wb") as fh_out:
            fh_out.write(data)
        size = len(data)
        preview = raw_output[:PREVIEW_CHARS]

    record = EvidenceRecord(
        project_id=project_id, host_id=host_id, finding_id=finding_id,
        finding_host_id=finding_host_id, tool=tool.strip()[:100], command=command,
        outcome=outcome, summary=summary.strip(), raw_output_path=path,
        raw_output_bytes=size, raw_output_preview=preview, observed_ip=observed_ip,
        executed_at=executed_at, agent_session_id=agent_session_id,
        recorded_by_user_id=recorded_by_user_id,
        agent_model=(agent_model or None) and agent_model[:100],
        agent_client=(agent_client or None) and agent_client[:100],
    )
    db.add(record)
    db.flush()
    return record


def list_evidence(
    db: Session,
    project_id: int,
    *,
    host_id: Optional[int] = None,
    finding_id: Optional[int] = None,
    agent_session_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[EvidenceRecord], int]:
    q = db.query(EvidenceRecord).filter(EvidenceRecord.project_id == project_id)
    if host_id is not None:
        q = q.filter(EvidenceRecord.host_id == host_id)
    if finding_id is not None:
        q = q.filter(EvidenceRecord.finding_id == finding_id)
    if agent_session_id is not None:
        q = q.filter(EvidenceRecord.agent_session_id == agent_session_id)
    total = q.count()
    rows = (
        q.order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
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


def read_raw_output(record: EvidenceRecord) -> str:
    if not record.raw_output_path or not os.path.isfile(record.raw_output_path):
        raise HTTPException(status_code=404, detail="This evidence record has no stored raw output")
    with open(record.raw_output_path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


def serialize_evidence(record: EvidenceRecord) -> dict:
    host = record.host
    return {
        "id": record.id,
        "host_id": record.host_id,
        "host_ip": host.ip_address if host is not None else None,
        "finding_id": record.finding_id,
        "finding_host_id": record.finding_host_id,
        "tool": record.tool,
        "command": record.command,
        "outcome": record.outcome,
        "summary": record.summary,
        "raw_output_preview": record.raw_output_preview,
        "raw_output_bytes": record.raw_output_bytes,
        "raw_output_truncated_in_preview": bool(
            record.raw_output_bytes and record.raw_output_preview is not None
            and len(record.raw_output_preview) < record.raw_output_bytes
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
