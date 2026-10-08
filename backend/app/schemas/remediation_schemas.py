"""Remediation tracking: one contract for the page and for agents."""
import re
from datetime import date, datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The CONTACT's progress.  ``closed`` is the contact's claim that it is fixed
#: — every page says "Reported fixed" — and is not the assessor's conclusion
#: (the endpoint's ``remediated``).
RemediationStatus = Literal["open", "closed", "deferred"]
#: Where that record and the assessor's endpoint status disagree
#: (``remediation_policy.VERIFICATIONS``).
Verification = Literal["reported_fixed_not_retested", "remediated_record_open"]
#: Where a row stands against its deadline — ``remediation_policy.STATES``.
RemediationState = Literal["overdue", "due_soon", "on_track", "not_assigned", "no_deadline", "deferred", "closed"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Grouping = Literal["host", "finding", "contact", "due", "team"]
#: How long past its deadline an overdue row is (``remediation_policy.OVERDUE_BANDS``).
OverdueBand = Literal["1-7", "8-30", "31-90", "90+"]
#: The longest timeline an installation can set (ten years).
TIMELINE_MAX_DAYS = 3650

#: Finding-on-host rows one call may change (a row naming only a finding
#: counts every host it expands to).
APPLY_MAX_TARGETS = 500
NOTE_MAX_CHARS = 10_000

# Deliberately loose: an address is checked for shape, never for existence.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def no_nul(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL is not allowed in text")
        return value


class NoteIn(_Base):
    body: str = Field(..., min_length=1, max_length=NOTE_MAX_CHARS)
    occurred_at: Optional[datetime] = Field(
        None, description="When it happened, if not now (a call made on Tuesday, recorded on Thursday).",
    )
    request_key: Optional[str] = Field(
        None, min_length=1, max_length=80,
        description="Stable key for this note; send the same one on a re-run so it is not added twice.",
    )


class TrackedFields(_Base):
    """The tracked fields.  A field that is left out is not touched; an
    explicit null clears it (status cannot be cleared)."""
    contact_email: Optional[str] = Field(None, max_length=254)
    contact_name: Optional[str] = Field(None, max_length=200)
    team: Optional[str] = Field(None, max_length=100, description="The group that owns the fix.")
    notified_on: Optional[date] = None
    status: Optional[RemediationStatus] = Field(None, description=(
        "The contact's progress. `closed` means the contact REPORTED it fixed (shown as "
        "\"Reported fixed\"); it does not change the assessor's endpoint status, whose "
        "`remediated` is the team's own conclusion."))
    closed_on: Optional[date] = Field(None, description=(
        "The day the contact reported it fixed; goes only with status `closed`."))

    @field_validator("contact_email")
    @classmethod
    def email_shape(cls, value):
        if value is None or value == "":
            return None
        value = value.lower()
        if not _EMAIL.match(value):
            raise ValueError("contact_email must be an email address")
        return value

    @field_validator("contact_name", "team")
    @classmethod
    def blank_is_none(cls, value):
        return value or None

    def changes(self) -> dict:
        """Only what the caller sent."""
        return {name: getattr(self, name) for name in self.model_fields_set
                if name in TrackedFields.model_fields}


class ApplyRow(TrackedFields):
    """One change.  Name the finding on a host by ``finding_host_id``, or by
    ``finding_id`` with ``host_id``; ``finding_id`` alone means every host of
    that finding."""
    finding_host_id: Optional[int] = Field(None, gt=0)
    finding_id: Optional[int] = Field(None, gt=0)
    host_id: Optional[int] = Field(None, gt=0)
    notes: list[NoteIn] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def one_target(self):
        if (self.finding_host_id is None) == (self.finding_id is None):
            raise ValueError("give finding_host_id, or finding_id (with host_id for one host)")
        if self.finding_host_id is not None and self.host_id is not None:
            raise ValueError("host_id goes with finding_id, not with finding_host_id")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be cleared")
        if self.closed_on is not None and self.status in ("open", "deferred"):
            raise ValueError("closed_on goes with status closed")
        return self


class ApplyBody(_Base):
    rows: list[ApplyRow] = Field(..., min_length=1, max_length=APPLY_MAX_TARGETS)
    dry_run: bool = Field(False, description="Report what would change and write nothing.")
    overwrite: bool = Field(
        False,
        description="Replace a value that is already set to something else. Without it such a "
                    "field is reported as a conflict and left alone.",
    )
    agent_model: Optional[str] = Field(None, max_length=100)


class NoteCreate(NoteIn):
    host_id: int = Field(..., gt=0)
    finding_host_id: Optional[int] = Field(None, gt=0)


class FollowUpBody(_Base):
    """Record that a contact was chased about their overdue and due-soon
    findings on hosts in this project."""
    contact_email: str = Field(..., min_length=3, max_length=254)
    # Not after the installation's today — checked by the service, which knows
    # the installation's time zone.
    followed_up_on: Optional[date] = Field(None, description="The day it happened, if not today.")
    note: Optional[str] = Field(None, max_length=NOTE_MAX_CHARS,
                                description="What was said or agreed, kept on each host's timeline.")
    finding_host_ids: Optional[list[int]] = Field(
        None, min_length=1, max_length=500,
        description="Only these of the contact's at-risk rows; left out, all of them.",
    )

    @field_validator("contact_email")
    @classmethod
    def lowered(cls, value):
        return value.lower()

    @field_validator("note")
    @classmethod
    def blank_is_none(cls, value):
        return value or None

class ContactReportBody(_Base):
    """Prepare one contact's remediation list as a document."""
    contact_email: str = Field(..., min_length=3, max_length=254)
    format: Literal["contact-docx", "contact-html"] = "contact-docx"

    @field_validator("contact_email")
    @classmethod
    def lowered(cls, value):
        return value.lower()


class PolicyUpdate(_Base):
    """The installation's remediation settings.  A field left out is not
    touched.  In ``days`` a severity set to null has no deadline."""
    enabled: Optional[bool] = None
    days: Optional[dict[Severity, Optional[int]]] = None
    due_soon_days: Optional[int] = Field(None, ge=0, le=365)
    time_zone: Optional[str] = Field(
        None, min_length=1, max_length=64,
        description="IANA name of the zone whose calendar day is 'today' for deadlines, e.g. Europe/Paris.",
    )

    @field_validator("time_zone")
    @classmethod
    def known_zone(cls, value):
        if value is None:
            return None
        try:
            return ZoneInfo(value).key
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("time_zone must be an IANA time zone name, such as UTC, "
                             "Europe/Paris or America/Los_Angeles")

    @field_validator("days")
    @classmethod
    def sensible_days(cls, value):
        for severity, days in (value or {}).items():
            if days is not None and not 1 <= days <= TIMELINE_MAX_DAYS:
                raise ValueError(f"days for {severity} must be between 1 and {TIMELINE_MAX_DAYS}, or null")
        return value


class NoteUpdate(_Base):
    body: Optional[str] = Field(None, min_length=1, max_length=NOTE_MAX_CHARS)
    occurred_at: Optional[datetime] = None
