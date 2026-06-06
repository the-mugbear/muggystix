"""
CVE Correlation Service

Service for correlating detected software versions with known vulnerabilities.
Provides automated vulnerability identification and CVSS scoring.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CVEInfo:
    """CVE vulnerability information"""
    cve_id: str
    title: str
    description: str
    cvss_score: float
    cvss_vector: str
    severity: str
    published_date: datetime
    modified_date: datetime
    affected_products: List[str]
    references: List[str]
    exploitability: str = "Unknown"
    patch_available: bool = False
    patch_url: Optional[str] = None


class CVECorrelationService:
    """Service for correlating software versions with CVE vulnerabilities"""

    def __init__(self):
        self.vulnerability_db = self._initialize_vulnerability_db()
        self.software_patterns = self._load_software_patterns()

    def find_vulnerabilities_for_service(
        self,
        service_name: str,
        service_product: str,
        service_version: str,
        port_number: int = None
    ) -> List[CVEInfo]:
        """
        Find known vulnerabilities for a specific service/software version
        """
        if not service_product or not service_version:
            return []

        logger.debug(f"Checking vulnerabilities for {service_product} {service_version}")

        vulnerabilities = []
        product_key = self._normalize_product_name(service_product)
        version_key = self._normalize_version(service_version)

        # Check against vulnerability database
        for vuln_pattern, cve_list in self.vulnerability_db.items():
            if self._matches_vulnerable_pattern(product_key, version_key, vuln_pattern):
                for cve_data in cve_list:
                    cve_info = self._create_cve_info(cve_data)
                    vulnerabilities.append(cve_info)

        # Add context-specific vulnerabilities (e.g., port-based)
        if port_number:
            port_specific_vulns = self._find_port_specific_vulnerabilities(
                port_number, service_name, service_product, service_version
            )
            vulnerabilities.extend(port_specific_vulns)

        # Sort by CVSS score (highest first)
        vulnerabilities.sort(key=lambda x: x.cvss_score, reverse=True)

        logger.info(f"Found {len(vulnerabilities)} vulnerabilities for {service_product} {service_version}")
        return vulnerabilities

    def find_os_vulnerabilities(self, os_name: str, os_family: str = None) -> List[CVEInfo]:
        """
        Find known vulnerabilities for an operating system
        """
        if not os_name:
            return []

        logger.debug(f"Checking OS vulnerabilities for {os_name}")

        vulnerabilities = []
        os_key = self._normalize_os_name(os_name)

        # Check for OS-specific vulnerabilities
        for vuln_pattern, cve_list in self.vulnerability_db.items():
            if self._matches_os_pattern(os_key, vuln_pattern):
                for cve_data in cve_list:
                    cve_info = self._create_cve_info(cve_data)
                    vulnerabilities.append(cve_info)

        # Sort by severity and CVSS score
        vulnerabilities.sort(key=lambda x: (x.severity == 'Critical', x.cvss_score), reverse=True)

        logger.info(f"Found {len(vulnerabilities)} OS vulnerabilities for {os_name}")
        return vulnerabilities

    def get_exploitability_info(self, cve_id: str) -> Dict[str, Any]:
        """
        Get exploitability information for a CVE
        """
        # This would typically query external databases like Metasploit, ExploitDB
        # For now, return basic classification

        exploit_indicators = {
            # EternalBlue and related
            'CVE-2017-0144': {
                'exploitability': 'High',
                'exploit_available': True,
                'exploit_complexity': 'Low',
                'exploit_frameworks': ['Metasploit', 'Custom exploits'],
                'in_the_wild': True
            },
            'CVE-2017-0145': {
                'exploitability': 'High',
                'exploit_available': True,
                'exploit_complexity': 'Low',
                'exploit_frameworks': ['Metasploit'],
                'in_the_wild': True
            },
            # BlueKeep
            'CVE-2019-0708': {
                'exploitability': 'High',
                'exploit_available': True,
                'exploit_complexity': 'Medium',
                'exploit_frameworks': ['Metasploit', 'Custom exploits'],
                'in_the_wild': True
            },
            # Log4Shell
            'CVE-2021-44228': {
                'exploitability': 'Critical',
                'exploit_available': True,
                'exploit_complexity': 'Low',
                'exploit_frameworks': ['Multiple'],
                'in_the_wild': True
            },
            # Default high exploitability for common patterns
        }

        return exploit_indicators.get(cve_id, {
            'exploitability': 'Unknown',
            'exploit_available': False,
            'exploit_complexity': 'Unknown',
            'exploit_frameworks': [],
            'in_the_wild': False
        })

    def _initialize_vulnerability_db(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Initialize vulnerability database with known CVEs
        This would typically be loaded from external sources like NVD
        """
        return {
            # Windows SMB vulnerabilities
            'microsoft_windows_smb': [
                {
                    'cve_id': 'CVE-2017-0144',
                    'title': 'Microsoft SMB Remote Code Execution Vulnerability (EternalBlue)',
                    'description': 'Microsoft Server Message Block 1.0 (SMBv1) server vulnerability allows remote code execution',
                    'cvss_score': 8.1,
                    'cvss_vector': 'CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H',
                    'severity': 'Critical',
                    'published_date': '2017-03-14',
                    'affected_versions': ['windows_7', 'windows_2008', 'windows_2012', 'windows_vista', 'windows_xp'],
                    'patch_available': True,
                    'patch_url': 'https://support.microsoft.com/en-us/topic/ms17-010',
                    'references': [
                        'https://nvd.nist.gov/vuln/detail/CVE-2017-0144',
                        'https://www.microsoft.com/en-us/msrc/faqs-security-update-guide'
                    ]
                },
                {
                    'cve_id': 'CVE-2017-0145',
                    'title': 'Microsoft SMB Remote Code Execution Vulnerability',
                    'description': 'Microsoft Server Message Block 1.0 (SMBv1) server vulnerability related to EternalBlue',
                    'cvss_score': 8.1,
                    'cvss_vector': 'CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H',
                    'severity': 'Critical',
                    'published_date': '2017-03-14',
                    'affected_versions': ['windows_7', 'windows_2008', 'windows_2012', 'windows_vista', 'windows_xp'],
                    'patch_available': True,
                    'patch_url': 'https://support.microsoft.com/en-us/topic/ms17-010'
                }
            ],

            # Windows RDP vulnerabilities
            'microsoft_windows_rdp': [
                {
                    'cve_id': 'CVE-2019-0708',
                    'title': 'Remote Desktop Services Remote Code Execution Vulnerability (BlueKeep)',
                    'description': 'Remote Desktop Protocol (RDP) vulnerability allows remote code execution',
                    'cvss_score': 9.8,
                    'cvss_vector': 'CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H',
                    'severity': 'Critical',
                    'published_date': '2019-05-14',
                    'affected_versions': ['windows_7', 'windows_2008', 'windows_2012', 'windows_vista', 'windows_xp'],
                    'patch_available': True,
                    'patch_url': 'https://support.microsoft.com/en-us/help/4500331'
                }
            ],

            # Apache HTTP Server vulnerabilities
            'apache_httpd': [
                {
                    'cve_id': 'CVE-2021-41773',
                    'title': 'Apache HTTP Server Path Traversal Vulnerability',
                    'description': 'Path traversal attack allows access to files outside document root',
                    'cvss_score': 7.5,
                    'cvss_vector': 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N',
                    'severity': 'High',
                    'published_date': '2021-10-05',
                    'affected_versions': ['2.4.49', '2.4.50'],
                    'patch_available': True
                }
            ],

            # OpenSSH vulnerabilities
            'openssh': [
                {
                    'cve_id': 'CVE-2020-14145',
                    'title': 'OpenSSH Observable Discrepancy Information Disclosure',
                    'description': 'Information disclosure vulnerability in OpenSSH',
                    'cvss_score': 5.9,
                    'cvss_vector': 'CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N',
                    'severity': 'Medium',
                    'published_date': '2020-06-17',
                    'affected_versions': ['6.2', '6.3', '6.4', '6.5', '6.6', '7.0', '7.1', '7.2', '7.3', '7.4', '7.5', '7.6', '7.7', '7.8', '7.9', '8.0', '8.1', '8.2', '8.3'],
                    'patch_available': True
                }
            ],

            # MySQL vulnerabilities
            'mysql': [
                {
                    'cve_id': 'CVE-2021-2307',
                    'title': 'MySQL Server Vulnerability',
                    'description': 'Vulnerability in MySQL Server component of Oracle MySQL',
                    'cvss_score': 4.9,
                    'cvss_vector': 'CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:N/I:N/A:H',
                    'severity': 'Medium',
                    'published_date': '2021-04-20',
                    'affected_versions': ['5.7.33', '8.0.23'],
                    'patch_available': True
                }
            ],

            # Microsoft IIS vulnerabilities
            'microsoft_iis': [
                {
                    'cve_id': 'CVE-2021-31207',
                    'title': 'Microsoft IIS Server Information Disclosure Vulnerability',
                    'description': 'Information disclosure vulnerability in Microsoft IIS',
                    'cvss_score': 7.5,
                    'cvss_vector': 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N',
                    'severity': 'High',
                    'published_date': '2021-05-11',
                    'affected_versions': ['10.0'],
                    'patch_available': True
                }
            ]
        }

    def _load_software_patterns(self) -> Dict[str, str]:
        """Load software name normalization patterns"""
        return {
            'apache': 'apache_httpd',
            'apache httpd': 'apache_httpd',
            'apache http server': 'apache_httpd',
            'nginx': 'nginx',
            'microsoft-iis': 'microsoft_iis',
            'iis': 'microsoft_iis',
            'openssh': 'openssh',
            'ssh': 'openssh',
            'mysql': 'mysql',
            'mariadb': 'mysql',  # Similar vulnerabilities
            'postgresql': 'postgresql',
            'postgres': 'postgresql',
            'microsoft sql server': 'mssql',
            'mssql': 'mssql',
            'smb': 'microsoft_windows_smb',
            'microsoft-ds': 'microsoft_windows_smb',
            'netbios-ssn': 'microsoft_windows_smb',
            'rdp': 'microsoft_windows_rdp',
            'ms-wbt-server': 'microsoft_windows_rdp'
        }

    def _normalize_product_name(self, product_name: str) -> str:
        """Normalize product name for database lookup"""
        if not product_name:
            return ""

        normalized = product_name.lower().strip()

        # Apply known patterns
        for pattern, normalized_name in self.software_patterns.items():
            if pattern in normalized:
                return normalized_name

        return normalized

    def _normalize_version(self, version: str) -> str:
        """Normalize version string"""
        if not version:
            return ""

        # Extract version numbers
        version_match = re.search(r'(\d+(?:\.\d+)*)', version)
        if version_match:
            return version_match.group(1)

        return version.lower().strip()

    def _normalize_os_name(self, os_name: str) -> str:
        """Normalize OS name for vulnerability lookup"""
        if not os_name:
            return ""

        os_lower = os_name.lower()

        # Windows detection
        if 'windows' in os_lower:
            if 'xp' in os_lower:
                return 'windows_xp'
            elif 'vista' in os_lower:
                return 'windows_vista'
            elif 'windows 7' in os_lower:
                return 'windows_7'
            elif 'windows 8' in os_lower:
                return 'windows_8'
            elif 'windows 10' in os_lower:
                return 'windows_10'
            elif '2003' in os_lower:
                return 'windows_2003'
            elif '2008' in os_lower:
                return 'windows_2008'
            elif '2012' in os_lower:
                return 'windows_2012'
            elif '2016' in os_lower:
                return 'windows_2016'
            elif '2019' in os_lower:
                return 'windows_2019'

        # Linux detection
        elif any(x in os_lower for x in ['linux', 'ubuntu', 'debian', 'centos', 'redhat']):
            return 'linux'

        return os_lower

    def _matches_vulnerable_pattern(self, product: str, version: str, pattern: str) -> bool:
        """Check if product/version matches vulnerability pattern"""
        if pattern == product:
            return True

        # For services that map to OS vulnerabilities
        if pattern == 'microsoft_windows_smb' and product in ['smb', 'microsoft-ds', 'netbios-ssn']:
            return True

        if pattern == 'microsoft_windows_rdp' and product in ['rdp', 'ms-wbt-server']:
            return True

        return False

    def _matches_os_pattern(self, os_name: str, pattern: str) -> bool:
        """Check if OS matches vulnerability pattern"""
        if pattern == 'microsoft_windows_smb':
            return os_name.startswith('windows_')

        if pattern == 'microsoft_windows_rdp':
            return os_name.startswith('windows_')

        return False

    def _find_port_specific_vulnerabilities(
        self,
        port_number: int,
        service_name: str,
        service_product: str,
        service_version: str
    ) -> List[CVEInfo]:
        """Find vulnerabilities specific to certain ports/services"""
        vulnerabilities = []

        # SMB vulnerabilities (ports 139, 445)
        if port_number in [139, 445]:
            smb_vulns = self._get_smb_vulnerabilities()
            vulnerabilities.extend(smb_vulns)

        # RDP vulnerabilities (port 3389)
        elif port_number == 3389:
            rdp_vulns = self._get_rdp_vulnerabilities()
            vulnerabilities.extend(rdp_vulns)

        # HTTP/HTTPS vulnerabilities (ports 80, 443, 8080, 8443)
        elif port_number in [80, 443, 8080, 8443]:
            web_vulns = self._get_web_server_vulnerabilities(service_product, service_version)
            vulnerabilities.extend(web_vulns)

        return vulnerabilities

    def _get_smb_vulnerabilities(self) -> List[CVEInfo]:
        """Get SMB-specific vulnerabilities"""
        smb_vulns = self.vulnerability_db.get('microsoft_windows_smb', [])
        return [self._create_cve_info(vuln) for vuln in smb_vulns]

    def _get_rdp_vulnerabilities(self) -> List[CVEInfo]:
        """Get RDP-specific vulnerabilities"""
        rdp_vulns = self.vulnerability_db.get('microsoft_windows_rdp', [])
        return [self._create_cve_info(vuln) for vuln in rdp_vulns]

    def _get_web_server_vulnerabilities(self, product: str, version: str) -> List[CVEInfo]:
        """Get web server specific vulnerabilities"""
        vulnerabilities = []

        normalized_product = self._normalize_product_name(product)

        if normalized_product in ['apache_httpd', 'microsoft_iis', 'nginx']:
            vulns = self.vulnerability_db.get(normalized_product, [])
            for vuln in vulns:
                if self._version_is_vulnerable(version, vuln.get('affected_versions', [])):
                    vulnerabilities.append(self._create_cve_info(vuln))

        return vulnerabilities

    def _version_is_vulnerable(self, current_version: str, vulnerable_versions: List[str]) -> bool:
        """Check if current version is in the vulnerable version list"""
        if not current_version or not vulnerable_versions:
            return False

        normalized_current = self._normalize_version(current_version)

        for vuln_version in vulnerable_versions:
            if normalized_current.startswith(vuln_version):
                return True

        return False

    def _create_cve_info(self, cve_data: Dict[str, Any]) -> CVEInfo:
        """Create CVEInfo object from raw data"""
        # Parse date
        published_date = datetime.strptime(cve_data.get('published_date', '1970-01-01'), '%Y-%m-%d')
        modified_date = published_date  # Default to published date

        # Get exploitability info
        exploit_info = self.get_exploitability_info(cve_data.get('cve_id', ''))

        return CVEInfo(
            cve_id=cve_data.get('cve_id', ''),
            title=cve_data.get('title', ''),
            description=cve_data.get('description', ''),
            cvss_score=cve_data.get('cvss_score', 0.0),
            cvss_vector=cve_data.get('cvss_vector', ''),
            severity=cve_data.get('severity', 'Unknown'),
            published_date=published_date,
            modified_date=modified_date,
            affected_products=cve_data.get('affected_versions', []),
            references=cve_data.get('references', []),
            exploitability=exploit_info.get('exploitability', 'Unknown'),
            patch_available=cve_data.get('patch_available', False),
            patch_url=cve_data.get('patch_url')
        )

    def search_cve_by_id(self, cve_id: str) -> Optional[CVEInfo]:
        """Search for a specific CVE by ID"""
        for product_vulns in self.vulnerability_db.values():
            for vuln in product_vulns:
                if vuln.get('cve_id') == cve_id:
                    return self._create_cve_info(vuln)
        return None

    def get_vulnerability_trends(self, days: int = 30) -> Dict[str, Any]:
        """Get vulnerability trends and statistics"""
        # This would typically analyze recent CVE data
        # For now, return basic statistics

        total_cves = sum(len(vulns) for vulns in self.vulnerability_db.values())
        critical_cves = 0
        high_cves = 0

        for product_vulns in self.vulnerability_db.values():
            for vuln in product_vulns:
                severity = vuln.get('severity', '').lower()
                if severity == 'critical':
                    critical_cves += 1
                elif severity == 'high':
                    high_cves += 1

        return {
            'total_vulnerabilities': total_cves,
            'critical_vulnerabilities': critical_cves,
            'high_vulnerabilities': high_cves,
            'most_common_products': ['Microsoft Windows', 'Apache HTTP Server', 'OpenSSH'],
            'trending_cves': ['CVE-2017-0144', 'CVE-2019-0708', 'CVE-2021-44228']
        }