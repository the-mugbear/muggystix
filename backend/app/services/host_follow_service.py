from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.db.models import HostFollow, FollowStatus, HostNote, NoteStatus

logger = logging.getLogger(__name__)


class HostFollowService:
    def __init__(self, db: Session):
        self.db = db

    def get_follow(self, host_id: int, user_id: int) -> Optional[HostFollow]:
        return (
            self.db.query(HostFollow)
            .filter(HostFollow.host_id == host_id, HostFollow.user_id == user_id)
            .first()
        )

    def set_follow_status(self, host_id: int, user_id: int, status: FollowStatus) -> HostFollow:
        follow = self.get_follow(host_id, user_id)
        if follow:
            follow.status = status
        else:
            follow = HostFollow(host_id=host_id, user_id=user_id, status=status)
            self.db.add(follow)
        self.db.commit()
        self.db.refresh(follow)
        return follow

    def assign_host(self, host_id: int, assignee_id: int, assigned_by_id: int) -> HostFollow:
        """Assign a host to ``assignee_id``.

        Upserts the assignee's follow row, stamping ``assigned_at`` /
        ``assigned_by_id`` and bumping status to In Review so the host
        lands in the assignee's My Queue.  The assignee's ``user_id`` IS
        the assignment target — there is no separate assigned_to column.
        """
        from datetime import datetime, timezone

        follow = self.get_follow(host_id, assignee_id)
        now = datetime.now(timezone.utc)
        if follow:
            follow.assigned_by_id = assigned_by_id
            follow.assigned_at = now
            follow.status = FollowStatus.IN_REVIEW
        else:
            follow = HostFollow(
                host_id=host_id,
                user_id=assignee_id,
                status=FollowStatus.IN_REVIEW,
                assigned_by_id=assigned_by_id,
                assigned_at=now,
            )
            self.db.add(follow)
        self.db.commit()
        self.db.refresh(follow)
        return follow

    def unassign_host(self, host_id: int, assignee_id: int) -> Optional[HostFollow]:
        """Clear assignment metadata, keeping the follow row + status.

        Unassigning isn't unfollowing — the assignee may still be
        watching/reviewing of their own accord.
        """
        follow = self.get_follow(host_id, assignee_id)
        if follow and follow.assigned_at is not None:
            follow.assigned_by_id = None
            follow.assigned_at = None
            self.db.commit()
            self.db.refresh(follow)
        return follow

    def record_view(self, host_id: int, user_id: int) -> None:
        """Record that a user viewed a host.

        Only updates `last_viewed_at` on an *existing* follow record;
        does NOT create one as a side effect.  Previously this method
        auto-created a `Watching` follow on every host detail page open,
        which polluted the dashboard "My Queue" widget with hosts the
        user had merely browsed.  The two follow states now have their
        intended meanings:

          - **In Review**  — user is actively working on this host
          - **Watching**   — user has explicitly chosen to follow
                             another teammate's progress on this host

        Side effect of this change: `last_viewed_at` only ticks for
        hosts the user has explicitly followed.  That's a feature —
        the dashboard "viewed" count and the "Viewed Xh ago" inline
        display now reflect deliberate engagement, not accidental
        page-loads.
        """
        follow = self.get_follow(host_id, user_id)
        if follow:
            follow.last_viewed_at = func.now()
            self.db.commit()

    def unfollow(self, host_id: int, user_id: int) -> None:
        follow = self.get_follow(host_id, user_id)
        if follow:
            self.db.delete(follow)
            self.db.commit()

    def list_notes(self, host_id: int, limit: int = 50) -> List[HostNote]:
        return (
            self.db.query(HostNote)
            .filter(HostNote.host_id == host_id)
            .options(selectinload(HostNote.author))
            .order_by(HostNote.created_at.desc())
            .limit(limit)
            .all()
        )

    def create_note(
        self,
        host_id: int,
        user_id: int,
        body: str,
        status: NoteStatus = NoteStatus.OPEN,
        parent_id: Optional[int] = None,
    ) -> HostNote:
        # Security fix: previously trusted ``parent_id`` verbatim, so a
        # note on host A in Project A could be threaded under a note on
        # host B in Project B — every status-change on the child would
        # notify the parent's author across the project boundary.  Same-
        # host check enforces that threading stays within one host (and
        # therefore one project).
        if parent_id is not None:
            parent = (
                self.db.query(HostNote)
                .filter(HostNote.id == parent_id, HostNote.host_id == host_id)
                .first()
            )
            if parent is None:
                raise ValueError("parent_id must reference a note on the same host")
        note = HostNote(host_id=host_id, user_id=user_id, body=body, status=status, parent_id=parent_id)
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        self.db.refresh(note, attribute_names=["author"])
        return note

    def update_note(
        self,
        note_id: int,
        user_id: int,
        body: Optional[str] = None,
        status: Optional[NoteStatus] = None,
        host_id: Optional[int] = None,
    ) -> HostNote:
        note = self.db.query(HostNote).filter(HostNote.id == note_id).first()
        if not note:
            raise ValueError("Note not found")
        if note.user_id != user_id:
            raise PermissionError("Cannot modify another user's note")
        if host_id is not None and note.host_id != host_id:
            raise ValueError("Note not found")
        if body is not None:
            note.body = body
        if status is not None:
            note.status = status
        self.db.commit()
        self.db.refresh(note)
        self.db.refresh(note, attribute_names=["author"])
        return note

    def delete_note(self, note_id: int, user_id: int, host_id: Optional[int] = None) -> None:
        note = self.db.query(HostNote).filter(HostNote.id == note_id).first()
        if not note:
            raise ValueError("Note not found")
        if note.user_id != user_id:
            raise PermissionError("Cannot delete another user's note")
        if host_id is not None and note.host_id != host_id:
            raise ValueError("Note not found")
        self.db.delete(note)
        self.db.commit()

    def get_dashboard_activity(self, user_id: int, limit: int = 5, project_id: int = None) -> Dict[str, object]:
        """Return recent note activity and follow counts for dashboard display,
        optionally scoped to a project."""
        note_query = self.db.query(func.count(HostNote.id)).filter(HostNote.user_id == user_id)
        if project_id is not None:
            note_query = note_query.join(models.Host, HostNote.host_id == models.Host.id).filter(models.Host.project_id == project_id)
        total_notes = note_query.scalar() or 0

        follow_query = self.db.query(func.count(HostFollow.id)).filter(HostFollow.user_id == user_id)
        if project_id is not None:
            follow_query = follow_query.join(models.Host, HostFollow.host_id == models.Host.id).filter(models.Host.project_id == project_id)
        follows_count = follow_query.scalar() or 0

        notes_query = (
            self.db.query(HostNote)
            .filter(HostNote.user_id == user_id)
            .options(selectinload(HostNote.host))
        )
        if project_id is not None:
            notes_query = notes_query.join(models.Host, HostNote.host_id == models.Host.id).filter(models.Host.project_id == project_id)
        notes = (
            notes_query
            .order_by(func.coalesce(HostNote.updated_at, HostNote.created_at).desc())
            .limit(limit)
            .all()
        )

        active_host_ids = {note.host_id for note in notes}

        recent_notes = []
        for note in notes:
            host = note.host
            recent_notes.append(
                {
                    "note_id": note.id,
                    "host_id": note.host_id,
                    "ip_address": host.ip_address if host else "unknown",
                    "hostname": host.hostname if host else None,
                    "status": note.status,
                    "preview": (note.body[:140] + "…") if len(note.body) > 140 else note.body,
                    "created_at": note.created_at,
                    "updated_at": note.updated_at,
                }
            )

        # Review progress: count hosts in each follow stage for this user,
        # scoped to the current project.
        host_count_query = self.db.query(func.count(models.Host.id))
        if project_id is not None:
            host_count_query = host_count_query.filter(models.Host.project_id == project_id)
        total_hosts = host_count_query.scalar() or 0

        status_query = (
            self.db.query(HostFollow.status, func.count(HostFollow.id))
            .filter(HostFollow.user_id == user_id)
        )
        if project_id is not None:
            status_query = status_query.join(models.Host, HostFollow.host_id == models.Host.id).filter(models.Host.project_id == project_id)
        status_rows = status_query.group_by(HostFollow.status).all()
        status_counts = {row[0]: row[1] for row in status_rows}
        watching = status_counts.get(FollowStatus.WATCHING, 0)
        in_review = status_counts.get(FollowStatus.IN_REVIEW, 0)
        reviewed = status_counts.get(FollowStatus.REVIEWED, 0)
        not_reviewed = total_hosts - (watching + in_review + reviewed)

        return {
            "total_notes": total_notes,
            "active_host_count": len(active_host_ids),
            "following_count": follows_count,
            "review_progress": {
                "total_hosts": total_hosts,
                "not_reviewed": not_reviewed,
                "watching": watching,
                "in_review": in_review,
                "reviewed": reviewed,
            },
            "recent_notes": recent_notes,
        }
