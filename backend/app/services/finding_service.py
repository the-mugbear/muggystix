"""FindingService — the canonical create / promote / triage logic for the
Finding spine (foundation phase 5).

Promotion is the bridge from frictionless capture (an annotation thread) to
a durable, roll-up-able record.  The annotation thread stays as the
finding's evidence/discussion; the Finding carries severity + disposition +
owner + the cross-host M2M.
"""
from typing import List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, case, select, asc, desc
from sqlalchemy.orm import Session, selectinload

from app.db.models import Annotation, Host, Scope, NoteStatus
from app.db.models_findings import (
    Finding, FindingHost, FindingStatusHistory, FindingStatus, FindingSeverity,
    FindingSource, FindingHostStatus,
)
from app.services.status_history_service import record_status_transition


_VALID_SEVERITIES = {s.value for s in FindingSeverity}
_VALID_STATUSES = {s.value for s in FindingStatus}
# Final dispositions that an analyst must justify when setting (the rationale is
# recorded in the status-history summary).  Working states (open/confirmed/
# retest) don't require one.
_TERMINAL_STATUSES = {
    FindingStatus.FALSE_POSITIVE.value,
    FindingStatus.ACCEPTED_RISK.value,
    FindingStatus.REMEDIATED.value,
}

# Status GROUPS the dashboards drill against. "active" is the working set an
# analyst still owns; "resolved" is every terminal disposition. Passed as the
# `status` filter value (status="active"/"resolved"); a real status filters
# exactly. Kept here so posture's active counts and the Findings list it links
# to share one definition.
_ACTIVE_STATUSES = {
    FindingStatus.OPEN.value,
    FindingStatus.CONFIRMED.value,
    FindingStatus.RETEST.value,
}
_STATUS_GROUPS = {"active": _ACTIVE_STATUSES, "resolved": _TERMINAL_STATUSES}


def _apply_status_filter(query, status: Optional[str]):
    """Apply a status filter that may be a real status OR a group keyword."""
    if not status:
        return query
    group = _STATUS_GROUPS.get(status)
    if group is not None:
        return query.filter(Finding.status.in_(group))
    return query.filter(Finding.status == status)


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


# --- Sortable list ordering --------------------------------------------------
# Severity/status sort by their meaningful rank (critical-first, open-first),
# not alphabetically. host_count is the per-finding blast radius via a
# correlated subquery (no extra join/group on the main query).
_SEVERITY_SORT = case(
    (Finding.severity == "critical", 0), (Finding.severity == "high", 1),
    (Finding.severity == "medium", 2), (Finding.severity == "low", 3),
    (Finding.severity == "info", 4), else_=5,
)
_STATUS_SORT = case(
    (Finding.status == "open", 0), (Finding.status == "confirmed", 1),
    (Finding.status == "retest", 2), (Finding.status == "remediated", 3),
    (Finding.status == "false_positive", 4), (Finding.status == "accepted_risk", 5),
    else_=6,
)
_HOST_COUNT_SORT = (
    select(func.count(FindingHost.id))
    .where(FindingHost.finding_id == Finding.id)
    .correlate(Finding).scalar_subquery()
)
_SORT_COLUMNS = {
    "severity": _SEVERITY_SORT,
    "status": _STATUS_SORT,
    "title": Finding.title,
    "host_count": _HOST_COUNT_SORT,
    "source": Finding.source,
    "created_at": Finding.created_at,
}
# Per-field default direction when the caller doesn't specify one (worst/most-
# relevant first): newest, most-severe, biggest-blast-radius lead.
_SORT_DEFAULT_DESC = {"created_at", "host_count"}


