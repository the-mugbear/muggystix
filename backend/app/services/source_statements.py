"""What each scanner observation SOURCE can state — the one declaration.

A row's fields are statements by the tool that produced it: a severity, a
CVSS score and vector, CVE ids, whether exploit code exists.  A tool that
cannot make a statement must leave the column empty (``UNKNOWN`` severity,
NULL CVSS, no CVE, NULL ``exploitable``) — never a default that reads as a
statement (Nikto's invented LOW, the exploit flag's old False).  This module
says, per ``VulnerabilitySource``, which statements the source CAN make, so
that:

* the writers refuse a statement from a source declared unable to make it
  (``refuse_undeclared`` in ``parser_utils.upsert_vulnerability`` and the
  Nessus path) — a programming error caught at the write, not a rule to
  remember;
* the Tool coverage page shows the declaration beside each tool
  (``parser_coverage.coverage_payload``);
* an empty column can be read for what it is: "this tool never says" when
  the declaration is False, "this tool could have said and did not" when it
  is True.

The catalog path (``misconfig_checks.record_misconfig``) is not a tool's
statement: a catalog check carries the CATALOG's title and severity whatever
tool saw it, so it is not checked here.

``EXTERNAL`` rows come from the tool-agnostic observation format, where every
field is the uploader's explicit statement (or its explicit absence); it may
state anything.  ``MANUAL`` is a person's or a seed's row: likewise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource


@dataclass(frozen=True)
class SourceStatements:
    """Which statements a source can make.  ``True`` = the tool's output can
    carry it; ``False`` = it never does, so the column stays empty."""
    severity: bool
    cvss: bool
    cve: bool
    exploitability: bool

    def as_dict(self) -> Dict[str, bool]:
        return {"severity": self.severity, "cvss": self.cvss, "cve": self.cve,
                "exploitability": self.exploitability}


_ALL = SourceStatements(severity=True, cvss=True, cve=True, exploitability=True)

STATEMENTS: Dict[VulnerabilitySource, SourceStatements] = {
    # Nessus: severity 0–4, CVSS v2 / v3, cve list, exploit tags.
    VulnerabilitySource.NESSUS: _ALL,
    # OpenVAS / Greenbone: threat / CVSS, CVE refs, the vector's E: metric.
    VulnerabilitySource.OPENVAS: _ALL,
    # Nikto: no rating of its own (a wrapper format may add one), occasionally
    # a CVE in a message; no CVSS, no exploit statement.
    VulnerabilitySource.NIKTO: SourceStatements(severity=True, cvss=False, cve=True, exploitability=False),
    # nmap NSE: vulners (CVSS, CVE, is_exploit) and vuln scripts (risk, CVEs,
    # the "(Exploitable)" state).
    VulnerabilitySource.NMAP: _ALL,
    # NetExec: its observations are catalog checks; it rates nothing itself.
    VulnerabilitySource.NETEXEC: SourceStatements(severity=False, cvss=False, cve=False, exploitability=False),
    # Shodan / CVE API: not written by any current parser; declared for the
    # enum's sake as sources that would carry what the record carries.
    VulnerabilitySource.SHODAN: SourceStatements(severity=False, cvss=True, cve=True, exploitability=False),
    VulnerabilitySource.CVE_API: SourceStatements(severity=True, cvss=True, cve=True, exploitability=True),
    # A person's or a seed's row: whatever they wrote.
    VulnerabilitySource.MANUAL: _ALL,
    # testssl.sh: its own severity per check, CVEs on some; no CVSS, no exploit.
    VulnerabilitySource.TESTSSL: SourceStatements(severity=True, cvss=False, cve=True, exploitability=False),
    # Nuclei: template severity, classification cvss-score, cve-id; nothing
    # about exploits.
    VulnerabilitySource.NUCLEI: SourceStatements(severity=True, cvss=True, cve=True, exploitability=False),
    # SMBMap: catalog checks only.
    VulnerabilitySource.SMBMAP: SourceStatements(severity=False, cvss=False, cve=False, exploitability=False),
    # dnsx: catalog checks only.
    VulnerabilitySource.DNSX: SourceStatements(severity=False, cvss=False, cve=False, exploitability=False),
    # The tool-agnostic observation format: every field is the uploader's
    # explicit statement or its explicit absence, so it may state anything.
    VulnerabilitySource.EXTERNAL: _ALL,
}


def statements_of(source: VulnerabilitySource) -> SourceStatements:
    """The declaration for a source; every enum value has one
    (``tests/test_source_statements.py``)."""
    return STATEMENTS[VulnerabilitySource(source)]


def statements_for_tool_id(tool_id: str) -> Optional[Dict[str, bool]]:
    """The declaration for a Tool coverage page entry (its id is the source
    name for the tools that write observations), or None for a tool that
    writes none."""
    try:
        return statements_of(VulnerabilitySource(tool_id)).as_dict()
    except ValueError:
        return None


class UndeclaredStatement(ValueError):
    """A writer passed a statement its source is declared unable to make."""


def refuse_undeclared(
    source: VulnerabilitySource,
    *,
    severity: Optional[VulnerabilitySeverity] = None,
    cvss_score: Optional[float] = None,
    cvss_vector: Optional[str] = None,
    cve_id: Optional[str] = None,
    exploitable: Optional[bool] = None,
) -> None:
    """Raise ``UndeclaredStatement`` when a value is a statement the source
    cannot make.  Called by every scanner-row writer before the write."""
    can = statements_of(source)
    name = VulnerabilitySource(source).value
    if not can.severity and severity is not None and severity != VulnerabilitySeverity.UNKNOWN:
        raise UndeclaredStatement(f"{name} does not rate severity; write UNKNOWN, not {severity}")
    if not can.cvss and (cvss_score is not None or cvss_vector):
        raise UndeclaredStatement(f"{name} does not state CVSS")
    if not can.cve and cve_id:
        raise UndeclaredStatement(f"{name} does not name CVEs")
    if not can.exploitability and exploitable is not None:
        raise UndeclaredStatement(f"{name} does not state exploitability; pass nothing, not {exploitable}")
