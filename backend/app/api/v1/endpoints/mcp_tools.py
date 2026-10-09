"""Declarative MCP tool registry — what the server exposes.

This module is *data* (which tool maps to which endpoint, and what the tool
says about it), and ``mcp_assist.py`` is *protocol* (JSON-RPC framing, auth,
loopback dispatch, telemetry).  They change for different reasons — adding a
tool touches only this file.

Each authored entry (``_AUTHORED``):
    description  : shown to the model in tools/list
    method      : HTTP verb of the underlying endpoint
    path         : loopback path, with the endpoint's ``{name}`` placeholders
    params       : {argument: description} or {argument: {schema keys}} — what
                   the tool says about an argument beyond what the endpoint
                   declares: its description, and anything that NARROWS it
    hidden       : endpoint parameters the tool does not offer
    defaults     : MCP-side argument defaults (smaller pages than the endpoints')
    additive / idempotent / metadata_write : what a write does (drive the
                   destructive / idempotent annotations)
    retired_params : arguments the tool used to take; accepted and dropped
    path_alternatives : {arg: path} — other endpoints the tool may reach, chosen
                   by which one id the caller passes (exactly one of the path's
                   own id and these); for assist_get_image, assist_get_host
    result       : "image" — the endpoint returns an image, handed back as an
                   MCP image content block

**A tool's arguments are its endpoint's.**  Which arguments exist, where each
is sent, their types, enums, bounds and required-ness are read from the
endpoint's OpenAPI operation the first time the registry is used
(``derive_tool``); ``TOOLS[name]`` is the authored entry plus the derived
``path_params`` / ``query_params`` / ``body_params`` / ``input_schema``.  A new
endpoint parameter is offered by its tool without an edit here.

**The registry is not a security boundary.**  Every session lists every tool,
and every dispatch loops back through the real endpoint, where the router-level
``enforce_agent_operator_access`` and the operator's project role make the
actual decision.  The MCP layer makes no security decision anywhere, and this
file must not become the place it starts.  The kind of work a tool belongs to
(``tool_workflows``) is derived from its name and only groups the reference
page.

Writes are **not** filtered by whether the caller may perform them: a write
tool is listed for every session, and whether it succeeds is the operator's
project role, checked per request.  An agent that wants the answer
before trying reads ``can_write_project_data`` from ``agent_identity``.

**One session, every tool (v2.337.0).**  The operator starts one agent
session and the agent does whatever work is asked within it.  There are no
plans, execution runs or recon runs (v2.433.0, v2.442.0): the agent reads a
scope and uploads what its scanners found, proposes tests on hosts, and
records what it ran as evidence.

What is deliberately NOT a tool
-------------------------------
The bulk, file-shaped endpoints: ``report-context.ndjson``, a scope's
``hosts.ndjson`` / ``live-hosts.txt`` / ``web-targets.txt``
(``/agent/scopes/{scope_id}/…``), and ``POST uploads``.
They are meant to stream to (or from) a file on the operator's disk, not to be
materialised into a model's context — a 40k-host target list read through a tool
call is the same data, minus the ability to pipe it into the next scanner, plus
the token bill.  The server ``instructions`` point at them with curl instead.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any, Dict, Iterator, List, Optional

# The kinds of work, used only as catalogue tags on the tool reference page
# (`tool_workflows`) — since v2.337.0 a key belongs to one project session and
# `tools/list` is not filtered by them.  Kept as plain strings rather than
# importing the enum: this module is pure data with no DB dependency.
WORKFLOW_ASSIST = "assist"
# Proposing tests on hosts and recording what they produced.  Replaces
# "plan_generation" and "execution" (v2.442.0).
WORKFLOW_TESTING = "testing"
# Reading a scope (its subnets, domains, target lists) and uploading scan
# output.  Was "recon" until v2.433.1, when recon runs were removed.
WORKFLOW_SCOPE = "scope"

ALL_WORKFLOWS = frozenset({WORKFLOW_ASSIST, WORKFLOW_TESTING, WORKFLOW_SCOPE})

_ASSIST = frozenset({WORKFLOW_ASSIST})
_TESTING = frozenset({WORKFLOW_TESTING})
_SCOPE = frozenset({WORKFLOW_SCOPE})

# What an entry says about a parameter (``params``) is laid over what the
# endpoint declares — see ``derive_tool``.  An id is never below 1, whether or
# not its endpoint says so: the transport checks it before building the URL.
HOST_ID = {"minimum": 1, "description": "Numeric host id (from assist_list_hosts)."}

# The scope a read is about.  Every scope read takes it as a path parameter.
SCOPE_ID = {"minimum": 1, "description": "Scope to read (from assist_list_scopes)."}

# The model the agent says it is running as (v2.434.0).  No protocol carries
# it, so the writes where it matters ask for it: it labels the tests, the
# proposal or the session, and lets output from different models be compared.
AGENT_MODEL = "The model you are running as (e.g. claude-opus-5-5). Optional; labels this work."

_AUTHORED: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # Every workflow
    # -----------------------------------------------------------------------
    "agent_identity": {
        "description": (
            "What your API key is: its project session, bound project, write "
            "capabilities, the operator you act for, and when the key expires. "
            "Call this first; one key does every kind of work."
        ),
        "method": "GET",
        "path": "/api/v1/agent/identity",
    },
    "session_renew": {
        "description": (
            "Push your API key's expiry out without changing the secret — call it "
            "before launching, or right after finishing, a long-blocking scan whose "
            "key might lapse while it runs. Renewal, not rotation: the same key keeps "
            "working, so an agent holding scan output it cannot cheaply reproduce does "
            "not get re-bootstrapped. Works even if the key has ALREADY expired, and is "
            "bounded by the session — ending the session revokes the key regardless. No "
            "arguments: your key identifies its own session (v2.316.0)."
        ),
        "method": "POST",
        "metadata_write": True,
        "path": "/api/v1/agent/session/renew",
    },
    "end_session": {
        "description": (
            "End your session — the LAST call you make, and only when the operator "
            "says they are finished (finishing a task is not that: report and wait); "
            "file any feedback you have not filed yet first; the key dies with this call. It "
            "revokes your key and marks the session ended so the operator's Agent "
            "Sessions page stops showing it as live. Optional `notes`: one or two "
            "lines on what the session did (v2.340.0)."
        ),
        "method": "POST",
        "metadata_write": True,
        "path": "/api/v1/agent/session/end",
        "params": {
            "notes": "What the session did, in a line or two.",
            "agent_model": AGENT_MODEL,
        },
    },
    "read_agent_guide": {
        "description": (
            "The agent guide — reference for working with BlueStick: endpoint body "
            "shapes, upload formats, recipes, and the scope and working-directory "
            "rules in full. Read the part you need when a tool's description leaves "
            "you guessing; there is no need to read it all before starting. Omit "
            "workflow for the whole guide, or ask for one slice."
        ),
        "method": "GET",
        "path": "/api/v1/agents-guide",
        "params": {
            "workflow": {
                "enum": ["testing", "reconnaissance", "assist"],
                "description": "One slice of the guide. Omit for the whole guide.",
            },
        },
    },
    "list_tools": {
        "description": (
            "BlueStick's tool catalogue — what each tool is for, its ports, install "
            "command, phases, whether it is intrusive, and whether BlueStick parses its "
            "output (ingestible). A reference, not a permission list: what you run is "
            "between you and the operator."
        ),
        "method": "GET",
        "path": "/api/v1/references/tools",
        "params": {
            "status": {
                "enum": ["reference", "suggested", "rejected"],
                "description": (
                    "reference = in the catalogue. suggested = proposed by an "
                    "agent, not yet curated. rejected = a declined suggestion."
                ),
            },
            "category": "Filter to one category.",
        },
    },
    "suggest_tool": {
        "description": (
            "Propose a tool for BlueStick's catalogue — one you used or needed that "
            "list_tools doesn't have. Records your rationale for a curator. It is "
            "catalogue intake only; it neither grants nor withholds anything."
        ),
        "method": "POST",
        "path": "/api/v1/agent/tool-suggestions",
        "additive": True,
        "params": {
            "name": "Tool name as it would be invoked (e.g. ligolo-ng).",
            "rationale": (
                "What you used or needed it for — this is what a curator "
                "reads. Be specific."
            ),
        },
    },
    "submit_feedback": {
        "description": (
            "File feedback about BlueStick AT THE MOMENT you hit friction — when "
            "you retry a call, guess a field or a route (a 404 on a path you expected), work around a tool, or re-read the "
            "guide to make something work — not from memory at the end. Several "
            "one-line submissions during a session are the norm. end_session "
            "revokes your key, so file before you end. It is read by a coding "
            "agent working on BlueStick itself, so write for that reader: name the "
            "tool or endpoint, expected vs actual, the exact error text or missing "
            "field, and what would have let you finish faster. `source` names the "
            "kind of work: assist (queries/notes only), reconnaissance (scope "
            "reads and uploads), or testing (proposing host tests and recording "
            "evidence) — the session itself is attributed from your key. One "
            "row is about ONE kind of work. tool_suggestions "
            "here are context; suggest_tool files the registry entry."
        ),
        "method": "POST",
        "metadata_write": True,
        "additive": True,
        "path": "/api/v1/agent/feedback",
        # Session bookkeeping, but an APPEND: a retry files a second row
        # (v2.343.2).
        "idempotent": False,
        # v2.449.0 — went with the `assist_sessions` table.  Still accepted
        # from a client holding the older tool list, and dropped
        # (`mcp_assist._validate_arguments`): the session comes from the key.
        "retired_params": ["assist_session_id"],
        "params": {
            "source": {
                "enum": ["assist", "reconnaissance", "testing"],
                "description": "The kind of work this feedback is about.",
            },
            "prompt_version": "The prompt_version from your instructions block.",
            # The endpoint takes free-form objects; these name the keys a
            # reviewer reads.
            "api_critiques": {
                "items": {"type": "object", "properties": {
                    "endpoint": {"type": "string"}, "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                }},
            },
            "tool_suggestions": {
                "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "category": {"type": "string"},
                    "rationale": {"type": "string"},
                }},
            },
            "friction_notes": "What was confusing, slow, or guessed.",
            "agent_metrics": "agent_name, model, tool_calls_total, notes — whatever your environment exposes.",
        },
    },
    # -----------------------------------------------------------------------
    # Assist — interactive read/write over an existing inventory
    # -----------------------------------------------------------------------
    "assist_get_context": {
        "description": (
            "Project-inventory orientation: the engagement dates (start/end), the "
            "members and their project roles, host/port/scope/scan "
            "totals, the scope list (capped at 50), and recent scans. It carries "
            "NO findings — use assist_list_hosts to locate hosts and "
            "assist_get_host_vulnerabilities for the scanner vulns on one. Call this first. "
            "default_host_view (name + filters) is the view the Hosts page OPENS on "
            "for everyone when a project admin set one: your unfiltered counts are "
            "the whole project, so when the operator asks about 'the hosts I see', "
            "say which set you counted."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/context",
    },
    "assist_list_hosts": {
        "description": (
            "List/filter hosts in the project. Prefer the `q` boolean DSL (same "
            "vocabulary as the Hosts page, e.g. port:, os:, service:, cve:, "
            "check:, has:, follow:, assigned: — combine with AND/OR/NOT and "
            "parentheses; the full field list is in the agent guide's assist "
            "slice, and assist_get_vocabulary gives this project's tag / label / "
            "site / username values). port:/service: match OPEN ports; add "
            "@closed, @filtered or @any to the value for others (port:22@any). "
            "assigned: takes me / any / none / a username, so "
            "'has:critical AND assigned:none' is 'hosts with a critical scanner "
            "observation that nobody is assigned' — for unowned triaged "
            "findings use assist_list_findings unowned=true. An EXPLOITABLE "
            "CRITICAL (the critical itself has the exploit) is "
            "has:critical_exploit — 'has:critical AND has:exploit' also matches "
            "a critical beside an exploitable low. Each row carries "
            "exploitable_count and critical_exploitable_count. sort_by="
            "critical_vulns / exploitable_vulns with sort_order=desc lists worst "
            "first (default: by address). Returns {items, total, has_more, limit, "
            "offset}: `total` is every matching host — quote it, never the length "
            "of `items` (a page); raise offset by limit while has_more. For a "
            "count alone, assist_count_hosts."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts",
        # The endpoint's own default is 500 — right for a file download, a lot
        # of tokens for a model that usually wants the first handful.
        "defaults": {"limit": 100},
        "params": {
            "q": "Boolean query DSL (see tool description).",
            "sort_by": {
                "enum": [
                    "ip_address", "critical_vulns", "high_vulns", "exploitable_vulns",
                    "open_ports", "note_count", "discovery_count", "hostname", "last_seen",
                ],
            },
            "sort_order": {"enum": ["asc", "desc"]},
        },
    },
    "assist_count_hosts": {
        "description": (
            "How many hosts match a filter — the whole answer to a counting "
            "question, in one call. Use this instead of paging assist_list_hosts "
            "and counting: a page is not the total, and a count that stopped at "
            "the first page is wrong in a way nobody can see. Same `q` DSL as "
            "assist_list_hosts (e.g. 'has:critical AND assigned:none' — critical "
            "findings nobody owns)."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/count",
        "params": {"q": "Boolean query DSL (see assist_list_hosts)."},
    },
    "assist_get_host": {
        "description": (
            "Full detail for one host, as the host inspector shows it: identity, "
            "names seen at the address, OS detail, ports with service detail and "
            "NSE script output (bounded), severity counts, your review status, tags, "
            "assignees, scope membership, per-domain assessment state (with "
            "assessment.vuln_scan_credentialed: did a vulnerability scan log in "
            "to the host — yes / no / not_stated), weakness "
            "flags (smb_unsigned, weak_tls…), SMB signing, certificates, network "
            "attribution, scan conflicts (each has resolved_at = when the shown "
            "value was picked, not that anyone settled it), note_count and "
            "finding_count. names = every name SEEN at the address. Notes and "
            "individual vulnerabilities are separate — use assist_get_host_notes "
            "and assist_get_host_vulnerabilities for those. Pass exactly one of "
            "host_id or ip (the address itself — one host per address)."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}",
        "path_alternatives": {"ip": "/api/v1/agent/assist/hosts/by-ip/{ip}"},
        "params": {"host_id": HOST_ID, "ip": "The host's address, e.g. 10.0.0.5."},
    },
    "assist_get_host_vulnerabilities": {
        "description": (
            "Every raw scanner vulnerability on a host with evidence: severity, "
            "CVE/plugin id, title, affected port/service, CVSS, description, "
            "remediation, scanner evidence. Worst-severity first. Use this to cite "
            "specifics in a report, not just counts. Returns {host_id, items, total, "
            "has_more, limit, offset}. NOTE: these are scanner rows — "
            "each `id` is a vulnerability id, NOT a project-Finding id, so do not "
            "pass it to assist_get_finding. The triaged project Findings (the spine "
            "assist_list_findings / assist_get_finding work on) are a separate set. "
            "Each row says which finding covers its issue, if any: `finding_id` / "
            "`finding_status` (the issue's), `finding_on_this_host` (false = the "
            "finding covers other hosts only, so this row is still unjudged here) "
            "and `finding_endpoint_status` (this host's own state on it). To read one "
            "issue's rows only, narrow with cve, plugin_id or search (title text)."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/vulnerabilities",
        "defaults": {"limit": 50},
        "params": {
            "host_id": HOST_ID,
            "search": "Only rows whose title contains this text.",
        },
    },
    "assist_list_findings": {
        "description": (
            "Findings across the WHOLE project — the spine an analyst reasons "
            "about, not one host's slice. Filter by severity, status, source, "
            "owner (`me` or a username), `unowned=true` (findings nobody owns), "
            "host_id, or a title substring. Returns `total` and a "
            "`severity_counts` breakdown for the filter you asked about, so "
            "\"how many criticals are open?\" is one call. A finding can span "
            "many hosts — `host_count` is distinct addresses and `endpoint_count` "
            "affected rows (named endpoints on one IP count once as a host); "
            "counting result rows is neither. Omit `status` (or pass 'all') for "
            "every status — the valid values are what assist_get_vocabulary "
            "returns. These are triaged project Findings, a different set from "
            "the raw scanner rows assist_get_host_vulnerabilities returns; the "
            "ids do not cross between the two."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/findings",
        "defaults": {"limit": 25},
        "params": {
            "status": {
                "enum": [
                    "open", "confirmed", "false_positive", "accepted_risk",
                    "remediated", "retest", "active", "resolved", "all",
                ],
                "description": (
                    "A status, or a group: active (still being worked) / resolved "
                    "(every terminal status). 'all' or omitted for every status. "
                    "Any other value is a 422."
                ),
            },
            "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
            "host_id": {"minimum": 1},
            "unowned": "Only findings with no owner.",
            "owner": "Username, or 'me' for this session's operator.",
        },
    },
    "assist_list_host_web_interfaces": {
        "description": (
            "Every web interface observed on one host, as a page — the "
            "continuation for assist_get_host, whose web_interfaces list is capped "
            "at 10 (its web_interfaces_truncated says when). Each item carries the "
            "URL, FQDN, title, server header, technologies, a "
            "screenshot_download_path when EyeWitness captured one, and the "
            "certificate / TLS facts (cert_not_after, cert_self_signed, cert "
            "organisations, tls_weak_protocol — null means the tool did not "
            "report it, not that it is fine; an expired certificate is the "
            "check:tls_cert_expired observation), plus what the web panel reads "
            "from the tool's TLS record: tls_version, cert_issuer, "
            "cert_subject_cn, cert_sans (first 20; cert_san_total). Read "
            "has_more and page with offset; total is the whole record (v2.343.3)."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/web-interfaces",
        "params": {"host_id": HOST_ID},
    },
    "assist_list_host_access": {
        "description": (
            "Every NetExec / SMBMap result on one host, as a page (v2.418.0): "
            "what BlueStick read from each tool line — login outcome, username, "
            "local admin, SMBv1, writable share, shares — beside the line itself "
            "(raw_output; raw_output_truncated when the import cut it). Compare "
            "the two to check a parse; credentials appear as the tool printed "
            "them. Read has_more and page with offset."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/access",
        "params": {"host_id": HOST_ID},
    },
    "assist_list_uninterpreted_lines": {
        "description": (
            "Imports whose parser did not interpret every line, newest first, "
            "with those lines as REDACTED shapes and counts (v2.418.0): kind "
            "`dropped` (not in the inventory), `text_only` (kept as the tool's "
            "line, nothing read from it), `module_as_login` / `module_as_text` "
            "(an nxc module's result). Values are placeholders (<IP>, <HOST>, "
            "<VALUE>, <CREDENTIAL>…). Not an ingestion issue — the data that was "
            "read is in the project. Pass job_id for one import."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/uninterpreted-lines",
    },
    "assist_get_host_notes": {
        "description": (
            "What the team has already written about this host. Read this "
            "BEFORE adding a note — a colleague may have recorded the same "
            "observation an hour ago — and before answering \"what do we know "
            "about X\", where the answer often lives in a note rather than in "
            "scan data. Notes carry who wrote them and whether an agent did, the "
            "thread (parent_id / thread_root_id), type, whether it is pinned, the "
            "finding an older thread was promoted to, and attachments as download "
            "references. Paged, newest first: read `total` and `has_more`, and pass "
            "`offset` to continue — a page is not the whole record."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/notes",
        "params": {"host_id": HOST_ID},
    },
    "assist_get_vocabulary": {
        "description": (
            "The values THIS project uses for tag:, label:, site:, scope: and "
            "assigned: — plus the valid finding statuses and severities. Call "
            "it before writing a query with any of those predicates: a guessed "
            "tag doesn't error, it returns zero hosts, and \"nothing is tagged "
            "production\" is a confidently wrong answer to what was really "
            "\"what are the tags called here?\"."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/vocabulary",
    },
    "assist_get_coverage": {
        "description": (
            "How much of this project has actually been assessed, per domain "
            "(port discovery, service detection, vulnerability assessment, web, "
            "TLS…). Every other tool reports what WAS found; this is what stops "
            "\"no critical findings\" being reported as \"no critical "
            "exposure\". Cite it whenever a report or an answer implies "
            "completeness. (The three SCOPE states — hosts in subnet scope, in "
            "name scope only, outside scope — are not here: count them with "
            "assist_count_hosts q=scope:subnet / scope:name / scope:none; they add "
            "up to the host total.) The vuln_assessment domain carries `credentialed` "
            "{credentialed, not_credentialed, credentials_not_stated} — of the "
            "assessed hosts, how many a scanner logged in to; a clean result "
            "from a scan that did not authenticate is weaker evidence. List "
            "each with assist_list_hosts q=vulnscan:credentialed | "
            "uncredentialed | unstated."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/coverage",
    },
    "assist_list_segments": {
        "description": (
            "Per-subnet rollup, worst-first: exposure (active findings by "
            "severity, tier-weighted), neglect (unowned findings, unreviewed "
            "and stale hosts), hygiene (end-of-life OS, certificate problems, "
            "weak auth, risky services) and a recommended next action. Use it "
            "for \"which segment is worst?\" and \"what is wrong with this "
            "subnet?\" instead of counting per subnet yourself. Same numbers "
            "as the Subnet Insights page. `no_coverage` marks a scoped range "
            "where nothing was ever discovered — a scanning gap, NOT a clean "
            "subnet. `adopted=false` means the project has no scoped subnets, "
            "so this is not assessable — not that there are no problems. "
            "Compare `total` with the number of subnets returned: the page is "
            "capped, and you are seeing the worst ones, not all of them."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/segments",
    },
    "assist_get_posture": {
        "description": (
            "The project's overall security condition — the headline label, "
            "the plain-language conclusion, the reasons behind it, the "
            "prioritised next actions, exposure/ownership/review coverage and "
            "finding disposition. Start here for \"where is this project?\" and "
            "build a report's executive summary from it rather than inventing "
            "a judgement from counts. Note that label='insufficient_evidence' "
            "means the estate has NOT been assessed enough to judge — it is "
            "not a clean bill of health, and reporting it as one is wrong. "
            "scanner_observations is the raw scanner rows (not findings): total "
            "(informational excluded, as the page states it — the same number as "
            "headline.detected_exposure.vuln_count), informational, by_severity, "
            "and hosts_by_severity — 'how many criticals?' has four "
            "answers (critical findings, critical scanner issues, critical scanner "
            "rows, hosts carrying one): say which you are giving."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/posture",
    },
    "assist_get_patterns": {
        "description": (
            "Cross-sectional analysis of the estate: blind spots (conditions "
            "spanning the whole estate, e.g. 'nearly everything is on an "
            "end-of-life OS'), segment outliers (subnets whose issue density "
            "is an outlier against the estate median — the 'this subnet looks "
            "worse than the rest' claim, with the ratio behind it), each "
            "condition's spread, per-family root-cause hypotheses with a "
            "recommended control, and per-subnet diagnostic profiles. This is "
            "what turns an inventory into an assessment. IMPORTANT: it is "
            "comparison ACROSS the estate, not change over time — do not "
            "describe these as trends or say anything got better or worse. "
            "adopted=false means no scoped subnets, so the analysis cannot "
            "run: report 'not assessable', never 'no patterns found'."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/patterns",
    },
    # v2.428.0 — Operations, Evidence gaps and scan compare, each the SAME
    # service its page uses (agent_assist_operations.py).
    "assist_get_workbench": {
        "description": (
            "Your operator's Operations page, as they see it. my_work is what is "
            "waiting on them BY KIND — answer 'how much is waiting on me?' with the "
            "kinds apart, as the page does: findings_to_decide (under investigation, "
            "or a proposal waits for their decision), findings_to_write (only "
            "required report text missing; the two add up to findings_needing_me), "
            "tests_assigned; then what they hold in review: hosts_in_review and "
            "tests_on_hosts_in_review (on those hosts, not assigned to them). "
            "to_claim is shared work; total is those parts added up. "
            "my_queue: hosts they are reviewing. my_tasks: host tests to do "
            "(group_counts counts each test once: assigned / in_review / triage). "
            "my_findings: findings they own that NEED them — each row's needs "
            "says why (under investigation, required report text missing, a "
            "proposal to decide); a confirmed, written-up finding is not listed. "
            "followups is 'Changed since review' — hosts YOUR OPERATOR reviewed "
            "(never a teammate's review) that gained open ports or critical/high "
            "observations after that review, or that they concluded 'needs more "
            "evidence' (total = hosts; the same hosts are q='follow:revisit'; "
            "every team member's are q='has:changed_since_review OR "
            "conclusion:needs_evidence'). There are no project-wide measures "
            "here: for hosts tested or untouched critical exposure read the "
            "total of assist_list_hosts q='has:tested' / q='has:untouched "
            "has:critical' (by address block: assist_get_terrain). There is no "
            "team roster either: what the team is already reviewing is "
            "assist_list_hosts q='follow:in_review' (any teammate's). Also "
            "blockers, and since_last_visit — scans, new and "
            "changed hosts, new critical/high scanner observations since they "
            "last marked Operations seen. Answers 'what's mine?' and 'what "
            "changed since I was last here?'. Reading never marks anything seen. "
            "*_unavailable=true means that section could not be computed — say "
            "so, never 'nothing' or 0. The lists here are PREVIEWS "
            "(my_queue 10, tasks 10 per group, notes/findings/follow-ups 15) — "
            "the page itself shows one full list at a time, as tabs: "
            "use the section's own count — my_work, in_review_count, total_open, "
            "followups.total — for 'how many', never the list length. For a WHOLE "
            "list: hosts in review = assist_list_hosts q='follow:mine'; changed "
            "since review = q='follow:revisit'; tests assigned = host_tests_list "
            "mine=true active_only=true; tests on hosts in review = host_tests_list "
            "q='follow:mine' active_only=true (every test to do on those hosts — "
            "group_counts.in_review leaves out the ones assigned to the operator, "
            "which it counts under assigned). Findings that need the operator have "
            "no whole-list read beyond this preview (assist_list_findings owner=me "
            "lists every finding they own, needing them or not). Each my_tasks row "
            "carries its tool. "
            "blockers.failed_import_count counts failed imports nobody has "
            "dismissed and no later clean import superseded, so it is smaller "
            "than assist_list_ingestion_issues' failed. investigate is null here "
            "because the untouched queue is not embedded — null is not an empty "
            "queue; read it with assist_list_worth_a_look."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench",
        # The untouched-hosts queue has its own tool (assist_list_worth_a_look).
        "hidden": ["include_investigate"],
    },
    "assist_list_worth_a_look": {
        "description": (
            "Operations' 'Untouched, with a reason' queue (it was 'Worth a look'): "
            "hosts NOBODY has touched (no review, "
            "note, host test, evidence or finding) that carry an observed weakness or a "
            "relevant change, each with its reasons and next action, in stated "
            "tier order (1 exploitable critical, 2 critical vulnerability, 3 "
            "exploit available, 4 high-value service new/changed, 5 scans "
            "disagree). queue_total and tier_counts cover the whole queue. The "
            "answer to 'what should we look at next?'. Tier 5 means scans "
            "recorded different values for the host: every recorded disagreement "
            "carries resolved_at (when BlueStick picked the value it shows), so "
            "'resolved' is not 'settled' — a person should still look."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench/investigate",
    },
    "assist_get_terrain": {
        "description": (
            "The address terrain (Posture's 'Where the team has been') as numbers: per /24 (IPv6 /64), hosts tested / "
            "planned / worked / untouched (exclusive, adding up to hosts), plus "
            "critical and critical_untouched. Answers 'which ranges has nobody "
            "touched?'. sort=untouched or critical_untouched puts the neglected "
            "blocks first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench/terrain",
    },
    "assist_list_evidence_gaps": {
        "description": (
            "The Evidence page's gap list: hosts an assessment domain applies to "
            "that carry no evidence in it, the open ports that made each eligible, "
            "and the step that closes the gap. domain is a key from "
            "assist_get_coverage (e.g. vuln_assessment, web_tls, auth_smb_ad); "
            "segment (optional) is matrix.segments[].key from the same call — a "
            "site id, a subnet key or 'unmapped', NOT a CIDR (a wrong one answers "
            "404 listing the accepted keys). total "
            "is exact. Respect scope_caution: hosts outside the declared scope "
            "must be confirmed in scope first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/evidence/gaps",
        "defaults": {"limit": 50},
        "params": {
            "domain": {
                "enum": [
                    "port_discovery", "service_detection", "os_detection",
                    "vuln_assessment", "web_tls", "auth_smb_ad", "validation",
                ],
            },
        },
    },
    "assist_compare_scans": {
        "description": (
            "What changed between two scans (ids from assist_list_scans): hosts "
            "new / gone / changed state, ports newly open, closed (observed "
            "not-open) and not_observed (the later scan never looked — NOT "
            "remediation). counts are exact; lists are capped at limit. Compare "
            "scans of the same targets and tool, or the difference is coverage."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scans/compare",
        "defaults": {"limit": 100},
        "params": {"a": "Baseline scan id", "b": "Later scan id"},
    },
    "assist_list_scan_hosts": {
        "description": (
            "The hosts ONE scan observed, as it observed them (the scan page's "
            "'As scanned' table; scan_id from assist_list_scans): state and "
            "hostname at scan, whether the scan first discovered the host, the "
            "ports it saw (at most 50 listed per host; the counts are exact) and "
            "credentialed — whether the scan authenticated to the host: true / "
            "false when the scanner said so (Nessus), null when it did not say. "
            "Null is 'not stated', never 'no'. Read total and has_more; page with skip."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scans/{scan_id}/hosts",
        "defaults": {"limit": 100},
        "params": {
            "scan_id": "Scan id (assist_list_scans)",
            "state": "Only hosts the scan observed in this state (up, down…).",
            "search": "Address or hostname at scan.",
        },
    },
    "assist_list_ingestion_issues": {
        "description": (
            "Uploads that failed, are still in flight, or parsed but dropped "
            "rows. CHECK THIS BEFORE REPORTING THAT SOMETHING IS ABSENT: 'no "
            "web servers in that range' and 'the httpx upload failed to parse' "
            "look identical from every other tool, and only one of them is a "
            "finding about the network. Counts as the Ingestion Results page "
            "states them: failed (an import went wrong), expired (a staged upload "
            "nobody started — never imported, nothing failed), discarded, and "
            "needs_attention (failed or partial, not dismissed, not replaced by a "
            "later import — say this one for 'how many imports need attention'). "
            "kind=failed means nothing from that "
            "file is in the project; kind=expired the same, because it was never "
            "started; kind=degraded means the file IS in the "
            "project but rows were dropped, so counts drawn from it are "
            "undercounts (everything else reports that job as completed); "
            "queued/processing mean data is still arriving. If has_issues is "
            "false, an empty result elsewhere is a real absence. Parse-error "
            "counts: unresolved_parse_errors_total is the project's number; "
            "unresolved_parse_errors is only the part with no import job (the "
            "rest is already under failed) — never report it as the total."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/ingestion-issues",
    },
    # v2.428.0 — images can be read inline with assist_get_image (below); the
    # download paths assist_get_finding and assist_get_host hand out stay, for
    # saving a file beside a report.
    "assist_get_finding": {
        "description": (
            "One finding with the evidence behind it — the note a human wrote "
            "to justify promoting it, the replies on that note's thread "
            "(`evidence_thread`), the finding's own comment thread, the affected "
            "hosts (with `name_id`/`fqdn` when a row is a named endpoint; "
            "`host_count` is distinct addresses, `endpoint_count` rows; each "
            "row's `finding_host_id` is what propose_endpoint_status and "
            "record_evidence take), and "
            "references to any attached screenshots. Every note says whether a "
            "person or an agent wrote it (`actor_type`). Use this when writing "
            "a finding up: assist_list_findings gives you titles and "
            "severities, this gives you what to cite. Screenshots come back as "
            "references (filename, size, download_path): look at one with "
            "assist_get_image, or save it beside a report from its download_path "
            "with the session's API key. scanner_evidence and evidence_records say "
            "whether a claim rests on a scanner's output or on a command a "
            "tester actually ran; state which, they are different assertions. "
            "Also: `report_text` (what the client report says — description, "
            "impact, recommendation, references, steps to reproduce, CVSS vector "
            "and score), `endpoint_status_counts` (per-host state), and "
            "`status_history` (who changed the status, when, from → to, and why). "
            "`images` lists the finding's images as the report sees them: `id`, "
            "`caption`, `in_report` (ticked for the report) and `placed_in` — the "
            "report-text fields whose Markdown places the image with "
            "`![caption](evidence:<id>)`; a ticked image no field places prints "
            "under Evidence. "
            "scanner_evidence lists at most 100 scanner rows: "
            "scanner_evidence_total is how many there are, and "
            "scanner_evidence_truncated says the list was cut."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/findings/{finding_id}",
        "params": {
            "finding_id": (
                "Project-Finding id, from assist_list_findings — NOT a "
                "per-host vulnerability id from assist_get_host_vulnerabilities."
            ),
        },
    },
    # v2.428.0 — the Findings hub's scanner-observations view and the Reports
    # page (agent_assist_reporting.py), and images inline.
    "assist_list_scanner_observations": {
        "description": (
            "Scanner results grouped by ISSUE across the project: title, severity, "
            "kind, sources, host_count and judged_host_count (hosts a finding "
            "already covers), plus the covering finding. Most severe first; "
            "sort=hosts puts the most widespread first ('which issues are on the "
            "most hosts?'). An issue is listed until EVERY host carrying it is "
            "judged — a row with judged_host_count > 0 is partly judged; "
            "include_judged adds the fully judged ones. total counts all matching "
            "issues; read has_more and page with offset. severity narrows to one "
            "severity. assist_list_observation_hosts lists one issue's hosts."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scanner-observations",
        "defaults": {"limit": 25},
        "params": {
            "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
            "kind": {"enum": ["misconfiguration", "vulnerability", "informational"]},
            "skip": "Older name for offset; still accepted.",
        },
    },
    "assist_list_observation_hosts": {
        "description": (
            "The hosts carrying one scanner issue (issue_key from "
            "assist_list_scanner_observations), by address: ports, severity, and "
            "whether a finding covers it on that host (judged, endpoint_status). "
            "total is every host carrying the issue; read has_more and page with offset."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scanner-observations/hosts",
        "defaults": {"limit": 100},
    },
    "assist_list_client_reports": {
        "description": (
            "The project's client reports (the Reports page): drafts, then issued "
            "reports by number — kind (full/addendum), status (draft, issued, "
            "superseded), baseline / revision_of / superseded_by, issued_at, "
            "files. Needs an auditor operator. assist_get_client_report for one."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/client-reports",
    },
    "assist_get_client_report": {
        "description": (
            "One client report and what it says: engagement details, executive "
            "summary, counts, and every finding as the report states it (ref, "
            "report text, affected endpoints, evidence attachment ids, and "
            "confirmations — the test results it prints as how the finding was "
            "confirmed, with confirmations_omitted for those left out). An issued "
            "report is its frozen text (content_source issued_snapshot); a draft "
            "is what it would say now (draft_live). For 'what did we tell the "
            "client', read the latest issued one. Files carry a download_path. "
            "In an addendum each finding's change is new, new_hosts or "
            "severity_changed (previous_severity = what the baseline reported), "
            "and delta counts them (findings_with_changed_severity). confirmations "
            "and summary.evidence_records are only what THIS report's template "
            "prints (evidence_records_not_printed counts the rest; "
            "agent_evidence_records those an agent recorded). Each finding's "
            "images[] is every image ticked for the report, with placed_in, "
            "printed and printed_in; summary.images_printed / images_trailing / "
            "images_not_printed (with images_not_printed_reasons) add up to "
            "summary.images, and template_images is what the template declares "
            "it prints. Those are null when printing could not be measured."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/client-reports/{report_id}",
    },
    "assist_get_image": {
        "description": (
            "Look at one image: a note/finding attachment (attachment_id, from "
            "assist_get_finding or assist_get_client_report) or a web interface's "
            "EyeWitness screenshot (interface_id, from assist_get_host). Returns "
            "the image inline, up to 2 MB; larger ones answer with the download "
            "path. Pass exactly one id."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/attachments/{attachment_id}",
        "path_alternatives": {
            "interface_id": "/api/v1/agent/assist/web-interfaces/{interface_id}/screenshot",
        },
        "result": "image",
    },
    "assist_list_recent_notes": {
        "description": (
            "Recent notes across the whole project, newest first — what the "
            "team has been discussing, as opposed to what a scanner found. "
            "Filter by author ('me' or a username). Each "
            "note names its `target` ({kind: host/port/finding/scan/scope/"
            "project, id, label}) and carries the same thread, "
            "label and attachment fields as assist_get_host_notes. Notes are "
            "discussion, not a work list: outstanding work is host tests "
            "(host_tests_list) and findings."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/notes",
        "defaults": {"limit": 25},
    },
    "assist_list_scopes": {
        "description": (
            "List this project's scopes: subnet CIDRs and declared domains "
            "(each capped at 100 with *_total / *_truncated). Name scope is "
            "independent of subnet scope — a name in scope does not put the "
            "address it resolves to in scope, and vice versa. "
            "names_in_scope_total is the deduplicated count of inventory names "
            "the domain entries cover."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scopes",
    },
    "assist_list_names": {
        "description": (
            "List the project's named assets (FQDNs) with in_scope, the "
            "addresses each currently resolves to (derived from the latest "
            "A/AAAA observations, never stored) and its evidence sources. "
            "in_scope means a declared domain covers the name; it does NOT make "
            "the address subnet-in-scope. The actionable queue is "
            "in_scope=true&resolved=false — approved names no upload has ever "
            "resolved. A name whose address is shared with other names (load "
            "balancer / vhost) must be tested by name, not by IP. host_id lists "
            "the names that CURRENTLY resolve to one host's address (latest "
            "A/AAAA); assist_get_host's names is wider — every name ever SEEN at "
            "the address (PTR, certificate, HTTP, scanner), so a PTR-only name "
            "appears there and not here."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/names",
        "params": {
            "q": "Case-insensitive substring on the FQDN.",
            "in_scope": "Only names a declared domain covers (true) / does not (false).",
            "resolved": "Only names with (true) / without (false) a current A/AAAA answer.",
            "host_id": "Only names currently resolving to this host's address.",
            "kind": {"enum": ["fqdn", "wildcard"]},
        },
    },
    "assist_list_scans": {
        "description": (
            "List the scans ingested into this project (most recent first). Each "
            "carries ingestion_job_id, the import that produced it — the job_id "
            "assist_list_uninterpreted_lines takes (a scan id is not a job id). "
            "tool narrows to one tool's scans, as the Scans page's chips do — "
            "'the last two nmap scans' is tool=nmap, limit=2. Returns {items, "
            "total, has_more, limit, offset}: `total` is the whole count for the "
            "filter, so 'how many nmap scans?' is one call with limit=1."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scans",
        "defaults": {"limit": 50},
        "params": {
            "offset": "Skip this many (newest first). Page while has_more is true.",
            "tool": "A tool name (nmap, nessus, netexec…) or scan type.",
        },
    },
    "assist_session_info": {
        "description": (
            "This unified session's inventory context: bound project, purpose, status, and the "
            "operator you act on behalf of — who `assigned:me` refers to. For whether "
            "you may write, call agent_identity and read `can_write_project_data`; "
            "this response does not carry it."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/session",
    },
    # --- evidence and proposals (v2.436.0) ---
    # What the agent DID is recorded directly; a change to what the team
    # CONCLUDED (report text, a finding, an observation's promotion or
    # dismissal, an endpoint's status) is a proposal a person decides.
    "record_evidence": {
        "description": (
            "Record what you ran against a host and what came back — the tool, the "
            "command verbatim, the outcome, a one-line summary, and the raw output "
            "(kept with the record). An MCP request is limited to 1 MiB in all, "
            "so send output larger than that with curl — POST /agent/evidence, "
            "same fields, up to 5 MB of raw_output. Text only: you cannot upload an "
            "image — save a screenshot in your working directory, name the file in "
            "`summary`, and ask the operator to attach it to the finding. Pass `host_test_id` (with a "
            "`request_key`) when it answers a proposed test. Recorded as it "
            "happened and never changed; cite it from proposals (evidence_ids)."
        ),
        "method": "POST",
        "path": "/api/v1/agent/evidence",
        "additive": True,
        "idempotent": False,
        "params": {
            "host_id": HOST_ID,
            "finding_host_id": "The finding endpoint (vhost) it ran against, if any.",
            "host_test_id": "The host test this answers, if any (from host_tests_list). Needs request_key.",
            "request_key": "Your own stable key for this record; re-sending it returns the record already stored (a safe retry). Required with host_test_id.",
            "tool": "The tool or method (nmap, curl, a script…).",
            # The endpoint sets no schema length on purpose (a schema 422
            # echoes the input back); its service answers 413 past 5 MB.
            "raw_output": {"maxLength": 5242880, "description": "The tool's output (up to 5 MB)."},
            "agent_model": AGENT_MODEL,
        },
    },
    "list_evidence": {
        "description": (
            "Evidence records, newest first, with a 2 KB preview of each raw output. "
            "The full output is a file: curl GET /agent/evidence/{id}/raw with your key."
        ),
        "method": "GET",
        "path": "/api/v1/agent/evidence",
        "params": {
            "host_test_id": "Only the evidence that answers this host test.",
            "agent_session_id": "Only what this agent session recorded (yours is on agent_identity).",
        },
    },
    "propose_finding_text": {
        "description": (
            "Propose report text for a finding — a person accepts (then may edit) or "
            "rejects it; nothing changes until then. EACH FIELD'S VALUE IS THE COMPLETE "
            "REPLACEMENT for that section, written as it will read in the client report: "
            "on accept it overwrites the section word for word. Never a critique, a list "
            "of suggestions, a diff or notes to the author — put why you changed it in "
            "`rationale`. Propose only the sections you would change. THE READER HAS NEVER "
            "SEEN BLUESTICK: no record numbers or ids (\"Finding #277\", \"evidence record "
            "57\"), no mention of BlueStick or of how the text was produced — name a finding "
            "by its title and a system by its address; a section naming a record is refused "
            "(422). NOT ENOUGH TO GO ON IS "
            "A VALID ANSWER: write a section only from the finding's data and recorded "
            "evidence; when that is too thin, do not propose the section and never fill it "
            "with a guess or a placeholder (\"TBD\", \"[needs confirmation]\") — say what is "
            "missing, and what would let you write it, in `rationale` and to your operator. "
            "One proposal per "
            "field; several may stand side by side (e.g. from different models). Fields: "
            "description, impact, recommendation, references, steps_to_reproduce "
            "(Markdown), cvss_vector. IMAGES: a section may hold `![caption](evidence:<id>)`, "
            "which prints that image of the finding there. Read `images` on assist_get_finding "
            "first: KEEP the references a section already holds when you rewrite it (dropping "
            "one moves the image back under Evidence), and reference only ids listed there — "
            "any other id is refused (422). You cannot tick an image \"In report\": a proposal "
            "placing one that is not ticked is not accepted until a person ticks it."
        ),
        "method": "POST",
        "path": "/api/v1/agent/proposals/finding-text",
        "additive": True,
        "idempotent": False,
        "params": {
            "fields": (
                "Field name → the section's complete new text, report-ready (it replaces "
                "the section on accept) — not comments about the current text."
            ),
            "rationale": (
                "Why — what you changed and why, what the reviewer should check. Your critique goes here."
            ),
            "agent_model": AGENT_MODEL,
        },
    },
    "propose_finding": {
        "description": (
            "Propose a new finding on one or more hosts — a person accepts or rejects it. "
            "Cite the evidence records that support it. `report_text` holds only the "
            "sections the evidence supports — leave out one you cannot support (never a "
            "guess or a placeholder) and say what is missing in `rationale`. Its reader "
            "has never seen BlueStick: no record numbers or ids in it (\"Finding #277\"), "
            "no mention of BlueStick — a section naming a record is refused (422)."
        ),
        "method": "POST",
        "path": "/api/v1/agent/proposals/finding",
        "additive": True,
        "idempotent": False,
        "params": {"agent_model": AGENT_MODEL},
    },
    "propose_observation": {
        "description": (
            "Propose promoting a scanner observation to a confirmed finding (or joining "
            "its finding), or dismissing it as a false positive — a person decides. "
            "scope: host (this host only; the default for dismiss) or issue (every host "
            "carrying it; the default for promote)."
        ),
        "method": "POST",
        "path": "/api/v1/agent/proposals/observation",
        "additive": True,
        "idempotent": False,
        "params": {
            "vulnerability_id": "The scanner observation's id.",
            "agent_model": AGENT_MODEL,
        },
    },
    "propose_endpoint_status": {
        "description": (
            "Propose a finding endpoint's status — open, remediated, retest or "
            "false_positive (this endpoint only) — for a person to decide."
        ),
        "method": "POST",
        "path": "/api/v1/agent/proposals/endpoint-status",
        "additive": True,
        "idempotent": False,
        "params": {"agent_model": AGENT_MODEL},
    },
    "list_proposals": {
        "description": "Proposals in this project and what happened to them (pending / accepted / rejected / superseded).",
        "method": "GET",
        "path": "/api/v1/agent/proposals",
    },
    # --- assist writes (allowed iff the operator's project role permits writes) ---
    "assist_add_note": {
        "description": (
            "Add a note to a host. Writes project data, so it succeeds only if the "
            "operator who started your session may write to this project — check "
            "`can_write_project_data` on agent_identity rather than probing. "
            "A note is discussion for the team: context, a question, a handoff. It "
            "has no status and is not how work is recorded — a check you ran is "
            "evidence (record_evidence), a check to run is a host test "
            "(host_tests_propose). Notes are stamped agent-authored and appear in "
            "the operator's UI and in the host inventory's JSON download; mark inferences as "
            "inferences."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/notes",
        "additive": True,
        "params": {"host_id": HOST_ID, "body": "Note text."},
    },
    "assist_set_follow": {
        "description": (
            "Set — or clear — a host's review status. Writes project data (see "
            "agent_identity's `can_write_project_data`). Pass `none` to remove "
            "your follow entirely, the inverse of setting one (use it to undo a "
            "status you set). Do NOT "
            "mark a host `reviewed` on your own initiative — reviewed is a human "
            "judgement with client-reportable weight; confirm with the operator first."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/follow",
        "params": {
            "host_id": HOST_ID,
            "status": {
                "enum": ["in_review", "reviewed", "none"],
                "description": "in_review / reviewed, or `none` to clear the follow.",
            },
        },
    },
    "assist_patch_host": {
        "description": (
            "Correct a host's hostname and/or OS after investigation. Writes project "
            "data (see agent_identity's `can_write_project_data`). "
            "Only these two operator-curated fields are editable "
            "— scan-derived facts (ports, services, vulns) are never mutated here. Send "
            "just the field you're fixing; sending neither is refused with a 400."
        ),
        "method": "PATCH",
        "path": "/api/v1/agent/hosts/{host_id}",
        # "Send at least one field" is stated in the description and enforced
        # by the endpoint, not encoded as a top-level `anyOf`: some hosts
        # (Codex among them) then present the whole tool as an opaque object
        # union instead of typed parameters.
        "params": {"host_id": HOST_ID},
    },
    # -----------------------------------------------------------------------
    # Scope reads — the agent reads a scope, runs its own tools, and uploads
    # the output.  The bulk host downloads stay curl (see the module docstring).
    # -----------------------------------------------------------------------
    "scope_list_subnets": {
        "description": (
            "The paginated CIDR list for a scope. Page until `subnets` comes back "
            "empty. The full host download is a curl, not a tool."
        ),
        "method": "GET",
        "path": "/api/v1/agent/scopes/{scope_id}/subnets",
        "defaults": {"limit": 100},
        "params": {"scope_id": SCOPE_ID},
    },
    "scope_list_domains": {
        "description": (
            "The paginated list of domains declared in scope ({domain, "
            "include_subdomains}) — the names you may resolve or probe without "
            "asking. A name in scope does not put the address it resolves to in "
            "subnet scope."
        ),
        "method": "GET",
        "path": "/api/v1/agent/scopes/{scope_id}/domains",
        "defaults": {"limit": 100},
        "params": {"scope_id": SCOPE_ID},
    },
    "get_upload_job": {
        "description": (
            "Poll an upload's parse status. Upload itself is a file POST you run with "
            "curl (POST /agent/uploads, see the server instructions); this is how you "
            "find out whether it parsed, and what it produced."
        ),
        "method": "GET",
        "path": "/api/v1/agent/uploads/{job_id}",
        "params": {"job_id": {"minimum": 1, "description": "Job id returned by the upload."}},
    },
}



# ---------------------------------------------------------------------------
# Host tests (v2.442.0) — the tests proposed on each host, shown on the host's
# page.  They replace test plans: no plan to register, no run to open, no
# approval.
# ---------------------------------------------------------------------------
_HOST_TEST_ID = {
    "minimum": 1,
    "description": "The host test's id (from host_tests_list or host_tests_propose).",
}

_AUTHORED["host_tests_propose"] = {
    "description": (
        "Propose tests on hosts — up to 200 in one call, each a single test on "
        "one host: the tool, what it establishes, the exact command ({ip} / "
        "{fqdn} placeholders allowed), the rationale and a priority. They "
        "appear on each host's page at once; there is no approval step and no "
        "plan to register. `label` groups the tests of one request (e.g. "
        "'SMB review 2026-10-01'). When a test would confirm or rule out one "
        "scanner observation, pass that observation's `vulnerability_id` (it "
        "must be on the same host): the test is shown on that weakness and "
        "its result settles it. Which hosts and which tests is the "
        "operator's request, not yours to widen. Every test needs its own "
        "`request_key`; re-sending the same key with the same content returns "
        "the existing test (a safe retry), with different content is a 409. "
        "The whole batch is validated before anything is written."
    ),
    "method": "POST",
    "path": "/api/v1/agent/host-tests",
    "additive": True,
}
_AUTHORED["host_tests_list"] = {
    "description": (
        "The project's host tests, each with its status (proposed, in_progress, "
        "done, dismissed), revision and how many evidence records answer it. "
        "Filter by host_id, status, label, assignee or the session that "
        "proposed them; `mine` is tests assigned to your operator; "
        "`active_only` keeps proposed and in_progress. Read this before "
        "proposing, so you do not duplicate a test that is already there."
    ),
    "method": "GET",
    "path": "/api/v1/agent/host-tests",
    "params": {
        "label": "Exact label.",
        "q": "A Hosts query (the same language as assist_list_hosts' q, e.g. has:critical): tests on the hosts it matches.",
    },
}
_AUTHORED["host_tests_get"] = {
    "description": "One host test: its command, rationale, status, revision and evidence count.",
    "method": "GET",
    "path": "/api/v1/agent/host-tests/{test_id}",
    "params": {"test_id": _HOST_TEST_ID},
}
_AUTHORED["host_tests_update"] = {
    "description": (
        "Change a host test's status (in_progress when you start it, done when "
        "it is finished, dismissed with `dismissed_reason` when it should not "
        "be run), its assignee or its `tester_summary`. Pass the "
        "`expected_revision` you read; a 409 means someone changed it since — "
        "read it again and decide. What the test produced is not written here: "
        "record it with record_evidence(host_test_id=…), which is what marks "
        "the host tested."
    ),
    "method": "PATCH",
    "path": "/api/v1/agent/host-tests/{test_id}",
    "params": {"test_id": _HOST_TEST_ID},
}

# v2.457.0 — remediation tracking: who was told about a finding on a host and
# where the fix stands.  The page's contract (`endpoints/remediation.py`).

_AUTHORED["remediation_list"] = {
    "description": (
        "Remediation tracking, one row per finding ON A HOST: the contact who "
        "was told (`contact_email`, `contact_name`), `notified_on`, the "
        "contact's progress `status` (open, closed, deferred) and `closed_on`. "
        "`closed` means the contact REPORTED it fixed — say \"reported "
        "fixed\", never \"closed\" or \"remediated\", when you describe it. "
        "The same finding can have a different contact and status on each "
        "host. This is the client's progress as a project admin recorded it — "
        "it is NOT the assessor's conclusion (`finding_status`, "
        "`endpoint_status` on the same row, where `remediated` means the team "
        "concluded it is fixed), and neither moves the other. Where the two "
        "disagree the row says so in `verification`: "
        "`reported_fixed_not_retested` (the record is closed, the endpoint is "
        "not remediated and not a false positive) or `remediated_record_open` "
        "(the endpoint is remediated, the record is open, deferred or was "
        "never written); null where they agree. `verification_counts` counts "
        "both over the selection and `verification=` lists exactly one. "
        "Each row also carries where it stands against its DEADLINE: `state` "
        "(overdue, due_soon, on_track, not_assigned — nobody was given a date, "
        "so no clock runs —, no_deadline — the severity has no timeline —, "
        "deferred, closed), `due_on`, `days_left` (negative once overdue), "
        "`closed_days_late` and `last_follow_up_on`. The deadline is the "
        "assigned date (`notified_on`) plus this installation's days for the "
        "finding's severity; it is derived, never set. "
        "Filter by `state` (one or several), `status`, `severity`, `contact` "
        "(part of an address or a name), `unassigned` (no contact yet), "
        "`host_id` or `finding_id`; `state_counts` and `status_counts` cover "
        "the whole selection and `total` the rows returned by the filter. "
        "Over the same selection: `severity_counts` (overdue and due soon per "
        "severity), `overdue_ages` (overdue rows by days past the deadline: "
        "1-7, 8-30, 31-90, 90+; filter with `overdue_band`) and "
        "`not_followed_up` (at-risk rows nobody followed up in "
        "`not_followed_up_days`; list them with `no_follow_up_days`). Each row "
        "has a `team` (the group that owns the fix; filter with `team`). "
        "`group=due` orders by deadline, the longest overdue first. Each "
        "row's `finding_host_id` is what remediation_apply takes. Needs an "
        "operator who is a project auditor. Answers 404 on an installation "
        "that has not turned remediation tracking on."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation",
    "params": {
        "state": "Where the row stands against its deadline; several are OR-ed.",
        "overdue_band": "Only overdue rows this many days past their deadline.",
        "group": "The order of the rows (default host; due = by deadline).",
    },
}
_AUTHORED["remediation_contacts"] = {
    "description": (
        "The remediation contacts in use in this project, the one with the "
        "most overdue first: each with how many findings on hosts they have "
        "(`total`, `open`, `closed` — reported fixed by the contact —, "
        "`deferred`), how those stand against "
        "their deadlines (`overdue`, `due_soon`, `on_track`) and the last day "
        "anyone recorded following up with them about a row still at risk "
        "(`last_follow_up_on`). Read it before writing, to use an address "
        "exactly as it is already recorded, and to see who needs chasing."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation/contacts",
}
_AUTHORED["remediation_teams"] = {
    "description": (
        "The teams that own fixes in this project, the one with the most "
        "overdue first: each with its findings on hosts (`total`, `open`, "
        "`overdue`, `due_soon`, `on_track`, `deferred`, `closed` — reported "
        "fixed by the contact) and how many "
        "`contacts` it has. `team: null` is the rows with a contact and no "
        "team. Set a row's team with remediation_apply (`team`)."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation/teams",
}
_AUTHORED["remediation_trend"] = {
    "description": (
        "Whether the remediation backlog is shrinking: `daily` — the counts "
        "by deadline state recorded each day since this installation began "
        "tracking (a day nobody recorded is absent, not zero) — and "
        "`closed_by_month` — rows the contact reported fixed (status "
        "`closed`) per month as `on_time`, `late` or "
        "`no_deadline`. `days` is how far back the daily counts go (default 90)."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation/trend",
}
_AUTHORED["remediation_follow_up"] = {
    "description": (
        "What to say to ONE remediation contact: their overdue and due-soon "
        "findings on hosts (`items`, the longest overdue first) and `text`, a "
        "plain-text message listing them, for the operator to send — the "
        "server sends nothing. `total` is every overdue or due-soon row of the "
        "contact and `not_listed` how many of them `items` leaves out (the "
        "message says so too): quote `total`, never the length of `items`. "
        "Give the contact's address exactly "
        "(`contact_email`, from remediation_contacts)."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation/follow-up",
}
_AUTHORED["remediation_record_follow_up"] = {
    "description": (
        "Record that the operator followed up with a contact about their "
        "overdue and due-soon findings on hosts: one immutable timeline entry "
        "per row and the day on each row (`last_follow_up_on`). Call it only "
        "AFTER the operator says the message was sent. A row already followed "
        "up on that day is left alone, so a repeat records nothing twice. "
        "`finding_host_ids` narrows it to some of the contact's at-risk rows. "
        "Needs an operator who is a project admin."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/follow-up",
    "idempotent": True,
}
_AUTHORED["remediation_timeline"] = {
    "description": (
        "A host's remediation timeline, newest first: every change to a "
        "tracked field (`field`, `from`, `to`), every recorded follow-up "
        "(`kind: follow_up`) and every note, with who "
        "recorded it, when it happened (`occurred_at`) and when it was "
        "recorded (`recorded_at`). `finding_host_id` narrows it to one finding "
        "on the host."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation/hosts/{host_id}/events",
    "params": {"host_id": HOST_ID},
}
_AUTHORED["remediation_apply"] = {
    "description": (
        "Set remediation tracking fields on findings on hosts — typically "
        "from a spreadsheet or CSV the operator holds, which you read locally "
        "(nothing is uploaded). Needs an operator who is a project admin. "
        "Each row names its target by `finding_host_id` (from "
        "remediation_list), or by `finding_id` with `host_id`; `finding_id` "
        "alone means EVERY host of that finding, so use it only when the "
        "source really assigns one contact to all of them. Send only the "
        "fields the source gives; a field you leave out is not touched and an "
        "explicit null clears it. Match each source row to exactly one "
        "finding yourself, from what you read: a row you cannot match is "
        "reported to the operator and left out, never guessed. ALWAYS call "
        "with `dry_run: true` first and show the operator the summary — "
        "targets, changed, unchanged, conflicts. A conflict is a field "
        "someone already set to a different value: it is left alone unless "
        "the operator tells you to replace it, and only then do you send "
        "`overwrite: true`. A row's `notes` become timeline entries (give "
        "each a `request_key` so a re-run does not add them twice, and "
        "`occurred_at` when the source dates them; one key is one note — never "
        "two different texts). `status: closed` records that the contact "
        "REPORTED it fixed (the pages say \"Reported fixed\"); it does not "
        "change the assessor's endpoint status, and you never set it because "
        "an endpoint is `remediated`. `closed_on` goes only with `status: "
        "closed`, or on a row already closed. The whole call is planned before anything "
        "is written, and a dry run refuses what the real call would refuse; "
        "at most 500 findings on hosts per call."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/apply",
    # The field changes converge on a retry; a note sent without a
    # request_key is added again.
    "idempotent": False,
}
_AUTHORED["remediation_add_note"] = {
    "description": (
        "Add a note to a host's remediation timeline (\"contacted the owner, "
        "will respond on Friday\"), optionally about one finding on that host "
        "(`finding_host_id`). `occurred_at` is when it happened, if not now. "
        "With a `request_key`, repeating the call returns the stored note. "
        "Needs an operator who is a project admin."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/events",
    "additive": True,
}

# v2.337.0 — a single project session sees every tool, so nothing gates
# ``tools/list`` any more.  What remains is a PRESENTATION grouping for the MCP
# reference page (which kind of work a tool belongs to), and v2.338.0 derives
# it from the tool's name in this one function instead of carrying a
# ``workflows`` field on every entry that a loop then rewrote.  Universal tools
# (identity, the guide/catalogue readers, the session bookkeeping) report
# every kind and the page shows them as
# shared.
_KIND_BY_PREFIX = (
    ("assist_", _ASSIST),
    ("scope_", _SCOPE),
    ("host_tests_", _TESTING),
    ("remediation_", _ASSIST),
)


# ---------------------------------------------------------------------------
# Parameters come from the routes
#
# An entry above names an endpoint; what that endpoint takes — which arguments,
# where each goes (path / query / body), its type, enum and bounds, whether it
# is required — is read from the endpoint's OpenAPI operation when the registry
# is first used.  Nothing about a parameter is typed here twice, so a tool
# cannot advertise an argument its endpoint does not take, or a value the
# endpoint refuses.
# ---------------------------------------------------------------------------

#: JSON Schema keywords whose value is one schema / a list of them / a map of
#: them.  Everything else (``enum``, ``default``, ``required``…) is a literal,
#: and the keys of ``properties`` are argument names, not keywords.
_SUBSCHEMA = ("items", "additionalProperties", "not")
_SUBSCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SUBSCHEMA_MAPS = ("properties", "$defs", "patternProperties")
_NULL = {"type": "null"}


def _plain(schema: Any, components: Dict[str, Any], _open: tuple = ()) -> Any:
    """A route's JSON Schema as a tool advertises it.

    References are inlined (a tool's schema stands alone), ``Optional[X]`` is
    ``X`` (an omitted argument is how "none" is said), a ``None`` default and
    pydantic's generated ``title`` are dropped, and an integer's exclusive
    bound is written inclusive — the form ``mcp_assist`` checks values against.
    """
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in _open:
            raise ValueError(f"schema {name!r} refers to itself; it cannot be inlined")
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        return _plain({**components[name], **rest}, components, (*_open, name))
    out: Dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in _SUBSCHEMA:
            out[key] = _plain(value, components, _open)
        elif key in _SUBSCHEMA_LISTS:
            out[key] = [_plain(v, components, _open) for v in value]
        elif key in _SUBSCHEMA_MAPS:
            out[key] = {k: _plain(v, components, _open) for k, v in value.items()}
        else:
            out[key] = value
    for key in ("anyOf", "oneOf", "allOf"):
        variants = out.get(key)
        if variants is None:
            continue
        kept = [v for v in variants if v != _NULL] if key != "allOf" else variants
        if len(kept) == 1:
            del out[key]
            out = {**kept[0], **out}
        else:
            out[key] = kept
    if out.get("default", 0) is None:
        del out["default"]
    if out.get("type") == "integer":
        if "exclusiveMinimum" in out:
            out["minimum"] = out.pop("exclusiveMinimum") + 1
        if "exclusiveMaximum" in out:
            out["maximum"] = out.pop("exclusiveMaximum") - 1
    return out


def _operation(openapi: Dict[str, Any], name: str, method: str, path: str) -> Dict[str, Any]:
    op = openapi.get("paths", {}).get(path, {}).get(method.lower())
    if op is None:
        raise ValueError(f"MCP tool {name}: {method} {path} is not a routed endpoint")
    return op


def derive_tool(name: str, authored: Dict[str, Any], openapi: Dict[str, Any]) -> Dict[str, Any]:
    """One registry entry with its parameters read from its endpoint.

    Adds ``path_params`` / ``query_params`` / ``body_params`` (where each
    argument is sent) and ``input_schema`` to the authored entry.  Authored
    per parameter, in ``params``: a description (a string), or a mapping laid
    over the endpoint's schema to NARROW it (an enum where the endpoint takes
    free text and validates in its handler, a bound it leaves to a service).
    ``hidden`` names endpoint parameters the tool does not offer.  A name in
    either that the endpoint does not take is an error, never ignored.
    """
    components = openapi.get("components", {}).get("schemas", {})
    op = _operation(openapi, name, authored["method"], authored["path"])
    hidden = set(authored.get("hidden", ()))
    overlays = dict(authored.get("params", {}))
    alternatives = authored.get("path_alternatives") or {}

    props: Dict[str, Any] = {}
    required: List[str] = []
    placed: Dict[str, List[str]] = {"path": [], "query": [], "body": []}
    seen = set()

    def offer(where: str, pname: str, schema: Dict[str, Any], is_required: bool) -> None:
        if pname in seen:
            raise ValueError(f"MCP tool {name}: the endpoint takes {pname!r} in two places")
        seen.add(pname)
        if pname in hidden:
            if is_required:
                raise ValueError(f"MCP tool {name}: {pname!r} is required by the endpoint and cannot be hidden")
            return
        props[pname] = schema
        placed[where].append(pname)
        if is_required:
            required.append(pname)

    def parameter(p: Dict[str, Any]) -> Dict[str, Any]:
        schema = _plain(p.get("schema", {}), components)
        if p.get("description"):
            schema["description"] = p["description"]
        return schema

    for p in op.get("parameters", ()):
        if p.get("in") in ("path", "query"):
            offer(p["in"], p["name"], parameter(p), bool(p.get("required")))
    content = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    if content:
        body = _plain(content.get("schema", {}), components)
        for pname, schema in body.get("properties", {}).items():
            offer("body", pname, schema, pname in body.get("required", ()))

    # A tool that reaches one of several endpoints by which id it is given:
    # each id is offered, and "exactly one" is the dispatcher's check.
    for arg, alt_path in alternatives.items():
        alt = _operation(openapi, name, authored["method"], alt_path)
        (p,) = [p for p in alt.get("parameters", ()) if p.get("in") == "path" and p["name"] == arg]
        seen.add(arg)
        props[arg] = parameter(p)
    if alternatives:
        required = [r for r in required if r not in placed["path"]]

    stale = sorted((set(overlays) | hidden) - seen)
    if stale:
        raise ValueError(
            f"MCP tool {name}: {', '.join(stale)} not taken by {authored['method']} {authored['path']}"
        )
    for pname, overlay in overlays.items():
        if isinstance(overlay, str):
            overlay = {"description": overlay}
        merged = {**props[pname], **overlay}
        if "enum" in overlay:
            # The endpoint's pattern says the same thing less readably.
            merged.pop("pattern", None)
        props[pname] = merged

    schema: Dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    spec = {k: v for k, v in authored.items() if k not in ("hidden", "params")}
    if placed["path"]:
        spec["path_params"] = placed["path"]
    if placed["query"]:
        spec["query_params"] = placed["query"]
    if placed["body"]:
        spec["body_params"] = placed["body"]
    spec["input_schema"] = schema
    return spec


def _api_schema() -> Dict[str, Any]:
    """The running API's OpenAPI document.

    Read from the application module only if it is already loaded: importing
    it applies database migrations, which a reader of this registry must never
    cause.  Every user of the registry runs inside the API process."""
    main = sys.modules.get("app.main")
    if main is None:
        raise RuntimeError(
            "The MCP tool registry is derived from the API's routes and exists only "
            "in the API process (app.main is not loaded)."
        )
    return main.app.openapi()


class _Registry(Mapping):
    """``{tool name: entry}`` with each entry's parameters derived from its
    endpoint — built on first use (the routes do not exist while this module
    is being imported) and kept."""

    def __init__(self, authored: Dict[str, Dict[str, Any]]):
        self._authored = authored
        self._built: Optional[Dict[str, Dict[str, Any]]] = None

    def _tools(self) -> Dict[str, Dict[str, Any]]:
        if self._built is None:
            openapi = _api_schema()
            self._built = {
                name: derive_tool(name, entry, openapi) for name, entry in self._authored.items()
            }
        return self._built

    def __getitem__(self, name: str) -> Dict[str, Any]:
        return self._tools()[name]

    def __iter__(self) -> Iterator[str]:
        # Names need no route: a caller that only lists them does not build.
        return iter(self._authored)

    def __len__(self) -> int:
        return len(self._authored)


TOOLS: Mapping = _Registry(_AUTHORED)


def tool_workflows(name: str) -> frozenset:
    """The kinds of work ``name`` belongs to, for grouping on the reference page."""
    for prefix, kinds in _KIND_BY_PREFIX:
        if name.startswith(prefix):
            return kinds
    return ALL_WORKFLOWS


def advertised_schema(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The tool's input schema with the MCP-side defaults folded in.

    v2.272.0 — the registry injects smaller page sizes than the endpoints' own
    defaults, but the schema still advertised the endpoint values (500 hosts
    where 100 is actually applied).  A client reading the schema was told one
    thing and got another, and /references/mcp-tools published the wrong number.
    Deriving the advertised default from the injected one means they can't drift.
    """
    defaults = spec.get("defaults")
    if not defaults:
        return spec["input_schema"]
    schema = dict(spec["input_schema"])
    props = {k: dict(v) for k, v in schema.get("properties", {}).items()}
    for arg, value in defaults.items():
        if arg in props:
            props[arg]["default"] = value
    schema["properties"] = props
    return schema


