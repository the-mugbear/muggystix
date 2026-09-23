"""Scanner observations grouped by ISSUE across a project's hosts (v2.386.0).

Scanner rows used to be reachable only one host at a time, in the inspector,
so an issue carried by forty hosts could only be seen — and promoted — by
opening one of them.  This lists each issue once with how many hosts carry it
and how many of those a finding already covers, and promotes several issues in
one call, each on every host that carries it or on the hosts the operator
ticked.

The two rules it leans on are defined elsewhere, and used as they are:

* what an issue IS — ``Vulnerability.issue_key`` (``vuln_identity``), the key
  a finding dedups on;
* whether a host's row is judged — ``engagement_metrics_service
  .observation_judged_on_host``, the SQL form of the inspector's
  ``finding_on_this_host``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from sqlalchemy import String, case, cast, distinct, func, literal, or_
from sqlalchemy.orm import Session

from app.db.models import Host, Port
from app.db.models_findings import Finding, FindingHost, FindingSource, FindingStatus
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.engagement_metrics_service import observation_judged_on_host
from app.services.finding_service import FindingService

# Most issues one promotion call may name, and most hosts one issue may list.
PROMOTE_ISSUE_CAP = 200

_RANK = {
    VulnerabilitySeverity.CRITICAL: 5,
    VulnerabilitySeverity.HIGH: 4,
    VulnerabilitySeverity.MEDIUM: 3,
    VulnerabilitySeverity.LOW: 2,
    VulnerabilitySeverity.INFO: 1,
}
_SEVERITY_OF_RANK = {rank: sev.value for sev, rank in _RANK.items()}
_SEVERITY_OF_RANK[0] = VulnerabilitySeverity.UNKNOWN.value


class ObservationError(ValueError):
    """A promotion request that names an issue or host it cannot apply to."""


def _rank():
    return case(*[(Vulnerability.severity == sev, rank) for sev, rank in _RANK.items()], else_=0)


def _key():
    """The issue key, with the per-row fallback ``issue_key`` itself uses for a
    row with no CVE and no title (such a row is an issue of its own)."""
    return func.coalesce(Vulnerability.issue_key, literal("row:") + cast(Vulnerability.id, String))


def _project_rows(db: Session, project_id: int, *columns):
    return (
        db.query(*columns)
        .select_from(Vulnerability)
        .join(Host, Host.id == Vulnerability.host_id)
        .filter(Host.project_id == project_id)
    )


def _findings_by_key(db: Session, project_id: int, keys: Sequence[str]) -> Dict[str, Finding]:
    """The scanner finding already covering each issue — by its dedup key,
    or, for a ``row:`` key, by the row it was promoted from."""
    if not keys:
        return {}
    row_ids = {int(k[4:]): k for k in keys if k.startswith("row:") and k[4:].isdigit()}
    issue_keys = [k for k in keys if not k.startswith("row:")]
    out: Dict[str, Finding] = {}
    conditions = []
    if issue_keys:
        conditions.append(Finding.dedup_key.in_(issue_keys))
    if row_ids:
        conditions.append(Finding.vuln_id.in_(list(row_ids)))
    if not conditions:
        return {}
    for f in (
        db.query(Finding)
        .filter(Finding.project_id == project_id, Finding.source == FindingSource.SCANNER.value, or_(*conditions))
        .order_by(Finding.id)
        .all()
    ):
        key = f.dedup_key if f.dedup_key in issue_keys else row_ids.get(f.vuln_id)
        if key and key not in out:
            out[key] = f
    return out


@dataclass
class IssueRow:
    issue_key: str
    title: str
    severity: str
    cve_id: Optional[str]
    sources: List[str]
    host_count: int
    judged_host_count: int
    finding_id: Optional[int] = None
    finding_status: Optional[str] = None


@dataclass
class IssuePage:
    items: List[IssueRow] = field(default_factory=list)
    total: int = 0


def list_issues(
    db: Session,
    project_id: int,
    *,
    search: Optional[str] = None,
    severity: Optional[str] = None,
    include_judged: bool = False,
    min_hosts: int = 1,
    skip: int = 0,
    limit: int = 50,
) -> IssuePage:
    """One row per issue, most severe first, then the most hosts left to judge.

    By default an issue every host of which a finding already covers is left
    out: this is the list of what still waits.  ``include_judged`` shows it.
    """
    key = _key()
    rank = func.max(_rank())
    hosts = func.count(distinct(Vulnerability.host_id))
    judged = func.count(distinct(case((observation_judged_on_host(), Vulnerability.host_id))))
    query = _project_rows(
        db, project_id,
        key.label("issue_key"), rank.label("rank"), hosts.label("hosts"), judged.label("judged"),
        func.min(Vulnerability.title).label("title"), func.max(Vulnerability.cve_id).label("cve_id"),
    )
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(Vulnerability.title.ilike(like), Vulnerability.cve_id.ilike(like)))
    query = query.group_by(key)
    if severity:
        wanted = next((r for r, s in _SEVERITY_OF_RANK.items() if s == severity.lower()), None)
        if wanted is None:
            raise ObservationError(f"Unknown severity '{severity}'")
        query = query.having(rank == wanted)
    if not include_judged:
        query = query.having(hosts > judged)
    if min_hosts > 1:
        query = query.having(hosts >= min_hosts)

    total = query.order_by(None).count()
    rows = (
        query.order_by(rank.desc(), (hosts - judged).desc(), hosts.desc(), func.min(Vulnerability.title))
        .offset(skip)
        .limit(limit)
        .all()
    )
    keys = [r.issue_key for r in rows]
    sources: Dict[str, List[str]] = {}
    if keys:
        for k, src in (
            _project_rows(db, project_id, key, Vulnerability.source)
            .filter(key.in_(keys))
            .distinct()
            .all()
        ):
            sources.setdefault(k, []).append(getattr(src, "value", src))
    findings = _findings_by_key(db, project_id, keys)
    items = []
    for r in rows:
        f = findings.get(r.issue_key)
        items.append(IssueRow(
            issue_key=r.issue_key,
            title=r.title,
            severity=_SEVERITY_OF_RANK.get(int(r.rank or 0), "unknown"),
            cve_id=r.cve_id,
            sources=sorted(sources.get(r.issue_key, [])),
            host_count=int(r.hosts),
            judged_host_count=int(r.judged),
            finding_id=f.id if f else None,
            finding_status=f.status if f else None,
        ))
    return IssuePage(items=items, total=int(total))


@dataclass
class IssueHost:
    host_id: int
    ip_address: str
    hostname: Optional[str]
    severity: str
    ports: List[int]
    judged: bool
    endpoint_status: Optional[str]


def issue_hosts(db: Session, project_id: int, issue_key: str) -> List[IssueHost]:
    """Every host carrying the issue, with whether a finding covers it there."""
    key = _key()
    judged = func.max(case((observation_judged_on_host(), 1), else_=0))
    rows = (
        _project_rows(
            db, project_id, Host.id, Host.ip_address, Host.hostname, func.max(_rank()).label("rank"),
            judged.label("judged"),
        )
        .filter(key == issue_key)
        .group_by(Host.id, Host.ip_address, Host.hostname)
        .order_by(Host.ip_address)
        .all()
    )
    if not rows:
        return []
    host_ids = [r.id for r in rows]
    ports: Dict[int, set] = {}
    for hid, number in (
        _project_rows(db, project_id, Vulnerability.host_id, Port.port_number)
        .join(Port, Port.id == Vulnerability.port_id)
        .filter(key == issue_key)
        .distinct()
        .all()
    ):
        ports.setdefault(hid, set()).add(number)
    endpoint: Dict[int, str] = {}
    finding = _findings_by_key(db, project_id, [issue_key]).get(issue_key)
    if finding is not None:
        endpoint = dict(
            db.query(FindingHost.host_id, FindingHost.host_status)
            .filter(FindingHost.finding_id == finding.id, FindingHost.host_id.in_(host_ids))
            .all()
        )
    return [
        IssueHost(
            host_id=r.id, ip_address=r.ip_address, hostname=r.hostname,
            severity=_SEVERITY_OF_RANK.get(int(r.rank or 0), "unknown"),
            ports=sorted(ports.get(r.id, ())), judged=bool(r.judged),
            endpoint_status=endpoint.get(r.id),
        )
        for r in rows
    ]


@dataclass
class PromoteItem:
    issue_key: str
    host_ids: Optional[List[int]] = None


@dataclass
class PromoteOutcome:
    issue_key: str
    finding_id: int
    created: bool
    host_count: int


def promote_issues(
    db: Session, project_id: int, actor_id: Optional[int], items: Sequence[PromoteItem],
) -> List[PromoteOutcome]:
    """Promote each issue to a finding on its hosts (all of them, or the ones
    named).  An issue that already has a finding JOINS it: the hosts are added
    and its status is left as it stands — a bulk action never re-opens a
    remediated finding or confirms over an accepted risk.

    Everything is checked before anything is written, so a request naming one
    unknown issue or one host that does not carry its issue changes nothing.
    The caller commits.
    """
    if not items:
        raise ObservationError("Name at least one issue to promote.")
    if len(items) > PROMOTE_ISSUE_CAP:
        raise ObservationError(f"At most {PROMOTE_ISSUE_CAP} issues per promotion.")
    keys = list(dict.fromkeys(i.issue_key for i in items))
    if len(keys) != len(items):
        raise ObservationError("An issue is named twice.")

    key = _key()
    rows_by_key: Dict[str, List[Vulnerability]] = {}
    for k, vuln in (
        _project_rows(db, project_id, key, Vulnerability).filter(key.in_(keys)).all()
    ):
        rows_by_key.setdefault(k, []).append(vuln)

    plan = []
    for item in items:
        rows = rows_by_key.get(item.issue_key)
        if not rows:
            raise ObservationError(f"No scanner observation in this project is the issue '{item.issue_key}'.")
        carrying = {v.host_id for v in rows}
        if item.host_ids is None:
            chosen = sorted(carrying)
        else:
            chosen = list(dict.fromkeys(item.host_ids))
            if not chosen:
                raise ObservationError("Choose at least one host for each issue.")
            outside = [h for h in chosen if h not in carrying]
            if outside:
                raise ObservationError(
                    f"Host(s) {', '.join(map(str, outside))} do not carry the issue '{rows[0].title}'."
                )
        wanted = set(chosen)
        # The evidencing row: the most severe on a chosen host, oldest first.
        rep = min((v for v in rows if v.host_id in wanted), key=lambda v: (-_RANK.get(v.severity, 0), v.id))
        plan.append((item.issue_key, rep, chosen))

    existing = _findings_by_key(db, project_id, keys)
    svc = FindingService(db)
    outcomes = []
    for issue_key, rep, chosen in plan:
        found = existing.get(issue_key)
        finding = svc.promote_vulnerability(
            vuln=rep, project_id=project_id, actor_id=actor_id,
            status=found.status if found is not None else FindingStatus.CONFIRMED.value,
            host_ids=chosen,
            summary="Promoted from the scanner observations list",
        )
        outcomes.append(PromoteOutcome(
            issue_key=issue_key, finding_id=finding.id, created=found is None, host_count=len(chosen),
        ))
    return outcomes
