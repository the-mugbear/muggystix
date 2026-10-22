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
  false_positive;
* ``subnet_details`` — a scope subnet's description and / or site (2.522.0;
  prod agent feedback #76, owner 2026-10-22).

Accepting runs the SAME service call a person's click makes
(``finding_actions``, ``FindingService``, ``subnet_details``), as the person who accepts it — their
permissions, their name on the change.  If the target changed underneath and
that call refuses, the proposal stays pending with ``error`` set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_auth import User
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingStatus, FindingVulnerability,
)
from app.db.models_proposals import (
    AgentProposal, EvidenceRecord, ProposalKind, ProposalSource, ProposalStatus,
)
from app.db.models_vulnerability import Vulnerability
from app.db.session import has_expired, persisted_ids
from app.services.cvss_service import CvssError, normalize_cvss
from app.services.finding_actions import (
    apply_report_text, finding_actor, promote_or_dismiss_vulnerability,
)
from app.services import report_images
from app.services.default_scope import project_scope
from app.services.finding_service import FindingService, validate_severity
from app.services.subnet_details import set_subnet_details
from app.services.report_text import (
    REPORT_TEXT_FIELDS, REPORT_TEXT_MAX, internal_reference_message, internal_references,
)

#: Evidence records one proposal may cite (review 2026-10-01 R11; the list
#: was uncapped and each id is checked and stored on the row).
EVIDENCE_IDS_MAX = 100

#: Fields a finding_text proposal may carry.
TEXT_FIELDS = (*REPORT_TEXT_FIELDS, "cvss_vector")
ENDPOINT_STATUSES = [s.value for s in FindingHostStatus]
NEW_FINDING_STATUSES = (FindingStatus.OPEN.value, FindingStatus.CONFIRMED.value)
#: What a ``subnet_details`` proposal may set, with the Scope page's own
#: limits (``SubnetUpdate``).
SUBNET_DETAIL_LIMITS = {"description": 1024, "site": 255}
#: Subnets one ``propose_subnet_details`` request may name (the page's bulk cap).
SUBNET_DETAILS_MAX = 200


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
    if len(ids) > EVIDENCE_IDS_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"A proposal cites at most {EVIDENCE_IDS_MAX} evidence records ({len(ids)} sent).",
        )
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
    """Add one proposal.  The caller notifies once for the whole request
    (:func:`_notify_finding_people`), not once per proposal: a report-text
    request is up to six proposals, and each notification re-reads the run's
    proposals."""
    proposal = AgentProposal(project_id=project_id, kind=kind, **who.columns(), **cols)
    db.add(proposal)
    db.flush()
    return proposal


def _notify_finding_people(db: Session, finding_id: Optional[int], who: Attribution) -> None:
    """Tell a finding's author and owner that an AI proposed changes to it
    (decision 6): ONE notification per person per agent session (per finding
    for an in-app draft), kept current as the run proposes more, never one
    per proposal.  The person whose agent it is is not told about their own
    run."""
    finding = db.get(Finding, finding_id) if finding_id is not None else None
    if finding is None:
        return
    recipients = {uid for uid in (finding.created_by_id, finding.owner_id) if uid and uid != who.user_id}
    # An author or owner who has since left the project or been deactivated is
    # not told: the notification carries the finding's title and the run's
    # proposals.  The same filter as a note's followers and a discussion's
    # participants — which also means a global admin with no membership row is
    # not told (they read the project, they are not on its notification lists).
    from app.services.notification_service import NotificationService

    recipients = NotificationService(db).current_members(finding.project_id, recipients)
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
        theirs = (Finding.created_by_id == uid) | (Finding.owner_id == uid)
        finding_ids = sorted({
            fid for (fid,) in db.query(AgentProposal.finding_id)
            .join(Finding, Finding.id == AgentProposal.finding_id)
            .filter(scope, theirs)
            .distinct()
        } | {
            # Promote / dismiss proposals name an observation, not a finding:
            # the finding is derived through ``finding_vulnerabilities``
            # (``_about_findings``).  A per-finding draft scope names its
            # finding, so this adds nothing there.
            fid for (fid,) in db.query(FindingVulnerability.finding_id)
            .join(AgentProposal, AgentProposal.vulnerability_id == FindingVulnerability.vuln_id)
            .join(Finding, Finding.id == FindingVulnerability.finding_id)
            .filter(scope, AgentProposal.kind.in_(_OBSERVATION_KINDS), theirs)
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
                user_id=uid, project_id=finding.project_id, type="proposal",
                source_type=source_type, source_id=source_id, actor_id=who.user_id,
            )
            db.add(existing)
        existing.title = title
        existing.body = body
        # A single finding opens that finding; several open the Proposals page.
        existing.finding_id = finding_ids[0] if one else None
    db.flush()


#: ``Notification.source_type`` of a "no finding yet" proposal notification;
#: ``source_id`` is the agent session.  Distinct from ``agent_session`` (a
#: person's own findings) so the two never overwrite each other, and so the
#: page can open the session's proposals unscoped — these are on nobody's
#: finding, which is exactly what ``scope=mine`` leaves out.
UNOWNED_PROPOSAL_SOURCE = "agent_session_new"


def _notify_project_admins(db: Session, project_id: int, who: Attribution) -> None:
    """Tell the project's admins about proposals that sit on NOBODY'S finding
    (review 2026-10-01 B9): a proposed new finding, or a promote / dismiss of
    a scanner observation that has no finding yet.  ``_notify_finding_people``
    has no author or owner to tell for those, so they waited unseen.

    Same shape as that function: ONE unread notification per admin per agent
    session, kept current as the run proposes more.  Admins are the project's
    admin members who are active; the person whose agent it is is not told
    about their own run."""
    recipients = {
        uid for (uid,) in db.query(ProjectMembership.user_id)
        .join(User, User.id == ProjectMembership.user_id)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.role == ProjectRole.ADMIN.value,
            User.is_active.is_(True),
        )
    } - {who.user_id}
    if not recipients:
        return
    if who.session is not None:
        source_type, source_id = UNOWNED_PROPOSAL_SOURCE, who.session.id
        scope = AgentProposal.agent_session_id == who.session.id
    else:
        source_type, source_id = "project", project_id
        scope = AgentProposal.agent_session_id.is_(None) & (AgentProposal.proposed_by_user_id == who.user_id)
    # "On no finding" as ``_about_findings`` derives it: the proposal names no
    # finding AND is not a promote / dismiss of an observation that evidences
    # one — that finding's author and owner are told by
    # ``_notify_finding_people``, and counting it here told the admins about
    # more unowned changes than the page they open lists.
    on_a_finding_by_observation = AgentProposal.kind.in_(_OBSERVATION_KINDS) & AgentProposal.vulnerability_id.in_(
        select(FindingVulnerability.vuln_id)
    )
    count = db.query(func.count(AgentProposal.id)).filter(
        AgentProposal.project_id == project_id, scope,
        AgentProposal.status == ProposalStatus.PENDING.value,
        AgentProposal.finding_id.is_(None),
        ~on_a_finding_by_observation,
        # A scope proposal is told about on its own (``_notify_scope_admins``).
        AgentProposal.kind != ProposalKind.SUBNET_DETAILS.value,
    ).scalar() or 0
    proposer = db.get(User, who.user_id) if who.user_id else None
    name = (proposer.full_name or proposer.username) if proposer is not None else "Someone"
    model = f" ({who.model})" if who.model else ""
    title = (
        "AI proposed a change that is on no finding yet" if count <= 1
        else f"AI proposed {count} changes that are on no finding yet"
    )
    body = (
        f"{name}'s agent{model} proposed new findings or scanner-observation decisions. "
        "No finding's author or owner reviews these — accept or reject them."
    )
    for uid in recipients:
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
                user_id=uid, project_id=project_id, type="proposal",
                source_type=source_type, source_id=source_id, actor_id=who.user_id,
            )
            db.add(existing)
        existing.title = title
        existing.body = body
    db.flush()


