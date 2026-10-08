"""Remediation tracking, shared by the page and by agents.  Callers own
transactions.

One row of the list is one finding on one host.  Every read takes a SET of
projects, so the project page and a cross-project lookup ("everything open
for this contact") are the same statement.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, func, or_, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.db import models
from app.db.models_findings import Finding, FindingHost, FindingHostStatus
from app.db.models_project import Project
from app.db.models_remediation import FindingHostRemediation as Remediation
from app.db.models_remediation import RemediationDaily, RemediationEvent, TRACKED_FIELDS
from app.schemas.remediation_schemas import APPLY_MAX_TARGETS
from app.services import remediation_policy as deadlines
from app.services.client_report_service import INCLUDED_STATUSES
from app.services.notification_service import display_name
from app.services.scope_targets_service import _ip_order_by

_STATUS = func.coalesce(Remediation.status, "open")


def _order(db, group: str, state=None, due=None) -> list:
    """Rows of one host, finding or contact together; addresses in address
    order (10.0.0.2 before 10.0.0.10), not as text."""
    by_address = _ip_order_by(db)
    by_finding = [Finding.title, Finding.id]
    if group == "due":
        # What needs someone first: overdue (longest overdue on top), due
        # soon, on track, then the rows with no clock.
        return [deadlines.state_rank(state), due.is_(None), due, *by_address, *by_finding, FindingHost.id]
    if group == "team":
        return [Remediation.team.is_(None), func.lower(Remediation.team), *by_address, *by_finding, FindingHost.id]
    if group == "finding":
        return [*by_finding, *by_address, FindingHost.id]
    if group == "contact":
        return [Remediation.contact_email.is_(None), Remediation.contact_email,
                *by_address, *by_finding, FindingHost.id]
    return [*by_address, *by_finding, FindingHost.id]


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _rows(db, project_ids: Sequence[int]):
    """Findings on hosts worth tracking: what a client report would include,
    plus anything already tracked (a finding reopened after its contact was
    told must not drop off the list)."""
    reportable = (Finding.status.in_(INCLUDED_STATUSES)
                  & (FindingHost.host_status != FindingHostStatus.FALSE_POSITIVE.value))
    return (
        db.query(FindingHost.id)
        .join(Finding, Finding.id == FindingHost.finding_id)
        .join(models.Host, models.Host.id == FindingHost.host_id)
        .outerjoin(Remediation, Remediation.finding_host_id == FindingHost.id)
        .filter(Finding.project_id.in_(tuple(project_ids)))
        .filter(or_(reportable, Remediation.id.isnot(None)))
    )


def list_rows(db, project_ids: Sequence[int], *, status: Optional[str] = None,
              state: Optional[Sequence[str]] = None,
              contact: Optional[str] = None, contact_email: Optional[str] = None,
              unassigned: bool = False,
              host_id: Optional[int] = None, finding_id: Optional[int] = None,
              severity: Optional[str] = None, team: Optional[str] = None,
              overdue_band: Optional[str] = None, no_follow_up_days: Optional[int] = None,
              verification: Optional[str] = None,
              group: str = "host", limit: int = 50, offset: int = 0,
              policy: Optional[deadlines.Policy] = None, today: Optional[date] = None) -> dict:
    policy = policy or deadlines.load(db)
    today = today or policy.today()
    state_of, due = deadlines.state_expr(db, policy, today), deadlines.due_expr(db, policy)
    band_of = deadlines.overdue_band_expr(db, policy, today)
    verification_of = deadlines.verification_expr()
    query = _rows(db, project_ids)
    if team:
        query = query.filter(func.lower(Remediation.team) == team.strip().lower())
    if contact_email:
        # Exactly this contact (the follow-up's rows), not a search.
        query = query.filter(Remediation.contact_email == contact_email.strip().lower())
    if contact:
        pattern = _like(contact.strip().lower())
        query = query.filter(or_(
            Remediation.contact_email.like(pattern, escape="\\"),
            func.lower(Remediation.contact_name).like(pattern, escape="\\"),
        ))
    if unassigned:
        # Nobody named at all: a row with a name and no address yet shows that
        # name in the Contact column, so it is not "no contact".
        query = query.filter(Remediation.contact_email.is_(None), Remediation.contact_name.is_(None))
    if host_id is not None:
        query = query.filter(FindingHost.host_id == host_id)
    if finding_id is not None:
        query = query.filter(FindingHost.finding_id == finding_id)

    # What a manager is asked first — how many of each severity are past or
    # near their deadline, and how late the overdue ones are — over the same
    # selection (the severity breakdown BEFORE the severity filter, so choosing
    # one severity does not empty it), in ONE grouped statement.  Each count opens its list
    # (`state` + `severity`, or `overdue_band`).
    at_risk = state_of.in_(deadlines.AT_RISK_STATES)
    never_or_long_ago = _not_followed_up(db, no_follow_up_days or policy.due_soon_days, today)
    severity_counts = {name: {"overdue": 0, "due_soon": 0} for name in deadlines.TIMELINE_SEVERITIES}
    overdue_ages = {name: 0 for name, _, _ in deadlines.OVERDUE_BANDS}
    not_followed_up = 0
    severity_of = func.lower(Finding.severity)
    # They follow the verification filter like any other part of the selection.
    selection = query if verification is None else query.filter(verification_of == verification)
    for sev, value, band, stale, n in (
        selection.filter(at_risk).with_entities(severity_of, state_of, band_of, never_or_long_ago, func.count())
        .group_by(severity_of, state_of, band_of, never_or_long_ago)
    ):
        if sev in severity_counts:
            severity_counts[sev][value] += n
        if severity and sev != severity:
            continue          # the breakdown by severity is of the whole selection; these follow the filter
        if band in overdue_ages:
            overdue_ages[band] += n
        if stale:
            not_followed_up += n

    if severity:
        query = query.filter(func.lower(Finding.severity) == severity)
    # Counted before the state and status filters: the chips show every state
    # of the current selection, whichever one is open.  The same statement
    # counts where the contact's record and the assessor's conclusion disagree
    # (`verification_counts`): over the selection, before the state, status and
    # verification filters, so each count is the list `verification=` opens by
    # itself.  The state counts follow the verification filter, as they follow
    # every other filter but their own.
    state_counts = {name: 0 for name in deadlines.STATES}
    verification_counts = {name: 0 for name in deadlines.VERIFICATIONS}
    for value, gap, n in (query.with_entities(state_of, verification_of, func.count())
                          .group_by(state_of, verification_of)):
        if gap in verification_counts:
            verification_counts[gap] += n
        if verification is None or gap == verification:
            state_counts[value] += n
    if verification is not None:
        query = query.filter(verification_of == verification)

    wanted = set(state or deadlines.STATES)
    if status:
        wanted &= set(_STATES_OF_STATUS[status])
    narrowed = overdue_band is not None or no_follow_up_days is not None
    if overdue_band is not None:
        query = query.filter(band_of == overdue_band)
    if no_follow_up_days is not None:
        # At risk and not chased: nobody recorded a follow-up in that many days.
        query = query.filter(at_risk, never_or_long_ago)
    if state or status:
        query = query.filter(state_of.in_(sorted(wanted)))
    total = (query.count() if narrowed
             else sum(n for name, n in state_counts.items() if name in wanted))

    rows = (
        query.join(Project, Project.id == Finding.project_id).with_entities(
            FindingHost.id.label("finding_host_id"), FindingHost.host_status.label("endpoint_status"),
            Finding.id.label("finding_id"), Finding.project_id.label("project_id"),
            Project.name.label("project_name"), Finding.title.label("finding_title"),
            Finding.severity.label("severity"), Finding.status.label("finding_status"),
            models.Host.id.label("host_id"), models.Host.ip_address.label("ip_address"),
            models.Host.hostname.label("hostname"), Remediation.contact_email.label("contact_email"),
            Remediation.contact_name.label("contact_name"), Remediation.team.label("team"),
            Remediation.notified_on.label("notified_on"), _STATUS.label("status"),
            Remediation.closed_on.label("closed_on"), Remediation.updated_at.label("updated_at"),
            state_of.label("state"), due.label("open_due_on"),
            Remediation.closed_due_on.label("closed_due_on"),
            Remediation.last_follow_up_on.label("last_follow_up_on"),
            verification_of.label("verification"),
        )
        .order_by(*_order(db, group, state_of, due)).offset(offset).limit(limit).all()
    )
    items = [_item(r, today) for r in rows]
    return {"items": items, "total": total, "has_more": offset + len(items) < total,
            "limit": limit, "offset": offset, "status_counts": _status_counts(state_counts),
            "state_counts": state_counts, "verification_counts": verification_counts,
            "severity_counts": severity_counts, "overdue_ages": overdue_ages,
            "not_followed_up": not_followed_up,
            "not_followed_up_days": no_follow_up_days or policy.due_soon_days,
            "as_of": today.isoformat()}


def _not_followed_up(db, days: int, today: date):
    """Nobody recorded a follow-up in the last ``days`` days (or ever)."""
    cutoff = today - timedelta(days=days)
    return or_(Remediation.last_follow_up_on.is_(None), Remediation.last_follow_up_on < cutoff)


_STATES_OF_STATUS = {"open": deadlines.OPEN_STATES, "closed": ("closed",), "deferred": ("deferred",)}


def _status_counts(state_counts: dict) -> dict:
    """Open / closed / deferred, from the states they are made of."""
    return {status: sum(state_counts.get(name, 0) for name in names)
            for status, names in _STATES_OF_STATUS.items()}


def _item(r, today: date) -> dict:
    """One list row from the labelled columns ``list_rows`` selects."""
    state = r.state
    closed_on, closed_due = deadlines.as_date(r.closed_on), deadlines.as_date(r.closed_due_on)
    # A closed row shows the deadline it was closed against; a deferred row
    # has no clock, so it shows none.
    due = closed_due if state == "closed" else None if state == "deferred" else deadlines.as_date(r.open_due_on)
    return {
        "finding_host_id": r.finding_host_id, "endpoint_status": r.endpoint_status,
        "finding_id": r.finding_id, "project_id": r.project_id, "project_name": r.project_name,
        "finding_title": r.finding_title, "severity": r.severity, "finding_status": r.finding_status,
        "host_id": r.host_id, "ip_address": r.ip_address, "hostname": r.hostname,
        "contact_email": r.contact_email, "contact_name": r.contact_name,
        "notified_on": _iso(r.notified_on), "status": r.status, "closed_on": _iso(r.closed_on),
        "updated_at": _iso(r.updated_at),
        "state": state, "due_on": _iso(due),
        # Days to the deadline of an open row: negative once it is overdue.
        "days_left": (due - today).days if due is not None and state != "closed" else None,
        # How many days after its deadline a row was closed (0: on time);
        # None when it had no deadline.
        "closed_days_late": (max(0, (closed_on - closed_due).days)
                             if state == "closed" and closed_on and closed_due else None),
        "last_follow_up_on": _iso(r.last_follow_up_on), "team": r.team,
        # Where this record and the assessor's `endpoint_status` disagree
        # (`remediation_policy.verification_expr`); None where they do not.
        "verification": r.verification,
    }


def state_counts_by_project(db, project_ids: Sequence[int], policy: deadlines.Policy,
                            today: date) -> dict[int, dict[str, int]]:
    """Each project's rows by state — the Remediation page's own chips (the
    same rows, the same definition), for a cross-project reader.  Every id is
    present."""
    out = {pid: {name: 0 for name in deadlines.STATES} for pid in project_ids}
    if not out:
        return out
    state_of = deadlines.state_expr(db, policy, today)
    for pid, value, n in (_rows(db, list(out)).with_entities(Finding.project_id, state_of, func.count())
                          .group_by(Finding.project_id, state_of)):
        out[pid][value] = n
    return out


def status_counts_by_project(db, project_ids: Sequence[int], policy: Optional[deadlines.Policy] = None,
                             today: Optional[date] = None) -> dict[int, dict[str, int]]:
    """Each project's open / closed / deferred, from the same states."""
    policy = policy or deadlines.load(db)
    by_state = state_counts_by_project(db, project_ids, policy, today or policy.today())
    return {pid: _status_counts(counts) for pid, counts in by_state.items()}


