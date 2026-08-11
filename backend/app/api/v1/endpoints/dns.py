from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.db.session import get_db
from app.services.dns_service import DNSService
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole

router = APIRouter(dependencies=[Depends(get_current_user)])


class DNSLookupResponse(BaseModel):
    hostname: str
    records: list
    message: str


@router.post(
    "/lookup/{hostname}",
    response_model=DNSLookupResponse,
    summary="Perform DNS lookup",
)
def perform_dns_lookup(
    hostname: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Perform DNS lookup for a hostname and store results."""
    dns_service = DNSService(db, project_id=project.id)

    # Get various DNS records
    dns_records = dns_service.get_dns_records(hostname)

    return {
        "hostname": hostname,
        "records": dns_records,
        "message": f"DNS lookup completed for {hostname}"
    }


@router.post(
    "/zone-transfer/{domain}",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions — analyst role required"},
    },
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Attempt zone transfer (analyst)",
)
def attempt_zone_transfer(
    domain: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Attempt DNS zone transfer for a domain. Requires analyst role.

    **Deliberately not surfaced in the UI** (v2.243.0) and not an orphan:
    AXFR is aimed at the target's authoritative nameserver, reads as active
    recon in their logs, and this handler accepts an arbitrary domain with no
    scope check or rate limit. It also originates from the BlueStick *server*,
    so the source address is the server's rather than the operator's — which
    may sit outside the engagement's authorised range. Kept as API-only
    surface for deliberate operator use; putting a button on it would need a
    scope check, an audit entry, and a throttle first.
    """
    dns_service = DNSService(db, project_id=project.id)

    result = dns_service.attempt_zone_transfer(domain)

    return result
