"""AI-assisted report drafting (v2.246.0).

Composes the pieces that already exist but never talked to each other:

* the **Finding spine** — promoted findings with their evidence-note threads
  (``evidence_annotation_id``), scanner corroboration, affected hosts, and the
  analyst's own comment thread — assembled by
  ``ReportGenerator._findings_for_report_ids`` (reused, not reimplemented);
* **attached evidence images** (``NoteAttachment``) — surfaced here as captions
  so the model can reference figures without us shipping image bytes to every
  provider;
* the operator's **LLM provider** — ``llm_provider_service.chat_completion``,
  which owns the SSRF-safe transport + Fernet-encrypted credentials.

The output is an editable Markdown draft returned to the caller — "AI drafts,
human commits", the same rule the ``/llm-providers/{id}/complete`` endpoint
already enforces. Deliberately STATELESS in this cut: no draft table, so no
schema change (the repo's Alembic heads need merging first — tracked
separately). Persistence/iteration is a clean fast-follow.

Findings are the right input, not the full host inventory: they are curated and
bounded (a project has thousands of hosts but tens to low-hundreds of promoted
findings), so the prompt stays within a sane token budget without sampling.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_findings import Finding
from app.db.models_vulnerability import Vulnerability
from app.services.report_generator import ReportGenerator
from app.services.llm_provider_service import (
    LLMProviderService,
    chat_completion,
)
from app.services.prompt_sanitizer import sanitize_for_llm

logger = logging.getLogger(__name__)

# Cap the number of evidence-image captions we enumerate per finding — a
# screenshot-heavy finding shouldn't dominate the prompt.
_MAX_CAPTIONS_PER_FINDING = 8

_SYSTEM_PROMPT = (
    "You are a senior penetration-test report writer. You draft clear, "
    "professional, factual security assessment reports for a technical and "
    "management audience. You are given the STRUCTURED, ALREADY-TRIAGED "
    "findings for one project — promoted findings with their severity, "
    "affected hosts, evidence notes, scanner corroboration, and figure "
    "captions.\n\n"
    "Rules:\n"
    "- Write in Markdown.\n"
    "- Ground every statement in the supplied data. Do NOT invent findings, "
    "hosts, CVEs, or severities that are not present. If the data is thin, say "
    "so plainly rather than padding.\n"
    "- Reference evidence figures by their caption when one is supplied "
    "(e.g. \"see Figure: <caption>\").\n"
    "- Order the findings by severity (critical first). Group sensibly.\n"
    "- This is a DRAFT for a human to review and edit — it is not final and "
    "must not be presented to a client as-is. Do not fabricate an executive "
    "sign-off, client name, or dates that were not provided.\n"
)


class ReportDraftService:
    def __init__(self, db: Session, current_user):
        self.db = db
        self.current_user = current_user

    # -- context assembly ---------------------------------------------------

    def _evidence_image_captions(
        self, findings: List[Dict[str, Any]]
    ) -> Dict[int, List[str]]:
        """finding id -> [caption or filename, …] for its attached evidence
        images (both the promoted source-note thread and the finding's own
        comment thread). Captions only — image bytes never enter the prompt.
        """
        out: Dict[int, List[str]] = {}
        for f in findings:
            fid = f.get("id")
            root = f.get("evidence_annotation_id")
            ann_q = self.db.query(models.Annotation.id).filter(
                or_(
                    models.Annotation.finding_id == fid,
                    models.Annotation.id == root,
                    models.Annotation.thread_root_id == root,
                )
                if root
                else (models.Annotation.finding_id == fid)
            )
            ann_ids = [r[0] for r in ann_q.all()]
            if not ann_ids:
                continue
            atts = (
                self.db.query(models.NoteAttachment)
                .filter(models.NoteAttachment.annotation_id.in_(ann_ids))
                .order_by(models.NoteAttachment.id)
                .limit(_MAX_CAPTIONS_PER_FINDING)
                .all()
            )
            captions = [
                (getattr(a, "caption", None) or getattr(a, "filename", None) or "screenshot")
                for a in atts
            ]
            if captions:
                out[fid] = captions
        return out

    def build_context(
        self,
        project_id: int,
        *,
        severities: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Assemble the compact, evidence-bearing findings context for the LLM.

        Reuses ``ReportGenerator._findings_for_report_ids`` so the draft path and
        the deterministic export path share ONE finding-correlation
        implementation (no second source of truth).
        """
        project = (
            self.db.query(models.Project)
            .filter(models.Project.id == project_id)
            .first()
        )
        gen = ReportGenerator(self.db, self.current_user, project_id)
        host_ids = [
            hid
            for (hid,) in self.db.query(models.Host.id)
            .filter(models.Host.project_id == project_id)
            .all()
        ]
        findings = gen._findings_for_report_ids(host_ids)

        sev_filter = {s.lower() for s in severities} if severities else None
        status_filter = {s.lower() for s in statuses} if statuses else None
        if sev_filter is not None:
            findings = [f for f in findings if (f.get("severity") or "").lower() in sev_filter]
        if status_filter is not None:
            findings = [f for f in findings if (f.get("status") or "").lower() in status_filter]

        captions = self._evidence_image_captions(findings)

        shaped: List[Dict[str, Any]] = []
        for f in findings:
            shaped.append(
                {
                    "title": f.get("title"),
                    "severity": f.get("severity"),
                    "status": f.get("status"),
                    "source": f.get("source"),
                    "host_count": f.get("host_count"),
                    "affected_hosts": f.get("affected_hosts", [])[:50],
                    # The analyst's evidence/rationale thread — the textual half
                    # of "reports include the evidence attached to a finding".
                    "evidence_notes": [
                        c.get("body", "")
                        for c in (f.get("comments") or [])
                        if c.get("body")
                    ],
                    "evidence_figures": captions.get(f.get("id"), []),
                }
            )

        # Severity roll-up so the model can open with accurate totals.
        sev_counts: Dict[str, int] = {}
        for f in shaped:
            key = (f.get("severity") or "unknown").lower()
            sev_counts[key] = sev_counts.get(key, 0) + 1

        return {
            "project_name": project.name if project else f"project #{project_id}",
            "finding_total": len(shaped),
            "severity_counts": sev_counts,
            "findings": shaped,
        }

    # -- generation ---------------------------------------------------------

    def generate(
        self,
        project_id: int,
        *,
        provider_id: Optional[int] = None,
        audience: Optional[str] = None,
        instructions: Optional[str] = None,
        severities: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Draft a report from the project's findings + evidence via the LLM.

        Raises ``ValueError`` for user-fixable problems (no provider configured,
        no findings to draft from) and ``RuntimeError`` for provider/transport
        failures — the endpoint maps these to 400 / 502 respectively.
        """
        svc = LLMProviderService(self.db)
        provider = (
            svc.get(provider_id, self.current_user.id)
            if provider_id is not None
            else svc.get_default(self.current_user.id)
        )
        if provider is None:
            raise ValueError(
                "No LLM provider is configured. Add one on the LLM Providers "
                "page (or pass a provider_id) before drafting a report."
            )

        context = self.build_context(
            project_id, severities=severities, statuses=statuses
        )
        if context["finding_total"] == 0:
            raise ValueError(
                "This project has no promoted findings to draft a report from. "
                "Promote findings on the Findings page first."
            )

        audience_line = (
            f"Intended audience: {audience.strip()}.\n"
            if audience and audience.strip()
            else ""
        )
        extra = (
            f"Additional operator instructions: {instructions.strip()}\n"
            if instructions and instructions.strip()
            else ""
        )
        user_message = (
            f"{audience_line}{extra}"
            "Draft the assessment report from this project's triaged findings. "
            "The structured data follows as JSON.\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False)}\n```"
        )

        # Server-side sanitisation is the enforcement point (mirrors the
        # /complete endpoint) — strip anything key-shaped before it leaves.
        safe_system = sanitize_for_llm(_SYSTEM_PROMPT)
        safe_user = sanitize_for_llm(user_message)

        result = chat_completion(
            provider,
            system=safe_system,
            messages=[{"role": "user", "content": safe_user}],
            max_tokens=max_tokens,
            temperature=0.4,
        )

        raw = result.get("raw") or {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        return {
            "content": result.get("content", ""),
            "provider_id": provider.id,
            "provider_type": provider.provider_type,
            "model_id": provider.model_id,
            "finding_total": context["finding_total"],
            "severity_counts": context["severity_counts"],
            "usage": usage,
        }

    # -- one finding's report text (review 2026-09-23 B-Ops-5) ---------------

    def _provider(self, provider_id: Optional[int]):
        svc = LLMProviderService(self.db)
        provider = (
            svc.get(provider_id, self.current_user.id)
            if provider_id is not None
            else svc.get_default(self.current_user.id)
        )
        if provider is None:
            raise ValueError(
                "No LLM provider is configured. Add one on the LLM Providers "
                "page (or pass a provider_id) before drafting."
            )
        return provider

    def build_finding_text_context(self, finding: Finding) -> Dict[str, Any]:
        """What is known about ONE finding: its identity, the hosts it is on,
        the analyst's notes (the source thread and the finding's comments),
        the scanner's description and solution when it was promoted from a
        scanner row, the report text already written, and figure captions."""
        notes: List[str] = []
        root = finding.evidence_annotation_id
        if root:
            notes += [
                body for (body,) in self.db.query(models.Annotation.body)
                .filter(or_(models.Annotation.id == root, models.Annotation.thread_root_id == root))
                .order_by(models.Annotation.created_at.asc(), models.Annotation.id.asc())
                .all() if body
            ]
        notes += [
            body for (body,) in self.db.query(models.Annotation.body)
            .filter(models.Annotation.finding_id == finding.id)
            .order_by(models.Annotation.created_at.asc(), models.Annotation.id.asc())
            .all() if body
        ]
        scanner = None
        if finding.vuln_id:
            vuln = self.db.get(Vulnerability, finding.vuln_id)
            if vuln is not None:
                scanner = {
                    "title": vuln.title, "cve_id": vuln.cve_id,
                    "description": (vuln.description or "")[:4000] or None,
                    "solution": (vuln.solution or "")[:2000] or None,
                    "evidence": (vuln.plugin_output or "")[:2000] or None,
                }
        hosts = [fh.host.ip_address for fh in finding.hosts if fh.host][:50]
        written = {
            k: getattr(finding, k) for k in FINDING_TEXT_FIELDS if (getattr(finding, k) or "").strip()
        }
        captions = self._evidence_image_captions(
            [{"id": finding.id, "evidence_annotation_id": root}]
        ).get(finding.id, [])
        return {
            "title": finding.title,
            "severity": finding.severity,
            "status": finding.status,
            "host_count": len(finding.hosts),
            "affected_hosts": hosts,
            "analyst_notes": notes[:40],
            "scanner": scanner,
            "already_written": written,
            "evidence_figures": captions,
        }

    def draft_finding_text(
        self,
        finding: Finding,
        fields: List[str],
        *,
        provider_id: Optional[int] = None,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        """Suggest Markdown for ``fields`` of one finding's report text.

        Returns suggestions only: nothing is written.  The author accepts
        (and edits) them through the ordinary finding update, so the
        authored-content rule is unchanged.  Raises ``ValueError`` for a
        user-fixable problem and ``RuntimeError`` when the provider fails or
        its answer cannot be read as the requested fields."""
        provider = self._provider(provider_id)
        context = self.build_finding_text_context(finding)
        user_message = (
            "Write these sections of this finding's write-up: "
            f"{', '.join(fields)}.  Return ONLY a JSON object whose keys are exactly "
            f"{json.dumps(fields)} and whose values are Markdown strings.  "
            "Use only the data below; where it is too thin for a section, say what "
            "is missing instead of inventing it.  Keep the text already written "
            "consistent with yours.\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, default=str)}\n```"
        )
        result = chat_completion(
            provider,
            system=sanitize_for_llm(_FINDING_TEXT_PROMPT),
            messages=[{"role": "user", "content": sanitize_for_llm(user_message)}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        suggestions = parse_finding_text_suggestions(result.get("content", ""), fields)
        if not suggestions:
            raise RuntimeError("The provider's answer could not be read as the requested sections.")
        raw = result.get("raw") or {}
        return {
            "suggestions": suggestions,
            "provider_id": provider.id,
            "provider_type": provider.provider_type,
            "model_id": provider.model_id,
            "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        }


# The finding's report-text fields a draft may fill (cvss is a measurement,
# never drafted).
FINDING_TEXT_FIELDS = ("description", "impact", "recommendation", "steps_to_reproduce", "references")

_FINDING_TEXT_PROMPT = (
    "You are a senior penetration-test report writer drafting ONE finding's "
    "write-up for a client report: description (what the issue is, in the "
    "client's terms), impact (what an attacker gains on this network), "
    "recommendation (what to change to fix it), steps to reproduce, and "
    "references (advisories and vendor guidance, one per line).\n\n"
    "Rules:\n"
    "- Ground every statement in the supplied data. Do NOT invent hosts, CVEs, "
    "versions or results that are not present.\n"
    "- Plain, factual Markdown; no headings (the report supplies them).\n"
    "- This is a DRAFT a human reviews and edits before it is used.\n"
    "- Answer with the JSON object requested and nothing else.\n"
)


def parse_finding_text_suggestions(content: str, fields: List[str]) -> Dict[str, str]:
    """The requested fields from a provider's answer: a JSON object, possibly
    inside a code fence or surrounded by prose.  Unknown keys, non-strings
    and empty values are dropped."""
    text = (content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: data[k].strip() for k in fields
        if isinstance(data.get(k), str) and data[k].strip()
    }
