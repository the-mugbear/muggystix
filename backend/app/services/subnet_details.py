"""A subnet's description and site — the ONE write of them (2.522.0).

The Scope page's edit (``PATCH /scopes/subnets/{id}``) and an accepted agent
proposal (``proposal_service``, kind ``subnet_details``) both call
:func:`set_subnet_details`, so an agent's proposal changes a subnet exactly as
the person's click does.  ``get_or_create_site`` is the one way a site name
becomes a ``Site`` row (the upload, the batch add and the edit use it too).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Site, Subnet


def get_or_create_site(
    db: Session, project_id: int, name: Optional[str], user_id: Optional[int], cache: Optional[dict] = None,
) -> Optional[Site]:
    """Get-or-create the project's Site for ``name`` (blank → None).

    Keyed by (project_id, name) so the Site metadata (criticality tier / owner
    / expected host count) attaches to the human-entered site name set from
    CSV col 4 or inline edit — keeping subnet.site_id in sync with the string.
    """
    name = (name or "").strip()
    if not name:
        return None
    if cache is not None and name in cache:
        return cache[name]
    site = db.query(Site).filter(Site.project_id == project_id, Site.name == name).first()
    if site is None:
        site = Site(project_id=project_id, name=name, created_by_id=user_id)
        db.add(site)
        db.flush()
    if cache is not None:
        cache[name] = site
    return site


def set_subnet_details(
    db: Session, project_id: int, subnet: Subnet, *,
    description: Optional[str] = None, site: Optional[str] = None, user_id: Optional[int] = None,
) -> None:
    """Set what is given: ``None`` leaves a field alone; an empty site clears
    it (a value sets it, keeping ``site_id`` in step).  The caller commits."""
    if description is not None:
        subnet.description = description
    if site is not None:
        subnet.site = site or None
        site_obj = get_or_create_site(db, project_id, site, user_id) if site else None
        subnet.site_id = site_obj.id if site_obj else None
