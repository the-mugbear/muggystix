"""AI-assisted report drafting.

Two drafts, both through the operator's own **LLM provider**
(``llm_provider_service.chat_completion``, which owns the SSRF-safe transport
and the encrypted credentials):

* **a report's summary** (``build_context`` / ``generate``) — from that
  report's own dataset, ``ClientReportService.content``: the findings the
  report prints, the endpoints it lists and its counts.  Never a second
  selection of findings — a summary must not describe what the report does
  not contain.  The answer is editable Markdown handed back to the caller;
  nothing is stored.
* **one finding's report text** (``build_finding_text_context`` /
  ``draft_finding_text``) — from what is known about that finding; the
  drafts become proposals.

Image bytes never enter a prompt: figures are named by their captions.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect as sa_inspect, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_findings import Finding
from app.db.models_vulnerability import Vulnerability
from app.services import report_writing_guidance
from app.services.llm_provider_service import (
    LLMProviderService,
    chat_completion,
)
from app.services.prompt_sanitizer import sanitize_for_llm
from app.services.report_text import internal_reference_message, internal_references

logger = logging.getLogger(__name__)

# Cap the number of evidence-image captions we enumerate per finding — a
# screenshot-heavy finding shouldn't dominate the prompt.
_MAX_CAPTIONS_PER_FINDING = 8
# The same for a finding's endpoints (``affected_count`` carries the total)
# and for each written section.
_MAX_AFFECTED_PER_FINDING = 50
_MAX_SECTION_CHARS = 2000

_SYSTEM_PROMPT = (
    "You are a senior penetration-test report writer. You draft clear, "
    "professional, factual security assessment reports for a technical and "
    "management audience. You are given the STRUCTURED, ALREADY-TRIAGED "
    "findings of one client report, exactly as that report lists them — "
    "each with its reference, severity, the systems it affects (a count and "
    "a sample), what the report says about it, and figure captions.\n\n"
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
        # One statement for every finding (review 2026-10-01 R16), through
        # the client report's lookup; every attached image, as before.
        from app.services.client_report_service import finding_image_attachments
        attached = finding_image_attachments(
            self.db, [(f.get("id"), f.get("evidence_annotation_id")) for f in findings],
            marked_only=False,
        )
        for f in findings:
            fid = f.get("id")
            atts = [att for att, _actor in attached.get(fid, ())][:_MAX_CAPTIONS_PER_FINDING]
            captions = [
                (getattr(a, "caption", None) or getattr(a, "filename", None) or "screenshot")
                for a in atts
            ]
            if captions:
                out[fid] = captions
        return out

    def build_context(self, report) -> Dict[str, Any]:
        """What the model is given to draft ONE report's summary: that
        report's own dataset (``ClientReportService.content`` — the findings
        it prints, the endpoints it lists for each, its counts), nothing
        else.

        One implementation (review 2026-10-10 H10): the draft used to be built
        from every finding attached to any host of the project — open ones
        and false positives included — so a summary could describe a Critical
        the report does not contain.  A finding the report leaves out, an
        endpoint it drops and a status it does not print are therefore not
        here either, and neither is the team's discussion: the written
        sections are what the report says.

        Raises ``ValueError`` when the report cannot be built (an addendum
        whose baseline is gone)."""
        from app.services.client_report_service import ClientReportService

        dataset, summary = ClientReportService(self.db).content(report)
        if dataset is None:
            raise ValueError(summary.get("error") or "This report cannot be built yet.")
        label = ClientReportService._label

        shaped: List[Dict[str, Any]] = []
        for item in dataset["findings"]:
            affected = [label(ep) for ep in item["affected"]]
            shaped.append({
                # The report's own reference ("F-03"), never a record id: the
                # reader of the summary can look that up in the report.
                "ref": item["ref"],
                "title": item["title"],
                "severity": item["severity"],
                "status": item["status"],
                # An addendum: new / new_hosts / severity_changed.
                "change": item["change"],
                # ``affected_count`` is the report's number; the list is a
                # preview of it, cut for the prompt.
                "affected_count": item["affected_count"],
                "affected_hosts": affected[:_MAX_AFFECTED_PER_FINDING],
                # What the report says about it, each cut for the prompt.
                **{
                    field: (item.get(field) or "")[:_MAX_SECTION_CHARS] or None
                    for field in ("description", "impact", "recommendation")
                },
                # Only figures the report PRINTS: a summary that cites a figure
                # the template leaves out points the reader at nothing (review
                # 2026-10-14, nitpick).  `printed` is None when it could not be
                # measured — then the image counts, as before.
                "evidence_figures": [
                    img["caption"] for img in item["images"]
                    if img.get("caption") and img.get("printed") is not False
                ][:_MAX_CAPTIONS_PER_FINDING],
            })

        return {
            "project_name": dataset["project"]["name"] or f"project #{report.project_id}",
            "report_title": dataset["report"]["title"],
            "report_kind": dataset["report"]["kind"],
            "finding_total": len(shaped),
            # The report's own roll-up (its summary table), so the model opens
            # with the totals the reader will see printed.
            "severity_counts": {
                sev: n for sev, n in dataset["counts"].items() if sev != "total" and n
            },
            "findings": shaped,
        }

    # -- generation ---------------------------------------------------------

    def generate(
        self,
        report,
        *,
        provider_id: Optional[int] = None,
        audience: Optional[str] = None,
        instructions: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Draft a report's summary from that report's findings via the LLM.

        Raises ``ValueError`` for user-fixable problems (no provider configured,
        no findings to draft from) and ``RuntimeError`` for provider/transport
        failures — the endpoint maps these to 400 / 502 respectively.
        """
        provider = self._provider(provider_id)
        context = self.build_context(report)
        if context["finding_total"] == 0:
            raise ValueError(
                "This report lists no findings to draft from. A report prints "
                "confirmed, accepted-risk and remediated findings: confirm one "
                "on the Findings page first."
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
            "Draft the assessment report from the findings this report lists. "
            "The structured data follows as JSON.\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False)}\n```"
        )

        # Server-side sanitisation is the enforcement point (mirrors the
        # /complete endpoint) — strip anything key-shaped before it leaves.
        safe_system = sanitize_for_llm(_SYSTEM_PROMPT)
        safe_user = sanitize_for_llm(user_message)

        self._end_read(provider)
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

    def _end_read(self, provider) -> None:
        """End the request's read transaction before the provider is called
        (review 2026-10-23b, REP-2) — the call can take minutes (a local
        model: 120 s, twice), and an open transaction keeps its share locks on
        findings, reports and hosts all that time, so a migration's ALTER
        queues behind it and every request behind the ALTER.  The renderer
        does the same before Quarto (``before_quarto=db.rollback``).  Nothing
        is pending here, and what the prompt needs is plain data by now; the
        provider row is detached first so the call reads its loaded values
        rather than opening a new transaction to reload them."""
        state = sa_inspect(provider, raiseerr=False)
        if state is not None and state.session is not None:
            self.db.expunge(provider)
        self.db.rollback()

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
            f"{json.dumps(fields)} plus \"missing\".  Each section's value is its "
            "Markdown, or null when the data below does not support writing it; "
            "\"missing\" maps each null section to one sentence naming the "
            "information or context that would let it be written.  Use only the "
            "data below.  Keep the text already written consistent with yours.\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, default=str)}\n```"
        )
        system = sanitize_for_llm(
            report_writing_guidance.drafter_system_prompt(report_writing_guidance.load(self.db))
        )
        self._end_read(provider)
        result = chat_completion(
            provider,
            system=system,
            messages=[{"role": "user", "content": sanitize_for_llm(user_message)}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        suggestions, declined = parse_finding_text_answer(result.get("content", ""), fields)
        if not suggestions and not declined:
            raise RuntimeError("The provider's answer could not be read as the requested sections.")
        # A section that names a BlueStick record is not report text (the
        # proposal would be refused for it): it is declined, with the reason.
        for field in list(suggestions):
            phrases = internal_references(suggestions[field])
            if phrases:
                del suggestions[field]
                declined[field] = ("The draft was left out: " + internal_reference_message(phrases))[:DECLINED_REASON_MAX]
        raw = result.get("raw") or {}
        return {
            "suggestions": suggestions,
            # Sections the model declined: {field: what would let it be written}.
            "declined": declined,
            "provider_id": provider.id,
            "provider_type": provider.provider_type,
            "model_id": provider.model_id,
            "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        }


# The finding's report-text fields a draft may fill (cvss is a measurement,
# never drafted).  How each is to be written is the installation's
# ``report_writing_guidance``, which also builds the system prompt.
FINDING_TEXT_FIELDS = report_writing_guidance.SECTIONS


#: The longest reason kept for a declined section (it is shown, never stored
#: as report text).
DECLINED_REASON_MAX = 500


def _answer_object(content: str) -> dict:
    text = (content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_finding_text_answer(content: str, fields: List[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """The provider's answer as (sections written, sections declined).

    A JSON object, possibly inside a code fence or surrounded by prose.  A
    requested field with text is written; one that is null, or named under
    ``missing``, is DECLINED with the reason given (2.455.0) — the model may
    say the data is too thin, and that sentence is never report text.
    Unknown keys and non-strings are dropped."""
    data = _answer_object(content)
    written = {
        k: data[k].strip() for k in fields
        if isinstance(data.get(k), str) and data[k].strip()
    }
    missing = data.get("missing") if isinstance(data.get("missing"), dict) else {}
    declined: Dict[str, str] = {}
    for k in fields:
        if k in written:
            continue
        reason = missing.get(k)
        if k in data or k in missing:
            text = reason.strip() if isinstance(reason, str) else ""
            declined[k] = (text or "The data does not support writing this section.")[:DECLINED_REASON_MAX]
    return written, declined


def parse_finding_text_suggestions(content: str, fields: List[str]) -> Dict[str, str]:
    """The requested fields the provider wrote (see :func:`parse_finding_text_answer`)."""
    return parse_finding_text_answer(content, fields)[0]
