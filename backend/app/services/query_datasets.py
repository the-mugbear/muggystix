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

from sqlalchemy import case, cast
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Query, Session

from app.db.models import Host, Port
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
        QueryColumn("ip", Host.ip_address, "text", "The host's address.", sort_expr=_ip_sort()),
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


DATASETS: Dict[str, Dataset] = {
    "hosts": Dataset(
        name="hosts",
        description="One row per host.",
        base=_hosts,
        base_id=Host.id,
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
}
