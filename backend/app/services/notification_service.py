"""
Notification Service

Handles @mention parsing, notification creation, and delivery for the
pentest coordination platform.
"""

import re
import logging
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.models_project import Notification, NoteMention, Project, ProjectMembership
from app.db.models_auth import User
from app.db.models import HostNote

logger = logging.getLogger(__name__)

# Match @username patterns — alphanumeric + underscores, 1-50 chars
MENTION_PATTERN = re.compile(r"@(\w{1,50})\b")


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def parse_mentions(self, body: str, project_id: int) -> List[User]:
        """Extract @username patterns from ``body`` and resolve to User
        objects whose membership includes ``project_id``.

        Security fix: previously matched any active username globally,
        which leaked Project A context (project_id, host label, note
        body) to a user who only belonged to Project B.  Scoping to
        members of the note's own project closes that authorization
        boundary break.
        """
        if not body:
            return []

        usernames = set(MENTION_PATTERN.findall(body))
        if not usernames:
            return []

        return (
            self.db.query(User)
            .join(ProjectMembership, ProjectMembership.user_id == User.id)
            .filter(
                User.username.in_(usernames),
                User.is_active.is_(True),
                ProjectMembership.project_id == project_id,
            )
            .all()
        )

    def process_note_mentions(
        self,
        note: HostNote,
        actor: User,
        project: Project,
    ) -> List[Notification]:
        """Parse mentions from a note, create NoteMention records, and
        generate notifications for each mentioned user.

        ``project`` is required (was ``Optional`` pre-2.48.2) so mention
        resolution can scope to project membership — see parse_mentions.

        Returns the list of created Notification objects.
        """
        mentioned_users = self.parse_mentions(note.body, project.id)
        if not mentioned_users:
            return []

        notifications = []
        project_id = project.id
        project_name = project.name

        # Get the host IP for context
        host = note.host
        host_label = host.ip_address if host else f"host #{note.host_id}"

        for user in mentioned_users:
            # Don't notify yourself
            if user.id == actor.id:
                continue

            # Create mention record (idempotent)
            existing = self.db.query(NoteMention).filter(
                NoteMention.note_id == note.id,
                NoteMention.user_id == user.id,
            ).first()
            if not existing:
                mention = NoteMention(note_id=note.id, user_id=user.id)
                self.db.add(mention)

            # Create notification
            notification = Notification(
                user_id=user.id,
                project_id=project_id,
                type="mention",
                title=f"@{actor.username} mentioned you on {host_label}",
                body=note.body[:200] if note.body else None,
                source_type="note",
                source_id=note.id,
                actor_id=actor.id,
            )
            self.db.add(notification)
            notifications.append(notification)

        return notifications

    def notify_status_change(
        self,
        note: HostNote,
        old_status: str,
        new_status: str,
        actor: User,
        project: Optional[Project] = None,
    ) -> List[Notification]:
        """Notify note author and thread participants when note status changes."""
        notifications = []
        project_id = project.id if project else None
        host = note.host
        host_label = host.ip_address if host else f"host #{note.host_id}"

        # Collect users to notify: note author + parent note author
        notify_user_ids = set()
        if note.user_id != actor.id:
            notify_user_ids.add(note.user_id)
        if note.parent_id:
            parent = self.db.query(HostNote).filter(HostNote.id == note.parent_id).first()
            if parent and parent.user_id != actor.id:
                notify_user_ids.add(parent.user_id)

        for user_id in notify_user_ids:
            notification = Notification(
                user_id=user_id,
                project_id=project_id,
                type="status_change",
                title=f"Note status changed to {new_status} on {host_label}",
                body=f"@{actor.username} changed status from {old_status} to {new_status}",
                source_type="note",
                source_id=note.id,
                actor_id=actor.id,
            )
            self.db.add(notification)
            notifications.append(notification)

        return notifications

    def notify_project_assignment(
        self,
        user: User,
        project: Project,
        role: str,
        actor: User,
    ) -> Notification:
        """Notify a user when they're added to a project."""
        notification = Notification(
            user_id=user.id,
            project_id=project.id,
            type="assignment",
            title=f"You were added to project '{project.name}'",
            body=f"@{actor.username} added you as {role}",
            source_type="project",
            source_id=project.id,
            actor_id=actor.id,
        )
        self.db.add(notification)
        return notification

    def notify_host_assignment(
        self,
        assignee: User,
        host,
        project: Project,
        actor: User,
    ) -> Optional[Notification]:
        """Notify a user when a host is assigned to them by someone else.

        Self-assignment produces no notification — you don't need a ping
        for work you just gave yourself.
        """
        if assignee.id == actor.id:
            return None
        label = host.hostname or host.ip_address
        notification = Notification(
            user_id=assignee.id,
            project_id=project.id,
            type="assignment",
            title=f"Host {label} assigned to you",
            body=f"@{actor.username} assigned {host.ip_address} to you",
            source_type="host",
            source_id=host.id,
            actor_id=actor.id,
        )
        self.db.add(notification)
        return notification

    def get_notifications(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Notification]:
        """Fetch notifications for a user."""
        query = self.db.query(Notification).filter(Notification.user_id == user_id)
        if unread_only:
            query = query.filter(Notification.is_read == False)
        return query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()

    def get_unread_count(self, user_id: int) -> int:
        """Fast count for notification badge."""
        return self.db.query(func.count(Notification.id)).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,
        ).scalar() or 0

    def mark_read(self, notification_ids: List[int], user_id: int) -> int:
        """Mark specific notifications as read. Returns count updated."""
        now = datetime.utcnow()
        updated = self.db.query(Notification).filter(
            Notification.id.in_(notification_ids),
            Notification.user_id == user_id,
            Notification.is_read == False,
        ).update(
            {"is_read": True, "read_at": now},
            synchronize_session=False,
        )
        return updated

    def mark_all_read(self, user_id: int) -> int:
        """Mark all notifications as read for a user."""
        now = datetime.utcnow()
        updated = self.db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,
        ).update(
            {"is_read": True, "read_at": now},
            synchronize_session=False,
        )
        return updated
