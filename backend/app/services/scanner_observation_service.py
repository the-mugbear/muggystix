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
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import String, and_, case, cast, distinct, false, func, literal, or_
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import array_agg as pg_array_agg
from sqlalchemy.orm import Session, joinedload

from app.db.models import Host, Port
from app.db.models_findings import Finding, FindingHost, FindingSource, FindingStatus
from app.db.models_host_tests import ACTIVE_TEST_STATUSES, TESTED_OUTCOMES, HostTest
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.engagement_metrics_service import join_judged, observation_judged_on_host
from app.services.finding_service import FindingService
from app.services.host_query_common import escape_like
from app.services.host_serialization import parse_references, serialize_vulnerability
from app.services.misconfig_checks import KIND_INFORMATIONAL, KIND_MISCONFIGURATION, KIND_VULNERABILITY

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


def _row_id(issue_key: str) -> Optional[int]:
    """The row a ``row:N`` key names — only in the exact form ``_key`` writes
    (``row:7``, never ``row:07``), so the lookup below matches what the
    string comparison matched."""
    digits = issue_key[4:] if isinstance(issue_key, str) and issue_key.startswith("row:") else ""
    if digits.isascii() and digits.isdigit() and str(int(digits)) == digits:
        return int(digits)
    return None


def _key_in(keys: Sequence[str]):
    """``_key() IN keys`` without wrapping the column (review 2026-10-01 R20).

    ``coalesce(issue_key, 'row:' || id) = …`` cannot use an index on either
    column, so every expand-row, host list and promotion read all the
    project's vulnerability rows.  Split by the key's form instead: a stored
    key is looked up on ``issue_key`` (indexed by ``c3f6b9d2e5a7``), a
    ``row:N`` key on the primary key.  The ``issue_key IS NULL`` beside the id
    keeps the old meaning exactly — ``row:N`` names row N only while that row
    has no key of its own — and ``issue_key`` never stores a ``row:`` value
    (``vuln_identity.issue_key`` returns None without a ``row_id``).
    """
    row_ids = [rid for rid in (_row_id(k) for k in keys) if rid is not None]
    stored = [k for k in keys if _row_id(k) is None]
    arms = []
    if stored:
        arms.append(
            Vulnerability.issue_key == stored[0] if len(stored) == 1
            else Vulnerability.issue_key.in_(stored)
        )
    if row_ids:
        arms.append(and_(
            Vulnerability.issue_key.is_(None),
            Vulnerability.id == row_ids[0] if len(row_ids) == 1 else Vulnerability.id.in_(row_ids),
        ))
    if not arms:
        return false()
    return arms[0] if len(arms) == 1 else or_(*arms)


def _key_is(issue_key: str):
    """``_key() == issue_key``, index-friendly — see ``_key_in``."""
    return _key_in([issue_key])


def _of_most_severe_row(db: Session, column):
    """``column`` from the group's most severe row (tie → lowest id), as an
    aggregate in the same GROUP BY (v2.402.0).

    ``min(title)`` beside ``max(severity)`` paired the alphabetically first
    title with the worst severity of ANOTHER row: "Synthetic low issue" was
    shown as Medium because a medium row shared its CVE.  On Postgres this is
    ``(array_agg(col ORDER BY rank DESC, id))[1]``; the SQLite test fallback
    has no ordered aggregate and keeps ``min`` (its tests skip the pairing).
    """
    if db.get_bind().dialect.name == "postgresql":
        return pg_array_agg(aggregate_order_by(column, _rank().desc(), Vulnerability.id.asc()))[1]
    return func.min(column)


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
    # v2.415.0 — misconfiguration (a catalog check) / vulnerability /
    # informational (misconfig_checks.vuln_kind, at the issue's severity).
    kind: str = KIND_VULNERABILITY
    # A scanner says an exploit exists for it, on at least one host.  A lead
    # for what to test first — not a statement that it was exploited.
    exploitable: bool = False
    # Review 2026-10-16, O1 / O2 — what tells two rows apart and what a
    # triage needs without opening each: the scanners' own check ids, the
    # highest CVSS any of its rows gives, the ports it was seen on (the first
    # PORT_CAP by number; ``port_count`` is all of them) and when an import
    # last recorded it.
    plugin_ids: List[str] = field(default_factory=list)
    cvss_score: Optional[float] = None
    ports: List[int] = field(default_factory=list)
    port_count: int = 0
    last_seen: Optional[datetime] = None


