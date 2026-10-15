"""What a finding still needs before the report goes out — the ONE definition.

Three kinds, each with one SQL condition on ``Finding`` (so a list can be
filtered by it) and one figure per row (so the row can say it):

* ``proposals`` — a proposal about it waits for a decision
  (``proposal_service``'s "about a finding");
* ``missing_text`` — the client report would include it and a required
  report section is empty (``client_report_service``'s rule and field list);
* ``uncaptioned_images`` — the client report would include it and an image
  ticked "In report", in a format the report prints, has no caption: the
  report prints the file's name under the figure
  (``report_images.caption_or_filename``).

The Findings list (``FindingService._selection`` and its rows), the agents'
read of it and Operations' "Findings that need me" all come here; the report
summary's ``uncaptioned_images`` is the same rule applied to the report's
own dataset (``report_images.caption_missing``).

"Under investigation" is a finding's STATUS: Operations lists it among what
its owner has to do (``with_status``), the Findings list has a column for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.db.models import Annotation, NoteAttachment
from app.db.models_findings import Finding
from app.schemas.findings import FindingNeed
from app.services.report_text import STRIP_WHITESPACE

#: The kinds, in the order a row says them.
NEED_KINDS = ("missing_text", "uncaptioned_images", "proposals")
#: ``needs=`` on a findings list: one kind, or ``any`` of them.
ANY_NEED = "any"
NEED_FILTERS = (ANY_NEED, *NEED_KINDS)



def _uncaptioned_image_of_finding():
    """WHERE clauses: a ``NoteAttachment`` (with its ``Annotation``) that is
    a report image of this ``Finding`` without a caption.  Whose image it is
    follows ``client_report_service.finding_image_attachments`` — the
    finding's own comments, or its source-note thread (the root and its
    replies); ``test_finding_needs`` pins the two together."""
    from app.services.report_images import REPORT_IMAGE_TYPES

    return (
        NoteAttachment.annotation_id == Annotation.id,
        NoteAttachment.include_in_report.is_(True),
        NoteAttachment.content_type.in_(tuple(REPORT_IMAGE_TYPES)),
        func.btrim(func.coalesce(NoteAttachment.caption, ""), STRIP_WHITESPACE) == "",
        or_(
            Annotation.finding_id == Finding.id,
            Annotation.id == Finding.evidence_annotation_id,
            Annotation.thread_root_id == Finding.evidence_annotation_id,
        ),
    )


def need_condition(kind: str):
    """SQL: this ``Finding`` has that need (``any``: at least one of them).
    Raises ``ValueError`` for a word that is not in ``NEED_FILTERS``."""
    from app.services import proposal_service
    from app.services.client_report_service import (
        missing_required_text_condition, reportable_finding_condition,
    )

    if kind == "proposals":
        return proposal_service.has_pending_proposal_condition()
    if kind == "missing_text":
        return and_(reportable_finding_condition(), missing_required_text_condition())
    if kind == "uncaptioned_images":
        return and_(
            reportable_finding_condition(),
            exists().where(*_uncaptioned_image_of_finding()).correlate(Finding),
        )
    if kind == ANY_NEED:
        return or_(*(need_condition(k) for k in NEED_KINDS))
    raise ValueError(f"needs must be one of {NEED_FILTERS}, not {kind!r}")


@dataclass(frozen=True)
class FindingNeeds:
    """One finding's needs: the sentences a row shows, and the figures
    behind them."""
    needs: List[FindingNeed] = field(default_factory=list)
    missing_text: List[str] = field(default_factory=list)
    uncaptioned_images: int = 0
    pending_proposals: int = 0


def has_endpoint_in(state: str):
    """``Finding`` has at least one endpoint in this state — a correlated
    EXISTS (the Findings list's ``host_state=`` for every state but
    ``retest``, which is :func:`has_retest_standing`)."""
    from app.db.models_findings import FindingHost
    return exists().where(FindingHost.finding_id == Finding.id, FindingHost.host_status == state)


# --- "a retest stands" — ONE definition ---------------------------------------
# Third review 2026-10-15, R1 (owner: "Proceed with all"): the guard read
# "Retest pending OR a request kept", every list and count read "Retest
# pending" only — so a request kept after a hand move to Still present was on
# no list, and a finding judged false positive kept its hosts on the retest
# list.  A retest STANDS on an endpoint while its request is recorded
# (``retest_requested_at`` — stamped on every move into ``retest``, kept on a
# hand move to Still present, cleared by a recorded result, Remediated or a
# false-positive judgement) and the FINDING is not judged a false positive
# (no defect is concluded there: ``remediation_policy.concluded_no_defect``).
# ``host_status = 'retest'`` is what the row SHOWS, not what is counted.

def retest_stands():
    """The SQL condition on a ``FindingHost`` row (with its ``Finding`` in
    the statement or correlated): a retest stands on it."""
    from app.db.models_findings import FindingHost, FindingStatus
    return and_(
        FindingHost.retest_requested_at.isnot(None),
        Finding.status != FindingStatus.FALSE_POSITIVE.value,
    )


def retest_stands_on(row, finding: Finding) -> bool:
    """:func:`retest_stands` for one loaded row — the guard of
    ``FindingService._move_endpoint`` and ``record_retest``, and the
    ``retest_stands`` each endpoint of a single finding carries.  Change both
    together (``test_retest_workflow.py`` pins them to each other)."""
    from app.db.models_findings import FindingStatus
    return row.retest_requested_at is not None and finding.status != FindingStatus.FALSE_POSITIVE.value


def has_retest_standing():
    """``Finding`` has an endpoint a retest stands on — a correlated EXISTS:
    the Findings list's ``host_state=retest``, Operations' ``decide`` /
    ``need=retest`` and the agents' lists."""
    from app.db.models_findings import FindingHost
    return exists().where(FindingHost.finding_id == Finding.id, retest_stands())


