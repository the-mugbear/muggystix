"""The installation's remediation settings and the ONE definition of a
deadline (v2.461.0).

Remediation tracking is a decision of the installation: a team that uses
BlueStick to write reports never sees it; a team that follows findings to
their fix turns it on in System settings.  Off means off everywhere — every
remediation route answers 404, Oversight carries no remediation block and no
alert is raised — so "no target, nothing is ever called overdue" still holds
for an installation that did not opt in.

A deadline is DERIVED while a row is open: the day it was assigned
(``notified_on``) plus the days this installation gives the finding's CURRENT
severity.  Changing the timeline or the severity therefore moves every open
deadline at once, and there is no stored date to drift.  Closing a row freezes
the deadline then in force (``closed_due_on``).

A project admin may set one row's deadline by hand (``due_override_on``, with
a reason on the timeline): that date then IS the deadline, whatever the
policy, the severity or the assigned date say, until it is cleared.

States, exclusive, decided in this order:

"Today" is the day in the installation's time zone (``Policy.today``): the
dates are entered by hand from a local calendar, and against the server's UTC
day a row became overdue in the middle of the afternoon.

* ``closed`` / ``deferred`` — the recorded status; a deferred row has no clock.
* ``no_deadline`` — open, and the severity has no timeline (informational).
* ``not_assigned`` — open, a timeline applies, no assigned date: the clock has
  not started, so the row can never read as overdue.
  (A row with a hand-set deadline is neither: its clock runs.)
* ``overdue`` — the deadline is before today.
* ``due_soon`` — the deadline is today or within the "due soon" window.
* ``on_track`` — the rest.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import Date, Integer, and_, case, cast, func, literal, null, or_
from sqlalchemy.orm import Session

from app.db.models_findings import Finding, FindingHost, FindingHostStatus
from app.db.models_remediation import (
    DEFAULT_DUE_SOON_DAYS, DEFAULT_TIME_ZONE, DEFAULT_TIMELINE_DAYS, TIMELINE_SEVERITIES,
    FindingHostRemediation as Remediation, RemediationPolicy,
)

#: Every state, in the order a work list shows them: what needs someone first.
STATES = ("overdue", "due_soon", "on_track", "not_assigned", "no_deadline", "deferred", "closed")
#: The states of a row whose recorded status is ``open``.
OPEN_STATES = ("overdue", "due_soon", "on_track", "not_assigned", "no_deadline")
#: The states an alert is raised for, and a follow-up is about.
AT_RISK_STATES = ("overdue", "due_soon")

NOT_ENABLED = "Remediation tracking is not enabled on this installation."


def zone(name: Optional[str]) -> Optional[ZoneInfo]:
    """The IANA time zone called ``name``, or None when there is none."""
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


@dataclass(frozen=True)
class Policy:
    enabled: bool
    days: dict            # severity -> days, or None for "no deadline"
    due_soon_days: int
    # Where the installation's day begins and ends.  Assigned and closed dates
    # are entered by hand from a local calendar, so "today" — and with it
    # every state — is that calendar's day, not the server clock's.
    time_zone: str = DEFAULT_TIME_ZONE

    def days_for(self, severity: Optional[str]) -> Optional[int]:
        return self.days.get((severity or "").lower())

    def today(self, now: Optional[datetime] = None) -> date:
        """The installation's current day: what every state is derived
        against and what the dialogs default to.  A stored zone this host no
        longer knows reads as UTC."""
        now = now or datetime.now(timezone.utc)
        return now.astimezone(zone(self.time_zone) or timezone.utc).date()

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "days": dict(self.days), "due_soon_days": self.due_soon_days,
                "time_zone": self.time_zone}


DEFAULT = Policy(False, dict(DEFAULT_TIMELINE_DAYS), DEFAULT_DUE_SOON_DAYS)


def load(db: Session) -> Policy:
    row = db.get(RemediationPolicy, 1)
    if row is None:
        return DEFAULT
    return Policy(bool(row.enabled),
                  {severity: getattr(row, f"days_{severity}") for severity in TIMELINE_SEVERITIES},
                  row.due_soon_days, row.time_zone or DEFAULT_TIME_ZONE)


def save(db: Session, body, user_id: Optional[int]) -> tuple[Policy, Policy]:
    """Write what the caller sent; returns ``(before, after)``.  The row is
    locked, so two admins saving at once do not interleave fields."""
    row = db.query(RemediationPolicy).filter(RemediationPolicy.id == 1).with_for_update().first()
    before = load(db)
    if row is None:
        row = RemediationPolicy(id=1, enabled=False, due_soon_days=DEFAULT_DUE_SOON_DAYS,
                                time_zone=DEFAULT_TIME_ZONE,
                                **{f"days_{s}": d for s, d in DEFAULT_TIMELINE_DAYS.items()})
        db.add(row)
    sent = body.model_fields_set
    if "enabled" in sent and body.enabled is not None:
        row.enabled = body.enabled
    if "due_soon_days" in sent and body.due_soon_days is not None:
        row.due_soon_days = body.due_soon_days
    if "days" in sent and body.days is not None:
        for severity, days in body.days.items():
            setattr(row, f"days_{severity}", days)
    if "time_zone" in sent and body.time_zone is not None:
        row.time_zone = body.time_zone
    row.updated_by_id = user_id
    db.flush()
    return before, load(db)


def proposed(policy: Policy, body) -> Policy:
    """``policy`` with the timelines, the warning window and the time zone
    the caller sent — what ``save`` would store, without storing it.  Whether
    the feature is on is not part of a preview."""
    sent = body.model_fields_set
    days = dict(policy.days)
    if "days" in sent and body.days is not None:
        days.update(body.days)
    return Policy(
        policy.enabled, days,
        body.due_soon_days if "due_soon_days" in sent and body.due_soon_days is not None else policy.due_soon_days,
        body.time_zone if "time_zone" in sent and body.time_zone is not None else policy.time_zone,
    )


def require_enabled(db: Session) -> Policy:
    """404, not 403: on an installation that did not opt in there is no such
    feature, for anyone."""
    policy = load(db)
    if not policy.enabled:
        raise HTTPException(status_code=404, detail=NOT_ENABLED)
    return policy


# --- the deadline, as SQL ---------------------------------------------------

def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _days_case(policy: Policy):
    """The days for the finding's severity; NULL when it has no timeline."""
    whens = {severity: days for severity, days in policy.days.items() if days is not None}
    if not whens:
        return null()
    return case(whens, value=func.lower(Finding.severity), else_=None)


