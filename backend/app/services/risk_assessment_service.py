"""
Risk Assessment Service

Comprehensive security risk analysis for network hosts including:
- Vulnerability assessment using hardcoded vulnerability patterns
- Configuration analysis of exposed services and ports
- Attack surface evaluation based on service fingerprints
- Risk scoring and prioritization with manual CVSS scoring

Data Sources Used:
- Host.ports: Open ports and services from network scans
- Port.scripts: Nmap script output for service detection
- HostScript: Additional security script results
- Hardcoded vulnerability patterns (not live CVE feeds)
- Service version analysis with predefined vulnerability rules

Current Limitations:
- No integration with live CVE databases (NVD, MITRE)
- Uses hardcoded vulnerability patterns instead of real-time CVE data
- Manual CVSS scores rather than official CVE scores
- Limited to predefined vulnerability checks

Assessment Process:
1. Service enumeration from scan data
2. Version fingerprinting and pattern matching
3. Configuration weakness detection
4. Attack surface calculation
5. Risk score computation using weighted algorithms
6. Security findings generation with remediation advice

For Real CVE Integration, Consider:
- NVD API: https://nvd.nist.gov/developers/vulnerabilities
- MITRE CVE API: https://cveawg.mitre.org/api/
- VulnDB: Commercial vulnerability database services
- CVE JSON feeds: https://github.com/CVEProject/cvelistV5
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_

from app.db.models import Host, Port, HostScript, Script
from app.db.models_risk import (
    HostRiskAssessment,
    HostVulnerability,
    SecurityFinding,
    VulnerabilityDatabase,
    RiskRecommendation
)
from app.services.vulnerability_db_service import VulnerabilityDatabaseService

logger = logging.getLogger(__name__)


class RiskAssessmentService:
    """Service for conducting comprehensive security risk assessments"""

    def __init__(self, db: Session):
        self.db = db
        self.vuln_db_service = VulnerabilityDatabaseService(db)

        # Risk scoring weights
        self.weights = {
            'vulnerability_score': 0.4,
            'exposure_score': 0.25,
            'configuration_score': 0.2,
            'attack_surface_score': 0.15
        }

        # Dangerous ports that increase exposure risk
        self.dangerous_ports = {
            21: "FTP", 23: "Telnet", 25: "SMTP", 53: "DNS", 69: "TFTP",
            110: "POP3", 135: "RPC", 139: "NetBIOS", 143: "IMAP",
            161: "SNMP", 389: "LDAP", 445: "SMB", 514: "RSH",
            993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 1521: "Oracle",
            2049: "NFS", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
            5900: "VNC", 6379: "Redis", 27017: "MongoDB"
        }

    def assess_host_risk(self, host_id: int, force_refresh: bool = False) -> HostRiskAssessment:
        """Assess security risk for a specific host"""
        # Ensure vulnerability database is seeded
        try:
            self.vuln_db_service.seed_common_vulnerabilities()
        except Exception as e:
            logger.warning(f"Could not seed vulnerability database: {str(e)}")

        # Continue with existing assessment logic
        host = self.db.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise ValueError(f"Host {host_id} not found")

        # Check for existing recent assessment
        if not force_refresh:
            recent_assessment = self.db.query(HostRiskAssessment).filter(
                and_(
                    HostRiskAssessment.host_id == host_id,
                    HostRiskAssessment.assessment_date > datetime.utcnow() - timedelta(hours=24)
                )
            ).first()

            if recent_assessment:
                logger.info(f"Using existing assessment for host {host_id}")
                return recent_assessment

        logger.info(f"Starting comprehensive risk assessment for host {host.ip_address}")

        # Create new assessment
        assessment = HostRiskAssessment(
            host_id=host_id,
            risk_score=0.0,
            risk_level="info",
            assessment_date=datetime.utcnow()
        )

        self.db.add(assessment)
        self.db.flush()  # Get the assessment ID

        try:
            # 1. Vulnerability Assessment
            vulnerability_score = self._assess_vulnerabilities(host, assessment)

            # 2. Configuration Analysis
            config_score = self._assess_configuration(host, assessment)

            # 3. Exposure Analysis
            exposure_score = self._assess_exposure(host, assessment)

            # 4. Attack Surface Analysis
            attack_surface_score = self._assess_attack_surface(host, assessment)

            # Calculate overall risk score
            overall_score = (
                vulnerability_score * self.weights['vulnerability_score'] +
                exposure_score * self.weights['exposure_score'] +
                config_score * self.weights['configuration_score'] +
                attack_surface_score * self.weights['attack_surface_score']
            )

            # Update assessment with scores
            assessment.risk_score = min(100.0, overall_score)
            assessment.risk_level = self._calculate_risk_level(assessment.risk_score)
            assessment.exposure_risk_score = exposure_score
            assessment.configuration_risk_score = config_score
            assessment.attack_surface_score = attack_surface_score
            assessment.patch_urgency_score = vulnerability_score

            # Generate risk summary
            assessment.risk_summary = self._generate_risk_summary(assessment)

            # Generate recommendations
            self._generate_recommendations(assessment)

            self.db.commit()
            logger.info(f"Risk assessment completed for {host.ip_address}: {assessment.risk_level} ({assessment.risk_score:.1f})")

            return assessment

        except Exception as e:
            self.db.rollback()
            logger.error(f"Risk assessment failed for host {host_id}: {str(e)}")
            raise

    def _assess_vulnerabilities(self, host: Host, assessment: HostRiskAssessment) -> float:
        """Assess vulnerabilities based on services and versions"""
        vulnerability_score = 0.0

        # Get all open ports with service information
        open_ports = [p for p in host.ports if p.state == 'open']

        for port in open_ports:
            # Check for known vulnerable services
            vuln_score = self._check_service_vulnerabilities(host, port, assessment)
            vulnerability_score += vuln_score

        # Update vulnerability counts in assessment
        vulnerabilities = self.db.query(HostVulnerability).filter(
            HostVulnerability.risk_assessment_id == assessment.id
        ).all()

        assessment.vulnerability_count = len(vulnerabilities)
        assessment.critical_vulnerabilities = len([v for v in vulnerabilities if v.severity == 'Critical'])
        assessment.high_vulnerabilities = len([v for v in vulnerabilities if v.severity == 'High'])
        assessment.medium_vulnerabilities = len([v for v in vulnerabilities if v.severity == 'Medium'])
        assessment.low_vulnerabilities = len([v for v in vulnerabilities if v.severity == 'Low'])

        return min(100.0, vulnerability_score)

    def _check_service_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment) -> float:
        """Check for vulnerabilities in specific services"""
        score = 0.0

        service = port.service_name.lower() if port.service_name else ""
        version = port.service_version or ""
        product = port.service_product or ""

        # Common vulnerable service patterns
        vulnerable_patterns = {
            'ssh': self._check_ssh_vulnerabilities,
            'http': self._check_http_vulnerabilities,
            'https': self._check_http_vulnerabilities,
            'ftp': self._check_ftp_vulnerabilities,
            'telnet': self._check_telnet_vulnerabilities,
            'smtp': self._check_smtp_vulnerabilities,
            'smb': self._check_smb_vulnerabilities,
            'rdp': self._check_rdp_vulnerabilities,
            'mysql': self._check_mysql_vulnerabilities,
            'mssql': self._check_mssql_vulnerabilities,
        }

        for service_type, check_function in vulnerable_patterns.items():
            if service_type in service:
                vuln_score = check_function(host, port, assessment, version, product)
                score += vuln_score

        # Also check the local vulnerability database for this product/service
        if product or service:
            db_score = self._check_database_vulnerabilities(host, port, assessment, version, product or service)
            score += db_score

        return score

    def _check_ssh_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check SSH service for vulnerabilities"""
        score = 0.0

        # Check for old SSH versions
        if version:
            version_match = re.search(r'(\d+\.\d+)', version)
            if version_match:
                ver = float(version_match.group(1))
                if ver < 2.0:
                    self._create_vulnerability(
                        assessment, host.id, "CVE-2016-0777", "SSH1 Protocol Enabled",
                        "SSH version 1 protocol is enabled which has known security vulnerabilities",
                        9.3, "Critical", port.port_number, "ssh"
                    )
                    score += 30

        # Check for weak SSH configurations
        ssh_scripts = [s for s in port.scripts if 'ssh' in s.script_id.lower()]
        for script in ssh_scripts:
            if 'password' in script.output.lower() and 'authentication' in script.output.lower():
                self._create_security_finding(
                    assessment, host.id, "weak_authentication",
                    "SSH Password Authentication Enabled",
                    "SSH server allows password authentication which is less secure than key-based auth",
                    "Medium", 5.0, script.output
                )
                score += 10

        return score

    def _check_http_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check HTTP/HTTPS services for vulnerabilities"""
        score = 0.0

        # Check for old Apache/Nginx versions
        if 'apache' in product.lower():
            apache_vulns = self._check_apache_version(version)
            for vuln in apache_vulns:
                self._create_vulnerability(assessment, host.id, **vuln)
                score += vuln['cvss_score'] * 2

        elif 'nginx' in product.lower():
            nginx_vulns = self._check_nginx_version(version)
            for vuln in nginx_vulns:
                self._create_vulnerability(assessment, host.id, **vuln)
                score += vuln['cvss_score'] * 2

        # Check for insecure HTTP headers
        http_scripts = [s for s in port.scripts if 'http' in s.script_id.lower()]
        for script in http_scripts:
            if 'server' in script.script_id.lower():
                if 'Server:' in script.output:
                    self._create_security_finding(
                        assessment, host.id, "information_disclosure",
                        "HTTP Server Banner Disclosure",
                        "Web server reveals version information in HTTP headers",
                        "Low", 2.0, script.output
                    )
                    score += 5

        return score

    def _check_telnet_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check Telnet service (inherently insecure)"""
        self._create_vulnerability(
            assessment, host.id, "CVE-1999-0619", "Telnet Service Enabled",
            "Telnet transmits data in cleartext and is inherently insecure",
            7.5, "High", port.port_number, "telnet"
        )

        self._create_security_finding(
            assessment, host.id, "insecure_protocol",
            "Insecure Telnet Protocol",
            "Telnet service is running which transmits credentials in cleartext",
            "High", 7.5, f"Telnet service on port {port.port_number}"
        )

        return 25

    def _check_smb_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check SMB service for vulnerabilities"""
        score = 0.0

        # Check for SMBv1
        smb_scripts = [s for s in port.scripts if 'smb' in s.script_id.lower()]
        for script in smb_scripts:
            if 'smb-protocol' in script.script_id or 'smb2-protocol' in script.script_id:
                if 'SMBv1' in script.output or '1.0' in script.output:
                    self._create_vulnerability(
                        assessment, host.id, "CVE-2017-0144", "SMBv1 Enabled (WannaCry)",
                        "SMBv1 protocol is enabled making system vulnerable to WannaCry-style attacks",
                        9.3, "Critical", port.port_number, "smb"
                    )
                    score += 35

        return score

    def _check_ftp_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check FTP service for vulnerabilities"""
        score = 0.0

        # FTP is inherently insecure (cleartext authentication)
        self._create_security_finding(
            assessment, host.id, "insecure_protocol",
            "FTP Cleartext Authentication",
            "FTP service transmits credentials in cleartext",
            "Medium", 6.0, f"Port {port.port_number}/tcp"
        )
        score += 10

        # Check for anonymous FTP
        ftp_scripts = [s for s in port.scripts if 'ftp' in s.script_id.lower()]
        for script in ftp_scripts:
            if 'anonymous' in script.output.lower() and 'allowed' in script.output.lower():
                self._create_vulnerability(
                    assessment, host.id, "CVE-2010-0927", "Anonymous FTP Access",
                    "FTP server allows anonymous access which may expose sensitive files",
                    5.3, "Medium", port.port_number, "ftp"
                )
                score += 15

        return score

    def _check_smtp_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check SMTP service for vulnerabilities"""
        score = 0.0

        # Check for open relay
        smtp_scripts = [s for s in port.scripts if 'smtp' in s.script_id.lower()]
        for script in smtp_scripts:
            if 'relay' in script.output.lower() and 'open' in script.output.lower():
                self._create_vulnerability(
                    assessment, host.id, "CWE-200", "Open SMTP Relay",
                    "SMTP server configured as open relay allowing spam distribution",
                    7.5, "High", port.port_number, "smtp"
                )
                score += 20

        return score

    def _check_rdp_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check RDP service for vulnerabilities"""
        score = 0.0

        # RDP exposed to internet is high risk
        self._create_security_finding(
            assessment, host.id, "exposed_service",
            "RDP Service Exposed",
            "Remote Desktop Protocol exposed increases brute force attack risk",
            "High", 7.0, f"Port {port.port_number}/tcp"
        )
        score += 15

        # Check for BlueKeep vulnerability (older Windows versions)
        if version and any(v in version.lower() for v in ['windows 7', 'windows server 2008', 'windows xp']):
            self._create_vulnerability(
                assessment, host.id, "CVE-2019-0708", "BlueKeep RDP Vulnerability",
                "RDP service vulnerable to BlueKeep remote code execution",
                9.8, "Critical", port.port_number, "rdp"
            )
            score += 35

        return score

    def _check_mysql_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check MySQL service for vulnerabilities"""
        score = 0.0

        # MySQL exposed externally
        self._create_security_finding(
            assessment, host.id, "exposed_database",
            "MySQL Database Exposed",
            "MySQL database service exposed to network increases attack surface",
            "Medium", 6.0, f"Port {port.port_number}/tcp"
        )
        score += 10

        # Check for common MySQL vulnerabilities based on version
        mysql_scripts = [s for s in port.scripts if 'mysql' in s.script_id.lower()]
        for script in mysql_scripts:
            if 'root' in script.output.lower() and 'empty' in script.output.lower():
                self._create_vulnerability(
                    assessment, host.id, "CWE-259", "MySQL Empty Root Password",
                    "MySQL root account has empty password",
                    9.1, "Critical", port.port_number, "mysql"
                )
                score += 30

        return score

    def _check_mssql_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check MSSQL service for vulnerabilities"""
        score = 0.0

        # MSSQL exposed externally
        self._create_security_finding(
            assessment, host.id, "exposed_database",
            "MSSQL Database Exposed",
            "MSSQL database service exposed to network increases attack surface",
            "Medium", 6.0, f"Port {port.port_number}/tcp"
        )
        score += 10

        # Check for common MSSQL vulnerabilities
        mssql_scripts = [s for s in port.scripts if 'mssql' in s.script_id.lower()]
        for script in mssql_scripts:
            if 'sa' in script.output.lower() and ('empty' in script.output.lower() or 'blank' in script.output.lower()):
                self._create_vulnerability(
                    assessment, host.id, "CWE-259", "MSSQL Empty SA Password",
                    "MSSQL sa account has empty password",
                    9.1, "Critical", port.port_number, "mssql"
                )
                score += 30

        return score

    def _assess_configuration(self, host: Host, assessment: HostRiskAssessment) -> float:
        """Assess host configuration security"""
        config_score = 0.0

        # Check for insecure services
        insecure_services = ['telnet', 'ftp', 'rsh', 'rlogin', 'tftp']
        for port in host.ports:
            if port.state == 'open' and port.service_name:
                if port.service_name.lower() in insecure_services:
                    self._create_security_finding(
                        assessment, host.id, "insecure_service",
                        f"Insecure {port.service_name.upper()} Service",
                        f"{port.service_name.upper()} service is inherently insecure",
                        "High", 7.0, f"Port {port.port_number}/{port.protocol}"
                    )
                    config_score += 15

        # Check for default credentials
        self._check_default_credentials(host, assessment)

        return min(100.0, config_score)

    def _check_database_vulnerabilities(self, host: Host, port: Port, assessment: HostRiskAssessment, version: str, product: str) -> float:
        """Check vulnerabilities using the local vulnerability database"""
        score = 0.0

        if not product:
            return score

        try:
            # Search for vulnerabilities affecting this product
            vulns = self.vuln_db_service.search_vulnerabilities_by_product(product, version)

            for vuln in vulns:
                # Create vulnerability record
                self._create_vulnerability(
                    assessment, host.id, vuln.cve_id, vuln.title,
                    vuln.description or f"Vulnerability in {product}",
                    vuln.cvss_v3_score or 5.0, vuln.severity or "Medium",
                    port.port_number, product.lower()
                )

                # Add to risk score based on severity
                severity_scores = {
                    'Critical': 25,
                    'High': 15,
                    'Medium': 8,
                    'Low': 3
                }
                score += severity_scores.get(vuln.severity, 5)

                logger.info(f"Found vulnerability {vuln.cve_id} for {product} on host {host.ip_address}")

        except Exception as e:
            logger.error(f"Error checking database vulnerabilities for {product}: {str(e)}")

        return score

    def _assess_exposure(self, host: Host, assessment: HostRiskAssessment) -> float:
        """Assess network exposure and dangerous services"""
        exposure_score = 0.0
        open_ports = [p for p in host.ports if p.state == 'open']

        assessment.exposed_services = len(open_ports)
        dangerous_count = 0

        for port in open_ports:
            # Check if port is considered dangerous
            if port.port_number in self.dangerous_ports:
                dangerous_count += 1
                service_name = self.dangerous_ports[port.port_number]

                self._create_security_finding(
                    assessment, host.id, "dangerous_service",
                    f"Dangerous {service_name} Service Exposed",
                    f"{service_name} service on port {port.port_number} increases attack surface",
                    "Medium", 5.0, f"Port {port.port_number}/{port.protocol}"
                )
                exposure_score += 8

        assessment.dangerous_ports = dangerous_count

        # High port count increases exposure
        if len(open_ports) > 10:
            exposure_score += 10
        elif len(open_ports) > 20:
            exposure_score += 20

        return min(100.0, exposure_score)

    def _assess_attack_surface(self, host: Host, assessment: HostRiskAssessment) -> float:
        """Calculate attack surface based on exposed services"""
        attack_surface = 0.0

        # Weight services by risk level
        service_weights = {
            'http': 5, 'https': 3, 'ssh': 4, 'telnet': 10, 'ftp': 8,
            'smtp': 6, 'smb': 9, 'rdp': 9, 'snmp': 7, 'dns': 4,
            'mysql': 7, 'mssql': 7, 'postgresql': 6, 'mongodb': 6,
            'redis': 8, 'vnc': 9, 'nfs': 7
        }

        for port in host.ports:
            if port.state == 'open' and port.service_name:
                service = port.service_name.lower()
                weight = service_weights.get(service, 3)  # Default weight
                attack_surface += weight

        return min(100.0, attack_surface)

    def _generate_risk_summary(self, assessment: HostRiskAssessment) -> str:
        """Generate human-readable risk summary"""
        summaries = []

        if assessment.critical_vulnerabilities > 0:
            summaries.append(f"{assessment.critical_vulnerabilities} critical vulnerabilities require immediate attention")

        if assessment.dangerous_ports > 0:
            summaries.append(f"{assessment.dangerous_ports} dangerous services exposed")

        if assessment.vulnerability_count > 0:
            summaries.append(f"Total of {assessment.vulnerability_count} security issues identified")

        if assessment.exposed_services > 10:
            summaries.append(f"High attack surface with {assessment.exposed_services} exposed services")

        if not summaries:
            return "Host appears to have a good security posture with no major issues identified"

        return ". ".join(summaries) + "."

    def _calculate_risk_level(self, score: float) -> str:
        """Calculate risk level from numeric score"""
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "medium"
        elif score >= 20:
            return "low"
        else:
            return "info"

    def _create_vulnerability(self, assessment: HostRiskAssessment, host_id: int,
                            cve_id: str, title: str, description: str,
                            cvss_score: float, severity: str, port_number: int = None,
                            service_name: str = None) -> HostVulnerability:
        """Create a vulnerability record"""
        vuln = HostVulnerability(
            risk_assessment_id=assessment.id,
            host_id=host_id,
            cve_id=cve_id,
            title=title,
            description=description,
            cvss_score=cvss_score,
            severity=severity,
            port_number=port_number,
            service_name=service_name,
            exploitability="Unknown"
        )

        self.db.add(vuln)
        return vuln

    def _create_security_finding(self, assessment: HostRiskAssessment, host_id: int,
                               finding_type: str, title: str, description: str,
                               severity: str, risk_score: float, evidence: str = None) -> SecurityFinding:
        """Create a security finding record"""
        finding = SecurityFinding(
            risk_assessment_id=assessment.id,
            host_id=host_id,
            finding_type=finding_type,
            title=title,
            description=description,
            severity=severity,
            risk_score=risk_score,
            evidence=evidence
        )

        self.db.add(finding)
        return finding

    def _generate_recommendations(self, assessment: HostRiskAssessment):
        """Generate security recommendations"""
        recommendations = []

        if assessment.critical_vulnerabilities > 0:
            recommendations.append("Immediately patch critical vulnerabilities")

        if assessment.dangerous_ports > 0:
            recommendations.append("Review and disable unnecessary dangerous services")

        if assessment.exposure_risk_score > 50:
            recommendations.append("Implement network segmentation to reduce exposure")

        if assessment.configuration_risk_score > 40:
            recommendations.append("Harden service configurations and disable insecure protocols")

        # Create recommendation records
        for i, rec_text in enumerate(recommendations):
            rec = RiskRecommendation(
                risk_assessment_id=assessment.id,
                title=f"Recommendation {i+1}",
                description=rec_text,
                priority="High" if i < 2 else "Medium",
                category="security"
            )
            self.db.add(rec)

    # Helper methods for version checking
    def _check_apache_version(self, version: str) -> List[Dict]:
        """Check Apache version for known vulnerabilities"""
        vulns = []
        if version and '2.4' in version:
            version_num = re.search(r'2\.4\.(\d+)', version)
            if version_num and int(version_num.group(1)) < 41:
                vulns.append({
                    'cve_id': 'CVE-2019-0197',
                    'title': 'Apache HTTP Server mod_http2 Vulnerability',
                    'description': 'HTTP/2 DoS vulnerability in Apache HTTP Server',
                    'cvss_score': 7.5,
                    'severity': 'High',
                    'port_number': 80,
                    'service_name': 'http'
                })
        return vulns

    def _check_nginx_version(self, version: str) -> List[Dict]:
        """Check Nginx version for known vulnerabilities"""
        vulns = []
        if version:
            version_match = re.search(r'(\d+\.\d+\.\d+)', version)
            if version_match:
                ver_parts = version_match.group(1).split('.')
                if len(ver_parts) >= 2 and int(ver_parts[0]) == 1 and int(ver_parts[1]) < 18:
                    vulns.append({
                        'cve_id': 'CVE-2019-20372',
                        'title': 'Nginx HTTP Request Smuggling',
                        'description': 'HTTP request smuggling vulnerability in Nginx',
                        'cvss_score': 5.3,
                        'severity': 'Medium',
                        'port_number': 80,
                        'service_name': 'http'
                    })
        return vulns

    def _check_default_credentials(self, host: Host, assessment: HostRiskAssessment):
        """Check for services that might have default credentials"""
        default_cred_services = ['ftp', 'telnet', 'ssh', 'http', 'https', 'snmp']

        for port in host.ports:
            if port.state == 'open' and port.service_name:
                if port.service_name.lower() in default_cred_services:
                    # Check if any scripts indicate default credentials
                    for script in port.scripts:
                        if any(keyword in script.output.lower() for keyword in ['default', 'admin', 'password']):
                            self._create_security_finding(
                                assessment, host.id, "weak_credentials",
                                f"Potential Default Credentials on {port.service_name.upper()}",
                                f"Service may be using default or weak credentials",
                                "High", 8.0, script.output[:200]
                            )

    def get_risk_summary(self, project_id: int = None) -> Dict[str, Any]:
        """Get overall risk summary for dashboard, optionally scoped to a project."""
        host_query = self.db.query(func.count(Host.id))
        if project_id is not None:
            host_query = host_query.filter(Host.project_id == project_id)
        total_hosts = host_query.scalar() or 0

        # Get hosts with recent assessments (last 7 days)
        assessment_query = self.db.query(HostRiskAssessment).join(Host).filter(
            HostRiskAssessment.assessment_date > datetime.utcnow() - timedelta(days=7)
        )
        if project_id is not None:
            assessment_query = assessment_query.filter(Host.project_id == project_id)
        recent_assessments = assessment_query.all()

        assessed_hosts = len(set(a.host_id for a in recent_assessments))
        unassessed_hosts = total_hosts - assessed_hosts

        # Risk distribution
        risk_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for assessment in recent_assessments:
            risk_counts[assessment.risk_level] += 1

        # Calculate percentages
        risk_percentages = {}
        for level, count in risk_counts.items():
            risk_percentages[level] = round((count / max(assessed_hosts, 1)) * 100, 1)

        # Get top risk hosts
        top_risk_hosts = []
        top_query = self.db.query(HostRiskAssessment).join(Host).options(
            joinedload(HostRiskAssessment.host)
        ).filter(
            HostRiskAssessment.risk_score > 60
        )
        if project_id is not None:
            top_query = top_query.filter(Host.project_id == project_id)
        high_risk_assessments = top_query.order_by(desc(HostRiskAssessment.risk_score)).limit(5).all()

        for assessment in high_risk_assessments:
            top_risk_hosts.append({
                'host_id': assessment.host_id,
                'ip_address': assessment.host.ip_address,
                'hostname': assessment.host.hostname,
                'risk_score': assessment.risk_score,
                'risk_level': assessment.risk_level,
                'vulnerability_count': assessment.vulnerability_count,
                'last_assessment': assessment.assessment_date.isoformat()
            })

        return {
            'total_hosts': total_hosts,
            'assessed_hosts': assessed_hosts,
            'unassessed_hosts': unassessed_hosts,
            'risk_distribution': risk_counts,
            'risk_percentages': risk_percentages,
            'top_risk_hosts': top_risk_hosts
        }

    def get_high_risk_hosts(self, limit: int = 10, min_risk_score: float = 70, project_id: int = None) -> List[Dict[str, Any]]:
        """Get high-risk hosts for critical findings dashboard, optionally scoped to a project."""
        query = self.db.query(HostRiskAssessment).join(Host).options(
            joinedload(HostRiskAssessment.host)
        ).filter(
            HostRiskAssessment.risk_score >= min_risk_score
        )
        if project_id is not None:
            query = query.filter(Host.project_id == project_id)
        high_risk_assessments = query.order_by(desc(HostRiskAssessment.risk_score)).limit(limit).all()

        result = []
        for assessment in high_risk_assessments:
            # Get top vulnerabilities
            top_vulns = self.db.query(HostVulnerability).filter(
                HostVulnerability.risk_assessment_id == assessment.id
            ).order_by(desc(HostVulnerability.cvss_score)).limit(3).all()

            # Get critical findings
            critical_findings = self.db.query(SecurityFinding).filter(
                and_(
                    SecurityFinding.risk_assessment_id == assessment.id,
                    SecurityFinding.severity.in_(['Critical', 'High'])
                )
            ).order_by(desc(SecurityFinding.risk_score)).limit(3).all()

            # Get recommendations
            recommendations = self.db.query(RiskRecommendation).filter(
                RiskRecommendation.risk_assessment_id == assessment.id
            ).limit(3).all()

            result.append({
                'host_id': assessment.host_id,
                'ip_address': assessment.host.ip_address,
                'hostname': assessment.host.hostname,
                'os_name': assessment.host.os_name,
                'risk_score': assessment.risk_score,
                'risk_level': assessment.risk_level,
                'vulnerability_count': assessment.vulnerability_count,
                'critical_vulnerabilities': assessment.critical_vulnerabilities,
                'high_vulnerabilities': assessment.high_vulnerabilities,
                'risk_summary': assessment.risk_summary,
                'top_vulnerabilities': [
                    {
                        'cve_id': v.cve_id,
                        'title': v.title,
                        'severity': v.severity,
                        'cvss_score': v.cvss_score,
                        'exploitability': v.exploitability
                    } for v in top_vulns
                ],
                'critical_findings': [
                    {
                        'finding_type': f.finding_type,
                        'title': f.title,
                        'severity': f.severity,
                        'risk_score': f.risk_score
                    } for f in critical_findings
                ],
                'recommendations': [r.description for r in recommendations]
            })

        return result