@dataclass
class IssuePage:
    items: List[IssueRow] = field(default_factory=list)
    total: int = 0


# The ports and check ids a row names; the counts say how many there are.
PORT_CAP = 8
PLUGIN_ID_CAP = 5

# The list's orders, one declaration for both doors.
ISSUE_SORTS = ("severity", "hosts", "cvss")

# Which issues by how far they are judged (review 2026-10-16, O3), one
# declaration for both doors: every issue; those with a host not yet judged
# (the agents' default and the page's old switch); those with no host judged;
# those judged on every host.
JUDGED_STATES = ("any", "waiting", "none", "all")


def resolve_judged(judged: Optional[str], include_judged: bool) -> str:
    """The ``judged`` filter a request asks for.  ``include_judged=true`` is
    the older spelling of ``judged=any``, still read on both doors; asking
    both for different things is refused, never one of them dropped."""
    if judged is None:
        return "any" if include_judged else "waiting"
    if judged not in JUDGED_STATES:
        raise ObservationError(f"Unknown judged '{judged}'. One of: {', '.join(JUDGED_STATES)}")
    if include_judged and judged != "any":
        raise ObservationError("include_judged=true is judged=any; send one of them")
    return judged


def list_issues(
    db: Session,
    project_id: int,
    *,
    search: Optional[str] = None,
    severity: Optional[str] = None,
    judged: str = "waiting",
    min_hosts: int = 1,
    skip: int = 0,
    limit: int = 50,
    kind: Optional[str] = None,
    sort: str = "severity",
    exploitable: bool = False,
) -> IssuePage:
    """One row per issue, most severe first, then the most hosts left to judge.

    ``sort="hosts"`` (v2.428.5) puts the most widespread issues first — most
    hosts left to judge, then most hosts, then severity: "which issues are on
    the most hosts?" (MCP acceptance feedback #13 had to page 606 groups and
    sort them itself).  ``sort="cvss"`` puts the highest CVSS first (an
    issue with none last).

    ``judged`` (``JUDGED_STATES``) — by default ``waiting``: an issue every
    host of which a finding already covers is left out, the list of what
    still waits.  ``exploitable`` keeps the issues a scanner reports an
    exploit for on at least one host (each row says so either way).
    ``search`` matches the title, the CVE id or a scanner's check id.
    """
    if sort not in ISSUE_SORTS:
        raise ObservationError(f"Unknown sort '{sort}'")
    if judged not in JUDGED_STATES:
        raise ObservationError(f"Unknown judged '{judged}'")
    key = _key()
    rank = func.max(_rank())
    hosts = func.count(distinct(Vulnerability.host_id))
    # The judged rule as joins: the per-row EXISTS ran once for the SELECT,
    # the HAVING and the ORDER BY each — 4.3 s over 400k rows at 80k hosts.
    base, judged_row = join_judged(_project_rows(db, project_id), [project_id])
    judged_hosts = func.count(distinct(case((judged_row, Vulnerability.host_id))))
    # Title and CVE come from the row the severity comes from — one aggregate
    # query still, no second pass over the groups.
    title = _of_most_severe_row(db, Vulnerability.title)
    cve = func.coalesce(_of_most_severe_row(db, Vulnerability.cve_id), func.max(Vulnerability.cve_id))
    has_exploit = func.bool_or(func.coalesce(Vulnerability.exploitable, false()))
    cvss = func.max(Vulnerability.cvss_score)
    last_seen = func.max(Vulnerability.last_seen)
    query = base.with_entities(
        key.label("issue_key"), rank.label("rank"), hosts.label("hosts"), judged_hosts.label("judged"),
        title.label("title"), cve.label("cve_id"), has_exploit.label("exploitable"),
        cvss.label("cvss_score"), last_seen.label("last_seen"),
    )
    if search and search.strip():
        # Escaped like every other search: "100%" and "a_b" are text.
        like = f"%{escape_like(search.strip())}%"
        query = query.filter(or_(
            Vulnerability.title.ilike(like, escape="\\"), Vulnerability.cve_id.ilike(like, escape="\\"),
            Vulnerability.plugin_id.ilike(like, escape="\\"),
        ))
    # v2.415.0 — kind: a catalog check's rows all carry it (its issue key is
    # the check); otherwise the issue's severity decides.
    info_rank = _RANK[VulnerabilitySeverity.INFO]
    if kind == KIND_MISCONFIGURATION:
        query = query.filter(Vulnerability.check_id.isnot(None))
    elif kind in (KIND_VULNERABILITY, KIND_INFORMATIONAL):
        query = query.filter(Vulnerability.check_id.is_(None))
    elif kind:
        raise ObservationError(f"Unknown kind '{kind}'")
    query = query.group_by(key)
    if kind == KIND_VULNERABILITY:
        query = query.having(rank > info_rank)
    elif kind == KIND_INFORMATIONAL:
        query = query.having(rank <= info_rank)
    if severity:
        wanted = next((r for r, s in _SEVERITY_OF_RANK.items() if s == severity.lower()), None)
        if wanted is None:
            raise ObservationError(f"Unknown severity '{severity}'")
        query = query.having(rank == wanted)
    if judged == "waiting":
        query = query.having(hosts > judged_hosts)
    elif judged == "none":
        query = query.having(judged_hosts == 0)
    elif judged == "all":
        query = query.having(hosts == judged_hosts)
    if min_hosts > 1:
        query = query.having(hosts >= min_hosts)
    if exploitable:
        query = query.having(has_exploit)

    # The total rides on the page as a window over the groups: counting in a
    # second query ran the whole aggregation twice.  A page past the end has
    # no rows to carry it, so only then is it counted on its own.
    if sort == "hosts":
        order = ((hosts - judged_hosts).desc(), hosts.desc(), rank.desc(), title, key)
    elif sort == "cvss":
        order = (cvss.desc().nullslast(), rank.desc(), (hosts - judged_hosts).desc(), hosts.desc(), title, key)
    else:
        order = (rank.desc(), (hosts - judged_hosts).desc(), hosts.desc(), title, key)
    rows = (
        query.add_columns(func.count().over().label("total_groups"))
        # The issue key is the LAST sort key (v2.474.1): the other keys are
        # counts and a title, which tie — and with ties the database may hand
        # the rows out in a different order for each page, so "Show more"
        # could repeat an issue or skip one although nothing had changed.
        # (Rows still move between pages when someone judges an issue; the
        # page de-duplicates for that.)
        .order_by(*order)
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = rows[0].total_groups if rows else (query.order_by(None).count() if skip else 0)
    keys = [r.issue_key for r in rows]
    sources: Dict[str, List[str]] = {}
    plugin_ids: Dict[str, List[str]] = {}
    ports: Dict[str, tuple] = {}
    if keys:
        for k, src, plugin_id in (
            _project_rows(db, project_id, key, Vulnerability.source, Vulnerability.plugin_id)
            .filter(_key_in(keys))
            .distinct()
            .all()
        ):
            value = getattr(src, "value", src)
            if value not in sources.setdefault(k, []):
                sources[k].append(value)
            if plugin_id and plugin_id not in plugin_ids.setdefault(k, []):
                plugin_ids[k].append(plugin_id)
        ports = _issue_ports(db, project_id, keys)
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
            kind=(KIND_MISCONFIGURATION if r.issue_key.startswith("check:")
                  else KIND_INFORMATIONAL if int(r.rank or 0) <= info_rank else KIND_VULNERABILITY),
            exploitable=bool(r.exploitable),
            plugin_ids=sorted(plugin_ids.get(r.issue_key, []))[:PLUGIN_ID_CAP],
            cvss_score=float(r.cvss_score) if r.cvss_score is not None else None,
            ports=list(ports.get(r.issue_key, ((), 0))[0]),
            port_count=int(ports.get(r.issue_key, ((), 0))[1]),
            last_seen=r.last_seen,
        ))
    return IssuePage(items=items, total=int(total))