def durations_by_project(db, project_ids: Sequence[int], today: date,
                         policy: Optional[deadlines.Policy] = None) -> dict[int, dict]:
    """How the fixes went and how late the worst one is, per project.

    * ``longest_overdue_days`` — days past the deadline of the most overdue
      open finding on a host; None when nothing is overdue.
    * ``days_to_close_total`` / ``closed_measured`` — the days from assigned
      to closed, summed over the closed rows that carry BOTH dates (and whose
      closed date is not before the assigned one), and how many those are; an
      average over several projects is the two sums divided, never an average
      of averages.
    * ``closed_late`` / ``closed_with_deadline`` — closed rows that were
      closed after the deadline frozen at close, out of the closed rows that
      had one.

    Every id is present.
    """
    policy = policy or deadlines.load(db)
    out = {pid: {"longest_overdue_days": None, "days_to_close_total": 0, "closed_measured": 0,
                 "closed_late": 0, "closed_with_deadline": 0} for pid in project_ids}
    if not out:
        return out
    overdue = deadlines.state_expr(db, policy, today) == "overdue"
    measured = and_(Remediation.status == "closed", Remediation.notified_on.isnot(None),
                    Remediation.closed_on.isnot(None), Remediation.closed_on >= Remediation.notified_on)
    judged = and_(Remediation.status == "closed", Remediation.closed_on.isnot(None),
                  Remediation.closed_due_on.isnot(None))
    if db.get_bind().dialect.name == "postgresql":
        days = Remediation.closed_on - Remediation.notified_on            # date - date is whole days
    else:
        days = cast(func.julianday(Remediation.closed_on) - func.julianday(Remediation.notified_on), Integer)
    for pid, earliest_due, n, total, late, with_deadline in (
        _rows(db, list(out)).with_entities(
            Finding.project_id,
            func.min(case((overdue, deadlines.due_expr(db, policy)))),
            func.sum(case((measured, 1), else_=0)),
            func.sum(case((measured, days), else_=0)),
            func.sum(case((and_(judged, Remediation.closed_on > Remediation.closed_due_on), 1), else_=0)),
            func.sum(case((judged, 1), else_=0)),
        ).group_by(Finding.project_id)
    ):
        earliest_due = deadlines.as_date(earliest_due)
        out[pid] = {
            "longest_overdue_days": (today - earliest_due).days if earliest_due is not None else None,
            "days_to_close_total": int(total or 0), "closed_measured": int(n or 0),
            "closed_late": int(late or 0), "closed_with_deadline": int(with_deadline or 0),
        }
    return out


