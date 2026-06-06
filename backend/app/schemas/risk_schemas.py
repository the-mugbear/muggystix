"""
Risk Assessment Pydantic Schemas

Data models for risk assessment API endpoints.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict
from datetime import datetime


# ------------------------------------------------------------------ #
#  Vulnerability schemas                                              #
# ------------------------------------------------------------------ #

class VulnerabilityBase(BaseModel):
    cve_id: str
    title: str
    description: Optional[str] = None
    cvss_score: Optional[float] = None
    severity: str
    exploitability: Optional[str] = None
    affected_software: Optional[str] = None
    affected_version: Optional[str] = None
    port_number: Optional[int] = None
    service_name: Optional[str] = None
    patch_available: bool = False
    patch_url: Optional[str] = None


class VulnerabilityResponse(VulnerabilityBase):
    id: int
    discovery_date: datetime
    source: str

    model_config = ConfigDict(from_attributes=True)


class VulnerabilityDetail(BaseModel):
    """Vulnerability detail as returned inside risk assessment responses."""
    cve_id: str
    title: str
    description: Optional[str] = None
    cvss_score: Optional[float] = None
    severity: str
    exploitability: Optional[str] = None
    affected_software: Optional[str] = None
    patch_available: bool = False
    patch_url: Optional[str] = None


# ------------------------------------------------------------------ #
#  Security finding schemas                                           #
# ------------------------------------------------------------------ #

class SecurityFindingBase(BaseModel):
    finding_type: str
    title: str
    description: str
    severity: str
    risk_score: float
    evidence: Optional[str] = None
    affected_component: Optional[str] = None
    recommendation: Optional[str] = None


class SecurityFindingResponse(SecurityFindingBase):
    id: int
    discovery_date: datetime
    source: str

    model_config = ConfigDict(from_attributes=True)


class SecurityFindingDetail(BaseModel):
    """Security finding as returned inside risk assessment responses."""
    finding_type: str
    title: str
    description: str
    severity: str
    risk_score: float
    evidence: Optional[str] = None
    recommendation: Optional[str] = None


# ------------------------------------------------------------------ #
#  Risk assessment schemas                                            #
# ------------------------------------------------------------------ #

class HostRiskAssessmentBase(BaseModel):
    risk_score: float = Field(..., ge=0, le=100)
    risk_level: str
    vulnerability_count: int = 0
    critical_vulnerabilities: int = 0
    high_vulnerabilities: int = 0
    medium_vulnerabilities: int = 0
    low_vulnerabilities: int = 0
    exposed_services: int = 0
    dangerous_ports: int = 0
    attack_surface_score: float = 0.0
    patch_urgency_score: float = 0.0
    exposure_risk_score: float = 0.0
    configuration_risk_score: float = 0.0
    risk_summary: Optional[str] = None


class HostRiskAssessmentResponse(HostRiskAssessmentBase):
    id: int
    host_id: int
    assessment_date: datetime
    last_updated: datetime

    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ #
#  Composite endpoint response schemas                                #
# ------------------------------------------------------------------ #

class EmptyStateInfo(BaseModel):
    """Describes what to show in the UI when no data is available."""
    type: str = Field(..., description="One of: no_hosts, no_assessments, no_high_risk")
    title: str
    message: str
    action_text: Optional[str] = None
    action_url: Optional[str] = None
    is_positive: Optional[bool] = None


class RiskAssessmentDetail(BaseModel):
    """Inline risk assessment detail (iso-datetime strings)."""
    risk_score: float
    risk_level: str
    vulnerability_count: int = 0
    critical_vulnerabilities: int = 0
    high_vulnerabilities: int = 0
    exposed_services: int = 0
    dangerous_ports: int = 0
    attack_surface_score: float = 0.0
    patch_urgency_score: float = 0.0
    exposure_risk_score: float = 0.0
    configuration_risk_score: float = 0.0
    risk_summary: Optional[str] = None
    assessment_date: str
    last_updated: str


class HostSummary(BaseModel):
    """Minimal host info embedded in risk responses."""
    id: int
    ip_address: str
    hostname: Optional[str] = None
    os_name: Optional[str] = None
    os_family: Optional[str] = None
    state: Optional[str] = None


class SummaryStats(BaseModel):
    total_vulnerabilities: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    total_findings: int = 0
    critical_findings: int = 0
    high_findings: int = 0


class HostRiskAnalysisResponse(BaseModel):
    """Complete host risk analysis response for GET /hosts/{id}/risk-assessment."""
    host: HostSummary
    risk_assessment: RiskAssessmentDetail
    vulnerabilities: Dict[str, List[VulnerabilityDetail]]
    security_findings: Dict[str, List[SecurityFindingDetail]]
    recommendations: List[str]
    summary_stats: SummaryStats


class RiskSummaryResponse(BaseModel):
    """Dashboard risk summary for GET /hosts/risk-summary."""
    total_hosts: Optional[int] = 0
    assessed_hosts: Optional[int] = 0
    unassessed_hosts: Optional[int] = 0
    risk_distribution: Optional[Dict[str, int]] = None
    risk_percentages: Optional[Dict[str, float]] = None
    top_risk_hosts: Optional[list] = None
    has_data: bool = False
    empty_state: EmptyStateInfo


class HighRiskHostsResponse(BaseModel):
    """Response for GET /hosts/high-risk."""
    hosts: list
    has_data: bool = False
    total_high_risk: int = 0
    empty_state: EmptyStateInfo


class AssessRiskResponse(BaseModel):
    """Response for POST /hosts/{id}/assess-risk."""
    message: str
    host_id: int
    ip_address: str
    risk_score: float
    risk_level: str
    vulnerability_count: int
    assessment_date: str


class DeleteRiskAssessmentResponse(BaseModel):
    """Response for DELETE /hosts/{id}/risk-assessment."""
    message: str
    host_id: int


class VulnerabilityStatsResponse(BaseModel):
    """Response for GET /vulnerability-stats."""
    vulnerability_distribution: Dict[str, int]
    hosts_with_vulnerabilities: int = 0
    top_cves: list


class RiskAssessmentRequest(BaseModel):
    """Request to trigger risk assessment"""
    include_vulnerability_scan: bool = True
    include_configuration_analysis: bool = True
    force_refresh: bool = False
