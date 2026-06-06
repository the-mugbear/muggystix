from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.db.session import get_db
from app.db import models
from app.schemas.schemas import DNSRecord
from app.services.dns_service import DNSService
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.db.models_auth import UserRole
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole

router = APIRouter(dependencies=[Depends(get_current_user)])


class DNSLookupResponse(BaseModel):
    hostname: str
    records: list
    message: str


@router.get(
    "/records",
    response_model=List[DNSRecord],
    summary="Get stored DNS records",
)
def get_dns_records(
    hostname: str = Query(..., description="Hostname to get DNS records for", examples=["example.com"]),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get stored DNS records for a hostname."""
    dns_service = DNSService(db, project_id=project.id)
    records = dns_service.get_stored_dns_records(hostname)
    return records


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
    """Attempt DNS zone transfer for a domain. Requires analyst role."""
    dns_service = DNSService(db, project_id=project.id)

    result = dns_service.attempt_zone_transfer(domain)

    return result
