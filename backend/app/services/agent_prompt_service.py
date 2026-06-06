"""
Agent Prompt Service

Builds the human-readable instruction blocks handed to AI agents for two
flows: (1) populating a new test plan, and (2) executing an approved test
plan.  Both flows previously had their instructions hardcoded inline in
``test_plans.py``; centralizing them here lets us version the prompts,
emit identical text from future export-bundle / report flows, and attach
a ``prompt_version`` to any feedback collected from agents.

The ``PROMPT_VERSION`` constant MUST be bumped whenever the instruction
content changes in a way that affects agent behavior.  Agent feedback
records stamp this version so prompt revisions can be compared over time.
"""

import json
import logging
from typing import Any, Dict, Optional

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


PROMPT_VERSION = "1.32.0"  # v2.85.0 — comprehensive feedback coverage + post-2.84.x guidance refresh.  Assist prompt now closes with the standard _feedback_section (AgentFeedbackSource gained ASSIST and AgentFeedback gained assist_session_id, so the row finally links back to the conversation it came from).  Recon feedback context drops scope_id from the rendered template — AgentFeedbackCreate never declared the field, so Pydantic silently dropped it on receive and recon feedback rows lost their workflow attribution; now the template only invites the linkable field, recon_session_id (scope is recoverable via ReconSession.scope_id when triage needs it).  Plan-gen / execution feedback contexts unchanged (already correct).  Also covers the AGENTS.md guidance-currency sweep (60-rpm rate-limit numbers bumped to 240 to match v2.84.0; /execution-sessions/{id}/complete + /environment + /feedback added to the API reference tables; has_exploit_available filter added to the plan-gen entry rubric now that v2.83.2 actually persists Vulnerability.exploitable).  Prior 1.31.0 (v2.79.0) — stop execution agents re-running recon scans.  Field report: an execution run proposed an nmap scan identical to recon's.  Root cause was guidance: the plan-gen rubric authored generic discovery/version scans (nmap -sV) as "tests" and nothing told plan-gen OR execution that recon already characterized these hosts.  PLAN-GEN prompt now carries a "build on recon — don't re-discover" clause (open ports/services/versions are already in candidate_hosts[].ports; propose targeted validation/exploitation on the KNOWN ports; never nmap -sn/-sV/full-port sweeps).  EXECUTION prompt now makes known_services[] authoritative (target tests at the known ports; narrow/skip any proposed test that resolves to a broad nmap discovery scan; the sanity check is single-port verification, not a re-scan).  AGENTS.md rubric retargeted (the "multiple services, no vulns -> reconnaissance -> nmap -sV" row is gone; named NSE scripts only), entry_template + Proposed-Test-Format example changed from `nmap --script smb-enum-shares` to a finding-driven `netexec` validation, known_services[] re-documented as authoritative, sanity-check clarified.  Prior 1.30.0 (v2.78.0) — comprehensive guidance-currency sweep across all workflows (audit follow-up to 1.29.0).  EXECUTION prompt: rewrote the stale "Results gate (v2.25.0) = zero rows" block to the real per-test coverage gate (every proposed test_index needs a TERMINAL result row — executed/skipped/failed/not_applicable — or no_tests_run_reason; pending/pending_approval block completion), and documented the 8192-byte session-notes cap.  ASSIST prompt: /assist/context is a headline summary (scopes capped 50 / recent_scans+recon capped 5 — use the totals block + scopes_truncated), /assist/hosts is a bare paginated array (default 500/max 5000, NO has_more — page with offset until short page; never count from one page), /assist/scopes 100-CIDR/scope cap, /assist/scans 500-max/no-offset.  PLAN-GEN prompt: empty final page (has_more boundary) is "done", not an error.  Prior 1.29.0 (v2.77.0) — plan-gen prompt now tells the agent to PAGE /context (call again with ?after_host_id={next_cursor} until summary.has_more is false) and to BATCH POST /entries in ≤500-host chunks, so a project with >500 candidate hosts gets full coverage instead of silently stopping at the first 500-host page (agents were reading has_more:true as a cap rather than a "fetch next page" signal).  Prior 1.28.0 (v2.76.1) — extended the OS-neutral API-invocation guidance to the EXECUTION prompt (and the shared § Environment probe in AGENTS.md): a Windows execution agent's BlueStick API calls (probe, result recording) hit the same bare-`curl`-is-an-Invoke-WebRequest-alias trap, so execution now spells out `curl.exe` / `Invoke-RestMethod` + PowerShell POST-body quoting.  Windows-only (execution stays Win+Linux; macOS is assist-only).  Prior 1.27.0 (v2.76.0) — AI-assist prompt made OS-neutral so Windows/macOS/Linux operators can all use it: assist's "commands" are HTTPS API calls (not shell tools), so it now spells out per-shell invocation (bash/zsh `curl -sk`; Windows PowerShell `curl.exe`/`Invoke-RestMethod`, with the bare-`curl`-is-an-alias gotcha called out; PowerShell POST-body quoting) and tells the assist agent to report just os_family/shell — the recon/execution tool-inventory + preflight flow explicitly does NOT apply to assist.  Prior 1.26.0 (v2.58.0) — platform renamed NetworkMapper → BlueStick in every operator-facing surface (AGENTS.md, agent prompts, frontend UI, FastAPI title, README/CLAUDE.md intros).  Working-directory slug stays `networkmapper-<project>-<workflow>-<session_id>` deliberately so concurrent agents from prior sessions on disk still align with what new agents create — the slug is internal session-isolation plumbing, not operator-facing.  Database name, env vars, logger names, and code identifiers also stay NetworkMapper — see v2.58.0 CHANGELOG for the user-visible-only scope rationale.  Prior 1.25.0 (v2.49.4) — Nessus integration block now surfaces a license-cap chunking directive (operator-supplied `max_hosts_per_scan` in extra_config) so an agent attacking a large scope splits the Nessus scan into multiple license-sized runs rather than submitting one oversize scan Nessus rejects or truncates.  Prior 1.24.0 (v2.49.3) — /agent/test-plans/{id}/context now returns entry_template + entry_batch_example + entry_schema so plan-gen agents pattern-match on a concrete payload instead of inferring the POST /entries shape from prose; the plan-gen prompt nudges them to use those fields.  Prior 1.23.0 (v2.49.1): qualify the session-scoped working directory with the project slug (networkmapper-<project>-<workflow>-<session_id>).  Within one deployment session_ids are already globally unique, but an operator who works two projects out of the same parent directory got opaque names (`networkmapper-recon-1` doesn't say which project) and a Nuclear-Clean + restart could collide with a leftover folder; the project slug self-documents the folder and survives ID-reset.  Prior 1.22.0 (v2.47.0): session-resume — RESUMED-SESSION notice on the prompt and the execution prompt gains a RESUMED-SESSION notice (steers a resumed agent to read /execution-context for prior progress and skip completed work instead of re-running it), AGENTS.md gains a § Resuming an interrupted session and progress-checkpoint guidance, and the recon prompt covers attaching to an existing recon session.  Prior 1.21.0 (v2.46.3): concurrent-agent isolation via session-scoped working directories.