def _issue_ports(db: Session, project_id: int, keys: Sequence[str]) -> Dict[str, tuple]:
    """issue key → (its first ``PORT_CAP`` port numbers, how many there are),
    in one grouped statement for the page — an informational issue can sit
    on every port of every host, so only the first few leave the database."""
    key = _key()
    number = Port.port_number
    if db.get_bind().dialect.name == "postgresql":
        first = pg_array_agg(aggregate_order_by(distinct(number), number.asc()))[1:PORT_CAP]
    else:  # the SQLite test fallback: every number, cut below
        first = func.group_concat(distinct(number))
    rows = (
        _project_rows(db, project_id, key, first, func.count(distinct(number)))
        .join(Port, Port.id == Vulnerability.port_id)
        .filter(_key_in(keys))
        .group_by(key)
        .all()
    )
    out: Dict[str, tuple] = {}
    for k, numbers, count in rows:
        if isinstance(numbers, str):
            numbers = sorted(int(n) for n in numbers.split(","))
        out[k] = (sorted(numbers or [])[:PORT_CAP], int(count))
    return out


@dataclass
class IssueHost:
    host_id: int
    ip_address: str
    hostname: Optional[str]
    severity: str
    ports: List[int]
    judged: bool
    endpoint_status: Optional[str]
    # Tests that name THIS issue on the host (``host_tests.issue_key``): still
    # to do (proposed / in progress), and results recorded for one (the
    # "tested" outcomes — a failed attempt is not a test).
    tests_to_do: int = 0
    tests_recorded: int = 0


