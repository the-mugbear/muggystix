"""Client reports — what a report says, the delta, and issuing (v2.380.0).

**What goes in.**  Findings whose status is ``confirmed``, ``accepted_risk``
(labelled "Risk accepted") or ``remediated`` (labelled "Remediated during the
assessment").  ``open`` / ``retest`` findings are still under investigation:
they stay out, and the report page says how many.  Within a finding, endpoints
judged ``false_positive`` are dropped (a finding whose every endpoint is a
false positive drops out entirely); remediated endpoints stay, labelled.
Informational findings are in the data with their severity; the template puts
them in an appendix.

**The dataset** is the JSON the template is rendered from: engagement details,
scope, counts, and one entry per finding with its report text, affected
endpoints and the images marked for the report.  A DRAFT is built from the
live findings every time; ISSUING freezes it into ``Report.snapshot`` together
with ``reported`` — the state the report stood for: every included finding
with its reference and endpoints.  ``reported`` is cumulative: it also carries
every finding a previous issue reported that is no longer included, flagged
``withdrawn`` with its reference, so a number is never handed to another
finding (v2.390.4; before, a withdrawn F-02 left ``reported`` and the next
addendum gave F-02 to a new finding).

**The delta** (an addendum) compares the live state with the baseline's
``reported`` by finding AND endpoint — never by date: a promotion that joins an
existing finding adds endpoints to an old finding, and that is new.  It lists
new findings, new endpoints on reported findings, and what was withdrawn since
(findings deleted, set false positive or back under investigation; endpoints
detached or set false positive).  It never reports remediation: a project is
one assessment window, not response tracking.

**References.**  A reference is assigned ONCE per project and kept by every
later document — full report, revision or addendum — so "F-03" means the same
finding everywhere (review 2026-09-23 C3).  The ledger is the merge of every
issued (and superseded) report's ``reported``; a finding it knows keeps its
reference, a new one continues after the highest ever issued.  The first
report therefore numbers F-01, F-02… in report order; later ones may list
them out of sequence, which is the price of a stable reference.  An addendum
may only be ISSUED against the current issue: a draft compared with an older
or superseded report would re-list findings the client already has.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session, load_only, noload, selectinload

from app.core.config import settings

from app.db.models import Annotation, Host, NoteAttachment, Port, Scope, ScopeDomain, Subnet
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingStatus, FindingVulnerability,
)
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.db.models_reports import (
    RenderStatus, Report, ReportImage, ReportKind, ReportProfile, ReportStatus,
)
from app.db.models_vulnerability import Vulnerability
from app.services.report_text import REPORT_TEXT_FIELDS, internal_references
from app.services import (
    proposal_service, quarto_render, report_images, report_scope, report_template_service,
)

SCHEMA_VERSION = 1

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SEVERITY_LABEL = {
    "critical": "Critical", "high": "High", "medium": "Medium", "low": "Low",
    "info": "Informational",
}
INCLUDED_STATUSES = (
    FindingStatus.CONFIRMED.value,
    FindingStatus.ACCEPTED_RISK.value,
    FindingStatus.REMEDIATED.value,
)
UNDER_INVESTIGATION = (FindingStatus.OPEN.value, FindingStatus.RETEST.value)
STATUS_NOTE = {
    FindingStatus.ACCEPTED_RISK.value: "Risk accepted",
    FindingStatus.REMEDIATED.value: "Remediated during the assessment",
}
STATUS_WORDS = {
    FindingStatus.OPEN.value: "back under investigation",
    FindingStatus.RETEST.value: "back under investigation",
    FindingStatus.FALSE_POSITIVE.value: "judged a false positive",
}
# The report text a finding should have before a report goes out.
REQUIRED_TEXT = ("description", "impact", "recommendation")


def missing_required_text(values: Any) -> List[str]:
    """The ``REQUIRED_TEXT`` sections that are empty on a finding (a dataset
    item, a row or a model): absent or whitespace only.  ONE definition — the
    report page's "Missing report text" and Operations' "Findings that need
    me" both read it."""
    get = values.get if hasattr(values, "get") else (lambda k: getattr(values, k, None))
    return [k for k in REQUIRED_TEXT if not (get(k) or "").strip()]


def internal_reference_warnings(items: List[dict]) -> List[dict]:
    """The findings whose written text names a BlueStick record, with the
    section and the phrases (``report_text.internal_references``, the rule a
    proposal is refused by): text a person wrote, or accepted before the
    refusal existed."""
    out = []
    for item in items:
        fields = [
            {"field": f, "phrases": phrases}
            for f in REPORT_TEXT_FIELDS
            if (phrases := internal_references(item.get(f)))
        ]
        if fields:
            out.append({"id": item["id"], "ref": item["ref"], "title": item["title"], "fields": fields})
    return out


def reportable_finding_condition():
    """SQL: this ``Finding`` would be in the client report — the rule
    ``ClientReportService._included`` applies in Python: an included status, and not every
    endpoint judged a false positive (a finding with no endpoint stays)."""
    endpoint = exists().where(FindingHost.finding_id == Finding.id).correlate(Finding)
    live_endpoint = (
        exists()
        .where(
            FindingHost.finding_id == Finding.id,
            FindingHost.host_status.is_distinct_from(FindingHostStatus.FALSE_POSITIVE.value),
        )
        .correlate(Finding)
    )
    return and_(Finding.status.in_(INCLUDED_STATUSES), or_(~endpoint, live_endpoint))


def blank_required_text() -> Dict[str, Any]:
    """SQL twin of :func:`missing_required_text`, per section: for each
    ``REQUIRED_TEXT`` field, the condition "it is NULL or whitespace only"
    (``str.strip()``'s whitespace)."""
    whitespace = " \t\n\r\x0b\x0c"
    return {
        k: func.btrim(func.coalesce(getattr(Finding, k), ""), whitespace) == ""
        for k in REQUIRED_TEXT
    }


def missing_required_text_condition():
    """SQL: some required report section of this ``Finding`` is empty."""
    return or_(*blank_required_text().values())


SETTINGS_KEYS = (
    "client_name", "classification", "engagement_type", "testers", "distribution",
    "system_description", "applications", "thick_clients", "other_targets",
)
# v2.382.0 — report details the template prints as a highlighted TODO when
# empty; the report page lists the same ones before issuing.  (The three
# optional target lists are "if applicable" and never a TODO.)
REQUIRED_DETAILS = (
    ("executive_summary", "executive summary"),
    ("client_name", "client"),
    ("classification", "classification"),
    ("engagement_type", "engagement type"),
    ("system_description", "system description"),
    ("testers", "assessment team"),
    ("distribution", "distribution list"),
    ("project_dates", "project dates"),
)
# Project roles that make someone part of the assessment team by default, and
# the role line they start with (editable per report).
TEAM_ROLES = {"admin": "Engagement lead", "analyst": "Tester"}
# Formats every renderer can place (Word and HTML alike).
REPORT_IMAGE_TYPES = report_images.REPORT_IMAGE_TYPES

# How a finding was confirmed (review 2026-10-01 B8): its linked evidence
# records whose outcome is ``finding``.  There is no per-record "in report"
# mark, so the outcome is the rule; the caps keep one noisy test from filling
# the report (and the snapshot) with tool output.
CONFIRMATION_OUTCOME = "finding"
CONFIRMATIONS_PER_FINDING = 10
CONFIRMATION_COMMAND_CHARS = 600
CONFIRMATION_SUMMARY_CHARS = 600
CONFIRMATION_OUTPUT_CHARS = 1500
CONFIRMATION_OUTPUT_LINES = 30
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# IN-list size for the batched evidence lookups.
_ID_CHUNK = 5000

_REF = re.compile(r"^F-(\d+)$")


class ReportStateError(Exception):
    """The report is not in a state that allows the action (→ 409)."""


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _date(value) -> Optional[str]:
    return value.date().isoformat() if isinstance(value, datetime) else None


def _sort_key(f: Finding):
    sev = SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else len(SEVERITY_ORDER)
    return (sev, -(f.cvss_score if f.cvss_score is not None else -1.0), (f.title or "").lower(), f.id)


def endpoint_key(fh: FindingHost) -> str:
    return f"{fh.host_id}:{fh.name_id if fh.name_id is not None else ''}"


