"""The Query page's datasets (v2.509.0): what a row is, and its columns.

Each dataset is declared ONCE here — its rows, its project scope, its host,
and its columns as typed model columns (the column-vs-blob rule: nothing a
query groups or compares is read out of a JSON blob).  ``query_service``
compiles a pipeline against this registry and nothing else; a column a user
needs that is not here is a column to add (or a field to promote), never an
expression typed into a query.

Every dataset joins its row to ``Host``: the search terms are the Hosts query
language and describe the row's host, and the project scope is the host's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Type
from urllib.parse import quote

from sqlalchemy import case, cast, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Query, Session, aliased

from app.db.models import DNSName, Host, Port, WebInterface
from app.db.models_auth import User
from app.db.models_findings import Finding, FindingHost, FindingHostStatus, FindingSeverity, FindingSource, FindingStatus
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


@dataclass
class QueryColumn:
    name: str
    expr: object                      # a SQLAlchemy column expression
    type: str                         # text | int | float | bool | datetime | enum
    description: str
    enum: Optional[Type] = None       # the Python Enum a stored value is
    # What the column sorts by when it is not its own value (an address as an
    # address, a severity by rank).
    sort_expr: Optional[object] = None
    # An enum kept as its VALUE in a text column (a finding's status), not a
    # database enum: compared with the member's value.
    enum_as_text: bool = False
    # An IP address kept as text: `<` / `>` compare it as an address, never
    # as text (second review 2026-10-16, I1: "10.0.0.10" < "10.0.0.9").
    address: bool = False
    # Numeric columns take avg / sum.
    @property
    def numeric(self) -> bool:
        return self.type in ("int", "float")


@dataclass
class Dataset:
    name: str
    description: str
    # The rows of one project: ``base(db, project_id)`` → a Query over the
    # dataset's table joined to Host, filtered to the project.
    base: Callable[[Session, int], Query]
    # The row's own id: the last sort key, so a listing is stable.
    base_id: object
    columns: Dict[str, QueryColumn]
    # The columns a plain listing shows, in order, and its order.
    default_fields: List[str]
    default_sort: List[str]
    examples: List[str] = field(default_factory=list)
    # How the Hosts-language search terms reach a host.  None: the row is
    # joined to its host and the predicate applies as it is.  A callable
    # wraps the host predicate for a row that has several hosts (a finding:
    # "an endpoint's host matches").  "none": the dataset has no host (names)
    # and search terms are refused.
    host_filter: Optional[object] = None
    # A listing row's page: the expressions read, and the path built from
    # them (None when there is nowhere to go).
    link_exprs: List[object] = field(default_factory=list)
    link: Optional[Callable[[tuple], Optional[str]]] = None


def _ip_sort():
    return cast(Host.ip_address, INET)


_SEVERITY_RANK = case(
    (Vulnerability.severity == VulnerabilitySeverity.CRITICAL, 5),
    (Vulnerability.severity == VulnerabilitySeverity.HIGH, 4),
    (Vulnerability.severity == VulnerabilitySeverity.MEDIUM, 3),
    (Vulnerability.severity == VulnerabilitySeverity.LOW, 2),
    (Vulnerability.severity == VulnerabilitySeverity.INFO, 1),
    else_=0,
)


def _host_columns() -> List[QueryColumn]:
    return [
        QueryColumn("host", Host.id, "int", "The host's id — what dc(host) counts."),
        QueryColumn("ip", Host.ip_address, "text", "The host's address (< and > compare addresses; ranges: subnet:).",
                    sort_expr=_ip_sort(), address=True),
        QueryColumn("hostname", Host.hostname, "text", "The host's display name."),
    ]


def _cols(*columns: QueryColumn) -> Dict[str, QueryColumn]:
    return {c.name: c for c in columns}


def _hosts(db: Session, project_id: int) -> Query:
    return db.query(Host).filter(Host.project_id == project_id)


def _ports(db: Session, project_id: int) -> Query:
    return db.query(Port).join(Host, Host.id == Port.host_id).filter(Host.project_id == project_id)


def _observations(db: Session, project_id: int) -> Query:
    return (
        db.query(Vulnerability)
        .join(Host, Host.id == Vulnerability.host_id)
        .outerjoin(Port, Port.id == Vulnerability.port_id)
        .filter(Host.project_id == project_id)
    )


# A finding's owner, by name (the user row joined under its own alias).
_Owner = aliased(User, name="finding_owner")

_FINDING_SEVERITY_RANK = case(
    *[(Finding.severity == sev.value, rank) for sev, rank in (
        (FindingSeverity.CRITICAL, 5), (FindingSeverity.HIGH, 4), (FindingSeverity.MEDIUM, 3),
        (FindingSeverity.LOW, 2), (FindingSeverity.INFO, 1),
    )],
    else_=0,
)


def _findings(db: Session, project_id: int) -> Query:
    return (
        db.query(Finding)
        .outerjoin(_Owner, _Owner.id == Finding.owner_id)
        .filter(Finding.project_id == project_id)
    )


def _finding_has_host(predicate):
    """A finding matches a Hosts-language term when one of its endpoints'
    hosts does — a correlated EXISTS (never ``IN (subquery)``)."""
    return (
        select(FindingHost.id)
        .join(Host, Host.id == FindingHost.host_id)
        .where(FindingHost.finding_id == Finding.id, predicate)
        .exists()
    )


def _endpoints(db: Session, project_id: int) -> Query:
    return (
        db.query(FindingHost)
        .join(Finding, Finding.id == FindingHost.finding_id)
        .join(Host, Host.id == FindingHost.host_id)
        .outerjoin(Port, Port.id == FindingHost.port_id)
        .filter(Finding.project_id == project_id)
    )


def _web(db: Session, project_id: int) -> Query:
    # A web interface carries its project; one tied to no host is listed too
    # (the host predicates then never match it).
    return (
        db.query(WebInterface)
        .outerjoin(Host, Host.id == WebInterface.host_id)
        .filter(or_(WebInterface.project_id == project_id, Host.project_id == project_id))
    )


def _names(db: Session, project_id: int) -> Query:
    return db.query(DNSName).filter(DNSName.project_id == project_id)


def _host_link(row: tuple) -> Optional[str]:
    return f"/hosts/{row[0]}" if row[0] is not None else None


DATASETS: Dict[str, Dataset] = {
    "hosts": Dataset(
        name="hosts",
        description="One row per host.",
        base=_hosts,
        base_id=Host.id,
        link_exprs=[Host.id], link=_host_link,
        columns=_cols(
            *_host_columns(),
            QueryColumn("state", Host.state, "text", "up, down or unknown."),
            QueryColumn("os", Host.os_name, "text", "The operating system as recorded."),
            QueryColumn("os_family", Host.os_family, "text", "The OS family (windows, linux, …)."),
            QueryColumn("smb_signing", Host.smb_signing, "text", "SMB signing as a scanner reported it."),
            QueryColumn("first_seen", Host.first_seen, "datetime", "When an import first recorded the host."),
            QueryColumn("last_seen", Host.last_seen, "datetime", "When an import last recorded the host."),
        ),
        default_fields=["ip", "hostname", "state", "os", "os_family"],
        default_sort=["ip"],
        examples=[
            "hosts | stats count by os_family",
            "hosts has:critical | stats count by state, os_family",
            "hosts port:445 | where isnull(smb_signing) | table ip, hostname, os",
        ],
    ),
    "ports": Dataset(
        name="ports",
        description="One row per port on a host (every state a scanner recorded).",
        base=_ports,
        base_id=Port.id,
        link_exprs=[Host.id], link=_host_link,
        columns=_cols(
            *_host_columns(),
            QueryColumn("port", Port.port_number, "int", "The port number."),
            QueryColumn("protocol", Port.protocol, "text", "tcp or udp."),
            QueryColumn("state", Port.state, "text", "open, closed, filtered…"),
            QueryColumn("service", Port.service_name, "text", "The service a scanner identified."),
            QueryColumn("product", Port.service_product, "text", "The product a scanner identified."),
            QueryColumn("version", Port.service_version, "text", "The product version a scanner identified."),
            QueryColumn("tunnel", Port.service_tunnel, "text", "ssl when the service is TLS-wrapped."),
            QueryColumn("first_seen", Port.first_seen, "datetime", "When an import first recorded the port."),
            QueryColumn("last_seen", Port.last_seen, "datetime", "When an import last recorded the port."),
        ),
        default_fields=["ip", "port", "protocol", "state", "service", "product", "version"],
        default_sort=["ip", "port"],
        examples=[
            'ports | where state="open" | stats dc(host) as hosts by service, port | sort -hosts',
            'ports | where state="open" | top limit=20 product, version',
            'ports site:"HQ" | where port IN (21, 23, 3389) AND state="open" | table ip, hostname, port, service',
        ],
    ),
    "observations": Dataset(
        name="observations",
        description="One row per scanner observation on a host (not yet a finding).",
        base=_observations,
        base_id=Vulnerability.id,
        # The observation's own inspector, not its host (second review, I9);
        # a row with no issue key is its own issue (`row:<id>`, as listed).
        link_exprs=[Vulnerability.issue_key, Vulnerability.id],
        link=lambda r: f"/observations?issue={quote(r[0] or f'row:{r[1]}', safe='')}",
        columns=_cols(
            *_host_columns(),
            QueryColumn("title", Vulnerability.title, "text", "The observation's title as the scanner wrote it."),
            QueryColumn("severity", Vulnerability.severity, "enum", "critical, high, medium, low, info.",
                        enum=VulnerabilitySeverity, sort_expr=_SEVERITY_RANK),
            QueryColumn("cve", Vulnerability.cve_id, "text", "The CVE id (the first one the scanner named)."),
            QueryColumn("plugin_id", Vulnerability.plugin_id, "text", "The scanner's own check id."),
            QueryColumn("source", Vulnerability.source, "enum", "The scanner (nessus, openvas, nuclei…).",
                        enum=VulnerabilitySource),
            QueryColumn("issue", Vulnerability.issue_key, "text",
                        "The issue key — one per issue across scanners (as on Scanner observations)."),
            QueryColumn("check", Vulnerability.check_id, "text", "The misconfiguration check id, if one."),
            QueryColumn("cvss", Vulnerability.cvss_score, "float", "The CVSS base score."),
            QueryColumn("exploitable", Vulnerability.exploitable, "bool", "A scanner reports an exploit for it."),
            QueryColumn("port", Port.port_number, "int", "The port it was seen on (null: host-level)."),
            QueryColumn("protocol", Port.protocol, "text", "The port's protocol."),
            QueryColumn("service", Port.service_name, "text", "The port's service."),
            QueryColumn("first_seen", Vulnerability.first_seen, "datetime", "When an import first recorded it."),
            QueryColumn("last_seen", Vulnerability.last_seen, "datetime", "When an import last recorded it."),
        ),
        default_fields=["ip", "hostname", "port", "severity", "title", "cve", "source"],
        default_sort=["-severity", "ip"],
        examples=[
            "observations | stats dc(host) as hosts, count by severity",
            'observations | where severity IN ("critical", "high") | stats dc(host) as hosts by title | sort -hosts | head 20',
            "observations has:web | where exploitable=true | table ip, port, title, cve",
        ],
    ),
    # --- Phase 2 (v2.510.0) -------------------------------------------------
    "findings": Dataset(
        name="findings",
        description="One row per finding. Search terms match a finding when one of its endpoints' hosts does.",
        base=_findings,
        base_id=Finding.id,
        host_filter=_finding_has_host,
        link_exprs=[Finding.id], link=lambda r: f"/findings/{r[0]}",
        columns=_cols(
            QueryColumn("finding", Finding.id, "int", "The finding's number."),
            QueryColumn("title", Finding.title, "text", "The finding's title."),
            QueryColumn("severity", Finding.severity, "enum", "critical, high, medium, low, info.",
                        enum=FindingSeverity, enum_as_text=True, sort_expr=_FINDING_SEVERITY_RANK),
            QueryColumn("status", Finding.status, "enum",
                        "open (under investigation), confirmed, false_positive, accepted_risk, remediated.",
                        enum=FindingStatus, enum_as_text=True),
            QueryColumn("source", Finding.source, "enum", "How it was made: scanner, execution, manual, note.",
                        enum=FindingSource, enum_as_text=True),
            QueryColumn("owner", _Owner.username, "text", "The owner's username (null: nobody owns it)."),
            QueryColumn("cvss", Finding.cvss_score, "float", "The CVSS base score."),
            QueryColumn("issue", Finding.dedup_key, "text", "The issue key a scanner finding stands for."),
            QueryColumn("created_at", Finding.created_at, "datetime", "When it was made."),
            QueryColumn("updated_at", Finding.updated_at, "datetime", "When it last changed."),
        ),
        default_fields=["finding", "severity", "status", "title", "owner"],
        default_sort=["-severity", "finding"],
        examples=[
            "findings | stats count by severity, status",
            'findings | where isnull(owner) AND status IN ("open", "confirmed") | table finding, severity, title',
            "findings site:\"HQ\" | top owner",
        ],
    ),
    "endpoints": Dataset(
        name="endpoints",
        description="One row per finding on one host — the finding's affected systems and each one's state.",
        base=_endpoints,
        base_id=FindingHost.id,
        link_exprs=[FindingHost.finding_id, FindingHost.id],
        link=lambda r: f"/findings/{r[0]}?endpoint={r[1]}#endpoints",
        columns=_cols(
            *_host_columns(),
            QueryColumn("finding", Finding.id, "int", "The finding's number."),
            QueryColumn("title", Finding.title, "text", "The finding's title."),
            QueryColumn("severity", Finding.severity, "enum", "The finding's severity.",
                        enum=FindingSeverity, enum_as_text=True, sort_expr=_FINDING_SEVERITY_RANK),
            QueryColumn("finding_status", Finding.status, "enum", "The finding's status.",
                        enum=FindingStatus, enum_as_text=True),
            QueryColumn("state", FindingHost.host_status, "enum",
                        "This host's state: open (still present), retest, remediated, false_positive.",
                        enum=FindingHostStatus, enum_as_text=True),
            QueryColumn("port", Port.port_number, "int", "The port the endpoint names (null: host-level)."),
            QueryColumn("retest_requested_at", FindingHost.retest_requested_at, "datetime",
                        "When a retest was asked for on this host (null: none)."),
            QueryColumn("added_at", FindingHost.created_at, "datetime", "When the host was added to the finding."),
        ),
        default_fields=["ip", "hostname", "finding", "severity", "state", "title"],
        default_sort=["-severity", "finding", "ip"],
        examples=[
            "endpoints | stats dc(host) as hosts by title, state | sort -hosts",
            'endpoints | where state="retest" | table ip, finding, title, retest_requested_at',
            'endpoints | where severity="critical" | stats dc(finding) as findings by ip | sort -findings | head 20',
        ],
    ),
    "web": Dataset(
        name="web",
        description="One row per web interface a tool recorded (httpx, eyewitness, nikto…).",
        base=_web,
        base_id=WebInterface.id,
        link_exprs=[Host.id], link=_host_link,
        columns=_cols(
            *_host_columns(),
            QueryColumn("url", WebInterface.url, "text", "The URL that answered."),
            QueryColumn("scheme", WebInterface.protocol, "text", "http or https."),
            QueryColumn("port", WebInterface.port, "int", "The port."),
            QueryColumn("status_code", WebInterface.status_code, "int", "The HTTP status code."),
            QueryColumn("title", WebInterface.title, "text", "The page title."),
            QueryColumn("server", WebInterface.server_header, "text", "The Server response header."),
            QueryColumn("source", WebInterface.source, "text", "The tool that recorded it."),
            QueryColumn("favicon_hash", WebInterface.favicon_hash, "text", "The favicon's hash (mmh3)."),
            QueryColumn("cert_not_after", WebInterface.cert_not_after, "datetime", "When the TLS certificate expires."),
            QueryColumn("cert_self_signed", WebInterface.cert_self_signed, "bool", "The certificate is self-signed."),
            QueryColumn("cert_org", WebInterface.cert_subject_org, "text", "The certificate subject's organisation."),
        ),
        default_fields=["ip", "url", "status_code", "title", "server"],
        default_sort=["ip", "port"],
        examples=[
            "web | stats dc(host) as hosts by server | sort -hosts | head 20",
            "web | where cert_self_signed=true | table url, cert_org, cert_not_after",
            "web | stats count by favicon_hash, title | sort -count | head 20",
        ],
    ),
    "names": Dataset(
        name="names",
        description="One row per DNS name the engagement knows. Names have no host of their own: use where.",
        base=_names,
        base_id=DNSName.id,
        host_filter="none",
        columns=_cols(
            QueryColumn("fqdn", DNSName.fqdn, "text", "The name (lowercase, no trailing dot)."),
            QueryColumn("kind", DNSName.kind, "text", "fqdn, or wildcard for a *.example.com pattern."),
            QueryColumn("first_seen", DNSName.first_seen, "datetime", "Its earliest observation."),
            QueryColumn("last_seen", DNSName.last_seen, "datetime", "Its latest observation."),
        ),
        default_fields=["fqdn", "kind", "first_seen", "last_seen"],
        default_sort=["fqdn"],
        examples=[
            "names | stats count by kind",
            'names | where like(fqdn, "%.corp.example.com") | table fqdn, last_seen',
        ],
    ),
}