def _load_instance_id() -> Optional[str]:
    """Look up the system identity UUID for the provenance block.

    Returns None if the row isn't seeded yet (first boot in progress)
    so callers can render a graceful "(pending)" placeholder.

    Logs at exception level if the lookup fails for any other reason
    (DB unreachable, schema drift, ORM mapping error) so a real
    bootstrap problem surfaces in operator logs instead of being
    silently masked by the same "(pending)" placeholder that a fresh
    install legitimately produces (review C-PR-5).
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError
    try:
        from app.db.session import SessionLocal
        from app.db.models_auth import SystemIdentity
        with SessionLocal() as db:
            row = db.query(SystemIdentity).first()
            return row.instance_id if row else None
    except (OperationalError, ProgrammingError) as exc:
        # Expected during boot before migrations land or if Postgres is
        # briefly unavailable. Log at warning so an operator notices a
        # persistent failure but doesn't get paged for first-boot races.
        logger.warning(
            "system_identity lookup failed (db or schema unavailable); "
            "provenance block will show '(pending)': %s", exc,
        )
        return None
    except Exception:
        # Anything else — ORM mapper drift, programming error in this
        # path — is a real bug we want to see.
        logger.exception(
            "Unexpected error loading system_identity for provenance block; "
            "provenance block will show '(pending)' but check the bootstrap path"
        )
        return None


def build_provenance_block(
    *,
    base_url: str,
    user_label: str,
    user_id: Optional[int],
    action: str,
    target_label: str,
    timestamp_iso: str,
) -> str:
    """Prepend a standardized provenance + identity-check header to any agent prompt.

    This is the trust anchor for hesitant agents.  The block tells the
    agent:

      1. Which BlueStick instance generated this prompt (UUID)
      2. Who authorized the action and when
      3. How to verify once, up front, that the URL they're being
         asked to curl is the same instance the prompt claims
      4. What to do if the check fails

    Verification is a single call to ``/.well-known/networkmapper.json``
    at the start of the session.  Once the check passes, the session
    is trusted for the duration of the agent's API key — the block
    does not tell the agent to re-check on every request.

    See ``_seed_system_identity`` in ``main.py`` for where the
    instance_id is generated, and the ``/.well-known/networkmapper.json``
    endpoint for what the agent will see when it verifies.
    """
    instance_id = _load_instance_id() or "(pending — retry after first boot)"
    origin = base_url.rsplit("/api/v1", 1)[0]
    user_suffix = f" (id {user_id})" if user_id is not None else ""
    return (
        f"## Provenance\n\n"
        f"This prompt was generated by BlueStick instance "
        f"`{instance_id}` at {timestamp_iso}.\n\n"
        f"- **Authorized by:** {user_label}{user_suffix}\n"
        f"- **Action:** {action}\n"
        f"- **Target:** {target_label}\n"
        f"- **Instance base URL:** {origin}\n\n"
        f"### Verify once before starting\n\n"
        f"```\n"
        f"curl -sk {origin}/.well-known/networkmapper.json\n"
        f"```\n\n"
        f"Confirm the response contains `\"instance_id\": \"{instance_id}\"` "
        f"and `\"name\": \"BlueStick\"`. If either does not match, "
        f"**stop** and alert the user — the prompt may have been tampered "
        f"with, copied from a different instance, or served by an unrelated "
        f"host. Once verified, the session is trusted for the duration of "
        f"your API key; you do not need to re-check on every request.\n\n"
        f"---\n\n"
    )


def resolve_base_url(request: Optional[Request]) -> str:
    """Return the externally-reachable ``{origin}/api/v1`` base URL.

    The request host may be an internal Docker hostname that remote agents
    can't reach.  Prefer the first configured CORS origin (which matches
    the user-facing URL), fall back to X-Forwarded-Host, then the request
    host.  Works when ``request`` is ``None`` (e.g. background tasks) by
    falling back to a localhost default.
    """
    cors = settings.CORS_ORIGINS
    if cors:
        origin = cors[0].rstrip("/")
    elif request is not None:
        fwd_host = request.headers.get("x-forwarded-host")
        if fwd_host:
            scheme = request.headers.get("x-forwarded-proto", "https")
            origin = f"{scheme}://{fwd_host}"
        else:
            host_hdr = request.headers.get("host", "localhost:3000")
            scheme = request.url.scheme
            origin = f"{scheme}://{host_hdr}"
    else:
        origin = "https://localhost:3000"

    return f"{origin}/api/v1"


def _feedback_section(base_url: str, source: str, context: Dict[str, Any]) -> str:
    """Standard feedback-request block appended to every agent prompt.

    Agents are asked to POST structured feedback at the end of the
    workflow so developers can track API friction, missing tools, and
    prompt-clarity issues across runs.  ``source`` identifies which
    prompt produced the feedback (``plan_generation`` or
    ``in_session_execution``) and ``context`` carries plan / session
    identifiers the agent should echo back verbatim.
    """
    # Use json.dumps for the value side so future non-int context (str /
    # bool / None) still emits valid JSON; ``repr`` only happens to work
    # while every caller passes int IDs.  Keys stay as plain strings —
    # the existing callers only use ASCII identifiers.
    ctx_lines = "\n".join(f'  "{k}": {json.dumps(v)},' for k, v in context.items())
    return (
        f"\n---\n\n"
        f"## Feedback Requested (required)\n\n"
        f"Before you finish, submit structured feedback so the BlueStick team "
        f"can improve the APIs, prompts, and tool reference. This is **not optional** — "
        f"even a short note helps.\n\n"
        f"`POST {base_url}/agent/feedback`\n\n"
        f"```json\n"
        f"{{\n"
        f'  "source": "{source}",\n'
        f'  "prompt_version": "{PROMPT_VERSION}",\n'
        f"{ctx_lines}\n"
        f'  "overall_rating": 1-5,\n'
        f'  "api_critiques": [\n'
        f'    {{"endpoint": "/agent/...", "issue": "what was missing/awkward/wrong", "suggestion": "..."}}\n'
        f"  ],\n"
        f'  "tool_suggestions": [\n'
        f'    // CLI binaries only (e.g. "naabu", "nuclei", "subfinder").\n'
        f'    // Workflow hints, grouping strategies, or general suggestions\n'
        f'    // belong in `friction_notes`, not here.\n'
        f'    {{"name": "binary-name", "category": "recon|enum|exploit|post|reporting", "rationale": "what this binary does that the catalog is missing"}}\n'
        f"  ],\n"
        f'  "friction_notes": "Free-text: what was confusing, what took extra effort, what you had to guess.",\n'
        f'  "agent_metrics": {{\n'
        f'    "agent_name": "claude-code|codex|chatgpt|other",\n'
        f'    "model": "model id if known (e.g. claude-opus-4-6)",\n'
        f'    "context_window_tokens": 0,\n'
        f'    "context_used_tokens": 0,\n'
        f'    "context_used_pct": 0.0,\n'
        f'    "input_tokens_total": 0,\n'
        f'    "output_tokens_total": 0,\n'
        f'    "cache_read_tokens": 0,\n'
        f'    "cache_write_tokens": 0,\n'
        f'    "tool_calls_total": 0,\n'
        f'    "wall_clock_seconds": 0,\n'
        f'    "notes": "Any other metrics you can report (cost estimate, rate-limit hits, compactions, etc.)"\n'
        f"  }}\n"
        f"}}\n"
        f"```\n\n"
        f"**Metrics guidance:** Report any of the `agent_metrics` fields you can access — "
        f"omit (or set to `null`) anything your environment does not expose. If you cannot "
        f"access *any* metrics, still submit `agent_metrics` with `agent_name` filled in and "
        f"a brief note explaining why. This data is used to compare agents and refine prompts.\n"
    )


def _render_filter_criteria(fc: Dict[str, Any]) -> str:
    """Render the user's host-filter selections as a readable bullet list.

    ``fc`` is the ``filter_criteria`` dict the user assembled in the
    test-plan UI (subnets / ports / services / vuln toggles / min risk
    score / search).  The old prompt just dumped the raw dict repr,
    which was so easy to miss that users reported the filters "did not
    seem to change the provided instructions" at all.
    """
    labels = {
        "subnets": "Subnets",
        "ports": "Ports",
        "services": "Services",
        "min_severity": "Minimum vulnerability severity",
        "has_critical_vulns": "Only hosts with critical vulnerabilities",
        "has_high_vulns": "Only hosts with high vulnerabilities",
        "min_risk_score": "Minimum risk score",
        "search": "Search term",
    }
    lines = []
    for key, val in fc.items():
        label = labels.get(key, key)
        if isinstance(val, bool):
            if val:
                lines.append(f"  - {label}")
        else:
            lines.append(f"  - {label}: `{val}`")
    return "\n".join(lines) if lines else "  - (none)"


def build_plan_generation_instructions(
    *,
    request: Optional[Request],
    plan_id: int,
    plan_title: str,
    raw_api_key: str,
    user_label: str,
    user_id: Optional[int],
    filter_criteria: Optional[Dict[str, Any]] = None,
) -> str:
    """Instructions for an agent populating a newly-created test plan."""
    from datetime import datetime, timezone
    base_url = resolve_base_url(request)
    # v2.9.6: the ``?workflow=`` query parameter returns only the
    # plan-generation + shared sections of AGENTS.md, saving ~1400
    # tokens per fetch vs the full file.  See _slice_agents_md in
    # backend/app/main.py for the filter semantics.
    agents_guide_url = f"{base_url}/agents-guide?workflow=plan_generation"

    provenance = build_provenance_block(
        base_url=base_url,
        user_label=user_label,
        user_id=user_id,
        action="test plan generation",
        target_label=f"test plan #{plan_id} ({plan_title})",
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
    )

    instructions = (
        provenance +
        f"## Agent Instructions\n\n"
        f"You have been assigned to populate a test plan in BlueStick.\n\n"
        f"**Workflow-scoped guide:** {agents_guide_url}\n"
        f"Fetch with `curl -sk '{agents_guide_url}'` for the endpoint schemas, "
        f"entry format, attribution requirements, and examples you need for "
        f"plan generation.  The URL includes a workflow filter so you only "
        f"get the sections relevant to this flow (not the execution docs).\n\n"
        f"**Base URL:** {base_url}/agent\n\n"
        f"**Prompt version:** {PROMPT_VERSION}\n\n"
        f"**Network access:** The API uses HTTPS with a self-signed certificate. "
        f"All `curl` commands require the `-sk` flags (silent + insecure) to skip "
        f"certificate verification. If curl to this URL fails due to sandbox "
        f"restrictions, ask the user to run the command for you or to provide an "
        f"alternate reachable URL.\n\n"
        f"**Authentication (include on every request):**\n"
        f"```\n"
        f"X-API-Key: {raw_api_key}\n"
        f"```\n\n"
        f"**Your Task (complete all steps without stopping):**\n"
        f"1. `GET {base_url}/agent/test-plans/{plan_id}/context`\n"
        f"   Fetch candidate hosts. **This endpoint is paginated** — it returns at "
        f"most `limit` hosts per call (default 500). When `summary.has_more` is "
        f"true there are more candidates: call it again with "
        f"`?after_host_id={{summary.next_cursor}}` and keep paging until "
        f"`has_more` is false. `has_more: true` means *fetch the next page* — it "
        f"is NOT a cap holding hosts back. You are responsible for **every** "
        f"candidate host across all pages, not just the first 500. A page can "
        f"legitimately return zero `candidate_hosts` while the prior page's "
        f"`has_more` was true (boundary case when the previous page was exactly "
        f"`limit`) — treat an empty page as *done*, not an error. Report a brief "
        f"summary to the user (total host count, vuln breakdown), then "
        f"**continue automatically** to step 2.\n"
        f"2. `PATCH {base_url}/agent/test-plans/{plan_id}`\n"
        f"   Set the plan description summarizing your scope, methodology, and "
        f"prioritization approach.  **Also stamp your identity** so the audit "
        f"trail records which agent populated the plan: include "
        f"`generated_by_model` (e.g. `claude-opus-4-7`), `generated_by_tool` "
        f"(e.g. `claude-code`, `codex`, `chatgpt`), and `prompt_version` "
        f'(echo back `"{PROMPT_VERSION}"` from this prompt).  These fields '
        f"are write-once; a later PATCH editing only the description will "
        f"not overwrite them.\n"
        f"3. `POST {base_url}/agent/test-plans/{plan_id}/entries`\n"
        f"   Add structured test entries for candidate hosts.  The context "
        f"response (step 1) carries `entry_template`, `entry_batch_example`, "
        f"and `entry_schema` — pattern-match on them directly instead of "
        f"inferring the request shape from these instructions.  Body is the "
        f"`entry_batch_example` shape (`{{\"entries\": [...]}}`), up to 500 "
        f"entries per call. If you selected more than 500 hosts across the paged "
        f"context, POST in **multiple batches of ≤500** until every selected host "
        f"has an entry.\n"
        f"4. `GET {base_url}/agent/test-plans/{plan_id}/validate`\n"
        f"   Dry-run validation — check warnings before submitting.\n"
        f"5. `POST {base_url}/agent/test-plans/{plan_id}/submit`\n"
        f"   Submit the plan for human review.\n\n"
        f"**Selection policy:** Create entries for all hosts with critical or high "
        f"vulnerabilities. Include medium-vuln hosts if they expose multiple services "
        f"or high-value ports (SMB, RDP, databases). Skip hosts with zero open ports.\n\n"
        f"**Describe tests as INTENT, not commands (v2.23.0).**  Each `proposed_tests` "
        f"entry should state *what is being verified* and *what evidence proves it* — "
        f"e.g. \"enumerate SMB sessions; evidence is a list of sessions or an explicit "
        f"`access denied`.\"  The agent who executes the plan probes its own host "
        f"environment and translates each intent into the right command flavour "
        f"(`enum4linux -S {{ip}}` from Kali vs `Get-SmbSession` inline-PowerShell on "
        f"Windows).  A `command` field is welcome as a *suggestion* but the executing "
        f"agent is authoritative — do not assume the executor shares the recon "
        f"environment (a Kali recon may be followed by a Windows execution by a "
        f"different operator).\n\n"
        f"**Build on recon — don't re-discover.**  These hosts have already been "
        f"scanned: each candidate's open ports, services, and versions are in "
        f"`candidate_hosts[].ports` / `.services` (recon records a service-version "
        f"scan for every live host). Propose **targeted validation/exploitation** that "
        f"*consumes* that data — validate THIS finding, exercise THIS service on the "
        f"KNOWN open ports. Do **not** propose discovery or service-version scans "
        f"(`nmap -sn`, `nmap -sV`, full-port sweeps) as tests: recon already ran them, "
        f"so re-running is wasted work and IDS noise, not a test. A host with no vulns "
        f"and nothing left to validate doesn't need an entry.\n\n"
        f"**Plan ID:** {plan_id}\n"
        f"**Plan Title:** {plan_title}\n"
    )
    if filter_criteria:
        instructions += (
            f"\n**Host filters applied by the user:**\n"
            f"{_render_filter_criteria(filter_criteria)}\n\n"
            f"The candidate host list returned by `GET .../test-plans/{plan_id}/context` "
            f"in step 1 is **already narrowed to these filters** — you do not need to "
            f"re-apply them yourself. The `summary.matching_filter` field in that "
            f"response tells you how many hosts matched. Apply the selection policy "
            f"above *on top of* this pre-filtered set: the filters decide which hosts "
            f"are candidates, the selection policy decides which candidates become test "
            f"entries. If the filtered set is empty or much smaller than expected, "
            f"report that to the user rather than widening the scope on your own.\n"
        )

    instructions += _feedback_section(
        base_url=base_url,
        source="plan_generation",
        context={"test_plan_id": plan_id},
    )
    return instructions


def _integration_block(integrations: list) -> str:
    """Render a list of decrypted integration credentials as a prompt section.

    ``integrations`` is a list of dicts produced by
    ``integration_service.decrypt_integration``.  The secrets are
    inlined in plaintext so a terminal-side agent (Claude Code, Codex,
    manual operator) can use them directly without a second round trip.

    Security boundary (enforced, not assumed):
      - The frontend ``InAppAgentPanel`` MUST call
        ``sanitizePromptForLlm`` (see
        ``frontend/src/utils/promptSanitizer.ts``) before POSTing any
        prompt derived from this block to a hosted LLM provider.  That
        sanitizer strips the agent API key line and replaces inlined
        credentials on every bullet labelled ``Access key / Secret key
        / Password / Username / API key / PDCP token / Secret`` with a
        ``[REDACTED]`` marker.
      - The redaction only runs on the in-app LLM path.  The copy-paste
        flow that shows the user the full instructions block (for them
        to hand to an out-of-band terminal agent) intentionally still
        contains the plaintext — the user is the one pasting it, they
        already authorized the exposure by creating the integration.
      - Changing the ``  - <Label>:`` bullet shape below without
        updating ``promptSanitizer.ts`` to match WILL leak secrets to
        whichever LLM provider is configured.  Keep the two in sync.
    """
    if not integrations:
        return (
            "\n### Credentialed scanners available\n"
            "No integration credentials are configured for this project. "
            "If you think a vulnerability scanner, template runner, or "
            "web-app scanner would help, ask the user to configure one "
            "at **Scanner Integrations** in the sidebar and resume.\n"
        )
    lines = ["\n### Credentialed scanners available\n"]
    lines.append(
        "The user has configured the following scanner credentials. "
        "Use them **only** against in-scope targets, and always show the "
        "exact invocation to the user before running it.\n"
    )
    for i in integrations:
        itype = i.get("integration_type", "generic_api")
        name = i.get("name") or "(unnamed)"
        base = i.get("base_url") or "(no base URL)"
        if itype == "nessus":
            # v2.49.4 — operator can set `extra_config.max_hosts_per_scan`
            # when creating the integration (their Nessus license cap).
            # Surface it so the agent splits big scopes into multiple
            # Nessus scans instead of submitting one oversize scan that
            # Nessus rejects or truncates.
            extra = i.get("extra_config") or {}
            max_hosts = extra.get("max_hosts_per_scan") if isinstance(extra, dict) else None
            chunking_note = (
                f"    **License cap**: this Nessus install is configured "
                f"with `max_hosts_per_scan = {max_hosts}`.  If a target "
                f"chunk you'd submit exceeds that, split into multiple "
                f"Nessus scans of {max_hosts} hosts each — run them "
                f"sequentially (Nessus serializes on the license) and "
                f"tag each scan name with `<n_of_m>` so the operator "
                f"can correlate.\n"
                if max_hosts
                else (
                    f"    **License cap**: no `max_hosts_per_scan` was "
                    f"configured on this integration.  If you discover "
                    f"Nessus refuses to start a scan past a certain "
                    f"size, ask the operator for the per-scan host "
                    f"limit (typically 256 / 512 / 1024 on Nessus Pro) "
                    f"and chunk to that.\n"
                )
            )
            lines.append(
                f"- **Nessus — `{name}`**\n"
                f"  - URL: `{base}`\n"
                f"  - Access key: `{i.get('secret') or '(missing)'}`\n"
                f"  - Secret key: `{i.get('secret2') or '(missing)'}`\n"
                f"  - Guidance: launch a policy scan via the Nessus REST API "
                f"    against the target list, poll for completion, pull the "
                f"    report as CSV or .nessus, and feed findings into your "
                f"    test-plan entries.\n"
                f"{chunking_note}"
            )
        elif itype == "openvas":
            lines.append(
                f"- **OpenVAS / GVM — `{name}`**\n"
                f"  - URL: `{base}`\n"
                f"  - Username: `{i.get('secret') or '(missing)'}`\n"
                f"  - Password: `{i.get('secret2') or '(missing)'}`\n"
                f"  - Guidance: use `gvm-tools` or the GMP API to create a "
                f"    target + task against in-scope subnets, kick it off, "
                f"    and parse the XML report when complete.\n"
            )
        elif itype == "nuclei":
            lines.append(
                f"- **Nuclei — `{name}`**\n"
                f"  - PDCP token: `{i.get('secret') or '(not set)'}`\n"
                f"  - Guidance: run nuclei against identified web services "
                f"    with the default template set; consider `-severity critical,high` "
                f"    for an initial pass. If the PDCP token is set, enable "
                f"    cloud templates.\n"
            )
        elif itype == "burp":
            lines.append(
                f"- **Burp — `{name}`**\n"
                f"  - URL: `{base}`\n"
                f"  - API key: `{i.get('secret') or '(missing)'}`\n"
                f"  - Guidance: use the Burp Enterprise / Professional REST "
                f"    API to launch scans against any web targets you discover.\n"
            )
        else:
            lines.append(
                f"- **{itype} — `{name}`**\n"
                f"  - URL: `{base}`\n"
                f"  - Secret: `{i.get('secret') or '(not set)'}`\n"
                f"  - Guidance: ask the user for the exact invocation if the "
                f"    tool's usage isn't obvious from its name.\n"
            )
    lines.append(
        "\nWhen results are available, record them as test-plan entries "
        "(severity-scored) so the human reviewer sees the same data as "
        "your manual findings. Do not blindly dump raw scanner output — "
        "summarize per host.\n"
    )
    return "".join(lines)


def build_recon_ingest_instructions(
    *,
    request: Optional[Request],
    recon_session_id: int,
    scope_id: int,
    scope_name: str,
    subnets: list,                 # list of CIDR strings
    raw_api_key: str,
    user_label: str,
    user_id: Optional[int],
    integrations: Optional[list] = None,  # decrypted integration dicts, optional
    resumed: bool = False,
    project_slug: str = "default",
) -> str:
    """Instructions for an agent performing reconnaissance against a scope.

    ``resumed=True`` prepends a RESUMED-SESSION notice — used when an
    interrupted recon session is re-issued via the recon resume
    endpoint, so the agent reads prior progress from
    ``/agent/recon/summary`` instead of repeating coverage.

    v2.11.0 — complete rewrite.  The previous implementation told the
    agent to create TestPlanEntry rows from recon findings, which was
    backwards: recon's job is to **populate host data** (via the
    existing ingestion pipeline), not to build a list of things-to-test
    against host data that doesn't exist yet.  Test plans come after
    recon, as a separate workflow the user explicitly triggers.

    The new workflow:

      1. Agent verifies provenance (one curl against /.well-known/).
      2. Agent fetches scope context (CIDRs + already-known hosts +
         tool-catalog suggestions).
      3. Agent proposes a tool command to the user, gets approval,
         runs the tool locally, saves output to a file.
      4. Agent POSTs the file to /agent/recon/upload which wraps the
         existing upload pipeline — every parser we already support
         works automatically (nmap XML, masscan, gnmap, nessus, etc).
      5. Agent polls /agent/recon/jobs/{id} until the parse completes.
      6. Agent GETs /agent/recon/summary for the rolling host/port
         count and decides what to run next.
      7. Agent repeats 3–6 until satisfied, then POSTs /complete.
      8. Submits structured feedback.

    Results land in the same scans/hosts/ports tables as human uploads,
    deduped against existing scan data, correlated to the scope's
    subnets, and enriched with any configured vulnerability sources.
    """
    from datetime import datetime, timezone
    base_url = resolve_base_url(request)
    agents_guide_url = f"{base_url}/agents-guide?workflow=reconnaissance"

    # v2.45.4 — cap the inline subnet list.  A scope with thousands of
    # CIDRs previously emitted one prompt line each, ballooning the
    # prompt past the agent's context window before any work began.
    # Beyond the cap, render a summary + a pointer to the paginated
    # endpoint (GET /agent/recon/subnets) that returns the authoritative
    # full list.  The agent's /recon/context call also carries
    # scope_size + a bounded sample for the same reason.
    _SUBNET_INLINE_CAP = 25
    if not subnets:
        scope_list = "  (none registered yet)"
    elif len(subnets) <= _SUBNET_INLINE_CAP:
        scope_list = "\n".join(f"  - `{c}`" for c in subnets)
    else:
        shown = "\n".join(f"  - `{c}`" for c in subnets[:_SUBNET_INLINE_CAP])
        scope_list = (
            f"{shown}\n"
            f"  - … and {len(subnets) - _SUBNET_INLINE_CAP} more "
            f"({len(subnets)} subnets total)\n\n"
            f"  **This scope is large — the list above is truncated.**  Fetch the\n"
            f"  authoritative, complete subnet list from the paginated endpoint:\n"
            f"  `GET {base_url}/agent/recon/subnets?offset=0&limit=500`\n"
            f"  (walk `offset` until the response's `subnets` array is empty).\n"
            f"  Do NOT assume the whole scope fits in one discovery pass — see\n"
            f"  the guide's § Scope-size awareness and work in batches."
        )

    provenance = build_provenance_block(
        base_url=base_url,
        user_label=user_label,
        user_id=user_id,
        action="reconnaissance (host discovery + ingest)",
        target_label=f"scope #{scope_id} ({scope_name}); recon session #{recon_session_id}",
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
    )

    # v2.47.0 — recon session re-issued via the resume endpoint after an
    # interruption: steer the agent to read prior progress before scanning.
    resume_notice = ""
    if resumed:
        resume_notice = (
            "> **⟳ RESUMED RECON SESSION.** This recon session was "
            "interrupted and resumed with a fresh API key. Prior uploads "
            "are already ingested — `GET /agent/recon/summary` returns the "
            "rolling host/port counts and `GET /agent/recon/context` lists "
            "already-known hosts. Read both before scanning so you continue "
            "coverage rather than repeating it (re-uploading a duplicate "
            "scan is harmless — ingestion dedupes — but wastes a pass). "
            "The environment probe (step 1) is still required for the new "
            "key.\n\n"
        )

    instructions = (
        provenance +
        resume_notice +
        f"## Agent Reconnaissance Instructions\n\n"
        f"Your job: populate BlueStick's host database for this authorized scope. "
        f"Run scanners locally, upload raw output, iterate. A human reviews findings and "
        f"generates the test plan separately — **you do not build test plans here**.\n\n"
        f"**Scope:** {scope_name} (id {scope_id}) · **Recon session:** #{recon_session_id}\n"
        f"**Subnets:**\n{scope_list}\n\n"
        f"**Base URL:** {base_url}/agent · **Prompt version:** {PROMPT_VERSION}\n"
        f"**Auth header (every request):** `X-API-Key: {raw_api_key}`\n"
        f"Self-signed cert — `curl` always needs `-sk`. If curl is blocked by your "
        f"sandbox, ask the user to run it for you.\n\n"
        f"### Read the guide first — it is binding\n\n"
        f"```\n"
        f"curl -sk '{agents_guide_url}'\n"
        f"```\n\n"
        f"The guide is the authoritative reference for the safety/approval protocol, "
        f"environment-probe body shape, scope-size sequencing, tool catalog, preflight "
        f"script, supported upload formats, exit criteria, and the feedback schema. The "
        f"checklist below is a *skeleton* — fetch the guide for every field shape, body "
        f"format, and how-to. Do not improvise from the checklist alone.\n\n"
        f"**Hard stops the guide does not negotiate:** out-of-scope targets are refused "
        f"(not approval-asked); intrusive tools (`intrusive: true` in the catalog) ask "
        f"per-command and never batch under plan-level approval.\n\n"
        f"### Task checklist\n\n"
        f"1. **Probe the environment** — `POST {base_url}/agent/recon/sessions/{recon_session_id}/environment` "
        f"(body shape: guide § Environment probe). Include `agent_model`, `agent_tool`, "
        f"`agent_prompt_version: \"{PROMPT_VERSION}\"`.\n"
        f"2. **Run preflight, re-post the probe with `tools_status[]`, then fetch context** — "
        f"fetch `{base_url}/references/preflight-script` (currently a bash script — "
        f"if your environment is PowerShell-only with no WSL, fetch it with "
        f"`curl -sk <url> -o preflight.sh`, inspect, and produce the equivalent "
        f"`tools_status` payload by hand via `Get-Command` checks for each tool). "
        f"In bash-capable environments the canonical invocation is "
        f"`curl -sk {base_url}/references/preflight-script | bash -s -- --json`. "
        f"Re-POST Step 1's endpoint with the preflight tools list folded in as "
        f"`tools_status` (shape: guide § Environment probe), then "
        f"`GET {base_url}/agent/recon/context` for the env-adapted `recommended_sequence`. "
        f"Report the preflight summary to the user *before* presenting any tool command.\n"
        f"3. **Present `recommended_sequence` to the user, get plan-level approval** — "
        f"per the guide's approval protocol. Call out any `swap_reason` entries so the "
        f"user knows which steps deviated from the canonical plan and why. Default to "
        f"comprehensive discovery; `known_hosts_probe` is opt-in only when the user "
        f"explicitly asks for the narrow alternative.\n"
        f"4. **Execute, upload, poll, summarize, iterate.** "
        f"**First, before running any tool**, create a session-scoped working "
        f"directory and `cd` into it — "
        f"`mkdir -p networkmapper-{project_slug}-recon-{recon_session_id} "
        f"&& cd networkmapper-{project_slug}-recon-{recon_session_id}` — and run "
        f"every tool from there. The path includes the project slug so two "
        f"projects working out of the same parent directory get distinct folders "
        f"(`networkmapper-homenetwork-recon-1` vs `networkmapper-clienta-recon-1`) "
        f"and a Nuclear-Clean reset doesn't collide with a leftover.  "
        f"One operator may run two agents at once; a shared working dir means "
        f"colliding output files and a process table you can't tell apart. See the "
        f"guide's § Working directory & concurrent agents. Then: run the tool locally with "
        f"machine-readable output; `POST {base_url}/agent/recon/upload` (multipart, "
        f"include `tool_name` and `command_run`); poll "
        f"`{base_url}/agent/recon/jobs/{{job_id}}` until terminal; verify with "
        f"`GET {base_url}/agent/recon/summary` (authoritative). Repeat per the guide's "
        f"§ Phases (discovery → service probe of live hosts → web → deep-dives). "
        f"**Chunk large scopes** — do NOT scan a multi-thousand-host scope in one pass "
        f"and upload one giant file. Scan and upload in batches (~256–1024 addresses / "
        f"~25–50 CIDRs each); see the guide's § Very large scopes for why and how.\n"
        f"5. **Complete the session — MANDATORY, this is the LAST thing you do.** "
        f"`POST {base_url}/agent/recon/complete` with `{{\"notes\": \"...\"}}`. "
        f"**The recon session stays `active` in the operator's UI until this call "
        f"returns 200** — uploading scans and submitting feedback do NOT complete it. "
        f"A run that scanned, uploaded, and submitted feedback but skipped this call "
        f"looks unfinished to the operator forever. Order of your final actions: "
        f"finish all uploads → submit feedback (below) → `POST /agent/recon/complete` "
        f"LAST. Confirm you received the 200 before you consider the run done.\n"
    )

    instructions += _integration_block(integrations or [])

    # v2.85.0 — drop scope_id from the rendered template.  The
    # AgentFeedback schema only carries recon_session_id (linkable to a
    # scope by joining ReconSession.scope_id when needed); pre-v2.85.0
    # the prompt invited the agent to send scope_id and Pydantic
    # silently dropped it on receive, which made the feedback row's
    # workflow attribution lossy.
    instructions += _feedback_section(
        base_url=base_url,
        source="reconnaissance",
        context={"recon_session_id": recon_session_id},
    )
    return instructions


def build_assist_instructions(
    *,
    request: Optional[Request],
    assist_session_id: int,
    project_id: int,
    project_name: str,
    purpose: Optional[str],
    raw_api_key: str,
    user_label: str,
    user_id: Optional[int],
) -> str:
    """Instructions for an agent in an interactive assist session.

    Assist sessions are read-only.  No scanning, no test plan
    creation, no host follow mutation, no execution.  The agent's
    job is to answer the operator's questions by querying BlueStick
    and synthesizing — not by acting on the data.

    Shorter prompt than recon/plan/execution because the surface is
    smaller (six endpoints) and the safety surface is trivial (no
    target traffic, no writes).  The biggest risk to flag is "the
    agent decides to run a scan locally to answer a question" — the
    operator has not consented to that and the assist key wouldn't
    accept the upload anyway.  Steer the agent to ask the operator
    to open a recon session if scanning is the right next step.
    """
    from datetime import datetime, timezone
    base_url = resolve_base_url(request)
    agents_guide_url = f"{base_url}/agents-guide?workflow=assist"

    provenance = build_provenance_block(
        base_url=base_url,
        user_label=user_label,
        user_id=user_id,
        action="interactive assist (read-only project query)",
        target_label=f"project #{project_id} ({project_name}); assist session #{assist_session_id}",
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
    )

    purpose_line = (
        f"**The operator stated this purpose:** {purpose.strip()}\n\n"
        if purpose and purpose.strip()
        else ""
    )

    instructions = (
        provenance +
        f"## Agent Interactive Assist Instructions\n\n"
        f"You are in a **read-only** assist session against project "
        f"**{project_name}** (id {project_id}).  Your job is to help the "
        f"operator query their project — answer questions about hosts, "
        f"summarize findings, surface relevant scans — by hitting "
        f"BlueStick's `/agent/assist/*` endpoints and synthesizing the "
        f"results.  Do **not** run scans, do **not** create test plans, "
        f"do **not** mutate follow status.  This session has no "
        f"authority to do any of those things — the API will refuse.\n\n"
        f"{purpose_line}"
        f"**Assist session:** #{assist_session_id} · **Project:** {project_name}\n"
        f"**Base URL:** {base_url}/agent · **Prompt version:** {PROMPT_VERSION}\n"
        f"**Auth header (every request):** `X-API-Key: {raw_api_key}`\n"
        f"Self-signed cert — every request must skip TLS verification.\n\n"
        f"### Invocation — assist runs on any OS\n\n"
        f"Your \"commands\" here are HTTPS API calls, not shell tools, so this "
        f"session works the same on **Windows, macOS, and Linux** — only the "
        f"way you invoke the HTTP client differs. Use the form for *your* shell:\n"
        f"- **bash / zsh** (Linux, macOS): `curl -sk -H 'X-API-Key: {raw_api_key}' '<url>'`\n"
        f"- **Windows PowerShell:** use **`curl.exe`** — bare `curl` is an alias "
        f"for `Invoke-WebRequest` and will NOT accept these flags. "
        f"`curl.exe -sk -H \"X-API-Key: {raw_api_key}\" \"<url>\"`, or native "
        f"`Invoke-RestMethod -SkipCertificateCheck -Headers @{{'X-API-Key'='{raw_api_key}'}} '<url>'`.\n"
        f"- **POST bodies:** single-quoted JSON (`-d '{{...}}'`) is a bash/zsh "
        f"idiom; in PowerShell pass `-d (ConvertTo-Json $obj)` to `curl.exe` or "
        f"`-Body ($obj | ConvertTo-Json)` to `Invoke-RestMethod`.\n\n"
        f"### Read the guide first\n\n"
        f"bash/zsh: `curl -sk '{agents_guide_url}'`  ·  "
        f"PowerShell: `curl.exe -sk \"{agents_guide_url}\"`\n\n"
        f"### Task checklist\n\n"
        f"1. **Probe the environment** — "
        f"`POST {base_url}/agent/assist/sessions/{assist_session_id}/environment`. "
        f"For assist, report just `os_family` (`windows` / `darwin` / `linux`) "
        f"and `shell` — the recon/execution tool-inventory + preflight flow does "
        f"**not** apply here (your commands are API calls, not scanner tools). "
        f"Include `agent_model`, `agent_tool`, "
        f"`agent_prompt_version: \"{PROMPT_VERSION}\"` for audit symmetry.\n"
        f"2. **Fetch project context** — "
        f"`GET {base_url}/agent/assist/context`.  This is a HEADLINE summary, "
        f"not a full inventory: the `scopes` list is capped at 50 (check "
        f"`scopes_truncated` — if true, call `/agent/assist/scopes` for the "
        f"rest) and `recent_scans`/`recent_recon` are capped at 5 each.  For "
        f"real counts read the `totals` block; never answer 'how many "
        f"scopes/scans/hosts' from the truncated lists.  Read this before "
        f"answering any operator question so your synthesis is grounded.\n"
        f"3. **Answer the operator's question.**  Use:\n"
        f"   - `GET /agent/assist/hosts?…` (filter shape mirrors the "
        f"`/hosts` page: `state`, `ports`, `services`, `subnets`, "
        f"`has_critical_vulns`, `has_high_vulns`, `min_risk_score`, "
        f"`search`).  **Paginated and capped**: it returns a bare array of at "
        f"most `limit` hosts (default 500, max 5000) with NO `total`/`has_more` "
        f"signal.  To answer any count/coverage question you MUST page — start "
        f"at `offset=0` and re-request with `offset += limit` until a page "
        f"returns fewer than `limit` rows.  Never report a host count from a "
        f"single page; a project can have tens of thousands of hosts.\n"
        f"   - `GET /agent/assist/hosts/{{host_id}}` for full open-port "
        f"detail on one host (returns the ENTIRE port list — large for hosts "
        f"with many open ports; prefer `open_port_count` from the list for "
        f"triage).\n"
        f"   - `GET /agent/assist/scopes` for scope CIDR lists (each scope's "
        f"CIDR list is capped at 100 subnets, silently — if a scope may have "
        f"more, say the list is partial; full enumeration needs a recon "
        f"session).\n"
        f"   - `GET /agent/assist/scans` for the recent scan inventory "
        f"(default 100, max 500, newest-first, NO offset — you cannot page "
        f"past the most recent 500; qualify 'all scans' answers accordingly).\n"
        f"4. **When you suggest a follow-up that requires action, hand it "
        f"back to the operator** — never act on their behalf.  Examples:\n"
        f"   - \"You have 12 hosts exposing FTP — want me to draft a "
        f"recon plan for deeper service detection?  Open a recon "
        f"session against the matching scope to proceed.\"\n"
        f"   - \"Several critical CVEs landed in last week's scan — open "
        f"a test plan from the Test Plans page to act on them.\"\n"
        f"   The operator drives execution; you assist their query.\n\n"
        f"### What this session can NOT do\n\n"
        f"- **Cannot upload scans** — that's the recon workflow.  If "
        f"the operator asks you to scan, tell them you'd need a recon "
        f"session minted from the Scopes page.\n"
        f"- **Cannot create or execute test plans** — point them at the "
        f"Test Plans page UI.\n"
        f"- **Cannot create notes or change host follow status** — those "
        f"are writes; v1 of assist is strictly read-only.\n\n"
        f"### Tone\n\n"
        f"You are a research partner, not an autonomous agent.  Keep "
        f"responses concise, ground every claim in a specific endpoint "
        f"+ filter you called, and flag uncertainty rather than guess. "
        f"The operator may pivot mid-session ('actually, just show me "
        f"the up hosts'); roll with it.\n"
    )

    # v2.85.0 — assist now closes with the same feedback block as the
    # other workflows.  AgentFeedbackSource gained an ASSIST value and
    # AgentFeedback gained an assist_session_id column so the row links
    # back to the conversation it came from.  Assist is interactive so
    # the "before you finish" moment is whenever the operator releases
    # the agent — the prompt frames it accordingly.
    instructions += _feedback_section(
        base_url=base_url,
        source="assist",
        context={"assist_session_id": assist_session_id},
    )
    return instructions


def build_execution_instructions(
    *,
    request: Optional[Request],
    plan_id: int,
    plan_title: str,
    session_id: int,
    entry_count: int,
    raw_api_key: str,
    user_label: str,
    user_id: Optional[int],
    resumed: bool = False,
    project_slug: str = "default",
) -> str:
    """Instructions for an agent executing an approved test plan.

    Covers the mandatory safety protocol: per-host sanity check, per-test
    human approval, and result recording.  Mirrors the legacy inline block
    that used to live in ``test_plans.execute_test_plan``.

    ``resumed=True`` prepends a RESUMED-SESSION notice — used when the
    session is re-issued via the resume endpoint after an interruption,
    so the agent reads prior progress from ``/execution-context`` and
    continues instead of re-running completed work.
    """
    from datetime import datetime, timezone
    base_url = resolve_base_url(request)
    # v2.9.6: ``?workflow=execution`` filters AGENTS.md to only the
    # execution workflow + shared sections, saving ~2100 tokens per
    # fetch vs the full file.  The biggest win of the three slices
    # because it drops all plan-generation content.
    agents_guide_url = f"{base_url}/agents-guide?workflow=execution"

    provenance = build_provenance_block(
        base_url=base_url,
        user_label=user_label,
        user_id=user_id,
        action=f"test plan execution ({entry_count} entries)",
        target_label=f"test plan #{plan_id} ({plan_title}); execution session #{session_id}",
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
    )

    # v2.47.0 — when the session is re-issued via the resume endpoint
    # after an interruption, steer the agent to read prior progress
    # rather than re-running completed tests.
    resume_notice = ""
    if resumed:
        resume_notice = (
            "> **⟳ RESUMED SESSION.** This execution session was interrupted "
            "and has been resumed with a fresh API key — it already holds "
            "partial progress. Step 1 (`/execution-context`) reports per-host "
            "`entry_status` and per-test `result_status`: treat any entry "
            "already `completed` and any test already `executed` / `skipped` "
            "as DONE. Do **not** re-run completed tests or re-sanity-check "
            "hosts that already passed — resume at the first host with "
            "outstanding work. Step 0 (environment probe) is still required: "
            "your new key needs a fresh probe, but all prior results are "
            "intact and must be preserved.\n\n"
        )

    instructions = (
        provenance +
        f"## Agent Execution Instructions\n\n"
        f"You are executing approved tests from a test plan in BlueStick.\n\n"
        f"**SAFETY RULES (mandatory — do not skip):**\n"
        f"1. **NEVER run a command without showing it to the user and getting explicit approval.**\n"
        f"2. Before testing each host, perform a **sanity check** to verify you are reaching the intended target.\n"
        f"3. If a sanity check fails, **STOP and ask the user** for guidance. Do not proceed.\n"
        f"4. Record all results to the API as you go.\n\n"
        f"**Workflow-scoped guide:** {agents_guide_url}\n"
        f"Fetch with `curl -sk '{agents_guide_url}'` for the execution "
        f"slice of AGENTS.md (sanity check methods, test result status "
        f"values, execution context shape, raw output storage). The URL "
        f"includes a workflow filter so plan-generation sections are omitted.\n\n"
        f"**Prompt version:** {PROMPT_VERSION}\n\n"
        f"**Network access:** The API uses HTTPS with a self-signed certificate, so\n"
        f"every API call must skip TLS verification.  These BlueStick API calls are\n"
        f"identical across OSes; only the HTTP-client invocation differs:\n"
        f"- **bash / zsh** (Linux): `curl -sk -H 'X-API-Key: ...' '<url>'`\n"
        f"- **Windows PowerShell:** use **`curl.exe`** — bare `curl` is an alias for\n"
        f"  `Invoke-WebRequest` and will NOT accept these flags: "
        f"`curl.exe -sk -H \"X-API-Key: ...\" \"<url>\"`, or native "
        f"`Invoke-RestMethod -SkipCertificateCheck`.  For POST bodies (the probe,\n"
        f"  result recording), pass `-d (ConvertTo-Json $obj)` to `curl.exe` or\n"
        f"  `-Body ($obj | ConvertTo-Json)` to `Invoke-RestMethod` instead of the\n"
        f"  bash single-quoted-JSON shown in the examples below.\n\n"
        f"**Authentication (include on every request):**\n"
        f"```\n"
        f"X-API-Key: {raw_api_key}\n"
        f"```\n\n"
        + resume_notice
        + "**Your Task (complete all steps, in order):**\n\n"
        f"### Step 0 — Probe the environment (v2.23.0, MANDATORY before any other action)\n"
        f"Run a short capability check on the operator's host and POST the result to:\n"
        f"`POST {base_url}/agent/execution-sessions/{{session_id}}/environment`\n"
        f"(see AGENTS.md § Environment probe for the exact body shape).\n\n"
        f"The plan describes test *intent*; this probe is how you learn what's actually\n"
        f"available on this user's host so you can translate intent into the right command\n"
        f"flavour at run time. Two operators on the same project (Windows + RemoteSigned\n"
        f"vs Kali Linux) need different commands for the same intent — the probe is what\n"
        f"makes that decision well-grounded instead of guessed.\n\n"
        f"**v2.28.0 — also include your identity** in the probe body so users can compare\n"
        f"results from different agents and models running against the same plan:\n"
        f"```\n"
        f"{{ ...environment fields,\n"
        f'   "agent_model": "<your model id, e.g. claude-opus-4-7 or gpt-5-codex>",\n'
        f'   "agent_tool": "<your harness, e.g. claude-code, codex, chatgpt, manual-curl>",\n'
        f'   "agent_prompt_version": "{PROMPT_VERSION}" }}\n'
        f"```\n"
        f"These persist to dedicated columns on the execution session row and surface in\n"
        f"the UI's session picker so a human can pick which run to review.\n\n"
        f"Then the `session_id` is the one returned by /execution-context in step 1.\n"
        f"Subsequent /execution-context responses echo your probe under `environment`, so\n"
        f"you don't have to re-send it — just read it from the next context call.\n\n"
        f"### Step 1 — Fetch execution context\n"
        f"`GET {base_url}/agent/test-plans/{plan_id}/execution-context`\n\n"
        f"Review the hosts and tests. Report to the user:\n"
        f'"Plan has N entries covering N hosts. I will test them in priority order '
        f'(critical first). Each test requires your approval before I run it."\n\n'
        f"Use the `environment` block in the response when translating each test entry's\n"
        f"intent into a concrete command (POSIX vs PowerShell flavour, etc.). When you\n"
        f"record the test result, put the *actual command you ran* in `command_run` so\n"
        f"the audit trail reflects the on-host translation, not just the plan's intent.\n\n"
        f"**The host's open ports/services are already known.** `known_services[]` on\n"
        f"each entry is the authoritative set recon recorded (open port, product,\n"
        f"version). Target every test at those known ports — do NOT re-discover or\n"
        f"re-sweep. If a proposed test resolves to a broad or full-port `nmap` discovery\n"
        f"scan (`-sV`, `-p-`, `--top-ports …` across the host), narrow it to the known\n"
        f"port(s) or skip it as redundant: recon already ran that scan. The per-host\n"
        f"sanity check (step 2a) is single-port *verification* of recorded data, not a\n"
        f"re-scan.\n\n"
        f"### Step 1.5 — Create a session working directory (before running any tool)\n"
        f"`mkdir -p networkmapper-{project_slug}-execution-{session_id} && "
        f"cd networkmapper-{project_slug}-execution-{session_id}` and run every "
        f"command from there. The path includes the project slug so two projects "
        f"working out of the same parent directory get distinct folders and a "
        f"Nuclear-Clean reset can't collide with a leftover. One operator may "
        f"run two agents at once (another execution, a recon run); a shared "
        f"working directory means colliding output files and a process table "
        f"you can't tell apart. See AGENTS.md § Working directory & concurrent "
        f"agents.\n\n"
        f"### Step 2 — For each host (in priority order)\n\n"
        f"#### 2a. Sanity check\n"
        f"Before running ANY tests on a host, verify the target:\n"
        f"- Report your **source IP, default gateway, and DNS server** so the user can confirm the network context.\n"
        f"- Run `dig -x {{ip}}` (or `nslookup {{ip}}`) and compare to the expected hostname from context.\n"
        f"- Banner-grab a known open port from context (e.g., `nc -w 3 {{ip}} {{port}}`) and compare the service.\n"
        f"- Report results to the user. Record the check:\n"
        f"  `POST {base_url}/agent/test-plans/{plan_id}/entries/{{entry_id}}/sanity-check`\n"
        f"- **If any check fails or looks suspicious, STOP and ask the user.**\n\n"
        f"#### 2b. Execute tests (one at a time, with approval)\n"
        f"For each test in the entry:\n\n"
        f"1. **PRESENT the command to the user:**\n"
        f'   "Test {{i}}/{{total}} for {{ip}} ({{hostname}}):\n'
        f"   Tool: {{tool}}\n"
        f"   Purpose: {{description}}\n"
        f"   Command: `{{command}}`\n"
        f"   Summary: a plain-language breakdown of what the command actually does —\n"
        f"   name each flag/option and what it changes, and call out anything\n"
        f"   intrusive, long-running, or that writes files. Keep it to 1–3 sentences.\n"
        f"   This is **mandatory for every command** and especially important for\n"
        f"   long or intricate invocations the user cannot eyeball at a glance.\n"
        f"   Expected: {{expected_result}}\n"
        f'   Shall I run this? [yes/modify/skip/abort]"\n\n'
        f"2. **Wait for user response.** Do NOT proceed without explicit approval.\n"
        f"   - yes → execute (or ask user to run + paste output if you lack shell access)\n"
        f"   - modify → user provides a modified command, use that instead\n"
        f"   - skip → record as skipped, move to next test\n"
        f"   - abort → stop all testing, record session as abandoned\n\n"
        f"3. **Verify the command actually ran before recording anything.** Inspect the\n"
        f"   output: if it shows a shell/tool error — `command not found`, `invalid\n"
        f"   option`, `unrecognized argument`, `permission denied`, a non-zero exit, a\n"
        f"   usage/help dump, or empty output where results were expected — the command\n"
        f"   did **not** execute successfully.\n"
        f"   - Do **NOT** record a failed or invalid command as the test result. A\n"
        f"     broken command is not a finding.\n"
        f"   - **Troubleshoot it instead:** confirm the tool is installed and on PATH,\n"
        f"     fix the syntax, correct the flags, or adjust privileges, then re-present\n"
        f"     the corrected command for approval (treat this as a `modify`).\n"
        f"   - If you cannot get it working after a reasonable attempt, record it with\n"
        f"     status `failed` (not `executed`) and a `findings_summary` explaining\n"
        f"     what went wrong and what you tried — then ask the user how to proceed.\n"
        f"   - Only record status `executed` when the command genuinely ran and\n"
        f"     produced real output to interpret.\n\n"
        f"4. **Record the result:**\n"
        f"   `POST {base_url}/agent/test-plans/{plan_id}/entries/{{entry_id}}/test-results`\n\n"
        f"#### 2c. Complete the entry\n"
        f"After all tests for a host:\n"
        f"`POST {base_url}/agent/test-plans/{plan_id}/entries/{{entry_id}}/complete`\n"
        f"Include a findings summary for the host.\n\n"
        f"**Sanity-check gate (v2.22.0).**  Completion requires *either* a\n"
        f"passing `HostSanityCheck` for this entry (recorded in step 2a) *or*\n"
        f"an explicit ``override_reason`` in the complete payload — the\n"
        f"server will return 400 otherwise.  Use the override path only when\n"
        f"a sanity check genuinely wasn't possible (target offline, scope\n"
        f"removed mid-run, etc.) and state the reason concretely:\n"
        f"```json\n"
        f'{{ "findings_summary": "...", "override_reason": "host 10.0.0.5 stopped responding before the verification banner-grab; recorded 0 results." }}\n'
        f"```\n"
        f"Overrides are audit-visible — a human reviewer will see exactly\n"
        f"which entries closed without verification and why.\n\n"
        f"**Results gate (per-test coverage).**  Completing an entry requires\n"
        f"that **every** proposed test has a recorded result — not just one.\n"
        f"For an entry with N proposed tests the server refuses (`400`) unless\n"
        f"every `test_index` 0..N-1 has a `TestExecutionResult` row in a\n"
        f"**terminal** status: `executed`, `skipped`, `failed`, or\n"
        f"`not_applicable`.  So:\n"
        f"  - Record a result for tests you DON'T run too — `skipped` (operator\n"
        f"    declined), `failed` (tool/network error), or `not_applicable`\n"
        f"    (doesn't apply on closer inspection).  `pending` and\n"
        f"    `pending_approval` are NOT terminal and will block completion —\n"
        f"    resolve them first.\n"
        f"  - An entry with zero proposed tests, or one you're closing before\n"
        f"    covering every test, requires an explicit ``no_tests_run_reason``\n"
        f"    in the complete payload (captured next to ``override_reason`` in\n"
        f"    the audit row) — e.g. target went offline before testing, or the\n"
        f"    host was reclassified out of scope mid-run.\n"
        f"Recording 1 of 3 tests and calling `/complete` with no reason returns\n"
        f"a 400 naming how many test indices still lack a result row.\n\n"
        f"**Status values.**  ``overall_status`` is a strict enum: pick from\n"
        f"`completed` (default — tests ran, here are findings), `rejected`\n"
        f"(host should not have been in the plan — explain in findings\n"
        f"summary), or `in_progress` (you're pausing rather than closing).\n"
        f"Unknown statuses are rejected at the API layer.\n\n"
        f"### Step 3 — Complete the session (v2.45.2 — MANDATORY)\n"
        f"After every host entry is in a terminal state (`completed` or `rejected`),\n"
        f"close the session itself:\n\n"
        f"```bash\n"
        f"curl -sk -X POST {base_url}/agent/execution-sessions/{session_id}/complete \\\n"
        f"  -H \"X-API-Key: $KEY\" -H \"Content-Type: application/json\" \\\n"
        f'  -d \'{{"notes": "...one-paragraph summary...", "overall_status": "completed"}}\'\n'
        f"```\n\n"
        f"Without this call the session row stays `active` indefinitely — operators\n"
        f"see the run as in-flight on the runs list even though your work is done.\n"
        f"Pre-v2.45.2 there was no such endpoint and sessions accumulated as\n"
        f"phantom \"still active\" rows; that gap is closed but only if YOU make the\n"
        f"call.  Use `overall_status: \"failed\"` instead when the session broke\n"
        f"(auth lost, plan invalidated, target gone) and you're closing it as a\n"
        f"failure rather than a success.  The endpoint refuses if any entries are\n"
        f"still non-terminal; finish them first or pass an explicit reason.\n"
        f"Keep `notes` concise — it is capped at 8192 bytes and appended to any\n"
        f"existing session notes; content past the cap is dropped, so don't dump\n"
        f"full findings there (those live on each entry's results).\n\n"
        f"### Step 4 — Report summary\n"
        f"After the session is closed, report to the user:\n"
        f"- Total tests executed vs. skipped\n"
        f"- Findings discovered (by severity)\n"
        f"- Hosts with critical/high findings that need immediate attention\n\n"
        f"**Plan ID:** {plan_id}\n"
        f"**Plan Title:** {plan_title}\n"
        f"**Entries:** {entry_count} hosts to test\n"
        f"**Session ID:** {session_id}\n"
    )

    instructions += _feedback_section(
        base_url=base_url,
        source="in_session_execution",
        context={"test_plan_id": plan_id, "execution_session_id": session_id},
    )
    return instructions
