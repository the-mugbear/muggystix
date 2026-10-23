from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.schemas import DashboardStats, VulnerabilityStats
from app.services.vulnerability_service import VulnerabilityService
# v2.244.0 — the personal-work routes (my-tasks / my-attention / team-review /
# new-scans-since) were removed: GET /operations batches all four from the same
# operations_read_service functions and is what the Operations page calls.
# Review 2026-10-22 (A-R7) — the rest of the retired dashboard went too: the
# project totals (a count over every port row), recent scans, subnet
# utilisation and note activity.  Nothing rendered them; the one reader, the
# Posture page's "Scanner observations and scope", reads the scanner
# observation counts and nothing else.
from app.api.deps import get_current_user
from app.api.deps import get_current_project
from app.db.models_project import Project

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get(
    "/stats",
    response_model=DashboardStats,
    summary="Scanner observations by severity, and the hosts carrying each",
)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    # The one section left is the whole answer, so a failure fails the
    # request (a cancelled statement is ``get_db``'s 503): the page then says
    # "could not be counted".  A null section read as "no observation is
    # recorded" — a clean project — before.
    vuln_data = VulnerabilityService(db).get_dashboard_statistics(project_id=project.id)
    by_severity = vuln_data['severity_breakdown']
    return DashboardStats(
        vulnerability_stats=VulnerabilityStats(
            total_vulnerabilities=vuln_data['total_vulnerabilities'],
            critical=by_severity.get('critical', 0),
            high=by_severity.get('high', 0),
            medium=by_severity.get('medium', 0),
            low=by_severity.get('low', 0),
            info=by_severity.get('info', 0),
            hosts_with_vulnerabilities=vuln_data['hosts_with_vulnerabilities'],
            hosts_by_severity=vuln_data['hosts_by_severity'],
        ),
    )