#: Flat keys, so a cross-project reader can add projects up key by key.
OVERDUE_KEYS = (*(f"overdue_{name}" for name in deadlines.TIMELINE_SEVERITIES),
                "overdue_age_1_7", "overdue_age_8_30", "overdue_age_31_90", "overdue_age_90_plus")
_AGE_KEY = {"1-7": "overdue_age_1_7", "8-30": "overdue_age_8_30", "31-90": "overdue_age_31_90",
            "90+": "overdue_age_90_plus"}


def overdue_breakdown_by_project(db, project_ids: Sequence[int], policy: deadlines.Policy,
                                 today: date) -> dict[int, dict[str, int]]:
    """Each project's OVERDUE rows by severity and by how late they are —
    the list's ``severity_counts`` / ``overdue_ages`` with the same
    expressions, for a cross-project reader.  Every id is present."""
    out = {pid: {key: 0 for key in OVERDUE_KEYS} for pid in project_ids}
    if not out:
        return out
    severity_of = func.lower(Finding.severity)
    band_of = deadlines.overdue_band_expr(db, policy, today)
    for pid, sev, band, n in (
        _rows(db, list(out)).filter(deadlines.state_expr(db, policy, today) == "overdue")
        .with_entities(Finding.project_id, severity_of, band_of, func.count())
        .group_by(Finding.project_id, severity_of, band_of)
    ):
        if f"overdue_{sev}" in out[pid]:
            out[pid][f"overdue_{sev}"] += n
        if band in _AGE_KEY:
            out[pid][_AGE_KEY[band]] += n
    return out


def _iso(value):
    return value.isoformat() if value is not None else None


# --- writes ---------------------------------------------------------------

def _resolve_targets(db, project_id: int, rows) -> list[list[FindingHost]]:
    """Each row's finding-on-host rows, all in this project.  A row that
    names nothing here refuses the whole call: nothing is guessed.

    Resolved in SQL as ids first and bounded at one past the limit, so a row
    naming one host of a finding on thirty thousand never loads the other
    hosts, and an oversized call is refused before anything is hydrated.
    """
    by_id = {row.finding_host_id for row in rows if row.finding_host_id is not None}
    whole = {row.finding_id for row in rows if row.finding_id is not None and row.host_id is None}
    pairs = {(row.finding_id, row.host_id) for row in rows
             if row.finding_id is not None and row.host_id is not None}
    named = []
    if by_id:
        named.append(FindingHost.id.in_(by_id))
    if whole:
        named.append(FindingHost.finding_id.in_(whole))
    if pairs:
        named.append(tuple_(FindingHost.finding_id, FindingHost.host_id).in_(pairs))
    light = (db.query(FindingHost.id, FindingHost.finding_id, FindingHost.host_id)
             .join(Finding, Finding.id == FindingHost.finding_id)
             .filter(Finding.project_id == project_id, or_(*named))
             .order_by(FindingHost.id).limit(APPLY_MAX_TARGETS + 1).all())
    if len(light) > APPLY_MAX_TARGETS:
        problem = f"the call names more than {APPLY_MAX_TARGETS} findings on hosts; send them in several calls"
        if whole:
            # ``finding_id`` alone means every host of the finding, which can
            # be more than one call may change.
            problem += (f" (a row with finding_id and no host_id covers every host of that finding: "
                        f"for a finding on more than {APPLY_MAX_TARGETS} hosts, name the hosts — "
                        f"finding_id with host_id, or finding_host_id — at most {APPLY_MAX_TARGETS} a call)")
        raise HTTPException(422, {"message": "Nothing was changed.", "problems": [problem]})
    known = {fh_id for fh_id, _, _ in light}
    of_finding: dict[int, list[tuple[int, int]]] = {}
    for fh_id, finding_id, host_id in light:
        of_finding.setdefault(finding_id, []).append((fh_id, host_id))

    resolved, problems, seen = [], [], {}
    for index, row in enumerate(rows):
        if row.finding_host_id is not None:
            targets = [row.finding_host_id] if row.finding_host_id in known else []
            what = f"finding_host_id {row.finding_host_id}"
        else:
            targets = [fh_id for fh_id, host_id in of_finding.get(row.finding_id, ())
                       if row.host_id is None or host_id == row.host_id]
            what = f"finding_id {row.finding_id}" + (f" on host_id {row.host_id}" if row.host_id else "")
        if not targets:
            problems.append(f"row {index}: {what} is not a finding on a host in this project")
        for fh_id in targets:
            if fh_id in seen:
                problems.append(f"rows {seen[fh_id]} and {index} both name finding_host_id {fh_id}")
            seen[fh_id] = index
        resolved.append(targets)
    if problems:
        raise HTTPException(422, {"message": "Nothing was changed.", "problems": problems[:50]})
    loaded = {fh.id: fh for fh in db.query(FindingHost).options(joinedload(FindingHost.finding))
              .filter(FindingHost.id.in_(seen))}
    return [[loaded[fh_id] for fh_id in targets] for targets in resolved]


