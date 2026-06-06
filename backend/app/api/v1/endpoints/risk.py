"""
Risk Assessment API Endpoints

Provides endpoints for security risk analysis and vulnerability management.
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session import get_db
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.db.models_auth import User, UserRole
from app.db.models import Host
from app.db.models_risk import HostRiskAssessment, HostVulnerability, SecurityFinding
from app.schemas.risk_schemas import (
    RiskSummaryResponse,
    HighRiskHostsResponse,
    HostRiskAnalysisResponse,
    AssessRiskResponse,
    DeleteRiskAssessmentResponse,
    VulnerabilityStatsResponse,
    RiskAssessmentRequest,
    VulnerabilityResponse,
    SecurityFindingResponse,
)
from app.services.risk_assessment_service import RiskAssessmentService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

_AUTH_RESPONSES = {
    401: {"description": "Not authenticated"},
}

_ADMIN_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions — admin role required"},
}


@router.get(
    "/hosts/risk-summary",
    response_model=RiskSummaryResponse,
    responses=_AUTH_RESPONSES,
    summary="Overall risk summary",
)
def get_risk_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get overall risk summary for dashboard. Returns assessed/unassessed host
    counts, risk distribution, and top-risk hosts."""
    try:
        risk_service = RiskAssessmentService(db)
        summary = risk_service.get_risk_summary(project_id=project.id)

        # Add empty state metadata
        summary['has_data'] = summary.get('assessed_hosts', 0) > 0
        summary['empty_state'] = {
            'type': 'no_assessments' if summary.get('total_hosts', 0) > 0 else 'no_hosts',
            'title': 'No Risk Assessments Available' if summary.get('total_hosts', 0) > 0 else 'No Hosts Discovered',
            'message': f'Run security assessments on hosts directly from the host details page to view risk analysis' if summary.get('total_hosts', 0) > 0 else 'Upload network scan files to discover hosts before running risk assessments',
            'action_text': None,
            'action_url': '/upload' if summary.get('total_hosts', 0) == 0 else None
        }

        return summary
    except Exception as e:
        logger.error(f"Error fetching risk summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch risk summary")


