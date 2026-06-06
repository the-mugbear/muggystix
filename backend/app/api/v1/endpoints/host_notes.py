"""Host notes CRUD endpoints."""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, desc
from sqlalchemy.orm import Session, selectinload

logger = logging.getLogger(__name__)

from app.db.session import get_db
from app.db import models
from app.db.models import HostNote as HostNoteModel, NoteStatus
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.schemas.schemas import HostNote, HostNoteCreate, HostNoteUpdate
from app.services.notification_service import NotificationService
from app.services.webhook_dispatcher import safe_dispatch
from app.services.host_follow_service import HostFollowService

router = APIRouter(dependencies=[Depends(get_current_user)])


def _serialize_note(note: HostNoteModel) -> HostNote:
    author_name = None
    if note.author:
        author_name = note.author.full_name or note.author.username
    return HostNote(
        id=note.id,
        body=note.body,
        status=note.status,
        author_id=note.user_id,
        author_name=author_name,
        parent_id=note.parent_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get(
    "/notes/unread-count",
    summary="Count notes from teammates since last activity-page visit",
)
def get_unread_activity_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get count of notes created/updated since the user last viewed the activity page."""
    last_seen = current_user.last_activity_seen_at
    query = db.query(func.count(HostNoteModel.id)).join(
        models.Host, HostNoteModel.host_id == models.Host.id
    ).filter(models.Host.project_id == project.id)
    if last_seen:
        query = query.filter(
            func.coalesce(HostNoteModel.updated_at, HostNoteModel.created_at) > last_seen
        )
        # Exclude the user's own notes from the unread count
        query = query.filter(HostNoteModel.user_id != current_user.id)
    else:
        # Never seen activity — count all notes by other users
        query = query.filter(HostNoteModel.user_id != current_user.id)
    count = query.scalar() or 0
    return {"unread_count": count}


@router.post(
    "/notes/mark-seen",
    status_code=204,
    summary="Mark all activity as seen (advances last_activity_seen_at)",
)
def mark_activity_seen(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Mark all current activity as seen by updating the user's last_activity_seen_at."""
    current_user.last_activity_seen_at = func.now()
    db.commit()
    return Response(status_code=204)


@router.get(
    "/notes/activity",
    summary="Project activity feed — host notes grouped by host",
)
def get_note_activity(
    status: Optional[str] = Query(None, description="Filter by note status (open, in_progress, resolved)"),
    author_id: Optional[int] = Query(None, description="Filter by author user ID"),
    search: Optional[str] = Query(None, description="Search notes by IP, hostname, or note body"),
    # v2.86.4 — pagination caps added.
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get all note activity grouped by host for the activity page."""
    query = (
        db.query(HostNoteModel)
        .options(selectinload(HostNoteModel.author))
        .join(models.Host, HostNoteModel.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id)
    )

    if status:
        query = query.filter(HostNoteModel.status == status)

    if author_id:
        query = query.filter(HostNoteModel.user_id == author_id)

    if search:
        from app.api.v1.endpoints.hosts import _escape_like
        escaped = _escape_like(search)
        query = query.filter(
            (models.Host.ip_address.ilike(f"%{escaped}%"))
            | (models.Host.hostname.ilike(f"%{escaped}%"))
            | (HostNoteModel.body.ilike(f"%{escaped}%"))
        )

    notes = (
        query.order_by(desc(func.coalesce(HostNoteModel.updated_at, HostNoteModel.created_at)))
        .offset(skip)
        .limit(limit)
        .all()
    )

    ancestor_note_map = {note.id: note for note in notes}
    pending_parent_ids = {
        note.parent_id for note in notes
        if note.parent_id is not None and note.parent_id not in ancestor_note_map
    }

    while pending_parent_ids:
        parent_notes = (
            db.query(HostNoteModel)
            .filter(HostNoteModel.id.in_(pending_parent_ids))
            .all()
        )
        if not parent_notes:
            break

        next_pending_ids = set()
        for parent_note in parent_notes:
            ancestor_note_map[parent_note.id] = parent_note
            if parent_note.parent_id is not None and parent_note.parent_id not in ancestor_note_map:
                next_pending_ids.add(parent_note.parent_id)
        pending_parent_ids = next_pending_ids

    # Build host lookup for enrichment
    host_ids = list({n.host_id for n in notes})
    hosts_map = {}
    if host_ids:
        hosts = db.query(models.Host).filter(models.Host.id.in_(host_ids)).all()
        hosts_map = {h.id: h for h in hosts}

    # Count notes per host
    host_note_counts = {}
    if host_ids:
        counts = (
            db.query(HostNoteModel.host_id, func.count(HostNoteModel.id))
            .filter(HostNoteModel.host_id.in_(host_ids))
            .group_by(HostNoteModel.host_id)
            .all()
        )
        host_note_counts = dict(counts)

    # Aggregate stats (scoped to project)
    total_notes = db.query(func.count(HostNoteModel.id)).join(
        models.Host, HostNoteModel.host_id == models.Host.id
    ).filter(models.Host.project_id == project.id).scalar() or 0
    status_counts = dict(
        db.query(HostNoteModel.status, func.count(HostNoteModel.id))
        .join(models.Host, HostNoteModel.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id)
        .group_by(HostNoteModel.status)
        .all()
    )

    def resolve_thread_root_id(note: HostNoteModel) -> int:
        current = note
        seen = {note.id}

        while current.parent_id is not None:
            parent = ancestor_note_map.get(current.parent_id)
            if parent is None or parent.id in seen:
                return current.parent_id
            current = parent
            seen.add(current.id)

        return current.id

    thread_note_counts = {}
    for note in notes:
        key = (note.host_id, resolve_thread_root_id(note))
        thread_note_counts[key] = thread_note_counts.get(key, 0) + 1

    results = []
    for note in notes:
        host = hosts_map.get(note.host_id)
        author_name = None
        if note.author:
            author_name = note.author.full_name or note.author.username
        thread_root_id = resolve_thread_root_id(note)
        results.append({
            "note_id": note.id,
            "host_id": note.host_id,
            "ip_address": host.ip_address if host else None,
            "hostname": host.hostname if host else None,
            "body": note.body,
            "status": note.status.value if hasattr(note.status, "value") else note.status,
            "author_name": author_name,
            "author_id": note.user_id,
            "parent_id": note.parent_id,
            "thread_root_id": thread_root_id,
            "thread_note_count": thread_note_counts.get((note.host_id, thread_root_id), 1),
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            "host_note_count": host_note_counts.get(note.host_id, 0),
        })

    # Get distinct authors for the user filter dropdown
    from app.db.models_auth import User as UserModel
    author_rows = (
        db.query(UserModel.id, UserModel.username, UserModel.full_name)
        .join(HostNoteModel, HostNoteModel.user_id == UserModel.id)
        .join(models.Host, HostNoteModel.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id)
        .distinct()
        .all()
    )
    authors = [
        {"id": a.id, "name": a.full_name or a.username}
        for a in author_rows
    ]

    return {
        "notes": results,
        "total_notes": total_notes,
        "status_counts": {
            "open": status_counts.get(NoteStatus.OPEN, status_counts.get("open", 0)),
            "in_progress": status_counts.get(NoteStatus.IN_PROGRESS, status_counts.get("in_progress", 0)),
            "resolved": status_counts.get(NoteStatus.RESOLVED, status_counts.get("resolved", 0)),
        },
        "authors": authors,
    }


@router.get(
    "/{host_id:int}/notes",
    response_model=List[HostNote],
    summary="List notes on a host",
)
def list_host_notes(
    host_id: int,
    # v2.86.4 — pagination cap added.
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    notes = follow_service.list_notes(host_id, limit=limit)
    return [_serialize_note(note) for note in notes]


@router.post(
    "/{host_id:int}/notes",
    response_model=HostNote,
    summary="Create a note on a host (parses @mentions to notify users)",
)
def create_host_note(
    host_id: int,
    payload: HostNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    try:
        note = follow_service.create_note(
            host_id, current_user.id, payload.body, payload.status,
            parent_id=payload.parent_id,
        )
    except ValueError as exc:
        # parent_id validation failure (cross-host threading attempt).
        raise HTTPException(status_code=400, detail=str(exc))

    # Audit finding H3: previously swallowed mention-processing
    # failures silently, so a broken notification pipeline would
    # return 200 and nobody would know the mentioned user wasn't
    # alerted.  Now: log with full context, increment a warning
    # counter via structured log extras, and surface a best-effort
    # ``mention_warning`` note on the serialized response so clients
    # can display a toast ("note saved, but mention notifications
    # failed — the tagged user may not be alerted").  The note
    # itself is still persisted; only the side-effect is partial.
    mention_warning: Optional[str] = None
    mention_notifs = []
    try:
        notification_service = NotificationService(db)
        mention_notifs = notification_service.process_note_mentions(note, current_user, project) or []
        db.commit()
    except Exception as exc:
        logger.exception(
            "Mention processing failed",
            extra={
                "note_id": note.id,
                "host_id": host_id,
                "author_id": current_user.id,
                "project_id": project.id,
            },
        )
        mention_warning = (
            "Note saved, but mention notifications could not be delivered. "
            "Tagged users may not have been alerted; contact an admin if "
            "this persists."
        )
        db.rollback()

    # Outbound webhook — only when a mention actually fired and the
    # commit succeeded.  Best-effort, post-commit (safe_dispatch swallows).
    if mention_warning is None and mention_notifs:
        safe_dispatch(
            db,
            project_id=project.id,
            event="note_mention",
            title=f"@{current_user.username} mentioned {len(mention_notifs)} user(s) on {host.hostname or host.ip_address}",
            body=(payload.body or "")[:280],
            context={"host_id": host_id, "note_id": note.id},
        )

    serialized = _serialize_note(note)
    if mention_warning:
        # HostNote is a Pydantic model; use model_copy to attach the
        # warning without mutating the schema signature.  Frontend
        # clients that don't know about mention_warning simply ignore
        # the extra field.
        return serialized.model_copy(update={"mention_warning": mention_warning})
    return serialized


@router.patch(
    "/{host_id:int}/notes/{note_id:int}",
    response_model=HostNote,
    summary="Edit a note (re-parses @mentions on body change)",
)
def update_host_note(
    host_id: int,
    note_id: int,
    payload: HostNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    try:
        note = follow_service.update_note(
            note_id,
            current_user.id,
            body=payload.body,
            status=payload.status,
            host_id=host_id,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized to modify this note")

    # Same partial-success contract as create_host_note — see audit
    # finding H3.  Note update succeeds independently of notification
    # delivery; a best-effort ``mention_warning`` on the response
    # body tells clients about the degradation without blocking the
    # core write.
    mention_warning: Optional[str] = None
    mention_notifs = []
    status_changed = False
    try:
        notification_service = NotificationService(db)
        if payload.body:
            mention_notifs = notification_service.process_note_mentions(note, current_user, project) or []
        if payload.status:
            old_status = "unknown"  # We don't track the previous value easily
            notification_service.notify_status_change(note, old_status, payload.status, current_user, project)
            status_changed = True
        db.commit()
    except Exception as exc:
        logger.exception(
            "Note update notification processing failed",
            extra={
                "note_id": note_id,
                "host_id": host_id,
                "author_id": current_user.id,
                "project_id": project.id,
            },
        )
        mention_warning = (
            "Note updated, but notifications could not be delivered. "
            "Tagged users or status watchers may not have been alerted."
        )
        db.rollback()

    if mention_warning is None:
        host_label = host.hostname or host.ip_address
        if mention_notifs:
            safe_dispatch(
                db,
                project_id=project.id,
                event="note_mention",
                title=f"@{current_user.username} mentioned {len(mention_notifs)} user(s) on {host_label}",
                body=(payload.body or "")[:280],
                context={"host_id": host_id, "note_id": note_id},
            )
        if status_changed:
            safe_dispatch(
                db,
                project_id=project.id,
                event="note_status_change",
                title=f"Note on {host_label} → {payload.status}",
                body=(payload.body or "")[:280],
                context={"host_id": host_id, "note_id": note_id, "status": payload.status},
            )

    serialized = _serialize_note(note)
    if mention_warning:
        return serialized.model_copy(update={"mention_warning": mention_warning})
    return serialized


@router.delete(
    "/{host_id:int}/notes/{note_id:int}",
    status_code=204,
    summary="Delete a note",
)
def delete_host_note(
    host_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    try:
        follow_service.delete_note(note_id, current_user.id, host_id=host_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized to delete this note")
    return Response(status_code=204)
