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

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, load_only, noload, selectinload

from app.core.config import settings

from app.db.models import Annotation, Host, NoteAttachment, Port, Scope, ScopeDomain, Subnet
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingStatus, FindingVulnerability,
)
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.db.models_reports import (
    RenderStatus, Report, ReportKind, ReportProfile, ReportStatus,
)
from app.db.models_vulnerability import Vulnerability
from app.services import proposal_service, report_scope, report_template_service

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
REPORT_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}

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
    report passes True; the host report and the drafter's captions pass False
    (their unmarked images are a parked decision — it is this one argument).

    ONE statement per ``_ID_CHUNK`` findings (review 2026-10-01 R16): the
    host report and the drafter each ran two queries per finding."""
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

    def _evidence(self, findings: List[Finding]) -> Tuple[Dict[int, List[dict]], int, int]:
        """Images MARKED for the report, from the finding's source-note thread
        and its own comments.  Returns (by finding, count skipped for format,
        count attached to an agent-written note — v2.437.0, a warning before
        issuing, never a block)."""
        by_finding: Dict[int, List[dict]] = defaultdict(list)
        skipped = by_agent = 0
        counted = set()
        attached = finding_image_attachments(
            self.db, [(f.id, f.evidence_annotation_id) for f in findings], marked_only=True,
        )
        for fid, rows in attached.items():
            for att, actor_type in rows:
                first = att.id not in counted
                counted.add(att.id)
                ext = REPORT_IMAGE_TYPES.get(att.content_type)
                if ext is None:
                    skipped += first
                    continue
                if actor_type == "agent":
                    by_agent += first
                by_finding[fid].append({
                    "attachment_id": att.id,
                    "file": f"evidence/{att.id}.{ext}",
                    "caption": att.filename,
                })
        return dict(by_finding), skipped, by_agent

    def _records_in_report(self, template_name: Optional[str]) -> bool:
        """Whether the report's template prints how findings were confirmed:
        ``template.json`` → ``"evidence_records": true``.  Opt-in — a template
        that does not ask gets none in its data, so none is frozen at issue
        either."""
        try:
            return report_template_service.get_template(template_name).evidence_records
        except report_template_service.TemplateError:
            return False

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
        rows = []
        for start in range(0, len(ids), _ID_CHUNK):
            rows += (
                self.db.query(
                    EvidenceRecord.id, EvidenceRecord.finding_id, EvidenceRecord.host_id,
                    EvidenceRecord.tool, EvidenceRecord.command, EvidenceRecord.summary,
                    EvidenceRecord.raw_output_preview, EvidenceRecord.raw_output_bytes,
                    EvidenceRecord.executed_at, EvidenceRecord.created_at,
                    EvidenceRecord.agent_session_id, EvidenceRecord.recorded_by_user_id,
                )
                .filter(
                    EvidenceRecord.finding_id.in_(ids[start:start + _ID_CHUNK]),
                    EvidenceRecord.outcome == CONFIRMATION_OUTCOME,
                )
                .all()
            )
        if not rows:
            return {}, {}, 0
        rows.sort(key=lambda r: (r.executed_at or r.created_at, r.id) if (r.executed_at or r.created_at)
                  else (datetime.min.replace(tzinfo=timezone.utc), r.id))

        listed: Dict[int, Dict[int, str]] = {}
        for f in findings:
            listed[f.id] = {
                fh.host_id: (fh.host.ip_address if fh.host is not None else f"host {fh.host_id}")
                for fh in f._report_endpoints
            }
        loose_hosts = {r.host_id for r in rows if not by_id[r.finding_id].hosts}
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
        omitted: Dict[int, int] = defaultdict(int)
        by_agent = 0
        for r in rows:
            finding = by_id[r.finding_id]
            if finding.hosts:
                host = listed[finding.id].get(r.host_id)
                if host is None:
                    continue
            else:
                host = addresses.get(r.host_id) or f"host {r.host_id}"
            if len(out[finding.id]) >= CONFIRMATIONS_PER_FINDING:
                omitted[finding.id] += 1
                continue
            when = r.executed_at or r.created_at
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if r.agent_session_id:
                by_agent += 1
                operator = names.get(operators.get(r.agent_session_id))
                by = (f"{operator} (agent session {r.agent_session_id})" if operator
                      else f"Agent session {r.agent_session_id}")
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

    def _scope_block(self, report: Report, project: Optional[Project], number: Optional[int]) -> dict:
        """The scope as the report states it (v2.441.0): the lists, their
        totals and per-site summary, whether each list is printed (the
        template's cutoff), and — when it is not — the separate file's name
        and SHA-256 (``report_scope``).  Frozen with the rest of the dataset
        at issue, so an issued report's file never changes."""
        scope = self._scope(report.project_id)
        try:
            template = report_template_service.get_template(report.template)
            cutoffs = {"inline_max": template.scope_inline_max,
                       "domains_inline_max": template.scope_domains_inline_max}
        except report_template_service.TemplateError:
            cutoffs = {}
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
        if self._records_in_report(report.template):
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
                "evidence": evidence.get(f.id, []),
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
            "scope": self._scope_block(report, project, number if number is not None else report.number),
            "severity_order": list(SEVERITY_ORDER),
            "severity_labels": SEVERITY_LABEL,
            "counts": counts,
            "findings": items,
            "delta": delta,
        }

        pending = self._pending_proposals([item["id"] for item in items])
        summary = {
            "counts": counts,
            "findings_shown": len(items),
            "under_investigation": self.under_investigation_count(report.project_id),
            "missing_text": [
                {
                    "id": item["id"], "ref": item["ref"], "title": item["title"],
                    "missing": [k for k in REQUIRED_TEXT if not (item.get(k) or "").strip()],
                }
                for item in items
                if any(not (item.get(k) or "").strip() for k in REQUIRED_TEXT)
            ],
            # Report details still empty — printed as a highlighted TODO.
            "missing_details": self._missing_details(dataset) + (
                [f"a full name on the account of {', '.join(no_full_name)} (the report shows the username)"]
                if no_full_name else []
            ),
            "images": sum(len(i["evidence"]) for i in items),
            "images_skipped": skipped_images,
            # v2.437.0 — warnings before issuing, never blocks.
            "agent_images": agent_images,
            # B8 — test results printed as "how it was confirmed", and how
            # many of them an agent recorded (a warning, never a block).
            "evidence_records": sum(len(i["confirmations"]) for i in items),
            "agent_evidence_records": agent_records,
            "pending_proposals": [
                {"id": item["id"], "ref": item["ref"], "title": item["title"], "count": pending[item["id"]]}
                for item in items if pending.get(item["id"])
            ],
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
        report.snapshot = {
            "schema": SCHEMA_VERSION, "dataset": dataset, "reported": reported, "summary": summary,
        }
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
        return report
