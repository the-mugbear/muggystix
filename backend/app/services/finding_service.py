"""FindingService — the canonical create / promote / triage logic for the
Finding spine (foundation phase 5).

Promotion is the bridge from frictionless capture (an annotation thread) to
a durable, roll-up-able record.  The annotation thread stays as the
finding's evidence/discussion; the Finding carries severity + disposition +
owner + the cross-host M2M.
"""
from typing import Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, case, select, asc, desc
from sqlalchemy.orm import Session, selectinload

from app.db.models import Annotation, Host, Scope, NoteStatus
from app.db.models_findings import (
    ACTIVE_FINDING_STATUSES, Finding, FindingHost, FindingStatusHistory, FindingStatus, FindingSeverity,
    FindingSource, FindingHostStatus,
)
from app.db.models_vulnerability import severity_rank
from app.services.host_query_common import escape_like
from app.services.report_text import clip as clip_report_text, seed_report_text_from_vuln
from app.services.status_history_service import record_status_transition
from app.services.vuln_identity import issue_key_for


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
_ACTIVE_STATUSES = set(ACTIVE_FINDING_STATUSES)
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
_SEVERITY_SORT = severity_rank(Finding.severity)
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
    def _attach_hosts(
        self, finding: Finding, host_ids: Sequence[int],
        names_by_host: Optional[Dict[int, int]] = None,
    ) -> None:
        """``names_by_host`` (host_id -> dns_names.id) stamps the named endpoint
        on the FindingHost rows it covers (v2.323.0) — inherited from the
        evidencing vulnerability or plan entry, never guessed."""
        # Read the already-attached set from the DB rather than the ORM
        # relationship: on paths that attach to an EXISTING finding (a second
        # scanner corroborating an issue), `finding.hosts` can be a stale
        # selectin load from before this transaction's flushes, and an empty
        # `seen` then re-inserts and trips uq_finding_host_name.
        # v2.324.0 — identity is (host, named endpoint): the same issue on two
        # vhosts of one address is two affected endpoints, both kept.
        names_by_host = names_by_host or {}
        seen = {
            (r[0], r[1]) for r in self.db.query(FindingHost.host_id, FindingHost.name_id)
            .filter(FindingHost.finding_id == finding.id)
            .all()
        } if finding.id is not None else set()
        pairs = []
        for hid in host_ids:
            if hid is None:
                continue
            pair = (hid, names_by_host.get(hid))
            # One set check: ``pair not in pairs`` scanned the list, which is
            # quadratic in the hosts of a widespread issue (2.374.4 review R1).
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
        requested = sorted({hid for hid, _ in pairs})
        if not pairs:
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
        for hid, name_id in pairs:
            self.db.add(FindingHost(
                finding_id=finding.id, host_id=hid, name_id=name_id,
                host_status=FindingHostStatus.OPEN.value,
            ))

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
            # v2.379.0 — the note is the analyst's own account of the issue:
            # it seeds the report description, which they then edit.
            description=clip_report_text(annotation.body),
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
        only_this_host: bool = False,
        host_ids: Optional[Sequence[int]] = None,
    ) -> Finding:
        """Promote a scanner vulnerability into a Finding (references, never
        copies — Finding.vuln_id).  Severity defaults to the vuln's own
        severity (``unknown`` → ``info``, since findings have no unknown).
        Idempotent on (vuln_id, source='scanner') so a double-click / a
        promote-then-dismiss can't fork two findings for one vuln — pass a
        terminal ``status`` (false_positive / accepted_risk) to dismiss.

        ``only_this_host`` (v2.366.0): attach the row's OWN host instead of
        every host carrying the issue.  The finding is still the ISSUE's — one
        per ``dedup_key`` — so promoting the same issue later from another host
        joins this finding rather than forking one.  What it changes is the
        claim: "confirmed" is recorded for the host that was looked at, not for
        hosts nobody has verified.  The others stay untriaged scanner
        observations that say "Finding #N covers other hosts only".

        ``host_ids`` (v2.386.0): exactly these hosts — the bulk promotion from
        the Scanner observations list, where the operator ticked the hosts
        they verified.  The caller checks they carry the issue.
        """
        def _hosts_for(v, k):
            if host_ids is not None:
                return list(host_ids)
            if only_this_host:
                return [v.host_id] if v.host_id else []
            return self._issue_host_ids(v, project_id, k)

        raw = severity or getattr(vuln.severity, "value", vuln.severity) or "medium"
        sev = "info" if str(raw).lower() == "unknown" else str(raw).lower()
        validate_severity(sev)
        _validate_status(status)

        # Identity of the ISSUE, not of the scanner row — see vuln_identity.
        # This is what makes the Nessus row and the GreenBone row for one
        # problem converge on a single finding.
        key = issue_key_for(vuln)

        existing = (
            self.db.query(Finding)
            .filter(
                Finding.vuln_id == vuln.id,
                Finding.source == FindingSource.SCANNER.value,
            )
            .first()
        )
        if existing is None and key and not key.startswith("row:"):
            # A different scanner already promoted this same issue. Attach to
            # that finding as corroborating evidence instead of forking a
            # second record — which is what produced two entries in the client
            # report for one problem.
            existing = (
                self.db.query(Finding)
                .filter(
                    Finding.project_id == project_id,
                    Finding.source == FindingSource.SCANNER.value,
                    Finding.dedup_key == key,
                )
                .first()
            )
        if existing is not None:
            # Record this scanner's row as evidence even when the finding
            # already existed; corroboration is the thing worth keeping.
            self.attach_vulnerability(finding=existing, vuln=vuln)
            # A second scanner may see the issue on hosts the first one missed.
            self._attach_hosts(
                existing, _hosts_for(vuln, key),
                names_by_host=self._vuln_names_by_host(vuln),
            )
            # Already promoted — if the caller is dismissing/redispositioning,
            # honour the new status rather than silently returning stale.
            if status != existing.status:
                self.set_status(finding=existing, status=status, actor_id=actor_id,
                                summary=summary or "Re-dispositioned scanner finding")
            self.db.flush()
            return existing

        finding = Finding(
            project_id=project_id,
            title=(vuln.title or "Vulnerability")[:500],
            severity=sev,
            status=status,
            source=FindingSource.SCANNER.value,
            owner_id=owner_id or actor_id,
            vuln_id=vuln.id,
            dedup_key=key,
            created_by_id=actor_id,
        )
        seed_report_text_from_vuln(finding, vuln)
        self.db.add(finding)
        self.db.flush()
        self.attach_vulnerability(finding=finding, vuln=vuln)
        self._attach_hosts(
            finding, _hosts_for(vuln, key),
            names_by_host=self._vuln_names_by_host(vuln),
        )
        record_status_transition(
            self.db, history_model=FindingStatusHistory, fk_field="finding_id",
            entity_id=finding.id, from_status=None, to_status=status,
            changed_by_id=actor_id,
            summary=summary or "Promoted from scanner vulnerability",
        )
        self.db.flush()
        return finding

    def dismiss_vulnerability_on_host(
        self,
        *,
        vuln,
        project_id: int,
        actor_id: Optional[int],
        severity: Optional[str] = None,
        owner_id: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> Finding:
        """v2.360.0 — "false positive HERE": the judgment is about this host's
        observation, so it lands on this host's endpoint row and nowhere else.

        ``promote_vulnerability(status='false_positive')`` dismisses the ISSUE:
        one finding, attached to every host that carries it.  A backported
        package on one server says nothing about the others, and an analyst in
        one host's inspector had no way to say only that.

        * No finding for the issue yet → one is created ``false_positive`` and
          attached to THIS host only.  Nothing is asserted about other hosts;
          a later promotion from one of them finds this finding by its issue
          key, attaches the rest as ``open`` and re-dispositions the finding —
          while this endpoint keeps its own ``false_positive``.
        * A finding exists → this host is attached if it was not, its endpoint
          rows become ``false_positive``, and the FINDING'S status is left
          alone: it is the issue's, and the issue was not re-judged.
        """
        raw = severity or getattr(vuln.severity, "value", vuln.severity) or "medium"
        sev = "info" if str(raw).lower() == "unknown" else str(raw).lower()
        validate_severity(sev)
        key = issue_key_for(vuln)

        finding = (
            self.db.query(Finding)
            .filter(Finding.vuln_id == vuln.id, Finding.source == FindingSource.SCANNER.value)
            .first()
        )
        if finding is None and key and not key.startswith("row:"):
            finding = (
                self.db.query(Finding)
                .filter(
                    Finding.project_id == project_id,
                    Finding.source == FindingSource.SCANNER.value,
                    Finding.dedup_key == key,
                )
                .first()
            )
        created = finding is None
        if created:
            finding = Finding(
                project_id=project_id,
                title=(vuln.title or "Vulnerability")[:500],
                severity=sev,
                status=FindingStatus.FALSE_POSITIVE.value,
                source=FindingSource.SCANNER.value,
                owner_id=owner_id or actor_id,
                vuln_id=vuln.id,
                dedup_key=key,
                created_by_id=actor_id,
            )
            seed_report_text_from_vuln(finding, vuln)
            self.db.add(finding)
            self.db.flush()
        self.attach_vulnerability(finding=finding, vuln=vuln)
        self._attach_hosts(finding, [vuln.host_id], names_by_host=self._vuln_names_by_host(vuln))
        self.db.flush()

        rows = (
            self.db.query(FindingHost)
            .filter(FindingHost.finding_id == finding.id, FindingHost.host_id == vuln.host_id)
            .all()
        )
        host = self.db.query(Host.ip_address).filter(Host.id == vuln.host_id).first()
        label = host[0] if host else f"host {vuln.host_id}"
        changed = False
        for row in rows:
            if row.host_status != FindingHostStatus.FALSE_POSITIVE.value:
                row.host_status = FindingHostStatus.FALSE_POSITIVE.value
                changed = True
        reason = f": {summary}" if summary else ""
        if created:
            record_status_transition(
                self.db, history_model=FindingStatusHistory, fk_field="finding_id",
                entity_id=finding.id, from_status=None, to_status=finding.status,
                changed_by_id=actor_id,
                summary=f"Dismissed as false positive on {label} only{reason}",
            )
        elif changed:
            # Same shape set_endpoint_status writes: the finding's status did
            # not move, the endpoint's did, and the trail names the endpoint.
            self.db.add(FindingStatusHistory(
                finding_id=finding.id, from_status=finding.status, to_status=finding.status,
                changed_by_id=actor_id,
                summary=f"Endpoint {label}: false positive here{reason}",
            ))
        self.db.flush()
        return finding

    def attach_vulnerability(self, *, finding: Finding, vuln) -> None:
        """Record a scanner row as evidence for this finding. Idempotent."""
        from app.db.models_findings import FindingVulnerability

        exists = (
            self.db.query(FindingVulnerability.id)
            .filter(
                FindingVulnerability.finding_id == finding.id,
                FindingVulnerability.vuln_id == vuln.id,
            )
            .first()
        )
        if exists is None:
            self.db.add(
                FindingVulnerability(finding_id=finding.id, vuln_id=vuln.id)
            )
            self.db.flush()

    @staticmethod
    def _vuln_names_by_host(vuln) -> Optional[Dict[int, int]]:
        """The named endpoint the scanner row was observed at, keyed by its
        host (v2.323.0).  Sibling hosts fanned out by issue key get no name —
        their own scanner rows would have to say."""
        name_id = getattr(vuln, "name_id", None)
        if name_id is None or vuln.host_id is None:
            return None
        return {vuln.host_id: name_id}

    def _issue_host_ids(self, vuln, project_id: int, key: Optional[str]) -> list:
        """Every project host carrying this ISSUE.

        "Promote once, cover every affected host" is the spine's whole point,
        but this used to fan out on ``plugin_id`` — which is scanner-specific,
        so a Nessus promotion covered only the hosts Nessus scanned and the
        GreenBone-only hosts were silently left out of the finding. Fanning out
        on the issue key covers both, and still falls back to plugin_id for
        rows with no usable key (identical plugin id IS the same issue within
        one scanner).
        """
        from app.db.models_vulnerability import Vulnerability as _Vuln

        host_ids = [vuln.host_id] if vuln.host_id else []
        siblings = None

        if key and key.startswith("cve:"):
            cve = key.split(":", 1)[1]
            siblings = (
                self.db.query(_Vuln.host_id)
                .join(Host, _Vuln.host_id == Host.id)
                .filter(Host.project_id == project_id, func.upper(_Vuln.cve_id) == cve)
                .distinct()
            )
        elif vuln.plugin_id:
            # Title-keyed matching is done in Python (normalisation has no SQL
            # equivalent), so scope the scan to this scanner's plugin rather
            # than loading every vulnerability in the project.
            siblings = (
                self.db.query(_Vuln.host_id)
                .join(Host, _Vuln.host_id == Host.id)
                .filter(Host.project_id == project_id, _Vuln.plugin_id == vuln.plugin_id)
                .distinct()
            )

        if siblings is not None:
            host_ids = list(
                dict.fromkeys(host_ids + [hid for (hid,) in siblings if hid is not None])
            )
        return host_ids

    def preview_vulnerability_promotion(self, *, vuln, project_id: int) -> dict:
        """Blast radius of promoting this vuln, WITHOUT mutating anything.

        Promotion attaches every project host carrying the same ISSUE (§11 —
        the cross-host fan-out an icon-click used to do silently), so the UI
        shows the count + a sample before the analyst commits. Also reports
        whether the vuln is already promoted (the call would be a no-op /
        re-disposition).

        v2.239.1 — this fanned out on ``plugin_id`` while ``promote_vulnerability``
        fanned out via ``_issue_host_ids`` on the issue key. The preview
        therefore UNDER-REPORTED the blast radius: an issue seen by both
        Nessus and GreenBone previewed as "3 hosts" and then attached 7. A
        confirmation dialog that misstates what the action does is worse than
        no dialog. Both paths now call the same fan-out, so they agree by
        construction.
        """
        key = issue_key_for(vuln)

        existing = (
            self.db.query(Finding)
            .filter(Finding.vuln_id == vuln.id, Finding.source == FindingSource.SCANNER.value)
            .first()
        )
        if existing is None and key and not key.startswith("row:"):
            # Same lookup promote_vulnerability does — a finding promoted from
            # another host/scanner already covers this issue, so the UI must
            # offer "covered" rather than a fresh promote.
            existing = (
                self.db.query(Finding)
                .filter(
                    Finding.project_id == project_id,
                    Finding.source == FindingSource.SCANNER.value,
                    Finding.dedup_key == key,
                )
                .first()
            )
        host_ids = self._issue_host_ids(vuln, project_id, key)
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

        # Hosts the finding does NOT already cover. When re-promoting an
        # existing finding this is what actually changes; showing only the
        # total would imply the action touches hosts it already attached.
        already_attached = 0
        if existing is not None and host_ids:
            already_attached = (
                self.db.query(func.count(FindingHost.id))
                .filter(
                    FindingHost.finding_id == existing.id,
                    FindingHost.host_id.in_(host_ids),
                )
                .scalar()
            ) or 0

        host_ip = self.db.query(Host.ip_address).filter(Host.id == vuln.host_id).scalar()
        host_endpoint_status = None
        if existing is not None:
            states = {
                s for (s,) in self.db.query(FindingHost.host_status)
                .filter(FindingHost.finding_id == existing.id, FindingHost.host_id == vuln.host_id)
                .all()
            }
            if states:
                # Several named endpoints on one host: false positive only
                # when every one of them is.
                host_endpoint_status = (
                    FindingHostStatus.FALSE_POSITIVE.value
                    if states == {FindingHostStatus.FALSE_POSITIVE.value}
                    else sorted(states - {FindingHostStatus.FALSE_POSITIVE.value})[0]
                )

        return {
            "host_ip": str(host_ip) if host_ip is not None else None,
            "host_endpoint_status": host_endpoint_status,
            "plugin_id": vuln.plugin_id,
            "issue_key": key,
            "affected_host_count": len(host_ids),
            "affected_host_sample": sample,
            "new_host_count": max(len(host_ids) - already_attached, 0),
            "already_promoted": existing is not None,
            "finding_id": existing.id if existing is not None else None,
            "finding_status": existing.status if existing is not None else None,
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
        # v2.323.0 — an execution-sourced finding inherits the plan entry's
        # named endpoint: the finding anchors to the name, the result row
        # (observed_ip) is the evidence about the binding.
        names_by_host = None
        if exec_result_id is not None:
            from app.db.models_agent import TestExecutionResult, TestPlanEntry
            row = (
                self.db.query(TestPlanEntry.host_id, TestPlanEntry.name_id)
                .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
                .filter(TestExecutionResult.id == exec_result_id)
                .first()
            )
            if row and row[1] is not None:
                names_by_host = {row[0]: row[1]}
        if host_ids:
            self._attach_hosts(finding, host_ids, names_by_host=names_by_host)
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

    def set_endpoint_status(
        self, *, finding: Finding, finding_host_id: int, host_status: str,
        actor_id: Optional[int],
    ) -> Finding:
        """v2.349.0 — the per-endpoint disposition (design review item 7).

        A finding's status is the ISSUE's; each affected endpoint keeps its
        own state (open / remediated / retest) so confirmation or remediation
        on one host never implies it on the others.  The change is written to
        the finding's history with the endpoint named, so the trail shows
        which host moved.
        """
        allowed = {s.value for s in FindingHostStatus}
        if host_status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"host_status must be one of {sorted(allowed)}",
            )
        row = (
            self.db.query(FindingHost)
            .filter(FindingHost.finding_id == finding.id, FindingHost.id == finding_host_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Endpoint is not attached to this finding")
        if row.host_status == host_status:
            return finding
        old = row.host_status
        row.host_status = host_status
        label = row.host.ip_address if row.host else f"host {row.host_id}"
        if row.name is not None:
            label = f"{row.name.fqdn} on {label}"
        # Written directly: the shared transition helper is for the finding's
        # own status and skips a no-change move, which this is by design.
        self.db.add(FindingStatusHistory(
            finding_id=finding.id, from_status=finding.status, to_status=finding.status,
            changed_by_id=actor_id, summary=f"Endpoint {label}: {old} → {host_status}",
        ))
        self.db.flush()
        return finding

    def add_hosts(self, *, finding: Finding, host_ids: Sequence[int]) -> Finding:
        self._attach_hosts(finding, host_ids)
        self.db.flush()
        return finding

    def remove_host(self, *, finding: Finding, host_id: int) -> Finding:
        """Detach EVERY endpoint row on ``host_id`` (named and unnamed).
        Callers that mean one named endpoint use ``remove_endpoint``."""
        self.db.query(FindingHost).filter(
            FindingHost.finding_id == finding.id, FindingHost.host_id == host_id,
        ).delete(synchronize_session=False)
        return finding

    def remove_endpoint(self, *, finding: Finding, finding_host_id: int) -> Optional[dict]:
        """v2.325.0 — detach exactly one affected-endpoint row.  Returns
        ``{host_id, name_id, host_status}`` (what an Undo must restore) or
        None when the row isn't on this finding."""
        row = (
            self.db.query(FindingHost)
            .filter(FindingHost.id == finding_host_id, FindingHost.finding_id == finding.id)
            .first()
        )
        if row is None:
            return None
        snapshot = {"host_id": row.host_id, "name_id": row.name_id, "host_status": row.host_status}
        self.db.delete(row)
        self.db.flush()
        return snapshot

    def restore_endpoint(
        self, *, finding: Finding, host_id: int, name_id: Optional[int] = None,
        host_status: Optional[str] = None,
    ) -> Finding:
        """v2.325.0 — (re)attach one endpoint with its name and status intact.
        ``name_id`` must belong to the finding's project; a name from another
        project is rejected like a foreign host."""
        if name_id is not None:
            from app.db.models import DNSName
            ok = (
                self.db.query(DNSName.id)
                .filter(DNSName.id == name_id, DNSName.project_id == finding.project_id)
                .first()
            )
            if ok is None:
                raise HTTPException(status_code=422, detail=f"Name {name_id} is not in this project.")
        self._attach_hosts(finding, [host_id], names_by_host={host_id: name_id} if name_id is not None else None)
        self.db.flush()  # autoflush is off — the row must exist before we look it up
        if host_status:
            row = (
                self.db.query(FindingHost)
                .filter(
                    FindingHost.finding_id == finding.id, FindingHost.host_id == host_id,
                    FindingHost.name_id.is_(None) if name_id is None else FindingHost.name_id == name_id,
                )
                .first()
            )
            if row is not None:
                row.host_status = host_status
        self.db.flush()
        return finding

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def delete_finding(self, *, finding: Finding) -> List[int]:
        """Delete a finding and everything that is only ITS (v2.375.0): the
        endpoint rows, scanner-evidence links, status history (ORM cascades)
        and its own comment thread (``annotations.finding_id`` cascades in the
        DB).  What it references survives: the source host note becomes
        promotable again and the scanner rows go back to being untriaged
        observations.  Does not commit.  Returns the comment ids so the caller
        can purge their attachment files once the delete has committed."""
        note_ids = [
            nid for (nid,) in self.db.query(Annotation.id).filter(Annotation.finding_id == finding.id)
        ]
        if note_ids:
            # Explicit, rather than trusting the DB cascade alone: the session
            # may hold these rows, and the test schema (SQLite) does not
            # enforce ON DELETE.
            self.db.query(Annotation).filter(Annotation.id.in_(note_ids)).delete(
                synchronize_session=False,
            )
        self.db.delete(finding)
        self.db.flush()
        return note_ids

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
                selectinload(Finding.owner), selectinload(Finding.created_by),
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
            q = q.filter(Finding.title.ilike(f"%{escape_like(search.strip())}%", escape="\\"))
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
            q = q.filter(Finding.title.ilike(f"%{escape_like(search.strip())}%", escape="\\"))
        return {sev: int(c) for sev, c in q.group_by(Finding.severity).all()}

    # ------------------------------------------------------------------
    # Comment / evidence thread (notes targeting the finding itself)
    # ------------------------------------------------------------------
    # A Finding hosts its own annotation thread so an analyst can refine it with
    # evidence (screenshots ride along via NoteAttachment) before it lands in a
    # report.  Same Annotation machinery as host notes, just a different target
    # column — finding_id instead of host_id.
    def list_finding_notes(self, finding_id: int, limit: int = 100) -> List[Annotation]:
        from app.services.host_serialization import note_load_options
        return (
            self.db.query(Annotation)
            .filter(Annotation.finding_id == finding_id)
            .options(*note_load_options())  # all that _serialize_note reads
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
        # ``project_id`` is a sibling TARGET, not a scope column — setting it
        # here would violate ck_annotations_exactly_one_target. See create_note
        # in host_follow_service.
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

    def get_finding_note(self, *, finding_id: int, note_id: int) -> Optional[Annotation]:
        """A comment, only if it belongs to THIS finding (the path's scope)."""
        return (
            self.db.query(Annotation)
            .filter(Annotation.id == note_id, Annotation.finding_id == finding_id)
            .first()
        )
