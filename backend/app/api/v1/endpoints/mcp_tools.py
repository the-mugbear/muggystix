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

**One session, every tool.**  The operator starts one agent session and the
agent does whatever work is asked within it, in whatever order: it reads a
scope and uploads what was collected, proposes tests on hosts, records what it
ran as evidence, and proposes findings.

**What a description says.**  What the tool returns and the one or two things
an agent gets wrong without being told — not the argument types the schema
carries, not history, not a rule the session prompt or the guide owns.  It
never names a security tool or flags to use, and never tells the agent to
source or cite the tool catalogue.

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

import re
import sys
from collections.abc import Mapping
from typing import Any, Dict, Iterator, List, Optional

# The kinds of work, used only as catalogue tags on the tool reference page
# (`tool_workflows`): a key belongs to one project session and `tools/list` is
# not filtered by them.  Kept as plain strings rather than importing the enum:
# this module is pure data with no DB dependency.
WORKFLOW_ASSIST = "assist"
# Proposing tests on hosts and recording what they produced.
WORKFLOW_TESTING = "testing"
# Reading a scope (its subnets, domains, target lists) and uploading scan
# output.
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

# The model the agent says it is running as.  No protocol carries
# it, so the writes where it matters ask for it: it labels the tests, the
# proposal or the session, and lets output from different models be compared.
AGENT_MODEL = "The model you are running as (e.g. claude-opus-5-5). Optional; labels this work."

