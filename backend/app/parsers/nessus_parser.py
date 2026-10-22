"""
Nessus XML Parser

Parses Nessus XML output files to extract vulnerability and host information
for integration with the risk assessment system.
"""

# defusedxml — hardened parser entry points that disable entity
# expansion and external DTD fetching.  See audit finding C1 for the
# XXE / billion-laughs threat model.  We use ``DET.parse`` /
# ``DET.fromstring`` for anything that touches untrusted bytes, but
# the ``Element`` class itself lives in stdlib ElementTree (defusedxml
# only wraps the parser — the element tree nodes are regular stdlib
# objects), so type annotations must reference stdlib.
import xml.etree.ElementTree as ET
import defusedxml.ElementTree as DET
from app.parsers.cut_short import EndTracking, settle_cut, xml_cut_short
from app.parsers.xml_stream_helpers import strip_namespace
import logging
import re
from typing import Iterable, List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class NessusVulnerability:
    """Represents a vulnerability finding from Nessus"""
    plugin_id: str
    plugin_name: str
    severity: int  # 0=Info, 1=Low, 2=Medium, 3=High, 4=Critical
    risk_factor: str
    cvss_base_score: Optional[float]
    cvss_vector: Optional[str]
    cvss3_base_score: Optional[float]
    cvss3_vector: Optional[str]
    cve_list: List[str]
    description: str
    solution: str
    synopsis: str
    plugin_output: Optional[str]
    port: int
    protocol: str
    service_name: Optional[str]
    exploitable: bool
    patch_publication_date: Optional[datetime]
    vuln_publication_date: Optional[datetime]
    # v2.390.0 — read and dropped before: the plugin's see-also links and
    # which exploit frameworks carry a module (not just "exploitable").
    see_also: List[str] = field(default_factory=list)
    exploit_frameworks: List[str] = field(default_factory=list)
    # v2.406.0 — the product the plugin is about (normalised CPE, e.g.
    # ``a:apache:tomcat``) and the versions its output names.  Nessus fires
    # one plugin per advisory range on an outdated product; these let the
    # inspector fold those rows into one "Apache Tomcat 9.0.13" group.
    cpe: Optional[str] = None
    installed_version: Optional[str] = None
    fixed_version: Optional[str] = None