def issue_host_total(db: Session, project_id: int, issue_key: str) -> int:
    """How many hosts carry the issue — the total ``issue_hosts`` pages over."""
    return (
        _project_rows(db, project_id, func.count(distinct(Host.id)))
        .filter(_key_is(issue_key))
        .scalar()
    ) or 0


def issue_hosts(
    db: Session, project_id: int, issue_key: str, limit: Optional[int] = None, offset: int = 0,
) -> List[IssueHost]:
    """The hosts carrying the issue (``limit`` of them by address from
    ``offset``, or all), with whether a finding covers it there.  The page's
    inspector asks for one more than it shows, so it knows when the list is cut — an
    issue on 10k hosts rendered 10k rows (review 2026-09-23 R11)."""
    judged = func.max(case((observation_judged_on_host(), 1), else_=0))
    query = (
        _project_rows(
            db, project_id, Host.id, Host.ip_address, Host.hostname, func.max(_rank()).label("rank"),
            judged.label("judged"),
        )
        .filter(_key_is(issue_key))
        .group_by(Host.id, Host.ip_address, Host.hostname)
        .order_by(Host.ip_address)
    )
    if offset:
        query = query.offset(offset)
    rows = (query.limit(limit) if limit else query).all()
    if not rows:
        return []
    host_ids = [r.id for r in rows]
    ports: Dict[int, set] = {}
    for hid, number in (
        _project_rows(db, project_id, Vulnerability.host_id, Port.port_number)
        .join(Port, Port.id == Vulnerability.port_id)
        .filter(_key_is(issue_key), Vulnerability.host_id.in_(host_ids))
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
    # Where testing of this issue stands on each host: two grouped statements
    # for the page (planned / tested are ``host_test_queries``' definitions,
    # narrowed to the tests that name this issue).
    to_do = dict(
        db.query(HostTest.host_id, func.count(HostTest.id))
        .filter(HostTest.project_id == project_id, HostTest.host_id.in_(host_ids),
                HostTest.issue_key == issue_key, HostTest.status.in_(ACTIVE_TEST_STATUSES))
        .group_by(HostTest.host_id)
        .all()
    )
    recorded = dict(
        db.query(HostTest.host_id, func.count(EvidenceRecord.id))
        .join(EvidenceRecord, EvidenceRecord.host_test_id == HostTest.id)
        .filter(HostTest.project_id == project_id, HostTest.host_id.in_(host_ids),
                HostTest.issue_key == issue_key, EvidenceRecord.outcome.in_(TESTED_OUTCOMES))
        .group_by(HostTest.host_id)
        .all()
    )
    return [
        IssueHost(
            host_id=r.id, ip_address=r.ip_address, hostname=r.hostname,
            severity=_SEVERITY_OF_RANK.get(int(r.rank or 0), "unknown"),
            ports=sorted(ports.get(r.id, ())), judged=bool(r.judged),
            endpoint_status=endpoint.get(r.id),
            tests_to_do=int(to_do.get(r.id, 0)), tests_recorded=int(recorded.get(r.id, 0)),
        )
        for r in rows
    ]


@dataclass
class IssueDetail:
    issue_key: str
    title: str
    # The scanner whose words these are: two tools describe one issue differently.
    source: str
    description: Optional[str] = None
    solution: Optional[str] = None
    cvss_score: Optional[float] = None
    cvss_vector: Optional[str] = None
    # v2.508.0 — the inspector: the same row's identifiers and references.
    cve_id: Optional[str] = None
    plugin_id: Optional[str] = None
    plugin_name: Optional[str] = None
    references: List[str] = field(default_factory=list)


def issue_detail(
    db: Session, project_id: int, issue_key: str, source: Optional[str] = None,
) -> Optional[IssueDetail]:
    """What a scanner wrote about the issue — so the list can be judged
    without opening one of its hosts.  One row speaks for it: the most severe
    that HAS a description (the most severe row may be a bare one from another
    tool), lowest id on a tie.  ``source`` asks for one scanner's words (review
    2026-10-16, O5: when two tools reported it, the other's text was
    unreachable).  What a scanner printed for a particular host
    (``plugin_output``) differs by host and stays on the host's page.
    ``None`` when the project has no such issue (from that scanner)."""
    described = case((func.coalesce(func.trim(Vulnerability.description), "") != "", 1), else_=0)
    query = _project_rows(db, project_id, Vulnerability).filter(_key_is(issue_key))
    if source is not None:
        try:
            query = query.filter(Vulnerability.source == VulnerabilitySource(source.lower()))
        except ValueError:
            raise ObservationError(
                f"Unknown source '{source}'. One of: {', '.join(s.value for s in VulnerabilitySource)}"
            )
    row = query.order_by(described.desc(), _rank().desc(), Vulnerability.id.asc()).first()
    if row is None:
        return None
    return IssueDetail(
        issue_key=issue_key,
        title=row.title,
        source=getattr(row.source, "value", row.source),
        description=(row.description or "").strip() or None,
        solution=(row.solution or "").strip() or None,
        cvss_score=row.cvss_score,
        cvss_vector=row.cvss_vector or None,
        cve_id=row.cve_id or None,
        plugin_id=row.plugin_id or None,
        plugin_name=(row.source_plugin_name or "").strip() or None,
        references=parse_references(row.references),
    )


def issue_host_rows(db: Session, project_id: int, issue_key: str, host_id: int) -> List[dict]:
    """The scanner rows of one issue on one host, as the host page serializes
    them (``serialize_vulnerability``: port, scanner, what it printed for this
    host — ``plugin_output`` — and when it was seen), most severe first, then
    by port.  What a scanner printed differs per host, so the inspector reads
    it when a host is opened; an empty list when the host carries no such
    row (or is not this project's)."""
    rows = (
        _project_rows(db, project_id, Vulnerability)
        .options(joinedload(Vulnerability.port))
        .filter(_key_is(issue_key), Vulnerability.host_id == host_id)
        .order_by(_rank().desc(), Vulnerability.port_id.asc().nullsfirst(), Vulnerability.id.asc())
        .all()
    )
    return [serialize_vulnerability(v) for v in rows]


@dataclass
class DismissOutcome:
    issue_key: str
    finding_id: int
    host_count: int


def dismiss_issue(
    db: Session, project_id: int, actor_id: int, issue_key: str,
    host_ids: Optional[Sequence[int]] = None, reason: Optional[str] = None,
) -> DismissOutcome:
    """Mark one issue a false positive from the list — on every host carrying
    it (``host_ids`` None), or on the hosts named.

    It is the host page's dismissal reached from the list — the same two
    service functions: issue-wide, ``FindingService.promote_vulnerability``
    with the false-positive status (the issue's finding becomes one, on every
    host this list counts); host by host, ``finding_actions
    .promote_or_dismiss_vulnerability(scope="host")`` (each named host's
    endpoint becomes one and an existing finding's own status is left alone).  Checked
    whole before anything is written; the hosts are taken in id order so two
    requests over the same issue lock in the same order.  The caller commits.
    """
    # Imported here: finding_actions is the click's function, and it imports
    # the finding service this module also uses.
    from app.services.finding_actions import promote_or_dismiss_vulnerability

    rows = [v for _k, v in _project_rows(db, project_id, _key(), Vulnerability).filter(_key_is(issue_key)).all()]
    if not rows:
        raise ObservationError(f"No scanner observation in this project is the issue '{issue_key}'.")
    by_host: Dict[int, Vulnerability] = {}
    for v in sorted(rows, key=lambda v: (-_RANK.get(v.severity, 0), v.id)):
        by_host.setdefault(v.host_id, v)  # the most severe row on each host, oldest first
    fp = FindingStatus.FALSE_POSITIVE.value
    if host_ids is None:
        rep = min(rows, key=lambda v: (-_RANK.get(v.severity, 0), v.id))
        # The hosts are THIS list's — every host whose row has the issue's
        # stored key — named outright, as the bulk promotion names them: the
        # service's own "every host" resolves a title-keyed row by plugin id
        # and would leave out hosts another tool reported it on.
        finding, _created = FindingService(db).promote_vulnerability(
            vuln=rep, project_id=project_id, actor_id=actor_id,
            status=fp, summary=reason, host_ids=sorted(by_host), on_join="set",
        )
        return DismissOutcome(issue_key=issue_key, finding_id=finding.id, host_count=len(by_host))
    chosen = sorted(dict.fromkeys(host_ids))
    if not chosen:
        raise ObservationError("Choose at least one host.")
    outside = [h for h in chosen if h not in by_host]
    if outside:
        raise ObservationError(
            f"Host(s) {', '.join(map(str, outside))} do not carry the issue '{rows[0].title}'."
        )
    finding = None
    for host_id in chosen:
        finding = promote_or_dismiss_vulnerability(
            db, vuln=by_host[host_id], project_id=project_id, actor_id=actor_id,
            status=fp, summary=reason, scope="host",
        )
    return DismissOutcome(issue_key=issue_key, finding_id=finding.id, host_count=len(chosen))


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
        _project_rows(db, project_id, key, Vulnerability).filter(_key_in(keys)).all()
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

    svc = FindingService(db)
    outcomes: Dict[str, PromoteOutcome] = {}
    # In issue-key order, whatever order the request names them in: each
    # promotion locks its issue's test evidence and then its finding, so two
    # overlapping requests must take the issues in the same order.
    for issue_key, rep, chosen in sorted(plan, key=lambda step: step[0]):
        # The join rule is the service's, decided when it finds (or loses the
        # insert to) the issue's finding — not from a read made before this
        # loop: a finding created in between (a false-positive dismissal on
        # one host) was then "joined" with status=confirmed and re-statused,
        # and reported as created.
        finding, created = svc.promote_vulnerability(
            vuln=rep, project_id=project_id, actor_id=actor_id,
            status=FindingStatus.CONFIRMED.value,
            host_ids=chosen,
            summary="Promoted from the scanner observations list",
            on_join="keep",
        )
        outcomes[issue_key] = PromoteOutcome(
            issue_key=issue_key, finding_id=finding.id, created=created, host_count=len(chosen),
        )
    return [outcomes[issue_key] for issue_key, _, _ in plan]