#: ``Notification.source_type`` of a scope-proposal notification; ``source_id``
#: is the agent session.  It opens the session's scope proposals on the
#: Proposals page (``/proposals?kind=subnet_details&…``), where "Accept all
#: shown" decides them together.
SCOPE_PROPOSAL_SOURCE = "agent_session_scope"


def _notify_scope_admins(db: Session, project_id: int, who: Attribution) -> None:
    """Tell the project's admins that an agent proposed subnet details — the
    scope is the project's, so no finding's author or owner is the reviewer.
    ONE unread notification per admin per agent session, kept current as the
    run proposes more (``_notify_project_admins``' shape); the operator is not
    told about their own run.  An analyst may decide them too."""
    if who.session is None:
        return
    recipients = {
        uid for (uid,) in db.query(ProjectMembership.user_id)
        .join(User, User.id == ProjectMembership.user_id)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.role == ProjectRole.ADMIN.value,
            User.is_active.is_(True),
        )
    } - {who.user_id}
    if not recipients:
        return
    count = db.query(func.count(AgentProposal.id)).filter(
        AgentProposal.project_id == project_id,
        AgentProposal.agent_session_id == who.session.id,
        AgentProposal.status == ProposalStatus.PENDING.value,
        AgentProposal.kind == ProposalKind.SUBNET_DETAILS.value,
    ).scalar() or 0
    proposer = db.get(User, who.user_id) if who.user_id else None
    name = (proposer.full_name or proposer.username) if proposer is not None else "Someone"
    model = f" ({who.model})" if who.model else ""
    title = (
        "AI proposed a subnet's description or site" if count <= 1
        else f"AI proposed descriptions or sites for {count} subnets"
    )
    body = f"{name}'s agent{model} proposed scope details for review — accept or reject them, together if you wish."
    for uid in recipients:
        existing = (
            db.query(Notification)
            .filter(
                Notification.user_id == uid, Notification.type == "proposal",
                Notification.source_type == SCOPE_PROPOSAL_SOURCE, Notification.source_id == who.session.id,
                Notification.is_read.is_(False),
            )
            .first()
        )
        if existing is None:
            existing = Notification(
                user_id=uid, project_id=project_id, type="proposal",
                source_type=SCOPE_PROPOSAL_SOURCE, source_id=who.session.id, actor_id=who.user_id,
            )
            db.add(existing)
        existing.title = title
        existing.body = body
    db.flush()


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------

def _refuse_internal_references(fields: Dict[str, str]) -> None:
    """A proposed section goes into the client report word for word, so one
    that names a BlueStick record ("Finding #277") is refused here: the
    proposer rewrites it, and the ids go in ``rationale``."""
    for field, value in fields.items():
        if field not in REPORT_TEXT_FIELDS:
            continue
        phrases = internal_references(value)
        if phrases:
            raise HTTPException(status_code=422, detail=f"{field}: {internal_reference_message(phrases)}")


