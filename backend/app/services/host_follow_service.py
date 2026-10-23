from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import (
    HostFollow, FollowStatus, Annotation,
)

logger = logging.getLogger(__name__)

# Sentinel so thread-meta updates can distinguish "field omitted" from
# "field explicitly set to None" (e.g. clearing an assignee or due date).
_UNSET = object()


class NoteHasRepliesError(Exception):
    """Raised when deleting a note that still has replies/thread members —
    the self-FKs (parent_id, thread_root_id) have no ON DELETE, so the DB
    would reject it.  The endpoint maps this to HTTP 409 (review #3)."""

# Thread-level work fields that any project member may edit (vs body /
# delete, which stay author-only).
# What a thread can be labelled.  "finding" and "action" went in v2.447.0 —
# a note is neither; rows that already carry them keep the label.
VALID_NOTE_TYPES = {"observation", "question", "decision", "handoff"}

#: What a note's author is told when the note was saved and the telling failed.
NOTE_NOTIFY_FAILED = (
    "Note saved, but mention notifications could not be delivered. "
    "Tagged users may not have been alerted; contact an admin if "
    "this persists."
)


class HostFollowService:
    def __init__(self, db: Session):
        self.db = db

    def get_follow(self, host_id: int, user_id: int) -> Optional[HostFollow]:
        return (
            self.db.query(HostFollow)
            .filter(HostFollow.host_id == host_id, HostFollow.user_id == user_id)
            .first()
        )

    @staticmethod
    def apply_review_status(follow: HostFollow, status: FollowStatus, user_id: int) -> None:
        """What setting a review status does to ``user_id``'s row — the ONE
        implementation, for one host (``set_follow_status``: the page's control,
        an agent) and for many (``POST /hosts/bulk/follow``).  The caller
        commits.

        Finishing a review records no conclusion (owner, 2026-10-10: one click,
        like starting one).  ``review_conclusion`` is never SET; a row that
        carries one from before keeps it while it stays Reviewed — marking it
        Reviewed again leaves the conclusion and the note alone — and loses
        both when the review is re-opened, which is the reviewer saying it no
        longer stands.
        """
        follow.status = status
        # Taking a host into review makes that user its reviewer of record —
        # which operators read as "this host is mine".  Stamp ownership so the
        # "Assigned to me" filter (keyed on assigned_at) reflects it, mirroring
        # the Assign action which already sets both.  Don't clobber an existing
        # assignment; unassign clears assigned_at independently.
        status_value = getattr(status, "value", status)
        if status_value in ("in_review", "reviewed") and follow.assigned_at is None:
            follow.assigned_at = func.now()
            follow.assigned_by_id = user_id
        if status_value == "reviewed":
            # The baseline "changed since review" is measured against.
            follow.reviewed_at = datetime.now(timezone.utc)
        else:
            follow.review_conclusion = None
            follow.review_summary = None
            follow.reviewed_at = None

    def set_follow_status(self, host_id: int, user_id: int, status: FollowStatus) -> HostFollow:
        follow = self.get_follow(host_id, user_id)
        if not follow:
            follow = HostFollow(host_id=host_id, user_id=user_id, status=status)
            self.db.add(follow)
        self.apply_review_status(follow, status, user_id)
        self.db.commit()
        self.db.refresh(follow)
        return follow

    def set_review_note(self, host_id: int, user_id: int, note: Optional[str]) -> Optional[HostFollow]:
        """The reviewer's optional note on their OWN finished review of a host
        (``host_follows.review_summary``); ``None`` removes it.  Nothing else
        moves — not ``reviewed_at``, so writing a note never takes a host out
        of "Changed since review".  Returns ``None`` when ``user_id`` has no
        finished review of the host: a note is on a review, and there is none.
        """
        follow = self.get_follow(host_id, user_id)
        if not follow or getattr(follow.status, "value", follow.status) != FollowStatus.REVIEWED.value:
            return None
        follow.review_summary = note
        self.db.commit()
        self.db.refresh(follow)
        return follow

    def restamp_reviews(self, project_id: int, user_id: int, host_ids: List[int]) -> List[int]:
        """"Still reviewed" (v2.450.0): the reviewer looked at what changed
        after their review and the review stands — move ``reviewed_at`` to
        now on ``user_id``'s OWN finished reviews of these hosts, leaving the
        note (and a conclusion recorded before they were retired) as it is.
        The baseline "changed since review" is measured against moves; nothing
        else does.

        All or nothing, in ONE statement: the conditions are in the UPDATE's
        WHERE, and a host that did not qualify — not in the project, or no
        finished review of the caller's — rolls the whole call back.  Returns
        the host ids that could NOT be stamped; empty means every one was.
        """
        wanted = list(dict.fromkeys(host_ids))
        in_project = (
            self.db.query(models.Host.id)
            .filter(models.Host.id.in_(wanted), models.Host.project_id == project_id)
        )
        stamped = self.db.execute(
            update(HostFollow)
            .where(
                HostFollow.user_id == user_id,
                HostFollow.host_id.in_(in_project),
                HostFollow.status == FollowStatus.REVIEWED,
            )
            .values(reviewed_at=datetime.now(timezone.utc))
            .returning(HostFollow.host_id)
            .execution_options(synchronize_session=False)
        ).scalars().all()
        refused = sorted(set(wanted) - set(stamped))
        if refused:
            self.db.rollback()
            return refused
        self.db.commit()
        return []

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

    def list_notes(self, host_id: int, limit: int = 50) -> List[Annotation]:
        # v2.369.1 — every relationship the serializer reads, not just the
        # author: this feeds the host detail, where ``promoted_findings``
        # (one-to-many) cost one query PER NOTE on every host open.
        from app.services.host_serialization import note_load_options
        return (
            self.db.query(Annotation)
            .filter(Annotation.host_id == host_id)
            .options(*note_load_options())
            .order_by(Annotation.created_at.desc())
            .limit(limit)
            .all()
        )

    def create_note(
        self,
        host_id: int,
        user_id: int,
        body: str,
        parent_id: Optional[int] = None,
        actor_type: str = "user",
        agent_session_id: Optional[int] = None,
    ) -> Annotation:
        # Security fix: previously trusted ``parent_id`` verbatim, so a
        # note on host A in Project A could be threaded under a note on
        # host B in Project B — a reply there would notify that thread's
        # writers across the project boundary.  Same-
        # host check enforces that threading stays within one host (and
        # therefore one project).
        parent = None
        if parent_id is not None:
            parent = (
                self.db.query(Annotation)
                .filter(Annotation.id == parent_id, Annotation.host_id == host_id)
                .first()
            )
            if parent is None:
                raise ValueError("parent_id must reference a note on the same host")
        # ``user_id`` is the operator either way; ``actor_type`` records whether
        # they typed it or an agent wrote it on their behalf.
        #
        # NOTE: do NOT also set ``project_id`` here. It is one of seven
        # mutually exclusive *targets* (``ck_annotations_exactly_one_target``),
        # not a denormalised scope column — a project-targeted note is a note on
        # the project itself. Setting it alongside ``host_id`` violates the
        # CHECK. Project scope is reached through the target; see
        # ``list_assist_recent_notes``.
        note = Annotation(
            host_id=host_id,
            user_id=user_id,
            body=body,
            parent_id=parent_id,
            actor_type=actor_type,
            agent_session_id=agent_session_id,
        )
        self.db.add(note)
        self.db.flush()  # assign note.id before stamping thread_root_id
        # Persist the thread root (review #5): a root note points at itself;
        # a reply inherits its parent's root.
        note.thread_root_id = parent.thread_root_id or parent.id if parent else note.id
        self.db.commit()
        self.db.refresh(note)
        self.db.refresh(note, attribute_names=["author"])
        return note

    def post_note(
        self,
        host: "models.Host",
        author,
        project,
        body: str,
        *,
        parent_id: Optional[int] = None,
        actor_type: str = "user",
        agent_session_id: Optional[int] = None,
    ) -> "tuple[Annotation, dict]":
        """Write a note on ``host`` and tell the people it concerns — the ONE
        path for a person's note (``POST /hosts/{id}/notes``) and an agent's
        (``POST /agent/hosts/{id}/notes``).  An agent's note is its operator's
        (``author`` is the operator), so the operator is never told about it
        and everybody else is told exactly as for a note the operator typed
        (until 2.489.0 only the page's route notified; an agent's ``@mention``
        reached nobody).

        The note commits first (``create_note``); the telling is best-effort in
        a second transaction, so a failure there never loses the note:

        1. the ``@mentioned`` members (and the ``note_mention`` webhook),
        2. on a reply, everyone already in the thread,
        3. everyone reviewing the host —

        each step skipping those already told.  Returns ``(note, fields)``
        where ``fields`` is what the author is shown: ``mentions_notified`` /
        ``unmatched_mentions``, or ``mention_warning`` when the telling failed.
        Raises ``ValueError`` for a ``parent_id`` that is not a note on this
        host.
        """
        from app.services.notification_service import NotificationService
        from app.services.webhook_dispatcher import stage_dispatch

        note = self.create_note(
            host.id, author.id, body,
            parent_id=parent_id, actor_type=actor_type, agent_session_id=agent_session_id,
        )
        # Read before anything below can roll back and expire them.
        note_id, host_id, project_id, author_id = note.id, host.id, project.id, author.id
        try:
            notifications = NotificationService(self.db)
            mentions = notifications.process_note_mentions(note, author, project) or []
            fields = notifications.mention_outcome(note.body, project_id, mentions)
            told = {n.user_id for n in mentions}
            told |= {
                n.user_id for n in notifications.notify_discussion_participants(
                    note, author, project, exclude_user_ids=told,
                )
            }
            notifications.notify_host_followers_of_note(
                note, author, project, exclude_user_ids=told,
            )
            # Staged INSIDE this transaction: the outbox row and the
            # notifications are atomic, the POST is fired after the commit, and
            # the rollback below discards the intent along with the rest.
            if mentions:
                stage_dispatch(
                    self.db,
                    project_id=project_id,
                    event="note_mention",
                    title=(
                        f"@{author.username} mentioned {len(mentions)} user(s) on "
                        f"{host.hostname or host.ip_address}"
                    ),
                    body=(body or "")[:280],
                    context={"host_id": host_id, "note_id": note_id},
                )
            self.db.commit()
        except Exception:
            logger.exception(
                "Mention processing failed",
                extra={
                    "note_id": note_id,
                    "host_id": host_id,
                    "author_id": author_id,
                    "project_id": project_id,
                },
            )
            fields = {"mention_warning": NOTE_NOTIFY_FAILED}
            self.db.rollback()
        return note, fields

    def _root_note(self, note: Annotation) -> Annotation:
        """Walk parent_id to the thread root (cycle-guarded)."""
        current = note
        seen = {note.id}
        while current.parent_id is not None:
            parent = (
                self.db.query(Annotation)
                .filter(Annotation.id == current.parent_id)
                .first()
            )
            if parent is None or parent.id in seen:
                break
            current = parent
            seen.add(current.id)
        return current

    def update_note_body(
        self,
        note_id: int,
        user_id: int,
        body: str,
        host_id: Optional[int] = None,
        commit: bool = True,
    ) -> Annotation:
        """Edit a note's body — AUTHOR ONLY (P3 permission split).

        Body is authored content; only its writer may change it.  Thread
        work-state (status/assignee/resolution/…) is a separate concern
        handled by ``update_thread_meta`` and is open to any project member.

        ``commit=False`` applies the change to the session WITHOUT
        committing, so the endpoint can apply body + thread-meta in ONE
        transaction (avoids the partial-commit-then-400 bug — review #1).
        """
        note = self.db.query(Annotation).filter(Annotation.id == note_id).first()
        if not note:
            raise ValueError("Note not found")
        if note.user_id != user_id:
            raise PermissionError("Cannot modify another user's note")
        if host_id is not None and note.host_id != host_id:
            raise ValueError("Note not found")
        note.body = body
        if commit:
            self.db.commit()
            self.db.refresh(note)
            self.db.refresh(note, attribute_names=["author"])
        return note

    def update_thread_meta(
        self,
        note_id: int,
        actor_id: int,
        host_id: Optional[int] = None,
        *,
        note_type=_UNSET,
        pinned=_UNSET,
        commit: bool = True,
    ) -> Annotation:
        """Set a thread's labels — its type and whether it is pinned — on the
        thread ROOT; any analyst may.  Fields left as ``_UNSET`` are untouched;
        ``note_type=None`` clears it.  ``commit=False`` defers the commit to
        the caller for one-transaction PATCHes (review #1)."""
        target = self.db.query(Annotation).filter(Annotation.id == note_id).first()
        if not target:
            raise ValueError("Note not found")
        if host_id is not None and target.host_id != host_id:
            raise ValueError("Note not found")
        note = self._root_note(target)

        if note_type is not _UNSET and note_type is not None and note_type not in VALID_NOTE_TYPES:
            raise ValueError(f"Invalid note_type; expected one of {sorted(VALID_NOTE_TYPES)}")
        if note_type is not _UNSET:
            note.note_type = note_type
        if pinned is not _UNSET:
            note.pinned = bool(pinned)

        if commit:
            self.db.commit()
            self.db.refresh(note)
            self.db.refresh(note, attribute_names=["author"])
        return note

    def delete_note(self, note_id: int, user_id: int, host_id: Optional[int] = None) -> None:
        note = self.db.query(Annotation).filter(Annotation.id == note_id).first()
        if not note:
            raise ValueError("Note not found")
        if note.user_id != user_id:
            raise PermissionError("Cannot delete another user's note")
        if host_id is not None and note.host_id != host_id:
            raise ValueError("Note not found")
        # review #3 — replies reference this note via parent_id AND
        # thread_root_id (both self-FKs with no ON DELETE), so deleting a
        # note that still has descendants would raise a raw DB FK error.
        # Reject cleanly; the operator must resolve/delete replies first.
        has_replies = (
            self.db.query(Annotation.id)
            .filter(
                Annotation.id != note.id,
                or_(Annotation.parent_id == note.id, Annotation.thread_root_id == note.id),
            )
            .first()
        )
        if has_replies:
            raise NoteHasRepliesError(
                "This note still has replies; delete or move them first."
            )
        # The note's images go with it.  One that a finding's report text
        # places stays until the reference is removed (409, naming the
        # section) — the same refusal as deleting the image itself.
        from app.db.models import NoteAttachment
        from app.services import report_images
        image_ids = [
            aid for (aid,) in
            self.db.query(NoteAttachment.id).filter(NoteAttachment.annotation_id == note.id)
        ]
        if image_ids:
            report_images.refuse_if_placed(
                self.db, image_ids, action="delete this comment", then="delete it",
            )
        nid = note.id
        self.db.delete(note)
        self.db.commit()
        # R6: the NoteAttachment rows cascaded with the note — drop their files too.
        from app.services.note_attachment_service import purge_note_files
        purge_note_files(nid)
