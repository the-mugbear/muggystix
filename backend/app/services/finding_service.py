"""FindingService — the canonical create / promote / triage logic for the
Finding spine (foundation phase 5).

Promotion is the bridge from frictionless capture (an annotation thread) to
a durable, roll-up-able record.  The annotation thread stays as the
finding's evidence/discussion; the Finding carries severity + disposition +
owner + the cross-host M2M.
"""
from typing import List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.db.models import Annotation, Host, Scope
from app.db.models_findings import (
    Finding, FindingHost, FindingStatusHistory, FindingStatus, FindingSeverity,
    FindingSource, FindingHostStatus,
)
from app.services.status_history_service import record_status_transition


_VALID_SEVERITIES = {s.value for s in FindingSeverity}
_VALID_STATUSES = {s.value for s in FindingStatus}


def validate_severity(severity: str) -> str:
    if severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=422,
            detail=f"severity must be one of {sorted(_VALID_SEVERITIES)}",
        )
    return severity


def _validate_status(status: str) -> str:
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(_VALID_STATUSES)}",
        )
    return status


def _first_body_line(body: Optional[str]) -> str:
    """First non-empty line of a note body, or a fallback title."""
    for line in (body or "").splitlines():
        if line.strip():
            return line.strip()
    return "Promoted finding"


