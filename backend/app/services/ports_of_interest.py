"""Catalog of high-value network ports and associated metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


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