def _current(record: Optional[Remediation], field: str):
    if record is None:
        return "open" if field == "status" else None
    return getattr(record, field)


def _is_set(field: str, value) -> bool:
    """Has someone recorded this?  ``open`` is where every row starts, so it
    is not something an update could be overwriting."""
    return value is not None and not (field == "status" and value == "open")


def _text(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _decide(record: Optional[Remediation], sent: dict, overwrite: bool):
    """What one row would do to one finding on a host: ``(changes,
    conflicts, unchanged, problem)``, each change ``(field, old, new)``.

    Status and closed date are decided TOGETHER: a closed date belongs to a
    closed row, so a date is never written beside a status that did not
    become (or stay) closed, and clearing a date from a row that is not
    closed is never a conflict.  Decided field by field, a conflicting status
    left its date behind on a deferred row.
    """
    wanted = dict(sent)
    status_now, closed_now = _current(record, "status"), _current(record, "closed_on")
    # A closed date on a row that is no longer closed is wrong data.
    if wanted.get("status") in ("open", "deferred") and "closed_on" not in wanted and closed_now is not None:
        wanted["closed_on"] = None
    # The request itself must make sense, whatever is stored.
    asked_status = wanted.get("status", status_now)
    if wanted.get("closed_on") is not None and asked_status != "closed":
        return [], [], 0, f"closed_on needs status closed (the status would be {asked_status})"

    changes, conflicts, unchanged = [], [], 0
    status_after = status_now
    for field in TRACKED_FIELDS:   # status is decided before closed_on
        if field not in wanted:
            continue
        old, new = _current(record, field), wanted[field]
        if old == new:
            unchanged += 1
            continue
        blocked = _is_set(field, old) and not overwrite
        if field == "closed_on":
            blocked = (blocked and status_after == "closed") if new is None else (blocked or status_after != "closed")
        if blocked:
            conflicts.append((field, old, new))
            continue
        changes.append((field, old, new))
        if field == "status":
            status_after = new
    return changes, conflicts, unchanged, None


def apply(db, project_id: int, body, who) -> dict:
    """Set tracked fields (and add notes) on findings on hosts.

    Everything is PLANNED first and written only if the whole plan is valid:
    a row naming nothing here, a closed date without a closed status, or one
    note key carrying two different notes refuses the call (422) and nothing
    is written.  A field already set to something else is a conflict and is
    left alone unless ``overwrite``.  A dry run returns the same plan — and
    the same refusals — and writes nothing.
    """
    resolved = _resolve_targets(db, project_id, body.rows)
    ids = [fh.id for targets in resolved for fh in targets]
    if body.dry_run:
        records = {r.finding_host_id: r for r in db.query(Remediation)
                   .filter(Remediation.finding_host_id.in_(ids)).populate_existing()}
    else:
        records = {r.finding_host_id: r for r in _lock_for_write(db, ids)}
    keys = [_note_key(note, fh) for row, targets in zip(body.rows, resolved)
            for note in row.notes for fh in targets if note.request_key]
    # A key's note, stored or accepted earlier in THIS call: the same note
    # again is a retry, a different one is a mistake.
    keyed = {key: (text_, when) for key, text_, when in db.query(
        RemediationEvent.request_key, RemediationEvent.body, RemediationEvent.occurred_at,
    ).filter(RemediationEvent.project_id == project_id, RemediationEvent.request_key.in_(keys))} if keys else {}

    now = datetime.now(timezone.utc)
    summary = {"targets": len(ids), "changed": 0, "unchanged": 0, "conflicts": 0,
               "notes_added": 0, "notes_already_recorded": 0}
    report, plan, problems = [], [], []
    for index, (row, targets) in enumerate(zip(body.rows, resolved)):
        sent = row.changes()
        entry = {"row": index, "finding_host_ids": [fh.id for fh in targets], "changed": [], "conflicts": []}
        for fh in targets:
            changes, conflicts, unchanged, problem = _decide(records.get(fh.id), sent, body.overwrite)
            if problem:
                problems.append(f"row {index}, finding_host_id {fh.id}: {problem}")
                continue
            as_reported = lambda c: {"finding_host_id": fh.id, "field": c[0],  # noqa: E731
                                     "from": _text(c[1]), "to": _text(c[2])}
            entry["changed"] += [as_reported(c) for c in changes]
            entry["conflicts"] += [as_reported(c) for c in conflicts]
            summary["changed"] += len(changes)
            summary["conflicts"] += len(conflicts)
            summary["unchanged"] += unchanged
            notes = []
            for note in row.notes:
                key = _note_key(note, fh)
                if key is not None and key in keyed:
                    stored_body, stored_when = keyed[key]
                    # The same note is the same text at the same time (as
                    # ``add_note``): a corrected date under an old key would
                    # otherwise be reported "already recorded" and dropped.
                    moved = (note.occurred_at is not None and stored_when is not None
                             and not _same_moment(stored_when, note.occurred_at))
                    if stored_body != note.body or moved:
                        problems.append(f"row {index}: request_key {note.request_key!r} already "
                                        f"carries a different note for finding_host_id {fh.id}")
                    else:
                        summary["notes_already_recorded"] += 1
                    continue
                if key is not None:
                    keyed[key] = (note.body, note.occurred_at)
                summary["notes_added"] += 1
                notes.append((note, key))
            plan.append((fh, changes, notes))
        report.append(entry)
    if problems:
        raise HTTPException(422, {"message": "Nothing was changed.", "problems": problems[:50]})

    if not body.dry_run:
        refreeze = []
        for fh, changes, notes in plan:
            record = records.get(fh.id)
            restarts_clock = any(field in ("status", "notified_on") for field, _, _ in changes)
            for field, old, new in changes:
                if record is None:
                    record = records[fh.id] = Remediation(
                        finding_host_id=fh.id, project_id=project_id, status="open")
                    db.add(record)
                setattr(record, field, new)
                record.updated_by_id = who.user_id
                # ``created_at`` from the same clock as ``occurred_at``: the
                # column's default is the database's transaction start, and a
                # save that straddled a minute read as backdated.
                db.add(_event(project_id, fh, who, kind="change", field=field,
                              old_value=_text(old), new_value=_text(new), occurred_at=now, created_at=now))
            if record is not None and restarts_clock:
                # A new status or assigned date is a new deadline: it alerts
                # anew, and an open row's deadline is derived, never stored.
                record.due_soon_alerted_for = record.overdue_alerted_for = None
                if record.status == "closed":
                    refreeze.append(record)
                else:
                    record.closed_due_on = None
            for note, key in notes:
                db.add(_event(project_id, fh, who, kind="note", body=note.body,
                              occurred_at=note.occurred_at or now, created_at=now, request_key=key))
        db.flush()
        _freeze_closed_deadlines(db, refreeze)
    return {"dry_run": body.dry_run, "overwrite": body.overwrite, "summary": summary, "rows": report}


def _lock_for_write(db, finding_host_ids: Sequence[int]) -> list[Remediation]:
    """Lock what a write to these findings on hosts touches and return their
    records.  ONE order for every writer (``apply``, a recorded follow-up, an
    endpoint's removal): the ``finding_hosts`` rows in id order — which also
    serialises two callers creating the same row's first record — then the
    records in the same order.  Writers that locked different things, or the
    same things in request order, could each hold what the other wanted."""
    ids = sorted(set(finding_host_ids))
    if not ids:
        return []
    db.query(FindingHost.id).filter(FindingHost.id.in_(ids)).order_by(FindingHost.id) \
        .with_for_update().all()
    return (db.query(Remediation).filter(Remediation.finding_host_id.in_(ids))
            .order_by(Remediation.finding_host_id).with_for_update().populate_existing().all())


def _freeze_closed_deadlines(db, records: Sequence[Remediation]) -> None:
    """Store, on rows that are closed, the deadline in force NOW — the
    assigned date plus today's timeline for the finding's current severity,
    read with ``due_expr`` (the one definition) in one statement for them all.

    It runs whenever a closed row's status or assigned date was written.  So
    correcting the assigned date of a row that is ALREADY closed re-freezes
    its deadline against the severity and timeline of the day of the
    correction, not those of the day it was closed: the earlier ones are not
    kept anywhere to freeze against."""
    if not records:
        return
    db.execute(
        update(Remediation)
        .values(closed_due_on=deadlines.due_expr(db, deadlines.load(db)))
        .where(Remediation.id.in_([record.id for record in records]),
               FindingHost.id == Remediation.finding_host_id,
               Finding.id == FindingHost.finding_id)
        .execution_options(synchronize_session=False)
    )
    for record in records:
        db.expire(record, ["closed_due_on"])


def _note_key(note, fh: FindingHost) -> Optional[str]:
    """One note on a row that covers several hosts is one entry per host."""
    return f"{note.request_key}:{fh.id}" if note.request_key else None


def _event(project_id: int, fh: Optional[FindingHost], who, *, host_id: Optional[int] = None, **columns):
    session = getattr(who, "session", None)
    return RemediationEvent(
        project_id=project_id, host_id=fh.host_id if fh is not None else host_id,
        finding_id=fh.finding_id if fh is not None else None,
        finding_host_id=fh.id if fh is not None else None,
        finding_title=(fh.finding.title or "")[:500] if fh is not None else None,
        author_id=who.user_id, agent_session_id=session.id if session is not None else None,
        **columns,
    )


# --- a finding leaves a host ---------------------------------------------

@dataclass(frozen=True)
class _Person:
    user_id: Optional[int]
    session: None = None


def _held(record: Remediation) -> str:
    """What a record that is about to go said, for the timeline."""
    parts = []
    if record.contact_email or record.contact_name:
        parts.append("contact " + " ".join(filter(None, [record.contact_name, record.contact_email])))
    if record.team:
        parts.append(f"team {record.team}")
    if record.notified_on:
        parts.append(f"assigned {record.notified_on.isoformat()}")
    parts.append(f"status {record.status}" + (f" on {record.closed_on.isoformat()}" if record.closed_on else ""))
    if record.last_follow_up_on:
        parts.append(f"last follow-up {record.last_follow_up_on.isoformat()}")
    return ", ".join(parts)


def before_endpoints_removed(db, finding: Finding, *, user_id: Optional[int], is_project_admin: bool,
                             host_id: Optional[int] = None, finding_host_id: Optional[int] = None,
                             deleting_finding: bool = False) -> int:
    """Called before finding-on-host rows are deleted — one endpoint
    (``finding_host_id``), every endpoint on a host (``host_id``), or all of
    the finding's.  Their remediation records are deleted with them (the
    foreign key cascades) and a restored endpoint is a new row, so a record
    somebody filled in is the project admins' to give up: anyone else is
    refused (409) and nothing is removed; for an admin each such record is
    written to its host's timeline, which outlives it.

    Does nothing — and says nothing — on an installation that has remediation
    tracking off.  Returns how many filled-in records go.  The caller deletes
    the rows in the same transaction."""
    if not deadlines.load(db).enabled:
        return 0
    scope = [FindingHost.finding_id == finding.id]
    if host_id is not None:
        scope.append(FindingHost.host_id == host_id)
    if finding_host_id is not None:
        scope.append(FindingHost.id == finding_host_id)
    leaving = [fh_id for (fh_id,) in db.query(FindingHost.id).filter(*scope)]
    # Locked like every other write, and only then read: an ``apply`` that
    # was filling one of them in has finished by the time this decides.
    records = [record for record in _lock_for_write(db, leaving) if _is_filled(record)]
    if not records:
        return 0
    if not is_project_admin:
        n = len(records)
        on = "this host" if n == 1 else f"{n} hosts"
        raise HTTPException(409, (
            f"This finding has a remediation record on {on} (a contact, dates or a status), which "
            f"would be deleted with it. A project admin must remove it. Nothing was changed."))
    targets = {fh.id: fh for fh in db.query(FindingHost).options(joinedload(FindingHost.finding))
               .filter(FindingHost.id.in_([record.finding_host_id for record in records]))}
    now = datetime.now(timezone.utc)
    who = _Person(user_id)
    for record in records:
        db.add(_event(finding.project_id, targets[record.finding_host_id], who, kind="change",
                      field="finding", old_value=_held(record),
                      new_value="finding deleted" if deleting_finding else "removed from this host",
                      occurred_at=now, created_at=now))
    db.flush()
    return len(records)


def _is_filled(record: Remediation) -> bool:
    """Somebody recorded something: a contact, a team, a date, a status that
    is not where every row starts, or a follow-up."""
    return bool(record.contact_email or record.contact_name or record.team or record.notified_on
                or record.status != "open" or record.last_follow_up_on)


# --- timeline -------------------------------------------------------------

def _require_host(db, project_id: int, host_id: int) -> None:
    if not db.query(models.Host.id).filter(
        models.Host.id == host_id, models.Host.project_id == project_id,
    ).first():
        raise HTTPException(404, "Host not found in this project")


def list_events(db, project_id: int, host_id: int, user_id: Optional[int], *,
                finding_host_id: Optional[int] = None, limit: int = 50, offset: int = 0) -> dict:
    _require_host(db, project_id, host_id)
    query = db.query(RemediationEvent).filter(
        RemediationEvent.project_id == project_id, RemediationEvent.host_id == host_id)
    if finding_host_id is not None:
        query = query.filter(RemediationEvent.finding_host_id == finding_host_id)
    total = query.count()
    rows = (query.options(joinedload(RemediationEvent.author))
            .order_by(RemediationEvent.occurred_at.desc(), RemediationEvent.id.desc())
            .offset(offset).limit(limit).all())
    return {"items": [serialize_event(row, user_id) for row in rows], "total": total,
            "has_more": offset + len(rows) < total, "limit": limit, "offset": offset}


def serialize_event(row: RemediationEvent, user_id: Optional[int]) -> dict:
    return {
        "id": row.id, "host_id": row.host_id, "kind": row.kind,
        "finding_id": row.finding_id, "finding_host_id": row.finding_host_id,
        "finding_title": row.finding_title,
        "field": row.field, "from": row.old_value, "to": row.new_value, "body": row.body,
        "occurred_at": _iso(row.occurred_at), "recorded_at": _iso(row.created_at),
        "edited_at": _iso(row.edited_at),
        "author_id": row.author_id,
        "author": display_name(row.author) if row.author is not None else None,
        "agent_session_id": row.agent_session_id,
        "can_modify": row.kind == "note" and row.author_id is not None and row.author_id == user_id,
    }


def add_note(db, project_id: int, body, who) -> tuple[RemediationEvent, bool]:
    """Returns ``(note, created)``; a repeated ``request_key`` with the same
    text is the stored note, with different text a 409."""
    _require_host(db, project_id, body.host_id)
    fh = None
    if body.finding_host_id is not None:
        fh = (db.query(FindingHost).join(Finding, Finding.id == FindingHost.finding_id)
              .options(joinedload(FindingHost.finding))
              .filter(FindingHost.id == body.finding_host_id, FindingHost.host_id == body.host_id,
                      Finding.project_id == project_id).first())
        if fh is None:
            raise HTTPException(422, "finding_host_id must be a finding on this host")
    def stored_under_the_key():
        stored = db.query(RemediationEvent).filter(
            RemediationEvent.project_id == project_id,
            RemediationEvent.request_key == body.request_key).first()
        if stored is None:
            return None
        # The same note is the same text about the same thing at the
        # same time; the key alone does not make a retry.
        same = (stored.body == body.body and stored.host_id == body.host_id
                and stored.finding_host_id == body.finding_host_id
                and (body.occurred_at is None or _same_moment(stored.occurred_at, body.occurred_at)))
        if not same:
            raise HTTPException(409, "This request_key already recorded a different note")
        return stored

    if body.request_key:
        stored = stored_under_the_key()
        if stored is not None:
            return stored, False
    now = datetime.now(timezone.utc)
    row = _event(project_id, fh, who, host_id=body.host_id, kind="note", body=body.body,
                 occurred_at=body.occurred_at or now, created_at=now,
                 request_key=body.request_key)
    if not body.request_key:
        db.add(row)
        db.flush()
        return row, True
    # A retry sent while the first call is still in flight sees no stored row
    # and loses the unique index: it answers with the winner's note, as the
    # read above would have a moment later.
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        stored = stored_under_the_key()
        if stored is None:
            raise
        return stored, False
    return row, True


def _same_moment(stored: datetime, sent: datetime) -> bool:
    """Two timestamps for the same instant, whichever carries a time zone."""
    as_utc = lambda t: t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)  # noqa: E731
    return as_utc(stored) == as_utc(sent)


