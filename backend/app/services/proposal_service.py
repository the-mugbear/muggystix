"""Agent proposals: changes to what the team concluded, decided by a person (v2.436.0).

The rule (the user, 2026-09-30): an agent's change to what the team has
CONCLUDED, or to what the client report says, is a proposal; everything else
it does directly.  Four kinds:

* ``finding_text`` — one report field (``field``); several proposals per
  field are kept so output from different models can be compared, and
  accepting one supersedes the field's other pending ones;
* ``finding_create`` — a new finding on hosts;
* ``observation_promote`` / ``observation_dismiss`` — a scanner observation
  to a finding, or a false positive (on this host, or the whole issue);
* ``endpoint_status`` — one finding endpoint's open / remediated / retest /
  false_positive.

Accepting runs the SAME service call a person's click makes
(``finding_actions``, ``FindingService``), as the person who accepts it — their
permissions, their name on the change.  If the target changed underneath and
that call refuses, the proposal stays pending with ``error`` set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_auth import User
from app.db.models_project import Notification
from app.db.models_findings import Finding, FindingHost, FindingHostStatus, FindingStatus
from app.db.models_proposals import (
    AgentProposal, EvidenceRecord, ProposalKind, ProposalSource, ProposalStatus,
)
from app.db.models_vulnerability import Vulnerability
from app.services.cvss_service import CvssError, normalize_cvss
from app.services.finding_actions import (
    apply_report_text, finding_actor, promote_or_dismiss_vulnerability,
)
from app.services.finding_service import FindingService, validate_severity
from app.services.report_text import REPORT_TEXT_FIELDS, REPORT_TEXT_MAX

#: Fields a finding_text proposal may carry.
TEXT_FIELDS = (*REPORT_TEXT_FIELDS, "cvss_vector")
ENDPOINT_STATUSES = [s.value for s in FindingHostStatus]
NEW_FINDING_STATUSES = (FindingStatus.OPEN.value, FindingStatus.CONFIRMED.value)


class Attribution:
    """Who proposed it: an agent session (its operator, client and model) or
    a person's in-app draft (their LLM provider's model)."""

    def __init__(
        self, *, user_id: Optional[int], session: Optional[AgentSession] = None,
        source: str = ProposalSource.AGENT.value, model: Optional[str] = None,
        client: Optional[str] = None, prompt_version: Optional[str] = None,
    ):
        self.user_id = user_id
        self.session = session
        self.source = source
        self.model = model or (session.generated_by_model if session is not None else None)
        self.client = client or (session.generated_by_tool if session is not None else None)
        self.prompt_version = prompt_version or (session.prompt_version if session is not None else None)

    def columns(self) -> dict:
        return {
            "source": self.source,
            "agent_session_id": self.session.id if self.session is not None else None,
            "proposed_by_user_id": self.user_id,
            "agent_model": (self.model or None) and self.model[:100],
            "agent_client": (self.client or None) and self.client[:100],
            "prompt_version": self.prompt_version,
        }


def _finding(db: Session, project_id: int, finding_id: int) -> Finding:
    finding = (
        db.query(Finding)
        .filter(Finding.id == finding_id, Finding.project_id == project_id)
        .first()
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found in this project")
    return finding


def _vulnerability(db: Session, project_id: int, vuln_id: int) -> Vulnerability:
    vuln = (
        db.query(Vulnerability)
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(Vulnerability.id == vuln_id, models.Host.project_id == project_id)
        .first()
    )
    if vuln is None:
        raise HTTPException(status_code=404, detail="Scanner observation not found in this project")
    return vuln


def _check_evidence(db: Session, project_id: int, evidence_ids: Optional[Iterable[int]]) -> Optional[List[int]]:
    ids = sorted(set(evidence_ids or []))
    if not ids:
        return None
    found = {
        eid for (eid,) in db.query(EvidenceRecord.id).filter(
            EvidenceRecord.id.in_(ids), EvidenceRecord.project_id == project_id,
        )
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Evidence records not found in this project: {missing}")
    return ids


def _add(db: Session, project_id: int, kind: str, who: Attribution, **cols) -> AgentProposal:
    proposal = AgentProposal(project_id=project_id, kind=kind, **who.columns(), **cols)
    db.add(proposal)
    db.flush()
    if proposal.finding_id is not None:
        _notify_finding_people(db, proposal, who)
    return proposal


def _notify_finding_people(db: Session, proposal: AgentProposal, who: Attribution) -> None:
    """Tell a finding's author and owner that an AI proposed changes to it
    (decision 6): ONE notification per person per agent session (per finding
    for an in-app draft), kept current as the run proposes more, never one
    per proposal.  The person whose agent it is is not told about their own
    run."""
    finding = db.get(Finding, proposal.finding_id)
    if finding is None:
        return
    recipients = {uid for uid in (finding.created_by_id, finding.owner_id) if uid and uid != who.user_id}
    if not recipients:
        return
    if who.session is not None:
        source_type, source_id = "agent_session", who.session.id
        scope = AgentProposal.agent_session_id == who.session.id
    else:
        source_type, source_id = "finding", finding.id
        scope = (AgentProposal.finding_id == finding.id) & AgentProposal.agent_session_id.is_(None)
    proposer = db.get(User, who.user_id) if who.user_id else None
    name = (proposer.full_name or proposer.username) if proposer is not None else "Someone"
    model = f" ({who.model})" if who.model else ""
    for uid in recipients:
        finding_ids = sorted({
            fid for (fid,) in db.query(AgentProposal.finding_id)
            .join(Finding, Finding.id == AgentProposal.finding_id)
            .filter(scope, (Finding.created_by_id == uid) | (Finding.owner_id == uid))
            .distinct()
        } | {finding.id})
        one = len(finding_ids) == 1
        title = (
            f"AI proposed changes to your finding: {finding.title}"[:255] if one
            else f"AI proposed changes to {len(finding_ids)} of your findings"
        )
        body = (
            f"{name}'s agent{model} proposed changes for review — accept or reject them."
            if who.session is not None else
            f"{name} drafted report text{model} for review — accept or reject it."
        )
        existing = (
            db.query(Notification)
            .filter(
                Notification.user_id == uid, Notification.type == "proposal",
                Notification.source_type == source_type, Notification.source_id == source_id,
                Notification.is_read.is_(False),
            )
            .first()
        )
        if existing is None:
            existing = Notification(
                user_id=uid, project_id=proposal.project_id, type="proposal",
                source_type=source_type, source_id=source_id, actor_id=who.user_id,
            )
            db.add(existing)
        existing.title = title
        existing.body = body
        # A single finding opens that finding; several open the Proposals page.
        existing.finding_id = finding_ids[0] if one else None
    db.flush()


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------

def propose_finding_text(
    db: Session, project_id: int, who: Attribution, *, finding_id: int,
    fields: Dict[str, str], rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None,
) -> List[AgentProposal]:
    """One proposal per field.  A CVSS vector is validated now, so a malformed
    one is refused at once rather than at accept."""
    _finding(db, project_id, finding_id)
    if not fields:
        raise HTTPException(status_code=422, detail="Propose at least one field.")
    unknown = sorted(set(fields) - set(TEXT_FIELDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields {unknown}; allowed: {list(TEXT_FIELDS)}")
    ev = _check_evidence(db, project_id, evidence_ids)
    out = []
    for field, value in fields.items():
        value = (value or "").strip()
        if not value:
            raise HTTPException(status_code=422, detail=f"{field}: a proposal needs text (to clear a field, edit it yourself).")
        if len(value) > REPORT_TEXT_MAX:
            raise HTTPException(status_code=422, detail=f"{field}: longer than {REPORT_TEXT_MAX} characters.")
        if field == "cvss_vector":
            try:
                normalize_cvss(value, None)
            except CvssError as exc:
                raise HTTPException(status_code=422, detail=f"cvss_vector: {exc}")
        out.append(_add(
            db, project_id, ProposalKind.FINDING_TEXT.value, who,
            finding_id=finding_id, field=field, payload={"value": value},
            rationale=rationale, evidence_ids=ev,
        ))
    return out


def propose_finding(
    db: Session, project_id: int, who: Attribution, *, title: str, severity: str,
    host_ids: List[int], status: str = FindingStatus.OPEN.value,
    report_text: Optional[Dict[str, str]] = None, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None,
) -> AgentProposal:
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A finding needs a title.")
    validate_severity(severity)
    if status not in NEW_FINDING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(NEW_FINDING_STATUSES)}")
    ids = sorted(set(host_ids or []))
    if not ids:
        raise HTTPException(status_code=422, detail="A finding needs at least one host.")
    found = {
        hid for (hid,) in db.query(models.Host.id).filter(
            models.Host.id.in_(ids), models.Host.project_id == project_id,
        )
    }
    if len(found) != len(ids):
        raise HTTPException(status_code=404, detail=f"Hosts not in this project: {sorted(set(ids) - found)}")
    text = {k: v.strip() for k, v in (report_text or {}).items() if v and v.strip()}
    unknown = sorted(set(text) - set(REPORT_TEXT_FIELDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown report fields {unknown}")
    return _add(
        db, project_id, ProposalKind.FINDING_CREATE.value, who,
        payload={"title": title[:500], "severity": severity, "status": status,
                 "host_ids": ids, "report_text": text},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )


def propose_observation(
    db: Session, project_id: int, who: Attribution, *, vulnerability_id: int,
    action: str, scope: Optional[str] = None, severity: Optional[str] = None,
    summary: Optional[str] = None, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None,
) -> AgentProposal:
    if action not in ("promote", "dismiss"):
        raise HTTPException(status_code=422, detail="action must be promote or dismiss")
    if scope not in (None, "host", "issue"):
        raise HTTPException(status_code=422, detail="scope must be host or issue")
    if severity is not None:
        validate_severity(severity)
    _vulnerability(db, project_id, vulnerability_id)
    kind = ProposalKind.OBSERVATION_PROMOTE if action == "promote" else ProposalKind.OBSERVATION_DISMISS
    return _add(
        db, project_id, kind.value, who, vulnerability_id=vulnerability_id,
        payload={"scope": scope, "severity": severity, "summary": summary},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )


def propose_endpoint_status(
    db: Session, project_id: int, who: Attribution, *, finding_id: int,
    finding_host_id: int, host_status: str, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None,
) -> AgentProposal:
    if host_status not in ENDPOINT_STATUSES:
        raise HTTPException(status_code=422, detail=f"host_status must be one of {ENDPOINT_STATUSES}")
    _finding(db, project_id, finding_id)
    fh = db.get(FindingHost, finding_host_id)
    if fh is None or fh.finding_id != finding_id:
        raise HTTPException(status_code=404, detail="That endpoint is not on this finding")
    return _add(
        db, project_id, ProposalKind.ENDPOINT_STATUS.value, who,
        finding_id=finding_id, finding_host_id=finding_host_id,
        payload={"host_status": host_status},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def get_proposal(db: Session, project_id: int, proposal_id: int) -> AgentProposal:
    proposal = (
        db.query(AgentProposal)
        .filter(AgentProposal.id == proposal_id, AgentProposal.project_id == project_id)
        .first()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found in this project")
    return proposal


def _require_pending(proposal: AgentProposal) -> None:
    if proposal.status != ProposalStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"This proposal is already {proposal.status}.")


def _history_note(proposal: AgentProposal, text: Optional[str]) -> str:
    who = proposal.agent_model or "an agent"
    base = f"From agent proposal #{proposal.id} ({who})"
    return f"{text} — {base}" if text else base


def _apply(db: Session, proposal: AgentProposal, user: User, edited_value: Optional[str]) -> Optional[int]:
    """Run the change as ``user``; returns the finding it produced / touched."""
    project_id = proposal.project_id
    kind = proposal.kind
    payload = proposal.payload or {}
    if kind == ProposalKind.FINDING_TEXT.value:
        finding = _finding(db, project_id, proposal.finding_id)
        value = edited_value if edited_value is not None else payload.get("value")
        apply_report_text(finding, finding_actor(db, project_id, user), {proposal.field: value})
        return finding.id
    if kind == ProposalKind.FINDING_CREATE.value:
        finding = FindingService(db).create_finding(
            project_id=project_id, title=payload["title"], severity=payload["severity"],
            status=payload.get("status") or FindingStatus.OPEN.value,
            host_ids=payload.get("host_ids") or [], actor_id=user.id,
        )
        text = payload.get("report_text") or {}
        if text:
            # The accepter created it, so they are its author and may write it.
            apply_report_text(finding, finding_actor(db, project_id, user), text)
        return finding.id
    if kind in (ProposalKind.OBSERVATION_PROMOTE.value, ProposalKind.OBSERVATION_DISMISS.value):
        vuln = _vulnerability(db, project_id, proposal.vulnerability_id)
        status = (
            FindingStatus.CONFIRMED.value if kind == ProposalKind.OBSERVATION_PROMOTE.value
            else FindingStatus.FALSE_POSITIVE.value
        )
        finding = promote_or_dismiss_vulnerability(
            db, vuln=vuln, project_id=project_id, actor_id=user.id,
            severity=payload.get("severity"), status=status,
            summary=_history_note(proposal, payload.get("summary")), scope=payload.get("scope"),
        )
        return finding.id
    if kind == ProposalKind.ENDPOINT_STATUS.value:
        finding = _finding(db, project_id, proposal.finding_id)
        FindingService(db).set_endpoint_status(
            finding=finding, finding_host_id=proposal.finding_host_id,
            host_status=payload["host_status"], actor_id=user.id,
        )
        return finding.id
    raise HTTPException(status_code=422, detail=f"Unknown proposal kind {kind!r}")


def accept_proposal(
    db: Session, proposal: AgentProposal, user: User, *, edited_value: Optional[str] = None,
    note: Optional[str] = None,
) -> AgentProposal:
    """Apply the change as ``user``.  A refusal from the underlying action (no
    right to edit that finding's text, the observation already promoted…) is
    recorded on the proposal, which stays pending, and re-raised.  The caller
    commits."""
    _require_pending(proposal)
    if edited_value is not None and proposal.kind != ProposalKind.FINDING_TEXT.value:
        raise HTTPException(status_code=422, detail="Only a report-text proposal can be accepted with an edit.")
    savepoint = db.begin_nested()
    try:
        result_finding_id = _apply(db, proposal, user, edited_value)
        savepoint.commit()
    except HTTPException as exc:
        savepoint.rollback()
        if exc.status_code != 403:
            proposal.error = str(exc.detail)[:2000]
        raise
    now = datetime.now(timezone.utc)
    proposal.status = ProposalStatus.ACCEPTED.value
    proposal.decided_by_user_id = user.id
    proposal.decided_at = now
    proposal.decision_note = note
    proposal.result_finding_id = result_finding_id
    proposal.error = None
    if edited_value is not None:
        proposal.payload = {**(proposal.payload or {}), "accepted_value": edited_value}
    if proposal.kind == ProposalKind.FINDING_TEXT.value:
        # The field's other pending proposals were drafted against text that
        # has now changed: kept for comparison, no longer "needs review".
        db.query(AgentProposal).filter(
            AgentProposal.finding_id == proposal.finding_id,
            AgentProposal.field == proposal.field,
            AgentProposal.kind == ProposalKind.FINDING_TEXT.value,
            AgentProposal.status == ProposalStatus.PENDING.value,
            AgentProposal.id != proposal.id,
        ).update(
            {"status": ProposalStatus.SUPERSEDED.value, "decided_at": now},
            synchronize_session=False,
        )
    return proposal


def reject_proposal(db: Session, proposal: AgentProposal, user: User, *, note: Optional[str] = None) -> AgentProposal:
    _require_pending(proposal)
    proposal.status = ProposalStatus.REJECTED.value
    proposal.decided_by_user_id = user.id
    proposal.decided_at = datetime.now(timezone.utc)
    proposal.decision_note = note
    return proposal


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def list_proposals(
    db: Session, project_id: int, *, status: Optional[str] = None, kind: Optional[str] = None,
    finding_id: Optional[int] = None, host_id: Optional[int] = None,
    agent_session_id: Optional[int] = None, limit: int = 100, offset: int = 0,
) -> Tuple[List[AgentProposal], int]:
    q = db.query(AgentProposal).filter(AgentProposal.project_id == project_id)
    if status:
        q = q.filter(AgentProposal.status == status)
    if kind:
        q = q.filter(AgentProposal.kind == kind)
    if finding_id is not None:
        q = q.filter(AgentProposal.finding_id == finding_id)
    if host_id is not None:
        # A host's proposals: its observations, and endpoints on it.
        vuln_ids = db.query(Vulnerability.id).filter(Vulnerability.host_id == host_id)
        fh_ids = db.query(FindingHost.id).filter(FindingHost.host_id == host_id)
        q = q.filter(
            AgentProposal.vulnerability_id.in_(vuln_ids) | AgentProposal.finding_host_id.in_(fh_ids)
        )
    if agent_session_id is not None:
        q = q.filter(AgentProposal.agent_session_id == agent_session_id)
    total = q.count()
    rows = (
        q.options(
            selectinload(AgentProposal.proposed_by), selectinload(AgentProposal.decided_by),
            selectinload(AgentProposal.finding),
            selectinload(AgentProposal.vulnerability).selectinload(Vulnerability.host),
            selectinload(AgentProposal.finding_host).selectinload(FindingHost.host),
        )
        .order_by(AgentProposal.created_at.desc(), AgentProposal.id.desc())
        .offset(offset).limit(limit).all()
    )
    return rows, total


def pending_counts(db: Session, project_id: int) -> Dict[str, int]:
    rows = (
        db.query(AgentProposal.kind, func.count(AgentProposal.id))
        .filter(AgentProposal.project_id == project_id, AgentProposal.status == ProposalStatus.PENDING.value)
        .group_by(AgentProposal.kind)
        .all()
    )
    return {kind: n for kind, n in rows}


def serialize_proposal(proposal: AgentProposal, current: Optional[Dict[int, Finding]] = None) -> dict:
    """The row, plus for report text the finding's current value so a reviewer
    sees the difference without a second request."""
    current_value = None
    if proposal.kind == ProposalKind.FINDING_TEXT.value and current and proposal.finding_id in current:
        current_value = getattr(current[proposal.finding_id], proposal.field, None)
    name = lambda u: (u.full_name or u.username) if u is not None else None  # noqa: E731
    return {
        "id": proposal.id,
        "kind": proposal.kind,
        "status": proposal.status,
        "source": proposal.source,
        "finding_id": proposal.finding_id,
        "vulnerability_id": proposal.vulnerability_id,
        "finding_host_id": proposal.finding_host_id,
        "field": proposal.field,
        "payload": proposal.payload,
        "current_value": current_value,
        "target": _target(proposal),
        "rationale": proposal.rationale,
        "evidence_ids": proposal.evidence_ids or [],
        "agent_session_id": proposal.agent_session_id,
        "proposed_by": name(proposal.proposed_by),
        "agent_model": proposal.agent_model,
        "agent_client": proposal.agent_client,
        "prompt_version": proposal.prompt_version,
        "created_at": proposal.created_at,
        "decided_by": name(proposal.decided_by),
        "decided_at": proposal.decided_at,
        "decision_note": proposal.decision_note,
        "result_finding_id": proposal.result_finding_id,
        "error": proposal.error,
    }


def _target(proposal: AgentProposal) -> dict:
    """What a reviewer needs to recognise the target without opening it."""
    finding, vuln, fh = proposal.finding, proposal.vulnerability, proposal.finding_host
    host = (vuln.host if vuln is not None else None) or (fh.host if fh is not None else None)
    return {
        "finding_title": finding.title if finding is not None else None,
        "observation_title": vuln.title if vuln is not None else None,
        "host_id": host.id if host is not None else None,
        "host_ip": host.ip_address if host is not None else None,
    }


def current_findings(db: Session, proposals: Iterable[AgentProposal]) -> Dict[int, Finding]:
    ids = {p.finding_id for p in proposals if p.finding_id is not None}
    if not ids:
        return {}
    return {f.id: f for f in db.query(Finding).filter(Finding.id.in_(ids)).all()}