def policy_due_expr(db: Session, policy: Policy):
    """What the POLICY alone gives an open row: assigned date + the days for
    the finding's severity.  NULL when either is missing."""
    if not any(days is not None for days in policy.days.values()):
        return cast(null(), Date)
    if _is_postgres(db):
        return Remediation.notified_on + _days_case(policy)        # date + integer is a date
    modifier = case({severity: f"+{days} days" for severity, days in policy.days.items() if days is not None},
                    value=func.lower(Finding.severity), else_=None)
    return func.date(Remediation.notified_on, modifier)            # SQLite; NULL modifier gives NULL


def due_for_severity_expr(db: Session, policy: Policy, severity: Optional[str]):
    """The policy's date for a row as if its finding had ``severity`` — what a
    severity change moves a deadline from and to.  NULL without a timeline for
    that severity or without an assigned date."""
    days = policy.days_for(severity)
    if days is None:
        return cast(null(), Date)
    if _is_postgres(db):
        return Remediation.notified_on + literal(days, Integer)
    return func.date(Remediation.notified_on, f"+{days} days")


def due_expr(db: Session, policy: Policy):
    """An OPEN row's deadline in force: the date set by hand
    (``due_override_on``) when there is one, the policy's otherwise.  NULL
    when neither gives one."""
    return func.coalesce(Remediation.due_override_on, policy_due_expr(db, policy))


def _day(db: Session, value: date):
    """A date to compare with ``due_expr`` (SQLite's is text)."""
    return literal(value, Date) if _is_postgres(db) else literal(value.isoformat())