def _own_note(db, project_id: int, event_id: int, user_id: Optional[int]) -> RemediationEvent:
    row = (db.query(RemediationEvent).filter(
        RemediationEvent.project_id == project_id, RemediationEvent.id == event_id,
    ).with_for_update().first())
    if row is None:
        raise HTTPException(404, "Timeline entry not found in this project")
    if row.kind != "note":
        raise HTTPException(409, "A recorded change cannot be edited or removed")
    if row.author_id is None or row.author_id != user_id:
        raise HTTPException(403, "A note can be changed only by the person who wrote it")
    return row


def update_note(db, project_id: int, event_id: int, body, user_id: Optional[int]) -> RemediationEvent:
    row = _own_note(db, project_id, event_id, user_id)
    if body.body is not None and body.body != row.body:
        row.body = body.body
        row.edited_at = datetime.now(timezone.utc)
    if body.occurred_at is not None:
        row.occurred_at = body.occurred_at
    db.flush()
    return row


def delete_note(db, project_id: int, event_id: int, user_id: Optional[int]) -> None:
    db.delete(_own_note(db, project_id, event_id, user_id))
    db.flush()


def contacts(db, project_ids: Iterable[int], *, limit: int = 200,
             policy: Optional[deadlines.Policy] = None, today: Optional[date] = None) -> list[dict]:
    """The contacts in use, each with their findings on hosts by state — the
    page's contact chooser and its "by contact" follow-up list.  The contact
    with the most overdue comes first; ``last_follow_up_on`` is the most
    recent day anyone recorded chasing them about a row that is still at
    risk (None: nobody has)."""
    policy = policy or deadlines.load(db)
    today = today or policy.today()
    state_of = deadlines.state_expr(db, policy, today)
    n = lambda *names: func.sum(case((state_of.in_(names), 1), else_=0))  # noqa: E731
    at_risk = state_of.in_(deadlines.AT_RISK_STATES)
    overdue = n("overdue")
    rows = (db.query(Remediation.contact_email, func.max(Remediation.contact_name), func.count(),
                     n(*deadlines.OPEN_STATES), overdue, n("due_soon"), n("on_track"), n("deferred"),
                     n("closed"), func.max(case((at_risk, Remediation.last_follow_up_on))),
                     func.count(func.distinct(Remediation.project_id)))
            .join(FindingHost, FindingHost.id == Remediation.finding_host_id)
            .join(Finding, Finding.id == FindingHost.finding_id)
            .filter(Remediation.project_id.in_(tuple(project_ids)),
                    Remediation.contact_email.isnot(None))
            .group_by(Remediation.contact_email)
            .order_by(overdue.desc(), n("due_soon").desc(), Remediation.contact_email)
            .limit(limit).all())
    return [{"contact_email": r[0], "contact_name": r[1], "total": r[2], "open": int(r[3] or 0),
             "overdue": int(r[4] or 0), "due_soon": int(r[5] or 0), "on_track": int(r[6] or 0),
             "deferred": int(r[7] or 0), "closed": int(r[8] or 0),
             "last_follow_up_on": _iso(deadlines.as_date(r[9])), "projects": r[10]} for r in rows]


