"""
Risk Assessment Database Models

Models for storing security risk assessments, vulnerabilities, and findings.
"""

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.models import Base

class HostRiskAssessment(Base):
    """Host-level risk assessment results"""
    __tablename__ = "host_risk_assessments"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False, index=True)

    # Risk metrics
    risk_score = Column(Float, nullable=False)  # 0-100
    risk_level = Column(String(20), nullable=False)  # critical, high, medium, low

    # Vulnerability counts
    vulnerability_count = Column(Integer, default=0)
    critical_vulnerabilities = Column(Integer, default=0)
    high_vulnerabilities = Column(Integer, default=0)
    medium_vulnerabilities = Column(Integer, default=0)
    low_vulnerabilities = Column(Integer, default=0)

    # Risk component scores
    exposed_services = Column(Integer, default=0)
    dangerous_ports = Column(Integer, default=0)
    attack_surface_score = Column(Float, default=0.0)
    patch_urgency_score = Column(Float, default=0.0)
    exposure_risk_score = Column(Float, default=0.0)
    configuration_risk_score = Column(Float, default=0.0)

    # Assessment metadata
    risk_summary = Column(Text)
    assessment_date = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    host = relationship("Host")
    vulnerabilities = relationship("HostVulnerability", back_populates="risk_assessment")
    security_findings = relationship("SecurityFinding", back_populates="risk_assessment")


class HostVulnerability(Base):
    """Individual vulnerabilities found on hosts"""
    __tablename__ = "host_vulnerabilities"

    id = Column(Integer, primary_key=True, index=True)
    risk_assessment_id = Column(Integer, ForeignKey("host_risk_assessments.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False, index=True)

    # CVE Information
    cve_id = Column(String(20), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)

    # Risk metrics
    cvss_score = Column(Float)
    cvss_vector = Column(String(200))
    severity = Column(String(20), nullable=False)  # Critical, High, Medium, Low
    exploitability = Column(String(50))  # High, Medium, Low, Unknown

    # Affected software/service
    affected_software = Column(String(200))
    affected_version = Column(String(100))
    port_number = Column(Integer)
    service_name = Column(String(100))

    # Patch information
    patch_available = Column(Boolean, default=False)
    patch_url = Column(String(500))
    patch_priority = Column(String(20))  # Critical, High, Medium, Low

    # Discovery metadata
    discovery_date = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50), default="nmap")  # nmap, nessus, manual

    # Relationships
    risk_assessment = relationship("HostRiskAssessment", back_populates="vulnerabilities")
    host = relationship("Host")


class SecurityFinding(Base):
    """Security configuration and policy findings"""
    __tablename__ = "security_findings"

    id = Column(Integer, primary_key=True, index=True)
    risk_assessment_id = Column(Integer, ForeignKey("host_risk_assessments.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts_v2.id"), nullable=False, index=True)

    # Finding details
    finding_type = Column(String(100), nullable=False)  # weak_config, exposed_service, etc.
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)

    # Risk assessment
    severity = Column(String(20), nullable=False)  # Critical, High, Medium, Low
    risk_score = Column(Float, nullable=False)  # 0-10

    # Evidence and context
    evidence = Column(Text)  # Technical details
    affected_component = Column(String(200))  # Service, port, configuration

    # Remediation
    recommendation = Column(Text)
    remediation_effort = Column(String(20))  # Low, Medium, High

    # Discovery metadata
    discovery_date = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50), default="automated")

    # Relationships
    risk_assessment = relationship("HostRiskAssessment", back_populates="security_findings")
    host = relationship("Host")


class VulnerabilityDatabase(Base):
    """CVE vulnerability database for reference"""
    __tablename__ = "vulnerability_database"

    id = Column(Integer, primary_key=True, index=True)
    cve_id = Column(String(20), unique=True, nullable=False, index=True)

    # Basic information
    title = Column(String(500), nullable=False)
    description = Column(Text)
    published_date = Column(DateTime)
    modified_date = Column(DateTime)

    # CVSS metrics
    cvss_v3_score = Column(Float)
    cvss_v3_vector = Column(String(200))
    cvss_v2_score = Column(Float)
    cvss_v2_vector = Column(String(200))

    # Classification
    severity = Column(String(20))  # Critical, High, Medium, Low
    attack_vector = Column(String(50))  # Network, Adjacent, Local, Physical
    attack_complexity = Column(String(20))  # Low, High
    exploitability_score = Column(Float)
    impact_score = Column(Float)

    # Affected products (JSON array)
    affected_products = Column(JSON)

    # References
    references = Column(JSON)  # Array of reference URLs

    # Update tracking
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RiskRecommendation(Base):
    """Security recommendations for risk mitigation"""
    __tablename__ = "risk_recommendations"

    id = Column(Integer, primary_key=True, index=True)
    risk_assessment_id = Column(Integer, ForeignKey("host_risk_assessments.id"), nullable=False)

    # Recommendation details
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(String(20), nullable=False)  # Critical, High, Medium, Low
    category = Column(String(50), nullable=False)  # patching, configuration, monitoring

    # Implementation details
    implementation_effort = Column(String(20))  # Low, Medium, High
    estimated_risk_reduction = Column(Float)  # 0-100 percentage

    # Status tracking
    status = Column(String(20), default="open")  # open, in_progress, completed, dismissed
    created_date = Column(DateTime, default=datetime.utcnow)

    # Relationships
    risk_assessment = relationship("HostRiskAssessment")