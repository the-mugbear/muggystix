"""Presentation mode (2.530.0): the shared state of a driven view of an
issued report.  ``planning``'s contract, in code:

* **What is shown** is the issued report's frozen dataset only
  (``presentation_slides``); a draft has no presentation (404).
* **Only a move, Take control and End raise ``revision``** — each ONE
  conditional UPDATE returning the new row.  A move must come from the
  signed-in driver AND the driving tab (``driver_id`` and ``driver_client_id``,
  atomically), at the revision it saw, while the presentation is live; Take
  control always wins while live; End is the starter's or a project admin's.
* **Presence never touches ``revision``**: each heartbeat upserts the tab's
  ``presentation_viewers`` row.  A Display's heartbeat also carries what it
  last RENDERED, stored only if newer than what is stored (monotonic: a
  delayed older acknowledgment changes nothing).
* **The position is checked against the report**: a slide the list for that
  filter does not hold is a 422, and a filter change must land on the current
  finding's first endpoint slide (the list is rebuilt for the PROPOSED filter).
* **Idle end:** a live presentation with no move and no heartbeat for
  ``IDLE_MINUTES`` is ended (``ended_reason = 'idle'``) the next time anyone
  reads it or starts one — a paused presentation someone still has open keeps
  heartbeating and stays live.
* Answers never carry ``driver_client_id``: they say who drives and whether
  the CALLER (its user and the client id it sent) is the driver.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import log_audit_event
from app.db.models_auth import User
from app.db.models_presentations import (
    CLIENT_ID_MAX, Presentation, PresentationLayoutReport, PresentationViewer, VIEWER_KINDS,
)
from app.db.models_reports import Report, ReportImage, ReportStatus
from app.services import presentation_slides as slides_mod
from app.services.person import person_of

IDLE_MINUTES = 30
#: A tab is "watching" when it heartbeat within this many seconds (lenient
#: for a browser that throttles a hidden tab's timers).
WATCHING_SECONDS = 30
#: A Display not heard from for this long is "disconnected".
DISPLAY_SILENT_SECONDS = 10
FIRST_SLIDE = "overview:1"


# ---------------------------------------------------------------------------
# The report and its slides
# ---------------------------------------------------------------------------

def presentable_report(db: Session, project_id: int, report_id: int) -> Report:
    """An ISSUED (or superseded) report of the project, or a 404 — a draft
    is not presented (owner, 2026-10-23: issued reports only)."""
    report = db.query(Report).filter(Report.id == report_id, Report.project_id == project_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status == ReportStatus.DRAFT:
        raise HTTPException(status_code=404, detail="Only an issued report can be presented.")
    return report


def frozen_images(db: Session, report: Report) -> frozenset:
    """The attachment ids whose copy the issue froze AND is still in the
    report's storage — a printed image outside it is "not available"."""
    from app.services.client_report_service import stored_file_path

    out = set()
    for att_id, path in db.query(ReportImage.attachment_id, ReportImage.storage_path).filter(
        ReportImage.report_id == report.id
    ):
        try:
            stored_file_path(path)
        except (ValueError, FileNotFoundError):
            continue
        out.add(att_id)
    return frozenset(out)


# Issued reports never change, so their slide lists are kept (per process) by
# report, filter and the set of images still on disk.
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 32


def _dataset(db: Session, report: Report) -> dict:
    snapshot = db.query(Report.snapshot).filter(Report.id == report.id).scalar() or {}
    dataset = snapshot.get("dataset")
    if not dataset:
        raise HTTPException(status_code=409, detail="This report has no frozen content to present.")
    return dataset


def status_notes(report: Report) -> dict:
    """What the report's template prints of a finding's status and an
    endpoint's state (``template.json`` → ``status_notes``): the issued
    report's own frozen copy says; a copy frozen before the key existed (or
    a report issued before copies) falls back to the installed template of
    the same name; a template that does not say, or cannot be read, prints
    neither (owner, 2026-10-24: the presentation says what the report says)."""
    from app.services import report_template_service
    from app.services.client_report_service import frozen_template_dir

    declared = None
    try:
        folder = frozen_template_dir(report)
        if folder is not None:
            declared = report_template_service.load_folder(folder).status_notes
    except (ValueError, OSError):
        pass
    if declared is None and report.template:
        try:
            declared = report_template_service.get_template(report.template).status_notes
        except (ValueError, OSError):
            pass
    return dict(declared) if declared is not None else dict(slides_mod.NO_STATUS)