def propose_finding_text(
    db: Session, project_id: int, who: Attribution, *, finding_id: int,
    fields: Dict[str, str], rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None, replaces: Optional[Iterable[int]] = None,
) -> List[AgentProposal]:
    """One proposal per field.  A CVSS vector is validated now, so a malformed
    one is refused at once rather than at accept — and so is a section that
    places an image (``![…](evidence:<id>)``) which is not this finding's."""
    finding = _finding(db, project_id, finding_id)
    if not fields:
        raise HTTPException(status_code=422, detail="Propose at least one field.")
    unknown = sorted(set(fields) - set(TEXT_FIELDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields {unknown}; allowed: {list(TEXT_FIELDS)}")
    ev = _check_evidence(db, project_id, evidence_ids)
    # A proposed section may keep, move or drop the images the current text
    # places (a dropped one prints under Evidence again); it may not reference
    # anything that is not an image of this finding.  Whether each is ticked
    # "In report" is checked when the proposal is accepted.
    # A tolerated spelling of a placement is rewritten to the one grammar
    # FIRST, so the check reads — and the proposal stores — what will be saved.
    fields = {
        f: report_images.normalise_references((v or "").strip()) if f in REPORT_TEXT_FIELDS else v
        for f, v in fields.items()
    }
    report_images.check_references(
        db, finding, {f: v for f, v in fields.items() if f in REPORT_TEXT_FIELDS}, require_marked=False,
    )
    _refuse_internal_references(fields)
    claimed = claim_replaced(db, project_id, who, replaces, [
        AgentProposal(kind=ProposalKind.FINDING_TEXT.value, finding_id=finding_id, field=f) for f in fields
    ])
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
        # The text the proposal was written against (2.454.0): the reviewer
        # compares the draft with what the field held then, and is told when
        # the field has changed since — accepting replaces the whole section.
        out.append(_add(
            db, project_id, ProposalKind.FINDING_TEXT.value, who,
            finding_id=finding_id, field=field,
            payload={"value": value, "base_value": getattr(finding, field, None)},
            rationale=rationale, evidence_ids=ev,
        ))
    mark_replaced(db, claimed, out)
    _notify_finding_people(db, finding_id, who)
    return out


def propose_finding(
    db: Session, project_id: int, who: Attribution, *, title: str, severity: str,
    host_ids: List[int], status: str = FindingStatus.OPEN.value,
    report_text: Optional[Dict[str, str]] = None, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None, replaces: Optional[Iterable[int]] = None,
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
    # Stored in the one placement grammar (see ``propose_finding_text``).
    text = {
        k: report_images.normalise_references(v.strip())
        for k, v in (report_text or {}).items() if v and v.strip()
    }
    unknown = sorted(set(text) - set(REPORT_TEXT_FIELDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown report fields {unknown}")
    # The limit ``propose_finding_text`` and the finding's own editor apply
    # (review 2026-10-01 R11): a new finding's text skipped it, so accepting
    # one stored text the editor would then refuse to save.
    for field, value in text.items():
        if len(value) > REPORT_TEXT_MAX:
            raise HTTPException(status_code=422, detail=f"{field}: longer than {REPORT_TEXT_MAX} characters.")
    # A finding that does not exist yet has no images to place.
    report_images.check_references(db, None, text, require_marked=False)
    _refuse_internal_references(text)
    claimed = claim_replaced(db, project_id, who, replaces, [AgentProposal(kind=ProposalKind.FINDING_CREATE.value)])
    proposal = _add(
        db, project_id, ProposalKind.FINDING_CREATE.value, who,
        payload={"title": title[:500], "severity": severity, "status": status,
                 "host_ids": ids, "report_text": text},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )
    mark_replaced(db, claimed, [proposal])
    _notify_project_admins(db, project_id, who)
    return proposal


def propose_observation(
    db: Session, project_id: int, who: Attribution, *, vulnerability_id: int,
    action: str, scope: Optional[str] = None, severity: Optional[str] = None,
    summary: Optional[str] = None, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None, replaces: Optional[Iterable[int]] = None,
) -> AgentProposal:
    if action not in ("promote", "dismiss"):
        raise HTTPException(status_code=422, detail="action must be promote or dismiss")
    if scope not in (None, "host", "issue"):
        raise HTTPException(status_code=422, detail="scope must be host or issue")
    if severity is not None:
        validate_severity(severity)
    _vulnerability(db, project_id, vulnerability_id)
    kind = ProposalKind.OBSERVATION_PROMOTE if action == "promote" else ProposalKind.OBSERVATION_DISMISS
    claimed = claim_replaced(db, project_id, who, replaces, [
        AgentProposal(kind=kind.value, vulnerability_id=vulnerability_id),
    ])
    proposal = _add(
        db, project_id, kind.value, who, vulnerability_id=vulnerability_id,
        payload={"scope": scope, "severity": severity, "summary": summary},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )
    mark_replaced(db, claimed, [proposal])
    # The findings this observation evidences, derived through
    # ``finding_vulnerabilities`` exactly as ``_about_findings`` does.
    finding_ids = [
        fid for (fid,) in db.query(FindingVulnerability.finding_id)
        .filter(FindingVulnerability.vuln_id == vulnerability_id)
        .distinct().order_by(FindingVulnerability.finding_id)
    ]
    if not finding_ids:
        # No finding, so no author or owner to tell: the project's admins (B9).
        _notify_project_admins(db, project_id, who)
    for finding_id in finding_ids:
        # The proposal is "needs review" on that finding (accepting a dismiss
        # drops an endpoint from the report), so its author and owner are told
        # like for any other proposal about it.  It used to tell nobody.
        _notify_finding_people(db, finding_id, who)
    return proposal


def propose_endpoint_status(
    db: Session, project_id: int, who: Attribution, *, finding_id: int,
    finding_host_id: int, host_status: str, rationale: Optional[str] = None,
    evidence_ids: Optional[Iterable[int]] = None, replaces: Optional[Iterable[int]] = None,
) -> AgentProposal:
    if host_status not in ENDPOINT_STATUSES:
        raise HTTPException(status_code=422, detail=f"host_status must be one of {ENDPOINT_STATUSES}")
    _finding(db, project_id, finding_id)
    fh = db.get(FindingHost, finding_host_id)
    if fh is None or fh.finding_id != finding_id:
        raise HTTPException(status_code=404, detail="That endpoint is not on this finding")
    claimed = claim_replaced(db, project_id, who, replaces, [
        AgentProposal(kind=ProposalKind.ENDPOINT_STATUS.value, finding_id=finding_id, finding_host_id=finding_host_id),
    ])
    proposal = _add(
        db, project_id, ProposalKind.ENDPOINT_STATUS.value, who,
        finding_id=finding_id, finding_host_id=finding_host_id,
        payload={"host_status": host_status},
        rationale=rationale, evidence_ids=_check_evidence(db, project_id, evidence_ids),
    )
    mark_replaced(db, claimed, [proposal])
    _notify_finding_people(db, finding_id, who)
    return proposal


def _scope_subnet(db: Session, project_id: int, cidr: str) -> models.Subnet:
    """The project's subnet declared as ``cidr`` — normalised as every writer
    stores it (``ip_network(strict=False)``: ``10.0.0.5`` is ``10.0.0.5/32``)."""
    import ipaddress

    try:
        normal = str(ipaddress.ip_network((cidr or "").strip(), strict=False))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{cidr!r} is not a network or an address.")
    scope = project_scope(db, project_id)
    subnet = (
        db.query(models.Subnet)
        .filter(models.Subnet.scope_id == scope.id, models.Subnet.cidr == normal)
        .first()
    ) if scope is not None else None
    if subnet is None:
        raise HTTPException(
            status_code=404,
            detail=f"{normal} is not declared in this project's scope (scope_list_subnets lists what is).",
        )
    return subnet


def propose_subnet_details(
    db: Session, project_id: int, who: Attribution, *, items: List[Dict[str, Optional[str]]],
    rationale: Optional[str] = None, evidence_ids: Optional[Iterable[int]] = None,
    replaces: Optional[Iterable[int]] = None,
) -> List[AgentProposal]:
    """One proposal per subnet: ``items`` are ``{cidr, description?, site?}``.

    Each sets at least one of the two, with text — clearing one is a
    person's edit, as with report text.  The subnet's values when proposed
    are kept (``base_description`` / ``base_site``): the reviewer sees what
    changes, and a bulk accept leaves a proposal whose subnet was edited since
    for a person to decide on its own.  All or nothing: one bad item refuses
    the request."""
    if not items:
        raise HTTPException(status_code=422, detail="Propose details for at least one subnet.")
    if len(items) > SUBNET_DETAILS_MAX:
        raise HTTPException(
            status_code=422, detail=f"At most {SUBNET_DETAILS_MAX} subnets per request ({len(items)} sent).",
        )
    planned = []
    seen = set()
    for item in items:
        subnet = _scope_subnet(db, project_id, item.get("cidr") or "")
        if subnet.id in seen:
            raise HTTPException(status_code=422, detail=f"{subnet.cidr} is named twice in one request.")
        seen.add(subnet.id)
        values = {}
        for field, limit in SUBNET_DETAIL_LIMITS.items():
            raw = item.get(field)
            if raw is None:
                continue
            value = raw.strip()
            if not value:
                raise HTTPException(
                    status_code=422,
                    detail=f"{subnet.cidr} {field}: a proposal needs text (to clear it, a person edits the subnet).",
                )
            if len(value) > limit:
                raise HTTPException(status_code=422, detail=f"{subnet.cidr} {field}: longer than {limit} characters.")
            values[field] = value
        if not values:
            raise HTTPException(status_code=422, detail=f"{subnet.cidr}: propose a description, a site, or both.")
        planned.append((subnet, values))
    ev = _check_evidence(db, project_id, evidence_ids)
    claimed = claim_replaced(db, project_id, who, replaces, [
        AgentProposal(kind=ProposalKind.SUBNET_DETAILS.value, subnet_id=subnet.id) for subnet, _ in planned
    ])
    out = [
        _add(
            db, project_id, ProposalKind.SUBNET_DETAILS.value, who, subnet_id=subnet.id,
            payload={**values, "base_description": subnet.description, "base_site": subnet.site},
            rationale=rationale, evidence_ids=ev,
        )
        for subnet, values in planned
    ]
    mark_replaced(db, claimed, out)
    _notify_scope_admins(db, project_id, who)
    return out


#: How many proposals one request may replace (a finding's six sections, with room).
REPLACES_MAX = 20


def _same_target(old: AgentProposal, new: AgentProposal) -> bool:
    if old.kind == ProposalKind.FINDING_TEXT.value:
        return new.kind == old.kind and (new.finding_id, new.field) == (old.finding_id, old.field)
    if old.kind == ProposalKind.FINDING_CREATE.value:
        return new.kind == old.kind
    if old.kind in _OBSERVATION_KINDS:
        return new.kind in _OBSERVATION_KINDS and new.vulnerability_id == old.vulnerability_id
    if old.kind == ProposalKind.SUBNET_DETAILS.value:
        return new.kind == old.kind and new.subnet_id == old.subnet_id
    return new.kind == old.kind and new.finding_host_id == old.finding_host_id


def claim_replaced(
    db: Session, project_id: int, who: Attribution, replaces: Optional[Iterable[int]],
    targets: List[AgentProposal],
) -> List[Tuple[AgentProposal, int]]:
    """The proposals ``replaces`` names, checked and locked BEFORE anything is
    written (2.519.0; prod agent feedback 2026-10-20: a corrected draft sat
    beside the wrong one).  ``targets`` are unsaved rows saying what the
    request will propose, in order; each claimed proposal is returned with
    the index of the target it gives way to (``mark_replaced``).

    Each must be the CALLER's (same proposer, same source — another's is a
    person's to decide), still pending, and about what one target is about
    (the same finding and section, the same new finding, the same
    observation, the same endpoint).  Locked as a decision locks
    (``lock_for_decision``), so it cannot be accepted at the same moment."""
    ids = sorted(set(replaces or []))
    if not ids:
        return []
    if len(ids) > REPLACES_MAX:
        raise HTTPException(status_code=422, detail=f"replaces names at most {REPLACES_MAX} proposals.")
    claimed = []
    for pid in ids:
        old = lock_for_decision(db, project_id, pid)
        if old.proposed_by_user_id != who.user_id or old.source != who.source:
            raise HTTPException(
                status_code=403,
                detail=f"Proposal #{pid} is not yours to replace: a person decides it.",
            )
        _require_pending(old)
        index = next((i for i, t in enumerate(targets) if _same_target(old, t)), None)
        if index is None:
            raise HTTPException(
                status_code=422,
                detail=f"Proposal #{pid} is about something this request proposes nothing for "
                       "(a replacement is about the same finding and section, observation, endpoint or subnet).",
            )
        claimed.append((old, index))
    return claimed


def mark_replaced(db: Session, claimed: List[Tuple[AgentProposal, int]], new: List[AgentProposal]) -> None:
    """Each claimed proposal becomes ``superseded``, pointing at the new one
    in its place; nobody decided it."""
    now = datetime.now(timezone.utc)
    for old, index in claimed:
        old.status = ProposalStatus.SUPERSEDED.value
        old.replaced_by_id = new[index].id
        old.decided_at = now
    if claimed:
        db.flush()


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def get_proposal(db: Session, project_id: int, proposal_id: int, *, for_update: bool = False) -> AgentProposal:
    """``for_update`` (deciding) locks the row until the caller commits, so two
    accepts of one proposal cannot both pass the pending check — without it a
    proposed finding was created twice.  ``populate_existing``: a row already
    in the session would otherwise be checked at its pre-lock status."""
    q = db.query(AgentProposal).filter(
        AgentProposal.id == proposal_id, AgentProposal.project_id == project_id,
    )
    if for_update:
        q = q.with_for_update().populate_existing()
    proposal = q.first()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found in this project")
    return proposal


def lock_for_decision(db: Session, project_id: int, proposal_id: int) -> AgentProposal:
    """The proposal, locked for a decision — the ONE way a decide path (single
    accept, reject, bulk) takes its locks.

    A report-text proposal locks its FINDING first, then itself (review
    2026-10-02 H1).  Accepting one writes the finding and then supersedes the
    field's other pending drafts; with only the proposal row locked, two
    reviewers accepting two drafts of one field each held their own proposal,
    one wrote the finding, the other waited on it, and the first's supersede
    then waited on the second's proposal — a deadlock Postgres broke with a
    500.  With the finding as the common lock the second reviewer waits here,
    and then reads their proposal as it now is (``populate_existing``):
    superseded, a 409 from the pending check.

    The kind and finding are read without a lock; neither changes after a
    proposal is created.  ``FOR NO KEY UPDATE``: deciders exclude each other,
    while a comment or history row referencing the finding is not held up.
    An endpoint-status proposal locks its ENDPOINT row first, then itself
    (comprehensive review 2026-10-16, A3): accepting it locks the endpoint in
    ``set_endpoint_status``, and removing that endpoint holds the row while
    its DELETE cascades into the proposal row — proposal-then-endpoint
    against endpoint-then-proposal, a deadlock Postgres broke with a 500.
    Endpoint first on both sides: the second waits, then finds the endpoint
    gone (a 404 for the proposal, deleted with it) or the decision made.

    A subnet-details proposal locks its SUBNET first, then itself — the same
    shape: deleting the subnet cascades into the proposal, and accepting one
    supersedes the subnet's other pending proposals, so two reviewers deciding
    two proposals for one subnet wait on the subnet, not on each other.

    Other kinds lock only the proposal, as before — a promote / dismiss takes
    its own locks in its own order (evidence first, then the finding), and
    nothing they write is superseded by another proposal's accept."""
    head = (
        db.query(
            AgentProposal.kind, AgentProposal.finding_id, AgentProposal.finding_host_id, AgentProposal.subnet_id,
        )
        .filter(AgentProposal.id == proposal_id, AgentProposal.project_id == project_id)
        .first()
    )
    if head is not None and head.kind == ProposalKind.SUBNET_DETAILS.value and head.subnet_id:
        db.query(models.Subnet.id).filter(models.Subnet.id == head.subnet_id).with_for_update().first()
    if head is not None and head.kind == ProposalKind.FINDING_TEXT.value and head.finding_id:
        db.query(Finding.id).filter(
            Finding.id == head.finding_id, Finding.project_id == project_id,
        ).with_for_update(key_share=True).first()
    if head is not None and head.kind == ProposalKind.ENDPOINT_STATUS.value and head.finding_host_id:
        # FOR UPDATE: the lock `set_endpoint_status` takes, and the one a
        # DELETE of the row needs.
        db.query(FindingHost.id).filter(FindingHost.id == head.finding_host_id).with_for_update().first()
    return get_proposal(db, project_id, proposal_id, for_update=True)


def _require_pending(proposal: AgentProposal) -> None:
    if proposal.status != ProposalStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"This proposal is already {proposal.status}.")


def _history_note(proposal: AgentProposal, text: Optional[str]) -> str:
    base = _provenance(proposal)
    return f"{text} — {base}" if text else base[0].upper() + base[1:]


def _provenance(proposal: AgentProposal) -> str:
    """Where an accepted change came from, for the finding's history
    (decision 4): the proposal, its model, and its agent session — or that it
    was an in-app AI draft."""
    if proposal.source == ProposalSource.LLM_DRAFT.value:
        model = f", {proposal.agent_model}" if proposal.agent_model else ""
        return f"from AI draft #{proposal.id}{model}"
    detail = ", ".join(filter(None, [
        proposal.agent_model,
        f"agent session #{proposal.agent_session_id}" if proposal.agent_session_id else None,
    ]))
    return f"from agent proposal #{proposal.id}" + (f" ({detail})" if detail else "")


def _apply(db: Session, proposal: AgentProposal, user: User, edited_value: Optional[str]) -> Optional[int]:
    """Run the change as ``user``; returns the finding it produced / touched."""
    project_id = proposal.project_id
    kind = proposal.kind
    payload = proposal.payload or {}
    if kind == ProposalKind.FINDING_TEXT.value:
        finding = _finding(db, project_id, proposal.finding_id)
        value = edited_value if edited_value is not None else payload.get("value")
        if proposal.field in REPORT_TEXT_FIELDS and isinstance(value, str):
            # The reviewer's edit (or a proposal stored before placements were
            # normalised) may use a tolerated spelling: check what will be
            # STORED, or an image the text places would pass unexamined.
            value = report_images.normalise_references(value.strip())
        sent = {proposal.field: value}
        if proposal.field in REPORT_TEXT_FIELDS:
            # Checked again now: an image may have been deleted since the
            # proposal was made, and one that nobody ticked "In report" is
            # never ticked by an accept — the refusal says what to do.
            report_images.check_references(db, finding, {proposal.field: value}, require_marked=True)
        if proposal.field == "cvss_vector":
            # A 3.x / 2.0 vector computes its score; a 4.0 one keeps the score
            # it is given, and the old score belonged to the old vector.
            # Nobody reviewing a vector proposal sees a score, so leave it
            # empty (shown as missing) rather than wrong.
            sent["cvss_score"] = None
        # Each changed field's history line names the proposal, so the finding
        # shows an AI wrote it — on the line the edit writes, not a second one.
        edited = "(edited on accept) " if edited_value is not None else ""
        apply_report_text(
            db, finding, finding_actor(db, project_id, user), sent,
            provenance=f"{edited}{_provenance(proposal)}",
        )
        return finding.id
    if kind == ProposalKind.FINDING_CREATE.value:
        finding = FindingService(db).create_finding(
            project_id=project_id, title=payload["title"], severity=payload["severity"],
            status=payload.get("status") or FindingStatus.OPEN.value,
            host_ids=payload.get("host_ids") or [], actor_id=user.id,
            summary=f"Created {_provenance(proposal)}",
        )
        text = payload.get("report_text") or {}
        if text:
            # The accepter created it, so they are its author and may write it.
            apply_report_text(
                db, finding, finding_actor(db, project_id, user), text, provenance=_provenance(proposal),
            )
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
            host_status=payload["host_status"], actor_id=user.id, note=_provenance(proposal),
        )
        return finding.id
    if kind == ProposalKind.SUBNET_DETAILS.value:
        subnet = db.get(models.Subnet, proposal.subnet_id) if proposal.subnet_id else None
        if subnet is None:
            raise HTTPException(status_code=404, detail="That subnet is no longer in the scope.")
        # The Scope page's own edit, as the accepter (a new site is theirs).
        set_subnet_details(
            db, project_id, subnet,
            description=payload.get("description"), site=payload.get("site"), user_id=user.id,
        )
        db.flush()
        return None
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
    if proposal.kind == ProposalKind.SUBNET_DETAILS.value:
        # The same for a subnet: the others were proposed against what it held.
        db.query(AgentProposal).filter(
            AgentProposal.subnet_id == proposal.subnet_id,
            AgentProposal.kind == ProposalKind.SUBNET_DETAILS.value,
            AgentProposal.status == ProposalStatus.PENDING.value,
            AgentProposal.id != proposal.id,
        ).update(
            {"status": ProposalStatus.SUPERSEDED.value, "decided_at": now},
            synchronize_session=False,
        )
    return proposal


COMPETING_DRAFTS_DETAIL = (
    "Several drafts of this field were selected — choose one on the finding."
)
COMPETING_SUBNET_DETAIL = (
    "Several proposals for this subnet were selected — accept one of them on its own."
)


def competing_drafts(db: Session, project_id: int, ids: Iterable[int]) -> Dict[int, str]:
    """The pending proposals among ``ids`` that compete with another one among
    ``ids`` — report text for the same finding and field, or details for the
    same subnet — each with why.  A bulk accept must not choose between them
    (decision 7): whichever came first would win and supersede the others."""
    rows = (
        db.query(AgentProposal.id, AgentProposal.kind, AgentProposal.finding_id,
                 AgentProposal.field, AgentProposal.subnet_id)
        .filter(
            AgentProposal.project_id == project_id,
            AgentProposal.id.in_(set(ids)),
            AgentProposal.kind.in_((ProposalKind.FINDING_TEXT.value, ProposalKind.SUBNET_DETAILS.value)),
            AgentProposal.status == ProposalStatus.PENDING.value,
        )
        .all()
    )
    groups: Dict[tuple, List[int]] = {}
    for pid, kind, fid, field, subnet_id in rows:
        key = (kind, subnet_id) if kind == ProposalKind.SUBNET_DETAILS.value else (kind, fid, field)
        groups.setdefault(key, []).append(pid)
    return {
        pid: COMPETING_SUBNET_DETAIL if key[0] == ProposalKind.SUBNET_DETAILS.value else COMPETING_DRAFTS_DETAIL
        for key, group in groups.items() if len(group) > 1 for pid in group
    }


REWRITTEN_SINCE_PROPOSED = "text_changed_since_proposed"
REWRITTEN_SINCE_PROPOSED_DETAIL = (
    "This section was rewritten after the draft was made — accepting would replace that text. "
    "Decide it on its own, with the current text beside the draft."
)


def text_changed_since_proposed(proposal: AgentProposal, current_value: Optional[str]) -> Optional[bool]:
    """Whether a report-text proposal's field no longer reads as it did when
    the draft was made — THE comparison (what a reviewer is warned by on a
    single proposal, and what a bulk accept skips by).

    ``None`` = not known: the proposal is of another kind, or was made before
    the field's text was recorded with it (``payload.base_value``, 2.454.0).
    Unknown is never "unchanged" and never "changed": a single proposal shows
    no warning for it, and a bulk accept does not skip it."""
    payload = proposal.payload or {}
    if proposal.kind != ProposalKind.FINDING_TEXT.value or "base_value" not in payload:
        return None
    return (payload.get("base_value") or "").strip() != (current_value or "").strip()


SUBNET_CHANGED_SINCE_PROPOSED_DETAIL = (
    "This subnet's description or site was changed after the proposal was made — accepting would "
    "replace that change. Decide it on its own, with the subnet's current values beside the proposal."
)


def left_pending_detail(proposal: AgentProposal) -> str:
    """What a bulk accept says about a proposal it left pending by
    :func:`rewritten_since_proposed`."""
    if proposal.kind == ProposalKind.SUBNET_DETAILS.value:
        return SUBNET_CHANGED_SINCE_PROPOSED_DETAIL
    return REWRITTEN_SINCE_PROPOSED_DETAIL


def subnet_changed_since_proposed(
    proposal: AgentProposal, description: Optional[str], site: Optional[str],
) -> Optional[bool]:
    """Whether a subnet-details proposal's subnet no longer holds what it held
    when proposed, for the fields the proposal sets — the report-text rule
    (:func:`text_changed_since_proposed`) for a subnet.  ``None`` for another kind."""
    if proposal.kind != ProposalKind.SUBNET_DETAILS.value:
        return None
    payload = proposal.payload or {}
    current = {"description": description, "site": site}
    return any(
        (payload.get(f"base_{field}") or "").strip() != (current[field] or "").strip()
        for field in SUBNET_DETAIL_LIMITS if field in payload
    )


def rewritten_since_proposed(db: Session, proposal: AgentProposal) -> bool:
    """A PENDING report-text proposal whose section a person has rewritten
    since the draft — read now, so call it with the decision's locks held
    (``lock_for_decision``: the finding, then the proposal).  A bulk accept
    leaves such a proposal pending (owner decision 2026-10-11): one click on
    "Accept all shown" must not replace text nobody compared the draft with,
    and report text has no history to get it back from.  A subnet-details
    proposal whose subnet was edited since is left the same way (its subnet
    is locked first)."""
    if proposal.kind == ProposalKind.SUBNET_DETAILS.value:
        if proposal.status != ProposalStatus.PENDING.value or proposal.subnet_id is None:
            return False
        row = (
            db.query(models.Subnet.description, models.Subnet.site)
            .filter(models.Subnet.id == proposal.subnet_id).first()
        )
        return row is not None and subnet_changed_since_proposed(proposal, row.description, row.site) is True
    if (
        proposal.kind != ProposalKind.FINDING_TEXT.value
        or proposal.status != ProposalStatus.PENDING.value
        or proposal.finding_id is None
        or proposal.field not in TEXT_FIELDS
    ):
        return False
    # The column, not the entity: a Finding already in the session would be
    # answered from its pre-lock state.
    current = (
        db.query(getattr(Finding, proposal.field))
        .filter(Finding.id == proposal.finding_id, Finding.project_id == proposal.project_id)
        .scalar()
    )
    return text_changed_since_proposed(proposal, current) is True


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
    agent_session_id: Optional[int] = None, mine_user_id: Optional[int] = None,
    limit: int = 100, offset: int = 0,
) -> Tuple[List[AgentProposal], int]:
    """``mine_user_id``: only that person's proposals (:func:`mine`)."""
    q = db.query(AgentProposal).filter(AgentProposal.project_id == project_id)
    if status:
        q = q.filter(AgentProposal.status == status)
    if kind:
        q = q.filter(AgentProposal.kind == kind)
    if finding_id is not None:
        q = q.filter(_about_findings([finding_id]))
    if mine_user_id is not None:
        q = q.filter(mine(project_id, mine_user_id))
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
        q.options(*_serialized_loads())
        .order_by(AgentProposal.created_at.desc(), AgentProposal.id.desc())
        .offset(offset).limit(limit).all()
    )
    return rows, total


_OBSERVATION_KINDS = (ProposalKind.OBSERVATION_PROMOTE.value, ProposalKind.OBSERVATION_DISMISS.value)


def findings_of(project_id: int, user_id: int):
    """The findings a person is told about (decision 6): those they authored
    or own.  A subquery, so it composes with :func:`_about_findings`."""
    return select(Finding.id).where(
        Finding.project_id == project_id,
        (Finding.created_by_id == user_id) | (Finding.owner_id == user_id),
    )


def mine(project_id: int, user_id: int):
    """A person's proposals — the ONE rule behind ``mine``, ``pending_mine``
    and the top-bar count: those about findings they authored or own
    (:func:`findings_of`), and those THEY made, through their agent's session
    or an in-app draft.  Without the second half a proposed new finding (on no
    finding yet) was on nobody's list but the admins': the operator whose
    agent proposed it saw an empty Proposals page and read the call as failed
    (2026-10-16)."""
    return _about_findings(findings_of(project_id, user_id)) | (AgentProposal.proposed_by_user_id == user_id)


def _about_findings(finding_ids):
    """The proposals ABOUT these findings (a list of ids, or a subquery of
    them) — the one definition behind a finding's Proposals section, "needs
    review" when issuing a report, and a person's own proposals.
    Those that name the finding, plus promote / dismiss proposals on a scanner
    observation that evidences it: dismissing one drops an endpoint from the
    report.  Derived through ``finding_vulnerabilities``, never stamped on
    the proposal, because the link can change after the proposal is made."""
    linked = select(FindingVulnerability.vuln_id).where(FindingVulnerability.finding_id.in_(finding_ids))
    return AgentProposal.finding_id.in_(finding_ids) | (
        AgentProposal.kind.in_(_OBSERVATION_KINDS) & AgentProposal.vulnerability_id.in_(linked)
    )


def pending_per_finding(db: Session, finding_ids: List[int]) -> Dict[int, int]:
    """Pending proposals per finding, by :func:`_about_findings` (a finding
    with any "needs review").  A UNION of (finding, proposal) pairs, so a
    proposal that both names a finding and evidences it counts once."""
    if not finding_ids:
        return {}
    pending = AgentProposal.status == ProposalStatus.PENDING.value
    direct = (
        db.query(AgentProposal.finding_id.label("fid"), AgentProposal.id.label("pid"))
        .filter(pending, AgentProposal.finding_id.in_(finding_ids))
    )
    via_observation = (
        db.query(FindingVulnerability.finding_id.label("fid"), AgentProposal.id.label("pid"))
        .join(AgentProposal, AgentProposal.vulnerability_id == FindingVulnerability.vuln_id)
        .filter(pending, AgentProposal.kind.in_(_OBSERVATION_KINDS),
                FindingVulnerability.finding_id.in_(finding_ids))
    )
    pairs = direct.union(via_observation).subquery()
    rows = db.query(pairs.c.fid, func.count()).group_by(pairs.c.fid).all()
    return {fid: n for fid, n in rows}


def has_pending_proposal_condition():
    """SQL: this ``Finding`` has a pending proposal about it — the same two
    routes as :func:`_about_findings` / :func:`pending_per_finding` (it names
    the finding, or promotes / dismisses an observation that evidences it), as
    a condition on ``Finding`` so a list can be filtered by it.  Operations'
    "Findings that need me" does; ``pending_per_finding`` then counts them for
    the rows shown, and ``test_operations_redesign`` pins the two together."""
    pending = AgentProposal.status == ProposalStatus.PENDING.value
    direct = (
        exists()
        .where(pending, AgentProposal.finding_id == Finding.id)
        .correlate(Finding)
    )
    via_observation = (
        exists()
        .where(
            pending,
            AgentProposal.kind.in_(_OBSERVATION_KINDS),
            AgentProposal.vulnerability_id == FindingVulnerability.vuln_id,
            FindingVulnerability.finding_id == Finding.id,
        )
        .correlate(Finding)
    )
    return or_(direct, via_observation)


def pending_counts(db: Session, project_id: int, *, mine_user_id: Optional[int] = None) -> Dict[str, int]:
    """Pending proposals per kind; with ``mine_user_id``, only that person's
    (:func:`mine`, the list's own rule)."""
    q = db.query(AgentProposal.kind, func.count(AgentProposal.id)).filter(
        AgentProposal.project_id == project_id, AgentProposal.status == ProposalStatus.PENDING.value,
    )
    if mine_user_id is not None:
        q = q.filter(mine(project_id, mine_user_id))
    return {kind: n for kind, n in q.group_by(AgentProposal.kind).all()}


def serialize_proposal(
    proposal: AgentProposal, current: Optional[Dict[int, Finding]] = None,
    replaces: Optional[Dict[int, List[int]]] = None,
) -> dict:
    """The row, plus for report text the finding's current value so a reviewer
    sees the difference without a second request.

    ``base_value`` is the field's text when the proposal was made (2.454.0);
    ``base_recorded`` is false for proposals made before it was kept, and then
    ``changed_since_proposed`` is null — unknown, never "unchanged"."""
    current_value = None
    if proposal.kind == ProposalKind.FINDING_TEXT.value and current and proposal.finding_id in current:
        current_value = getattr(current[proposal.finding_id], proposal.field, None)
    payload = proposal.payload or {}
    base_recorded = proposal.kind == ProposalKind.FINDING_TEXT.value and "base_value" in payload
    base_value = payload.get("base_value") if base_recorded else None
    changed_since_proposed = None
    if proposal.status == ProposalStatus.PENDING.value and current and proposal.finding_id in current:
        changed_since_proposed = text_changed_since_proposed(proposal, current_value)
    if proposal.status == ProposalStatus.PENDING.value and proposal.subnet is not None:
        changed_since_proposed = subnet_changed_since_proposed(
            proposal, proposal.subnet.description, proposal.subnet.site,
        )
    name = lambda u: (u.full_name or u.username) if u is not None else None  # noqa: E731
    return {
        "id": proposal.id,
        "kind": proposal.kind,
        "status": proposal.status,
        "source": proposal.source,
        "finding_id": proposal.finding_id,
        "vulnerability_id": proposal.vulnerability_id,
        "finding_host_id": proposal.finding_host_id,
        "subnet_id": proposal.subnet_id,
        "field": proposal.field,
        "payload": proposal.payload,
        "current_value": current_value,
        "base_value": base_value,
        "base_recorded": base_recorded,
        "changed_since_proposed": changed_since_proposed,
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
        # 2.519.0 — its proposer sent another in its place (`superseded`), and
        # the ones this one replaced.
        "replaced_by_id": proposal.replaced_by_id,
        "replaces_ids": (replaces or {}).get(proposal.id, []),
    }


def _target(proposal: AgentProposal) -> dict:
    """What a reviewer needs to recognise the target without opening it."""
    finding, vuln, fh = proposal.finding, proposal.vulnerability, proposal.finding_host
    host = (vuln.host if vuln is not None else None) or (fh.host if fh is not None else None)
    subnet = proposal.subnet
    return {
        "finding_title": finding.title if finding is not None else None,
        "observation_title": vuln.title if vuln is not None else None,
        "host_id": host.id if host is not None else None,
        "host_ip": host.ip_address if host is not None else None,
        # A subnet-details proposal: the subnet and what it holds NOW (the
        # proposal's payload has what it held when proposed).
        "subnet_cidr": subnet.cidr if subnet is not None else None,
        "subnet_description": subnet.description if subnet is not None else None,
        "subnet_site": subnet.site if subnet is not None else None,
    }


def _current_findings(db: Session, proposals: Iterable[AgentProposal]) -> Dict[int, Finding]:
    ids = {p.finding_id for p in proposals if p.finding_id is not None}
    if not ids:
        return {}
    return {f.id: f for f in db.query(Finding).filter(Finding.id.in_(ids)).all()}


def _serialized_loads():
    """What a serialized proposal reads beside its own columns — the ONE list,
    for the list's query and for ``serialize_many``'s read by id."""
    return (
        selectinload(AgentProposal.proposed_by), selectinload(AgentProposal.decided_by),
        selectinload(AgentProposal.finding),
        selectinload(AgentProposal.vulnerability).selectinload(Vulnerability.host),
        selectinload(AgentProposal.finding_host).selectinload(FindingHost.host),
        selectinload(AgentProposal.subnet),
    )


def serialize_many(db: Session, proposals: List[AgentProposal]) -> List[dict]:
    """Rows as every proposal route returns them, with each report-text
    proposal's ``current_value`` read in one query.

    Before or after the caller's commit (H-e, backlog 2026-10-22): rows a
    commit expired are read again together, by the ids the identity map
    still holds, with what the answer reads — never one SELECT per row and
    per relation.  Rows still loaded are serialized as they are."""
    if has_expired(proposals):
        ids = persisted_ids(proposals)
        loaded = {
            p.id: p for p in
            db.query(AgentProposal).options(*_serialized_loads()).filter(AgentProposal.id.in_(ids))
        }
        proposals = [loaded[i] for i in ids]
    current = _current_findings(db, proposals)
    replaces: Dict[int, List[int]] = {}
    ids = [p.id for p in proposals]
    if ids:
        for old_id, new_id in (
            db.query(AgentProposal.id, AgentProposal.replaced_by_id)
            .filter(AgentProposal.replaced_by_id.in_(ids)).order_by(AgentProposal.id)
        ):
            replaces.setdefault(new_id, []).append(old_id)
    return [serialize_proposal(p, current, replaces) for p in proposals]


def without_texts(rows: List[dict]) -> List[dict]:
    """Serialized rows with each report text replaced by its length
    (``value_chars``, ``base_value_chars``, ``report_text_chars``) — the
    agents' create answers and their list (2.519.0).  The texts are in
    ``GET /agent/proposals/{id}``.

    The full rows echoed every text — 8 to 11 KB for one finding's sections,
    43.8 KB for a list of 14 — which an agent's terminal cut off or moved to a
    file outside its working directory (prod feedback 2026-09-30, 2026-10-07,
    2026-10-20).  Changed in place and returned."""
    for row in rows:
        payload = dict(row.get("payload") or {})
        if row["kind"] == ProposalKind.FINDING_TEXT.value:
            row["value_chars"] = len(payload.get("value") or "")
            row["base_value_chars"] = len(row.get("base_value") or "")
            for key in ("payload", "current_value", "base_value"):
                row.pop(key, None)
        elif row["kind"] == ProposalKind.FINDING_CREATE.value and payload.get("report_text"):
            payload["report_text_chars"] = {k: len(v or "") for k, v in payload.pop("report_text").items()}
            row["payload"] = payload
    return rows


def serialize_created(db: Session, proposals: List[AgentProposal]) -> List[dict]:
    """What a create route answers to the agent that just proposed: the rows
    without the report text the caller sent and the text it would replace
    (``without_texts``)."""
    rows = without_texts(serialize_many(db, proposals))
    for row in rows:
        if row["kind"] == ProposalKind.FINDING_CREATE.value:
            # A proposed finding is not a finding: told nothing, an operator
            # looked for it on the Findings page and read the call as failed.
            row["note"] = NEW_FINDING_NOTE
    return rows


#: What the agent passes on after ``propose_finding`` (2026-10-16).
NEW_FINDING_NOTE = (
    "Proposed, not created: there is no finding until a person accepts this proposal. "
    "Tell your operator it is waiting on the Proposals page (and in this session's "
    "Proposals section) for someone to accept or reject. list_proposals shows the "
    "decision; an accepted one carries result_finding_id, the new finding."
)