def _hosts_to_retest():
    """How many of the finding's HOSTS a retest stands on — distinct hosts,
    so a host under two names is one (a scalar subquery correlated to
    ``Finding``)."""
    from app.db.models_findings import FindingHost
    return (
        select(func.count(func.distinct(FindingHost.host_id)))
        .where(FindingHost.finding_id == Finding.id, retest_stands())
        .correlate(Finding)
        .scalar_subquery()
    )


def needs_of(
    db: Session, finding_ids: Sequence[int], *, with_status: bool = False,
) -> Dict[int, FindingNeeds]:
    """Every listed finding's needs, in TWO statements whatever the page
    holds: the report's side (empty required sections and uncaptioned report
    images, both only for a finding the report would include) and the pending
    proposals.  Each figure is the count behind the ``need_condition`` of the
    same name, so a row never says what its filter would not list.

    ``with_status`` puts "N hosts awaiting a retest" and "under
    investigation" first (Operations)."""
    from app.services import proposal_service
    from app.services.client_report_service import (
        REQUIRED_TEXT, UNDER_INVESTIGATION, blank_required_text, reportable_finding_condition,
    )

    ids = list(finding_ids)
    if not ids:
        return {}
    blank = blank_required_text()
    uncaptioned = (
        select(func.count(NoteAttachment.id))
        .where(*_uncaptioned_image_of_finding())
        .correlate(Finding)
        .scalar_subquery()
    )
    reportable = reportable_finding_condition()
    rows = (
        db.query(
            Finding.id, Finding.status, reportable.label("reportable"),
            *[blank[k].label(f"blank_{k}") for k in REQUIRED_TEXT],
            case((reportable, uncaptioned), else_=0).label("uncaptioned"),
            # Operations only: how many of its hosts await a retest.
            (_hosts_to_retest() if with_status else literal(0)).label("hosts_to_retest"),
        )
        .filter(Finding.id.in_(ids))
        .all()
    )
    pending = proposal_service.pending_per_finding(db, ids)

    out: Dict[int, FindingNeeds] = {}
    for row in rows:
        needs: List[FindingNeed] = []
        if with_status and int(row.hosts_to_retest or 0):
            n = int(row.hosts_to_retest)
            needs.append(FindingNeed(kind="retest", text=f"{n} host{'' if n == 1 else 's'} awaiting a retest",
                                     count=n))
        if with_status and row.status in UNDER_INVESTIGATION:
            needs.append(FindingNeed(kind="under_investigation", text="under investigation", count=1))
        missing = [k for k in REQUIRED_TEXT if getattr(row, f"blank_{k}")] if row.reportable else []
        if missing:
            needs.append(FindingNeed(
                kind="missing_text", text=f"report text missing: {', '.join(missing)}",
                count=len(missing),
            ))
        images = int(row.uncaptioned or 0)
        if images:
            needs.append(FindingNeed(
                kind="uncaptioned_images",
                text=f"{images} report image{'' if images == 1 else 's'} without a caption",
                count=images,
            ))
        proposals = int(pending.get(row.id, 0))
        if proposals:
            needs.append(FindingNeed(
                kind="proposals",
                text=f"{proposals} proposal{'' if proposals == 1 else 's'} to decide",
                count=proposals,
            ))
        out[row.id] = FindingNeeds(
            needs=needs, missing_text=missing, uncaptioned_images=images, pending_proposals=proposals,
        )
    return out
