"""The Finding spine (foundation phase 3).

One ``Finding`` is the settled *record* that everything rolls up by — it
unifies the three previously-disconnected result streams:

  * promoted annotations  (source='note', evidence -> the annotation thread)
  * scanner vulnerabilities (source='scanner', vuln_id reference)
  * test results that found something (source='execution'; the evidence
    records point at the finding through ``EvidenceRecord.finding_id``)

It carries the lifecycle the old ``SecurityFinding`` never had (status +
owner) and a many-to-many to hosts (``finding_hosts``) so one finding —
"SMB signing disabled" — is managed once and attached to N hosts.

References, never copies: a scanner/execution-sourced finding points AT the
source row rather than duplicating it, so there's no second dedup problem.

Status/severity/source are ``String`` columns (not Postgres ENUM) so the
vocabulary can evolve without an ``ALTER TYPE`` migration; the enums below
are the canonical value sets, enforced in application code.
"""
import enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index, event, text, update,
)
from sqlalchemy.orm import Session, relationship
from sqlalchemy.orm.util import identity_key
from sqlalchemy.sql import func

from app.db.session import Base


class FindingSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_RISK = "accepted_risk"
    REMEDIATED = "remediated"
    # No RETEST since 2.501.0 (owner, 2026-10-14): a retest is per host —
    # ``FindingHostStatus.RETEST``.  Revision ``e5c1a7b3d9f2`` moved every
    # finding off it; history rows may still name it as text.


class FindingSource(str, enum.Enum):
    NOTE = "note"
    SCANNER = "scanner"
    EXECUTION = "execution"
    MANUAL = "manual"


class FindingHostStatus(str, enum.Enum):
    """Per-host disposition within a multi-host finding — drives retest
    deltas ('was open on this host, now remediated')."""
    OPEN = "open"
    REMEDIATED = "remediated"
    RETEST = "retest"
    # v2.360.0 — the issue does not apply to THIS endpoint (a backported
    # package, a scanner misfire here).  It says nothing about the finding's
    # other hosts: dismissing one host's observation used to mark the issue a
    # false positive on every host that carries it.
    FALSE_POSITIVE = "false_positive"


#: Which findings ``uq_finding_scanner_issue`` covers (R8).  The migration
#: carries its own copy — a revision never imports from the models.
SCANNER_ISSUE_PREDICATE = (
    "source = 'scanner' AND dedup_key IS NOT NULL AND substr(dedup_key, 1, 4) <> 'row:'"
)


