"""FindingService — the canonical create / promote / triage logic for the
Finding spine (foundation phase 5).

Promotion is the bridge from frictionless capture (an annotation thread) to
a durable, roll-up-able record.  The annotation thread stays as the
finding's evidence/discussion; the Finding carries severity + disposition +
owner + the cross-host M2M.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy import func, case, select, asc, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.models import Annotation, Host
from app.db.models_auth import User
from app.db.models_findings import (
    ACTIVE_FINDING_STATUSES, Finding, FindingHost, FindingStatusHistory, FindingStatus, FindingSeverity,
    FindingSource, FindingHostStatus,
)
from app.db.models_vulnerability import severity_rank
from app.services.host_query_common import escape_like
from app.services.report_text import seed_report_text_from_vuln
from app.services.status_history_service import record_status_transition
from app.services.vuln_identity import issue_key_for


_VALID_SEVERITIES = {s.value for s in FindingSeverity}
_VALID_STATUSES = {s.value for s in FindingStatus}
# Final dispositions — the "resolved" status group.  The pages ask for a reason
# when setting one (kept in the status-history summary); it is optional.
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

#: What a promotion does to the status of a finding the issue already has
#: (``FindingService.promote_vulnerability``).
_ON_JOIN = ("set", "confirm", "keep")

#: Endpoints a findings LIST row carries (review 2026-10-01 C2).  The row's
#: ``host_count`` is the true total; the finding's own page has them all.
ENDPOINT_PREVIEW = 5
_STATUS_GROUPS = {"active": _ACTIVE_STATUSES, "resolved": _TERMINAL_STATUSES}


def endpoint_segments(db: Session, project_id: int, host_ids: Sequence[int]) -> Dict[int, Dict[str, object]]:
    """The network segment of each of a finding's hosts, for the finding page's
    groups: ``host_id -> {key, label, kind, order}``.

    The segments are the project's ONE grouping — ``subnet_insight_service.
    group_hosts_into_segments`` over every scoped host, as the Posture grid and
    the Evidence matrix use it (sites; most-specific subnets when the project
    defines no site) — plus Evidence's ``unmapped`` for a host outside every
    scoped subnet.  The rule is decided over the PROJECT's hosts, never over
    this finding's alone: a finding whose hosts carry no site would otherwise
    be grouped by subnet in a project the other pages group by site.

    ``order`` is the segment's position in that grouping (the grid's column
    order, ``unmapped`` last).  ``kind`` is ``site`` | ``subnet`` |
    ``unassigned`` | ``unmapped``.  Two statements whatever the number of
    hosts (one when the project has no subnet); none for no host.
    """
    wanted = set(host_ids)
    if not wanted:
        return {}
    from app.services.evidence_service import UNMAPPED_SEGMENT, UNMAPPED_SEGMENT_LABEL
    from app.services.subnet_insight_service import (
        UNASSIGNED_SEGMENT, group_hosts_into_segments, resolve_host_locations,
    )

    grouping = group_hosts_into_segments(resolve_host_locations(db, project_id))
    out: Dict[int, Dict[str, object]] = {}
    for order, key in enumerate(grouping["keys"]):
        if key == UNASSIGNED_SEGMENT:
            kind = "unassigned"
        else:
            kind = "subnet" if key.startswith("subnet:") else "site"
        segment = {"key": key, "label": grouping["labels"].get(key) or key, "kind": kind, "order": order}
        for host_id in grouping["hosts"][key] & wanted:
            out[host_id] = segment
    unmapped = {
        "key": UNMAPPED_SEGMENT, "label": UNMAPPED_SEGMENT_LABEL, "kind": "unmapped",
        "order": len(grouping["keys"]),
    }
    for host_id in wanted - set(out):
        out[host_id] = unmapped
    return out


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
# The owner's display name, as the list shows it (full name, else username);
# NULL for an unowned finding (v2.408.0, UX review: Owner was the one column
# that could not be sorted).
_OWNER_SORT = (
    select(func.lower(func.coalesce(func.nullif(User.full_name, ""), User.username)))
    .where(User.id == Finding.owner_id)
    .correlate(Finding).scalar_subquery()
)
_SORT_COLUMNS = {
    "severity": _SEVERITY_SORT,
    "status": _STATUS_SORT,
    "title": Finding.title,
    "host_count": _HOST_COUNT_SORT,
    "source": Finding.source,
    "created_at": Finding.created_at,
    "owner": _OWNER_SORT,
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
    ordered = direction(col)
    # Unowned findings sort after every owner in both directions: reversing
    # the names should not bring the unassigned pile to the top.
    if sort == "owner":
        ordered = ordered.nulls_last()
    return (ordered, Finding.id.desc())


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
        host_ids: Optional[Sequence[int]] = None,
        on_join: str = "set",
    ) -> Tuple[Finding, bool]:
        """Promote a scanner vulnerability into a Finding (references, never
        copies — Finding.vuln_id).  Severity defaults to the vuln's own
        severity (``unknown`` → ``info``, since findings have no unknown).
        Idempotent on (vuln_id, source='scanner') so a double-click / a
        promote-then-dismiss can't fork two findings for one vuln — pass a
        terminal ``status`` (false_positive / accepted_risk) to dismiss.

        Returns ``(finding, created)``: ``created`` is False when the call
        joined the issue's existing finding — found by the lookup, or the
        winner of a concurrent insert.

        ``host_ids``: attach exactly these hosts instead of every host
        carrying the issue.  A host-scoped promotion passes the row's own
        host (v2.366.0), the bulk promotion from the Scanner observations
        list the hosts the operator ticked (v2.386.0; the caller checks they
        carry the issue).  The finding is still the ISSUE's — one per
        ``dedup_key`` — so promoting the same issue later from another host
        joins this finding rather than forking one.  What it changes is the
        claim: "confirmed" is recorded for the hosts that were looked at, not
        for hosts nobody has verified.  The others stay untriaged scanner
        observations that say "Finding #N covers other hosts only".

        ``on_join`` — what happens to the status of a finding the issue
        already has:

        * ``"set"`` — it takes ``status``.  The promote / dismiss click and an
          accepted proposal: there the person chose the status.
        * ``"confirm"`` (review 2026-10-01 R9) — the only change made is open
          / retest → confirmed.  A concluded finding (accepted risk,
          remediated, false positive) is joined as it stands and a confirmed
          one is never taken back to open.  For callers that record a result
          on ONE host — a test's evidence — and are not re-judging the issue.
        * ``"keep"`` — joined exactly as it stands.  The bulk promotion,
          which says an issue's finding is never re-statused by it; the
          caller cannot make that true by passing the status it read earlier,
          because the finding may have been created since.

        Unless the call is a false-positive dismissal, the results of tests
        that confirmed this issue on the attached hosts are linked to the
        finding (``link_issue_evidence``), so it shows what demonstrated it
        whichever way it was promoted.  Those evidence rows are locked BEFORE
        the finding is looked up or inserted — the one lock order of every
        path that makes an issue's finding (``create_finding_from_evidence``
        locks the evidence first too); the opposite order deadlocked.
        """
        if on_join not in _ON_JOIN:
            raise ValueError(f"on_join must be one of {_ON_JOIN}")
        sev = self._scanner_severity(vuln, severity)
        _validate_status(status)

        # Identity of the ISSUE, not of the scanner row — see vuln_identity.
        # This is what makes the Nessus row and the GreenBone row for one
        # problem converge on a single finding.
        key = issue_key_for(vuln)
        hosts = list(host_ids) if host_ids is not None else self._issue_host_ids(vuln, project_id, key)

        links_evidence = status != FindingStatus.FALSE_POSITIVE.value
        if links_evidence:
            from app.services.agent_evidence_service import link_issue_evidence, lock_issue_evidence

            lock_issue_evidence(self.db, host_ids=hosts, issue_key=key)

        # A different scanner may already have promoted this same issue.
        # Joining that finding as corroborating evidence instead of forking a
        # second record is what keeps one entry in the client report for one
        # problem.
        finding, created = self._get_or_create_scanner_finding(
            vuln, project_id, key, severity=sev, status=status,
            owner_id=owner_id, actor_id=actor_id,
        )
        # Record this scanner's row as evidence even when the finding already
        # existed (corroboration is the thing worth keeping); a second
        # scanner may also see the issue on hosts the first one missed.
        self.attach_vulnerability(finding=finding, vuln=vuln)
        self._attach_hosts(finding, hosts, names_by_host=self._vuln_names_by_host(vuln))
        if created:
            record_status_transition(
                self.db, history_model=FindingStatusHistory, fk_field="finding_id",
                entity_id=finding.id, from_status=None, to_status=status,
                changed_by_id=actor_id,
                summary=summary or "Promoted from scanner vulnerability",
            )
        else:
            may_move = on_join == "set" or (
                on_join == "confirm"
                and status == FindingStatus.CONFIRMED.value
                and finding.status in (FindingStatus.OPEN.value, FindingStatus.RETEST.value)
            )
            if status != finding.status and may_move:
                self.set_status(finding=finding, status=status, actor_id=actor_id,
                                summary=summary or "Re-dispositioned scanner finding")
        self.db.flush()
        if links_evidence:
            link_issue_evidence(self.db, host_ids=hosts, issue_key=key, finding_id=finding.id)
        return finding, created

    @staticmethod
    def _scanner_severity(vuln, severity: Optional[str]) -> str:
        """The severity a scanner finding is made with: the caller's, else
        the row's own (``unknown`` → ``info``: findings have no unknown)."""
        raw = severity or getattr(vuln.severity, "value", vuln.severity) or "medium"
        sev = "info" if str(raw).lower() == "unknown" else str(raw).lower()
        return validate_severity(sev)

    def _get_or_create_scanner_finding(
        self, vuln, project_id: int, key: Optional[str], *, severity: str, status: str,
        owner_id: Optional[int], actor_id: Optional[int],
    ) -> Tuple[Finding, bool]:
        """The issue's scanner finding and whether THIS call made it.  A new
        one is created with ``status``; an existing one — or the winner of a
        concurrent insert — is returned as it stands."""
        existing = self._scanner_finding_for(vuln, project_id, key)
        if existing is not None:
            return existing, False
        finding = Finding(
            project_id=project_id,
            title=(vuln.title or "Vulnerability")[:500],
            severity=severity,
            status=status,
            source=FindingSource.SCANNER.value,
            owner_id=owner_id or actor_id,
            vuln_id=vuln.id,
            dedup_key=key,
            created_by_id=actor_id,
        )
        seed_report_text_from_vuln(finding, vuln)
        winner = self._insert_scanner_finding(finding, vuln, project_id, key)
        if winner is not None:
            return winner, False
        return finding, True

    def reopen_false_positive_endpoints(
        self, *, finding: Finding, host_id: int, actor_id: Optional[int], note: Optional[str] = None,
    ) -> int:
        """A promotion made ON this host says the issue is real here: endpoint
        rows of the finding on this host that were dismissed as a false
        positive go back to ``open``, each through the endpoint-status step
        (its history line).  Other hosts' rows are never touched.  Returns how
        many moved."""
        rows = (
            self.db.query(FindingHost)
            .options(selectinload(FindingHost.host), selectinload(FindingHost.name))
            .filter(
                FindingHost.finding_id == finding.id, FindingHost.host_id == host_id,
                FindingHost.host_status == FindingHostStatus.FALSE_POSITIVE.value,
            )
            .order_by(FindingHost.id)
            .all()
        )
        moved = sum(
            1 for row in rows
            if self._move_endpoint(finding, row, FindingHostStatus.OPEN.value, actor_id, note)
        )
        if moved:
            self.db.flush()
        return moved

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
        sev = self._scanner_severity(vuln, severity)
        key = issue_key_for(vuln)

        # A finding someone promoted or dismissed first — the lookup's, or the
        # winner of a concurrent insert (R8) — is the issue's, and its status
        # stays theirs.
        finding, created = self._get_or_create_scanner_finding(
            vuln, project_id, key, severity=sev, status=FindingStatus.FALSE_POSITIVE.value,
            owner_id=owner_id, actor_id=actor_id,
        )
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

    def _scanner_finding_for(self, vuln, project_id: int, key: Optional[str]) -> Optional[Finding]:
        """The scanner finding that already covers this row: the one promoted
        from it, else the ISSUE's (one per ``dedup_key`` in a project — the
        partial unique index ``uq_finding_scanner_issue``).

        A row with no issue identity (a ``row:`` key) is outside that index —
        nothing in the database refuses a second finding for it — so its
        lookup-then-insert is serialised on the scanner row itself: the second
        promotion waits here until the first commits, and its lookup (a new
        statement, so it sees the commit) then finds the first one's finding.
        The lock lasts to the end of the transaction."""
        if not key or key.startswith("row:"):
            from app.db.models_vulnerability import Vulnerability as _Vuln

            self.db.query(_Vuln.id).filter(_Vuln.id == vuln.id).with_for_update().first()
        existing = (
            self.db.query(Finding)
            .filter(Finding.vuln_id == vuln.id, Finding.source == FindingSource.SCANNER.value)
            .order_by(Finding.id)
            .first()
        )
        if existing is None and key and not key.startswith("row:"):
            existing = (
                self.db.query(Finding)
                .filter(
                    Finding.project_id == project_id,
                    Finding.source == FindingSource.SCANNER.value,
                    Finding.dedup_key == key,
                )
                .order_by(Finding.id)
                .first()
            )
        return existing

    def _insert_scanner_finding(
        self, finding: Finding, vuln, project_id: int, key: Optional[str],
    ) -> Optional[Finding]:
        """Insert a new scanner finding; returns None when it went in, or the
        finding that got there first (review 2026-10-01 R8).

        The lookup-then-insert above is not atomic: two promotions of one issue
        both found nothing and inserted two findings.  The unique index now
        refuses the second insert; it runs in a savepoint so the refusal costs
        only the insert, and the caller joins the winner exactly as if its
        lookup had found it (the pattern ``host_test_service.create_tests``
        uses for request keys)."""
        try:
            with self.db.begin_nested():
                self.db.add(finding)
                self.db.flush()
        except IntegrityError:
            winner = self._scanner_finding_for(vuln, project_id, key)
            if winner is None:
                raise
            return winner
        return None

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
            # Plain comparisons so the `cve_id` index is usable — wrapping the
            # column in upper() made this a scan of the project's rows.  The
            # key is upper-case; parsers store a CVE as the tool wrote it,
            # which is upper or (nuclei, nikto) lower.
            siblings = (
                self.db.query(_Vuln.host_id)
                .join(Host, _Vuln.host_id == Host.id)
                .filter(Host.project_id == project_id,
                        _Vuln.cve_id.in_(sorted({cve, cve.upper(), cve.lower()})))
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
        summary: Optional[str] = None,
    ) -> Finding:
        """``summary`` goes on the creation history row (v2.439.1: an
        accepted agent proposal names itself there)."""
        validate_severity(severity)
        _validate_status(status)
        finding = Finding(
            project_id=project_id, title=title[:500], severity=severity,
            status=status, source=source, owner_id=owner_id,
            vuln_id=vuln_id, created_by_id=actor_id,
        )
        self.db.add(finding)
        self.db.flush()
        # (Until v2.442.0 a finding raised from a plan's execution result
        # inherited the entry's named endpoint here; evidence records link to
        # their finding through ``EvidenceRecord.finding_id`` instead.)
        if host_ids:
            self._attach_hosts(finding, host_ids)
        record_status_transition(
            self.db, history_model=FindingStatusHistory, fk_field="finding_id",
            entity_id=finding.id, from_status=None, to_status=status,
            changed_by_id=actor_id, summary=summary,
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
            # A reason for a terminal determination is ASKED FOR, never required
            # (owner, 2026-10-02): the pages always prompt, and an empty answer is
            # accepted.  When given it lives in the status-history summary
            # (surfaced by the history endpoint and the report).
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
        actor_id: Optional[int], note: Optional[str] = None,
    ) -> Finding:
        """v2.349.0 — the per-endpoint disposition (design review item 7).

        A finding's status is the ISSUE's; each affected endpoint keeps its
        own state (open / remediated / retest) so confirmation or remediation
        on one host never implies it on the others.  The change is written to
        the finding's history with the endpoint named, so the trail shows
        which host moved.
        """
        self._validate_endpoint_status(host_status)
        row = (
            self.db.query(FindingHost)
            .filter(FindingHost.finding_id == finding.id, FindingHost.id == finding_host_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Endpoint is not attached to this finding")
        if self._move_endpoint(finding, row, host_status, actor_id, note):
            self.db.flush()
        return finding

    def set_endpoint_statuses(
        self, *, finding: Finding, finding_host_ids: Sequence[int], host_status: str,
        actor_id: Optional[int], note: Optional[str] = None,
    ) -> int:
        """The same change for several endpoints of ONE finding (review
        2026-10-01 B13: after a retest, 300 endpoints were 300 requests).

        All-or-nothing: every id must be an endpoint of this finding, or
        nothing is written (404 naming the strangers).  Each endpoint that
        actually moves goes through :meth:`_move_endpoint` — the single
        route's own step — so it gets the same history line.  Rows are loaded
        once with their host and name; nothing is read per endpoint.  Returns
        how many moved; the caller commits."""
        self._validate_endpoint_status(host_status)
        wanted = list(dict.fromkeys(finding_host_ids))
        rows = (
            self.db.query(FindingHost)
            .options(selectinload(FindingHost.host), selectinload(FindingHost.name))
            .filter(FindingHost.finding_id == finding.id, FindingHost.id.in_(wanted))
            .order_by(FindingHost.id)
            .all()
        )
        missing = sorted(set(wanted) - {r.id for r in rows})
        if missing:
            shown = ", ".join(str(i) for i in missing[:20]) + (" …" if len(missing) > 20 else "")
            raise HTTPException(
                status_code=404,
                detail=f"Not endpoints of this finding: {shown}. Nothing was changed.",
            )
        moved = sum(1 for row in rows if self._move_endpoint(finding, row, host_status, actor_id, note))
        self.db.flush()
        return moved

    @staticmethod
    def _validate_endpoint_status(host_status: str) -> None:
        allowed = {s.value for s in FindingHostStatus}
        if host_status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"host_status must be one of {sorted(allowed)}",
            )

    def _move_endpoint(
        self, finding: Finding, row: FindingHost, host_status: str,
        actor_id: Optional[int], note: Optional[str],
    ) -> bool:
        """Set one endpoint row's state and write its history line.  False
        when it already had that state (nothing written).  Does not flush."""
        if row.host_status == host_status:
            return False
        old = row.host_status
        row.host_status = host_status
        label = row.host.ip_address if row.host else f"host {row.host_id}"
        if row.name is not None:
            label = f"{row.name.fqdn} on {label}"
        # Written directly: the shared transition helper is for the finding's
        # own status and skips a no-change move, which this is by design.
        self.db.add(FindingStatusHistory(
            finding_id=finding.id, from_status=finding.status, to_status=finding.status,
            changed_by_id=actor_id,
            # ``note``: where the change came from (an accepted proposal).
            summary=f"Endpoint {label}: {old} → {host_status}" + (f" — {note}" if note else ""),
        ))
        return True

    def add_hosts(
        self, *, finding: Finding, host_ids: Sequence[int], actor_id: Optional[int] = None,
    ) -> List[int]:
        """An analyst records that the issue affects these hosts too — the
        explicit "someone verified it here" path, distinct from promotion.

        A host already on the finding in ANY form (host-level or a named
        endpoint) is skipped, not given a second, unnamed row.  Every id must
        be a host of the finding's project or nothing is written
        (``_attach_hosts`` raises 422).  New rows start ``open``; one history
        entry names what was added.  Returns the host ids actually added."""
        attached = {
            hid for (hid,) in self.db.query(FindingHost.host_id)
            .filter(FindingHost.finding_id == finding.id).distinct()
        }
        wanted = [hid for hid in dict.fromkeys(host_ids) if hid is not None and hid not in attached]
        if not wanted:
            return []
        self._attach_hosts(finding, wanted)
        self.db.flush()
        labels = [
            ip for (ip,) in self.db.query(Host.ip_address)
            .filter(Host.id.in_(wanted)).order_by(Host.ip_address)
        ]
        shown = ", ".join(labels[:10]) + (f" and {len(labels) - 10} more" if len(labels) > 10 else "")
        self.db.add(FindingStatusHistory(
            finding_id=finding.id, from_status=finding.status, to_status=finding.status,
            changed_by_id=actor_id,
            summary=f"Added {len(wanted)} affected host{'s' if len(wanted) != 1 else ''}: {shown}",
        ))
        self.db.flush()
        return wanted

    def _before_endpoints_removed(
        self, finding: Finding, *, actor_id: Optional[int], is_project_admin: bool, **which,
    ) -> None:
        """Deleting an endpoint row deletes what hangs on it, and restoring
        the endpoint makes a NEW row.  On an installation that tracks
        remediation, a filled-in remediation record is therefore the project
        admins' to give up (``remediation_service.before_endpoints_removed``:
        409 for anyone else, a timeline entry for an admin).  With tracking
        off this does and says nothing."""
        from app.services import remediation_service

        remediation_service.before_endpoints_removed(
            self.db, finding, user_id=actor_id, is_project_admin=is_project_admin, **which,
        )

    def remove_host(
        self, *, finding: Finding, host_id: int,
        actor_id: Optional[int] = None, is_project_admin: bool = False,
    ) -> Finding:
        """Detach EVERY endpoint row on ``host_id`` (named and unnamed).
        Callers that mean one named endpoint use ``remove_endpoint``."""
        self._before_endpoints_removed(
            finding, actor_id=actor_id, is_project_admin=is_project_admin, host_id=host_id,
        )
        self.db.query(FindingHost).filter(
            FindingHost.finding_id == finding.id, FindingHost.host_id == host_id,
        ).delete(synchronize_session=False)
        return finding

    def remove_endpoint(
        self, *, finding: Finding, finding_host_id: int,
        actor_id: Optional[int] = None, is_project_admin: bool = False,
    ) -> Optional[dict]:
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
        self._before_endpoints_removed(
            finding, actor_id=actor_id, is_project_admin=is_project_admin, finding_host_id=finding_host_id,
        )
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
    def delete_finding(
        self, *, finding: Finding, actor_id: Optional[int] = None, is_project_admin: bool = False,
    ) -> List[int]:
        """Delete a finding and everything that is only ITS (v2.375.0): the
        endpoint rows, scanner-evidence links, status history (ORM cascades)
        and its own comment thread (``annotations.finding_id`` cascades in the
        DB).  What it references survives: the source host note becomes
        promotable again and the scanner rows go back to being untriaged
        observations.  Does not commit.  Returns the comment ids so the caller
        can purge their attachment files once the delete has committed."""
        self._before_endpoints_removed(
            finding, actor_id=actor_id, is_project_admin=is_project_admin, deleting_finding=True,
        )
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

    @staticmethod
    def _selection(
        q, *, project_id: int,
        status: Optional[str] = None, severity: Optional[str] = None,
        owner_id: Optional[int] = None, unowned: bool = False,
        source: Optional[str] = None, host_id: Optional[int] = None,
        search: Optional[str] = None,
    ):
        """The Findings list's filters, in ONE place (v2.474.0).

        The list, its severity roll-up and its owner counts are the same
        selection with one filter left out each (the roll-up ignores
        ``severity``, the owner counts ignore the owner) — so each calls this
        and simply does not pass the filter it ignores.  It was written out
        twice; a third copy for the owner counts would have been a third
        definition of what a filter means."""
        q = q.filter(Finding.project_id == project_id)
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
        return q

    def list_findings(
        self, *, project_id: int,
        status: Optional[str] = None, severity: Optional[str] = None,
        owner_id: Optional[int] = None, source: Optional[str] = None,
        host_id: Optional[int] = None, unowned: bool = False,
        search: Optional[str] = None,
        limit: int = 100, offset: int = 0,
        sort: Optional[str] = None, sort_dir: Optional[str] = None,
    ):
        """One page of findings and the filtered total.

        Endpoints are NOT loaded (review 2026-10-01 C2): a widespread issue has
        thousands, and a list row shows a count and a handful.  A caller that
        lists rows takes :meth:`endpoint_summaries` (the page) or
        :meth:`address_summaries` (the agents' list) for the page's ids."""
        options = [selectinload(Finding.owner), selectinload(Finding.created_by)]
        q = self._selection(
            self.db.query(Finding).options(*options), project_id=project_id,
            status=status, severity=severity, owner_id=owner_id, unowned=unowned,
            source=source, host_id=host_id, search=search,
        )
        total = q.count()
        q = q.order_by(*_finding_order(sort, sort_dir))
        rows = q.offset(offset).limit(limit).all()
        return rows, total

    def endpoint_summaries(
        self, finding_ids: Sequence[int], *, preview: int = ENDPOINT_PREVIEW,
        first_host_id: Optional[int] = None,
    ) -> Dict[int, dict]:
        """What a list row says about a finding's endpoints, for a page of
        findings in TWO statements whatever the page holds (review 2026-10-01
        C2): ``{finding_id: {"host_count", "status_counts", "preview"}}``.

        ``host_count`` is every endpoint row (what the ``host_count`` sort
        orders by); ``status_counts`` the per-endpoint states over all of
        them; ``preview`` the first ``preview`` endpoints by id, as plain
        tuples with the host's address and the endpoint's name already joined
        — no entity is built and nothing is loaded per row.

        ``first_host_id`` (the list's ``host_id`` filter) ranks that host's
        endpoint rows ahead of the rest, so a host's findings card reads this
        host's state from the preview instead of fetching every finding; the
        remaining places are filled first-by-id as before.  Same two
        statements — only the window's ordering changes."""
        ids = list(finding_ids)
        out: Dict[int, dict] = {
            fid: {"host_count": 0, "status_counts": {}, "preview": []} for fid in ids
        }
        if not ids:
            return out
        for fid, state, n in (
            self.db.query(FindingHost.finding_id, FindingHost.host_status, func.count(FindingHost.id))
            .filter(FindingHost.finding_id.in_(ids))
            .group_by(FindingHost.finding_id, FindingHost.host_status)
        ):
            out[fid]["status_counts"][state] = int(n)
            out[fid]["host_count"] += int(n)
        if preview <= 0:
            return out
        from app.db.models import DNSName

        rank_order = [FindingHost.id]
        if first_host_id is not None:
            rank_order.insert(0, case((FindingHost.host_id == first_host_id, 0), else_=1))
        ranked = (
            select(
                FindingHost.id.label("id"), FindingHost.finding_id.label("finding_id"),
                FindingHost.host_id.label("host_id"), FindingHost.name_id.label("name_id"),
                FindingHost.host_status.label("host_status"),
                func.row_number().over(
                    partition_by=FindingHost.finding_id, order_by=rank_order,
                ).label("rn"),
            )
            .where(FindingHost.finding_id.in_(ids))
            .subquery()
        )
        for row in (
            self.db.query(
                ranked.c.id, ranked.c.finding_id, ranked.c.host_id, ranked.c.name_id,
                ranked.c.host_status, Host.ip_address, Host.hostname, DNSName.fqdn,
            )
            .outerjoin(Host, Host.id == ranked.c.host_id)
            .outerjoin(DNSName, DNSName.id == ranked.c.name_id)
            .filter(ranked.c.rn <= preview)
            .order_by(ranked.c.finding_id, ranked.c.rn)
        ):
            out[row.finding_id]["preview"].append(row)
        return out

    def address_summaries(self, finding_ids: Sequence[int], *, sample: int = 10) -> Dict[int, dict]:
        """How many hosts a finding is on, for a page of findings in two
        statements: ``{finding_id: {"host_count", "endpoint_count",
        "sample"}}`` — distinct addresses, endpoint rows on a host, and up to
        ``sample`` of the addresses (lowest first).  The agents' list reads
        this; it used to load every endpoint and its ``Host`` to count them
        (review 2026-10-07)."""
        ids = list(finding_ids)
        out: Dict[int, dict] = {
            fid: {"host_count": 0, "endpoint_count": 0, "sample": []} for fid in ids
        }
        if not ids:
            return out
        for fid, hosts, endpoints in (
            self.db.query(
                FindingHost.finding_id,
                func.count(func.distinct(Host.ip_address)),
                func.count(FindingHost.id),
            )
            .join(Host, Host.id == FindingHost.host_id)
            .filter(FindingHost.finding_id.in_(ids))
            .group_by(FindingHost.finding_id)
        ):
            out[fid]["host_count"] = int(hosts)
            out[fid]["endpoint_count"] = int(endpoints)
        if sample <= 0:
            return out
        addresses = (
            select(FindingHost.finding_id.label("finding_id"), Host.ip_address.label("ip"))
            .join(Host, Host.id == FindingHost.host_id)
            .where(FindingHost.finding_id.in_(ids))
            .distinct()
            .subquery()
        )
        ranked = select(
            addresses.c.finding_id, addresses.c.ip,
            func.row_number().over(
                partition_by=addresses.c.finding_id, order_by=addresses.c.ip,
            ).label("rn"),
        ).subquery()
        for fid, ip in self.db.execute(
            select(ranked.c.finding_id, ranked.c.ip).where(ranked.c.rn <= sample)
        ):
            out[fid]["sample"].append(ip)
        for row in out.values():
            row["sample"].sort()
        return out

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
        q = self._selection(
            self.db.query(Finding.severity, func.count(Finding.id)), project_id=project_id,
            status=status, owner_id=owner_id, unowned=unowned,
            source=source, host_id=host_id, search=search,
        )
        return {sev: int(c) for sev, c in q.group_by(Finding.severity).all()}

    def owner_counts(
        self, *, project_id: int,
        status: Optional[str] = None, severity: Optional[str] = None,
        source: Optional[str] = None, host_id: Optional[int] = None,
        search: Optional[str] = None,
    ) -> List[dict]:
        """Who owns the listed findings, and how many each (v2.474.0) — what
        the page's Owner filter offers.

        Respects every filter EXCEPT the owner (so everyone stays listed while
        one is chosen) and ignores pagination: each count is the size of the
        list its owner opens.  One grouped statement.  ``owner_id`` None is
        the unowned findings.  Named owners first, by name; unowned last."""
        q = self._selection(
            self.db.query(Finding.owner_id, User.full_name, User.username, func.count(Finding.id))
            .outerjoin(User, User.id == Finding.owner_id),
            project_id=project_id, status=status, severity=severity,
            source=source, host_id=host_id, search=search,
        )
        rows = [
            {"owner_id": owner_id, "owner_name": (full_name or username) if owner_id is not None else None,
             "count": int(n)}
            for owner_id, full_name, username, n in
            q.group_by(Finding.owner_id, User.full_name, User.username).all()
        ]
        rows.sort(key=lambda r: (r["owner_id"] is None, (r["owner_name"] or "").lower(), r["owner_id"] or 0))
        return rows

    # ------------------------------------------------------------------
    # Comment / evidence thread (notes targeting the finding itself)
    # ------------------------------------------------------------------
    # A Finding hosts its own annotation thread so an analyst can refine it with
    # evidence (screenshots ride along via NoteAttachment) before it lands in a
    # report.  Same Annotation machinery as host notes, just a different target
    # column — finding_id instead of host_id.
    def comment_activity(
        self, project_id: int, *, search: Optional[str] = None,
        author_id: Optional[int] = None, limit: int = 20,
    ) -> tuple[list[dict], int]:
        """The project's finding discussions, most recently active first
        (v2.408.0).  Collaboration listed host-note threads only, so a comment
        on a finding — and a mention in one — never appeared there.

        A discussion matches the filters when any of its comments does (the
        author filter: a comment by that person; search: the finding title or
        a comment body).  Its count, newest comment and participants are the
        WHOLE thread's.  Five statements, whatever the limit.
        """
        ts = func.coalesce(Annotation.updated_at, Annotation.created_at)
        matching = (
            self.db.query(Annotation.finding_id.label("fid"), func.max(ts).label("last_at"))
            .join(Finding, Finding.id == Annotation.finding_id)
            .filter(Finding.project_id == project_id)
        )
        if author_id is not None:
            matching = matching.filter(Annotation.user_id == author_id)
        if search and search.strip():
            like = f"%{escape_like(search.strip())}%"
            matching = matching.filter(
                Finding.title.ilike(like, escape="\\") | Annotation.body.ilike(like, escape="\\")
            )
        grouped = matching.group_by(Annotation.finding_id).subquery()
        total = self.db.query(func.count()).select_from(grouped).scalar() or 0
        top = (
            self.db.query(grouped.c.fid)
            .order_by(desc(grouped.c.last_at), desc(grouped.c.fid))
            .limit(limit)
            .all()
        )
        ids = [r.fid for r in top]
        if not ids:
            return [], total

        stats = {
            fid: (int(n), last_at)
            for fid, n, last_at in (
                self.db.query(Annotation.finding_id, func.count(Annotation.id), func.max(ts))
                .filter(Annotation.finding_id.in_(ids))
                .group_by(Annotation.finding_id)
                .all()
            )
        }
        latest = {
            n.finding_id: n
            for n in (
                self.db.query(Annotation)
                .options(selectinload(Annotation.author))
                .filter(Annotation.finding_id.in_(ids))
                .distinct(Annotation.finding_id)
                .order_by(Annotation.finding_id, desc(ts), desc(Annotation.id))
                .all()
            )
        }
        participants: dict[int, list[str]] = {}
        for fid, full_name, username in (
            self.db.query(Annotation.finding_id, User.full_name, User.username)
            .join(User, User.id == Annotation.user_id)
            .filter(Annotation.finding_id.in_(ids))
            .distinct()
            .order_by(Annotation.finding_id, User.username)
            .all()
        ):
            participants.setdefault(fid, []).append(full_name or username)
        findings = {f.id: f for f in self.db.query(Finding).filter(Finding.id.in_(ids)).all()}

        rows = []
        for fid in ids:
            f = findings[fid]
            note = latest.get(fid)
            count, last_at = stats.get(fid, (0, None))
            rows.append({
                "finding_id": fid,
                "title": f.title,
                "severity": f.severity,
                "status": f.status.value if hasattr(f.status, "value") else f.status,
                "comment_count": count,
                "last_activity_at": last_at,
                "latest": None if note is None else {
                    "note_id": note.id,
                    "body": note.body,
                    "author_name": (note.author.full_name or note.author.username) if note.author else None,
                    "actor_type": note.actor_type or "user",
                    "created_at": note.created_at,
                },
                "participants": participants.get(fid, []),
            })
        return rows, total

    def list_finding_notes(self, finding_id: int, limit: int = 100) -> List[Annotation]:
        from app.services.host_serialization import note_load_options
        return (
            self.db.query(Annotation)
            .filter(Annotation.finding_id == finding_id)
            .options(*note_load_options())  # all that _serialize_note reads
            # Oldest-first reads as a thread; the id breaks a tie (two comments in
            # one transaction share a timestamp, and their order was arbitrary).
            .order_by(Annotation.created_at.asc(), Annotation.id.asc())
            .limit(limit)
            .all()
        )

    def create_finding_note(
        self, *, finding_id: int, user_id: int, body: str,
        parent_id: Optional[int] = None,
    ) -> Annotation:
        # Threading stays within one finding (mirrors the same-host guard on
        # host notes): a reply's parent must be a note on THIS finding, else a
        # reply could notify another project's thread.
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
            parent_id=parent_id,
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
