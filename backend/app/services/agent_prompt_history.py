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
        "version": "1.62.0",
        "app_version": "2.316.0",
        "summary": (
            "The env-probe `tools_status` field is now typed, so the guide drops "
            "its 'the server tolerates both a list and a dict' note: send a list "
            "of {name, status} objects, status one of ok/warn/missing/info, or the "
            "probe is rejected with a 422 instead of silently stored. The status "
            "vocabulary was always the contract the recon planner branches on; it "
            "is now enforced at the boundary. Found while reviewing the MCP tool "
            "surface for tool/endpoint drift."
        ),
    },
    {
        "version": "1.61.0",
        "app_version": "2.315.0",
        "summary": (
            "The guide's assist tool-name list catches up with a rename: the "
            "per-host tool `assist_get_host_findings` is now "
            "`assist_get_host_vulnerabilities`. It returns raw scanner "
            "vulnerability rows, whose ids are a different id-space from the "
            "triaged project Findings that `assist_list_findings` / "
            "`assist_get_finding` work on — the old shared word 'findings' led "
            "agents to pass a host vuln id to `assist_get_finding` and get a "
            "404. Found by an agent exercising all 27 assist MCP tools."
        ),
    },
    {
        "version": "1.60.0",
        "app_version": "2.314.0",
        "summary": (
            "The recon sequence can now block a step that has no fallback, and "
            "the guide says so. Previously only steps with a documented "
            "fallback were checked against the environment probe, so the "
            "optional eyewitness screenshot pass kept advertising a runnable "
            "command on hosts whose own preflight had reported eyewitness "
            "missing. Such a step now comes back with "
            "`blocked_reason: \"tool_unavailable\"` and `unavailable_tool`, "
            "distinct from the existing `neither_available` because the "
            "agent's move differs: there is no fallback to consider, so report "
            "it rather than reaching for `acceptable_fallbacks`. Found by an "
            "agent running the full recon workflow against a /24."
        ),
    },
    {
        "version": "1.59.0",
        "app_version": "2.311.0",
        "summary": (
            "The guide catches up with 1.58.0's authority change, which had "
            "only reached the prompts. AGENTS.md still published the removed "
            "capability names next to each write route and still told agents a "
            "write is refused unless the host is assigned to their operator — "
            "both false since v2.309.0, and the second one materially so: an "
            "analyst's agent may write anywhere in the project. The write "
            "section now states the real rule and points at "
            "`GET /agent/identity`, which gained `can_write_project_data` (and "
            "the operator's `project_role`) in the same release so an agent can "
            "learn its authority by asking rather than by reading a 403. "
            "Assigned-hosts is kept as guidance about whose work to touch, "
            "which is what it now is, rather than as a boundary the server "
            "enforces."
        ),
    },
    {
        "version": "1.58.0",
        "app_version": "2.309.0",
        "summary": (
            "Assist prompts state authority as a rule instead of a grant list. "
            "The capability system is gone, so there is no longer a read-only "
            "session and no per-session write grant: an agent acts with its "
            "operator's own project permissions, checked on every call. The "
            "prompt used to render one of two authority blocks depending on "
            "whether writes had been granted, which meant it could contradict "
            "itself and could promise the agent something the server would "
            "later refuse. Now one block: 'You act as <operator>' — you can "
            "change what they can change, a 403 means their role does not "
            "permit it and is not an error to retry, and scans / test plans / "
            "execution remain refused from assist regardless of role. The "
            "assigned-hosts narrowing is gone with the row-level constraint, "
            "so the prompt points at `assigned:me` as where notes usually "
            "belong rather than as a boundary."
        ),
    },
    {
        "version": "1.57.0",
        "app_version": "2.304.0",
        "summary": (
            "Surviving your own key expiring mid-job. Every workflow prompt now "
            "carries the same short block, because the failure is the same "
            "everywhere and it is expensive: an agent launches a long scanner, "
            "blocks for hours, its key lapses while it waits, and it discovers "
            "that only when it tries to upload — with the scanning already "
            "done. Two rules, in order of preference: before starting anything "
            "long, check `key_expires_at` from /agent/identity and POST to the "
            "`renew_path` it now returns; and if you get a 401 anyway — the "
            "normal outcome, since a blocked agent cannot make requests — read "
            "the body, and on `recoverable: true` renew with the SAME key and "
            "retry the exact request that failed. The prompt states the part "
            "that actually costs money if an agent gets it wrong: never re-run "
            "a scan because of a 401, and never discard output you are holding."
        ),
    },
    {
        "version": "1.56.0",
        "app_version": "2.297.0",
        "summary": (
            "Completeness of the picture (assist P2). INGESTION ISSUES "
            "(`/assist/ingestion-issues`): the prompt now tells the agent to "
            "check this BEFORE reporting that anything is absent — 'no web "
            "servers in that range' and 'the httpx upload failed to parse' "
            "produce the same empty result from every other tool, and only one "
            "of them is a finding about the network. It also names the quiet "
            "case, `kind=degraded`: a file that parsed and IS in the project "
            "but had rows dropped, so counts from it are undercounts while the "
            "job still reads as completed. SEGMENTS (`/assist/segments`): the "
            "bullet now describes the real payload — exposure, neglect and "
            "hygiene per subnet with a recommended action — because the "
            "endpoint stopped hand-rolling its rollup and now wraps the "
            "service behind the Subnet Insights page; it also warns that "
            "`no_coverage` is a scanning gap rather than a clean subnet, and "
            "that a capped page shows the worst subnets, not all of them. "
            "WEB INTERFACES: `/assist/hosts/<id>` now returns what a host is "
            "serving (url, title, server banner, technologies) plus a "
            "screenshot download path per EyeWitness capture — a second "
            "screenshot store from note attachments, captured automatically at "
            "ingest, so it exists for hosts nobody has written a note about."
        ),
    },
    {
        "version": "1.55.0",
        "app_version": "2.294.0",
        "summary": (
            "The analysis stage of the analyst's job, which assist had no read "
            "on at all. POSTURE (`/assist/posture`): the project's overall "
            "condition, wrapping the same computation the Posture page shows a "
            "manager — an agent deriving 'how exposed are we' from raw counts "
            "would quote different numbers than the page, and the disagreement "
            "surfaces as the agent being wrong. The prompt calls out "
            "`insufficient_evidence` explicitly: it means the estate has not "
            "been assessed enough to judge, and reporting it as a clean bill of "
            "health is the most damaging wrong thing an agent could say about "
            "an engagement. PATTERNS (`/assist/patterns`): blind spots, segment "
            "outliers, condition spread and per-family root causes — the "
            "'hosts on this subnet look worse than the rest' and 'the inventory "
            "is all end-of-life' claims, with the evidence that justifies "
            "stating them that broadly. The prompt forbids calling these "
            "trends: the analysis is cross-sectional by design (an engagement "
            "runs weeks), and 'got worse' is a claim the data cannot support. "
            "FINDING EVIDENCE (`/assist/findings/<id>`): the note a human wrote "
            "to justify promoting a finding, its comment thread, and its "
            "screenshots. An agent asked to write findings up previously had "
            "titles and severities and none of the evidence. Screenshots are "
            "handed over as download paths, not bytes — a base64 image costs "
            "thousands of tokens and cannot be shown to anyone, and the report "
            "needs the file on disk beside it regardless."
        ),
    },
    {
        "version": "1.54.0",
        "app_version": "2.293.0",
        "summary": (
            "Three more analyst questions assist could not answer. TESTING "
            "HISTORY (`/assist/hosts/<id>/testing`): it could see what scanners "
            "reported and nothing about what the team did, so it could not "
            "distinguish a finding nobody has looked at from one a tester "
            "confirmed by hand — and every answer implicitly claimed the "
            "former. Only entries from approved plans, never rejected ones: a "
            "reviewer flipping an entry to rejected has already decided, and an "
            "agent reporting it as outstanding work re-litigates that. SEGMENTS "
            "(`/assist/segments`): per-subnet hosts/criticals/highs/unassigned, "
            "sorted worst-first, because 'which segment is worst?' was "
            "otherwise a count per subnet reassembled by the agent — arithmetic "
            "it does silently and sometimes wrongly, and the ordering IS the "
            "answer. RECENT NOTES (`/assist/notes`): per-host notes answer "
            "'what about THIS host'; picking an engagement back up is a "
            "question about the work, and open notes are the outstanding-work "
            "list the project actually keeps."
        ),
    },
    {
        "version": "1.53.0",
        "app_version": "2.292.0",
        "summary": (
            "Assist becomes a place an analyst can ask anything, rather than "
            "only 'which hosts match X'. Four questions it could not answer: "
            "(1) FINDINGS across the project — it could see them one host at a "
            "time, so the spine had to be rebuilt by walking hosts, which "
            "counts a finding once per affected host; `/assist/findings` "
            "returns the real total plus a severity breakdown for whatever "
            "filter was asked, including `unowned=true`. (2) NOTES — assist "
            "could write them and not read them, so 'what do we already know "
            "about this host?' was unanswerable and an agent could duplicate a "
            "colleague's note from an hour earlier. (3) VOCABULARY — the DSL "
            "accepts tag:/label:/site:/assigned:<user> and nothing told the "
            "agent which values exist, so it guessed; a guessed tag returns "
            "zero hosts rather than an error, making 'nothing is tagged "
            "production' a confident wrong answer. (4) COVERAGE — every other "
            "surface reports what WAS found, and without per-domain assessment "
            "coverage 'no critical findings' reads as 'no critical exposure'. "
            "The prompt now names all four and says when to reach for each."
        ),
    },
    {
        "version": "1.52.0",
        "app_version": "2.291.0",
        "summary": (
            "Assist gains what it needed to answer questions and to fill in a "
            "report. COUNTING: `GET /agent/assist/hosts/count` returns the total "
            "for any filter — the host list is a bare array with no total, so "
            "'how many hosts have critical findings and no assignee?' could only "
            "be answered by paging to exhaustion, and an agent that stopped at "
            "the first page reported a confident wrong number. `assigned:` also "
            "accepts `none` now (it took me/any/username/id while the sibling "
            "`follow:` accepted `none`, so the obvious phrasing errored on one "
            "field and worked on the other). REPORTS: the prompt now says where "
            "a report template lives — a file on the OPERATOR's machine, in the "
            "working directory the agent already reads and writes. BlueStick "
            "hosts no templates and stores no finished report; its job is the "
            "data, and the agent's is to replace each placeholder with a value "
            "it actually fetched, leaving anything it could not source visibly "
            "unfilled rather than inventing a number."
        ),
    },
    {
        "version": "1.51.0",
        "app_version": "2.281.0",
        "summary": (
            "Every workflow's prompt now opens with a mandatory read-back: before "
            "its first tool call or command, the agent tells the operator — in its "
            "own words, specific to this session — what it may touch, what it may "
            "run without asking, where output will go, and what it will stop and "
            "ask about. BlueStick cannot enforce the guardrails (commands run on "
            "the operator's machine; the server sees only what is reported), and "
            "this is the one thing it can do instead: the read-back is the single "
            "moment a human sees the agent's *understanding* of the bounds rather "
            "than its output, so a wrong scope costs a sentence to fix instead of "
            "a scan against the wrong range — and it makes the agent's own words "
            "the record, since an agent that stated one working directory and "
            "wrote to another has visibly contradicted itself. Verbatim recital is "
            "explicitly rejected: it can be produced without reading anything and "
            "gives the operator nothing to check. Per-workflow wording — recon and "
            "execution recite directory/tools/scope, plan generation and assist "
            "recite what data they read and write, since a rule that obviously "
            "doesn't apply is how a read-back becomes boilerplate. New "
            "`list_approved_tools` MCP tool (all workflows) so the tool half of "
            "the read-back is read from the live registry rather than recalled; "
            "the safety-rule block, previously execution-only, is unchanged."
        ),
    },
    {
        "version": "1.50.0",
        "app_version": "2.279.0",
        "summary": (
            "Approval model changed from 'ask before every command' to a bounded "
            "exception, and MCP now covers all three agentic workflows. An agent "
            "may run a command WITHOUT waiting when three things hold: the tool "
            "is in BlueStick's approved set, the target is a host already in the "
            "project's inventory, and every file it writes lands in the session's "
            "working directory. Everything else — unapproved tool, target outside "
            "the inventory, reads or writes outside that directory, changes to "
            "settings/software/credentials — still stops and asks. Commands are "
            "shown either way. Blanket approval was the safe-sounding rule that "
            "trained operators to click through fifty routine prompts, so the one "
            "command that mattered arrived looking like the other forty-nine; "
            "naming the bounds is what makes the remaining prompts mean "
            "something. An agent that needs an unapproved tool now has somewhere "
            "to say so (`suggest_tool` / POST /agent/tool-suggestions) instead of "
            "substituting one nobody vetted. The guide states plainly that "
            "BlueStick cannot enforce any of this — the client's sandbox is the "
            "real boundary, and the server's contribution is the record. MCP: "
            "recon, plan generation and execution have tools now, scoped to the "
            "caller's own workflow (`agent_identity` reports which), with the "
            "bulk file-shaped endpoints deliberately still curl."
        ),
    },
    {
        "version": "1.49.0",
        "app_version": "2.271.0",
        "summary": (
            "MCP surface hardened after an external protocol review. New "
            "`assist_record_environment` tool covers the MANDATORY environment "
            "probe, which previously had no tool — an MCP-only client had to "
            "shell out to curl for the one call it must make first; it resolves "
            "the session from your key, so no `session_id` argument. "
            "`tools/list` now hides writes your session lacks the capability "
            "for (so an unseen write is a missing grant, not a missing "
            "feature), every tool carries MCP annotations (`readOnlyHint` and "
            "friends) so hosts can auto-approve reads, and successful calls "
            "return `structuredContent` as well as text. `Authorization: "
            "Bearer` now works at the MCP layer alongside `X-API-Key` (Codex "
            "reads its key from an env var and sends bearer). Protocol "
            "conformance: version negotiation no longer echoes versions we "
            "don't implement, malformed params answer -32602 instead of a 500, "
            "unknown tools/arguments are protocol errors, and the server no "
            "longer mints an Mcp-Session-Id it ignored."
        ),
    },
    {
        "version": "1.48.0",
        "app_version": "2.266.0",
        "summary": (
            "MCP transport for the assist surface. The same assist reads and "
            "writes are now exposed as MCP tools over a Streamable-HTTP endpoint "
            "at `/api/v1/mcp`, so MCP-capable hosts (Copilot, Claude Code, Cursor) "
            "can call them as native tools instead of shelling `curl` — read tools "
            "can be marked always-allow and stop prompting. The Start Assist dialog "
            "emits a ready-to-paste `mcp_config`. Auth/scope/capability/audit are "
            "unchanged (the MCP layer loops back into `/agent/assist/*` in-process, "
            "forwarding X-API-Key). The AGENTS.md assist slice documents the tool "
            "names and notes that the bulk `report-context.ndjson` stream stays a "
            "curl download, not an MCP tool."
        ),
    },
    {
        "version": "1.47.0",
        "app_version": "2.265.0",
        "summary": (
            "Report-generation data source for assist. New "
            "`GET /agent/assist/report-context.ndjson` streams the COMPLETE "
            "per-host dossier (identity, ports, findings-with-evidence, notes, "
            "discoveries, canonical/execution findings, provenance, tags, review "
            "state) — one JSON object per host, uncapped, memory-bounded — the "
            "same correlated record the server-side report builds. The prompt "
            "directs report-writing there (download to a file, populate a "
            "template) instead of stitching per-host calls, so a project with "
            "tens of thousands of hosts can be reported completely."
        ),
    },
    {
        "version": "1.46.0",
        "app_version": "2.264.0",
        "summary": (
            "Assist can now read individual findings, not just counts. New "
            "`GET /agent/assist/hosts/{id}/findings` returns per-finding "
            "severity, CVE/plugin id, affected port, exploitability, "
            "description, remediation, and scanner evidence (severity filter + "
            "total/has_more pagination) — the prompt directs evidence-rich "
            "reporting there instead of citing bare vuln_summary counts. Also "
            "fixed the port double-count: NetExec stored its transport "
            "(smb/ldap/winrm) in the port `protocol` column, duplicating a "
            "physical port against a tcp scan and inflating open_port_count; "
            "protocol is now the IP transport and the NXC value is the service "
            "name (existing rows migrated)."
        ),
    },
    {
        "version": "1.45.0",
        "app_version": "2.263.0",
        "summary": (
            "Assist-agent feedback (v1.44.0) actioned. The 'What this session "
            "can NOT do' block is now generated from the capability flag, so it "
            "no longer hard-codes 'strictly read-only' and contradicts the "
            "Writing section on a write-enabled session; the provenance action "
            "label now names host-attribute writes too. The /assist/scopes note "
            "stops claiming the 100-subnet cap is silent — it documents the "
            "subnet_total / subnets_truncated fields the response actually "
            "returns. (Companion API changes, not prompt text: /assist/context "
            "totals gains scan_count; /assist/session echoes capabilities + "
            "constraint + operator; assist host DTOs carry the operator's follow "
            "status; the environment probe echoes agent_model/tool/prompt_version.)"
        ),
    },
    {
        "version": "1.44.0",
        "app_version": "2.258.0",
        "summary": (
            "Server-side DNS querying removed entirely — BlueStick never "
            "originates network queries. The /dns lookup + zone-transfer (AXFR) "
            "routes and the upload-time DNS enrichment option are gone; DNS data "
            "is ingested only from operator-produced files (dnsx JSON, DNS CSV, "
            "amass). The recon exit criterion now says to collect DNS records "
            "terminal-side (dnsx / dig) and upload the output, rather than expect "
            "server enrichment."
        ),
    },
    {
        "version": "1.43.0",
        "app_version": "2.254.1",
        "summary": (
            "Host-assignment DSL guidance updated + contract tightened. The "
            "`assigned:` predicate (alias `assignee:`) now accepts a USERNAME "
            "(case-insensitive) or numeric id, not only `me`/`any` — user ids "
            "aren't surfaced, so username is the normal case. The `has:` value "
            "vocabulary is now enumerated and includes the new `weak_tls` "
            "(SSLv2/SSLv3/TLS 1.0/1.1 offered). AGENTS.md was also trimmed of "
            "version-archaeology and duplicated guidance for concision; no "
            "workflow step, endpoint, or behavioural rule changed."
        ),
    },
    {
        "version": "1.42.0",
        "app_version": "2.246.0",
        "summary": (
            "Assist gains writes + a bulk-download valve. (1) PATCH "
            "/agent/hosts/<id> corrects operator-curated host attributes "
            "(hostname / os_name) after investigation, gated by the new "
            "write:host capability and the same assigned-hosts row scope as "
            "notes/follow; only those two fields are editable, scan-derived "
            "facts stay read-only. (2) GET /agent/assist/hosts.ndjson streams "
            "EVERY matching host (same filters + q DSL) one JSON object per "
            "line, uncapped, so a project with thousands of hosts is "
            "downloaded to disk and processed with jq/grep instead of paged "
            "into the agent's context. The assist prompt documents both."
        ),
    },
    {
        "version": "1.41.0",
        "app_version": "2.241.0",
        "summary": (
            "Recon summary is no longer allowed to overflow the agent's "
            "context. Measured at 40,000 hosts in one session, "
            "GET /agent/recon/summary returned 31.4 MB (~7.8M tokens) — "
            "unusable by any model, and the old guidance ('don't read or "
            "echo it whole') was unfollowable since receiving a tool result "
            "is reading it. hosts[] is now a 50-host sample carrying "
            "hosts_total / hosts_truncated, and the complete data moved to "
            "three streamed downloads (recon/hosts.ndjson, "
            "recon/live-hosts.txt, recon/web-targets.txt) that the agent "
            "redirects to a file and parses with jq/grep. Past 1000 hosts "
            "live_hosts_file_content is EMPTIED rather than shortened — a "
            "trimmed -iL file would under-scan the scope while the run "
            "reported full coverage. Agents must report hosts_total, not "
            "hosts.length."
        ),
    },
    {
        "version": "1.40.0",
        "app_version": "2.231.0",
        "summary": (
            "Assist sessions can now be started with limited write access. When "
            "the operator grants it, the assist prompt gains a write section "
            "covering POST /agent/hosts/<id>/notes and /follow, scoped to hosts "
            "assigned to that operator (find them with `?q=assigned:me`), plus "
            "write-discipline rules: announce before writing, record evidence-"
            "backed observations only, mark uncertainty in the note body, and "
            "never set `reviewed` without the operator confirming. Notes written "
            "through the API are stamped agent-authored and render with an "
            "'Agent' badge. Read-only sessions keep the previous prompt verbatim."
        ),
    },
    {
        "version": "1.39.0",
        "app_version": "2.230.0",
        "summary": (
            "New host-query DSL field `exploitport:<port>` — matches hosts whose "
            "exploitable finding is ON that port (same-row correlation), stricter "
            "than `port:X AND has:exploit` (X open AND any exploit anywhere). "
            "Available anywhere the DSL is (`GET /agent/assist/hosts?q=`). "
            "Exploitability remains a source-agnostic boolean (Nessus today). "
            "Additive; no endpoint or field removed."
        ),
    },
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