def teams(db, project_ids: Iterable[int], *, limit: int = 200,
          policy: Optional[deadlines.Policy] = None, today: Optional[date] = None) -> list[dict]:
    """The teams that own fixes, each with its findings on hosts by state —
    the most overdue first.  A team is free text compared without case; the
    spelling shown is one that was entered.  Rows with a contact and no team
    are one last entry, ``team: null``, so nothing assigned is left out."""
    policy = policy or deadlines.load(db)
    today = today or policy.today()
    state_of = deadlines.state_expr(db, policy, today)
    n = lambda *names: func.sum(case((state_of.in_(names), 1), else_=0))  # noqa: E731
    key = func.lower(Remediation.team)
    overdue = n("overdue")
    rows = (db.query(key, func.max(Remediation.team), func.count(), n(*deadlines.OPEN_STATES), overdue,
                     n("due_soon"), n("on_track"), n("deferred"), n("closed"),
                     func.count(func.distinct(Remediation.contact_email)),
                     func.count(func.distinct(Remediation.project_id)))
            .join(FindingHost, FindingHost.id == Remediation.finding_host_id)
            .join(Finding, Finding.id == FindingHost.finding_id)
            .filter(Remediation.project_id.in_(tuple(project_ids)),
                    or_(Remediation.team.isnot(None), Remediation.contact_email.isnot(None)))
            .group_by(key)
            .order_by(key.is_(None), overdue.desc(), n("due_soon").desc(), key)
            .limit(limit).all())
    return [{"team": r[1], "total": r[2], "open": int(r[3] or 0), "overdue": int(r[4] or 0),
             "due_soon": int(r[5] or 0), "on_track": int(r[6] or 0), "deferred": int(r[7] or 0),
             "closed": int(r[8] or 0), "contacts": r[9], "projects": r[10]} for r in rows]