def annotations(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tool annotations — hints a client uses to pick approval defaults.

    Without these a host has no way to tell a read from a mutation except by
    reading the description, so it must prompt for everything (v2.271.0).
    ``readOnlyHint`` is the one that earns the feature: it's what lets a client
    offer "always allow" on the reads.
    """
    # A tool is read-only iff it doesn't mutate — the HTTP method, nothing else.
    # This used to be qualified against the (now removed) per-tool capability,
    # which would have advertised the (since removed) environment probe as safe
    # to auto-approve: it was a POST that carried no capability because it wrote
    # session metadata rather than project data.  Authority and mutation are different questions,
    # and only mutation belongs in an annotation a client auto-approves from.
    is_write = spec["method"] != "GET"
    # The spec defines destructiveHint:false as "additive updates only", so the
    # flag lives on the entry rather than being inferred from the name: adding a
    # note or a test result appends, while setting follow state or patching a
    # host REPLACES a stored value.  The operative
    # question for idempotency is whether a retry is safe — re-sending a
    # replacement converges, a second append is a second row.
    additive = bool(spec.get("additive"))
    # A session-bookkeeping write (renewing the key, ending the session) is a
    # POST, but it writes SESSION metadata, not project data, so it is not
    # destructive (v2.316.0; the environment probe, removed in v2.434.0, was
    # the case that prompted it).  readOnlyHint stays false — it does write —
    # but destructiveHint is the flag that gates auto-approval.
    metadata_write = bool(spec.get("metadata_write"))
    # idempotentHint answers one question: is a RETRY safe?  The inference
    # below ("a non-additive write converges") is right for updates and for
    # completions, and wrong for anything that CREATES, and feedback
    # is an append even though it is session bookkeeping.  v2.343.2 (external
    # review, finding 8): a spec says so explicitly with ``"idempotent": False``
    # and the builder honours it; destructiveHint is untouched, because that is
    # the flag clients gate auto-approval on and the creators should keep
    # asking.
    idempotent = spec.get("idempotent")
    if idempotent is None:
        idempotent = (not additive) or metadata_write
    ann: Dict[str, Any] = {
        "readOnlyHint": not is_write,
        "destructiveHint": is_write and not additive and not metadata_write,
        "idempotentHint": bool(idempotent),
        "openWorldHint": False,
    }
    return ann


def tool_list_payload() -> List[Dict[str, Any]]:
    """The ``tools`` array for a ``tools/list`` response — the whole catalogue.

    v2.309.0 — the ``granted`` capability filter is gone with the capability
    system. Write tools are listed for every session now, and whether a
    particular write succeeds is decided by the operator's project role at the
    endpoint. That is a small honesty improvement as well as a simplification:
    the previous filter implied the listed set was the *permitted* set, when
    the row-level constraint meant a listed write could still be refused.

    v2.337.0 — the per-workflow filter is gone too: one project session does
    every kind of work.  The endpoint behind each tool re-decides on every call.
    """
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": advertised_schema(spec),
            "annotations": annotations(name, spec),
        }
        for name, spec in TOOLS.items()
    ]
