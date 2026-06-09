"""Site management — set the criticality tier / owner / expected-host-count
that the attention model weights by.  Project-scoped; the Site NAME comes
from the subnet's site string (CSV/inline edit), so this surface edits
METADATA only, never the name.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Site, Subnet
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role

router = APIRouter(dependencies=[Depends(get_current_user)])


class SiteResponse(BaseModel):
    id: int
    name: str
    criticality_tier: int
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    expected_host_count: Optional[int] = None
    subnet_count: int = 0

    model_config = {"from_attributes": True}


class SiteUpdate(BaseModel):
    criticality_tier: Optional[int] = Field(None, ge=1, le=4)
    # Pass null to clear the owner / expected count.
    owner_id: Optional[int] = None
    expected_host_count: Optional[int] = Field(None, ge=0)


def _serialize(site: Site, subnet_count: int) -> SiteResponse:
    return SiteResponse(
        id=site.id, name=site.name, criticality_tier=site.criticality_tier,
        owner_id=site.owner_id,
        owner_name=(site.owner.full_name or site.owner.username) if site.owner else None,
        expected_host_count=site.expected_host_count, subnet_count=subnet_count,
    )


@router.get("", response_model=List[SiteResponse], summary="List the project's sites + metadata")
def list_sites(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    sites = (
        db.query(Site).filter(Site.project_id == project.id).order_by(Site.criticality_tier, Site.name).all()
    )
    counts = dict(
        db.query(Subnet.site_id, func.count(Subnet.id))
        .filter(Subnet.site_id.isnot(None))
        .group_by(Subnet.site_id)
        .all()
    )
    return [_serialize(s, int(counts.get(s.id, 0))) for s in sites]


@router.patch("/{site_id}", response_model=SiteResponse, summary="Update a site's tier / owner / expected host count")
def update_site(
    site_id: int,
    body: SiteUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _role: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    site = db.query(Site).filter(Site.id == site_id, Site.project_id == project.id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found in this project")
    fields = body.model_dump(exclude_unset=True)
    if "criticality_tier" in fields and fields["criticality_tier"] is not None:
        site.criticality_tier = fields["criticality_tier"]
    if "owner_id" in fields:
        # Defence-in-depth: an owner must be a member of this project.
        if fields["owner_id"] is not None:
            from app.db.models_project import ProjectMembership
            member = (
                db.query(ProjectMembership)
                .filter(ProjectMembership.project_id == project.id, ProjectMembership.user_id == fields["owner_id"])
                .first()
            )
            if not member:
                raise HTTPException(status_code=422, detail="Owner must be a member of this project")
        site.owner_id = fields["owner_id"]
    if "expected_host_count" in fields:
        site.expected_host_count = fields["expected_host_count"]
    db.commit()
    db.refresh(site)
    subnet_count = (
        db.query(func.count(Subnet.id)).filter(Subnet.site_id == site.id).scalar() or 0
    )
    return _serialize(site, int(subnet_count))