# --- history ------------------------------------------------------------------

def snapshot(db, today: Optional[date] = None) -> int:
    """Write today's count per project (replacing an earlier one of today);
    returns the projects written.  A no-op while the installation has
    remediation tracking off.  The caller commits."""
    policy = deadlines.load(db)
    if not policy.enabled:
        return 0
    today = today or policy.today()
    ids = [pid for (pid,) in db.query(Project.id).order_by(Project.id)]
    counts = state_counts_by_project(db, ids, policy, today)
    rows = [{"project_id": pid, "day": today, **states} for pid, states in counts.items()
            if any(states.values())]
    # A project that has nothing left today loses today's earlier row.
    stale = db.query(RemediationDaily).filter(RemediationDaily.day == today)
    if rows:
        stale = stale.filter(RemediationDaily.project_id.notin_([row["project_id"] for row in rows]))
    stale.delete(synchronize_session=False)
    if rows:
        # One upsert, the projects in id order: two workers sweeping at the
        # same moment both succeed (a delete followed by plain inserts made
        # the second one fail on the unique index, and its sweep with it).
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        statement = insert(RemediationDaily).values(rows)
        db.execute(statement.on_conflict_do_update(
            index_elements=[RemediationDaily.project_id, RemediationDaily.day],
            set_={name: statement.excluded[name] for name in deadlines.STATES},
        ))
    db.flush()
    return len(rows)


def trend(db, project_ids: Sequence[int], *, days: int = 90, today: Optional[date] = None) -> dict:
    """Whether the backlog is shrinking: the daily counts recorded since the
    installation began tracking (summed over the projects), and how the
    closed rows ended per month — on time, late, or with no deadline to
    judge them by.  A day nobody recorded is absent, never a zero."""
    today = today or deadlines.load(db).today()
    ids = tuple(project_ids)
    since = today - timedelta(days=days)
    names = ("overdue", "due_soon", "on_track", "not_assigned", "deferred", "closed")
    daily = []
    if ids:
        columns = [func.sum(getattr(RemediationDaily, name)) for name in names]
        for row in (db.query(RemediationDaily.day, *columns)
                    .filter(RemediationDaily.project_id.in_(ids), RemediationDaily.day >= since)
                    .group_by(RemediationDaily.day).order_by(RemediationDaily.day)):
            daily.append({"day": _iso(deadlines.as_date(row[0])),
                          **{name: int(value or 0) for name, value in zip(names, row[1:])}})

    months: dict[str, dict] = {}
    if ids:
        first = first_of_twelve_months(today)
        if db.get_bind().dialect.name == "postgresql":
            month_of = func.to_char(Remediation.closed_on, "YYYY-MM")
        else:
            month_of = func.strftime("%Y-%m", Remediation.closed_on)
        ended = case((Remediation.closed_due_on.is_(None), "no_deadline"),
                     (Remediation.closed_on > Remediation.closed_due_on, "late"), else_="on_time")
        for month, how, n in (db.query(month_of, ended, func.count())
                              .filter(Remediation.project_id.in_(ids), Remediation.status == "closed",
                                      Remediation.closed_on.isnot(None), Remediation.closed_on >= first,
                                      Remediation.closed_on <= today)
                              .group_by(month_of, ended)):
            months.setdefault(month, {"on_time": 0, "late": 0, "no_deadline": 0})[how] = n
    return {"as_of": today.isoformat(), "days": days, "daily": daily,
            "closed_by_month": [{"month": key, **months[key]} for key in sorted(months)]}


