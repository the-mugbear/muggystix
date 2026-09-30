"""Declarative MCP tool registry — what the server exposes, per workflow.

Split out of ``mcp_assist.py`` in v2.278.0, when the surface stopped being
assist-only and grew the three agentic workflows.  The seam is real: this module
is *data* (which tool maps to which endpoint, with which schema), and
``mcp_assist.py`` is *protocol* (JSON-RPC framing, auth, loopback dispatch,
telemetry).  They change for different reasons and by different people — adding
a tool touches only this file.

Each entry:
    description  : shown to the model in tools/list
    workflows    : which key workflows may see it in tools/list (see below)
    method       : HTTP verb of the underlying endpoint
    path         : loopback path; ``{name}`` placeholders filled from path_params
    path_params  : argument names substituted into the path (omit if none)
    query_params : argument names sent as querystring (omit if none)
    body_params  : argument names sent in the JSON body (omit if none)
    input_schema : JSON Schema advertised to the client
    defaults     : MCP-side argument defaults (smaller pages than the endpoints')
    auto_params  : arguments filled from the caller's own identity when omitted
    additive     : True iff the write only appends (drives destructive/idempotent
                   annotations)
    path_alternatives : {arg: path} — other endpoints the tool may reach, chosen
                   by which one id the caller passes (exactly one of path_params
                   and these); v2.428.0, for assist_get_image
    result       : "image" — the endpoint returns an image, handed back as an
                   MCP image content block (v2.428.0)

**``workflows`` is an entry-point affordance, not a security boundary.**  Hiding
a tool from a key that cannot use it stops the model from trying a call whose
403 it would read as its own bug.  It decides nothing: every dispatch still
loops back through the real endpoint, where the router-level
``enforce_agent_operator_access`` and the object-level gates (a run belongs
to the session that opened it) make the actual decision.  The MCP layer
makes no security decision anywhere, and this file must not become the place it
starts.

Writes are **not** filtered by whether the caller may perform them (v2.309.0
removed the per-session capability grants that filter keyed off).  A write tool
is listed for every session of its workflow, and whether it succeeds is the
operator's project role, checked per request.  An agent that wants the answer
before trying reads ``can_write_project_data`` from ``agent_identity``.

**One session, every tool (v2.337.0).**  The operator starts one agent
session; the agent opens whatever work it needs within it.  Plans and
execution runs are optional groupings, not a sequence (v2.433.0); there are
no recon runs — the agent reads a scope and uploads what its scanners found.

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

from typing import Any, Dict, List

# The four kinds of work, used only as catalogue tags on the tool reference page
# (`tool_workflows`) — since v2.337.0 a key belongs to one project session and
# `tools/list` is not filtered by them.  Kept as plain strings rather than
# importing the enum: this module is pure data with no DB dependency.
WORKFLOW_ASSIST = "assist"
WORKFLOW_PLAN_GENERATION = "plan_generation"
WORKFLOW_EXECUTION = "execution"
# Reading a scope (its subnets, domains, target lists) and uploading scan
# output.  Was "recon" until v2.433.1, when recon runs were removed.
WORKFLOW_SCOPE = "scope"

ALL_WORKFLOWS = frozenset(
    {WORKFLOW_ASSIST, WORKFLOW_PLAN_GENERATION, WORKFLOW_EXECUTION, WORKFLOW_SCOPE}
)

_ASSIST = frozenset({WORKFLOW_ASSIST})
_PLAN = frozenset({WORKFLOW_PLAN_GENERATION})
_EXEC = frozenset({WORKFLOW_EXECUTION})
_SCOPE = frozenset({WORKFLOW_SCOPE})

HOST_ID_PROP = {
    "host_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Numeric host id (from assist_list_hosts).",
    }
}

# The scope a read is about.  Every scope read takes it as a path parameter.
SCOPE_ID_PROP = {
    "scope_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Scope to read (from assist_list_scopes).",
    }
}

# (The environment-probe fields and the ``record_environment`` tool went with
# the probe in v2.434.0.)

# The model the agent says it is running as (v2.434.0).  No protocol carries
# it, so the writes where it matters ask for it: it labels the plan, the run or
# the session, and lets output from different models be compared.
AGENT_MODEL_PROP = {
    "agent_model": {
        "type": "string",
        "maxLength": 200,
        "description": "The model you are running as (e.g. claude-opus-5-5). Optional; labels this work.",
    }
}

# Closed vocabularies the endpoints enforce (v2.434.1, acceptance run H2: a
# tool said "e.g. completed" for an enum that has no ``completed``, and left
# ``test_phase`` free while the endpoint 422'd on anything off its list).
# Plain literals because this module has no DB imports;
# tests/test_mcp_enum_contract.py fails if one drifts from its endpoint.
TEST_PHASE_FIELD = {
    "type": "string",
    "enum": ["reconnaissance", "enumeration", "exploitation", "post_exploitation", "reporting"],
    "description": "Which phase of the engagement this belongs to.",
}
ENTRY_STATUS_FIELD = {
    "type": "string",
    "enum": ["proposed", "in_progress", "completed", "rejected"],
    "description": "proposed (not tested yet), in_progress, completed, or rejected (dropped from the plan).",
}
TEST_RESULT_STATUS_FIELD = {
    "type": "string",
    "enum": ["pending", "pending_approval", "executed", "skipped", "failed", "not_applicable"],
    "description": (
        "executed (it ran — record what it produced), skipped, failed (it could not "
        "run), not_applicable; pending / pending_approval (you showed the command and "
        "wait for the operator's go-ahead) are not final and block completing the entry."
    ),
}
SANITY_METHOD_FIELD = {
    "type": "string",
    "enum": ["ping", "banner_grab", "reverse_dns", "network_context"],
    "description": "How you checked the target is the one the plan names.",
}

# A proposed test, as the plan entries carry it.  Mirrors ProposedTest in
# app/schemas/schemas.py.
_PROPOSED_TEST_ITEM = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "description": (
                "Tool name as invoked (e.g. nmap, testssl, netexec)."
            ),
        },
        "description": {"type": "string", "description": "What this test establishes."},
        "command": {
            "type": "string",
            "description": (
                "Exact command to run. Write output into the working directory "
                "the session runs in."
            ),
        },
        "expected_result": {"type": "string"},
        "references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tool", "description"],
    "additionalProperties": False,
}


TOOLS: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # Every workflow
    # -----------------------------------------------------------------------
    "agent_identity": {
        "description": (
            "What your API key is: its unified project session, open phases, bound "
            "project, write capabilities, the operator you act for, and when the key "
            "expires. Call this first to see which plans and execution runs your "
            "session already has open; one key can open and use every kind of work."
        ),
        "method": "GET",
        "path": "/api/v1/agent/identity",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "end_session": {
        "description": (
            "End your session — the LAST call you make, and only when the operator "
            "says they are finished (finishing a task is not that: report and wait); "
            "file any feedback you have not filed yet first; the key dies with this call. It "
            "revokes your key and marks the session ended so the operator's Agent "
            "Activity page stops showing it as running. Refused (409, naming the ids) "
            "while an execution run is still open: complete it "
            "first (execution_complete_session). Optional `notes`: one or two "
            "lines on what the session did (v2.340.0)."
        ),
        "method": "POST",
        "metadata_write": True,
        "path": "/api/v1/agent/session/end",
        "body_params": ["notes", "agent_model"],
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "What the session did, in a line or two.",
                },
                **AGENT_MODEL_PROP,
            },
            "additionalProperties": False,
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
        "query_params": ["workflow"],
        # A project session's identity supplies "project", which deliberately
        # returns the full guide. A phase slice remains available on direct HTTP.
        "auto_params": {"workflow": "workflow"},
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow": {
                    "type": "string",
                    "enum": ["plan_generation", "execution", "reconnaissance", "assist"],
                    "description": "Usually omit — resolved from your API key.",
                },
            },
            "additionalProperties": False,
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
        "query_params": ["status", "category"],
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["reference", "suggested", "rejected"],
                    "description": (
                        "reference = in the catalogue. suggested = proposed by an "
                        "agent, not yet curated. rejected = a declined suggestion."
                    ),
                },
                "category": {"type": "string", "description": "Filter to one category."},
            },
            "additionalProperties": False,
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
        "body_params": ["name", "rationale", "category", "description"],
        "additive": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Tool name as it would be invoked (e.g. ligolo-ng).",
                },
                "rationale": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": (
                        "What you used or needed it for — this is what a curator "
                        "reads. Be specific."
                    ),
                },
                "category": {"type": "string", "maxLength": 100},
                "description": {"type": "string", "maxLength": 2000},
            },
            "required": ["name", "rationale"],
            "additionalProperties": False,
        },
    },
    "submit_feedback": {
        "description": (
            "File feedback about BlueStick AT THE MOMENT you hit friction — when "
            "you retry a call, guess a field, work around a tool, or re-read the "
            "guide to make something work — not from memory at the end. Several "
            "one-line submissions during a session are the norm. "
            "execution_complete_session reports `feedback_recorded: false` when "
            "the session has filed none; end_session does not (it revokes your "
            "key), so file before you end. It is read by a coding "
            "agent working on BlueStick itself, so write for that reader: name the "
            "tool or endpoint, expected vs actual, the exact error text or missing "
            "field, and what would have let you finish faster. `source` names the "
            "kind of work: assist (queries/notes only), reconnaissance, "
            "plan_generation, or in_session_execution; add the matching "
            "test_plan_id / execution_session_id when you have one — the session "
            "itself is attributed from your key. One row is "
            "about ONE kind of work: source=assist takes no phase ids, so a "
            "session that also drafted a plan files that part as its own "
            "plan_generation row with test_plan_id. tool_suggestions "
            "here are context; suggest_tool files the registry entry."
        ),
        "method": "POST",
        "metadata_write": True,
        "additive": True,
        "path": "/api/v1/agent/feedback",
        # Session bookkeeping, but an APPEND: a retry files a second row
        # (v2.343.2).
        "idempotent": False,
        "body_params": [
            "source", "prompt_version", "test_plan_id",
            "execution_session_id", "assist_session_id", "overall_rating",
            "api_critiques", "tool_suggestions", "friction_notes", "agent_metrics",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["assist", "reconnaissance", "plan_generation", "in_session_execution"],
                    "description": "The kind of work this feedback is about.",
                },
                "prompt_version": {"type": "string", "description": "The prompt_version from your instructions block."},
                "test_plan_id": {"type": "integer", "minimum": 1},
                "execution_session_id": {"type": "integer", "minimum": 1},
                "assist_session_id": {
                    "type": "integer", "minimum": 1,
                    "description": "Only with source=assist, on a pre-consolidation assist session; normally omit — the session comes from your key.",
                },
                "overall_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "api_critiques": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "endpoint": {"type": "string"}, "issue": {"type": "string"},
                        "suggestion": {"type": "string"},
                    }},
                },
                "tool_suggestions": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "category": {"type": "string"},
                        "rationale": {"type": "string"},
                    }},
                },
                "friction_notes": {"type": "string", "description": "What was confusing, slow, or guessed."},
                "agent_metrics": {
                    "type": "object",
                    "description": "agent_name, model, tool_calls_total, notes — whatever your environment exposes.",
                },
            },
            "required": ["source"],
            "additionalProperties": False,
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
            "assist_get_host_vulnerabilities for the scanner vulns on one. Call this first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/context",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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
            "first (default: by address). Paginate with limit/offset — but for a "
            "COUNT use assist_count_hosts, not the length of a page. Returns host briefs."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts",
        "query_params": [
            "q", "search", "state", "ports", "services", "subnets",
            "has_critical_vulns", "has_high_vulns", "limit", "offset",
            "sort_by", "sort_order",
        ],
        # The endpoint's own default is 500 — right for a file download, a lot
        # of tokens for a model that usually wants the first handful.
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Boolean query DSL (see tool description)."},
                "search": {"type": "string", "description": "Substring match on IP, hostname, or OS."},
                "state": {"type": "string", "description": "Host state filter (e.g. up)."},
                "ports": {"type": "string", "description": "Comma-separated port numbers."},
                "services": {"type": "string", "description": "Comma-separated service names."},
                "subnets": {"type": "string", "description": "Comma-separated CIDR blocks."},
                "has_critical_vulns": {"type": "boolean"},
                "has_high_vulns": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "ip_address", "critical_vulns", "high_vulns", "exploitable_vulns",
                        "open_ports", "note_count", "discovery_count", "hostname", "last_seen",
                    ],
                    "default": "ip_address",
                },
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
            },
            "additionalProperties": False,
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
        "query_params": [
            "q", "search", "state", "ports", "services", "subnets",
            "has_critical_vulns", "has_high_vulns",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Boolean query DSL (see assist_list_hosts)."},
                "search": {"type": "string"},
                "state": {"type": "string"},
                "ports": {"type": "string"},
                "services": {"type": "string"},
                "subnets": {"type": "string"},
                "has_critical_vulns": {"type": "boolean"},
                "has_high_vulns": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "assist_get_host": {
        "description": (
            "Full detail for one host, as the host inspector shows it: identity, "
            "names seen at the address, OS detail, ports with service detail and "
            "NSE script output (bounded), severity counts, your review status, tags, "
            "assignees, scope membership, per-domain assessment state, weakness "
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
        "path_params": ["host_id"],
        "path_alternatives": {"ip": "/api/v1/agent/assist/hosts/by-ip/{ip}"},
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "ip": {"type": "string", "maxLength": 64, "description": "The host's address, e.g. 10.0.0.5."},
            },
            "additionalProperties": False,
        },
    },
    "assist_get_host_vulnerabilities": {
        "description": (
            "Every raw scanner vulnerability on a host with evidence: severity, "
            "CVE/plugin id, title, affected port/service, CVSS, description, "
            "remediation, scanner evidence. Worst-severity first. Use this to cite "
            "specifics in a report, not just counts. NOTE: these are scanner rows — "
            "each `id` is a vulnerability id, NOT a project-Finding id, so do not "
            "pass it to assist_get_finding. The triaged project Findings (the spine "
            "assist_list_findings / assist_get_finding work on) are a separate set."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/findings",
        "path_params": ["host_id"],
        "query_params": ["severity", "limit", "offset"],
        "defaults": {"limit": 50},
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "severity": {
                    "type": "string",
                    "description": "Comma-separated severities to include (critical/high/medium/low/info). Default: all.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["host_id"],
            "additionalProperties": False,
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
        "query_params": [
            "status", "severity", "source", "host_id", "unowned", "owner",
            "search", "limit", "offset",
        ],
        "defaults": {"limit": 25},
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "open / confirmed / false_positive / accepted_risk / remediated / "
                        "retest (assist_get_vocabulary lists them), or 'all' / omitted "
                        "for every status."
                    ),
                },
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                "source": {"type": "string"},
                "host_id": {"type": "integer", "minimum": 1},
                "unowned": {"type": "boolean", "description": "Only findings with no owner."},
                "owner": {"type": "string", "description": "Username, or 'me' for this session's operator."},
                "search": {"type": "string", "maxLength": 200},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 25},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
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
        "path_params": ["host_id"],
        "query_params": ["limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
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
        "path_params": ["host_id"],
        "query_params": ["limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
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
        "query_params": ["job_id", "limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    "assist_get_host_notes": {
        "description": (
            "What the team has already written about this host. Read this "
            "BEFORE adding a note — a colleague may have recorded the same "
            "observation an hour ago — and before answering \"what do we know "
            "about X\", where the answer often lives in a note rather than in "
            "scan data. Notes carry who wrote them and whether an agent did, the "
            "thread (parent_id / thread_root_id), type, status, assignee, due date, "
            "the finding a thread was promoted to, and attachments as download "
            "references. Paged, newest first: read `total` and `has_more`, and pass "
            "`offset` to continue — a page is not the whole record."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/notes",
        "path_params": ["host_id"],
        "query_params": ["limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_get_coverage": {
        "description": (
            "How much of this project has actually been assessed, per domain "
            "(port discovery, service detection, vulnerability assessment, web, "
            "TLS…). Every other tool reports what WAS found; this is what stops "
            "\"no critical findings\" being reported as \"no critical "
            "exposure\". Cite it whenever a report or an answer implies "
            "completeness."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/coverage",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_get_host_testing": {
        "description": (
            "What has been PLANNED or RUN against this host by the team: "
            "plan entries, the tests proposed for each, and the recorded "
            "results (command, outcome, findings, severity). This is how you "
            "tell a scanner's claim from something someone tested — say which "
            "it is when you report a finding. Archived plans and rejected "
            "entries are left out."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/testing",
        "path_params": ["host_id"],
        "input_schema": {
            "type": "object",
            "properties": dict(HOST_ID_PROP),
            "required": ["host_id"],
            "additionalProperties": False,
        },
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
        "query_params": ["limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
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
            "not a clean bill of health, and reporting it as one is wrong."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/posture",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # v2.428.0 — Operations, Evidence gaps and scan compare, each the SAME
    # service its page uses (agent_assist_operations.py).
    "assist_get_workbench": {
        "description": (
            "Your operator's Operations 'My work', as they see it: hosts they are "
            "reviewing (my_queue), plan steps (my_tasks), note threads assigned to "
            "them, findings they own, team review, follow-ups ('needs another "
            "look'), blockers, and since_last_visit — scans, new and changed hosts, "
            "new critical/high scanner observations since they last marked "
            "Operations seen. Answers 'what's mine?' and 'what changed since I was "
            "last here?'. Reading never marks anything seen. *_unavailable=true "
            "means that section could not be computed — say so, never 'nothing'. "
            "The lists are PREVIEWS as on the page (my_queue 10, tasks/notes/"
            "findings/follow-ups 15): use the section's own count — "
            "in_review_count, total_open, total — for 'how many', never the "
            "list length. blockers.failed_import_count counts failed imports "
            "nobody has dismissed and no later clean import superseded, so it is "
            "smaller than assist_list_ingestion_issues' failed. investigate is "
            "null here because the 'Worth a look' queue is not embedded — null is "
            "not an empty queue; read it with assist_list_worth_a_look."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_worth_a_look": {
        "description": (
            "Operations' 'Worth a look' queue: hosts NOBODY has touched (no review, "
            "note, plan entry or finding) that carry an observed weakness or a "
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
        "query_params": ["tier", "limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                "tier": {"type": "integer", "minimum": 1, "maximum": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    "assist_get_terrain": {
        "description": (
            "The Operations terrain as numbers: per /24 (IPv6 /64), hosts tested / "
            "planned / worked / untouched (exclusive, adding up to hosts), plus "
            "critical and critical_untouched. Answers 'which ranges has nobody "
            "touched?'. sort=untouched or critical_untouched puts the neglected "
            "blocks first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/workbench/terrain",
        "query_params": ["sort", "limit"],
        "input_schema": {
            "type": "object",
            "properties": {
                "sort": {"type": "string", "enum": ["address", "untouched", "critical_untouched"], "default": "address"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            },
            "additionalProperties": False,
        },
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
        "query_params": ["domain", "segment", "limit"],
        "defaults": {"limit": 50},
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "segment": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
            },
            "required": ["domain"],
            "additionalProperties": False,
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
        "query_params": ["a", "b", "limit"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "Baseline scan id"},
                "b": {"type": "integer", "description": "Later scan id"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
    "assist_list_ingestion_issues": {
        "description": (
            "Uploads that failed, are still in flight, or parsed but dropped "
            "rows. CHECK THIS BEFORE REPORTING THAT SOMETHING IS ABSENT: 'no "
            "web servers in that range' and 'the httpx upload failed to parse' "
            "look identical from every other tool, and only one of them is a "
            "finding about the network. kind=failed means nothing from that "
            "file is in the project; kind=degraded means the file IS in the "
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
        "query_params": ["limit"],
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "additionalProperties": False,
        },
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
            "`host_count` is distinct addresses, `endpoint_count` rows), and "
            "references to any attached screenshots. Every note says whether a "
            "person or an agent wrote it (`actor_type`). Use this when writing "
            "a finding up: assist_list_findings gives you titles and "
            "severities, this gives you what to cite. Screenshots come back as "
            "references (filename, size, download_path): look at one with "
            "assist_get_image, or save it beside a report from its download_path "
            "with the session's API key. scanner_evidence and execution_evidence say "
            "whether a claim rests on a scanner's output or on a command a "
            "tester actually ran; state which, they are different assertions. "
            "Also: `report_text` (what the client report says — description, "
            "impact, recommendation, references, steps to reproduce, CVSS vector "
            "and score), `endpoint_status_counts` (per-host state), and "
            "`status_history` (who changed the status, when, from → to, and why)."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/findings/{finding_id}",
        "path_params": ["finding_id"],
        "input_schema": {
            "type": "object",
            "properties": {
                "finding_id": {
                    "type": "integer",
                    "description": (
                        "Project-Finding id, from assist_list_findings — NOT a "
                        "per-host vulnerability id from assist_get_host_vulnerabilities."
                    ),
                },
            },
            "required": ["finding_id"],
            "additionalProperties": False,
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
            "issues. assist_list_observation_hosts lists one issue's hosts."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scanner-observations",
        "query_params": ["search", "severity", "kind", "include_judged", "min_hosts", "sort", "skip", "limit"],
        "defaults": {"limit": 25},
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "maxLength": 200},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                "kind": {"type": "string", "enum": ["misconfiguration", "vulnerability", "informational"]},
                "include_judged": {"type": "boolean", "default": False},
                "min_hosts": {"type": "integer", "minimum": 1, "default": 1},
                "sort": {"type": "string", "enum": ["severity", "hosts"], "default": "severity"},
                "skip": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
            },
            "additionalProperties": False,
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
        "query_params": ["issue_key", "limit", "offset"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_key": {"type": "string", "minLength": 1, "maxLength": 600},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["issue_key"],
            "additionalProperties": False,
        },
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_get_client_report": {
        "description": (
            "One client report and what it says: engagement details, executive "
            "summary, counts, and every finding as the report states it (ref, "
            "report text, affected endpoints, evidence attachment ids). An issued "
            "report is its frozen text (content_source issued_snapshot); a draft "
            "is what it would say now (draft_live). For 'what did we tell the "
            "client', read the latest issued one. Files carry a download_path."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/client-reports/{report_id}",
        "path_params": ["report_id"],
        "input_schema": {
            "type": "object",
            "properties": {"report_id": {"type": "integer", "minimum": 1}},
            "required": ["report_id"],
            "additionalProperties": False,
        },
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
        "path_params": ["attachment_id"],
        "path_alternatives": {
            "interface_id": "/api/v1/agent/assist/web-interfaces/{interface_id}/screenshot",
        },
        "result": "image",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "integer", "minimum": 1},
                "interface_id": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    "assist_list_recent_notes": {
        "description": (
            "Recent notes across the whole project, newest first — what the "
            "team has been working on, as opposed to what a scanner found. "
            "Filter by status (open notes are the outstanding-work list this "
            "project actually keeps) or by author ('me' or a username). Each "
            "note names its `target` ({kind: host/port/finding/scan/scope/"
            "test_plan/project, id, label}) and carries the same thread, "
            "assignee and attachment fields as assist_get_host_notes."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/notes",
        "query_params": ["limit", "status", "author"],
        "defaults": {"limit": 25},
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
                "status": {"type": "string", "enum": ["open", "in_progress", "resolved"]},
                "author": {"type": "string", "description": "Username, or 'me'."},
            },
            "additionalProperties": False,
        },
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "query_params": ["q", "in_scope", "resolved", "host_id", "kind", "limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Case-insensitive substring on the FQDN."},
                "in_scope": {"type": "boolean", "description": "Only names a declared domain covers (true) / does not (false)."},
                "resolved": {"type": "boolean", "description": "Only names with (true) / without (false) a current A/AAAA answer."},
                "host_id": {"type": "integer", "description": "Only names currently resolving to this host's address."},
                "kind": {"type": "string", "enum": ["fqdn", "wildcard"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    "assist_list_scans": {
        "description": (
            "List the scans ingested into this project (most recent first). Each "
            "carries ingestion_job_id, the import that produced it — the job_id "
            "assist_list_uninterpreted_lines takes (a scan id is not a job id). "
            "tool narrows to one tool's scans, as the Scans page's chips do — "
            "'the last two nmap scans' is tool=nmap, limit=2."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/scans",
        "query_params": ["limit", "offset", "tool"],
        "defaults": {"limit": 50},
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "offset": {
                    "type": "integer", "minimum": 0,
                    "description": "Skip this many (newest first). Page until a page is shorter than limit.",
                },
                "tool": {"type": "string", "maxLength": 100, "description": "A tool name (nmap, nessus, netexec…) or scan type."},
            },
            "additionalProperties": False,
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
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "start_execution": {
        "description": (
            "Open an execution run on a test plan (a draft or one in progress) in your "
            "session, so you can record results against its entries. Returns the "
            "plan's hosts and a `read_back` to state before testing."
        ),
        "method": "POST",
        "path": "/api/v1/agent/execution-sessions/start",
        # A retry is refused (one active run per plan) or opens a second run
        # on a later call — either way not a converging write (v2.343.2).
        "idempotent": False,
        "body_params": ["plan_id", "agent_model"],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "The plan to execute."},
                **AGENT_MODEL_PROP,
            },
            "required": ["plan_id"],
            "additionalProperties": False,
        },
    },
    "create_test_plan": {
        "description": (
            "Register a test plan — the record of what you set out to test and, once "
            "executed, what you found. Fill it in with plan_add_entries, then "
            "start_execution when you are ready to work it; nothing waits on approval. "
            "To plan an EXACT set of hosts, pass host_ids, or q (a host query such as "
            "'follow:in_review OR assigned:me', resolved to its matching hosts now); "
            "plan_get_context then offers only those hosts."
        ),
        "method": "POST",
        "path": "/api/v1/agent/test-plans",
        # A retry creates a second draft plan (v2.343.2).
        "idempotent": False,
        "body_params": ["title", "description", "filter_criteria", "host_ids", "q", "agent_model"],
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Plan title."},
                "description": {"type": "string", "description": "Optional summary of scope/method."},
                "filter_criteria": {
                    "type": "object",
                    "description": (
                        "Optional: the host filters this plan is scoped to — plan_get_context "
                        "then pre-filters candidate_hosts by them."
                    ),
                    "properties": {
                        "subnets": {"type": "array", "items": {"type": "string"}},
                        "ports": {"type": "array", "items": {"type": "integer"}},
                        "services": {"type": "array", "items": {"type": "string"}},
                        "min_severity": {"type": "string"},
                        "has_critical_vulns": {"type": "boolean"},
                        "has_high_vulns": {"type": "boolean"},
                        "search": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "host_ids": {
                    "type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 10000,
                    "description": "Plan exactly these hosts (ids from assist_list_hosts). Not with q.",
                },
                "q": {
                    "type": "string",
                    "description": "A host query, resolved to its matching hosts when the plan is created. Not with host_ids.",
                },
                **AGENT_MODEL_PROP,
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    # --- assist writes (allowed iff the operator's project role permits writes) ---
    "assist_add_note": {
        "description": (
            "Add a note to a host. Writes project data, so it succeeds only if the "
            "operator who started your session may write to this project — check "
            "`can_write_project_data` on agent_identity rather than probing. "
            "Notes are stamped agent-authored and appear in the operator's UI and in "
            "client-facing reports — record observations tied to host/port/finding "
            "evidence, mark inferences as inferences."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/notes",
        "path_params": ["host_id"],
        "body_params": ["body", "status"],
        "additive": True,
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "body": {"type": "string", "minLength": 1, "description": "Note text."},
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "resolved"],
                    "default": "open",
                },
            },
            "required": ["host_id", "body"],
            "additionalProperties": False,
        },
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
        "path_params": ["host_id"],
        "body_params": ["status"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "status": {
                    "type": "string",
                    "enum": ["watching", "in_review", "reviewed", "none"],
                    "description": "watching / in_review / reviewed, or `none` to clear the follow.",
                },
            },
            "required": ["host_id", "status"],
            "additionalProperties": False,
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
        "path_params": ["host_id"],
        "body_params": ["hostname", "os_name"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "hostname": {"type": "string", "maxLength": 255},
                "os_name": {"type": "string", "maxLength": 255},
            },
            "required": ["host_id"],
            # v2.313.0 — an `anyOf` used to encode "send at least one field", so
            # a client could catch the endpoint's 400 before making the call.
            # It cost more than it bought: a top-level `anyOf` makes some hosts
            # (Codex among them) present the whole tool as an opaque object
            # union instead of three typed parameters, so the model has to guess
            # argument names to make a call that would otherwise be obvious. The
            # constraint is stated in the description and enforced by the
            # endpoint; typed parameters are worth more than a client-side
            # pre-check only some clients perform.
            "additionalProperties": False,
        },
    },
    # -----------------------------------------------------------------------
    # Plans — the record of what an agent sets out to test.  Nothing here
    # executes anything.
    # -----------------------------------------------------------------------
    "plan_get_context": {
        "description": (
            "Everything you need to draft this plan: candidate hosts with their open "
            "ports, services and existing findings, plus the selection policy and an "
            "entry template. Call this first — proposing tests without it means "
            "proposing against hosts you have not looked at. plan_id is resolved from "
            "your key. Page with `after_host_id` (the last host id from the previous "
            "page); use `detail_level=brief` to pick candidates cheaply, then `full` "
            "for the hosts you will write entries for."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/context",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "query_params": ["limit", "after_host_id", "include_zero_port", "detail_level"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — your key is bound to one plan.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                "after_host_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Cursor: return only hosts with id greater than this. Pass the "
                        "last host id from the previous page to fetch the next batch."
                    ),
                },
                "include_zero_port": {
                    "type": "boolean",
                    "description": "Include hosts with no open ports (excluded by default).",
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["brief", "full"],
                    "description": (
                        "'brief' = summary fields only (no ports array), for candidate "
                        "selection; 'full' (default) = full port detail per host."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    "plan_list": {
        "description": (
            "List the project's test plans, newest first. Every plan in the "
            "project is listed (any session may fill in or execute one its "
            "operator may); `mine=true` narrows to the plans this session drafted."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans",
        "query_params": ["status", "mine"],
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by plan status (e.g. draft)."},
                "mine": {"type": "boolean", "description": "Only the plans this session drafted."},
            },
            "additionalProperties": False,
        },
    },
    "plan_get": {
        "description": (
            "The plan with its entries — what you have proposed so far and each "
            "entry's status. plan_id is resolved from your key."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
            },
            "additionalProperties": False,
        },
    },
    # Opening a plan is `create_test_plan` (above, with the other phase
    # openers); the tools from here on fill it in.
    "plan_update": {
        "description": (
            "Set the plan's title/description and record which model and harness "
            "drafted it. Describe the scope, prioritisation and methodology — it is "
            "what a reader of the plan sees first."
        ),
        "method": "PATCH",
        "path": "/api/v1/agent/test-plans/{plan_id}",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "title", "description", "generated_by_model", "generated_by_tool",
            "prompt_version",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {
                    "type": "string",
                    "description": "Scope, prioritisation and methodology. Required before submit.",
                },
                "generated_by_model": {"type": "string", "description": "Model you are running as."},
                "generated_by_tool": {"type": "string", "description": "Harness you run in."},
                "prompt_version": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "plan_add_entries": {
        "description": (
            "Add proposed tests to the plan, one entry per host. Each entry carries a "
            "rationale a reader of the plan will see — say what the evidence is and what "
            "the test would establish, not just what you would run. Use the structured "
            "proposed_tests form (tool + description + command). Batch related hosts in "
            "one call."
        ),
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": ["entries"],
        "additive": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "items": {
                        "type": "object",
                        "properties": {
                            **HOST_ID_PROP,
                            "priority": {
                                "type": "string",
                                "enum": ["critical", "high", "medium", "low"],
                            },
                            "test_phase": TEST_PHASE_FIELD,
                            "proposed_tests": {
                                "type": "array",
                                "minItems": 1,
                                "items": _PROPOSED_TEST_ITEM,
                            },
                            "rationale": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Why this host and these tests — the reviewer reads this.",
                            },
                            "notes": {"type": "string"},
                            "target_fqdn": {
                                "type": "string",
                                "maxLength": 253,
                                "description": (
                                    "Optional named endpoint on this host the tests are against (must be one "
                                    "of the host's `names`). Set it for web tests behind a shared address; "
                                    "use {fqdn} in commands."
                                ),
                            },
                        },
                        "required": ["host_id", "priority", "test_phase", "proposed_tests", "rationale"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
    "plan_update_entry": {
        "description": (
            "Revise one entry — usually to act on reviewer feedback before "
            "resubmitting. Send only the fields you are changing."
        ),
        "method": "PATCH",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "priority", "test_phase", "proposed_tests", "rationale", "status",
            "findings", "results_data", "notes", "expected_updated_at",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entry_id": {"type": "integer", "minimum": 1},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "test_phase": TEST_PHASE_FIELD,
                "proposed_tests": {"type": "array", "items": _PROPOSED_TEST_ITEM},
                "rationale": {"type": "string"},
                "status": ENTRY_STATUS_FIELD,
                "findings": {"type": "string"},
                "results_data": {
                    "type": "object",
                    "description": "Structured results for the entry (free-form object), as the REST body takes it.",
                },
                "notes": {"type": "string"},
                "expected_updated_at": {
                    "type": "string",
                    "description": (
                        "The entry's updated_at as you last read it. Send it to make "
                        "the write conditional — it fails rather than overwriting a "
                        "change someone else made in between."
                    ),
                },
            },
            "required": ["entry_id"],
            "additionalProperties": False,
        },
    },
    "plan_validate": {
        "description": (
            "Check the plan for gaps — no entries, a missing description, a too-short "
            "rationale — and report its candidate-host coverage. Advice, not a gate."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/validate",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
            },
            "additionalProperties": False,
        },
    },
    # -----------------------------------------------------------------------
    # Execution — records what the agent ran against a plan's hosts.  The
    # commands run on the operator's machine, under their client's sandbox;
    # BlueStick records.
    # -----------------------------------------------------------------------
    "execution_get_context": {
        "description": (
            "The plan to work through: every entry with its host, proposed tests, "
            "priority and current status. "
            "Work entries in the order given. plan_id is resolved from your key."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/execution-context",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
            },
            "additionalProperties": False,
        },
    },
    "execution_record_sanity_check": {
        "description": (
            "Record a target check — evidence that you reached the host you meant to "
            "(resolved IP, banner, source address). Optional; worth recording when the "
            "target could be ambiguous (a name behind a load balancer, a reassigned "
            "address). A failed check is worth raising with the operator."
        ),
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/sanity-check",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "method", "target_ip", "port_checked", "expected_value", "actual_value",
            "source_ip", "dns_result", "passed", "details",
        ],
        "additive": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entry_id": {"type": "integer", "minimum": 1},
                "method": SANITY_METHOD_FIELD,
                "target_ip": {"type": "string", "description": "The IP you actually reached."},
                "port_checked": {"type": "integer", "minimum": 1, "maximum": 65535},
                "expected_value": {"type": "string"},
                "actual_value": {"type": "string"},
                "source_ip": {"type": "string", "description": "The address you tested FROM."},
                "dns_result": {"type": "string"},
                "passed": {"type": "boolean", "description": "Did the target match what you expected."},
                "details": {"type": "string"},
            },
            "required": ["entry_id", "method", "target_ip", "passed"],
            "additionalProperties": False,
        },
    },
    "execution_record_test_result": {
        "description": (
            "Record what one proposed test produced: the exact command you ran, its "
            "output, and whether it is a finding. test_index is the position of the "
            "test in the entry's proposed_tests. Record results as you go — an entry "
            "cannot complete with no results recorded."
        ),
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/test-results",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "test_index", "status", "command_run", "raw_output", "findings_summary",
            "severity", "is_finding", "observed_ip",
        ],
        "additive": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entry_id": {"type": "integer", "minimum": 1},
                "test_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Index into the entry's proposed_tests array.",
                },
                "status": TEST_RESULT_STATUS_FIELD,
                "command_run": {
                    "type": "string",
                    "description": (
                        "The command as actually executed, verbatim. This is the audit "
                        "record — do not paraphrase it or drop the output path."
                    ),
                },
                "raw_output": {"type": "string", "description": "Tool output, trimmed if huge."},
                "findings_summary": {"type": "string"},
                "severity": {"type": "string"},
                "is_finding": {"type": "boolean", "default": False},
                "observed_ip": {
                    "type": "string",
                    "maxLength": 45,
                    "description": (
                        "The IP the command actually reached, when the entry targets a name "
                        "(target_fqdn). A name behind a load balancer may resolve differently at "
                        "run time; recording it keeps the evidence tied to the real address."
                    ),
                },
            },
            "required": ["entry_id", "test_index", "status"],
            "additionalProperties": False,
        },
    },
    "execution_complete_entry": {
        "description": (
            "Close out an entry once its tests are recorded. Closing one with proposed "
            "tests that have no result needs no_tests_run_reason, which is audit-logged."
        ),
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/complete",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "findings_summary", "overall_status", "no_tests_run_reason",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entry_id": {"type": "integer", "minimum": 1},
                "findings_summary": {"type": "string"},
                "overall_status": {
                    "type": "string",
                    "enum": ["completed", "rejected"],
                    "default": "completed",
                },
                "no_tests_run_reason": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Why the entry is closing with no test results recorded.",
                },
            },
            "required": ["entry_id"],
            "additionalProperties": False,
        },
    },
    "execution_get_progress": {
        "description": (
            "Live progress for this execution session: entries done, in flight and "
            "remaining. Use it to resume after an interruption instead of re-running "
            "work that is already recorded."
        ),
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/execution-progress",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
            },
            "additionalProperties": False,
        },
    },
    "execution_complete_session": {
        "description": (
            "Close the execution session with a summary. Use overall_status 'failed' "
            "when you are stopping because the engagement broke rather than because "
            "the work finished — that distinction is what a reviewer needs. The "
            "response carries feedback_recorded: when false, submit_feedback with "
            "this run's friction before you go on (v2.343.0)."
        ),
        "method": "POST",
        "path": "/api/v1/agent/execution-sessions/{session_id}/complete",
        "path_params": ["session_id"],
        # v2.338.0 — filled from the execution-specific identity field, not the
        # old recon-or-execution ``workflow_session_id`` that handed this route
        # a recon run's id whenever both phases were open.
        "auto_params": {"session_id": "execution_session_id"},
        "body_params": ["notes", "overall_status"],
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "notes": {
                    "type": "string",
                    "maxLength": 8192,
                    "description": "Closing summary: coverage, gaps, environment problems.",
                },
                "overall_status": {
                    "type": "string",
                    "enum": ["completed", "failed"],
                    "default": "completed",
                },
            },
            "additionalProperties": False,
        },
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
        "path_params": ["scope_id"],
        "query_params": ["limit", "offset"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                **SCOPE_ID_PROP,
            },
            "required": ["scope_id"],
            "additionalProperties": False,
        },
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
        "path_params": ["scope_id"],
        "query_params": ["limit", "offset"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                **SCOPE_ID_PROP,
            },
            "required": ["scope_id"],
            "additionalProperties": False,
        },
    },
    "get_upload_job": {
        "description": (
            "Poll an upload's parse status. Upload itself is a file POST you run with "
            "curl (POST /agent/uploads, see the server instructions); this is how you "
            "find out whether it parsed, and what it produced."
        ),
        "method": "GET",
        "path": "/api/v1/agent/uploads/{job_id}",
        "path_params": ["job_id"],
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Job id returned by the upload.",
                },
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
}


# v2.337.0 — a single project session sees every tool, so nothing gates
# ``tools/list`` any more.  What remains is a PRESENTATION grouping for the MCP
# reference page (which kind of work a tool belongs to), and v2.338.0 derives
# it from the tool's name in this one function instead of carrying a
# ``workflows`` field on every entry that a loop then rewrote.  Universal tools
# (identity, the guide/catalogue readers, the session bookkeeping, the phase
# openers any session calls) report every kind and the page shows them as
# shared.
_KIND_BY_PREFIX = (
    ("assist_", _ASSIST),
    ("scope_", _SCOPE),
    ("plan_", _PLAN),
    ("execution_", _EXEC),
)


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
    # completions, and wrong for anything that CREATES — create_test_plan and
    # start_execution each mint a new row per call, and feedback
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