def _finding_order(sort: Optional[str], sort_dir: Optional[str]):
    col = _SORT_COLUMNS.get(sort or "")
    if col is None:
        return (Finding.created_at.desc(), Finding.id.desc())  # default: newest first
    if sort_dir in ("asc", "desc"):
        descending = sort_dir == "desc"
    else:
        descending = sort in _SORT_DEFAULT_DESC
    direction = desc if descending else asc
    return (direction(col), Finding.id.desc())


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

    def promote_vulnerability(
        self,
        *,
        vuln,
        project_id: int,
        actor_id: Optional[int],
        severity: Optional[str] = None,
        status: str = FindingStatus.CONFIRMED.value,
        owner_id: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> Finding:
        """Promote a scanner vulnerability into a Finding (references, never
        copies — Finding.vuln_id).  Severity defaults to the vuln's own
        severity (``unknown`` → ``info``, since findings have no unknown).
        Idempotent on (vuln_id, source='scanner') so a double-click / a
        promote-then-dismiss can't fork two findings for one vuln — pass a
        terminal ``status`` (false_positive / accepted_risk) to dismiss.
        """
        raw = severity or getattr(vuln.severity, "value", vuln.severity) or "medium"
        sev = "info" if str(raw).lower() == "unknown" else str(raw).lower()
        validate_severity(sev)
        _validate_status(status)

        existing = (
            self.db.query(Finding)
            .filter(
                Finding.vuln_id == vuln.id,
                Finding.source == FindingSource.SCANNER.value,
            )
            .first()
        )
        if existing is not None:
            # Already promoted — if the caller is dismissing/redispositioning,
            # honour the new status rather than silently returning stale.
            if status != existing.status:
                self.set_status(finding=existing, status=status, actor_id=actor_id,
                                summary=summary or "Re-dispositioned scanner finding")
            return existing

        finding = Finding(
            project_id=project_id,
            title=(vuln.title or "Vulnerability")[:500],
            severity=sev,
            status=status,
            source=FindingSource.SCANNER.value,
            owner_id=owner_id or actor_id,
            vuln_id=vuln.id,
            created_by_id=actor_id,
        )
        self.db.add(finding)
        self.db.flush()
        # Cross-host dedup: a scanner finding keyed by plugin_id is the SAME
        # issue wherever that plugin fires, so attach every host in the project
        # carrying it — "promote once" yields one finding spanning all affected
        # hosts (the spine's whole point). Plugin-less vulns attach just their
        # own host.
        host_ids = [vuln.host_id] if vuln.host_id else []
        if vuln.plugin_id:
            from app.db.models_vulnerability import Vulnerability as _Vuln
            sibling = (
                self.db.query(_Vuln.host_id)
                .join(Host, _Vuln.host_id == Host.id)
                .filter(Host.project_id == project_id, _Vuln.plugin_id == vuln.plugin_id)
                .distinct()
            )
            host_ids = list(dict.fromkeys(host_ids + [hid for (hid,) in sibling if hid is not None]))
        self._attach_hosts(finding, host_ids)
        record_status_transition(
            self.db, history_model=FindingStatusHistory, fk_field="finding_id",
            entity_id=finding.id, from_status=None, to_status=status,
            changed_by_id=actor_id,
            summary=summary or "Promoted from scanner vulnerability",
        )
        self.db.flush()
        return finding

    def preview_vulnerability_promotion(self, *, vuln, project_id: int) -> dict:
        """Blast radius of promoting this vuln, WITHOUT mutating anything.

        Promotion attaches every project host carrying the same plugin_id
        (§11 — the cross-host fan-out an icon-click used to do silently), so
        the UI shows the count + a sample before the analyst commits. Also
        reports whether the vuln is already promoted (the call would be a
        no-op / re-disposition).
        """
        from app.db.models_vulnerability import Vulnerability as _Vuln

        existing = (
            self.db.query(Finding)
            .filter(Finding.vuln_id == vuln.id, Finding.source == FindingSource.SCANNER.value)
            .first()
        )
        host_ids = [vuln.host_id] if vuln.host_id else []
        if vuln.plugin_id:
            sibling = (
                self.db.query(_Vuln.host_id)
                .join(Host, _Vuln.host_id == Host.id)
                .filter(Host.project_id == project_id, _Vuln.plugin_id == vuln.plugin_id)
                .distinct()
            )
            host_ids = list(dict.fromkeys(host_ids + [hid for (hid,) in sibling if hid is not None]))
        sample = []
        if host_ids:
            sample = [
                ip for (ip,) in (
                    self.db.query(Host.ip_address)
                    .filter(Host.id.in_(host_ids[:50]))
                    .order_by(Host.ip_address)
                    .limit(10)
                    .all()
                ) if ip
            ]
        return {
            "plugin_id": vuln.plugin_id,
            "affected_host_count": len(host_ids),
            "affected_host_sample": sample,
            "already_promoted": existing is not None,
            "finding_id": existing.id if existing is not None else None,
        }

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
            # Terminal determinations must be justified — the rationale lives in
            # the status-history summary (surfaced by the history endpoint and
            # the report).
            if status in _TERMINAL_STATUSES and not (summary and summary.strip()):
                raise HTTPException(
                    status_code=422,
                    detail=f"A justification is required to set a finding to '{status}'.",
                )
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
        host_id: Optional[int] = None, unowned: bool = False,
        search: Optional[str] = None,
        limit: int = 100, offset: int = 0,
        sort: Optional[str] = None, sort_dir: Optional[str] = None,
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
        q = _apply_status_filter(q, status)
        if severity:
            q = q.filter(Finding.severity == severity)
        if unowned:
            q = q.filter(Finding.owner_id.is_(None))
        elif owner_id is not None:
            q = q.filter(Finding.owner_id == owner_id)
        if source:
            q = q.filter(Finding.source == source)
        if host_id is not None:
            q = q.filter(Finding.hosts.any(FindingHost.host_id == host_id))
        if search and search.strip():
            q = q.filter(Finding.title.ilike(f"%{search.strip()}%"))
        total = q.count()
        q = q.order_by(*_finding_order(sort, sort_dir))
        rows = q.offset(offset).limit(limit).all()
        return rows, total

    def severity_counts(
        self, *, project_id: int,
        status: Optional[str] = None, owner_id: Optional[int] = None,
        source: Optional[str] = None, host_id: Optional[int] = None,
        unowned: bool = False, search: Optional[str] = None,
    ) -> dict:
        """Per-severity finding counts for the rollup header.  Respects every
        filter EXCEPT severity (the point is to show the full severity
        breakdown within the current status/source/host/owner scope) and
        ignores pagination."""
        q = (
            self.db.query(Finding.severity, func.count(Finding.id))
            .filter(Finding.project_id == project_id)
        )
        q = _apply_status_filter(q, status)
        if unowned:
            q = q.filter(Finding.owner_id.is_(None))
        elif owner_id is not None:
            q = q.filter(Finding.owner_id == owner_id)
        if source:
            q = q.filter(Finding.source == source)
        if host_id is not None:
            q = q.filter(Finding.hosts.any(FindingHost.host_id == host_id))
        if search and search.strip():
            q = q.filter(Finding.title.ilike(f"%{search.strip()}%"))
        return {sev: int(c) for sev, c in q.group_by(Finding.severity).all()}

    # ------------------------------------------------------------------
    # Comment / evidence thread (notes targeting the finding itself)
    # ------------------------------------------------------------------
    # A Finding hosts its own annotation thread so an analyst can refine it with
    # evidence (screenshots ride along via NoteAttachment) before it lands in a
    # report.  Same Annotation machinery as host notes, just a different target
    # column — finding_id instead of host_id.
    def list_finding_notes(self, finding_id: int, limit: int = 100) -> List[Annotation]:
        return (
            self.db.query(Annotation)
            .filter(Annotation.finding_id == finding_id)
            .options(selectinload(Annotation.author))
            .order_by(Annotation.created_at.asc())  # oldest-first reads as a thread
            .limit(limit)
            .all()
        )

    def create_finding_note(
        self, *, finding_id: int, user_id: int, body: str,
        parent_id: Optional[int] = None,
    ) -> Annotation:
        # Threading stays within one finding (mirrors the same-host guard on
        # host notes): a reply's parent must be a note on THIS finding, else a
        # status-change could notify across a project boundary.
        parent = None
        if parent_id is not None:
            parent = (
                self.db.query(Annotation)
                .filter(Annotation.id == parent_id, Annotation.finding_id == finding_id)
                .first()
            )
            if parent is None:
                raise ValueError("parent_id must reference a comment on the same finding")
        note = Annotation(
            finding_id=finding_id, user_id=user_id, body=body,
            status=NoteStatus.OPEN, parent_id=parent_id,
        )
        self.db.add(note)
        self.db.flush()  # assign note.id before stamping thread_root_id
        note.thread_root_id = (parent.thread_root_id or parent.id) if parent else note.id
        self.db.commit()
        self.db.refresh(note)
        self.db.refresh(note, attribute_names=["author"])
        return note