def first_of_twelve_months(today: date) -> date:
    """The first day of the month eleven months before this one: with the
    current month that is exactly twelve, whatever their lengths."""
    months = today.year * 12 + (today.month - 1) - 11
    return date(months // 12, months % 12 + 1, 1)


# --- following up with a contact ---------------------------------------------

FOLLOW_UP_MAX_ROWS = 500


def follow_up(db, project_ids: Sequence[int], contact_email: str, *,
              policy: Optional[deadlines.Policy] = None, today: Optional[date] = None) -> dict:
    """What to say to one contact: their overdue and due-soon findings on
    hosts, and the message as plain text to paste into mail or chat.  Nothing
    is sent by the server.

    ``items`` (and the message) list at most ``FOLLOW_UP_MAX_ROWS``, the most
    overdue first; ``total`` is every at-risk row of the contact and
    ``not_listed`` how many of them the list leaves out, which the message
    says.  Recording the follow-up covers all of them, listed or not."""
    policy = policy or deadlines.load(db)
    today = today or policy.today()
    email = contact_email.strip().lower()
    listing = list_rows(db, project_ids, contact_email=email, state=deadlines.AT_RISK_STATES,
                        group="due", limit=FOLLOW_UP_MAX_ROWS, policy=policy, today=today)
    items = listing["items"]
    not_listed = listing["total"] - len(items)
    name = next((row["contact_name"] for row in items if row["contact_name"]), None)
    return {"contact_email": email, "contact_name": name, "as_of": today.isoformat(),
            "overdue": listing["state_counts"]["overdue"], "due_soon": listing["state_counts"]["due_soon"],
            "items": items, "has_more": listing["has_more"],
            "total": listing["total"], "not_listed": not_listed,
            "project_ids": sorted({row["project_id"] for row in items}),
            "text": follow_up_text(name, items, today, several_projects=len(set(project_ids)) > 1,
                                   not_listed=not_listed)}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def follow_up_text(name: Optional[str], items: Sequence[dict], today: date, *,
                   several_projects: bool = False, not_listed: int = 0) -> str:
    """Plain text: tool-neutral, no markup, one line per finding on a host.
    ``not_listed``: at-risk rows of the contact that ``items`` leaves out."""
    def line(row: dict) -> str:
        where = row["ip_address"] + (f" ({row['hostname']})" if row["hostname"] else "")
        left = row["days_left"]
        when = (f"{_plural(-left, 'day', 'days')} overdue" if left < 0
                else "due today" if left == 0 else f"due in {_plural(left, 'day', 'days')}")
        project = f" [{row['project_name']}]" if several_projects else ""
        return (f"- {where}: {row['finding_title']} ({str(row['severity']).lower()}){project} - "
                f"due {row['due_on']}, {when}")

    overdue = [row for row in items if row["state"] == "overdue"]
    soon = [row for row in items if row["state"] == "due_soon"]
    parts = [f"Hello{' ' + name if name else ''},", "",
             f"This is a follow-up on findings assigned to you for remediation, as of {today.isoformat()}."]
    if overdue:
        parts += ["", f"Past the remediation deadline ({len(overdue)}):", *map(line, overdue)]
    if soon:
        parts += ["", f"Approaching the deadline ({len(soon)}):", *map(line, soon)]
    if not_listed > 0:
        parts += ["", f"{_plural(not_listed, 'more finding on a host is', 'more findings on hosts are')} "
                      f"overdue or approaching the deadline and not listed here; the list above is the "
                      f"{len(items)} that need attention first."]
    if not items:
        parts += ["", "Nothing assigned to you is overdue or approaching its deadline."]
    parts += ["", "Please reply with the status of each, or the date you expect it to be fixed."]
    return "\n".join(parts)


def record_follow_up(db, project_id: int, body, who) -> dict:
    """Record that the contact was chased about their at-risk rows in this
    project: one immutable timeline entry per finding on a host, and the day
    on each row (``last_follow_up_on``), so two people do not chase the same
    contact and an overdue row nobody chased can be seen."""
    policy = deadlines.load(db)
    today = policy.today()
    on = body.followed_up_on or today
    if on > today:
        raise HTTPException(422, {"message": "Nothing was recorded.", "problems": [
            f"followed_up_on cannot be in the future (today is {today.isoformat()})"]})
    # Every at-risk row of the contact, resolved in SQL with no page cut: the
    # preview lists the first ``FOLLOW_UP_MAX_ROWS`` by deadline, and a
    # follow-up recorded from another page of rows must not be refused.
    at_risk = (_rows(db, [project_id])
               .filter(Remediation.contact_email == body.contact_email,
                       deadlines.state_expr(db, policy, today).in_(deadlines.AT_RISK_STATES)))
    if body.finding_host_ids is not None:
        named = set(body.finding_host_ids)
        ids = [fh_id for (fh_id,) in at_risk.filter(FindingHost.id.in_(named))]
        unknown = sorted(named - set(ids))
        if unknown:
            raise HTTPException(422, {"message": "Nothing was recorded.", "problems": [
                f"finding_host_id {i} is not an overdue or due-soon row of this contact" for i in unknown[:50]]})
    else:
        ids = [fh_id for (fh_id,) in at_risk]
    if not ids:
        return {"recorded": 0, "already_recorded": 0, "followed_up_on": on.isoformat(), "finding_host_ids": []}
    records = _lock_for_write(db, ids)
    targets = {fh.id: fh for fh in db.query(FindingHost).options(joinedload(FindingHost.finding))
               .filter(FindingHost.id.in_(ids))}
    now = datetime.now(timezone.utc)
    happened = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc) if on != today else now
    # A row already followed up on that day is left alone: a second click, or
    # a second admin the same day, is not a second follow-up.
    already = [r for r in records if r.last_follow_up_on == on]
    records = [r for r in records if r.last_follow_up_on != on]
    for record in records:
        if record.last_follow_up_on is None or record.last_follow_up_on < on:
            record.last_follow_up_on = on
        db.add(_event(project_id, targets[record.finding_host_id], who, kind="follow_up",
                      body=body.note, new_value=body.contact_email, occurred_at=happened, created_at=now))
    db.flush()
    return {"recorded": len(records), "already_recorded": len(already), "followed_up_on": on.isoformat(),
            "finding_host_ids": [r.finding_host_id for r in records]}