def slides_for(db: Session, report: Report, kind: Optional[str], value: Optional[str]) -> dict:
    images = frozen_images(db, report)
    key = (report.id, kind, (value or "").strip() or None, images)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    # Read when the list is built, not per move: it hashes the frozen copy.
    shown = status_notes(report)
    dataset = _dataset(db, report)
    try:
        flt = slides_mod.check_filter(dataset, kind, value)
    except slides_mod.FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = slides_mod.presentation_slides(dataset, flt, set(images), shown)
    result["keys"] = {s["key"] for s in result["slides"]}
    result["sites"] = slides_mod.scope_sites(dataset)
    result["segments"] = slides_mod.scope_segments(dataset)
    _CACHE[key] = result
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


def _ref_of(key: str) -> Optional[str]:
    if key.startswith("f:"):
        return key.split(":")[1]
    return None


def check_position(db: Session, report: Report, presentation: Optional[Presentation],
                   slide_key: str, kind: Optional[str], value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The normalised filter, or a 422: the slide must be in the list built
    for the PROPOSED filter, and a filter change must land on the current
    finding's first endpoint slide (or stay where it is, off a finding)."""
    value = (value or "").strip() or None
    if kind is None or value is None:
        kind = value = None
    result = slides_for(db, report, kind, value)
    if slide_key not in result["keys"]:
        raise HTTPException(status_code=422, detail="That slide is not in this report for this host filter.")
    if presentation is not None and (kind, value) != (presentation.host_filter_kind, presentation.host_filter_value):
        ref = _ref_of(presentation.slide_key)
        expected = slides_mod.first_hosts_key(result, ref) if ref else presentation.slide_key
        if slide_key != expected:
            raise HTTPException(
                status_code=422,
                detail="A host filter change goes to the first endpoint slide of the current finding.",
            )
    return kind, value


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def live_presentation(db: Session, report: Report) -> Optional[Presentation]:
    """The report's live presentation, after ending it if it has gone idle."""
    p = (
        db.query(Presentation)
        .filter(Presentation.report_id == report.id, Presentation.ended_at.is_(None))
        .first()
    )
    if p is None:
        return None
    if _expire_if_idle(db, p):
        return None
    return p


def _expire_if_idle(db: Session, p: Presentation) -> bool:
    last_seen = select(func.max(PresentationViewer.last_seen_at)).where(
        PresentationViewer.presentation_id == p.id
    ).scalar_subquery()
    cutoff = func.now() - timedelta(minutes=IDLE_MINUTES)
    row = db.execute(
        update(Presentation)
        .where(
            Presentation.id == p.id, Presentation.ended_at.is_(None),
            Presentation.last_move_at < cutoff,
            or_(last_seen.is_(None), last_seen < cutoff),
        )
        .values(ended_at=func.now(), ended_reason="idle", revision=Presentation.revision + 1,
                driver_client_id=None)
        .returning(Presentation.id)
    ).first()
    if row is None:
        return False
    db.commit()
    db.refresh(p)
    return True


def get_presentation(db: Session, project_id: int, presentation_id: int) -> Presentation:
    p = db.query(Presentation).filter(
        Presentation.id == presentation_id, Presentation.project_id == project_id,
    ).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    if p.ended_at is None:
        _expire_if_idle(db, p)
    return p


def state(db: Session, p: Presentation, user: User, client_id: Optional[str]) -> dict:
    """The answer every presentation route gives: the position, who drives,
    whether the CALLER drives, and who is watching (with each Display's
    rendered position and status)."""
    viewers = (
        db.query(PresentationViewer, func.extract("epoch", func.now() - PresentationViewer.last_seen_at))
        .filter(PresentationViewer.presentation_id == p.id)
        .order_by(PresentationViewer.id)
        .all()
    )
    watching, displays = [], []
    for v, age in viewers:
        age = float(age or 0)
        if v.kind == "display":
            if age > DISPLAY_SILENT_SECONDS:
                status = "disconnected"
            elif v.rendered_revision is not None and v.rendered_revision >= p.revision:
                status = "synchronized"
            else:
                status = "catching_up"
            if age <= WATCHING_SECONDS * 10:
                displays.append({
                    "client": v.id, "person": person_of(v.user), "status": status,
                    "rendered": None if v.rendered_revision is None else {
                        "revision": v.rendered_revision, "slide_key": v.rendered_slide_key,
                        "host_filter": _filter_out(v.rendered_host_filter_kind, v.rendered_host_filter_value),
                    },
                    "fitted": v.fitted,
                })
        if age <= WATCHING_SECONDS:
            watching.append({"person": person_of(v.user), "kind": v.kind})
    ended = None
    if p.ended_at is not None:
        ended = {"at": p.ended_at.isoformat(), "reason": p.ended_reason, "by": person_of(p.ended_by)}
    return {
        "id": p.id,
        "report_id": p.report_id,
        "revision": p.revision,
        "slide_key": p.slide_key,
        "host_filter": _filter_out(p.host_filter_kind, p.host_filter_value),
        "driver": person_of(p.driver),
        "you_are_driving": bool(
            p.ended_at is None and client_id and p.driver_id == user.id and p.driver_client_id == client_id
        ),
        # The caller drives from ANOTHER tab (or from this page before it reloaded).
        "driving_elsewhere": bool(
            p.ended_at is None and p.driver_id == user.id and p.driver_client_id != client_id
        ),
        "started_by": person_of(p.started_by),
        "started_at": p.started_at.isoformat() if p.started_at else None,
        "ended": ended,
        "watching": watching,
        "displays": displays,
    }


def _filter_out(kind: Optional[str], value: Optional[str]) -> Optional[dict]:
    return None if kind is None else {"kind": kind, "value": value}


def _client(client_id: Optional[str]) -> str:
    c = (client_id or "").strip()
    if not c or len(c) > CLIENT_ID_MAX:
        raise HTTPException(status_code=422, detail="A tab id (1–64 characters) is required.")
    return c


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def start(db: Session, report: Report, user: User, client_id: str) -> Presentation:
    """Start a presentation of ``report`` driven from this tab — or, when one
    is live, return it (a second Start joins)."""
    client_id = _client(client_id)
    existing = live_presentation(db, report)
    if existing is not None:
        return existing
    p = Presentation(
        project_id=report.project_id, report_id=report.id, started_by_id=user.id,
        driver_id=user.id, driver_client_id=client_id, slide_key=FIRST_SLIDE,
    )
    try:
        with db.begin_nested():
            db.add(p)
            db.flush()
    except IntegrityError:
        # Another Start won the race: join it.
        existing = live_presentation(db, report)
        if existing is None:
            raise HTTPException(status_code=409, detail="The presentation could not be started; try again.")
        return existing
    log_audit_event(db, user.id, "presentation_started", resource_type="presentation",
                    resource_id=str(p.id), details={"report_id": report.id}, commit=False)
    db.commit()
    db.refresh(p)
    return p


def heartbeat(db: Session, p: Presentation, report: Report, user: User, client_id: str, kind: str,
              rendered: Optional[dict] = None) -> None:
    """Presence, and a Display's rendered position.  Never raises
    ``revision``."""
    client_id = _client(client_id)
    if kind not in VIEWER_KINDS:
        raise HTTPException(status_code=422, detail="kind is display, controller or browse.")
    if p.ended_at is not None:
        return
    stmt = pg_insert(PresentationViewer).values(
        presentation_id=p.id, user_id=user.id, client_id=client_id, kind=kind, last_seen_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_presentation_viewers_client",
        set_={"last_seen_at": func.now(), "kind": kind},
        # A tab id belongs to the person whose tab made it.
        where=PresentationViewer.user_id == user.id,
    )
    db.execute(stmt)
    if rendered is not None and kind == "display":
        revision = int(rendered.get("revision") or 0)
        if revision < 1 or revision > p.revision:
            raise HTTPException(status_code=422, detail="A Display cannot have rendered that revision.")
        flt = rendered.get("host_filter") or {}
        f_kind, f_value = check_position(db, report, None, str(rendered.get("slide_key") or ""),
                                         flt.get("kind"), flt.get("value"))
        db.execute(
            update(PresentationViewer)
            .where(
                PresentationViewer.presentation_id == p.id, PresentationViewer.client_id == client_id,
                PresentationViewer.user_id == user.id, PresentationViewer.kind == "display",
                or_(PresentationViewer.rendered_revision.is_(None),
                    PresentationViewer.rendered_revision < revision),
            )
            .values(
                rendered_revision=revision, rendered_slide_key=str(rendered.get("slide_key")),
                rendered_host_filter_kind=f_kind, rendered_host_filter_value=f_value,
                rendered_at=func.now(), fitted=bool(rendered.get("fitted")),
            )
        )
    db.commit()


def _conflict(db: Session, p: Presentation, user: User, client_id: Optional[str], reason: str) -> HTTPException:
    db.rollback()
    db.refresh(p)
    return HTTPException(status_code=409, detail={
        "reason": reason, "message": {
            "stale": "The presentation moved on before this change arrived.",
            "not_driver": "This tab is not driving the presentation.",
            "ended": "The presentation has ended.",
        }[reason],
        "presentation": state(db, p, user, client_id),
    })


def move(db: Session, p: Presentation, report: Report, user: User, client_id: str, seen_revision: int,
         slide_key: str, kind: Optional[str], value: Optional[str]) -> Presentation:
    client_id = _client(client_id)
    kind, value = check_position(db, report, p, slide_key, kind, value)
    row = db.execute(
        update(Presentation)
        .where(
            Presentation.id == p.id, Presentation.ended_at.is_(None),
            Presentation.revision == seen_revision,
            Presentation.driver_id == user.id, Presentation.driver_client_id == client_id,
        )
        .values(slide_key=slide_key, host_filter_kind=kind, host_filter_value=value,
                revision=Presentation.revision + 1, last_move_at=func.now())
        .returning(Presentation.id)
    ).first()
    if row is None:
        db.refresh(p)
        if p.ended_at is not None:
            raise _conflict(db, p, user, client_id, "ended")
        if p.driver_id != user.id or p.driver_client_id != client_id:
            raise _conflict(db, p, user, client_id, "not_driver")
        raise _conflict(db, p, user, client_id, "stale")
    db.commit()
    db.refresh(p)
    return p


def take_control(db: Session, p: Presentation, user: User, client_id: str) -> Presentation:
    """Always wins while live; keeps the slide and the filter."""
    client_id = _client(client_id)
    row = db.execute(
        update(Presentation)
        .where(Presentation.id == p.id, Presentation.ended_at.is_(None))
        .values(driver_id=user.id, driver_client_id=client_id, revision=Presentation.revision + 1)
        .returning(Presentation.id)
    ).first()
    if row is None:
        raise _conflict(db, p, user, client_id, "ended")
    log_audit_event(db, user.id, "presentation_control_taken", resource_type="presentation",
                    resource_id=str(p.id), details={"report_id": p.report_id}, commit=False)
    db.commit()
    db.refresh(p)
    return p


def end(db: Session, p: Presentation, user: User, client_id: Optional[str], *, is_project_admin: bool) -> Presentation:
    if not (is_project_admin or p.started_by_id == user.id):
        raise HTTPException(status_code=403, detail="Only who started the presentation, or a project admin, ends it.")
    row = db.execute(
        update(Presentation)
        .where(Presentation.id == p.id, Presentation.ended_at.is_(None))
        .values(ended_at=func.now(), ended_reason="ended", ended_by_id=user.id,
                revision=Presentation.revision + 1, driver_client_id=None)
        .returning(Presentation.id)
    ).first()
    if row is None:
        raise _conflict(db, p, user, client_id, "ended")
    log_audit_event(db, user.id, "presentation_ended", resource_type="presentation",
                    resource_id=str(p.id), details={"report_id": p.report_id}, commit=False)
    db.commit()
    db.refresh(p)
    return p


def report_layout(db: Session, report: Report, user: User, slide_key: str, overflow_px: int, font: str) -> bool:
    """Record a slide that overflowed its estimate in someone's browser.  The
    same slide, font and person within the hour is recorded once."""
    slide_key = (slide_key or "")[:200]
    font = (font or "unknown")[:100]
    recent = db.query(PresentationLayoutReport.id).filter(
        PresentationLayoutReport.report_id == report.id,
        PresentationLayoutReport.slide_key == slide_key,
        PresentationLayoutReport.font == font,
        PresentationLayoutReport.user_id == user.id,
        PresentationLayoutReport.created_at > func.now() - timedelta(hours=1),
    ).first()
    if recent is not None:
        return False
    db.add(PresentationLayoutReport(report_id=report.id, slide_key=slide_key,
                                    overflow_px=max(0, min(int(overflow_px), 100_000)), font=font,
                                    user_id=user.id))
    db.commit()
    return True