class Finding(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    title = Column(String(500), nullable=False)
    severity = Column(String(20), nullable=False, index=True)   # FindingSeverity
    status = Column(
        String(20), nullable=False, default=FindingStatus.OPEN.value, index=True,
    )  # FindingStatus
    source = Column(String(20), nullable=False)                 # FindingSource
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Evidence / source references — vuln_id for a scanner-sourced finding;
    # evidence_annotation_id points at the originating annotation THREAD ROOT
    # for note-sourced findings.  (``exec_result_id`` went with test plans in
    # v2.442.0: evidence records carry ``finding_id`` instead.)
    evidence_annotation_id = Column(
        Integer, ForeignKey("annotations.id", ondelete="SET NULL"), nullable=True,
    )
    vuln_id = Column(
        Integer, ForeignKey("vulnerabilities.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    # Scanner-agnostic identity of the ISSUE this finding records — the value
    # from ``services.vuln_identity.issue_key`` (``cve:CVE-…`` or
    # ``title:<normalised>``). Declared since the spine landed but never
    # computed; wiring it in v2.236.0 is what makes promoting the Nessus row
    # and the GreenBone row for one issue converge on ONE finding instead of
    # forking two. Deliberately excludes the scanner, which is provenance
    # rather than identity.
    # The issue key of the observations this finding covers — the same width
    # as ``vulnerabilities.issue_key`` (a narrower column made long
    # title-keyed issues impossible to promote; migration e2a4c6f8b0d1).
    dedup_key = Column(String(600), nullable=True, index=True)

    # v2.379.0 — the finding's REPORT TEXT: what the client report says about
    # the issue.  Authored content (same rule as the title: its author, a
    # project admin or a global admin edits it), Markdown, seeded once at
    # promotion from the scanner row / source note and owned by people after
    # that — a later corroborating scanner never overwrites it.  Typed columns,
    # not a blob: the report page counts findings still missing text.
    description = Column(Text, nullable=True)
    impact = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    references = Column(Text, nullable=True)          # Markdown (usually a list of links)
    steps_to_reproduce = Column(Text, nullable=True)
    # v2.495.0 — the written text of the Evidence section.  The report prints
    # it before the images no section places (each after its note's text).
    evidence_text = Column(Text, nullable=True)
    # A 3.x / 2.0 vector determines the score (cvss_service); a 4.0 vector's
    # score is entered, since its scoring table is not reimplemented here.
    cvss_vector = Column(String(200), nullable=True)
    cvss_score = Column(Float, nullable=True)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # One more for every committed transaction that wrote the finding (revision
    # f2b8d4a6c1e3): every write route answers with the whole finding AND this
    # number, so a page holding two answers keeps the higher one — an answer
    # that arrives late never puts an older finding back.  Counted by ONE
    # listener (``_count_finding_revisions`` below), never by a caller.
    revision = Column(Integer, nullable=False, default=1, server_default=text("1"))

    project = relationship("Project")
    owner = relationship("User", foreign_keys=[owner_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    # foreign_keys pinned: annotations.finding_id is a SECOND annotations<->
    # findings path (a finding's own comment thread), so this must be explicit.
    evidence_annotation = relationship("Annotation", foreign_keys=[evidence_annotation_id])
    # Plain lazy (review 2026-10-01 C2, as ``Host`` in v2.393.0): this was
    # ``lazy="selectin"``, so EVERY whole-entity Finding query also fetched all
    # of its endpoints — a findings page of widespread issues loaded tens of
    # thousands of rows nothing read.  A path that reads endpoints names the
    # load (``selectinload(Finding.hosts)``); ``tests/test_finding_loading.py``
    # pins that querying findings is one statement.
    hosts = relationship(
        "FindingHost", back_populates="finding", cascade="all, delete-orphan",
    )
    status_history = relationship(
        "FindingStatusHistory", back_populates="finding",
        cascade="all, delete-orphan",
    )
    # Every scanner row evidencing this finding (see FindingVulnerability).
    # Plain lazy too (C2): nothing reads it through the relationship — the
    # readers query ``FindingVulnerability`` directly.
    vulnerabilities = relationship(
        "FindingVulnerability", back_populates="finding",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_finding_project_status", "project_id", "status"),
        # Review 2026-10-01 R8 — "one scanner finding per issue" is enforced by
        # the database (migration b2e5a8c1d4f6), not only by a read-then-insert:
        # two concurrent promotions of one issue used to insert two findings.
        # ``row:`` keys identify a single scanner row, not an issue, and are
        # left out (``substr`` rather than LIKE: no ``%`` in DDL, and SQLite's
        # test schema accepts it).
        Index(
            "uq_finding_scanner_issue", "project_id", "dedup_key", unique=True,
            postgresql_where=text(SCANNER_ISSUE_PREDICATE),
            sqlite_where=text(SCANNER_ISSUE_PREDICATE),
        ),
    )


class FindingHost(Base):
    """A host affected by a finding (the cross-host M2M).  Carries a per-host
    status so a finding remediated on one host but open on another reflects
    the truth, and retest deltas are per-host."""
    __tablename__ = "finding_hosts"

    id = Column(Integer, primary_key=True)
    finding_id = Column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False,
    )
    host_id = Column(
        Integer, ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Indexed (a4f8c2e6b0d1): deleting a port clears this by ``port_id``, which
    # was a scan of every finding endpoint per deleted port.
    port_id = Column(Integer, ForeignKey("ports_v2.id", ondelete="SET NULL"), nullable=True, index=True)
    # v2.323.0 — the NAMED endpoint on this host the finding applies to (the
    # stable identity: portal.example.com), inherited from the evidencing
    # vulnerability / plan entry.  The finding anchors to the name; the host
    # row is where it was observed at the time.  Null for host-level findings.
    name_id = Column(Integer, ForeignKey("dns_names.id", ondelete="SET NULL"), nullable=True, index=True)
    host_status = Column(String(20), nullable=False, default=FindingHostStatus.OPEN.value)
    # When the endpoint was last marked ``remediated`` (revision b7d2e5f9a1c3;
    # the remediation pages' time to remediate).  Written only by
    # ``FindingService._move_endpoint``, cleared when the endpoint leaves
    # ``remediated``.  Back-filled from the history line of each move; NULL on
    # a remediated endpoint whose move left no such line.
    remediated_at = Column(DateTime(timezone=True), nullable=True)
    # When the retest that stands on this endpoint was asked for (stamped and
    # cleared ONLY by ``FindingService._move_endpoint``, restored by an Undo):
    # a retest is closed by a result recorded since then.  Set on every
    # endpoint in ``retest`` (``ck_finding_hosts_retest_requested``; one
    # already in retest before 2.501.0 has the epoch — any result counts) and
    # kept on a hand move to Still present.  ``finding_needs.retest_stands``
    # is what reads it.
    retest_requested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    finding = relationship("Finding", back_populates="hosts")
    host = relationship("Host")
    name = relationship("DNSName", foreign_keys=[name_id])

    __table_args__ = (
        # v2.324.0 — one row per (finding, host, named endpoint): two vhosts on
        # one address affected by the same issue are two affected endpoints,
        # both kept.  NULLS NOT DISTINCT so unnamed (host-level) rows stay
        # unique per host on Postgres; SQLite (test fallback) ignores the flag.
        UniqueConstraint(
            "finding_id", "host_id", "name_id",
            name="uq_finding_host_name", postgresql_nulls_not_distinct=True,
        ),
        # A host Retest pending always carries its request: "a retest stands"
        # reads the request alone (third review 2026-10-15, R1/H2 — a row in
        # retest with none skipped the guard and was on no list).
        CheckConstraint(
            "host_status <> 'retest' OR retest_requested_at IS NOT NULL",
            name="ck_finding_hosts_retest_requested",
        ),
    )


# Endpoint states that are NOT live work on that host (v2.365.0 — one
# definition; it lived privately in report_generator).  ``remediated``: fixed
# here.  ``false_positive`` (v2.360.0): a host-only judgment — the finding may
# be confirmed elsewhere, but it is not an active finding HERE.  A finding's own
# status says nothing about a given host, so anything that attributes a finding
# to a host, site or subnet must apply BOTH filters.  The site / subnet
# summaries applied only the finding's, so a host dismissed as a false positive
# kept adding its site to the finding's exposure — and disagreed with reports.
INACTIVE_ENDPOINT_STATES = frozenset({
    FindingHostStatus.REMEDIATED.value, FindingHostStatus.FALSE_POSITIVE.value,
})


def finding_active_on_host():
    """SQL condition: this ``FindingHost`` row is live work on its host."""
    return FindingHost.host_status.notin_(tuple(INACTIVE_ENDPOINT_STATES))


# A finding still being worked: under investigation or confirmed.  The ONE
# tuple (review 2026-09-23 B-Debt-3 — five copies had drifted apart in which
# endpoint states they also honoured).  Per HOST, pair it with
# ``finding_active_on_host()``; per ISSUE, with
# ``engagement_metrics_service.finding_is_a_result()``.
ACTIVE_FINDING_STATUSES = (FindingStatus.OPEN.value, FindingStatus.CONFIRMED.value)


class FindingVulnerability(Base):
    """A scanner vulnerability that evidences a finding (v2.236.0).

    ``Finding.vuln_id`` is a single FK, so promoting the Nessus row and the
    GreenBone row for one issue produced two Findings and two entries in the
    client report — the duplication the inspector's grouping only hid. A
    Finding is the canonical issue; the scanner rows are *evidence* for it, and
    there can be several.

    ``Finding.vuln_id`` is retained as the ORIGINATING row (the one the analyst
    promoted from) rather than dropped — expand phase; the column still backs
    the idempotency check and existing reads.

    Corroboration is worth recording in its own right: "two independent
    scanners found this" is a stronger finding, and a report can say so.
    """
    __tablename__ = "finding_vulnerabilities"

    id = Column(Integer, primary_key=True)
    finding_id = Column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False,
    )
    vuln_id = Column(
        Integer, ForeignKey("vulnerabilities.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    finding = relationship("Finding", back_populates="vulnerabilities")

    __table_args__ = (
        UniqueConstraint("finding_id", "vuln_id", name="uq_finding_vulnerability"),
    )


class FindingStatusHistory(Base):
    """A finding's history: its status transitions (the status-transition
    recorder, app.services.status_history_service), lines about its endpoints,
    and — v2.519.0, revision ``b8e4d2f6a0c3`` — each edit of a field
    (``field`` set, with the value ``before_value`` / ``after_value``; written
    only by ``finding_actions.record_edit``).  Note status history, which
    shared it, was dropped in v2.447.0."""
    __tablename__ = "finding_status_history"

    id = Column(Integer, primary_key=True)
    finding_id = Column(
        Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    summary = Column(Text, nullable=True)
    # An edit's row: which field, and its value before and after (as shown —
    # an owner's name, a severity's label).  Null on a status / endpoint row.
    field = Column(String(40), nullable=True)
    before_value = Column(Text, nullable=True)
    after_value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    finding = relationship("Finding", back_populates="status_history")
    changed_by = relationship("User", foreign_keys=[changed_by_id])


def _finding_id_of(endpoint: FindingHost) -> Optional[int]:
    """The finding an endpoint row belongs to."""
    if endpoint.finding_id is not None:
        return endpoint.finding_id
    parent = endpoint.finding
    return parent.id if parent is not None else None


#: ``Session.info`` key: the findings this transaction has written so far.
_WRITTEN_FINDINGS = "bluestick_written_findings"


@event.listens_for(Session, "after_flush")
def _note_written_findings(session: Session, flush_context) -> None:
    """Which findings this flush wrote: a change of the finding's row, or an
    endpoint (``finding_hosts``) added, changed or removed.  (In
    ``after_flush`` the session still shows the flush's own new / dirty /
    deleted lists and attribute history.)  A finding the flush inserted
    starts at 1 and one it deleted has no number left."""
    written = set()
    for obj in session.dirty:
        if isinstance(obj, Finding) and session.is_modified(obj, include_collections=False):
            written.add(obj.id)
        elif isinstance(obj, FindingHost) and session.is_modified(obj, include_collections=False):
            written.add(_finding_id_of(obj))
    for obj in list(session.new) + list(session.deleted):
        if isinstance(obj, FindingHost):
            written.add(_finding_id_of(obj))
    written.discard(None)
    written -= {
        obj.id for obj in list(session.new) + list(session.deleted) if isinstance(obj, Finding)
    }
    if written:
        session.info.setdefault(_WRITTEN_FINDINGS, set()).update(written)


@event.listens_for(Session, "before_commit")
def _count_finding_revisions(session: Session) -> None:
    """THE writer of ``findings.revision``: one more, at commit, for every
    finding the transaction wrote — every route, bulk route, accepted
    proposal and agent write goes through here, as none of them writes the
    number itself.

    ``revision + 1`` is computed by the database as the transaction's LAST
    statement, so two transactions writing one finding number their commits
    in the order they commit (the second waits for the first's row lock and
    re-reads it), and the lock is taken after every other lock the
    transaction holds — it adds no step to anyone's lock order.  A savepoint's
    release is not the commit: the count waits for the outer one.

    What changes an answer WITHOUT a write of either table — a host or name
    deleted under an endpoint (the database cascades), a later retest result
    on the host — is not counted: the next read shows it, and an answer is
    put on a page only over a LOWER revision.
    """
    if session.in_nested_transaction():
        return
    session.flush()
    written = session.info.pop(_WRITTEN_FINDINGS, None)
    if not written:
        return
    table = Finding.__table__
    session.connection().execute(
        update(table).where(table.c.id.in_(sorted(written))).values(revision=table.c.revision + 1)
    )
    for finding_id in written:
        held = session.identity_map.get(identity_key(Finding, finding_id))
        if held is not None:
            session.expire(held, ["revision"])


@event.listens_for(Session, "after_rollback")
def _forget_written_findings(session: Session) -> None:
    session.info.pop(_WRITTEN_FINDINGS, None)
