"""Presentation mode (2.530.0): a shared, driven view of an ISSUED report.

Every route is the client reports' own floor — auditor and above (owner,
2026-10-24: viewers have no access to this mode); ending is further the
starter's or a project admin's.  The rules live in
``services/presentation_service`` and ``services/presentation_slides``.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user, is_project_admin, require_project_role
from app.db.models_auth import User
from app.db.models_presentations import Presentation
from app.db.models_project import Project, ProjectRole
from app.db.models_reports import Report, ReportImage
from app.db.session import get_db
from app.schemas.presentations import (
    AgendaIn, AgendaSaveIn, ClientIn, EndIn, HeartbeatIn, LayoutReportIn, MoveIn, SpotlightIn,
)
from app.services import presentation_service as svc
from app.services.client_report_service import stored_file_path

router = APIRouter(dependencies=[
    Depends(get_current_user),
    Depends(require_project_role(ProjectRole.AUDITOR)),
])


def _report_of(db: Session, project: Project, p: Presentation) -> Report:
    return svc.presentable_report(db, project.id, p.report_id)


@router.get("/client-reports/{report_id}/presentation")
def live_presentation(
    report_id: int,
    client_id: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """``{"live": <presentation> | null}`` — the report page's "Join"."""
    report = svc.presentable_report(db, project.id, report_id)
    p = svc.live_presentation(db, report)
    return {"live": None if p is None else svc.state(db, p, user, client_id)}


@router.post("/client-reports/{report_id}/presentation")
def start_presentation(
    report_id: int,
    body: ClientIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """Start a presentation driven from this tab, or join the live one."""
    report = svc.presentable_report(db, project.id, report_id)
    p = svc.start(db, report, user, body.client_id)
    return svc.state(db, p, user, body.client_id)


@router.get("/client-reports/{report_id}/presentation/slides")
def presentation_slides(
    report_id: int,
    host_filter_kind: Optional[str] = Query(None),
    host_filter_value: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """The report's slide list for a host filter: ``{stage, slides, filter,
    sites, segments}``.  The same list for the same report and filter on
    every read."""
    report = svc.presentable_report(db, project.id, report_id)
    result = svc.slides_for(db, report, host_filter_kind, host_filter_value)
    return {k: v for k, v in result.items() if k != "keys"}


@router.get("/client-reports/{report_id}/presentation/images/{attachment_id}")
def presentation_image(
    report_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """An image the issue FROZE, from the report's own storage — never the
    live attachment (a 404 when the report holds no copy, 410 when the copy
    is gone from storage)."""
    report = svc.presentable_report(db, project.id, report_id)
    row = db.query(ReportImage).filter(
        ReportImage.report_id == report.id, ReportImage.attachment_id == attachment_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="This report holds no copy of that image.")
    try:
        target = stored_file_path(row.storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    return FileResponse(path=str(target), media_type=row.content_type,
                        headers={"Cache-Control": "private, max-age=3600"})


@router.post("/client-reports/{report_id}/presentation/layout-reports", status_code=202)
def report_layout(
    report_id: int,
    body: LayoutReportIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """A slide that overflowed its estimate in this browser (the fit
    fallback) — kept so the estimator can be fixed."""
    report = svc.presentable_report(db, project.id, report_id)
    return {"recorded": svc.report_layout(db, report, user, body.slide_key, body.overflow_px, body.font)}


@router.post("/presentations/{presentation_id}/heartbeat")
def heartbeat(
    presentation_id: int,
    body: HeartbeatIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """A tab's poll: presence (and a Display's rendered position), then the
    presentation.  Never raises the revision."""
    p = svc.get_presentation(db, project.id, presentation_id)
    report = _report_of(db, project, p)
    rendered = body.rendered.model_dump() if body.rendered is not None else None
    svc.heartbeat(db, p, report, user, body.client_id, body.kind, rendered)
    return svc.state(db, p, user, body.client_id)


@router.post("/presentations/{presentation_id}/move")
def move(
    presentation_id: int,
    body: MoveIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """The driver moves (a slide, or a host filter change).  409 with the
    current presentation when the revision is stale, the tab is not the
    driver, or it has ended."""
    p = svc.get_presentation(db, project.id, presentation_id)
    report = _report_of(db, project, p)
    flt = body.host_filter
    p = svc.move(db, p, report, user, body.client_id, body.revision, body.slide_key,
                 flt.kind if flt else None, flt.value if flt else None,
                 body.agenda.model_dump() if body.agenda else None)
    return svc.state(db, p, user, body.client_id)


@router.post("/presentations/{presentation_id}/spotlight")
def spotlight(
    presentation_id: int,
    body: SpotlightIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """The driver highlights a point of the stage for 5 s on every tab.  The
    move's 409s (stale, not the driver, ended); never raises the revision."""
    p = svc.get_presentation(db, project.id, presentation_id)
    p = svc.spotlight(db, p, user, body.client_id, body.revision, body.x, body.y)
    return svc.state(db, p, user, body.client_id)


# --- Agendas (E3): the report's, auditor and above ------------------------------

@router.get("/client-reports/{report_id}/presentation/agendas")
def list_agendas(report_id: int, db: Session = Depends(get_db), project: Project = Depends(get_current_project)):
    report = svc.presentable_report(db, project.id, report_id)
    return svc.list_agendas(db, report)


@router.post("/client-reports/{report_id}/presentation/agendas", status_code=201)
def create_agenda(
    report_id: int,
    body: AgendaIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    report = svc.presentable_report(db, project.id, report_id)
    return svc.create_agenda(db, report, user, body.name, body.items)


@router.put("/client-reports/{report_id}/presentation/agendas/{agenda_id}")
def save_agenda(
    report_id: int,
    agenda_id: int,
    body: AgendaSaveIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """A new version (409 when someone saved since ``expected_version``)."""
    report = svc.presentable_report(db, project.id, report_id)
    return svc.save_agenda(db, report, user, agenda_id, body.name, body.items, body.expected_version)


@router.delete("/client-reports/{report_id}/presentation/agendas/{agenda_id}", status_code=204)
def delete_agenda(
    report_id: int,
    agenda_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """409 while a running presentation follows it."""
    report = svc.presentable_report(db, project.id, report_id)
    svc.delete_agenda(db, report, agenda_id)
    return Response(status_code=204)


@router.post("/presentations/{presentation_id}/take-control")
def take_control(
    presentation_id: int,
    body: ClientIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    p = svc.get_presentation(db, project.id, presentation_id)
    p = svc.take_control(db, p, user, body.client_id)
    return svc.state(db, p, user, body.client_id)


@router.post("/presentations/{presentation_id}/end")
def end(
    presentation_id: int,
    body: EndIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    p = svc.get_presentation(db, project.id, presentation_id)
    p = svc.end(db, p, user, body.client_id, is_project_admin=is_project_admin(db, project.id, user))
    return svc.state(db, p, user, body.client_id)
