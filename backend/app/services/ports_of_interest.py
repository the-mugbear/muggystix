"""Catalog of high-value network ports and associated metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class PortOfInterest:
    port: int
    protocol: str
    label: str
    category: str
    weight: int
    rationale: str
    recommended_action: str


PORTS_OF_INTEREST: List[PortOfInterest] = [
    PortOfInterest(
        port=22,
        protocol="tcp",
        label="SSH Remote Administration",
        category="Remote Access",
        weight=5,
        rationale="Common administrative access; brute-force and credential reuse are frequent attack vectors.",
        recommended_action="Restrict to bastion hosts or enforce MFA/keys with network segmentation."
    ),
    PortOfInterest(
        port=23,
        protocol="tcp",
        label="Telnet",
        category="Legacy Remote Access",
        weight=6,
        rationale="Cleartext credentials; routinely abused for lateral movement on legacy infrastructure.",
        recommended_action="Disable or migrate to secure alternatives such as SSH."
    ),
    PortOfInterest(
        port=445,
        protocol="tcp",
        label="SMB File Sharing",
        category="Lateral Movement",
        weight=7,
        rationale="Primary vector for ransomware and worm propagation (EternalBlue, DoublePulsar).",
        recommended_action="Limit exposure, enable SMB signing, and patch against known CVEs."
    ),
    PortOfInterest(
        port=389,
        protocol="tcp",
        label="LDAP",
        category="Directory Services",
        weight=5,
        rationale="Exposes directory queries; risky when combined with weak authentication or default binds.",
        recommended_action="Require LDAPS and restrict anonymous binds."
    ),
    PortOfInterest(
        port=3268,
        protocol="tcp",
        label="Global Catalog",
        category="Directory Services",
        weight=4,
        rationale="Reveals AD structure and can leak sensitive attributes in hybrid environments.",
        recommended_action="Restrict exposure to trusted networks and enforce secure binds."
    ),
    PortOfInterest(
        port=3389,
        protocol="tcp",
        label="RDP",
        category="Remote Access",
        weight=7,
        rationale="High-value lateral movement and initial access vector; brute-force and BlueKeep-style exploits common.",
        recommended_action="Gate behind VPN or zero-trust brokers, enforce MFA, monitor for failed logons."
    ),
    PortOfInterest(
        port=5985,
        protocol="tcp",
        label="WinRM",
        category="Remote Access",
        weight=6,
        rationale="PowerShell remoting exposed; credential compromise enables full host control.",
        recommended_action="Constrain remoting endpoints and require Kerberos with Just Enough Administration."
    ),
    PortOfInterest(
        port=1433,
        protocol="tcp",
        label="MSSQL",
        category="Databases",
        weight=6,
        rationale="Database engines with default creds or xp_cmdshell exposure enable data exfiltration.",
        recommended_action="Restrict to application tiers and enforce strong authentication."
    ),
    PortOfInterest(
        port=3306,
        protocol="tcp",
        label="MySQL",
        category="Databases",
        weight=5,
        rationale="Internet-facing MySQL often leaks data; weak SQL accounts are common.",
        recommended_action="Limit exposure and rotate credentials; require TLS connections."
    ),
    PortOfInterest(
        port=5432,
        protocol="tcp",
        label="PostgreSQL",
        category="Databases",
        weight=5,
        rationale="Database instances with weak auth or replication enabled expose sensitive data.",
        recommended_action="Restrict network access and enforce scram-sha-256 authentication."
    ),
    PortOfInterest(
        port=27017,
        protocol="tcp",
        label="MongoDB",
        category="Databases",
        weight=6,
        rationale="Historically defaults to no authentication; large data leaks observed.",
        recommended_action="Enable auth, bind to localhost, and require TLS."
    ),
    PortOfInterest(
        port=9200,
        protocol="tcp",
        label="Elasticsearch",
        category="Databases",
        weight=5,
        rationale="Cluster APIs allow data extraction or remote code execution when misconfigured.",
        recommended_action="Enable Shield/Security features and segment the cluster network."
    ),
    PortOfInterest(
        port=6379,
        protocol="tcp",
        label="Redis",
        category="Caching",
        weight=6,
        rationale="Unauthenticated access enables RCE via module loading or config rewrite.",
        recommended_action="Bind to localhost, require auth, and disable dangerous commands."
    ),
    PortOfInterest(
        port=5900,
        protocol="tcp",
        label="VNC",
        category="Remote Access",
        weight=5,
        rationale="Often exposed with weak or no passwords; session hijacking risk.",
        recommended_action="Tunnel over SSH and enforce strong authentication policies."
    ),
]


def ports_by_number() -> dict[int, PortOfInterest]:
    return {entry.port: entry for entry in PORTS_OF_INTEREST}


# --- The Hosts list's Exposure chips ----------------------------------------
# The browser used to hold a hand-mirrored copy of the table above (numbers
# and weights) plus a service-name map that existed only there; nothing
# compared the two (review 2026-10-23b, FEP-4).  The list row now carries its
# chips (`exposure`), built here from this one table.

#: The short word a chip prints for each port of interest.
CHIP_LABELS: Dict[int, str] = {
    22: "SSH", 23: "Telnet", 445: "SMB", 389: "LDAP", 3268: "Global Catalog",
    3389: "RDP", 5985: "WinRM", 1433: "MSSQL", 3306: "MySQL", 5432: "PostgreSQL",
    27017: "MongoDB", 9200: "Elasticsearch", 6379: "Redis", 5900: "VNC",
}

#: nmap service names that identify a port of interest WHEREVER it was found
#: (lower-cased ``Port.service_name`` as nmap's probe engine reports it), so
#: RDP probed on 3390 still reads "RDP" — on the chips and in every
#: risky-service count (``port_of_interest``, owner 2026-10-23).
SERVICE_NAMES: Dict[str, int] = {
    "telnet": 23,
    "microsoft-ds": 445, "netbios-ssn": 445, "smb": 445,
    "ms-wbt-server": 3389, "rdp": 3389,
    "wsman": 5985, "winrm": 5985,
    "ms-sql-s": 1433, "mssql": 1433,
    "mongodb": 27017, "mongod": 27017,
    "redis": 6379,
    "ssh": 22,
    "ldap": 389, "ldaps": 389,
    "mysql": 3306,
    "postgresql": 5432, "postgres": 5432,
    "elasticsearch": 9200,
    "vnc": 5900, "vnc-http": 5900,
    "globalcatldap": 3268, "globalcatldapssl": 3268,
}


def port_of_interest(port: Any) -> Optional[PortOfInterest]:
    """The port of interest an OPEN port exposes, or None — the ONE rule
    behind the Hosts list's Exposure chips, the subnet insight's risky
    exposed services and the Operations queue's high-value ports (owner,
    2026-10-23: "one rule everywhere").

    A PROBED service name decides: a port-of-interest name gives that port of
    interest wherever it runs (RDP on 3390), any other probed name gives none
    (ssh probed on 3389 is not RDP).  Otherwise — no name, or nmap's table
    guess — the port's number decides.  ``port_of_interest_condition`` is
    its SQL form, pinned to it row by row."""
    if port.state != "open":
        return None
    by_number = ports_by_number()
    name = (port.service_name or "").strip().lower()
    if port.service_method == "probed" and name:
        return by_number.get(SERVICE_NAMES.get(name, -1))
    return by_number.get(port.port_number)


def port_of_interest_condition():
    """``port_of_interest(port) is not None`` as a condition on ``Port`` —
    for the reads that count or rank in SQL."""
    from sqlalchemy import and_, func, not_, or_

    from app.db import models

    by_number = ports_by_number()
    name = func.lower(func.trim(func.coalesce(models.Port.service_name, "")))
    probed_named = and_(func.coalesce(models.Port.service_method, "") == "probed", name != "")
    poi_names = sorted(n for n, number in SERVICE_NAMES.items() if number in by_number)
    return and_(
        models.Port.state == "open",
        or_(
            and_(probed_named, name.in_(poi_names)),
            and_(not_(probed_named), models.Port.port_number.in_(sorted(by_number))),
        ),
    )


def exposure_chips(ports: Iterable[Any]) -> List[Dict[str, Any]]:
    """A host's Exposure chips, ranked: ``[{key, label, port, detected,
    weight}]`` over its OPEN ports.

    Per open port: a PROBED service name wins and is shown as detected (a port
    of interest's chip when the name is one, else the name itself at weight 0,
    so a host with only http / https still says so); otherwise a port of
    interest's NUMBER gives a guess, marked as such.  A name that came from
    nmap's port table (method "table") is a guess: nmap assigned it from the
    number.  The same service on two ports is one chip, and a detection beats
    a guess.  Highest weight first, detected ahead of guessed, then by label.
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    for p in ports:
        if p.state != "open":
            continue
        name = (p.service_name or "").strip().lower()
        probed_named = p.service_method == "probed" and bool(name)
        poi = port_of_interest(p)
        if poi is not None:
            label = CHIP_LABELS[poi.port]
            chip = {"key": label, "label": label, "port": p.port_number, "detected": probed_named, "weight": poi.weight}
        elif probed_named:
            chip = {"key": name, "label": name, "port": p.port_number, "detected": True, "weight": 0}
        else:
            continue
        existing = by_key.get(chip["key"])
        if existing is None or (chip["detected"] and not existing["detected"]):
            by_key[chip["key"]] = chip
    return sorted(by_key.values(), key=lambda c: (-c["weight"], not c["detected"], c["label"].lower()))

