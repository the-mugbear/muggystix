"""Agent prompt version history (structured).

Previously this lived as a ~6.6 KB trailing comment on the
``PROMPT_VERSION`` constant in ``agent_prompt_service``.  Every bump of a
one-line constant therefore produced a multi-KB diff, and the history was
unstructured prose unreachable by any tooling.

It now lives here as data.  ``PROMPT_VERSION`` is DERIVED from the newest
entry (``PROMPT_VERSION_HISTORY[0]["version"]``), so the version and its
changelog can never drift: bumping the prompt = prepend one entry to this
list.

Entries are newest-first.  ``app_version`` is the platform release the
change shipped in.  Keep ``summary`` to the operator-facing "what changed
and why" — the same content that used to live in the comment.
"""
from __future__ import annotations

from typing import Dict, List

# Newest first.  PROMPT_VERSION is taken from entry [0].
PROMPT_VERSION_HISTORY: List[Dict[str, str]] = [
    {
        "version": "1.38.0",
        "app_version": "2.202.0",
        "summary": (
            "Version-compatibility clarity (from recon feedback #8). An agent "
            "couldn't tell whether the agents-guide it fetched matched the "
            "prompt it was given, because the guide's header carried the "
            "platform version (e.g. 2.201.0) while the prompt carried "
            "PROMPT_VERSION (e.g. 1.37.0) — two unrelated numbering schemes. "
            "Now: (1) GET /agents-guide stamps the served guide with the LIVE "
            "PROMPT_VERSION, so the guide and the prompt always report the same "
            "compatibility number; (2) AGENTS.md gained a 'Version & "
            "compatibility' note saying the Prompt version is the only number "
            "that matters and the backend stamp is just freshness; (3) all four "
            "/context responses (plan, execution, recon, assist) now carry "
            "`prompt_version` so the agent can verify mid-session without "
            "re-parsing the guide. No endpoint removed; additive field."
        ),
    },
    {
        "version": "1.37.0",
        "app_version": "2.201.0",
        "summary": (
            "Assist agents can now run the FULL Hosts query DSL via "
            "`GET /agent/assist/hosts?q=...` — the same parser/evaluator the "
            "human Hosts page uses. This closes the headline assist gap: "
            "`follow:` and `assigned:` resolve against the operator who started "
            "the session, so an assist agent can finally answer 'which hosts do "
            "I have in review?' (`q=follow:in_review`) and 'assigned to me?' "
            "(`q=assigned:me`), plus the whole boolean surface (cve:, vuln:, "
            "tag:, label:, site:, note:, has:, AND/OR/NOT, …) that the narrow "
            "discrete filters couldn't express. Still strictly read-only — the "
            "DSL only filters, never mutates. Malformed `q` is a 400. AGENTS.md "
            "assist endpoint table + operating examples updated."
        ),
    },
    {
        "version": "1.36.0",
        "app_version": "2.200.0",
        "summary": (
            "AGENTS.md accuracy pass — the guide had drifted from the API "
            "across ~120 backend releases. Corrected: /agent/hosts filter list "
            "(dropped the non-existent `min_risk_score`, documented the real "
            "`has_exploit_available`); /agent/assist/hosts filter list (dropped "
            "`min_risk_score`); /agent/recon/subnets pagination (actually "
            "default 500 / max 2000, not 100/500); /agent/scans optional "
            "filters (`tool`, `created_after`, `sort_by`, `sort_order`); and the "
            "environment-aware blocked-step shape — the placeholder keeps the "
            "original step `phase` (there is no `manual_action_required` phase "
            "value), so agents must key on `blocked_reason`/null `tool`+`command`, "
            "and `acceptable_fallbacks[]` is documented. No endpoint or behaviour "
            "changes — documentation correctness only."
        ),
    },
    {
        "version": "1.35.0",
        "app_version": "2.140.0",
        "summary": (
            "whatweb promoted to a first-class recon web tool. A new "
            "WhatwebParser ingests `whatweb --log-json` output into the same "
            "web_interfaces table as httpx (source=\"whatweb\": title, server "
            "header, detected tech stack), so the agent can now run AND upload "
            "whatweb instead of it being only an httpx fallback it couldn't "
            "ingest. build_tool_catalog gains a first-class phase=web whatweb "
            "entry (apt/brew/gem install hints; `whatweb --version` preflight); "
            "httpx/nmap_web list it as an alternative. preflight.sh + the "
            "AGENTS.md preflight tool list now check whatweb. Use it as the "
            "dependable web pass when httpx (Go binary / Python-CLI collision) "
            "won't install."
        ),
    },
    {
        "version": "1.34.0",
        "app_version": "2.139.0",
        "summary": (
            "Recon-agent ergonomics from a live test run. (1) GET "
            "/agent/recon/jobs/{id} now echoes queue_age_s (created->started) "
            "and parse_s (started->completed), both null until the transition "
            "happens, so an agent can tell a backed-up queue from a slow parse "
            "before it keeps polling. (2) GET /agent/recon/summary and POST "
            "/agent/recon/complete now carry live_hosts_file_content — a "
            "ready-to-redirect, newline-joined IP file of every host discovered "
            "SO FAR this session — so the mandatory staged service-probe pass can "
            "`-iL session-hosts.txt` instead of rebuilding the list from hosts[]. "
            "Distinct from known_hosts_probe.live_hosts_file_content, which is "
            "PRIOR recon. AGENTS.md updated in both the polling-loop and "
            "summary-envelope sections."
        ),
    },
    {
        "version": "1.33.0",
        "app_version": "2.109.0",
        "summary": (
            "Single-sourced the execution safety rules (CR5 Refactor #3).  The "
            "live execution prompt and the offline bundle instructions each "
            "carried their own copy of the mandatory approval/sanity-check/"
            "result-recording block and had drifted (rule 2 read differently "
            "live vs offline); both now render the canonical rules from "
            "app.services.agent_policy.SAFETY_RULES, with a golden parity test. "
            "Rule wording is the unified form (sanity check = source IP / "
            "reverse DNS / banner grab to verify the intended target; record "
            "every command + outcome).  AGENTS.md remains the authoritative "
            "detailed protocol; the prompt carries the terse skeleton."
        ),
    },
    {
        "version": "1.32.0",
        "app_version": "2.85.0",
        "summary": (
            "Comprehensive feedback coverage + post-2.84.x guidance refresh. "
            "Assist prompt now closes with the standard _feedback_section "
            "(AgentFeedbackSource gained ASSIST and AgentFeedback gained "
            "assist_session_id, so the row finally links back to the "
            "conversation it came from). Recon feedback context drops scope_id "
            "from the rendered template — AgentFeedbackCreate never declared the "
            "field, so Pydantic silently dropped it on receive and recon feedback "
            "rows lost their workflow attribution; now the template only invites "
            "the linkable field, recon_session_id (scope is recoverable via "
            "ReconSession.scope_id when triage needs it). Plan-gen / execution "
            "feedback contexts unchanged (already correct). Also covers the "
            "AGENTS.md guidance-currency sweep (60-rpm rate-limit numbers bumped "
            "to 240 to match v2.84.0; /execution-sessions/{id}/complete + "
            "/environment + /feedback added to the API reference tables; "
            "has_exploit_available filter added to the plan-gen entry rubric now "
            "that v2.83.2 actually persists Vulnerability.exploitable)."
        ),
    },
    {
        "version": "1.31.0",
        "app_version": "2.79.0",
        "summary": (
            "Stop execution agents re-running recon scans. Field report: an "
            "execution run proposed an nmap scan identical to recon's. Root cause "
            "was guidance: the plan-gen rubric authored generic discovery/version "
            "scans (nmap -sV) as \"tests\" and nothing told plan-gen OR execution "
            "that recon already characterized these hosts. PLAN-GEN prompt now "
            "carries a \"build on recon — don't re-discover\" clause (open "
            "ports/services/versions are already in candidate_hosts[].ports; "
            "propose targeted validation/exploitation on the KNOWN ports; never "
            "nmap -sn/-sV/full-port sweeps). EXECUTION prompt now makes "
            "known_services[] authoritative (target tests at the known ports; "
            "narrow/skip any proposed test that resolves to a broad nmap discovery "
            "scan; the sanity check is single-port verification, not a re-scan). "
            "AGENTS.md rubric retargeted (the \"multiple services, no vulns -> "
            "reconnaissance -> nmap -sV\" row is gone; named NSE scripts only), "
            "entry_template + Proposed-Test-Format example changed from "
            "`nmap --script smb-enum-shares` to a finding-driven `netexec` "
            "validation, known_services[] re-documented as authoritative, "
            "sanity-check clarified."
        ),
    },
    {
        "version": "1.30.0",
        "app_version": "2.78.0",
        "summary": (
            "Comprehensive guidance-currency sweep across all workflows (audit "
            "follow-up to 1.29.0). EXECUTION prompt: rewrote the stale \"Results "
            "gate (v2.25.0) = zero rows\" block to the real per-test coverage gate "
            "(every proposed test_index needs a TERMINAL result row — "
            "executed/skipped/failed/not_applicable — or no_tests_run_reason; "
            "pending/pending_approval block completion), and documented the "
            "8192-byte session-notes cap. ASSIST prompt: /assist/context is a "
            "headline summary (scopes capped 50 / recent_scans+recon capped 5 — "
            "use the totals block + scopes_truncated), /assist/hosts is a bare "
            "paginated array (default 500/max 5000, NO has_more — page with offset "
            "until short page; never count from one page), /assist/scopes "
            "100-CIDR/scope cap, /assist/scans 500-max/no-offset. PLAN-GEN prompt: "
            "empty final page (has_more boundary) is \"done\", not an error."
        ),
    },
    {
        "version": "1.29.0",
        "app_version": "2.77.0",
        "summary": (
            "Plan-gen prompt now tells the agent to PAGE /context (call again with "
            "?after_host_id={next_cursor} until summary.has_more is false) and to "
            "BATCH POST /entries in <=500-host chunks, so a project with >500 "
            "candidate hosts gets full coverage instead of silently stopping at "
            "the first 500-host page (agents were reading has_more:true as a cap "
            "rather than a \"fetch next page\" signal)."
        ),
    },
    {
        "version": "1.28.0",
        "app_version": "2.76.1",
        "summary": (
            "Extended the OS-neutral API-invocation guidance to the EXECUTION "
            "prompt (and the shared § Environment probe in AGENTS.md): a Windows "
            "execution agent's BlueStick API calls (probe, result recording) hit "
            "the same bare-`curl`-is-an-Invoke-WebRequest-alias trap, so execution "
            "now spells out `curl.exe` / `Invoke-RestMethod` + PowerShell POST-body "
            "quoting. Windows-only (execution stays Win+Linux; macOS is "
            "assist-only)."
        ),
    },
    {
        "version": "1.27.0",
        "app_version": "2.76.0",
        "summary": (
            "AI-assist prompt made OS-neutral so Windows/macOS/Linux operators can "
            "all use it: assist's \"commands\" are HTTPS API calls (not shell "
            "tools), so it now spells out per-shell invocation (bash/zsh `curl "
            "-sk`; Windows PowerShell `curl.exe`/`Invoke-RestMethod`, with the "
            "bare-`curl`-is-an-alias gotcha called out; PowerShell POST-body "
            "quoting) and tells the assist agent to report just os_family/shell — "
            "the recon/execution tool-inventory + preflight flow explicitly does "
            "NOT apply to assist."
        ),
    },
    {
        "version": "1.26.0",
        "app_version": "2.58.0",
        "summary": (
            "Platform renamed NetworkMapper -> BlueStick in every operator-facing "
            "surface (AGENTS.md, agent prompts, frontend UI, FastAPI title, "
            "README/CLAUDE.md intros). Working-directory slug stays "
            "`networkmapper-<project>-<workflow>-<session_id>` deliberately so "
            "concurrent agents from prior sessions on disk still align with what "
            "new agents create — the slug is internal session-isolation plumbing, "
            "not operator-facing. Database name, env vars, logger names, and code "
            "identifiers also stay NetworkMapper — see v2.58.0 CHANGELOG for the "
            "user-visible-only scope rationale."
        ),
    },
    {
        "version": "1.25.0",
        "app_version": "2.49.4",
        "summary": (
            "Nessus integration block now surfaces a license-cap chunking "
            "directive (operator-supplied `max_hosts_per_scan` in extra_config) so "
            "an agent attacking a large scope splits the Nessus scan into multiple "
            "license-sized runs rather than submitting one oversize scan Nessus "
            "rejects or truncates."
        ),
    },
    {
        "version": "1.24.0",
        "app_version": "2.49.3",
        "summary": (
            "/agent/test-plans/{id}/context now returns entry_template + "
            "entry_batch_example + entry_schema so plan-gen agents pattern-match "
            "on a concrete payload instead of inferring the POST /entries shape "
            "from prose; the plan-gen prompt nudges them to use those fields."
        ),
    },
    {
        "version": "1.23.0",
        "app_version": "2.49.1",
        "summary": (
            "Qualify the session-scoped working directory with the project slug "
            "(networkmapper-<project>-<workflow>-<session_id>). Within one "
            "deployment session_ids are already globally unique, but an operator "
            "who works two projects out of the same parent directory got opaque "
            "names (`networkmapper-recon-1` doesn't say which project) and a "
            "Nuclear-Clean + restart could collide with a leftover folder; the "
            "project slug self-documents the folder and survives ID-reset."
        ),
    },
    {
        "version": "1.22.0",
        "app_version": "2.47.0",
        "summary": (
            "Session-resume — RESUMED-SESSION notice on the prompt and the "
            "execution prompt gains a RESUMED-SESSION notice (steers a resumed "
            "agent to read /execution-context for prior progress and skip "
            "completed work instead of re-running it), AGENTS.md gains a § Resuming "
            "an interrupted session and progress-checkpoint guidance, and the "
            "recon prompt covers attaching to an existing recon session."
        ),
    },
    {
        "version": "1.21.0",
        "app_version": "2.46.3",
        "summary": "Concurrent-agent isolation via session-scoped working directories.",
    },
]

# The live prompt version — always the newest history entry.  Importers
# (agent_prompt_service, bundle_service) read PROMPT_VERSION from
# agent_prompt_service, which re-exports this.
PROMPT_VERSION: str = PROMPT_VERSION_HISTORY[0]["version"]