class FindingService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Project resolution for an annotation (which can target several things)
    # ------------------------------------------------------------------
    def _project_id_for_annotation(self, ann: Annotation) -> Optional[int]:
        if ann.project_id is not None:
            return ann.project_id
        if ann.host_id is not None:
            host = self.db.get(Host, ann.host_id)
            return host.project_id if host else None
        if ann.scope_id is not None:
            scope = self.db.get(Scope, ann.scope_id)
            return scope.project_id if scope else None
        # scan/port/plan-targeted annotations don't carry an obvious project
        # without another join; callers promote host/scope/project notes.
        return None

    # ------------------------------------------------------------------
    # Create / promote
    # ------------------------------------------------------------------
    def _attach_hosts(self, finding: Finding, host_ids: Sequence[int]) -> None:
        seen = {fh.host_id for fh in finding.hosts}
        requested = [hid for hid in host_ids if hid is not None and hid not in seen]
        if not requested:
            return
        # Cross-tenant guard (the single choke point all three write paths
        # share — create / promote / add-hosts).  host_ids are global,
        # sequential hosts_v2 ids; without this an analyst in project A could
        # attach project B's hosts to an A finding AND read back B's
        # IP/hostname via the response.  Same IDOR shape as the agent_activity
        # fix.  One query; reject the whole request if any host is foreign.
        valid = {
            r[0] for r in self.db.query(Host.id)
            .filter(Host.id.in_(requested), Host.project_id == finding.project_id)
            .all()
        }
        invalid = [hid for hid in requested if hid not in valid]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Hosts {invalid} are not in this project.",
            )
        for hid in requested:
            self.db.add(FindingHost(
                finding_id=finding.id, host_id=hid,
                host_status=FindingHostStatus.OPEN.value,
            ))
            seen.add(hid)

    def promote_annotation(
        self,
        *,
        annotation: Annotation,
        severity: str,
        actor_id: Optional[int],
        title: Optional[str] = None,
        status: str = FindingStatus.CONFIRMED.value,
        owner_id: Optional[int] = None,
        extra_host_ids: Optional[Sequence[int]] = None,
    ) -> Finding:
        """Promote an annotation thread into a Finding.  Classifying a note
        as a finding is itself a confirmation, so status defaults to
        ``confirmed``; severity is required (the one real new input)."""
        validate_severity(severity)
        _validate_status(status)
        project_id = self._project_id_for_annotation(annotation)
        if project_id is None:
            raise HTTPException(
                status_code=422,
                detail="Cannot resolve a project for this annotation; promote a "
                       "host-, scope-, or project-scoped note.",
            )
        # The thread root is the evidence anchor (== self for a root note).
        evidence_id = annotation.thread_root_id or annotation.id

        # Idempotent: a double-click / retry must not create a second finding
        # for the same note thread.  "References, never copies" (model
        # docstring) — one note-sourced finding per evidence thread root.
        existing = (
            self.db.query(Finding)
            .filter(
                Finding.evidence_annotation_id == evidence_id,
                Finding.source == FindingSource.NOTE.value,
            )
            .first()
        )
        if existing is not None:
            return existing

        derived_title = (title or _first_body_line(annotation.body))[:500]
        # A promoted finding shouldn't land "unassigned": carry the note
        # thread's existing work-assignee if it has one, else default to the
        # promoter — they're the one triaging it.  (Explicit owner_id wins.)
        effective_owner = owner_id or annotation.assignee_id or actor_id
        finding = Finding(
            project_id=project_id,
            title=derived_title,
            severity=severity,
            status=status,
            source=FindingSource.NOTE.value,
            owner_id=effective_owner,
            evidence_annotation_id=evidence_id,
            created_by_id=actor_id,
        )
        self.db.add(finding)
        self.db.flush()

        host_ids: List[int] = []
        if annotation.host_id is not None:
            host_ids.append(annotation.host_id)
        if extra_host_ids:
            host_ids.extend(extra_host_ids)
        self._attach_hosts(finding, host_ids)

        record_status_transition(
            self.db, history_model=FindingStatusHistory, fk_field="finding_id",
            entity_id=finding.id, from_status=None, to_status=status,
            changed_by_id=actor_id, summary="Promoted from note",
        )
        self.db.flush()
        return finding

    def create_finding(
        self,
        *,
        project_id: int,
        title: str,
        severity: str,
        actor_id: Optional[int],
        status: str = FindingStatus.OPEN.value,
        source: str = FindingSource.MANUAL.value,
        owner_id: Optional[int] = None,
        host_ids: Optional[Sequence[int]] = None,
        vuln_id: Optional[int] = None,
        exec_result_id: Optional[int] = None,
    ) -> Finding:
        validate_severity(severity)
        _validate_status(status)
        finding = Finding(
            project_id=project_id, title=title[:500], severity=severity,
            status=status, source=source, owner_id=owner_id,
            vuln_id=vuln_id, exec_result_id=exec_result_id, created_by_id=actor_id,
        )
        self.db.add(finding)
        self.db.flush()
        if host_ids:
            self._attach_hosts(finding, host_ids)
        record_status_transition(
            self.db, history_model=FindingStatusHistory, fk_field="finding_id",
            entity_id=finding.id, from_status=None, to_status=status,
            changed_by_id=actor_id,
        )
        self.db.flush()
        return finding

    # ------------------------------------------------------------------
    # Triage
    # ------------------------------------------------------------------
    def set_status(
        self, *, finding: Finding, status: str, actor_id: Optional[int],
        summary: Optional[str] = None,
    ) -> Finding:
        _validate_status(status)
        if status != finding.status:
            old = finding.status
            finding.status = status
            record_status_transition(
                self.db, history_model=FindingStatusHistory, fk_field="finding_id",
                entity_id=finding.id, from_status=old, to_status=status,
                changed_by_id=actor_id, summary=summary,
            )
        return finding

    def add_hosts(self, *, finding: Finding, host_ids: Sequence[int]) -> Finding:
        self._attach_hosts(finding, host_ids)
        self.db.flush()
        return finding

    def remove_host(self, *, finding: Finding, host_id: int) -> Finding:
        self.db.query(FindingHost).filter(
            FindingHost.finding_id == finding.id, FindingHost.host_id == host_id,
        ).delete(synchronize_session=False)
        return finding

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_findings(
        self, *, project_id: int,
        status: Optional[str] = None, severity: Optional[str] = None,
        owner_id: Optional[int] = None, source: Optional[str] = None,
        host_id: Optional[int] = None, limit: int = 100, offset: int = 0,
    ):
        # Eager-load what _serialize touches (each finding's hosts + their
        # Host rows, and the owner) so a page of findings — amplified by the
        # one-finding-many-hosts design — doesn't N+1.  Mirrors _load.
        q = (
            self.db.query(Finding)
            .options(
                selectinload(Finding.hosts).selectinload(FindingHost.host),
                selectinload(Finding.owner),
            )
            .filter(Finding.project_id == project_id)
        )
        if status:
            q = q.filter(Finding.status == status)
        if severity:
            q = q.filter(Finding.severity == severity)
        if owner_id is not None:
            q = q.filter(Finding.owner_id == owner_id)
        if source:
            q = q.filter(Finding.source == source)
        if host_id is not None:
            q = q.filter(Finding.hosts.any(FindingHost.host_id == host_id))
        total = q.count()
        rows = (
            q.order_by(Finding.created_at.desc())
            .offset(offset).limit(limit).all()
        )
        return rows, total
