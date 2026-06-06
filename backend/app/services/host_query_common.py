"""Dependency-free primitives shared across the /hosts query stack.

These three building blocks — LIKE escaping, CIDR parsing, and the
service→port lookup table — are used by the legacy filter builder
(``host_query``), the per-dimension predicate helpers
(``host_query_predicates``), and the boolean query DSL
(``host_query_dsl``).  They live here, in a leaf module that imports only
``models``, so those higher-level modules can all depend on them without
forming an import cycle.

v2.93.0 — lifted out of ``host_query`` (where they originally lived)
when the predicate layer was introduced; ``host_query`` re-exports them
so existing imports (e.g. ``from app.services.host_query import
escape_like``) keep working unchanged.
"""
from __future__ import annotations

import ipaddress

from sqlalchemy import cast, literal
from sqlalchemy.dialects.postgresql import INET

from app.db import models


# ---------------------------------------------------------------------------
# Service → port lookup
# ---------------------------------------------------------------------------

# Maps a friendly service name (typed into the search box or supplied
# in the ``services=`` filter) to the canonical port numbers we use as
# additional search anchors.  Used by both the /hosts free-text search
# and the structured ``services`` filter.
SERVICE_PORT_MAPPINGS: dict[str, list[int]] = {
    # Web services
    'http': [80, 8000, 8080, 8081, 8008, 8888],
    'https': [443, 8443, 8444],
    'web': [80, 443, 8000, 8080, 8081, 8008, 8443, 8444, 8888],

    # Remote access
    'ssh': [22],
    'telnet': [23],
    'rdp': [3389],
    'vnc': [5900, 5901, 5902, 5903, 5904, 5905],

    # File transfer
    'ftp': [21, 20],
    'sftp': [22],
    'ftps': [990, 989],
    'tftp': [69],

    # Email
    'smtp': [25, 587, 465],
    'pop3': [110, 995],
    'imap': [143, 993],
    'mail': [25, 110, 143, 587, 465, 995, 993],

    # DNS
    'dns': [53],

    # Network management
    'snmp': [161, 162],
    'ntp': [123],
    'syslog': [514],

    # Databases
    'mysql': [3306],
    'postgresql': [5432],
    'postgres': [5432],
    'mssql': [1433],
    'sqlserver': [1433],
    'oracle': [1521],
    'mongodb': [27017],
    'redis': [6379],

    # Windows services
    'netbios': [137, 138, 139],
    'smb': [445, 139],
    'cifs': [445],
    'winrm': [5985, 5986],
    'rpc': [135],
    'ldap': [389, 636],
    'kerberos': [88],

    # Other common services
    'dhcp': [67, 68],
    'printer': [515, 631, 9100],
    'ipp': [631],
    'upnp': [1900],
    'sip': [5060, 5061],
    'rtsp': [554],
    'irc': [6667, 6697],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user input is matched literally.

    The /hosts/ search path uses ``ilike`` extensively; without
    escaping, a search term containing ``%`` or ``_`` would silently
    match more rows than the user expected.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_subnets(subnet_str: str):
    """Convert a comma-separated CIDR list into a list of SQLAlchemy
    filter expressions, one per CIDR.  Returns ``None`` when the input
    didn't yield any usable conditions so callers can skip the filter.

    Every CIDR compiles to ``hosts_v2.ip_address::inet <<= :cidr::inet``
    — exact containment regardless of prefix length, single predicate
    per CIDR.

    v2.86.7 — collapsed the previous two-branch implementation (per-IP
    OR predicates for networks ≤ 1000 addresses, ``inet <<=`` for
    larger).  The small-network branch generated up to ~1000+
    ``ip_address == ...`` predicates per CIDR, so filtering by even
    three /24s ballooned the WHERE clause past 700 ORs — bad for query
    compilation time and SQL size, and the index path the comment
    invoked isn't actually faster than ``inet <<=`` once Postgres has
    the canonical containment plan available.  ``inet <<=`` also
    correctly includes the network + broadcast addresses (the old code
    had to special-case them).
    """
    subnet_conditions = []
    subnets_list = [s.strip() for s in subnet_str.split(',') if s.strip()]

    for subnet_cidr in subnets_list:
        try:
            # Validate the CIDR but discard the parsed object — we only
            # need to confirm the input is a real network before handing
            # the string to Postgres' inet cast (which would raise its
            # own less-useful error on a malformed value).
            ipaddress.ip_network(subnet_cidr, strict=False)
            subnet_conditions.append(
                cast(models.Host.ip_address, INET).op('<<=')(
                    cast(literal(subnet_cidr), INET)
                )
            )
        except (ipaddress.AddressValueError, ValueError):
            # Not a valid CIDR — fall back to prefix-match as if the
            # user typed an IP fragment.
            subnet_conditions.append(models.Host.ip_address.like(f'{subnet_cidr}%'))

    return subnet_conditions if subnet_conditions else None
