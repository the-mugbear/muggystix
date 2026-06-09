"""Host notes CRUD endpoints."""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, desc
from sqlalchemy.orm import Session, selectinload

logger = logging.getLogger(__name__)

from app.db.session import get_db
from app.db import models
from app.db.models import Annotation as AnnotationModel, NoteStatus, ActivityCursor
from app.db.models_auth import User, UserRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole, ProjectMembership
from app.schemas.schemas import (
    Annotation, AnnotationCreate, AnnotationUpdate, AnnotationStatusHistoryEntry,
)
from app.services.notification_service import NotificationService
from app.services.webhook_dispatcher import safe_dispatch
from app.services.host_follow_service import (
    HostFollowService, VALID_NOTE_TYPES, NoteHasRepliesError,
)
# CR4-2 — serializer moved to the service layer (was defined here and
# imported back by host_serialization, a service -> router dependency).
from app.services.host_serialization import _serialize_note
from app.db.cursor_upsert import upsert_user_project_cursor

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get(
    "/notes/unread-count",
    summary="Count notes from teammates since last activity-page visit",
)
def get_unread_activity_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Count notes by teammates since the caller last marked THIS project's
    activity feed seen.  RV-6 — the cursor is per (user, project), so a
    visit to one project no longer hides unread activity in another."""
    cursor = (
        db.query(ActivityCursor)
        .filter(
            ActivityCursor.user_id == current_user.id,
            ActivityCursor.project_id == project.id,
        )
        .first()
    )
    last_seen = cursor.last_seen_at if cursor else None
    query = db.query(func.count(AnnotationModel.id)).join(
        models.Host, AnnotationModel.host_id == models.Host.id
    ).filter(
        models.Host.project_id == project.id,
        # Always exclude the caller's own notes from the unread count.
        AnnotationModel.user_id != current_user.id,
    )
    if last_seen:
        query = query.filter(
            func.coalesce(AnnotationModel.updated_at, AnnotationModel.created_at) > last_seen
        )
    count = query.scalar() or 0
    return {"unread_count": count}


@router.post(
    "/notes/mark-seen",
    status_code=204,
    summary="Mark this project's activity feed seen (per-user/project cursor)",
)
def mark_activity_seen(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Advance the caller's Activity cursor for THIS project only (RV-6)."""
    now = datetime.now(timezone.utc)
    # Race-safe upsert (review #9).
    upsert_user_project_cursor(
        db, ActivityCursor,
        user_id=current_user.id, project_id=project.id,
        ts_column="last_seen_at", ts_value=now,
    )
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
        db.query(AnnotationModel)
        .options(selectinload(AnnotationModel.author))
        .join(models.Host, AnnotationModel.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id)
    )

    if status:
        # review #5 — filter by THREAD (root) status, not the per-message
        # status: return every message of threads whose root has `status`,
        # so the feed agrees with the root-status badge the UI renders.
        root_ids_with_status = (
            db.query(AnnotationModel.id)
            .join(models.Host, AnnotationModel.host_id == models.Host.id)
            .filter(
                models.Host.project_id == project.id,
                AnnotationModel.parent_id.is_(None),
                AnnotationModel.status == status,
            )
        )
        query = query.filter(AnnotationModel.thread_root_id.in_(root_ids_with_status))

    if author_id:
        query = query.filter(AnnotationModel.user_id == author_id)

    if search:
        from app.api.v1.endpoints.hosts import _escape_like
        escaped = _escape_like(search)
        query = query.filter(
            (models.Host.ip_address.ilike(f"%{escaped}%"))
            | (models.Host.hostname.ilike(f"%{escaped}%"))
            | (AnnotationModel.body.ilike(f"%{escaped}%"))
        )

    notes = (
        query.order_by(desc(func.coalesce(AnnotationModel.updated_at, AnnotationModel.created_at)))
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
            db.query(AnnotationModel)
            .filter(AnnotationModel.id.in_(pending_parent_ids))
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
            db.query(AnnotationModel.host_id, func.count(AnnotationModel.id))
            .filter(AnnotationModel.host_id.in_(host_ids))
            .group_by(AnnotationModel.host_id)
            .all()
        )
        host_note_counts = dict(counts)

    # Aggregate stats (scoped to project)
    total_notes = db.query(func.count(AnnotationModel.id)).join(
        models.Host, AnnotationModel.host_id == models.Host.id
    ).filter(models.Host.project_id == project.id).scalar() or 0
    # review #5 — status counts are THREAD counts by root status (root
    # notes only), matching the thread-status filter above so totals and
    # filtered results can't contradict each other.
    status_counts = dict(
        db.query(AnnotationModel.status, func.count(AnnotationModel.id))
        .join(models.Host, AnnotationModel.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            AnnotationModel.parent_id.is_(None),
        )
        .group_by(AnnotationModel.status)
        .all()
    )

    def resolve_thread_root_id(note: AnnotationModel) -> int:
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
        # Thread status is the ROOT note's status, not the latest reply's.
        # Pre-fix the Activity feed showed `latest.status`, so replying to a
        # resolved thread (replies are forced to status "open" client-side)
        # silently reopened it.  The root note is always loaded into
        # ancestor_note_map by the parent-walk above; fall back to this
        # note's own status only if the root somehow isn't present.
        root_note = ancestor_note_map.get(thread_root_id)
        thread_status_source = root_note if root_note is not None else note
        thread_root_status = (
            thread_status_source.status.value
            if hasattr(thread_status_source.status, "value")
            else thread_status_source.status
        )
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
            "thread_root_status": thread_root_status,
            "thread_note_count": thread_note_counts.get((note.host_id, thread_root_id), 1),
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            "host_note_count": host_note_counts.get(note.host_id, 0),
        })

    # Get distinct authors for the user filter dropdown
    from app.db.models_auth import User as UserModel
    author_rows = (
        db.query(UserModel.id, UserModel.username, UserModel.full_name)
        .join(AnnotationModel, AnnotationModel.user_id == UserModel.id)
        .join(models.Host, AnnotationModel.host_id == models.Host.id)
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
    response_model=List[Annotation],
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
    response_model=Annotation,
    summary="Create a note on a host (parses @mentions to notify users)",
    # RV-4 — note writes require ANALYST+; viewers/auditors are read-only.
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def create_host_note(
    host_id: int,
    payload: AnnotationCreate,
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
        # Annotation is a Pydantic model; use model_copy to attach the
        # warning without mutating the schema signature.  Frontend
        # clients that don't know about mention_warning simply ignore
        # the extra field.
        return serialized.model_copy(update={"mention_warning": mention_warning})
    return serialized


@router.patch(
    "/{host_id:int}/notes/{note_id:int}",
    response_model=Annotation,
    summary="Edit a note body (author-only) and/or thread work-state (any project member)",
    # RV-4 — ANALYST+ to mutate; "any project member" in the docstring
    # means any member who can write (ANALYST/ADMIN), not viewers/auditors.
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def update_host_note(
    host_id: int,
    note_id: int,
    payload: AnnotationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """P3 permission split:

    * ``body`` is authored content — only its author may change it.
    * thread work-state (``status``/``assignee_id``/``due_at``/
      ``note_type``/``resolution_summary``/``pinned``) is collaborative —
      any project member may change it, so a teammate can resolve or
      reassign an abandoned thread.  Status changes are recorded in
      ``annotation_status_history`` and resolving requires a summary.

    ``model_fields_set`` distinguishes an omitted field from an explicit
    null (which clears a nullable thread field).
    """
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    provided = payload.model_fields_set
    follow_service = HostFollowService(db)

    # Resolve target + thread root up front so we can validate everything
    # BEFORE mutating, capture the pre-change root status for the status
    # notification, and never partially commit (review #1).
    target = (
        db.query(AnnotationModel)
        .filter(AnnotationModel.id == note_id, AnnotationModel.host_id == host_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Note not found")
    root = follow_service._root_note(target)
    old_status = root.status

    # Validate assignee membership up front (review #3) — mirror host
    # assignment: assignee must be an active user, and (unless a global
    # admin) a member of this project.  null clears the assignee.
    if "assignee_id" in provided and payload.assignee_id is not None:
        assignee = db.query(User).filter(
            User.id == payload.assignee_id, User.is_active.is_(True)
        ).first()
        if not assignee:
            raise HTTPException(status_code=404, detail="Assignee not found")
        if assignee.role != UserRole.ADMIN:
            is_member = db.query(ProjectMembership).filter(
                ProjectMembership.project_id == project.id,
                ProjectMembership.user_id == assignee.id,
            ).first()
            if not is_member:
                raise HTTPException(
                    status_code=400, detail="Assignee is not a member of this project"
                )

    # Collect thread-meta fields (model_fields_set distinguishes omitted
    # from an explicit null that clears a nullable field).
    meta = {}
    if "status" in provided and payload.status is not None:
        meta["status"] = NoteStatus(payload.status.value)
    for key in ("assignee_id", "due_at", "note_type", "resolution_summary", "pinned"):
        if key in provided:
            meta[key] = getattr(payload, key)

    want_body = "body" in provided and payload.body is not None

    # Validate EVERYTHING before mutating anything (review #1) — a rejected
    # field must never leave a committed body edit, and validating up front
    # means the failure path needs no rollback at all.
    if want_body and target.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit a note's body")
    if meta.get("note_type") and meta["note_type"] not in VALID_NOTE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid note_type; expected one of {sorted(VALID_NOTE_TYPES)}",
        )
    if meta.get("status") == NoteStatus.RESOLVED:
        eff_summary = (
            meta["resolution_summary"] if "resolution_summary" in meta
            else root.resolution_summary
        )
        if not (eff_summary and str(eff_summary).strip()):
            raise HTTPException(
                status_code=400, detail="Resolving a thread requires a resolution summary"
            )

    # Apply body (author-only) + thread-meta (any analyst) in ONE
    # transaction; both service calls defer their commit so the PATCH is
    # all-or-nothing.  Validation already passed, so the only failure here
    # is an unexpected DB error.
    # review #3-round #4 — keep the EDITED note (the target reply) distinct
    # from the thread ROOT.  Body edits + @mentions + the response belong to
    # the edited note; status/assignee/etc. + the status notification belong
    # to the root.  Previously a combined reply PATCH reassigned `note` to
    # the root, so mentions parsed the root's body and the response returned
    # the root instead of the edited reply.
    edited_note = target
    body_changed = False
    try:
        if want_body:
            edited_note = follow_service.update_note_body(
                note_id, current_user.id, body=payload.body, host_id=host_id, commit=False,
            )
            body_changed = True
        if meta:
            root = follow_service.update_thread_meta(
                note_id, current_user.id, host_id=host_id, commit=False, **meta,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    new_status = root.status
    status_changed = bool("status" in meta and new_status != old_status)

    # Best-effort notifications in a SEPARATE transaction — the note write
    # already committed, so a notification failure can't lose the edit
    # (audit H3 contract).  Restores in-app status-change notifications to
    # the thread author/participants (review #2), not just the webhook.
    mention_warning: Optional[str] = None
    mention_notifs = []
    try:
        notification_service = NotificationService(db)
        if body_changed and payload.body:
            mention_notifs = notification_service.process_note_mentions(edited_note, current_user, project) or []
        if status_changed:
            notification_service.notify_status_change(
                root,
                old_status.value if hasattr(old_status, "value") else str(old_status),
                new_status.value if hasattr(new_status, "value") else str(new_status),
                current_user, project,
            )
        db.commit()
    except Exception:
        logger.exception(
            "Note update notification processing failed",
            extra={
                "note_id": note_id,
                "host_id": host_id,
                "actor_id": current_user.id,
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
            ns = new_status.value if hasattr(new_status, "value") else str(new_status)
            safe_dispatch(
                db,
                project_id=project.id,
                event="note_status_change",
                title=f"Note thread on {host_label} → {ns}",
                body=(root.resolution_summary or "")[:280],
                context={"host_id": host_id, "note_id": root.id, "status": ns},
            )

    # Response: the edited reply when the body changed (what the client
    # edited); otherwise the thread root (where the metadata lives).
    response_note = edited_note if body_changed else root
    db.refresh(response_note)
    db.refresh(response_note, attribute_names=["author", "assignee"])
    serialized = _serialize_note(response_note)
    if mention_warning:
        return serialized.model_copy(update={"mention_warning": mention_warning})
    return serialized


@router.get(
    "/{host_id:int}/notes/{note_id:int}/history",
    response_model=List[AnnotationStatusHistoryEntry],
    summary="Status-transition history for a note thread",
)
def get_host_note_history(
    host_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    note = (
        db.query(AnnotationModel)
        .filter(AnnotationModel.id == note_id, AnnotationModel.host_id == host_id)
        .first()
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    # review #5 — status history is thread-level; resolve a reply id to its
    # root so requesting history through a reply doesn't return the reply's
    # (empty) history.
    follow_service = HostFollowService(db)
    root_id = note.thread_root_id or follow_service._root_note(note).id
    rows = follow_service.get_status_history(root_id)
    return [
        AnnotationStatusHistoryEntry(
            id=r.id,
            from_status=r.from_status,
            to_status=r.to_status,
            changed_by_id=r.changed_by_id,
            changed_by_name=(
                (r.changed_by.full_name or r.changed_by.username) if r.changed_by else None
            ),
            summary=r.summary,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete(
    "/{host_id:int}/notes/{note_id:int}",
    status_code=204,
    summary="Delete a note",
    # RV-4 — ANALYST+ (author-only enforcement stays in the service).
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
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
    except NoteHasRepliesError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=204)