_AUTHORED: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # The session itself
    # -----------------------------------------------------------------------
    "agent_identity": {
        "description": (
            "What your API key is: its session and project, the operator you act "
            "for and their project role, whether you may write project data "
            "(`can_write_project_data`), and when the key expires "
            "(`key_expires_at`, `renewable_until`). Call this first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/identity",
    },
    "session_renew": {
        "description": (
            "Push your key's expiry out; the key itself does not change. Call it "
            "before, or right after, something long-running. Works on a key that "
            "has ALREADY expired, until the session's own deadline "
            "(`renewable_until`); ending the session revokes the key regardless."
        ),
        "method": "POST",
        "metadata_write": True,
        "path": "/api/v1/agent/session/renew",
    },
    "end_session": {
        "description": (
            "End your session and revoke your key — the LAST call you make, and "
            "only when the operator says they are finished (finishing a task is "
            "not that: report and wait); file any feedback you have not filed yet "
            "first. Never refused; tests and evidence stay in the project."
        ),
        "method": "POST",
        "metadata_write": True,
        "path": "/api/v1/agent/session/end",
        "params": {"agent_model": AGENT_MODEL},
    },
    "read_agent_guide": {
        "description": (
            "The agent guide: endpoint body shapes, upload formats, recipes, and "
            "the scope and working-directory rules in full. Read the part you need "
            "when a tool leaves you guessing. Omit `part` for the whole guide."
        ),
        "method": "GET",
        "path": "/api/v1/agents-guide",
        "params": {
            "part": {
                "enum": ["testing", "reconnaissance", "assist", "remediation"],
                "description": (
                    "One PART of the guide, not a kind of session: assist (reading "
                    "and reporting), reconnaissance (scope reads and uploads), "
                    "testing (host tests and evidence), remediation (remediation "
                    "tracking). Omit for the whole guide."
                ),
            },
        },
    },
    "list_tools": {
        "description": (
            "BlueStick's tool catalogue, kept by the team: what each tool is for, "
            "its ports, install command, whether it is intrusive, whether BlueStick "
            "parses its output (`ingestible`) and, for a parsed tool, the "
            "`run_command` / `run_note` that produce a file it ingests. A "
            "reference, not a permission list: what you run is between you and "
            "the operator."
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
            "it does not list. Records your rationale for a curator. It is "
            "catalogue intake only; it neither grants nor withholds anything."
        ),
        "method": "POST",
        "path": "/api/v1/agent/tool-suggestions",
        "additive": True,
        "params": {"name": "Tool name as it would be invoked (e.g. ligolo-ng)."},
    },
    # The installation's configured scanners.  The ask-first rule is the
    # session's (agent_policy.render_scanner_integrations_rule); it is said
    # here too because an agent may read a tool and nothing else.
    "list_scanner_integrations": {
        "description": (
            "The scanners configured in this BlueStick installation (they belong "
            "to no project): each one's `id`, name, type and address, plus what "
            "was configured beside it (e.g. a Nessus licence's host cap). NO "
            "credentials. Before using one, ask the operator whether they want "
            "you to, and tell them that saying yes means BlueStick shares that "
            "scanner's credentials with you so you can talk to it; only then call "
            "request_scanner_credentials."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scanner-integrations",
    },
    "request_scanner_credentials": {
        "description": (
            "ASK THE OPERATOR FIRST. Returns ONE configured scanner's credentials "
            "(`credentials`, named by what they are) so you can talk to it. Before "
            "calling, ask the operator whether they want you to use that scanner "
            "and tell them that saying yes means BlueStick shares its credentials "
            "with you; call this only after they say yes, with `operator_agreed: "
            "true`. Without it the call is refused (422). Every request is "
            "recorded and shown to the operator. Needs an operator who can write "
            "to the project (403 otherwise: tell them). Keep the credentials out "
            "of notes, evidence, feedback and proposals."
        ),
        "method": "POST",
        "path": "/api/v1/agent/scanner-integrations/{integration_id}/credentials",
        # NOT marked `additive`, although it replaces nothing: a client may
        # approve an additive write without asking, and this call hands over
        # credentials — it should read to the client as one to confirm.
        # Each request writes its own audit row, so a retry is not a no-op.
        "idempotent": False,
        "params": {
            "integration_id": {
                "minimum": 1,
                "description": "The scanner's `id` (from list_scanner_integrations).",
            },
        },
    },
    "submit_feedback": {
        "description": (
            "File feedback about BlueStick AT THE MOMENT you hit friction — a "
            "retry, a guessed field or route, a workaround — one short entry each. "
            "A developer working on BlueStick reads it: name the tool or endpoint, "
            "expected vs actual and the exact error text. One line of "
            "`friction_notes` is a complete entry. Answers an acknowledgement (id "
            "and counts), not the entry."
        ),
        "method": "POST",
        "metadata_write": True,
        "additive": True,
        "path": "/api/v1/agent/feedback",
        # Session bookkeeping, but an APPEND: a retry files a second row.
        "idempotent": False,
        "params": {
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
            "Project orientation: engagement dates, members and their project "
            "roles, host / port / scope / scan / domain totals, name counts "
            "(`names.in_scope_unresolved` = approved names nothing has resolved), "
            "the scopes (first 50; `scopes_truncated`) and the five latest scans. "
            "No findings. `default_host_view` is the view the Hosts page opens on "
            "when a project admin set one: your unfiltered counts are the whole "
            "project, so say which set you counted when the operator asks about "
            "'the hosts I see'."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/context",
    },
    "assist_list_hosts": {
        "description": (
            "Hosts matching a filter. `q` is the Hosts page's query language "
            "(port:, os:, service:, cve:, check:, has:, follow:, assigned: …, with "
            "AND / OR / NOT and parentheses); the field list is in the agent "
            "guide, and assist_get_vocabulary gives this project's tag / label / "
            "site / username values. port: and service: match OPEN ports "
            "(port:22@any for other states). follow:mine is the operator's own "
            "review list, follow:in_review any teammate's; assigned: is about "
            "HOSTS (unowned findings are assist_list_findings unowned=true). "
            "has:critical_exploit is a critical that itself has an exploit — "
            "'has:critical AND has:exploit' also matches a critical beside an "
            "exploitable low. Returns {items, total, has_more, limit, offset}: "
            "quote `total`, never the length of a page. A malformed q is a 400; a "
            "discrete filter value that cannot be read is a 422 naming it."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts",
        # The endpoint's own default is 500 — right for a file download, a lot
        # of tokens for a model that usually wants the first handful.
        "defaults": {"limit": 100},
        # The route's own text for `q` calls follow:in_review "hosts you have
        # in review"; it is any teammate's.  Laid over until the route says so.
        "params": {"q": "The Hosts page's query language (see the tool description)."},
    },
    "assist_count_hosts": {
        "description": (
            "How many hosts match a filter, in one call — the same arguments as "
            "assist_list_hosts. Use it instead of counting pages. "
            "q='has:critical AND assigned:none' is hosts with a critical scanner "
            "observation and nobody assigned; unowned FINDINGS are "
            "assist_list_findings unowned=true."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/count",
        "params": {"q": "The Hosts page's query language (see assist_list_hosts)."},
    },
    "assist_get_host": {
        "description": (
            "One host as the host inspector shows it: identity, `names` (every "
            "name seen at the address), OS, ports with service detail and bounded "
            "NSE output, severity counts, the operator's review status (`follow`), "
            "tags, assignees, scope membership, per-domain `assessment` "
            "(vuln_scan_credentialed: yes / no / not_stated), weakness flags, "
            "certificates, scan conflicts (`resolved_at` = when the shown value "
            "was picked, not that anyone settled it), note_count and "
            "finding_count. `web_interfaces` is the first 10 "
            "(assist_list_host_web_interfaces has all). Notes and scanner rows "
            "are their own tools. Pass exactly one of host_id or ip."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}",
        "path_alternatives": {"ip": "/api/v1/agent/assist/hosts/by-ip/{ip}"},
        "params": {"host_id": HOST_ID, "ip": "The host's address, e.g. 10.0.0.5."},
    },
    "assist_get_host_vulnerabilities": {
        "description": (
            "One host's raw scanner rows, worst first: severity, CVE / plugin id, "
            "title, port, CVSS, description, solution, scanner evidence. Returns "
            "{host_id, items, total, has_more, limit, offset}. These are scanner "
            "observations: each `id` is a vulnerability id (what "
            "propose_observation and a host test's `vulnerability_id` take), NOT a "
            "finding id. `finding_id` / `finding_status` name the finding that "
            "covers the issue; `finding_on_this_host` false means it covers other "
            "hosts only, so the row is unjudged here; `finding_endpoint_status` is "
            "this host's own state on it. cve, plugin_id or search narrow to one "
            "issue."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/vulnerabilities",
        "defaults": {"limit": 50},
        "params": {"host_id": HOST_ID},
    },
    "assist_list_findings": {
        "description": (
            "The project's findings — what the team has judged, a different set "
            "from scanner rows (ids do not cross with "
            "assist_get_host_vulnerabilities). Returns {total, severity_counts, "
            "findings}: `total` covers the whole filter and `severity_counts` the "
            "same filter without `severity`, so 'how many criticals are open?' is "
            "one call with limit=1; there is no has_more — page with offset until "
            "you hold `total` rows. `host_count` is distinct addresses and "
            "`endpoint_count` affected rows; `hosts` is a sample of at most 10 "
            "addresses (`hosts_truncated`). owner takes `me` or a username; an "
            "unknown one is a 400."
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
            # The endpoint compares severity and source as given, so an unknown
            # one would read as "no such findings": the tool names the values.
            "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
            "source": {"enum": ["note", "scanner", "execution", "manual", "all"]},
            "host_id": {"minimum": 1},
        },
    },
    "assist_list_host_web_interfaces": {
        "description": (
            "Every web interface observed on one host, paged ({items, total, "
            "has_more}); assist_get_host lists only the first 10. Each: URL, FQDN, "
            "title, server header, technologies, the tool that observed it, "
            "`screenshot_download_path` when one was captured, and the TLS facts "
            "the tool reported (cert_not_after, cert_self_signed, cert "
            "organisations, issuer, subject CN, cert_sans — the first 20 of "
            "cert_san_total —, tls_version, tls_weak_protocol). null means the "
            "tool did not report it, not that it is fine. `is_latest` false is an "
            "earlier scan's row of the same URL."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/web-interfaces",
        "params": {"host_id": HOST_ID},
    },
    "assist_list_host_access": {
        "description": (
            "Every NetExec / SMBMap result on one host, paged: what BlueStick read "
            "from each tool line (login outcome, username, local admin, SMBv1, "
            "writable share, shares) beside the line itself (`raw_output`; "
            "`raw_output_truncated` when the import cut it). Credentials appear as "
            "the tool printed them."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/access",
        "params": {"host_id": HOST_ID},
    },
    "assist_list_uninterpreted_lines": {
        "description": (
            "Imports whose parser did not interpret every line, newest first, "
            "with those lines as REDACTED shapes and counts. kind: `dropped` (not "
            "in the inventory), `text_only` (kept as the tool's line, nothing read "
            "from it), `module_as_login` / `module_as_text` (an nxc module's "
            "result). Not an ingestion issue — what was read is in the project. "
            "`job_id` is an import's id (`ingestion_job_id` on assist_list_scans), "
            "not a scan id; an unknown one is a 404. Needs an analyst operator."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/uninterpreted-lines",
    },
    "assist_get_host_notes": {
        "description": (
            "The team's notes on one host, newest first, paged ({items, total, "
            "has_more}). Read it before adding a note and before answering 'what "
            "do we know about X'. Each note: author, whether an agent wrote it "
            "(`actor_type`), its thread (parent_id / thread_root_id), note_type, "
            "pinned, and attachments as download references."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/notes",
        "params": {"host_id": HOST_ID},
    },
    "assist_get_vocabulary": {
        "description": (
            "The values THIS project uses for tag:, label:, site:, scope: and "
            "assigned:, plus the finding statuses and severities. A guessed tag "
            "does not error — it matches zero hosts — so read this before writing "
            "such a predicate."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/vocabulary",
    },
    "assist_get_writing_guidance": {
        "description": (
            "How THIS installation wants a finding's report text written: `general` "
            "and `sections` (description, impact, recommendation, "
            "steps_to_reproduce, references). Read it before propose_finding with "
            "`report_text`; assist_get_finding carries the same block for a "
            "finding that exists. It decides style and content, never the "
            "report-text rules."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/writing-guidance",
    },
    "assist_get_coverage": {
        "description": (
            "How much of the project has been assessed, per domain (port "
            "discovery, service detection, vulnerability assessment, web / TLS …) "
            "and per segment (`matrix`) — what stops 'no critical findings' being "
            "reported as 'no critical exposure'. vuln_assessment carries "
            "`credentialed` {credentialed, not_credentialed, "
            "credentials_not_stated}: of the assessed hosts, how many a scanner "
            "logged in to (list them with assist_list_hosts "
            "q=vulnscan:credentialed | uncredentialed | unstated). A domain's gap "
            "as a host list is assist_list_hosts q=gap:<domain key>. The three scope "
            "states are not here: assist_count_hosts q=scope:subnet / scope:name / "
            "scope:none."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/coverage",
    },
    "assist_list_segments": {
        "description": (
            "Per-subnet rollup, worst first — the Subnet Insights page's numbers: "
            "`exposure` (active findings by severity, tier-weighted), `neglect` "
            "(unowned active findings, unreviewed hosts), `hygiene` (end-of-life "
            "OS, certificate problems, weak auth, risky services) and a "
            "recommended action. `no_coverage` marks a scoped range where nothing "
            "was discovered — a gap, NOT a clean subnet. `adopted=false` means the "
            "project has no scoped subnets: not assessable. `total` is every "
            "subnet; page with offset."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/segments",
    },
    "assist_get_posture": {
        "description": (
            "The project's overall condition as the Posture page states it: "
            "`label`, `conclusion`, `reasons`, `priorities`, `headline`, "
            "`evidence`, `disposition`, `sites`. label='insufficient_evidence' "
            "means not assessed enough to judge — it is not a clean bill of "
            "health. `scanner_observations` counts raw scanner rows, not findings "
            "(`total` leaves informational out; by_severity; hosts_by_severity; "
            "null when it could not be counted): 'how many criticals?' may mean "
            "findings, scanner issues, scanner rows or hosts — say which you are "
            "giving."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/posture",
    },
    "assist_get_patterns": {
        "description": (
            "Cross-sectional analysis of the estate: `blind_spots` (conditions "
            "spanning it), `segment_outliers` (subnets whose issue density is an "
            "outlier against the median; times_median null = no baseline to "
            "compare), `conditions` with their spread, `family_summary` (a "
            "root-cause hypothesis and control per family) and "
            "`diagnostic_profiles`. It compares ACROSS the estate, never over "
            "time — do not call these trends. adopted=false means no scoped "
            "subnets: report 'not assessable', never 'no patterns found'."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/patterns",
    },
    # v2.428.0 — Operations, Evidence gaps and scan compare, each the SAME
    # service its page uses (agent_assist_operations.py).
    "assist_get_workbench": {
        "description": (
            "Your operator's Operations page. `my_work` is what waits on them BY "
            "KIND — answer with the kinds apart: findings_to_decide, "
            "findings_to_write (the two add up to findings_needing_me), "
            "tests_assigned, hosts_in_review, tests_on_hosts_in_review. `total` is "
            "hosts_in_review + tests_assigned + tests_on_hosts_in_review + "
            "findings_needing_me; `to_claim` (unassigned critical / high tests) is "
            "shared work and not in it. The lists are PREVIEWS — my_queue 10, "
            "my_tasks 10 per group, my_findings and followups 15, recent_notes 8 — "
            "so answer 'how many' from my_work, in_review_count, total_open, "
            "group_counts or followups.total, never from a list's length. The "
            "whole lists: hosts in review = assist_list_hosts q='follow:mine'; "
            "changed since review (`followups`: the operator's OWN finished "
            "reviews that gained open ports or critical / high observations) "
            "= q='follow:revisit'; tests assigned "
            "= host_tests_list mine=true active_only=true; tests on hosts in "
            "review = host_tests_list q='follow:mine' active_only=true; findings "
            "that need them = assist_list_my_findings. `since_last_visit`: scans, "
            "new and changed hosts, new critical / high scanner observations since "
            "they last marked Operations seen (reading marks nothing). `blockers`: "
            "failed or partial imports nobody dismissed and no later import "
            "replaced. `setup`: whether the project has hosts and a subnet scope "
            "yet. `investigate` is null here — the untouched queue is "
            "assist_list_worth_a_look. `*_unavailable: true` means that section "
            "could not be computed: say so, never 0. There are no project-wide "
            "measures and no team roster: read the total of assist_list_hosts "
            "q='has:tested', q='has:untouched has:critical' or "
            "q='follow:in_review'."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench",
        # The untouched-hosts queue has its own tool (assist_list_worth_a_look).
        "hidden": ["include_investigate"],
    },
    "assist_list_worth_a_look": {
        "description": (
            "Operations' 'Untouched, with a reason' queue: hosts NOBODY has "
            "touched (no review or assignment, note, host test, evidence or "
            "finding) that carry an observed weakness or a relevant change, each "
            "with its reasons and next action, in stated tier order (1 exploitable "
            "critical, 2 critical vulnerability, 3 exploit available, 4 high-value "
            "service new or changed, 5 scans disagree). `queue_total` and "
            "`tier_counts` cover the whole queue whatever tier or offset you pass. "
            "A 503 means it could not be computed — not that the queue is empty."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench/investigate",
    },
    # The Operations "Findings" tab as a whole list
    # (operations_read_service.compute_my_findings, the page's own function).
    "assist_list_my_findings": {
        "description": (
            "Every finding your OPERATOR owns that needs something from them — "
            "the Operations 'Findings' tab, paged, severity first. Each row's "
            "`needs` says why (under investigation, required report text missing, "
            "a proposal to decide). need=decide (under investigation, or a "
            "proposal waiting) and need=write (only report text missing) never "
            "overlap and add up to the list. `total` is the size of the list this "
            "call pages; `total_open` and `need_counts` describe the whole list "
            "whatever `need` says. A finding they own that needs nothing is not "
            "here (assist_list_findings owner=me lists those too), and a "
            "teammate's never is."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench/findings",
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
        "params": {"scan_id": "Scan id (assist_list_scans)"},
    },
    "assist_list_ingestion_issues": {
        "description": (
            "Imports that failed, are still in flight, or parsed but dropped rows "
            "— check it before reporting that something is ABSENT: if `has_issues` "
            "is false, an empty result elsewhere is a real absence. Counts as the "
            "Ingestion Results page states them: failed, expired (a staged upload "
            "nobody started), discarded, degraded, queued / processing, and "
            "needs_attention (failed or partial, not dismissed, not replaced by a "
            "later import — the answer to 'how many imports need attention'). An "
            "issue's kind: failed or expired = nothing from that file is in the "
            "project; degraded = it is in, but rows were dropped, so counts drawn "
            "from it are undercounts; parse_error = a recorded failure with no "
            "import job. unresolved_parse_errors_total is the project's number; "
            "unresolved_parse_errors only the part with no import job. Needs an "
            "analyst operator."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/ingestion-issues",
    },
    # v2.428.0 — images can be read inline with assist_get_image (below); the
    # download paths assist_get_finding and assist_get_host hand out stay, for
    # saving a file beside a report.
    "assist_get_finding": {
        "description": (
            "One finding with what a write-up cites. `hosts`: the first 100 "
            "endpoint rows (`hosts_truncated`); `host_count` is distinct addresses "
            "and `endpoint_count` rows; each row's `finding_host_id` is what "
            "propose_endpoint_status and record_evidence take; "
            "`endpoint_status_counts` covers them all. `evidence_note`, "
            "`evidence_thread` and `comments` — each note says whether a person or "
            "an agent wrote it (`actor_type`). `scanner_evidence` (the first 100 "
            "of scanner_evidence_total) and `evidence_records`: a scanner's output "
            "and a command a tester ran are different assertions — say which a "
            "claim rests on. `report_text`, `status_history`, `writing_guidance` "
            "(read it before propose_finding_text) and `images`: `id`, `caption`, "
            "`in_report` and `placed_in`, the report-text fields whose Markdown "
            "places the image with `![caption](evidence:<id>)`; a ticked image no "
            "field places prints under Evidence. Attachments are references "
            "(download_path): look at one with assist_get_image."
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
            "severity; exploitable=true keeps issues a scanner reports an exploit "
            "for (each row says `exploitable` — a report, not proof it was "
            "exploited). assist_list_observation_hosts lists one issue's hosts."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scanner-observations",
        "defaults": {"limit": 25},
        # The page route's name for `offset`; one argument for one thing.
        "hidden": ["skip"],
        "params": {
            "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
            "kind": {"enum": ["misconfiguration", "vulnerability", "informational"]},
        },
    },
    "assist_list_observation_hosts": {
        "description": (
            "The hosts carrying one scanner issue (issue_key from "
            "assist_list_scanner_observations), by address: ports, severity, and "
            "whether a finding covers it on that host (judged, endpoint_status), "
            "and the tests naming this issue there (tests_to_do, tests_recorded). "
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
            "One client report as it states things: engagement details, executive "
            "summary, `counts`, `scope`, and every finding (ref, report text, "
            "affected endpoints, `confirmations` — the test results it prints, "
            "with confirmations_omitted for the rest — and `images[]` with "
            "placed_in, printed and printed_in). content_source: issued_snapshot "
            "is the frozen text as issued, draft_live what the draft would say "
            "now; for 'what did we tell the client' read the latest issued one "
            "(`latest_issued_id` on assist_list_client_reports). In an addendum "
            "each finding's `change` is new, new_hosts or severity_changed "
            "(previous_severity = what the baseline reported) and `delta` counts "
            "them. confirmations and summary.evidence_records are only what THIS "
            "template prints (evidence_records_not_printed counts the rest). "
            "summary.images_printed + images_trailing + images_not_printed = "
            "summary.images; null when printing could not be measured. Files "
            "carry a download_path. Needs an auditor operator."
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
            "The project's named assets (FQDNs): `in_scope` (a declared domain "
            "covers the name — it does NOT put the address in subnet scope), "
            "`current_ips` (the first 10 of current_ip_total, from the latest "
            "A/AAAA observations) and `sources`. in_scope=true with resolved=false "
            "is the names approved for testing that no upload has resolved. A "
            "name that shares its address with other names must be tested by "
            "name, not by IP. host_id lists the names CURRENTLY resolving to that "
            "host; assist_get_host's `names` is wider — every name ever seen "
            "there, PTR and certificate included."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/names",
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
            "This session's purpose, status, project and operator — who "
            "`assigned:me` and `follow:mine` mean. Whether you may write is "
            "`can_write_project_data` on agent_identity."
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
            "host_test_id": "The host test this answers, if any (from host_tests_list). Needs request_key.",
            "request_key": "Your own stable key for this record; re-sending it returns the record already stored (a safe retry). Required with host_test_id.",
            # The endpoint sets no schema length on purpose (a schema 422
            # echoes the input back); its service answers 413 past 5 MB.
            "raw_output": {"maxLength": 5242880},
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
            "Propose report text for a finding; a person accepts (then may edit) or "
            "rejects it, and nothing changes until then. One proposal per field; "
            "several drafts of a field may stand side by side. EACH FIELD'S VALUE IS "
            "THE COMPLETE REPLACEMENT for that section, as it will read in the client "
            "report — never a critique, suggestions or a diff (why you changed it goes "
            "in `rationale`). Propose only the sections you would change. THE READER "
            "HAS NEVER SEEN BLUESTICK: no record numbers or ids (\"Finding #277\", "
            "\"evidence record 57\") and no mention of BlueStick — name a finding by "
            "its title and a system by its address; a section naming a record is "
            "refused (422). NOT ENOUGH TO GO ON IS A VALID ANSWER: write a section "
            "only from the finding's data and recorded evidence; when that is too "
            "thin, leave it out — never a guess or a placeholder — and say what is "
            "missing in `rationale` and to your operator. Fields: description, impact, "
            "recommendation, references, steps_to_reproduce (Markdown), cvss_vector. A "
            "section may place one of the finding's images with "
            "`![caption](evidence:<id>)`: keep the references a section already holds, "
            "and use only ids listed under `images` on assist_get_finding (any other "
            "is a 422; an image nobody ticked 'In report' blocks acceptance until a "
            "person ticks it). Answers ids and text lengths, not the text."
        ),
        "method": "POST",
        "path": "/api/v1/agent/proposals/finding-text",
        "additive": True,
        "idempotent": False,
        "params": {
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
            "no mention of BlueStick — a section naming a record is refused (422). "
            "Before writing `report_text`, read assist_get_writing_guidance and write "
            "each section the way this installation asks."
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
            "vulnerability_id": (
                "A scanner row's `id` from assist_get_host_vulnerabilities — not an "
                "issue_key from assist_list_scanner_observations."
            ),
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
            "Add a note to a host — discussion for the team (context, a question, a "
            "handoff), stamped agent-authored. Not how work is recorded: a check "
            "you ran is record_evidence, a check to run is host_tests_propose. "
            "Mark inferences as inferences. Needs an operator who may write "
            "(`can_write_project_data` on agent_identity)."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/notes",
        "additive": True,
        "params": {"host_id": HOST_ID},
    },
    "assist_set_follow": {
        "description": (
            "Set the OPERATOR's review status on a host — in_review or reviewed — "
            "or `none` to remove it. Do NOT mark a host `reviewed` on your own "
            "initiative: it is a human judgement; confirm with the operator first. "
            "Read the host's `follow` before overwriting it. Needs an operator who "
            "may write."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/follow",
        "params": {
            "host_id": HOST_ID,
            "status": "in_review / reviewed, or `none` to clear the follow.",
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
# Host tests — the tests proposed on each host, shown on the host's page.
# ---------------------------------------------------------------------------
_HOST_TEST_ID = {
    "minimum": 1,
    "description": "The host test's id (from host_tests_list or host_tests_propose).",
}

_AUTHORED["host_tests_propose"] = {
    "description": (
        "Propose tests on hosts — up to 200 per call, each one test on one "
        "host: the tool, what it establishes (`description`), the exact command "
        "({ip} / {fqdn} placeholders allowed), the rationale and a priority. "
        "They show on each host's page at once; nothing approves them. Which "
        "hosts and which tests is the operator's request, not yours to widen. "
        "`label` groups one request's tests. `vulnerability_id` (a scanner "
        "row's id from assist_get_host_vulnerabilities, on the same host) "
        "links the test to the weakness it confirms. Every test needs its own "
        "`request_key`: the same key with the same content returns the stored "
        "test, with different content is a 409. The batch is all-or-nothing. "
        "Read host_tests_list first, so you do not duplicate a test that is "
        "already there."
    ),
    "method": "POST",
    "path": "/api/v1/agent/host-tests",
    "additive": True,
    "params": {"agent_model": AGENT_MODEL},
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
        "Remediation tracking, one row per finding ON A HOST: the contact "
        "(`contact_email`, `contact_name`), `team`, `notified_on` (the assigned "
        "date), the contact's progress `status` (open, closed, deferred) and "
        "`closed_on`. `closed` means the contact REPORTED it fixed — say "
        "\"reported fixed\", never \"remediated\": the assessor's conclusion is "
        "`finding_status` / `endpoint_status` on the same row, and neither "
        "moves the other. Where the two disagree the row's `verification` says "
        "so (`reported_fixed_not_retested`, `remediated_record_open`; null "
        "where they agree). Against its deadline each row has `state` (overdue, "
        "due_soon, on_track, not_assigned — nobody was given a date, so no "
        "clock runs —, no_deadline — the severity has no timeline —, deferred, "
        "closed), `due_on` (the deadline in force; `deadline_source` policy or "
        "override), `days_left`, `closed_days_late`, `last_follow_up_on`, and "
        "for a deferred row `deferred_review_on` / `deferral_review_due`. "
        "Counts over the selection, whatever page you read: `total`, "
        "`state_counts`, `status_counts`, `verification_counts`, `flag_counts`, "
        "`severity_counts`, `overdue_ages`, `not_followed_up`. Each row's "
        "`finding_host_id` is what remediation_apply takes. Needs an auditor "
        "operator. Answers 404 where the installation has not turned "
        "remediation tracking on — say so, do not retry."
    ),
    "method": "GET",
    "path": "/api/v1/agent/remediation",
    "params": {"group": "The order of the rows (default host; due = by deadline)."},
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
        "server sends nothing. With `upcoming_days` it also lists the on-track "
        "rows whose deadline falls within that many days (`upcoming` counts "
        "them). `total` is every such row of the "
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
        "Send the `upcoming_days` the message was read with, so the upcoming "
        "rows it listed are recorded too. "
        "Needs an operator who is a project admin."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/follow-up",
    "idempotent": True,
}
_AUTHORED["remediation_assign_from_report"] = {
    "description": (
        "Start the remediation clock from an ISSUED client report: every "
        "finding on a host that report listed (its frozen content, not "
        "today's findings) that is still in the remediation list, has no "
        "assigned date and is open gets the assigned date — `assigned_on`, "
        "or the day the report was issued. Nothing else is touched and a row "
        "that already has a date is left alone, so a repeat assigns nothing. "
        "`report_id` is from assist_list_client_reports. ALWAYS call with "
        "`dry_run: true` first and show the operator the numbers: `assigned`, "
        "`already_assigned`, `not_open`, `not_in_list` (listed by the report "
        "and no longer a finding on that host, or no longer tracked). A draft "
        "or replaced report is a 409. Each row's timeline gets the change and "
        "a note naming the report. Needs an operator who is a project admin."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/assign-from-report",
    "idempotent": True,
    "params": {"agent_model": AGENT_MODEL},
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
        "from a spreadsheet the operator holds, which you read locally "
        "(nothing is uploaded). Needs an operator who is a project admin. "
        "Each row names its target by `finding_host_id` (from "
        "remediation_list) or by `finding_id` with `host_id`; `finding_id` "
        "alone means EVERY host of that finding. Send only the fields the "
        "source gives: a field left out is untouched, an explicit null clears "
        "it. Match each source row to exactly one finding yourself; a row you "
        "cannot match is reported to the operator and left out, never "
        "guessed. ALWAYS call with `dry_run: true` first and show the "
        "operator the summary — targets, changed, unchanged, conflicts. A "
        "conflict is a field someone already set to another value: it stays "
        "unless the operator tells you to replace it, and only then do you "
        "send `overwrite: true`. `notes` become timeline entries (give each a "
        "`request_key`, so a re-run does not repeat them). `status: closed` "
        "records that the contact REPORTED it fixed; it never changes the "
        "assessor's endpoint status, and you never set it because an endpoint "
        "is `remediated`. `closed_on` goes only with closed. `status: "
        "deferred` needs `deferred_review_on` (today or later) and a note. "
        "`due_override_on` sets ONE row's deadline by hand, needs a note, and "
        "is sent only when the source gives that date — the ordinary deadline "
        "is derived, never sent. All-or-nothing; at most 500 findings on "
        "hosts per call."
    ),
    "method": "POST",
    "path": "/api/v1/agent/remediation/apply",
    "params": {"agent_model": AGENT_MODEL},
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
#: A route parameter's pattern that only lists the accepted words.
_CLOSED_LIST = re.compile(r"\^\(([A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*)\)\$")


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
        # A closed list written as a pattern (``^(asc|desc)$``) is offered as
        # the enum it is: the route stays the one place the values are typed.
        closed = _CLOSED_LIST.fullmatch(schema.get("pattern") or "")
        if closed and "enum" not in schema:
            del schema["pattern"]
            schema["enum"] = closed.group(1).split("|")
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

    def warm(self) -> int:
        """Build the registry now and say how many tools it holds.  Deriving
        it means generating the API's OpenAPI document — about a second —
        which the first reader would otherwise pay inside a request."""
        return len(self._tools())

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