_CPE_PREFIX = re.compile(r"^(?:x-)?cpe:(?:/|2\.3:)", re.IGNORECASE)
_INSTALLED_VERSION = re.compile(r"^\s*Installed version\s*:\s*(\S.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_FIXED_VERSION = re.compile(r"^\s*Fixed version\s*:\s*(\S.*?)\s*$", re.IGNORECASE | re.MULTILINE)


def normalize_cpe(text: Optional[str]) -> Optional[str]:
    """``part:vendor:product`` of the first CPE in a ReportItem's ``<cpe>``.

    Nessus writes ``cpe:/a:apache:tomcat`` (sometimes with a version, or
    several newline-separated entries, some ``x-cpe:``).  The version is
    dropped on purpose: it is the grouping key for "the same product", and
    the installed version comes from the plugin output instead.
    """
    for token in (text or "").split():
        if not _CPE_PREFIX.match(token):
            continue
        parts = _CPE_PREFIX.sub("", token).lower().split(":")
        if len(parts) >= 3 and all(parts[:3]) and parts[0] in ("a", "o", "h"):
            return ":".join(parts[:3])[:255]
    return None


def extract_versions(plugin_output: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(installed, fixed)`` from Nessus's ``Installed version : …`` /
    ``Fixed version : …`` lines — the first install when several are listed."""
    if not plugin_output:
        return None, None
    installed = _INSTALLED_VERSION.search(plugin_output)
    fixed = _FIXED_VERSION.search(plugin_output)
    return (
        installed.group(1)[:100] if installed else None,
        fixed.group(1)[:100] if fixed else None,
    )


@dataclass
class NessusHost:
    """Represents a host from Nessus scan"""
    ip_address: str
    hostname: Optional[str]
    operating_system: Optional[str]
    mac_address: Optional[str]
    netbios_name: Optional[str]
    fqdn: Optional[str]
    vulnerabilities: List[NessusVulnerability]
    host_properties: Dict[str, str]
    # Review 2026-10-01 N9 — <ReportItem>s that raised while being read.  They
    # were logged and dropped, so the import reported nothing missing.
    unreadable_items: int = 0
    # Review 2026-10-01 B12 — whether Nessus ran its local (authenticated)
    # checks on this host.  None when the file does not say.
    credentialed: Optional[bool] = None


# Plugin 19506 ("Nessus Scan Information") prints `Credentialed checks : no`
# or `Credentialed checks : yes, as 'svc' via ssh`.
_CREDENTIALED_LINE = re.compile(r"^\s*Credentialed checks\s*:\s*(yes|no)\b", re.IGNORECASE | re.MULTILINE)
SCAN_INFORMATION_PLUGIN_ID = "19506"


def credentialed_status(host_properties: Dict[str, str], vulnerabilities: Iterable["NessusVulnerability"]) -> Optional[bool]:
    """Did Nessus authenticate to this host?  The host's own
    ``<tag name="Credentialed_Scan">true|false</tag>`` answers it; a file
    without the tag is read from plugin 19506's output.  None when neither
    is present — never a guess from which plugins fired."""
    tag = (host_properties.get("Credentialed_Scan") or "").strip().lower()
    if tag in ("true", "false"):
        return tag == "true"
    for vuln in vulnerabilities:
        if vuln.plugin_id != SCAN_INFORMATION_PLUGIN_ID or not vuln.plugin_output:
            continue
        match = _CREDENTIALED_LINE.search(vuln.plugin_output)
        if match:
            return match.group(1).lower() == "yes"
    return None


class NessusParser:
    """Streams a Nessus XML file host by host (``iter_file``).

    Streaming is the only way in: the whole-file ``parse_file`` /
    ``parse_content`` paths and the statistics they built (review 2026-10-10,
    R7) read a large export whole and had no caller but themselves.
    """

    #: Nessus severity number → the risk factor an item without one is given.
    severity_mapping = {0: "Info", 1: "Low", 2: "Medium", 3: "High", 4: "Critical"}

    def iter_file(self, file_path: str) -> Tuple[Dict[str, Any], Iterable[NessusHost]]:
        """Stream hosts from a Nessus XML file without loading everything into memory."""

        scan_info: Dict[str, Any] = {}

        def host_generator() -> Iterable[NessusHost]:
            root_seen = False
            # <Report> is the parent of every <ReportHost>.  Captured on its
            # start event so each fully-consumed host can be detached from it
            # (see the end-of-ReportHost finally) — otherwise the child list
            # accumulates one empty shell per host for the whole file.
            report_elem = None
            # Complete <ReportHost> elements read: what precedes a cut.
            hosts_read = 0
            source = EndTracking.open(file_path)
            try:
                context = DET.iterparse(source, events=("start", "end"))
                for event, elem in context:
                    tag = strip_namespace(elem.tag)

                    if event == "start":
                        if not root_seen:
                            if tag != "NessusClientData_v2":
                                raise ValueError("Not a valid Nessus XML file")
                            root_seen = True
                        if tag == "policyName" and elem.text and 'policy_name' not in scan_info:
                            scan_info['policy_name'] = elem.text
                        elif tag == "Report":
                            report_elem = elem
                            report_name = elem.get('name')
                            if report_name:
                                scan_info['report_name'] = report_name
                                if 'scan_name' not in scan_info:
                                    scan_info['scan_name'] = report_name
                        elif tag == "NessusClientData_v2":
                            scanner_name = elem.get('pluginName') or elem.get('scannerName')
                            if scanner_name:
                                scan_info['scanner_name'] = scanner_name
                            scanner_version = elem.get('scannerVersion')
                            if scanner_version:
                                scan_info['scanner_version'] = scanner_version

                    if event == "end" and tag == "ReportHost":
                        hosts_read += 1
                        try:
                            host = self._parse_host(elem)
                            if host:
                                yield host
                        finally:
                            # elem.clear() empties this ReportHost's contents but
                            # leaves the empty node attached to <Report>, so the
                            # child list would grow one shell per host across the
                            # whole file.  Detach the fully-consumed host from its
                            # parent too, keeping peak memory flat on large
                            # exports (the streaming intent of this path).
                            elem.clear()
                            if report_elem is not None:
                                try:
                                    report_elem.remove(elem)
                                except ValueError:
                                    pass
            except ET.ParseError as exc:
                # The reader stopped.  Invalid content fails the import (it
                # always did, worded as "truncated").  A file that simply
                # ENDS is settled in the one place: the import fails as cut
                # short, or — asked to keep what the file holds — the hosts
                # end here, every complete <ReportHost> delivered and the one
                # the cut fell in dropped whole (its end tag never came).
                if not xml_cut_short(source, file_path):
                    raise ValueError(f"Invalid Nessus XML: {exc}") from exc
                settle_cut(hosts_read, label="Nessus", unit="host", cause=exc)
            finally:
                source.close()

        return scan_info, host_generator()

    def _parse_host(self, report_host: ET.Element) -> Optional[NessusHost]:
        """Parse a single host from the report"""
        host_properties = {}
        vulnerabilities = []

        # Parse host properties
        host_properties_elem = report_host.find('HostProperties')
        if host_properties_elem is not None:
            for host_prop in host_properties_elem.findall('tag'):
                name = host_prop.get('name')
                text = host_prop.text
                if name and text:
                    host_properties[name] = text

        # Extract key host information
        ip_address = host_properties.get('host-ip')
        if not ip_address:
            # Fallback to name attribute
            ip_address = report_host.get('name')

        if not ip_address:
            logger.warning("Host without IP address found, skipping")
            return None

        hostname = host_properties.get('host-fqdn') or host_properties.get('netbios-name')
        operating_system = self._extract_os_info(host_properties)
        mac_address = host_properties.get('mac-address')
        netbios_name = host_properties.get('netbios-name')
        fqdn = host_properties.get('host-fqdn')

        # Parse vulnerability items
        unreadable_items = 0
        for report_item in report_host.findall('ReportItem'):
            try:
                vuln = self._parse_vulnerability(report_item)
                if vuln:
                    vulnerabilities.append(vuln)
            except Exception as e:
                logger.warning(f"Error parsing vulnerability: {e}")
                unreadable_items += 1
                continue

        return NessusHost(
            unreadable_items=unreadable_items,
            credentialed=credentialed_status(host_properties, vulnerabilities),
            ip_address=ip_address,
            hostname=hostname,
            operating_system=operating_system,
            mac_address=mac_address,
            netbios_name=netbios_name,
            fqdn=fqdn,
            vulnerabilities=vulnerabilities,
            host_properties=host_properties
        )

    def _parse_vulnerability(self, report_item: ET.Element) -> Optional[NessusVulnerability]:
        """Parse a vulnerability from a ReportItem"""
        plugin_id = report_item.get('pluginID')
        if not plugin_id:
            logger.warning(
                "Skipping ReportItem with missing pluginID (truncated or malformed Nessus XML)"
            )
            return None
        plugin_name = report_item.get('pluginName', 'Unknown')
        try:
            port = int(report_item.get('port', 0))
        except (TypeError, ValueError):
            port = 0
        protocol = report_item.get('protocol', 'tcp')
        service_name = report_item.get('svc_name')
        try:
            severity = int(report_item.get('severity', 0))
        except (TypeError, ValueError):
            severity = 0

        # Parse vulnerability details
        risk_factor = self._get_text_or_none(report_item, 'risk_factor')
        description = self._get_text_or_none(report_item, 'description', '')
        solution = self._get_text_or_none(report_item, 'solution', '')
        synopsis = self._get_text_or_none(report_item, 'synopsis', '')
        plugin_output = self._get_text_or_none(report_item, 'plugin_output')
        # Before truncation — the version lines can sit past the cap.
        installed_version, fixed_version = extract_versions(plugin_output)
        if (
            plugin_output
            and settings.NESSUS_PLUGIN_OUTPUT_MAX_CHARS
            and len(plugin_output) > settings.NESSUS_PLUGIN_OUTPUT_MAX_CHARS
        ):
            logger.debug(
                "Truncating Nessus plugin output for plugin %s to %d characters",
                plugin_id,
                settings.NESSUS_PLUGIN_OUTPUT_MAX_CHARS,
            )
            plugin_output = (
                plugin_output[: settings.NESSUS_PLUGIN_OUTPUT_MAX_CHARS]
                + "... [truncated]"
            )

        # Parse CVSS scores
        cvss_base_score = self._parse_float(self._get_text_or_none(report_item, 'cvss_base_score'))
        cvss_vector = self._get_text_or_none(report_item, 'cvss_vector')
        cvss3_base_score = self._parse_float(self._get_text_or_none(report_item, 'cvss3_base_score'))
        cvss3_vector = self._get_text_or_none(report_item, 'cvss3_vector')

        # Parse CVE list
        # v2.406.0 — Nessus writes one <cve> element PER CVE; reading only the
        # first kept one CVE of a plugin that names twenty.  Commas are still
        # split for exports that pack them into one element.  The first stays
        # the row's primary cve_id, so issue identity does not move.
        cve_list: List[str] = []
        for element in report_item.findall('cve'):
            for cve in (element.text or '').split(','):
                cve = cve.strip()
                if cve and cve not in cve_list:
                    cve_list.append(cve)

        # Parse dates
        patch_publication_date = self._parse_date(self._get_text_or_none(report_item, 'patch_publication_date'))
        vuln_publication_date = self._parse_date(self._get_text_or_none(report_item, 'vuln_publication_date'))

        # Determine if exploitable
        exploitable = self._is_exploitable(report_item)

        # Map severity to risk factor if not provided
        if not risk_factor:
            risk_factor = self.severity_mapping.get(severity, "Info")

        return NessusVulnerability(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            severity=severity,
            risk_factor=risk_factor,
            cvss_base_score=cvss_base_score,
            cvss_vector=cvss_vector,
            cvss3_base_score=cvss3_base_score,
            cvss3_vector=cvss3_vector,
            cve_list=cve_list,
            description=description,
            solution=solution,
            synopsis=synopsis,
            plugin_output=plugin_output,
            port=port,
            protocol=protocol,
            service_name=service_name,
            exploitable=exploitable,
            patch_publication_date=patch_publication_date,
            vuln_publication_date=vuln_publication_date,
            see_also=[
                line.strip() for line in (self._get_text_or_none(report_item, 'see_also') or '').splitlines()
                if line.strip()
            ],
            exploit_frameworks=[
                f"{label}: {name}" for label, tag in (
                    ("Metasploit", "metasploit_name"), ("Core Impact", "core_impact_name"),
                    ("CANVAS", "canvas_package"),
                ) if (name := self._get_text_or_none(report_item, tag))
            ],
            cpe=normalize_cpe(" ".join(c.text or "" for c in report_item.findall('cpe'))),
            installed_version=installed_version,
            fixed_version=fixed_version,
        )

    def _extract_os_info(self, host_properties: Dict[str, str]) -> Optional[str]:
        """Extract operating system information from host properties"""
        # Try different OS detection methods used by Nessus
        os_candidates = [
            host_properties.get('operating-system'),
            host_properties.get('os'),
            host_properties.get('HOST_START_os'),
            host_properties.get('system-type')
        ]

        for os_info in os_candidates:
            if os_info and os_info.strip():
                return os_info.strip()

        return None

    def _get_text_or_none(self, element: ET.Element, tag: str, default: Optional[str] = None) -> Optional[str]:
        """Get text content of a child element or return None/default"""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return default

    def _parse_float(self, value: Optional[str]) -> Optional[float]:
        """Parse string to float, return None if invalid"""
        if not value:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string to datetime object"""
        if not date_str:
            return None

        # Common date formats used by Nessus
        date_formats = [
            '%Y/%m/%d',
            '%Y-%m-%d',
            '%m/%d/%Y',
            '%d/%m/%Y',
            '%Y/%m/%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S'
        ]

        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        logger.warning(f"Could not parse date: {date_str}")
        return None

    def _is_exploitable(self, report_item: ET.Element) -> bool:
        """Determine if vulnerability is exploitable based on various indicators"""
        # Check for explicit exploit information
        exploit_available = self._get_text_or_none(report_item, 'exploit_available')
        if exploit_available and exploit_available.lower() == 'true':
            return True

        # Check for metasploit modules
        metasploit = self._get_text_or_none(report_item, 'metasploit_name')
        if metasploit:
            return True

        # Check for core impact modules
        core_impact = self._get_text_or_none(report_item, 'core_impact_name')
        if core_impact:
            return True

        # Check exploit frameworks
        canvas = self._get_text_or_none(report_item, 'canvas_package')
        if canvas:
            return True

        # Check for known exploit code
        exploit_code = self._get_text_or_none(report_item, 'exploit_code_maturity')
        if exploit_code and exploit_code.lower() in ['functional', 'high', 'proof-of-concept']:
            return True

        return False