@router.get(
    "/hosts/high-risk",
    response_model=HighRiskHostsResponse,
    responses=_AUTH_RESPONSES,
    summary="High-risk hosts",
)
def get_high_risk_hosts(
    limit: int = Query(10, ge=1, le=50, description="Max hosts to return"),
    min_risk_score: float = Query(70.0, ge=0.0, le=100.0, description="Minimum risk score threshold"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get hosts with risk score at or above the threshold. Used for the
    critical-findings dashboard widget."""
    try:
        risk_service = RiskAssessmentService(db)
        high_risk_hosts = risk_service.get_high_risk_hosts(limit, min_risk_score, project_id=project.id)

        # Get total host count for empty state
        total_hosts = db.query(func.count(Host.id)).filter(Host.project_id == project.id).scalar() or 0
        total_assessments = db.query(func.count(HostRiskAssessment.id)).join(
            Host, HostRiskAssessment.host_id == Host.id
        ).filter(Host.project_id == project.id).scalar() or 0

        # Structure response with empty state metadata
        response = {
            'hosts': high_risk_hosts,
            'has_data': len(high_risk_hosts) > 0,
            'total_high_risk': len(high_risk_hosts),
            'empty_state': {
                'type': 'no_high_risk' if total_assessments > 0 else ('no_assessments' if total_hosts > 0 else 'no_hosts'),
                'title': 'No Critical Security Findings' if total_assessments > 0 else ('No Risk Assessments Available' if total_hosts > 0 else 'No Hosts Discovered'),
                'message': f'Great news! No hosts currently have critical security issues (risk score ≥{min_risk_score})' if total_assessments > 0 else (f'Review hosts individually to trigger security assessments and identify risks' if total_hosts > 0 else 'Upload network scan files to discover hosts before running security assessments'),
                'action_text': 'View All Hosts' if total_assessments > 0 else (None if total_hosts > 0 else 'Upload Scan'),
                'action_url': '/hosts' if total_assessments > 0 else (None if total_hosts > 0 else '/upload'),
                'is_positive': total_assessments > 0 and len(high_risk_hosts) == 0
            }
        }

        return response
    except Exception as e:
        logger.error(f"Error fetching high-risk hosts: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch high-risk hosts")


@router.get(
    "/hosts/{host_id}/risk-assessment",
    response_model=HostRiskAnalysisResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Host or assessment not found"}},
    summary="Get host risk assessment",
)
def get_host_risk_assessment(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get complete risk assessment for a specific host including
    vulnerabilities grouped by severity, security findings, and recommendations."""
    # Verify host exists and belongs to project
    host = db.query(Host).filter(Host.id == host_id, Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Get the latest risk assessment
    assessment = db.query(HostRiskAssessment).filter(
        HostRiskAssessment.host_id == host_id
    ).order_by(HostRiskAssessment.assessment_date.desc()).first()

    if not assessment:
        raise HTTPException(status_code=404, detail="No risk assessment found for this host")

    try:
        # Get vulnerabilities grouped by severity
        vulnerabilities = db.query(HostVulnerability).filter(
            HostVulnerability.risk_assessment_id == assessment.id
        ).all()

        vuln_by_severity = {
            'Critical': [],
            'High': [],
            'Medium': [],
            'Low': []
        }

        for vuln in vulnerabilities:
            if vuln.severity in vuln_by_severity:
                vuln_data = {
                    'cve_id': vuln.cve_id,
                    'title': vuln.title,
                    'description': vuln.description,
                    'cvss_score': vuln.cvss_score,
                    'severity': vuln.severity,
                    'exploitability': vuln.exploitability,
                    'affected_software': vuln.affected_software,
                    'patch_available': vuln.patch_available,
                    'patch_url': vuln.patch_url
                }
                vuln_by_severity[vuln.severity].append(vuln_data)

        # Get security findings grouped by severity
        findings = db.query(SecurityFinding).filter(
            SecurityFinding.risk_assessment_id == assessment.id
        ).all()

        findings_by_severity = {
            'Critical': [],
            'High': [],
            'Medium': [],
            'Low': []
        }

        for finding in findings:
            if finding.severity in findings_by_severity:
                finding_data = {
                    'finding_type': finding.finding_type,
                    'title': finding.title,
                    'description': finding.description,
                    'severity': finding.severity,
                    'risk_score': finding.risk_score,
                    'evidence': finding.evidence,
                    'recommendation': finding.recommendation
                }
                findings_by_severity[finding.severity].append(finding_data)

        # Get recommendations
        from app.db.models_risk import RiskRecommendation
        recommendations = db.query(RiskRecommendation).filter(
            RiskRecommendation.risk_assessment_id == assessment.id
        ).all()

        # Build response
        response = {
            'host': {
                'id': host.id,
                'ip_address': host.ip_address,
                'hostname': host.hostname,
                'os_name': host.os_name,
                'os_family': host.os_family,
                'state': host.state
            },
            'risk_assessment': {
                'risk_score': assessment.risk_score,
                'risk_level': assessment.risk_level,
                'vulnerability_count': assessment.vulnerability_count,
                'critical_vulnerabilities': assessment.critical_vulnerabilities,
                'high_vulnerabilities': assessment.high_vulnerabilities,
                'exposed_services': assessment.exposed_services,
                'dangerous_ports': assessment.dangerous_ports,
                'attack_surface_score': assessment.attack_surface_score,
                'patch_urgency_score': assessment.patch_urgency_score,
                'exposure_risk_score': assessment.exposure_risk_score,
                'configuration_risk_score': assessment.configuration_risk_score,
                'risk_summary': assessment.risk_summary,
                'assessment_date': assessment.assessment_date.isoformat(),
                'last_updated': assessment.last_updated.isoformat()
            },
            'vulnerabilities': vuln_by_severity,
            'security_findings': findings_by_severity,
            'recommendations': [rec.description for rec in recommendations],
            'summary_stats': {
                'total_vulnerabilities': assessment.vulnerability_count,
                'critical_count': assessment.critical_vulnerabilities,
                'high_count': assessment.high_vulnerabilities,
                'medium_count': assessment.medium_vulnerabilities,
                'low_count': assessment.low_vulnerabilities,
                'total_findings': len(findings),
                'critical_findings': len(findings_by_severity['Critical']),
                'high_findings': len(findings_by_severity['High'])
            }
        }

        return response

    except Exception as e:
        logger.error(f"Error building risk assessment response for host {host_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to build risk assessment response")


@router.post(
    "/hosts/{host_id}/assess-risk",
    response_model=AssessRiskResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Host not found"}},
    summary="Trigger risk assessment",
)
def assess_host_risk(
    host_id: int,
    request: RiskAssessmentRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Trigger risk assessment for a specific host. Use force_refresh=true to
    recalculate even if a recent assessment exists."""
    # Verify host exists and belongs to project
    host = db.query(Host).filter(Host.id == host_id, Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    try:
        risk_service = RiskAssessmentService(db)

        force_refresh = request.force_refresh if request else False
        assessment = risk_service.assess_host_risk(host_id, force_refresh=force_refresh)

        logger.info(f"Risk assessment completed for host {host.ip_address}: {assessment.risk_level}")

        return {
            'message': 'Risk assessment completed successfully',
            'host_id': host_id,
            'ip_address': host.ip_address,
            'risk_score': assessment.risk_score,
            'risk_level': assessment.risk_level,
            'vulnerability_count': assessment.vulnerability_count,
            'assessment_date': assessment.assessment_date.isoformat()
        }

    except ValueError as e:
        logger.error(f"Risk assessment failed for host {host_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error during risk assessment for host {host_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Risk assessment failed")


@router.get(
    "/hosts/{host_id}/vulnerabilities",
    response_model=List[VulnerabilityResponse],
    responses={**_AUTH_RESPONSES, 404: {"description": "Host not found"}},
    summary="Get host vulnerabilities",
)
def get_host_vulnerabilities(
    host_id: int,
    severity: str = Query(None, pattern="^(Critical|High|Medium|Low)$", description="Filter by severity level"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get vulnerabilities for a specific host, optionally filtered by severity."""
    # Verify host exists and belongs to project
    host = db.query(Host).filter(Host.id == host_id, Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    query = db.query(HostVulnerability).filter(HostVulnerability.host_id == host_id)

    if severity:
        query = query.filter(HostVulnerability.severity == severity)

    vulnerabilities = query.order_by(HostVulnerability.cvss_score.desc()).all()

    return vulnerabilities


@router.get(
    "/hosts/{host_id}/security-findings",
    response_model=List[SecurityFindingResponse],
    responses={**_AUTH_RESPONSES, 404: {"description": "Host not found"}},
    summary="Get security findings",
)
def get_host_security_findings(
    host_id: int,
    severity: str = Query(None, pattern="^(Critical|High|Medium|Low)$", description="Filter by severity level"),
    finding_type: str = Query(None, description="Filter by finding type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get security findings for a specific host."""
    # Verify host exists and belongs to project
    host = db.query(Host).filter(Host.id == host_id, Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    query = db.query(SecurityFinding).filter(SecurityFinding.host_id == host_id)

    if severity:
        query = query.filter(SecurityFinding.severity == severity)

    if finding_type:
        query = query.filter(SecurityFinding.finding_type == finding_type)

    findings = query.order_by(SecurityFinding.risk_score.desc()).all()

    return findings


@router.get(
    "/vulnerability-stats",
    response_model=VulnerabilityStatsResponse,
    responses=_AUTH_RESPONSES,
    summary="Vulnerability statistics",
)
def get_vulnerability_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get overall vulnerability statistics including severity distribution,
    affected host count, and top CVEs."""
    try:
        from sqlalchemy import func

        # Count vulnerabilities by severity (scoped to project)
        vuln_stats = db.query(
            HostVulnerability.severity,
            func.count(HostVulnerability.id).label('count')
        ).join(
            Host, HostVulnerability.host_id == Host.id
        ).filter(Host.project_id == project.id).group_by(HostVulnerability.severity).all()

        # Count hosts with vulnerabilities
        hosts_with_vulns = db.query(func.count(func.distinct(HostVulnerability.host_id))).join(
            Host, HostVulnerability.host_id == Host.id
        ).filter(Host.project_id == project.id).scalar() or 0

        # Top CVEs
        top_cves = db.query(
            HostVulnerability.cve_id,
            HostVulnerability.title,
            func.count(HostVulnerability.id).label('affected_hosts'),
            func.avg(HostVulnerability.cvss_score).label('avg_cvss')
        ).join(
            Host, HostVulnerability.host_id == Host.id
        ).filter(Host.project_id == project.id).group_by(
            HostVulnerability.cve_id,
            HostVulnerability.title
        ).order_by(func.count(HostVulnerability.id).desc()).limit(10).all()

        return {
            'vulnerability_distribution': {stat.severity: stat.count for stat in vuln_stats},
            'hosts_with_vulnerabilities': hosts_with_vulns,
            'top_cves': [
                {
                    'cve_id': cve.cve_id,
                    'title': cve.title,
                    'affected_hosts': cve.affected_hosts,
                    'average_cvss': round(float(cve.avg_cvss or 0), 1)
                } for cve in top_cves
            ]
        }

    except Exception as e:
        logger.error(f"Error fetching vulnerability statistics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch vulnerability statistics")


@router.delete(
    "/hosts/{host_id}/risk-assessment",
    response_model=DeleteRiskAssessmentResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Host not found"}},
    summary="Delete risk assessment (admin)",
)
def delete_host_risk_assessment(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_role("admin")),
    project: Project = Depends(get_current_project),
):
    """Delete all risk assessments for a host. Requires admin role."""
    # Verify host exists and belongs to project
    host = db.query(Host).filter(Host.id == host_id, Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    try:
        # Delete all risk assessments for the host
        assessments_deleted = db.query(HostRiskAssessment).filter(
            HostRiskAssessment.host_id == host_id
        ).delete()

        db.commit()

        return {
            'message': f'Deleted {assessments_deleted} risk assessments for host {host.ip_address}',
            'host_id': host_id
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting risk assessments for host {host_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete risk assessments")