def told_by_report(db: Session, report: Report) -> set[tuple[int, str]]:
    """What an issued report told the client about, from its frozen snapshot:
    ``(finding id, endpoint key)`` pairs, the key as ``endpoint_key`` builds it.

    A full report: every finding and system it reported.  An addendum: only
    what it ADDED to its baseline — new findings and the new systems of
    earlier ones — since the rest was told by the baseline.  (With the
    baseline gone, everything the addendum's snapshot holds.)  Only the
    ``reported`` map is read, never the dataset.
    """
    def reported(report_id: int) -> dict:
        row = db.query(Report.snapshot["reported"]).filter(Report.id == report_id).first()
        entries = row[0] if row is not None and isinstance(row[0], dict) else {}
        return {fid: entry for fid, entry in entries.items()
                if isinstance(entry, dict) and not entry.get("withdrawn")}

    def pairs(entries: dict) -> set[tuple[int, str]]:
        return {(int(fid), key) for fid, entry in entries.items() if str(fid).isdigit()
                for key in (entry.get("endpoints") or {})}

    told = pairs(reported(report.id))
    if report.kind == ReportKind.ADDENDUM and report.baseline_report_id is not None:
        told -= pairs(reported(report.baseline_report_id))
    return told


def _ref_number(ref: Optional[str]) -> int:
    m = _REF.match(ref or "")
    return int(m.group(1)) if m else 0


def stored_file_path(storage_path: str) -> Path:
    """A rendered report file's path on disk, confined to ``REPORT_FILES_DIR``.

    Shared by the report page's download and the agent's (v2.428.0), so the
    two cannot disagree on where a file is or on what escapes the root.
    Raises ``ValueError`` for a stored path outside the root and
    ``FileNotFoundError`` when the file is gone from storage.
    """
    root = Path(settings.REPORT_FILES_DIR).resolve()
    try:
        target = (root / storage_path).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        raise ValueError("Report file path invalid")
    if not target.is_file():
        raise FileNotFoundError("The file is missing from report storage.")
    return target


def report_image_dir(project_id: int, report_id: int) -> Path:
    """Where an issued report keeps its own copies of its evidence images:
    beside its rendered files, under ``REPORT_FILES_DIR`` (so the uploads
    backup takes them with the report)."""
    return Path(settings.REPORT_FILES_DIR) / str(int(project_id)) / str(int(report_id)) / "evidence"


#: The folders an issue writes beside a report's rendered files: its copies
#: of its evidence images, of its template, and of its scope file.
_EVIDENCE_DIR, _TEMPLATE_DIR, _SCOPE_DIR = "evidence", "template", "scope"


def _discard_report_folder(project_id: int, report_id: int, name: str) -> None:
    """Remove ``<REPORT_FILES_DIR>/<project>/<report>/<name>`` and nothing
    else: the path is built from the two numbers, resolved, and must be
    exactly that — a symlink that leads anywhere else is left alone.  The
    report's own folder goes too when that leaves it empty (a draft has no
    rendered files)."""
    root = Path(settings.REPORT_FILES_DIR).resolve()
    try:
        expected = (str(int(project_id)), str(int(report_id)), name)
        folder = root.joinpath(*expected)
        if folder.is_symlink() or folder.resolve().relative_to(root).parts != expected:
            return
    except (TypeError, ValueError, OSError):
        return
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.rmdir()          # only when empty
    except OSError:
        pass


def discard_report_images(project_id: int, report_id: int) -> None:
    """Remove the copies ``freeze_report_images`` made — when an issue fails
    while it still holds the report's lock, at the start of the next attempt,
    and when the DRAFT is discarded.  NEVER after the issuing transaction has
    rolled back: the lock is gone and the folder may hold a later request's
    committed copies (external review 2026-10-02 H1).  (Review 2026-10-01 M2: a crash between the copy and the commit left
    the folder, and nothing ever removed it.)  Never called for a report
    that was issued: the callers hold a draft.

    Only ever this report's ``evidence`` folder (``_discard_report_folder``)."""
    _discard_report_folder(project_id, report_id, _EVIDENCE_DIR)


def discard_report_copies(project_id: int, report_id: int) -> None:
    """Everything an issue attempt copied for a DRAFT — its images
    (``discard_report_images``), its template and its scope file — under the
    same rule: while the issue still holds the report's lock, at the start of
    the next attempt, and when the draft is discarded; never after the
    issuing transaction rolled back, and never for an issued report."""
    for name in (_EVIDENCE_DIR, _TEMPLATE_DIR, _SCOPE_DIR):
        _discard_report_folder(project_id, report_id, name)


def _report_dir(project_id: int, report_id: int, name: str) -> Path:
    return Path(settings.REPORT_FILES_DIR) / str(int(project_id)) / str(int(report_id)) / name


def freeze_report_template(report: Report) -> Optional[dict]:
    """Copy the report's template, as the renderer would use it now (its
    base's files, its own, every uploaded file in place), into the report's
    own storage → ``{"name", "sha256"}`` for the snapshot.  Called while
    issuing, under the report's lock: from then on the report's files are
    rendered from this copy, so a logo uploaded — or a template folder
    changed by an upgrade — between the issue and its render can neither
    change them nor block them.

    The folder is emptied first: this is a draft being issued, so whatever
    is there is left over from an attempt that did not commit.  None — and no
    copy — when the template cannot be loaded (the render then says so).
    Raises ``ReportStateError`` when the copy cannot be written."""
    _discard_report_folder(report.project_id, report.id, _TEMPLATE_DIR)
    try:
        template = report_template_service.get_template(report.template)
    except report_template_service.TemplateError:
        return None
    # Under the template's name, so a render's messages name the template.
    target = _report_dir(report.project_id, report.id, _TEMPLATE_DIR) / template.name
    try:
        digest = report_template_service.freeze(template, target)
    except (OSError, ValueError) as exc:
        _discard_report_folder(report.project_id, report.id, _TEMPLATE_DIR)
        raise ReportStateError(
            f"The '{template.name}' template could not be copied into report storage "
            f"({getattr(exc, 'strerror', None) or exc})."
        ) from exc
    return {"name": template.name, "sha256": digest}


def frozen_template_dir(report: Report) -> Optional[Path]:
    """The folder of an issued report's own template copy, checked against
    the digest recorded at issue.  None for a report issued before the copy
    existed (its snapshot does not name one).  Raises ``ValueError`` when the
    copy is gone or is no longer what was issued."""
    frozen = (report.snapshot or {}).get("template")
    if not isinstance(frozen, dict):
        return None
    name = str(frozen.get("name") or "")
    root = Path(settings.REPORT_FILES_DIR).resolve()
    folder = _report_dir(report.project_id, report.id, _TEMPLATE_DIR) / name
    try:
        folder.resolve().relative_to(root)
        intact = bool(name) and folder.is_dir() and not folder.is_symlink() and (
            report_template_service.folder_digest(folder) == frozen.get("sha256"))
    except (ValueError, OSError):
        intact = False
    if not intact:
        raise ValueError(
            "This report's own copy of its template is missing from report storage, or is no longer "
            "the one it was issued with. Restore it (uploads/client_reports, from a backup), or "
            "revise the report."
        )
    return folder


def freeze_report_scope(report: Report, dataset: dict) -> None:
    """Keep the bytes of the scope file an issued report names (its name and
    SHA-256 are printed in it), written under the report's lock like its
    images.  Nothing is kept for a report that lists its scope itself."""
    _discard_report_folder(report.project_id, report.id, _SCOPE_DIR)
    scope = dataset.get("scope") or {}
    if not scope.get("file"):
        return
    target = _report_dir(report.project_id, report.id, _SCOPE_DIR)
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "scope.csv").write_bytes(report_scope.scope_csv(scope))
    except OSError as exc:
        _discard_report_folder(report.project_id, report.id, _SCOPE_DIR)
        raise ReportStateError(
            f"The report's scope file could not be written to report storage ({exc.strerror or 'error'})."
        ) from exc


