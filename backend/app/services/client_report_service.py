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
with its reference and endpoints.  ``reported`` is cumulative (an addendum's
covers everything reported so far), so the next addendum always compares
against one state.

**The delta** (an addendum) compares the live state with the baseline's
``reported`` by finding AND endpoint — never by date: a promotion that joins an
existing finding adds endpoints to an old finding, and that is new.  It lists
new findings, new endpoints on reported findings, and what was withdrawn since
(findings deleted, set false positive or back under investigation; endpoints
detached or set false positive).  It never reports remediation: a project is
one assessment window, not response tracking.

**References.**  A full report numbers its findings F-01, F-02… in report
order.  An addendum keeps the baseline's references for findings already
reported and continues the numbering for new ones, so "F-03" means the same
finding in the report and its addenda.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.db.models import Annotation, NoteAttachment, Port, Scope, ScopeDomain, Subnet
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingStatus, FindingVulnerability,
)
from app.db.models_project import Project
from app.db.models_reports import (
    RenderStatus, Report, ReportKind, ReportProfile, ReportStatus,
)
from app.db.models_vulnerability import Vulnerability

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
# Formats every renderer can place (Word, Typst and HTML alike).
REPORT_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}

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

    # ------------------------------------------------------------------
    # Live state
    # ------------------------------------------------------------------
    def _included(self, project_id: int) -> List[Finding]:
        findings = (
            self.db.query(Finding)
            .options(
                selectinload(Finding.hosts).selectinload(FindingHost.host),
                selectinload(Finding.hosts).selectinload(FindingHost.name),
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

    def _evidence(self, findings: List[Finding]) -> Tuple[Dict[int, List[dict]], int]:
        """Images MARKED for the report, from the finding's source-note thread
        and its own comments.  Returns (by finding, count skipped for format)."""
        by_finding: Dict[int, List[dict]] = defaultdict(list)
        skipped = 0
        finding_ids = {f.id for f in findings}
        roots: Dict[int, int] = {
            f.evidence_annotation_id: f.id for f in findings if f.evidence_annotation_id
        }
        if not finding_ids:
            return {}, 0
        conds = [Annotation.finding_id.in_(finding_ids)]
        if roots:
            conds += [Annotation.id.in_(roots), Annotation.thread_root_id.in_(roots)]
        rows = (
            self.db.query(NoteAttachment, Annotation.id, Annotation.finding_id, Annotation.thread_root_id)
            .join(Annotation, Annotation.id == NoteAttachment.annotation_id)
            .filter(or_(*conds), NoteAttachment.include_in_report.is_(True))
            .order_by(NoteAttachment.id)
            .all()
        )
        for att, ann_id, ann_finding, ann_root in rows:
            targets = set()
            if ann_finding in finding_ids:
                targets.add(ann_finding)
            if ann_id in roots:
                targets.add(roots[ann_id])
            if ann_root in roots:
                targets.add(roots[ann_root])
            ext = REPORT_IMAGE_TYPES.get(att.content_type)
            if ext is None:
                skipped += 1
                continue
            for fid in targets:
                by_finding[fid].append({
                    "attachment_id": att.id,
                    "file": f"evidence/{att.id}.{ext}",
                    "caption": att.filename,
                })
        return dict(by_finding), skipped

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
        evidence, skipped_images = self._evidence(findings)

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
            baseline_reported = dict((baseline.snapshot or {}).get("reported") or {})

        # References.
        refs: Dict[int, str] = {}
        if report.kind == ReportKind.ADDENDUM:
            next_n = max((_ref_number(v.get("ref")) for v in baseline_reported.values()), default=0)
            for f in findings:
                prior = baseline_reported.get(str(f.id))
                if prior and prior.get("ref"):
                    refs[f.id] = prior["ref"]
                else:
                    next_n += 1
                    refs[f.id] = f"F-{next_n:02d}"
        else:
            for i, f in enumerate(findings, start=1):
                refs[f.id] = f"F-{i:02d}"

        reported = {
            str(f.id): {
                "ref": refs[f.id], "title": f.title, "severity": f.severity, "status": f.status,
                "endpoints": {k: self._label(ep) for k, ep in endpoints[f.id].items()},
            }
            for f in findings
        }

        # Which findings the document shows.
        delta = None
        shown: List[Tuple[Finding, Optional[str], List[dict]]] = []
        if report.kind == ReportKind.ADDENDUM:
            withdrawn = self._withdrawn(report.project_id, baseline_reported, reported)
            for f in findings:
                prior = baseline_reported.get(str(f.id))
                if prior is None:
                    shown.append((f, "new", []))
                    continue
                new_keys = [k for k in endpoints[f.id] if k not in (prior.get("endpoints") or {})]
                if new_keys:
                    shown.append((f, "new_hosts", [endpoints[f.id][k] for k in new_keys]))
            delta = {
                "new_findings": sum(1 for _, c, _ in shown if c == "new"),
                "findings_with_new_endpoints": sum(1 for _, c, _ in shown if c == "new_hosts"),
                "withdrawn": withdrawn,
            }
        else:
            shown = [(f, None, []) for f in findings]

        items = []
        for index, (f, change, new_affected) in enumerate(shown):
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
                "evidence": evidence.get(f.id, []),
                "corroboration": corroboration.get(f.id, []),
            })

        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for item in items:
            if item["change"] in (None, "new") and item["severity"] in counts:
                counts[item["severity"]] += 1
        counts["total"] = sum(counts[s] for s in SEVERITY_ORDER)

        settings = {k: (report.settings or {}).get(k) for k in SETTINGS_KEYS}
        settings["testers"] = list(settings.get("testers") or [])
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
            "scope": self._scope(report.project_id),
            "severity_order": list(SEVERITY_ORDER),
            "severity_labels": SEVERITY_LABEL,
            "counts": counts,
            "findings": items,
            "delta": delta,
        }

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
            "missing_details": self._missing_details(dataset),
            "images": sum(len(i["evidence"]) for i in items),
            "images_skipped": skipped_images,
            "delta": {
                "new_findings": delta["new_findings"],
                "findings_with_new_endpoints": delta["findings_with_new_endpoints"],
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

    def summary(self, report: Report) -> dict:
        """What the report page shows before (draft) or after (issued) issuing."""
        if report.status != ReportStatus.DRAFT and report.snapshot:
            return dict((report.snapshot or {}).get("summary") or {})
        try:
            return self.build(report)[2]
        except ReportStateError as exc:
            return {"error": str(exc)}

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

    def issue(self, report_id: int, project_id: int, *, user_id: int, fingerprint: str) -> Report:
        """Freeze a draft.  Serialised per project (the project row is locked)
        so two issues cannot take the same number."""
        self.db.query(Project).filter(Project.id == project_id).with_for_update().one()
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