def state_expr(db: Session, policy: Policy, today: date):
    status = func.coalesce(Remediation.status, "open")
    due = due_expr(db, policy)
    # A date set by hand is a running clock by itself: such a row is never
    # "no deadline" or "not assigned".
    by_policy = Remediation.due_override_on.is_(None)
    return case(
        (status == "closed", "closed"),
        (status == "deferred", "deferred"),
        (and_(by_policy, _days_case(policy).is_(None)), "no_deadline"),
        (and_(by_policy, Remediation.notified_on.is_(None)), "not_assigned"),
        (due < _day(db, today), "overdue"),
        (due <= _day(db, today + timedelta(days=policy.due_soon_days)), "due_soon"),
        else_="on_track",
    )


def within_days(db: Session, policy: Policy, today: date, days: int):
    """An open row's deadline falls on or before ``today + days``."""
    return due_expr(db, policy) <= _day(db, today + timedelta(days=days))


#: What an admin is to look at beside the deadline states.
FLAGS = ("deferral_review_due", "deadline_overridden")


def flag_exprs(db: Session, today: date) -> dict:
    """The ONE definition of each flag, as a SQL condition.

    * ``deferral_review_due`` — the row is deferred and its review date is
      today or past, or it has none (deferred before review dates existed).
    * ``deadline_overridden`` — the row's deadline was set by hand.
    """
    review = Remediation.deferred_review_on
    return {
        "deferral_review_due": and_(Remediation.status == "deferred",
                                    or_(review.is_(None), review <= _day(db, today))),
        "deadline_overridden": Remediation.due_override_on.isnot(None),
    }


#: Where the contact's record and the assessor's conclusion about the same
#: finding on a host disagree.  The record's ``closed`` is the CONTACT's claim
#: (shown as "Reported fixed"); the endpoint's ``remediated`` is the ASSESSOR's
#: conclusion (shown as "Remediated").  Neither is ever written from the other,
#: so the gap between them is a state of its own.
VERIFICATIONS = ("reported_fixed_not_retested", "remediated_record_open")


def verification_expr():
    """The ONE definition of that gap; NULL where the two agree or have
    nothing to say to each other.

    * ``reported_fixed_not_retested`` — the record is ``closed`` and the
      endpoint is neither ``remediated`` nor a false positive: the contact says
      fixed, the team has not concluded so.
    * ``remediated_record_open`` — the endpoint is ``remediated`` and a
      record EXISTS that is ``open`` or ``deferred``: the team concluded
      fixed, the record still reads as work.  An endpoint nobody ever tracked
      has no record to be "still open".
    """
    status = func.coalesce(Remediation.status, "open")
    endpoint = FindingHost.host_status
    remediated = FindingHostStatus.REMEDIATED.value
    return case(
        (and_(status == "closed",
              endpoint.notin_((remediated, FindingHostStatus.FALSE_POSITIVE.value))),
         "reported_fixed_not_retested"),
        (and_(endpoint == remediated, Remediation.id.isnot(None), status != "closed"),
         "remediated_record_open"),
        else_=None,
    )


#: How long past its deadline an overdue row is: ``(name, first day, last day)``.
OVERDUE_BANDS = (("1-7", 1, 7), ("8-30", 8, 30), ("31-90", 31, 90), ("90+", 91, None))


def overdue_days_expr(db: Session, policy: Policy, today: date):
    """Whole days past the deadline (positive once overdue); NULL without one."""
    due = due_expr(db, policy)
    if _is_postgres(db):
        return literal(today, Date) - due                              # date - date is whole days
    return cast(func.julianday(today.isoformat()) - func.julianday(due), Integer)


def overdue_band_expr(db: Session, policy: Policy, today: date):
    """The band of an OVERDUE row; NULL for every other row."""
    days = overdue_days_expr(db, policy, today)
    whens = [(days <= last, name) if last is not None else (days >= first, name)
             for name, first, last in OVERDUE_BANDS]
    return case((state_expr(db, policy, today) != "overdue", None), *whens, else_=None)


def state_rank(state):
    """Order by state as ``STATES`` lists them."""
    return case({name: index for index, name in enumerate(STATES)}, value=state, else_=len(STATES))


def as_date(value) -> Optional[date]:
    """SQLite hands a computed date back as text."""
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