def issued_scope_file(report: Report, scope: dict) -> bytes:
    """The scope file of an ISSUED report: the bytes kept when it was issued
    — or, for a report issued before they were kept, built again from its
    frozen scope — and in either case only if they are the file whose SHA-256
    the report prints.  Raises ``ValueError`` when they are not."""
    stored = _report_dir(report.project_id, report.id, _SCOPE_DIR) / "scope.csv"
    try:
        data = stored.read_bytes() if stored.is_file() and not stored.is_symlink() else None
    except OSError:
        data = None
    if data is None:
        data = report_scope.scope_csv(scope)
    printed = (scope.get("file") or {}).get("sha256")
    if printed and hashlib.sha256(data).hexdigest() != printed:
        raise ValueError(
            f"report {report.id}: its scope file no longer has the SHA-256 the report prints ({printed})"
        )
    return data


def discard_project_report_files(project_id: int) -> None:
    """Remove a DELETED project's report storage: every issued report's
    rendered files and image copies.  Confined to ``REPORT_FILES_DIR``."""
    root = Path(settings.REPORT_FILES_DIR).resolve()
    try:
        target = (root / str(int(project_id))).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return
    if target != root:
        shutil.rmtree(target, ignore_errors=True)


def freeze_report_images(db: Session, report: Report, dataset: dict) -> int:
    """Copy the bytes of every image the report PRINTS into the report's own
    storage and record each (``report_images``).  Called while issuing, in
    the issue's transaction: from then on the report renders from these
    copies, whatever happens to the attachments (before this, an image
    deleted between the issue and a successful render failed that render for
    good).  Returns the number copied.

    Which images the report prints is the dataset's ``printed`` mark
    (``_mark_printed``, review 2026-10-01 S2): an image this template prints
    nowhere is not copied, and its file is not required — a brief that shows
    no evidence must not be refused over a screenshot it would never show.
    An image without the mark (what is printed could not be measured) is
    copied, as every image was before.

    Raises ``ReportStateError`` — nothing copied, nothing issued — when a
    PRINTED image's file is not in storage: a report must not be signed off
    with a figure it cannot print.

    The report's folder is emptied first (M2): this is a draft being issued,
    so whatever is there is left over from an attempt that did not commit —
    never this report's copies."""
    discard_report_images(report.project_id, report.id)
    wanted: Dict[int, Tuple[dict, dict]] = {}
    for finding in dataset.get("findings") or []:
        for img in finding.get("images") or []:
            if img.get("printed") is not False:
                wanted.setdefault(img["attachment_id"], (img, finding))
    if not wanted:
        return 0
    attachments = {
        att.id: att for att in
        db.query(NoteAttachment).filter(
            NoteAttachment.id.in_(list(wanted)), NoteAttachment.project_id == report.project_id,
        )
    }
    base = (Path(settings.UPLOAD_DIR) / "note_attachments").resolve()
    sources: Dict[int, Path] = {}
    missing = []
    for att_id, (img, finding) in wanted.items():
        att = attachments.get(att_id)
        source = None
        if att is not None:
            try:
                candidate = (base / att.storage_path).resolve()
                candidate.relative_to(base)
                source = candidate if candidate.is_file() else None
            except (ValueError, OSError):
                source = None
        if source is None:
            missing.append(f"{finding.get('ref')} \"{str(img.get('caption') or '')[:60]}\" (image {att_id})")
        else:
            sources[att_id] = source
    if missing:
        raise ReportStateError(
            "The file of an evidence image is missing from storage, so the report cannot print it: "
            f"{'; '.join(missing[:10])}{' …' if len(missing) > 10 else ''}. Attach the image again "
            "(or un-tick \"In report\" and remove it from the text), then issue."
        )
    root = Path(settings.REPORT_FILES_DIR)
    target_dir = report_image_dir(report.project_id, report.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        for att_id, source in sources.items():
            img, _finding = wanted[att_id]
            target = target_dir / Path(img["file"]).name
            data = source.read_bytes()
            target.write_bytes(data)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            db.add(ReportImage(
                report_id=report.id, attachment_id=att_id,
                content_type=attachments[att_id].content_type, size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_path=str(target.relative_to(root)),
            ))
    except OSError as exc:
        discard_report_images(report.project_id, report.id)
        raise ReportStateError(
            f"The report's evidence images could not be copied into report storage ({exc.strerror or 'error'})."
        ) from exc
    return len(sources)


def _severity_value(value) -> Optional[str]:
    """A severity as compared between a baseline and now: the enum's value,
    trimmed, lower-case — "High", "high " and Severity.HIGH are one rating.
    None when nothing is stored (a baseline frozen before severity was)."""
    text = str(getattr(value, "value", value) or "").strip().lower()
    return text or None


def report_excerpt(text: Optional[str], *, max_chars: int, max_lines: Optional[int] = None) -> Tuple[Optional[str], bool]:
    """``(text, cut)`` — tool text made fit to print: terminal colour codes
    and control characters removed (one NUL or ESC makes a Word file
    unreadable), line endings normalised, then cut to ``max_lines`` and
    ``max_chars``.  None when nothing printable is left."""
    if not text:
        return None, False
    clean = _ANSI.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    clean = _CONTROL.sub("", clean)
    clean = "\n".join(line.rstrip() for line in clean.split("\n")).strip("\n")
    cut = False
    if max_lines is not None:
        lines = clean.split("\n")
        if len(lines) > max_lines:
            clean, cut = "\n".join(lines[:max_lines]), True
    if len(clean) > max_chars:
        clean, cut = clean[:max_chars].rstrip(), True
    return (clean or None), cut


def finding_image_attachments(
    db: Session, findings: Iterable[Tuple[int, Optional[int]]], *, marked_only: bool,
) -> Dict[int, List[Tuple[NoteAttachment, Optional[str]]]]:
    """Every finding's attached files, in attachment order: finding id →
    ``[(attachment, the note's actor_type), …]``.

    ``findings`` is ``(finding id, evidence_annotation_id)`` pairs.  A file
    belongs to a finding when it hangs on one of the finding's own comments
    (``annotations.finding_id``) or on its source-note thread (the
    ``evidence_annotation_id`` root and its replies).  ``marked_only`` keeps
    only files marked for the report (``include_in_report``): the client
    report passes True; the drafter's captions and ``report_images.attachments_of``
    pass False (they need every image of the finding, ticked or not).

    ONE statement per ``_ID_CHUNK`` findings (review 2026-10-01 R16): a
    caller once ran two queries per finding."""
    pairs = [(fid, root) for fid, root in findings if fid is not None]
    out: Dict[int, List[Tuple[NoteAttachment, Optional[str]]]] = defaultdict(list)
    for start in range(0, len(pairs), _ID_CHUNK):
        chunk = pairs[start:start + _ID_CHUNK]
        finding_ids = {fid for fid, _ in chunk}
        roots: Dict[int, List[int]] = defaultdict(list)
        for fid, root in chunk:
            if root:
                roots[root].append(fid)
        conds = [Annotation.finding_id.in_(finding_ids)]
        if roots:
            conds += [Annotation.id.in_(list(roots)), Annotation.thread_root_id.in_(list(roots))]
        query = (
            db.query(
                NoteAttachment, Annotation.id, Annotation.finding_id, Annotation.thread_root_id,
                Annotation.actor_type,
            )
            .join(Annotation, Annotation.id == NoteAttachment.annotation_id)
            .filter(or_(*conds))
        )
        if marked_only:
            query = query.filter(NoteAttachment.include_in_report.is_(True))
        for att, ann_id, ann_finding, ann_root, actor_type in query.order_by(NoteAttachment.id).all():
            targets = set()
            if ann_finding in finding_ids:
                targets.add(ann_finding)
            targets.update(roots.get(ann_id, ()))
            targets.update(roots.get(ann_root, ()))
            for fid in targets:
                out[fid].append((att, actor_type))
    return dict(out)


class ClientReportService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Profile → a new draft's settings
    # ------------------------------------------------------------------
    def get_profile(self, project_id: int) -> Optional[ReportProfile]:
        return self.db.query(ReportProfile).filter(ReportProfile.project_id == project_id).first()

    def project_team(self, project_id: int) -> List[dict]:
        """The project's analysts and admins as an assessment team — what a
        report lists when nobody has written a team yet.  Leads first."""
        from app.db.models_auth import User
        from app.db.models_project import ProjectMembership

        rows = (
            self.db.query(User.id, User.full_name, User.username, User.email, ProjectMembership.role)
            .join(ProjectMembership, ProjectMembership.user_id == User.id)
            .filter(ProjectMembership.project_id == project_id, User.is_active.is_(True))
            .all()
        )
        team = []
        for uid, full_name, username, email, role in rows:
            role = getattr(role, "value", role)
            if role not in TEAM_ROLES:
                continue
            team.append({
                "user_id": uid, "name": full_name or username, "role": TEAM_ROLES[role], "email": email or None,
                "_order": 0 if role == "admin" else 1,
            })
        team.sort(key=lambda t: (t.pop("_order"), (t["name"] or "").lower()))
        return team

    def _with_full_names(self, entries: Optional[Iterable[dict]]) -> Tuple[List[dict], List[str]]:
        """Team entries with each account-linked person under their FULL NAME,
        never their username.

        An entry keeps the name written when it was added, so one added while
        the account had no full name (the picker then falls back to the
        username), or before it was set, would print the username for good.
        Here an entry whose name is empty or is exactly its account's username
        takes the account's current full name; a name someone typed stays as
        written.  Returns the entries and the usernames still without a full
        name (listed as a missing detail, so a reviewer can fix the account)."""
        from app.db.models_auth import User

        entries = [dict(e) for e in (entries or []) if isinstance(e, dict)]
        ids = {e.get("user_id") for e in entries if isinstance(e.get("user_id"), int)}
        if not ids:
            return entries, []
        users = {
            uid: (username, (full_name or "").strip())
            for uid, username, full_name in
            self.db.query(User.id, User.username, User.full_name).filter(User.id.in_(ids))
        }
        unnamed: List[str] = []
        for e in entries:
            account = users.get(e.get("user_id"))
            if account is None:
                continue
            username, full_name = account
            stored = (e.get("name") or "").strip()
            if stored and stored != username:
                continue  # written by someone — theirs
            if full_name:
                e["name"] = full_name
            else:
                e["name"] = stored or username
                if username not in unnamed:
                    unnamed.append(username)
        return entries, unnamed

    def settings_from_profile(self, project_id: int) -> Dict[str, Any]:
        """A new draft's engagement details: the profile's, with the project's
        analysts and admins as the team when the profile names nobody."""
        profile = self.get_profile(project_id)
        if profile is None:
            settings: Dict[str, Any] = {k: ([] if k in ("testers", "distribution") else None) for k in SETTINGS_KEYS}
        else:
            settings = {k: getattr(profile, k, None) for k in SETTINGS_KEYS}
            settings["testers"] = list(profile.testers or [])
            settings["distribution"] = list(profile.distribution or [])
        if not settings["testers"]:
            settings["testers"] = self.project_team(project_id)
        return settings

    @staticmethod
    def settings_from_issued(report: Report) -> Dict[str, Any]:
        """The engagement details an issued report went out with — what a
        revision of it, or an addendum to it, starts from (v2.404.0; before,
        an addendum started from the profile and a revision of an addendum
        inherited that addendum's empty details).

        ``Report.settings`` cannot change once issued (PATCH refuses), so it
        is the frozen form as written; the snapshot's ``engagement`` fills any
        key it lacks (a report issued before a key existed).  Copies — the new
        draft never shares a list with the issued report."""
        own = report.settings or {}
        frozen = (((report.snapshot or {}).get("dataset") or {}).get("engagement")) or {}
        settings: Dict[str, Any] = {}
        for key in SETTINGS_KEYS:
            value = own.get(key)
            if value in (None, "", []):
                value = frozen.get(key)
            if key in ("testers", "distribution"):
                value = [dict(e) for e in (value or []) if isinstance(e, dict)]
            settings[key] = value
        return settings

    def settings_for_addendum(self, baseline: Report) -> Dict[str, Any]:
        """An addendum's details: its baseline's, as issued — the same client,
        classification, team and distribution the client already has.  A
        detail the baseline left empty takes the project's report default."""
        settings = self.settings_from_issued(baseline)
        defaults = self.settings_from_profile(baseline.project_id)
        for key in SETTINGS_KEYS:
            if settings.get(key) in (None, "", []):
                settings[key] = defaults.get(key)
        return settings

    # ------------------------------------------------------------------
    # Live state
    # ------------------------------------------------------------------
    def _included(self, project_id: int) -> List[Finding]:
        # Only the host columns an endpoint prints.  Host's relationships are
        # lazy="selectin", so loading the entity dragged in every port,
        # scanner row (with its plugin output), note and tag of every
        # affected host — on each draft view, save, preview and inside the
        # issue lock (review 2026-09-23 C7).
        findings = (
            self.db.query(Finding)
            .options(
                selectinload(Finding.hosts).selectinload(FindingHost.host).options(
                    load_only(Host.id, Host.ip_address, Host.hostname),
                    noload(Host.ports), noload(Host.vulnerabilities),
                    noload(Host.notes), noload(Host.tag_assignments),
                ),
                selectinload(Finding.hosts).selectinload(FindingHost.name),
                noload(Finding.vulnerabilities),
            )
            .filter(Finding.project_id == project_id, Finding.status.in_(INCLUDED_STATUSES))
            .all()
        )
        out = []
        for f in findings:
            live = [fh for fh in f.hosts if fh.host_status != FindingHostStatus.FALSE_POSITIVE.value]
            # Every endpoint judged a false positive: nothing is left to report.
            if f.hosts and not live:
                continue
            f._report_endpoints = live  # type: ignore[attr-defined]
            out.append(f)
        out.sort(key=_sort_key)
        return out

    def under_investigation_count(self, project_id: int) -> int:
        return (
            self.db.query(func.count(Finding.id))
            .filter(Finding.project_id == project_id, Finding.status.in_(UNDER_INVESTIGATION))
            .scalar()
        ) or 0

    def _ports(self, findings: Iterable[Finding]) -> Dict[int, str]:
        ids = {fh.port_id for f in findings for fh in f._report_endpoints if fh.port_id}
        if not ids:
            return {}
        rows = self.db.query(Port.id, Port.port_number, Port.protocol).filter(Port.id.in_(ids)).all()
        return {pid: f"{num}/{proto}" for pid, num, proto in rows}

    def _corroboration(self, finding_ids: List[int]) -> Dict[int, List[str]]:
        if not finding_ids:
            return {}
        rows = (
            self.db.query(FindingVulnerability.finding_id, Vulnerability.source)
            .join(Vulnerability, Vulnerability.id == FindingVulnerability.vuln_id)
            .filter(FindingVulnerability.finding_id.in_(finding_ids))
            .distinct()
            .all()
        )
        out: Dict[int, set] = defaultdict(set)
        for fid, source in rows:
            name = getattr(source, "value", source)
            out[fid].add({"openvas": "OpenVAS", "netexec": "NetExec", "cve_api": "CVE API"}.get(
                str(name), str(name).capitalize()))
        return {fid: sorted(names) for fid, names in out.items()}

    def _evidence(self, findings: List[Finding]) -> Tuple[Dict[int, dict], int, int]:
        """Images MARKED for the report, from the finding's source-note thread
        and its own comments.  Returns (by finding, count skipped for format,
        count attached to an agent-written note — v2.437.0, a warning before
        issuing, never a block).

        Per finding: ``images`` (every marked image, with its caption — the
        author's, else the file name — and the sections that place it),
        ``placed`` (section → the images its text references: the only ones
        the renderer shows there) and ``evidence`` (the images no section
        places: the trailing evidence block).  ``report_images`` decides."""
        by_finding: Dict[int, dict] = {}
        skipped = by_agent = 0
        counted = set()
        attached = finding_image_attachments(
            self.db, [(f.id, f.evidence_annotation_id) for f in findings], marked_only=True,
        )
        by_id = {f.id: f for f in findings}
        for fid, rows in attached.items():
            ticked = []
            for att, actor_type in rows:
                first = att.id not in counted
                counted.add(att.id)
                if not report_images.printable(att):
                    skipped += first
                    continue
                if actor_type == "agent":
                    by_agent += first
                ticked.append(att)
            images, placed, unplaced = report_images.report_placement(by_id[fid], ticked)
            by_finding[fid] = {"images": images, "placed": placed, "evidence": unplaced}
        return by_finding, skipped, by_agent

    @staticmethod
    def _template(template):
        """The report's template — by name, or one already loaded (a build
        loads it once and hands it to everything that reads it) — or None
        when there is no such template."""
        if template is not None and not isinstance(template, str):
            return template
        try:
            return report_template_service.get_template(template)
        except report_template_service.TemplateError:
            return None

    def _records_in_report(self, template) -> bool:
        """Whether the report's template prints how findings were confirmed:
        ``template.json`` → ``"evidence_records": true``.  Opt-in — a template
        that does not ask gets none in its data, so none is frozen at issue
        either."""
        template = self._template(template)
        return bool(template is not None and template.evidence_records)

    def _confirmations(self, findings: List[Finding]) -> Tuple[Dict[int, List[dict]], Dict[int, int], int]:
        """How each finding was confirmed (review 2026-10-01 B8): its linked
        evidence records with outcome ``finding`` — the tool, the command as
        run, when, who, and a trimmed excerpt of the output.  Returns (by
        finding, further records not shown per finding, count recorded by an
        agent — a warning before issuing, like agent images).

        Only records on a system the report lists for that finding: a host
        judged a false positive, or one the finding was never attached to, is
        not in the report, so neither is what was run against it.  A finding
        with no systems keeps all of its records.

        Everything here is tool or user text.  It travels in ``data.json``
        and is printed escaped or as a verbatim block the filter fills
        (``code()``) — never as ``.qmd`` source."""
        from app.db.models_agent import AgentSession
        from app.db.models_auth import User

        by_id = {f.id: f for f in findings}
        ids = list(by_id)
        # A finding with endpoints takes only records on a host the report
        # lists for it — an endpoint row that is not a false positive, which
        # is what `_report_endpoints` holds (`listed_findings`).  One with no
        # endpoint at all takes every record.
        loose = {f.id for f in findings if not f.hosts}
        on_a_listed_system = exists().where(
            FindingHost.finding_id == EvidenceRecord.finding_id,
            FindingHost.host_id == EvidenceRecord.host_id,
            FindingHost.host_status.is_distinct_from(FindingHostStatus.FALSE_POSITIVE.value),
        )
        # The database ranks and cuts (review 2026-10-02 R2): every matching
        # record used to be loaded — command, summary and output preview
        # included — and sorted here, to keep ten per finding.  The order is
        # the one that sort had: when it was run (else recorded), then id.
        when_run = func.coalesce(EvidenceRecord.executed_at, EvidenceRecord.created_at)
        rows = []
        for start in range(0, len(ids), _ID_CHUNK):
            chunk = ids[start:start + _ID_CHUNK]
            with_systems = [i for i in chunk if i not in loose]
            without = [i for i in chunk if i in loose]
            eligible = []
            if with_systems:
                eligible.append(and_(EvidenceRecord.finding_id.in_(with_systems), on_a_listed_system))
            if without:
                eligible.append(EvidenceRecord.finding_id.in_(without))
            ranked = (
                self.db.query(
                    EvidenceRecord.id.label("id"),
                    func.row_number().over(
                        partition_by=EvidenceRecord.finding_id,
                        order_by=(when_run.asc().nulls_first(), EvidenceRecord.id.asc()),
                    ).label("position"),
                    func.count().over(partition_by=EvidenceRecord.finding_id).label("eligible"),
                )
                .filter(EvidenceRecord.outcome == CONFIRMATION_OUTCOME, or_(*eligible))
                .subquery()
            )
            rows += (
                self.db.query(
                    EvidenceRecord.id, EvidenceRecord.finding_id, EvidenceRecord.host_id,
                    EvidenceRecord.tool, EvidenceRecord.command, EvidenceRecord.summary,
                    EvidenceRecord.raw_output_preview, EvidenceRecord.raw_output_bytes,
                    EvidenceRecord.executed_at, EvidenceRecord.created_at,
                    EvidenceRecord.agent_session_id, EvidenceRecord.recorded_by_user_id,
                    ranked.c.eligible,
                )
                .join(ranked, ranked.c.id == EvidenceRecord.id)
                .filter(ranked.c.position <= CONFIRMATIONS_PER_FINDING)
                .order_by(EvidenceRecord.finding_id, ranked.c.position)
                .all()
            )
        if not rows:
            return {}, {}, 0

        listed: Dict[int, Dict[int, str]] = {}
        for f in findings:
            listed[f.id] = {
                fh.host_id: (fh.host.ip_address if fh.host is not None else f"host {fh.host_id}")
                for fh in f._report_endpoints
            }
        # Addresses not already loaded with the endpoints: a finding without
        # systems, and an endpoint added since the findings were read.
        loose_hosts = {r.host_id for r in rows if r.host_id not in listed[r.finding_id]}
        addresses = dict(
            self.db.query(Host.id, Host.ip_address).filter(Host.id.in_(loose_hosts)).all()
        ) if loose_hosts else {}

        session_ids = {r.agent_session_id for r in rows if r.agent_session_id}
        operators = dict(
            self.db.query(AgentSession.id, AgentSession.started_by_id)
            .filter(AgentSession.id.in_(session_ids)).all()
        ) if session_ids else {}
        user_ids = {r.recorded_by_user_id for r in rows if r.recorded_by_user_id} | {
            uid for uid in operators.values() if uid
        }
        names = {
            uid: (full_name or "").strip() or username
            for uid, full_name, username in
            self.db.query(User.id, User.full_name, User.username).filter(User.id.in_(user_ids))
        } if user_ids else {}

        out: Dict[int, List[dict]] = defaultdict(list)
        omitted: Dict[int, int] = {}
        by_agent = 0
        for r in rows:
            finding = by_id[r.finding_id]
            host = listed[finding.id].get(r.host_id) or addresses.get(r.host_id) or f"host {r.host_id}"
            if r.eligible > CONFIRMATIONS_PER_FINDING:
                omitted[finding.id] = r.eligible - CONFIRMATIONS_PER_FINDING
            when = r.executed_at or r.created_at
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if r.agent_session_id:
                by_agent += 1
                # The client report names the OPERATOR, in full, and nothing
                # about the session (owner, 2026-10-02): the person whose
                # agent ran the test answers for it.  `by_agent` stays in the
                # data for the report page's own count.
                by = names.get(operators.get(r.agent_session_id))
            else:
                by = names.get(r.recorded_by_user_id)
            command, _ = report_excerpt(r.command, max_chars=CONFIRMATION_COMMAND_CHARS)
            summary, _ = report_excerpt(r.summary, max_chars=CONFIRMATION_SUMMARY_CHARS)
            output, cut = report_excerpt(
                r.raw_output_preview, max_chars=CONFIRMATION_OUTPUT_CHARS, max_lines=CONFIRMATION_OUTPUT_LINES,
            )
            if output and not cut and r.raw_output_bytes:
                # The stored preview is itself the start of a longer output.
                cut = len((r.raw_output_preview or "").encode("utf-8", errors="replace")) < r.raw_output_bytes
            out[finding.id].append({
                "id": r.id,
                "tool": report_excerpt(r.tool, max_chars=100)[0] or "",
                "host": host,
                "outcome": CONFIRMATION_OUTCOME,
                "summary": summary,
                "command": command,
                "output": output,
                "output_truncated": bool(output and cut),
                "executed_at": _iso(when),
                "date": when.date().isoformat() if when is not None else None,
                "by": by,
                "by_agent": bool(r.agent_session_id),
            })
        return dict(out), dict(omitted), by_agent

    def _pending_proposals(self, finding_ids: List[int]) -> Dict[int, int]:
        """Pending agent proposals per finding (v2.437.0): a finding with one
        is "needs review".  Issuing warns about them, never blocks.  The
        finding's own proposals AND promote / dismiss proposals on its scanner
        observations — the proposal service's one definition."""
        return proposal_service.pending_per_finding(self.db, finding_ids)

    def _endpoint(self, fh: FindingHost, ports: Dict[int, str]) -> dict:
        host = fh.host
        return {
            "address": host.ip_address if host else f"host {fh.host_id}",
            "hostname": (host.hostname if host else None) or None,
            "name": fh.name.fqdn if fh.name is not None else None,
            "port": ports.get(fh.port_id) if fh.port_id else None,
            "state": "Remediated" if fh.host_status == FindingHostStatus.REMEDIATED.value else None,
        }

    @staticmethod
    def _label(ep: dict) -> str:
        parts = [ep["address"]]
        if ep.get("name"):
            parts.append(ep["name"])
        elif ep.get("hostname"):
            parts.append(ep["hostname"])
        if ep.get("port"):
            parts.append(ep["port"])
        return " · ".join(parts)

    def _scope(self, project_id: int) -> dict:
        scope_ids = [sid for (sid,) in self.db.query(Scope.id).filter(Scope.project_id == project_id).all()]
        if not scope_ids:
            return {"subnets": [], "domains": []}
        subnets = (
            self.db.query(Subnet.cidr, Subnet.description, Subnet.site)
            .filter(Subnet.scope_id.in_(scope_ids)).order_by(Subnet.cidr).all()
        )
        domains = (
            self.db.query(ScopeDomain.domain, ScopeDomain.include_subdomains)
            .filter(ScopeDomain.scope_id.in_(scope_ids)).order_by(ScopeDomain.domain).all()
        )
        seen = set()
        subnet_rows = []
        for cidr, description, site in subnets:
            if cidr in seen:
                continue
            seen.add(cidr)
            subnet_rows.append({"cidr": cidr, "description": description or None, "site": site or None})
        return {
            "subnets": subnet_rows,
            "domains": [{"domain": d, "include_subdomains": bool(sub)} for d, sub in domains],
        }

    def _scope_block(self, report: Report, project: Optional[Project], number: Optional[int],
                     template=None) -> dict:
        """The scope as the report states it (v2.441.0): the lists, their
        totals and per-site summary, whether each list is printed (the
        template's cutoff), and — when it is not — the separate file's name
        and SHA-256 (``report_scope``).  Frozen with the rest of the dataset
        at issue, so an issued report's file never changes."""
        scope = self._scope(report.project_id)
        template = self._template(template if template is not None else report.template)
        cutoffs = {} if template is None else {
            "inline_max": template.scope_inline_max,
            "domains_inline_max": template.scope_domains_inline_max,
        }
        scope.update(report_scope.summarise(scope["subnets"], scope["domains"], **cutoffs))
        return report_scope.attach_file(
            scope, project_slug=project.slug if project else None, number=number, report_id=report.id,
        )

    # ------------------------------------------------------------------
    # Dataset
    # ------------------------------------------------------------------
    def build(
        self, report: Report, *, number: Optional[int] = None,
        issued_at: Optional[datetime] = None,
    ) -> Tuple[dict, dict, dict]:
        """``(dataset, reported, summary)`` from the live findings."""
        project = self.db.get(Project, report.project_id)
        findings = self._included(report.project_id)
        ports = self._ports(findings)
        corroboration = self._corroboration([f.id for f in findings])
        evidence, skipped_images, agent_images = self._evidence(findings)
        confirmations: Dict[int, List[dict]] = {}
        confirmations_omitted: Dict[int, int] = {}
        agent_records = 0
        # Loaded ONCE per build: the manifest, its base's and every asset's
        # presence are read from disk each time.
        template = self._template(report.template)
        if self._records_in_report(template):
            confirmations, confirmations_omitted, agent_records = self._confirmations(findings)

        endpoints: Dict[int, Dict[str, dict]] = {
            f.id: {endpoint_key(fh): self._endpoint(fh, ports) for fh in f._report_endpoints}
            for f in findings
        }

        baseline = None
        baseline_reported: Dict[str, dict] = {}
        if report.kind == ReportKind.ADDENDUM:
            baseline = report.baseline
            if baseline is None or baseline.status == ReportStatus.DRAFT or not baseline.snapshot:
                raise ReportStateError("An addendum needs an issued report to compare against.")
            # What the client HAS: the baseline's live entries (a finding it
            # already listed as withdrawn is not "reported").
            baseline_reported = {
                fid: entry for fid, entry in ((baseline.snapshot or {}).get("reported") or {}).items()
                if not entry.get("withdrawn")
            }

        # References: assigned once per project, kept by every document.
        ledger = self._ledger(report.project_id)
        next_n = max((_ref_number(v.get("ref")) for v in ledger.values()), default=0)
        refs: Dict[int, str] = {}
        for f in findings:
            prior = ledger.get(str(f.id))
            if prior and prior.get("ref"):
                refs[f.id] = prior["ref"]
            else:
                next_n += 1
                refs[f.id] = f"F-{next_n:02d}"

        live_reported = {
            str(f.id): {
                "ref": refs[f.id], "title": f.title, "severity": f.severity, "status": f.status,
                "endpoints": {k: self._label(ep) for k, ep in endpoints[f.id].items()},
            }
            for f in findings
        }
        # Cumulative: every earlier reference that is not live now stays,
        # flagged, so its number is never reused.
        reported = dict(live_reported)
        for fid, entry in ledger.items():
            if fid not in reported:
                reported[fid] = {**entry, "withdrawn": True}

        # Which findings the document shows.
        delta = None
        # (finding, change, new endpoints, the severity the baseline gave it
        # when that differs from now).
        shown: List[Tuple[Finding, Optional[str], List[dict], Optional[str]]] = []
        if report.kind == ReportKind.ADDENDUM:
            withdrawn = self._withdrawn(report.project_id, baseline_reported, live_reported)
            for f in findings:
                prior = baseline_reported.get(str(f.id))
                if prior is None:
                    shown.append((f, "new", [], None))
                    continue
                # Re-rated since the baseline (owner's decision 2026-10-01,
                # review B17): the client has this finding at another
                # severity, and "Nothing has changed" was wrong for a Medium
                # raised to Critical.  Severity ONLY — a title is wording and
                # a status is remediation tracking, which a report never
                # does.  Against the baseline's frozen value, never a date; a
                # baseline that stored no severity counts as unchanged.
                was, now = _severity_value(prior.get("severity")), _severity_value(f.severity)
                previous = was if (was and now and was != now) else None
                new_keys = [k for k in endpoints[f.id] if k not in (prior.get("endpoints") or {})]
                if new_keys:
                    # Both at once: listed ONCE, with its new systems, and it
                    # carries the earlier severity too.
                    shown.append((f, "new_hosts", [endpoints[f.id][k] for k in new_keys], previous))
                elif previous:
                    shown.append((f, "severity_changed", [], previous))
            delta = {
                "new_findings": sum(1 for _, c, _, _ in shown if c == "new"),
                "findings_with_new_endpoints": sum(1 for _, c, _, _ in shown if c == "new_hosts"),
                # Every re-rated finding, including one also listed for its
                # new systems.
                "findings_with_changed_severity": sum(1 for _, _, _, p in shown if p),
                "withdrawn": withdrawn,
            }
        else:
            shown = [(f, None, [], None) for f in findings]

        items = []
        for index, (f, change, new_affected, previous_severity) in enumerate(shown):
            affected = list(endpoints[f.id].values())
            items.append({
                "_path": f"findings.{index}",
                "id": f.id,
                "ref": refs[f.id],
                "title": f.title,
                "severity": f.severity,
                "severity_label": SEVERITY_LABEL.get(f.severity, f.severity),
                "status": f.status,
                "status_note": STATUS_NOTE.get(f.status),
                "cvss_score": f.cvss_score,
                "cvss_vector": f.cvss_vector,
                "description": f.description,
                "impact": f.impact,
                "recommendation": f.recommendation,
                "references": f.references,
                "steps_to_reproduce": f.steps_to_reproduce,
                "affected": affected,
                "affected_count": len(affected),
                "new_affected": new_affected,
                "change": change,
                "previous_severity": previous_severity,
                "previous_severity_label": (
                    SEVERITY_LABEL.get(previous_severity, previous_severity) if previous_severity else None
                ),
                # Every image marked "In report"; the ones each written
                # section places; and the rest — the trailing evidence block.
                "images": evidence.get(f.id, {}).get("images", []),
                "placed": evidence.get(f.id, {}).get("placed", {}),
                "evidence": evidence.get(f.id, {}).get("evidence", []),
                # How it was confirmed (B8): each entry has its own data path
                # so the template's code() can name its command and output.
                "confirmations": [
                    {"_path": f"findings.{index}.confirmations.{n}", **entry}
                    for n, entry in enumerate(confirmations.get(f.id, []))
                ],
                "confirmations_omitted": confirmations_omitted.get(f.id, 0),
                "corroboration": corroboration.get(f.id, []),
            })

        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for item in items:
            if item["change"] in (None, "new") and item["severity"] in counts:
                counts[item["severity"]] += 1
        counts["total"] = sum(counts[s] for s in SEVERITY_ORDER)

        settings = {k: (report.settings or {}).get(k) for k in SETTINGS_KEYS}
        # The team is linked to accounts; the distribution list is names typed in.
        settings["testers"], no_full_name = self._with_full_names(settings.get("testers"))
        settings["distribution"] = list(settings.get("distribution") or [])

        revision_of = report.revision_of
        draft = issued_at is None and report.status == ReportStatus.DRAFT
        heading = settings.get("client_name") or (project.name if project else None) or ""
        dataset = {
            "schema": SCHEMA_VERSION,
            "report": {
                "id": report.id,
                "kind": report.kind,
                "title": report.title,
                # The line under the title: the client (else the project),
                # marked on a draft.
                "heading": f"{heading} — DRAFT" if draft else heading,
                "number": number if number is not None else report.number,
                "draft": draft,
                "date": _date(issued_at) if issued_at else datetime.now(timezone.utc).date().isoformat(),
                "issued_at": _iso(issued_at or report.issued_at),
                "template": report.template,
                # The title block's authors: the assessment team.
                "authors": [t.get("name") for t in settings["testers"] if t.get("name")],
                "baseline": {
                    "number": baseline.number, "title": baseline.title,
                    "date": _date(baseline.issued_at),
                } if baseline is not None else None,
                "revision_of": {
                    "number": revision_of.number, "title": revision_of.title,
                    "date": _date(revision_of.issued_at),
                } if revision_of is not None else None,
            },
            "project": {
                "name": project.name if project else None,
                "start_date": _date(project.start_date) if project else None,
                "end_date": _date(project.end_date) if project else None,
            },
            "engagement": settings,
            "executive_summary": report.executive_summary,
            "scope": self._scope_block(
                report, project, number if number is not None else report.number, template,
            ),
            "severity_order": list(SEVERITY_ORDER),
            "severity_labels": SEVERITY_LABEL,
            "counts": counts,
            "findings": items,
            "delta": delta,
        }

        # What THIS report prints of that (S2): marks each image, and takes
        # out the test results of findings the report does not show in detail.
        printing = self._mark_printed(template if template is not None else report.template, dataset)
        if printing is not None:
            agent_records = sum(1 for i in items for c in i["confirmations"] if c.get("by_agent"))

        pending = self._pending_proposals([item["id"] for item in items])
        summary = {
            "counts": counts,
            "findings_shown": len(items),
            "under_investigation": self.under_investigation_count(report.project_id),
            "missing_text": [
                {
                    "id": item["id"], "ref": item["ref"], "title": item["title"],
                    "missing": missing_required_text(item),
                }
                for item in items
                if missing_required_text(item)
            ],
            # Report details still empty — printed as a highlighted TODO.
            "missing_details": self._missing_details(dataset) + (
                [f"a full name on the account of {', '.join(no_full_name)} (the report shows the username)"]
                if no_full_name else []
            ),
            "images": sum(len(i["images"]) for i in items),
            # Of those: placed inside a written section by its author, and
            # left for the trailing evidence block.
            "images_placed": sum(1 for i in items for img in i["images"] if img["placed_in"]),
            "images_unplaced": sum(len(i["evidence"]) for i in items),
            # Where THIS report prints them (S2) — `images_placed` /
            # `images_unplaced` above say what the authors did, these what
            # the template does with it: inside a written section, in the
            # trailing evidence block, or nowhere (with the reasons, and what
            # the template declares it prints).  The three add up to
            # `images`.  None when it could not be measured (the template is
            # gone or cannot be filled) — and absent from a report issued
            # before this.
            "images_printed": printing["in_text"] if printing else None,
            "images_trailing": printing["trailing"] if printing else None,
            "images_not_printed": printing["not_printed"] if printing else None,
            "images_not_printed_reasons": printing["reasons"] if printing else None,
            "template_images": printing["template_images"] if printing else None,
            "images_skipped": skipped_images,
            # v2.437.0 — warnings before issuing, never blocks.
            "agent_images": agent_images,
            # B8 — test results PRINTED as "how it was confirmed", and how
            # many of them an agent recorded (a warning, never a block).
            # `evidence_records_not_printed`: results of findings this report
            # lists without their details (an addendum's known findings) —
            # they are not in the data either.
            "evidence_records": sum(len(i["confirmations"]) for i in items),
            "evidence_records_not_printed": printing["records_not_printed"] if printing else None,
            "agent_evidence_records": agent_records,
            "pending_proposals": [
                {"id": item["id"], "ref": item["ref"], "title": item["title"], "count": pending[item["id"]]}
                for item in items if pending.get(item["id"])
            ],
            # Written text that names a BlueStick record ("Finding #277"),
            # which the reader cannot look up — a warning, never a block.
            "internal_references": internal_reference_warnings(items),
            # v2.441.0 — over the template's cutoff the report names a scope
            # file instead of listing the scope: the operator must send it.
            "scope_external": {
                "networks": dataset["scope"]["totals"]["networks"],
                "domains": dataset["scope"]["totals"]["domains"],
                "inline_max": dataset["scope"]["inline_max"],
                "domains_inline_max": dataset["scope"]["domains_inline_max"],
                "file": dataset["scope"]["file"],
            } if dataset["scope"]["external"] else None,
            "delta": {
                "new_findings": delta["new_findings"],
                "findings_with_new_endpoints": delta["findings_with_new_endpoints"],
                "findings_with_changed_severity": delta["findings_with_changed_severity"],
                "withdrawn": len(delta["withdrawn"]),
            } if delta else None,
        }
        return dataset, reported, summary

    @staticmethod
    def _mark_printed(template, dataset: dict) -> Optional[dict]:
        """Say, in the dataset, what this report's template PRINTS of each
        finding's evidence (review 2026-10-01 S2) → the counts for the
        summary, or None when it cannot be measured (no such template, or it
        cannot be filled: everything then stays as built, and counts as
        printed for the issue's copies).

        A template prints what its ``.qmd`` asks for, and the shipped ones
        differ: the brief prints no image, the worklist only those placed in
        the recommendation of a finding still to fix, and an addendum lists a
        finding the client already has in one line.  So the template is
        filled once (Jinja only) and what it asked the helpers for is noted
        (``quarto_render.printed_parts``).  Then, per finding:

        * every image gets ``printed`` and ``printed_in`` (the sections that
          print it).  A placed image prints where a section that places it is
          printed with images; an unplaced one when the template prints the
          trailing block for that finding.  The issue copies — and requires
          the file of — printed images only.
        * ``confirmations`` are emptied when the template did not read them
          for that finding: the report's data holds the test results it
          prints, not those of a finding it shows as a table row.
        """
        template = ClientReportService._template(template)
        if template is None:
            return None
        try:
            parts = report_template_service.printed_parts(template, dataset)
        except quarto_render.RenderError:
            return None
        figures = set(parts["figures"])
        shown = set(parts["findings"])
        read = set(parts["confirmations"])
        out = {
            "in_text": 0, "trailing": 0, "not_printed": 0, "records_not_printed": 0,
            # Why an image prints nowhere: its finding is listed without its
            # details; it is placed in a section this template prints without
            # images (or not at all); it is placed nowhere and this template
            # has no trailing evidence block.
            "reasons": {"finding_not_detailed": 0, "section_not_printed": 0, "no_evidence_block": 0},
            "template_images": template.images,
        }
        for index, item in enumerate(dataset.get("findings") or []):
            fields = parts["fields"].get(index, {})
            for img in item.get("images") or []:
                placed_in = img.get("placed_in") or []
                printed_in = [field for field in placed_in if fields.get(field)]
                at_end = not placed_in and img.get("file") in figures
                img["printed_in"] = printed_in
                img["printed"] = bool(printed_in) or at_end
                if printed_in:
                    out["in_text"] += 1
                elif at_end:
                    out["trailing"] += 1
                else:
                    out["not_printed"] += 1
                    reason = ("finding_not_detailed" if index not in shown
                              else "section_not_printed" if placed_in else "no_evidence_block")
                    out["reasons"][reason] += 1
            if item.get("confirmations") and index not in read:
                out["records_not_printed"] += len(item["confirmations"])
                item["confirmations"] = []
                item["confirmations_omitted"] = 0
        return out

    @staticmethod
    def _missing_details(dataset: dict) -> List[str]:
        engagement = dataset["engagement"]
        project = dataset["project"]
        values = {
            **engagement,
            "executive_summary": dataset.get("executive_summary"),
            "project_dates": project.get("start_date") and project.get("end_date"),
        }
        missing = []
        for key, label in REQUIRED_DETAILS:
            value = values.get(key)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                missing.append(label)
        return missing

    def _withdrawn(self, project_id: int, baseline: Dict[str, dict], current: Dict[str, dict]) -> List[dict]:
        out = []
        gone_ids = [int(fid) for fid in baseline if fid not in current]
        statuses = dict(
            self.db.query(Finding.id, Finding.status)
            .filter(Finding.project_id == project_id, Finding.id.in_(gone_ids)).all()
        ) if gone_ids else {}
        for fid, prior in sorted(baseline.items(), key=lambda kv: _ref_number(kv[1].get("ref"))):
            base = {
                "ref": prior.get("ref"), "title": prior.get("title"),
                "severity_label": SEVERITY_LABEL.get(prior.get("severity"), prior.get("severity")),
            }
            if fid not in current:
                status = statuses.get(int(fid))
                if status is None:
                    reason = "The finding was withdrawn."
                elif status in STATUS_WORDS:
                    reason = f"The finding was {STATUS_WORDS[status]}."
                else:
                    reason = "The finding no longer applies to any reported endpoint."
                out.append({**base, "reason": reason, "endpoints": []})
                continue
            gone = [label for key, label in (prior.get("endpoints") or {}).items()
                    if key not in (current[fid].get("endpoints") or {})]
            if gone:
                out.append({**base, "reason": "No longer affects these endpoints.", "endpoints": gone})
        return out

    def _ledger(self, project_id: int) -> Dict[str, dict]:
        """Every finding any issued report has referenced: finding id → its
        latest ``reported`` entry.  Merged over every issued and superseded
        report in issue order, so a reference that fell out of a later
        ``reported`` (issued before v2.390.4, when withdrawn entries were
        dropped) is still known and never handed to another finding."""
        # Only the ``reported`` key of each snapshot (review 2026-10-01 R16):
        # a snapshot also holds the whole dataset — every finding's text — and
        # every draft view, save and preview fetched and parsed all of it, for
        # each issued report, to read this one small map.  The JSON path is
        # taken in the database (``->`` on Postgres, json_extract on SQLite).
        rows = (
            self.db.query(Report.snapshot["reported"])
            .filter(
                Report.project_id == project_id,
                Report.status.in_((ReportStatus.ISSUED, ReportStatus.SUPERSEDED)),
                Report.number.isnot(None),
            )
            .order_by(Report.number)
            .all()
        )
        ledger: Dict[str, dict] = {}
        for (reported,) in rows:
            if not isinstance(reported, dict):
                continue  # no snapshot, or one without the key
            for fid, entry in reported.items():
                if isinstance(entry, dict):
                    ledger[fid] = entry
        return ledger

    def summary(self, report: Report) -> dict:
        """What the report page shows before (draft) or after (issued) issuing."""
        if report.status != ReportStatus.DRAFT and report.snapshot:
            return dict((report.snapshot or {}).get("summary") or {})
        try:
            return self.build(report)[2]
        except ReportStateError as exc:
            return {"error": str(exc)}

    def content(self, report: Report) -> Tuple[Optional[dict], dict]:
        """``(dataset, summary)`` — what the report says.  An issued (or
        superseded) report's frozen dataset; a draft's, built from the live
        findings as a preview would.  The dataset is None when a draft cannot
        be built (an addendum whose baseline is gone); the summary then
        carries the reason, as ``summary()`` does."""
        if report.status != ReportStatus.DRAFT and report.snapshot:
            snap = report.snapshot or {}
            return snap.get("dataset"), dict(snap.get("summary") or {})
        try:
            dataset, _, summary = self.build(report)
        except ReportStateError as exc:
            return None, {"error": str(exc)}
        return dataset, summary

    # ------------------------------------------------------------------
    # Issuing
    # ------------------------------------------------------------------
    def latest_issued(self, project_id: int) -> Optional[Report]:
        return (
            self.db.query(Report)
            .filter(Report.project_id == project_id, Report.status == ReportStatus.ISSUED)
            .order_by(Report.number.desc())
            .first()
        )

    def issue(
        self, report_id: int, project_id: int, *, user_id: int,
        fingerprint: Union[str, Callable[[Report], str]],
    ) -> Report:
        """Freeze a draft.  Serialised per project (the project row is locked)
        so two issues cannot take the same number.

        ``fingerprint`` is the template fingerprint to record, or a callable
        given the LOCKED report that returns it (review 2026-10-01 R13) — the
        template is only certain once the row cannot change.  It is called
        before anything is written, so it may refuse by raising.

        ``FOR NO KEY UPDATE`` (``key_share=True``), not ``FOR UPDATE``: two
        issues still exclude each other, but the lock no longer blocks the
        ``FOR KEY SHARE`` every insert into a project-owned table takes, so
        ingestion, notes and triage keep running while a report is issued
        (review 2026-09-23 R4)."""
        self.db.query(Project).filter(Project.id == project_id).with_for_update(key_share=True).one()
        report = (
            self.db.query(Report)
            .filter(Report.id == report_id, Report.project_id == project_id)
            .with_for_update().populate_existing().one_or_none()
        )
        if report is None:
            raise LookupError("Report not found")
        if report.status != ReportStatus.DRAFT:
            raise ReportStateError("Only a draft can be issued; this report has already been issued.")
        original = None
        if report.revision_of_id:
            original = (
                self.db.query(Report).filter(Report.id == report.revision_of_id)
                .with_for_update().populate_existing().one_or_none()
            )
            if original is None or original.status != ReportStatus.ISSUED:
                raise ReportStateError(
                    "The report this revises is no longer the current issue (it was superseded or deleted)."
                )
        if report.kind == ReportKind.ADDENDUM:
            baseline = report.baseline
            if baseline is None or baseline.status == ReportStatus.DRAFT:
                raise ReportStateError("An addendum needs an issued report to compare against.")
            # The current issue — other than the one this draft revises (a
            # revision of the latest addendum compares with that addendum's
            # own baseline).
            latest = (
                self.db.query(Report)
                .filter(
                    Report.project_id == project_id, Report.status == ReportStatus.ISSUED,
                    Report.id != (original.id if original is not None else -1),
                )
                .order_by(Report.number.desc())
                .first()
            )
            if latest is None or latest.id != baseline.id:
                raise ReportStateError(
                    f"Report {baseline.number} is no longer the current issue"
                    + (f" (report {latest.number} was issued since)" if latest is not None else "")
                    + ". Compare this addendum against the current issue before issuing it, or it "
                    "would list again what the client already has."
                )

        if callable(fingerprint):
            fingerprint = fingerprint(report)

        number = (
            self.db.query(func.max(Report.number)).filter(Report.project_id == project_id).scalar() or 0
        ) + 1
        now = datetime.now(timezone.utc)
        dataset, reported, summary = self.build(report, number=number, issued_at=now)
        # The report's own copies of its images, before anything is marked
        # issued: a missing file refuses the issue.  If the transaction does
        # not commit AFTER this method returns, the copies stay until the next
        # attempt (which empties the folder first, under this lock) or until
        # the draft is discarded — the caller must not remove them once the
        # lock is gone.
        freeze_report_images(self.db, report, dataset)
        try:
            # Its own copies of its template and of the scope file it names,
            # under the same rule as the images.
            frozen_template = freeze_report_template(report)
            freeze_report_scope(report, dataset)
            report.snapshot = {
                "schema": SCHEMA_VERSION, "dataset": dataset, "reported": reported, "summary": summary,
            }
            if frozen_template is not None:
                report.snapshot["template"] = frozen_template
            report.number = number
            report.status = ReportStatus.ISSUED
            report.issued_at = now
            report.issued_by_id = user_id
            report.template_fingerprint = fingerprint
            report.render_status = RenderStatus.PENDING
            report.render_error = None
            if original is not None:
                original.status = ReportStatus.SUPERSEDED
            self.db.flush()
        except Exception:
            discard_report_copies(project_id, report_id)
            raise
        return report
