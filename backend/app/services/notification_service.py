"""
Notification Service

Handles @mention parsing, notification creation, and delivery for the
pentest coordination platform.
"""

import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.db.models_project import (
    Notification, NoteMention, Project, ProjectMembership,
)
from app.db.models_auth import User
from app.db.models import Annotation
from app.schemas.schemas import MentionNotified

logger = logging.getLogger(__name__)

#: ``notifications.type`` of a remediation deadline alert
#: (``remediation_alerts``) — shown only while remediation tracking is on.
REMEDIATION_NOTIFICATION_TYPE = "remediation"


def _continues_name(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _mention_token(body: str, start: int) -> str:
    """The ``@word`` written at ``start`` (just after the ``@``): name
    characters, with a ``.`` or ``-`` kept only when a name character follows
    it — the same boundary a match must end at, so ``@eval-ana.`` reads
    ``eval-ana``."""
    end = start
    while end < len(body):
        ch = body[end]
        if _continues_name(ch):
            end += 1
        elif ch in ".-" and end + 1 < len(body) and _continues_name(body[end + 1]):
            end += 1
        else:
            break
    return body[start:end]


def scan_mentions(body: str, usernames: Iterable[str]) -> Tuple[Set[str], List[str]]:
    """``(matched usernames, unmatched @words)`` for ``body``.

    Matching is ``find_mentions``'s rule.  An ``@`` in mention position
    (not inside a word) where NO known username matches reports the word
    written after it (see ``_mention_token``) as unmatched — conservatively:
    a bare ``@`` or ``@`` before punctuation reports nothing, and each word is
    reported once (case-insensitively), in order.  The frontend twin is
    ``unmatchedMentionTokens`` in ``utils/mentions.ts``.
    """
    if not body or "@" not in body:
        return set(), []
    names = sorted({u for u in usernames if u}, key=len, reverse=True)
    lowered = [(n, n.lower()) for n in names]
    low = body.lower()
    found: Set[str] = set()
    unmatched: List[str] = []
    seen_unmatched: Set[str] = set()
    at = body.find("@")
    while at != -1:
        prev = body[at - 1] if at > 0 else ""
        if not (prev and (_continues_name(prev) or prev in ".-")):
            hit = False
            for name, lname in lowered:
                if not low.startswith(lname, at + 1):
                    continue
                end = at + 1 + len(lname)
                nxt = body[end] if end < len(body) else ""
                after = body[end + 1] if end + 1 < len(body) else ""
                if _continues_name(nxt) or (nxt in (".", "-") and _continues_name(after)):
                    continue
                found.add(name)
                hit = True
                break
            if not hit:
                token = _mention_token(body, at + 1)
                if token and token.lower() not in seen_unmatched:
                    seen_unmatched.add(token.lower())
                    unmatched.append(token)
        at = body.find("@", at + 1)
    return found, unmatched


def find_mentions(body: str, usernames: Iterable[str]) -> Set[str]:
    """The usernames ``body`` @mentions, matched against a KNOWN list.

    A pattern like ``@(\\w+)`` cannot be used: usernames are free text
    (``eval-ana``, ``j.smith``), and ``\\w`` stops at the hyphen, so
    ``@eval-ana`` resolved to ``eval`` and notified nobody.  Instead each
    ``@`` is compared with the candidate names, longest first,
    case-insensitively.  A match must end at a boundary: ``@ana`` does not
    match inside ``@anabel`` or ``@ana-maria``, but does before punctuation
    (``@ana.``, ``@ana,``).  An ``@`` inside a word (an e-mail address) is
    not a mention.
    """
    return scan_mentions(body, usernames)[0]


def display_name(user: Optional[User]) -> str:
    """How a person is named in a notification: their full name, else their
    username (v2.404.0 — titles read "Ana Ortiz mentioned you", not
    "@eval-ana mentioned you")."""
    if user is None:
        return "Someone"
    return (user.full_name or "").strip() or user.username


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def parse_mentions(self, body: str, project_id: int) -> List[User]:
        """Resolve the @mentions in ``body`` to active members of
        ``project_id`` (see ``find_mentions`` for the matching rule).

        Security fix: previously matched any active username globally,
        which leaked Project A context (project_id, host label, note
        body) to a user who only belonged to Project B.  Only members of
        the note's own project are candidates.
        """
        if not body or "@" not in body:
            return []

        members = self._active_members(project_id)
        names = find_mentions(body, (u.username for u in members))
        return [u for u in members if u.username in names]

    def _active_members(self, project_id: int) -> List[User]:
        return (
            self.db.query(User)
            .join(ProjectMembership, ProjectMembership.user_id == User.id)
            .filter(
                User.is_active.is_(True),
                ProjectMembership.project_id == project_id,
            )
            .all()
        )

    def current_members(self, project_id: int, user_ids: Iterable[int]) -> Set[int]:
        """Those of ``user_ids`` who are active and hold a membership of the
        project NOW — the one recipient filter for notifications sent to
        people because of something they did earlier (wrote in a thread,
        follow a host, authored or own a finding).  A global admin with no
        membership row is not a member here."""
        ids = set(user_ids)
        if not ids:
            return set()
        return {
            uid for (uid,) in self.db.query(ProjectMembership.user_id)
            .join(User, User.id == ProjectMembership.user_id)
            .filter(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id.in_(ids),
                User.is_active.is_(True),
            ).all()
        }

    def mention_outcome(
        self, body: Optional[str], project_id: int, notifications: Iterable[Notification],
    ) -> dict:
        """What a note's author is told after posting (v2.404.0): who the
        mention ``notifications`` just reached (``mentions_notified``: username
        + display name) and the ``@words`` that matched no active member of
        the project (``unmatched_mentions``, see ``scan_mentions``) — so a
        mention that reaches nobody is no longer silent.  A member mentioned
        but not notified (the author, or someone this note already told) is in
        neither list."""
        notified_ids = {n.user_id for n in notifications}
        if not body or "@" not in body:
            return {"mentions_notified": [], "unmatched_mentions": []}
        members = self._active_members(project_id)
        _, unmatched = scan_mentions(body, (u.username for u in members))
        notified = sorted(
            (
                MentionNotified(username=u.username, name=display_name(u))
                for u in members if u.id in notified_ids
            ),
            key=lambda m: m.name.lower(),
        )
        return {"mentions_notified": notified, "unmatched_mentions": unmatched}

    def _note_context(self, note: Annotation) -> dict:
        """Where a note lives, for a notification: the label a title reads
        and the ids the Activity page deep-links with."""
        if note.finding_id is not None:
            finding = note.finding
            title = finding.title if finding is not None else f"finding #{note.finding_id}"
            if len(title) > 80:
                title = title[:79] + "…"
            return {"label": f"finding “{title}”", "host_id": None, "finding_id": note.finding_id}
        host = note.host
        label = host.ip_address if host else f"host #{note.host_id}"
        return {"label": label, "host_id": note.host_id, "finding_id": None}

    def process_note_mentions(
        self,
        note: Annotation,
        actor: User,
        project: Project,
    ) -> List[Notification]:
        """Parse mentions from a note (on a host or a finding), record them,
        and notify each mentioned user.

        ``project`` is required (was ``Optional`` pre-2.48.2) so mention
        resolution can scope to project membership — see parse_mentions.

        Only a user NEWLY mentioned in this note is notified: an edit that
        keeps an existing @mention does not ping that user again (the
        ``note_mentions`` row is the record that they were told).

        Returns the list of created Notification objects.
        """
        mentioned_users = self.parse_mentions(note.body, project.id)
        if not mentioned_users:
            return []

        already = {
            uid for (uid,) in self.db.query(NoteMention.user_id)
            .filter(NoteMention.note_id == note.id).all()
        }
        ctx = self._note_context(note)
        notifications = []
        for user in mentioned_users:
            # Don't notify yourself, nor anyone this note already told.
            if user.id == actor.id or user.id in already:
                continue
            self.db.add(NoteMention(note_id=note.id, user_id=user.id))
            notification = Notification(
                user_id=user.id,
                project_id=project.id,
                type="mention",
                title=f"{display_name(actor)} mentioned you on {ctx['label']}"[:255],
                body=note.body[:200] if note.body else None,
                source_type="note",
                source_id=note.id,
                host_id=ctx["host_id"],
                finding_id=ctx["finding_id"],
                actor_id=actor.id,
            )
            self.db.add(notification)
            notifications.append(notification)

        return notifications

    def notify_discussion_participants(
        self,
        note: Annotation,
        actor: User,
        project: Project,
        exclude_user_ids: Optional[set] = None,
    ) -> List[Notification]:
        """Tell the people already in a conversation that it moved on, so a
        reply reaches them without an @mention.

        - A comment on a FINDING goes to everyone who has commented on that
          finding, its author (``created_by_id``) and its owner — the finding
          page shows one conversation, so its participants are the whole
          thread.
        - A REPLY to a host note goes to everyone who has written in that
          thread.  A new top-level host note has no thread yet; the host's
          reviewers hear about it through ``notify_host_followers_of_note``.

        Skips the author and ``exclude_user_ids`` (those @mentioned, who were
        already told), and anyone no longer an active project member.
        """
        exclude = set(exclude_user_ids or set())
        exclude.add(actor.id)

        if note.finding_id is not None:
            recipients = {
                uid for (uid,) in self.db.query(Annotation.user_id)
                .filter(Annotation.finding_id == note.finding_id, Annotation.user_id.isnot(None))
                .distinct().all()
            }
            finding = note.finding
            if finding is not None:
                recipients.update(u for u in (finding.created_by_id, finding.owner_id) if u)
            ntype = "finding_comment"
        elif note.parent_id is not None:
            root_id = note.thread_root_id or note.parent_id
            recipients = {
                uid for (uid,) in self.db.query(Annotation.user_id)
                .filter(
                    (Annotation.thread_root_id == root_id) | (Annotation.id == root_id),
                    Annotation.user_id.isnot(None),
                )
                .distinct().all()
            }
            ntype = "note_reply"
        else:
            return []

        recipients -= exclude
        if not recipients:
            return []
        # Only current, active members of this project: someone removed from
        # the project must not keep receiving its note bodies.
        recipients = self.current_members(project.id, recipients)

        ctx = self._note_context(note)
        verb = "replied" if note.parent_id is not None else "commented"
        notifications = []
        for uid in recipients:
            notification = Notification(
                user_id=uid,
                project_id=project.id,
                type=ntype,
                title=f"{display_name(actor)} {verb} on {ctx['label']}"[:255],
                body=note.body[:200] if note.body else None,
                source_type="note",
                source_id=note.id,
                host_id=ctx["host_id"],
                finding_id=ctx["finding_id"],
                actor_id=actor.id,
            )
            self.db.add(notification)
            notifications.append(notification)
        return notifications

    def notify_host_followers_of_note(
        self,
        note: Annotation,
        actor: User,
        project: Project,
        exclude_user_ids: Optional[set] = None,
    ) -> List[Notification]:
        """Notify everyone reviewing / having reviewed this host that a new
        note landed on it — so a host you own doesn't change under you without
        you knowing.  Skips the author and anyone already notified another way
        (e.g. @mentioned), so a teammate isn't pinged twice for one note."""
        from app.db.models import HostFollow, FollowStatus

        exclude = set(exclude_user_ids or set())
        exclude.add(actor.id)
        follower_ids = {
            uid for (uid,) in (
                self.db.query(HostFollow.user_id)
                .filter(
                    HostFollow.host_id == note.host_id,
                    HostFollow.status.in_(
                        [FollowStatus.IN_REVIEW.value, FollowStatus.REVIEWED.value]
                    ),
                )
                .distinct()
                .all()
            )
        } - exclude
        if not follower_ids:
            return []
        # Only current, active members of this project — the rule
        # ``notify_discussion_participants`` applies (review 2026-10-01 R12).
        # A follow row outlives a membership, so someone removed from the
        # project kept receiving the body of every new note on hosts they had
        # reviewed.
        follower_ids = self.current_members(project.id, follower_ids)
        if not follower_ids:
            return []

        host = note.host
        host_label = host.ip_address if host else f"host #{note.host_id}"
        notifications = []
        for uid in follower_ids:
            notification = Notification(
                user_id=uid,
                project_id=project.id,
                type="host_note",
                title=f"{display_name(actor)} added a note on {host_label}"[:255],
                body=note.body[:200] if note.body else None,
                source_type="note",
                source_id=note.id,
                host_id=note.host_id,
                actor_id=actor.id,
            )
            self.db.add(notification)
            notifications.append(notification)
        return notifications

    def notify_followers_of_scan_update(
        self,
        scan_id: int,
        actor_id: Optional[int],
    ) -> List[Notification]:
        """Alert reviewers that hosts they're reviewing got new scan evidence.

        A scan that RE-OBSERVES an already-known host (host_scan_history
        .host_created = False) means the data changed under whoever's reviewing
        it.  One summary notification per follower per scan (not per host) so a
        big re-scan can't spam.  Skips the uploader.  Best-effort — the caller
        wraps this so a notification error never fails the ingest.

        Only people who are on the project NOW are told (``current_members``,
        the rule of the two notifications above): a review row outlives its
        reviewer's membership, and someone removed from the project was still
        sent the scan's file name.  One grouped statement over the scan's own
        history — the re-observed hosts are never brought back to be bound as
        literals (a re-scan of a large project re-observes tens of thousands)."""
        from app.db.models import HostScanHistory, HostFollow, FollowStatus, Scan

        scan = self.db.get(Scan, scan_id)
        if scan is None:
            return []
        conditions = [
            HostScanHistory.scan_id == scan_id,
            HostScanHistory.host_created.is_(False),
            HostFollow.status.in_([FollowStatus.IN_REVIEW.value, FollowStatus.REVIEWED.value]),
        ]
        if actor_id is not None:
            conditions.append(HostFollow.user_id != actor_id)
        # Per reviewer: how many of their hosts the scan re-observed, and one
        # of them (the link, when it is the only one).
        per_user = {
            uid: (int(n), one_host)
            for uid, n, one_host in (
                self.db.query(
                    HostFollow.user_id,
                    func.count(func.distinct(HostFollow.host_id)),
                    func.min(HostFollow.host_id),
                )
                .join(HostScanHistory, HostScanHistory.host_id == HostFollow.host_id)
                .filter(*conditions)
                .group_by(HostFollow.user_id)
            )
        }
        members = self.current_members(scan.project_id, per_user)
        scan_label = scan.filename or f"scan #{scan_id}"
        project_id = scan.project_id
        notifications = []
        for uid in sorted(members):
            n, one_host = per_user[uid]
            notification = Notification(
                user_id=uid,
                project_id=project_id,
                type="scan_update",
                title=f"{n} host{'s' if n != 1 else ''} you're reviewing got new scan data",
                body=f"Updated by {scan_label}",
                source_type="scan",
                source_id=scan_id,
                # Single-host updates deep-link to that host; multi-host land on
                # the scan's host list.
                host_id=(one_host if n == 1 else None),
                actor_id=actor_id,
            )
            self.db.add(notification)
            notifications.append(notification)
        return notifications

    def notify_queue_unhealthy(self, queue_name: str, failed_count: int) -> List[Notification]:
        """Alert global admins that the job reaper PERMANENTLY failed jobs it
        couldn't recover (retry cap exceeded or the uploaded file went missing).

        This is the actionable tail of the silent-worker incident: routine
        crash-requeues self-heal and stay quiet, but a permanent failure needs a
        human.  Deployment-wide (``project_id`` null, ``type='system'``); fired
        once per reap cycle, and a failed job leaves the reaper's scan set so it
        never re-alerts.  Best-effort — the caller wraps it.
        """
        from app.services.operational_alerts import _global_admin_ids

        notifications = []
        for uid in _global_admin_ids(self.db):
            notification = Notification(
                user_id=uid,
                project_id=None,
                type="system",
                title=f"{queue_name} queue: {failed_count} job(s) could not be recovered",
                body=(
                    "The reaper permanently failed these jobs (retry cap exceeded "
                    "or the uploaded file is missing). Check the failed queue."
                ),
                source_type="ingestion_jobs",
                source_id=None,
                actor_id=None,
            )
            self.db.add(notification)
            notifications.append(notification)
        return notifications

    def _retest_recipients(self, finding, actor_id: Optional[int], members: Dict[int, set]) -> set:
        """Who is told that hosts of ``finding`` await a retest: the
        finding's owner, or — "When there's no owner notify all project
        members" — every active member; never the person who asked.
        ``members`` caches each project's active members for one action."""
        if finding.owner_id is not None and finding.owner_id == actor_id:
            # The owner asked for it themself: there is nobody to tell (the
            # walkthrough found a global-admin owner — no membership row —
            # read as "no owner", and the whole project was told).
            return set()
        recipients = (self.current_members(finding.project_id, {finding.owner_id})
                      if finding.owner_id is not None else set())
        if not recipients:
            # No owner — or one who can no longer be told (left the project,
            # deactivated, a global admin with no membership): every member,
            # so the retest is not lost.
            if finding.project_id not in members:
                members[finding.project_id] = {u.id for u in self._active_members(finding.project_id)}
            recipients = set(members[finding.project_id])
        recipients.discard(actor_id)
        return recipients

    def notify_retests_requested(self, pending, actor_id: Optional[int]) -> List[Notification]:
        """Hosts now await a retest (2.501.0; owner, 2026-10-14) — ``pending``
        is one action's ``(finding, rows)`` pairs.  ONE notification per
        RECIPIENT for the action (third review 2026-10-15, N13: a bulk
        "Reported fixed" over 100 unowned findings sent every member 100):

        * told about one finding: ``source_type: finding_retest``,
          ``source_id`` the first endpoint, ``finding_id`` set — the link opens
          the finding at that endpoint; ``host_id`` when one host is named;
        * told about several: ``source_type: finding_retests``, no finding —
          the link opens the project's retest list (``host_state=retest``),
          and the text says how many findings and hosts.

        ``type: retest``.  The caller commits."""
        members: Dict[int, set] = {}
        by_recipient: Dict[tuple, list] = {}
        for finding, rows in pending:
            if not rows:
                continue
            for uid in self._retest_recipients(finding, actor_id, members):
                by_recipient.setdefault((uid, finding.project_id), []).append((finding, rows))
        if not by_recipient:
            return []
        actor = self.db.get(User, actor_id) if actor_id else None
        asked = f" — asked by {display_name(actor)}" if actor is not None else ""
        out = []
        for (uid, project_id), items in sorted(by_recipient.items(), key=lambda kv: kv[0]):
            if len(items) == 1:
                finding, rows = items[0]
                labels = [r.host.ip_address if r.host is not None else f"host {r.host_id}" for r in rows]
                shown = ", ".join(labels[:3]) + (f" and {len(labels) - 3} more" if len(labels) > 3 else "")
                n = len(rows)
                host_ids = {r.host_id for r in rows}
                notification = Notification(
                    user_id=uid, project_id=project_id, type="retest",
                    title=f"Retest pending: {finding.title}"[:255],
                    body=(f"{n} host{'' if n == 1 else 's'} await{'s' if n == 1 else ''} a retest ({shown})"
                          + asked + ". Anyone in the project may record the retest's result."),
                    source_type="finding_retest", source_id=rows[0].id, finding_id=finding.id,
                    host_id=next(iter(host_ids)) if len(host_ids) == 1 else None, actor_id=actor_id,
                )
            else:
                hosts = {r.host_id for _, rows in items for r in rows}
                notification = Notification(
                    user_id=uid, project_id=project_id, type="retest",
                    title=f"Retests pending on {len(items)} findings"[:255],
                    body=(f"{len(hosts)} host{'' if len(hosts) == 1 else 's'} across {len(items)} findings "
                          f"await a retest{asked}. Anyone in the project may record the retest's result."),
                    source_type="finding_retests", source_id=None, actor_id=actor_id,
                )
            self.db.add(notification)
            out.append(notification)
        return out

    def notify_report_job_finished(self, job) -> Optional[Notification]:
        """Tell the requester their async report finished (completed or failed).

        The inventory JSON is written by the report worker and the download
        dialog does not have to stay open for it, so completion needs a signal
        that outlives the dialog.  One notification per terminal transition, to the
        requester only; ``source_type='report_job'`` + ``source_id`` is what
        the Activity page deep-links to the export tray with.  Best-effort —
        the worker wraps this so a notification failure never fails the job.
        Returns None when the job has no requester (deleted user) or is not
        in a terminal state.
        """
        if not job.requested_by_id or job.status not in ("completed", "failed"):
            return None
        fmt = job.format or "report"
        kind = job.report_type or "comprehensive"
        if job.status == "completed":
            title = f"Report ready: {job.result_filename or f'{kind} ({fmt})'}"
            body = f"{kind} · {fmt}"
            ntype = "report_ready"
        else:
            title = f"Report failed: {kind} ({fmt})"
            body = (job.error_message or job.last_error or "The report worker reported an error.")[:200]
            ntype = "report_failed"
        notification = Notification(
            user_id=job.requested_by_id,
            project_id=job.project_id,
            type=ntype,
            title=title[:255],
            body=body,
            source_type="report_job",
            source_id=job.id,
            actor_id=None,
        )
        self.db.add(notification)
        return notification

    def notify_findings_assigned(
        self, *, assignee_id: int, project, findings: List, actor: Optional[User],
    ) -> Optional[Notification]:
        """Tell ``assignee_id`` the findings just given to them — ONE
        notification per action, however many findings (review 2026-10-10
        R1c: the bulk assign told the person and the finding's own PATCH did
        not).  One finding is named and opens it (``source_type: "finding"``);
        several say how many and open the reader's findings (``"findings"``,
        ``utils/notificationLinks``).  Nothing
        when the list is empty or the person gave them to themself.  The
        caller commits."""
        if not findings or (actor is not None and actor.id == assignee_id):
            return None
        n = len(findings)
        who = f"@{actor.username}" if actor is not None else "Someone"
        if n == 1:
            finding = findings[0]
            title = f"Finding assigned to you: {finding.title}"
            body = f"{who} assigned the finding '{finding.title}' to you in '{project.name}'"
            source_type, source_id, finding_id = "finding", finding.id, finding.id
        else:
            title = f"{n} findings assigned to you"
            body = f"{who} assigned {n} findings to you in '{project.name}'"
            source_type, source_id, finding_id = "findings", project.id, None
        notification = Notification(
            user_id=assignee_id, project_id=project.id, type="assignment",
            title=title[:255], body=body, source_type=source_type, source_id=source_id,
            finding_id=finding_id, actor_id=actor.id if actor is not None else None,
        )
        self.db.add(notification)
        return notification

    def notify_host_tests_assigned(
        self, tests: Iterable, actor_id: Optional[int], agent_session_id: Optional[int] = None,
    ) -> List[Notification]:
        """Tell each person the host tests just assigned to them (review
        2026-10-01 B9 — assigning a test told nobody).

        ONE notification per assignee per call, however many tests the call
        assigned: a batch of 200 is one line, not 200.  Nothing for a test a
        person gave themself (or their own agent gave them).  ``source_type``
        is ``host_test`` and ``source_id`` the first test; ``host_id`` is set
        when every test is on one host, so the notification opens that host's
        Tests section — with several hosts it is null and the page sends the
        person to their work list.  The caller commits.

        ``agent_session_id`` — the tests were proposed by that agent session.
        An agent may propose its 200 tests in 200 calls, so the unit is then
        the SESSION, not the call: the assignee's unread notification for the
        session is updated to cover everything it has given them since (as a
        run's proposals are one notification per finding author), and a new
        one starts only after they read it.  A person assigning by hand keeps
        one notification per action."""
        from app.db.models import Host
        from app.db.models_host_tests import HostTest

        per_user: dict = {}
        for test in tests:
            uid = test.assigned_to_id
            if uid is None or uid == actor_id:
                continue
            per_user.setdefault(uid, []).append(test)
        if not per_user:
            return []
        actor = self.db.get(User, actor_id) if actor_id else None
        # The session's unread notification per assignee, and the tests it now
        # covers: the session's tests for that person from the notification's
        # first test on.  The notification has no session column — it is the
        # session's because its ``source_id`` is one of the session's tests.
        current: dict = {}
        if agent_session_id is not None:
            session_tests = select(HostTest.id).where(HostTest.agent_session_id == agent_session_id)
            for uid in list(per_user):
                existing = (
                    self.db.query(Notification)
                    .filter(
                        Notification.user_id == uid, Notification.type == "assignment",
                        Notification.source_type == "host_test",
                        Notification.source_id.in_(session_tests),
                        Notification.is_read.is_(False),
                    )
                    .order_by(Notification.id.desc())
                    # Two calls of one run: the second waits, then counts the
                    # first's tests (a new statement sees its commit).
                    .with_for_update()
                    .first()
                )
                if existing is None:
                    continue
                covered = (
                    self.db.query(HostTest)
                    .filter(
                        HostTest.agent_session_id == agent_session_id,
                        HostTest.assigned_to_id == uid,
                        HostTest.id >= existing.source_id,
                    )
                    .order_by(HostTest.id)
                    .all()
                )
                if covered:
                    current[uid] = existing
                    per_user[uid] = covered
        host_ids = {t.host_id for rows in per_user.values() for t in rows}
        addresses = dict(self.db.query(Host.id, Host.ip_address).filter(Host.id.in_(host_ids)).all())
        notifications = []
        for uid, rows in per_user.items():
            hosts = {t.host_id for t in rows}
            first = rows[0]
            one_host = next(iter(hosts)) if len(hosts) == 1 else None
            where = (
                str(addresses.get(one_host) or f"host #{one_host}") if one_host is not None
                else f"{len(hosts)} hosts"
            )
            title = (
                f"Test assigned to you on {where}" if len(rows) == 1
                else f"{len(rows)} tests assigned to you on {where}"
            )
            what = (first.description or "").strip()
            if len(what) > 160:
                what = what[:159] + "…"
            more = f" (and {len(rows) - 1} more)" if len(rows) > 1 else ""
            notification = current.get(uid)
            if notification is None:
                notification = Notification(
                    user_id=uid, project_id=first.project_id, type="assignment",
                    source_type="host_test", actor_id=actor_id,
                )
                self.db.add(notification)
            notification.title = title[:255]
            notification.body = f"{display_name(actor)} assigned you: {what}{more}"
            notification.source_id = first.id
            notification.host_id = one_host
            notifications.append(notification)
        return notifications

    def _shown(self, query):
        """``query`` narrowed to the notifications this installation shows.

        With remediation tracking switched off nothing about remediation is
        shown (the two installation modes): the deadline alerts written while
        it was on stay stored — switching it back on brings them back — but
        the bell, its count and "mark all read" leave them out."""
        from app.services import remediation_policy

        if remediation_policy.load(self.db).enabled:
            return query
        return query.filter(Notification.type != REMEDIATION_NOTIFICATION_TYPE)

    def get_notifications(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Notification]:
        """Fetch notifications for a user."""
        query = self._shown(self.db.query(Notification).filter(Notification.user_id == user_id))
        if unread_only:
            query = query.filter(Notification.is_read == False)
        # `id` breaks ties: rows written in one transaction share `created_at`,
        # and a page cut between them could repeat or skip one (review
        # 2026-10-16, N3).
        return query.order_by(Notification.created_at.desc(), Notification.id.desc()).offset(offset).limit(limit).all()

    def get_unread_count(self, user_id: int) -> int:
        """Fast count for notification badge."""
        return self._shown(self.db.query(func.count(Notification.id)).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,
        )).scalar() or 0

    def mark_read(self, notification_ids: List[int], user_id: int) -> int:
        """Mark specific notifications as read. Returns count updated."""
        now = datetime.now(timezone.utc)  # read_at is timezone-aware (review 2026-10-01 N1)
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
        """Mark all notifications as read for a user — the ones shown."""
        now = datetime.now(timezone.utc)
        updated = self._shown(self.db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,
        )).update(
            {"is_read": True, "read_at": now},
            synchronize_session=False,
        )
        return updated
