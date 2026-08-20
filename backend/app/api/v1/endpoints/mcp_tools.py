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
    capability   : the write capability the underlying endpoint requires
    defaults     : MCP-side argument defaults (smaller pages than the endpoints')
    auto_params  : arguments filled from the caller's own identity when omitted
    additive     : True iff the write only appends (drives destructive/idempotent
                   annotations)

**``workflows`` is an entry-point affordance, not a security boundary.**  Hiding
a tool from a key that cannot use it stops the model from trying a call whose
403 it would read as its own bug — the same reason capability-less writes are
hidden.  It decides nothing: every dispatch still loops back through the real
endpoint, and ``require_plan_scope`` / ``require_recon_scope`` /
``require_capability`` make the actual decision there.  The MCP layer makes no
security decision anywhere, and this file must not become the place it starts.

**Three entry points, deliberately.**  Recon, plan generation, and execution are
separate sessions with separate keys because the operator starts each one
knowingly — that is the control the workflow is designed around.  A single
"do everything" key would collapse that, and no tool list here should imply one
exists.

What is deliberately NOT a tool
-------------------------------
The bulk, file-shaped endpoints: ``report-context.ndjson``, ``recon/hosts.ndjson``,
``recon/live-hosts.txt``, ``recon/web-targets.txt``, and ``POST recon/upload``.
They are meant to stream to (or from) a file on the operator's disk, not to be
materialised into a model's context — a 40k-host target list read through a tool
call is the same data, minus the ability to pipe it into the next scanner, plus
the token bill.  The server ``instructions`` point at them with curl instead.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# The four workflows a key can belong to (AgentSessionWorkflow values).  Kept as
# plain strings rather than importing the enum: this module is pure data with no
# DB dependency, and the values are already a wire contract via /agent/identity.
WORKFLOW_ASSIST = "assist"
WORKFLOW_PLAN_GENERATION = "plan_generation"
WORKFLOW_EXECUTION = "execution"
WORKFLOW_RECON = "recon"

ALL_WORKFLOWS = frozenset(
    {WORKFLOW_ASSIST, WORKFLOW_PLAN_GENERATION, WORKFLOW_EXECUTION, WORKFLOW_RECON}
)

_ASSIST = frozenset({WORKFLOW_ASSIST})
_PLAN = frozenset({WORKFLOW_PLAN_GENERATION})
_EXEC = frozenset({WORKFLOW_EXECUTION})
_RECON = frozenset({WORKFLOW_RECON})

HOST_ID_PROP = {
    "host_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Numeric host id (from assist_list_hosts).",
    }
}

# Shared environment-probe fields.  All three probes accept the same body (the
# endpoints share `apply_environment_probe`), so the schema is written once —
# a field added for recon must not silently go missing for execution.
_PROBE_PROPERTIES = {
    "os_family": {"type": "string", "enum": ["windows", "darwin", "linux"]},
    "os_release": {"type": "string"},
    "arch": {"type": "string"},
    "shell": {"type": "string", "description": "e.g. bash, zsh, powershell."},
    "powershell_version": {"type": "string"},
    "powershell_execution_policy": {
        "type": "string",
        "description": "Windows only — e.g. RemoteSigned, Restricted.",
    },
    "python": {"type": "string", "description": "Path or command that runs a real Python."},
    "python_version": {"type": "string"},
    "wsl_available": {"type": "boolean"},
    "notes": {"type": "string"},
    "agent_model": {"type": "string", "description": "Model you are running as."},
    "agent_tool": {"type": "string", "description": "Harness you run in (e.g. claude-code)."},
    "agent_prompt_version": {
        "type": "string",
        "description": "PROMPT_VERSION from your session's instructions.",
    },
}

_PROBE_BODY_PARAMS = list(_PROBE_PROPERTIES)

# A proposed test, as the plan entries carry it.  Mirrors ProposedTest in
# app/schemas/schemas.py — `tool` is the field the tool registry is checked
# against, which is why the free-string form is discouraged in the description.
_PROPOSED_TEST_ITEM = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "description": (
                "Tool name as BlueStick registers it (e.g. nmap, testssl, netexec). "
                "Use suggest_tool if what you need isn't in the approved set."
            ),
        },
        "description": {"type": "string", "description": "What this test establishes."},
        "command": {
            "type": "string",
            "description": (
                "Exact command the operator would run. Write output into the "
                "working directory the session runs in — a command that writes "
                "elsewhere needs the operator's explicit approval."
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
            "What your API key is: workflow (assist / plan_generation / execution / "
            "recon), bound project, session ids, write capabilities, the operator you "
            "act for, and when the key expires. Call this first if you are unsure "
            "which workflow you are in — the available tools differ per workflow."
        ),
        "workflows": ALL_WORKFLOWS,
        "method": "GET",
        "path": "/api/v1/agent/identity",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "read_agent_guide": {
        "description": (
            "AGENTS.md — the authoritative guide for how to work with BlueStick: the "
            "approval and sanity-check protocol, the working-directory rules, endpoint "
            "body shapes, upload formats, exit criteria. Sliced to your own workflow, "
            "resolved from your key. READ THIS FIRST, once, before your first "
            "substantive call. The tool descriptions here are a skeleton; the guide is "
            "the part that tells you how, and it is binding."
        ),
        "workflows": ALL_WORKFLOWS,
        "method": "GET",
        "path": "/api/v1/agents-guide",
        "query_params": ["workflow"],
        # Filled from the caller's identity: the slice you want is the workflow
        # your key belongs to, and asking a model to name it invites the one
        # answer that returns another workflow's instructions.
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
    "list_approved_tools": {
        "description": (
            "The tools BlueStick approves for agents to run, with the ports, install "
            "command and phase metadata for each. This is the list the approval "
            "guardrail keys off — read it before telling the operator what you may run "
            "unprompted, and before assuming a tool you know is available here. Pass "
            "status to see the documented-but-not-approved set instead."
        ),
        "workflows": ALL_WORKFLOWS,
        "method": "GET",
        "path": "/api/v1/references/tools",
        "query_params": ["status", "category"],
        # Always send a status: the unfiltered listing is 60+ tools, most of them
        # documentation for humans, and an agent reading that as "what I may run"
        # is the exact confusion the status column exists to prevent.
        "defaults": {"status": "approved"},
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["approved", "reference", "suggested", "rejected"],
                    "description": (
                        "approved = you may run it. reference = documented for the "
                        "operator, not for you. suggested = someone asked, nobody has "
                        "vetted it yet."
                    ),
                },
                "category": {"type": "string", "description": "Filter to one category."},
            },
            "additionalProperties": False,
        },
    },
    "suggest_tool": {
        "description": (
            "Ask for a tool that isn't in BlueStick's approved set. Records the request "
            "with your rationale for a human to vet; it does NOT grant permission to "
            "run anything. Use this instead of silently substituting an unapproved "
            "tool — a recorded ask is how the approved set grows."
        ),
        "workflows": ALL_WORKFLOWS,
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
                        "What you needed it for and why the approved set doesn't cover "
                        "it. This is what a human reads when vetting — be specific."
                    ),
                },
                "category": {"type": "string", "maxLength": 100},
                "description": {"type": "string", "maxLength": 2000},
            },
            "required": ["name", "rationale"],
            "additionalProperties": False,
        },
    },
    # -----------------------------------------------------------------------
    # Assist — interactive read/write over an existing inventory
    # -----------------------------------------------------------------------
    "assist_get_context": {
        "description": (
            "Project orientation for this assist session: host/port/scope/scan "
            "totals, the scope list (capped at 50), and recent scans. It carries "
            "NO findings — use assist_list_hosts to locate hosts and "
            "assist_get_host_findings for the findings on one. Call this first."
        ),
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/context",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_hosts": {
        "description": (
            "List/filter hosts in the project. Prefer the `q` boolean DSL (same "
            "vocabulary as the Hosts page: port:, os:, service:, subnet:, tag:, "
            "cve:, vuln:, tech:, has:, follow:, assigned: — combine with AND/OR/"
            "NOT and parentheses). assigned: takes me / any / none / a username, "
            "so 'has:critical AND assigned:none' is 'critical findings nobody "
            "owns'. Paginate with limit/offset — but for a COUNT use "
            "assist_count_hosts, not the length of a page. Returns host briefs."
        ),
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts",
        "query_params": [
            "q", "search", "state", "ports", "services", "subnets",
            "has_critical_vulns", "has_high_vulns", "limit", "offset",
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
        "workflows": _ASSIST,
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
            "Full detail for one host: identity, OS, per-port service detail, "
            "severity counts, and your review status. Notes and individual "
            "findings are separate — use assist_get_host_findings for findings."
        ),
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}",
        "path_params": ["host_id"],
        "input_schema": {
            "type": "object",
            "properties": dict(HOST_ID_PROP),
            "required": ["host_id"],
            "additionalProperties": False,
        },
    },
    "assist_get_host_findings": {
        "description": (
            "Every finding on a host with evidence: severity, CVE/plugin id, title, "
            "affected port/service, CVSS, description, remediation, scanner evidence. "
            "Worst-severity first. Use this to cite specifics in a report, not just counts."
        ),
        "workflows": _ASSIST,
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
            "many hosts — `host_count` is how big it is; counting rows is not."
        ),
        "workflows": _ASSIST,
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
                    "description": "open / triaged / confirmed / remediated / closed / false_positive, or 'all'.",
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
    "assist_get_host_notes": {
        "description": (
            "What the team has already written about this host. Read this "
            "BEFORE adding a note — a colleague may have recorded the same "
            "observation an hour ago — and before answering \"what do we know "
            "about X\", where the answer often lives in a note rather than in "
            "scan data. Notes carry who wrote them and whether an agent did."
        ),
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/notes",
        "path_params": ["host_id"],
        "query_params": ["limit"],
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
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
        "workflows": _ASSIST,
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
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/coverage",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_scopes": {
        "description": "List the network scopes (CIDR boundaries) defined for this project.",
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/scopes",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_scans": {
        "description": "List the scans ingested into this project (most recent first).",
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/scans",
        "query_params": ["limit"],
        "defaults": {"limit": 50},
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
            "additionalProperties": False,
        },
    },
    "assist_session_info": {
        "description": (
            "This assist session's identity: bound project, granted write capabilities, "
            "row-scope constraint, and the operator you act on behalf of. Call this to "
            "learn whether you may write and to whom `assigned:me` refers."
        ),
        "workflows": _ASSIST,
        "method": "GET",
        "path": "/api/v1/agent/assist/session",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_record_environment": {
        "description": (
            "Record the operator's environment (OS family, shell) on this assist "
            "session. REQUIRED FIRST STEP — the guide mandates it before other work, "
            "so BlueStick's guidance matches the machine you're actually on. The "
            "session is resolved from your key; you do not need to pass session_id."
        ),
        "workflows": _ASSIST,
        "method": "POST",
        "path": "/api/v1/agent/assist/sessions/{session_id}/environment",
        "path_params": ["session_id"],
        "auto_params": {"session_id": "workflow_session_id"},
        # Field names mirror EnvironmentSummary exactly — the schema advertises
        # additionalProperties:false and we reject unknown arguments, so a name
        # that doesn't exist server-side would be a hard error, not a silently
        # dropped field.
        "body_params": _PROBE_BODY_PARAMS,
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — resolved from your API key.",
                },
                **_PROBE_PROPERTIES,
            },
            # Assist only needs os_family + shell (see AGENTS.md); the rest are
            # accepted so a probe built for recon/execution posts unchanged.
            "required": ["os_family"],
            "additionalProperties": False,
        },
    },
    # --- assist writes (capability-gated by the underlying endpoint) ---
    "assist_add_note": {
        "description": (
            "Add a note to a host. Requires the write:notes capability on your key. "
            "Notes are stamped agent-authored and appear in the operator's UI and in "
            "client-facing reports — record observations tied to host/port/finding "
            "evidence, mark inferences as inferences."
        ),
        "workflows": _ASSIST,
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/notes",
        "path_params": ["host_id"],
        "body_params": ["body", "status"],
        "capability": "write:notes",
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
            "Set a host's review status. Requires the write:follow capability. Do NOT "
            "mark a host `reviewed` on your own initiative — reviewed is a human "
            "judgement with client-reportable weight; confirm with the operator first."
        ),
        "workflows": _ASSIST,
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/follow",
        "path_params": ["host_id"],
        "body_params": ["status"],
        "capability": "write:follow",
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "status": {"type": "string", "enum": ["watching", "in_review", "reviewed"]},
            },
            "required": ["host_id", "status"],
            "additionalProperties": False,
        },
    },
    "assist_patch_host": {
        "description": (
            "Correct a host's hostname and/or OS after investigation. Requires the "
            "write:host capability. Only these two operator-curated fields are editable "
            "— scan-derived facts (ports, services, vulns) are never mutated here. Send "
            "just the field you're fixing."
        ),
        "workflows": _ASSIST,
        "method": "PATCH",
        "path": "/api/v1/agent/hosts/{host_id}",
        "path_params": ["host_id"],
        "body_params": ["hostname", "os_name"],
        "capability": "write:host",
        "input_schema": {
            "type": "object",
            "properties": {
                **HOST_ID_PROP,
                "hostname": {"type": "string", "maxLength": 255},
                "os_name": {"type": "string", "maxLength": 255},
            },
            "required": ["host_id"],
            # host_id on its own is a no-op the endpoint rejects with 400 — say
            # "send at least one field" in the schema so the client catches it.
            "anyOf": [{"required": ["hostname"]}, {"required": ["os_name"]}],
            "additionalProperties": False,
        },
    },
    # -----------------------------------------------------------------------
    # Stage 2 — plan generation.  Reads what recon found, proposes tests, and
    # hands the draft to a human.  Nothing here executes anything.
    # -----------------------------------------------------------------------
    "plan_get_context": {
        "description": (
            "Everything you need to draft this plan: candidate hosts with their open "
            "ports, services and existing findings, plus the project's scopes. Call "
            "this first — proposing tests without it means proposing against hosts "
            "you have not looked at. plan_id is resolved from your key."
        ),
        "workflows": _PLAN,
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/context",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "query_params": ["limit", "offset", "min_severity", "host_ids"],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — your key is bound to one plan.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
                "min_severity": {
                    "type": "string",
                    "description": "Only include hosts with a finding at or above this severity.",
                },
                "host_ids": {
                    "type": "string",
                    "description": "Comma-separated host ids to restrict the context to.",
                },
            },
            "additionalProperties": False,
        },
    },
    "plan_list": {
        "description": "List the test plans this agent owns in the project, newest first.",
        "workflows": _PLAN,
        "method": "GET",
        "path": "/api/v1/agent/test-plans",
        "query_params": ["status", "limit", "offset"],
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by plan status (e.g. draft)."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "plan_get": {
        "description": (
            "The plan with its entries — what you have proposed so far, each entry's "
            "status, and the approval state. plan_id is resolved from your key."
        ),
        "workflows": _PLAN,
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
    # No `plan_create` tool: POST /agent/test-plans is behind `deny_scoped_keys`,
    # so a plan-bound key — which is the only kind of key this workflow issues —
    # can never use it. The operator creates the plan; the agent fills it in.
    "plan_update": {
        "description": (
            "Set the plan's title/description and record which model and harness "
            "drafted it. A description summarising scope, prioritisation and "
            "methodology is REQUIRED before plan_submit will accept the plan."
        ),
        "workflows": _PLAN,
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
            "rationale a human reviewer will read — say what the evidence is and what "
            "the test would establish, not just what you would run. Prefer the "
            "structured proposed_tests form (tool + description + command) over free "
            "strings: only the structured form can be checked against the approved "
            "tool set. Batch related hosts in one call."
        ),
        "workflows": _PLAN,
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
                            "test_phase": {
                                "type": "string",
                                "description": "Which phase of the engagement this belongs to.",
                            },
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
        "workflows": _PLAN,
        "method": "PATCH",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "priority", "test_phase", "proposed_tests", "rationale", "status",
            "findings", "notes", "expected_updated_at",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "entry_id": {"type": "integer", "minimum": 1},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "test_phase": {"type": "string"},
                "proposed_tests": {"type": "array", "items": _PROPOSED_TEST_ITEM},
                "rationale": {"type": "string"},
                "status": {"type": "string"},
                "findings": {"type": "string"},
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
            "Dry-run the checks plan_submit will apply — missing description, entries "
            "without proposed tests, hosts outside the project. Costs nothing and "
            "reports every problem at once, unlike submit, which stops at the first."
        ),
        "workflows": _PLAN,
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
    "plan_submit": {
        "description": (
            "Submit the draft for human approval. This ENDS your part of stage 2 — "
            "nothing in the plan runs until a human approves it, and execution needs "
            "a separate key the operator mints. Run plan_validate first."
        ),
        "workflows": _PLAN,
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/submit",
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
    # Stage 3 — execution.  Works an APPROVED plan on the operator's machine.
    # BlueStick records; the commands run on their host, under their client's
    # sandbox, and the operator approves them there.
    # -----------------------------------------------------------------------
    "execution_record_environment": {
        "description": (
            "Record the operator's environment on this execution session. REQUIRED "
            "FIRST STEP — the commands you propose have to match the machine they "
            "will run on (PowerShell execution policy, WSL, what's on PATH). "
            "session_id is resolved from your key."
        ),
        "workflows": _EXEC,
        "method": "POST",
        "path": "/api/v1/agent/execution-sessions/{session_id}/environment",
        "path_params": ["session_id"],
        "auto_params": {"session_id": "workflow_session_id"},
        "body_params": _PROBE_BODY_PARAMS,
        "capability": "write:execution",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — resolved from your API key.",
                },
                **_PROBE_PROPERTIES,
            },
            "required": ["os_family"],
            "additionalProperties": False,
        },
    },
    "execution_get_context": {
        "description": (
            "The approved plan to work through: every entry with its host, proposed tests, "
            "priority and current status, plus the environment probe echoed back. "
            "Work entries in the order given. plan_id is resolved from your key."
        ),
        "workflows": _EXEC,
        "method": "GET",
        "path": "/api/v1/agent/test-plans/{plan_id}/execution-context",
        "path_params": ["plan_id"],
        "auto_params": {"plan_id": "plan_id"},
        "query_params": ["limit", "offset", "status"],
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1, "description": "Usually omit."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
                "status": {"type": "string", "description": "Filter entries by status."},
            },
            "additionalProperties": False,
        },
    },
    "execution_record_sanity_check": {
        "description": (
            "Record that you verified you are pointed at the right host before testing "
            "it — resolved IP, banner, or whatever the method was. An entry cannot be "
            "completed without a PASSING check on file unless you give "
            "execution_complete_entry an override_reason. Do this per host, per entry."
        ),
        "workflows": _EXEC,
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
                "method": {
                    "type": "string",
                    "description": "How you verified the target (see the guide's sanity-check methods).",
                },
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
        "workflows": _EXEC,
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/test-results",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "test_index", "status", "command_run", "raw_output", "findings_summary",
            "severity", "is_finding", "sanity_override_reason",
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
                "status": {
                    "type": "string",
                    "description": "Outcome of running it (e.g. completed, failed, skipped).",
                },
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
                "sanity_override_reason": {"type": "string", "maxLength": 500},
            },
            "required": ["entry_id", "test_index", "status"],
            "additionalProperties": False,
        },
    },
    "execution_complete_entry": {
        "description": (
            "Close out an entry once its tests are recorded. Refused unless a passing "
            "sanity check exists, or you supply override_reason explaining why one was "
            "not possible — that override is audit-visible and a human will read it."
        ),
        "workflows": _EXEC,
        "method": "POST",
        "path": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/complete",
        "path_params": ["plan_id", "entry_id"],
        "auto_params": {"plan_id": "plan_id"},
        "body_params": [
            "findings_summary", "overall_status", "override_reason", "no_tests_run_reason",
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
                "override_reason": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Why no passing sanity check exists (target down, scope changed…).",
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
        "workflows": _EXEC,
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
            "the work finished — that distinction is what a reviewer needs."
        ),
        "workflows": _EXEC,
        "method": "POST",
        "path": "/api/v1/agent/execution-sessions/{session_id}/complete",
        "path_params": ["session_id"],
        "auto_params": {"session_id": "workflow_session_id"},
        "body_params": ["notes", "overall_status"],
        "capability": "write:execution",
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
    # Stage 1 — reconnaissance.  Populates host data from scanner output run on
    # the operator's machine.  The bulk paths (upload, target-file downloads)
    # stay curl — see the module docstring.
    # -----------------------------------------------------------------------
    "recon_record_environment": {
        "description": (
            "Record the operator's environment on this recon session. REQUIRED FIRST "
            "STEP — which scanners exist on this host decides what you can actually "
            "run. session_id is resolved from your key."
        ),
        "workflows": _RECON,
        "method": "POST",
        "path": "/api/v1/agent/recon/sessions/{session_id}/environment",
        "path_params": ["session_id"],
        "auto_params": {"session_id": "workflow_session_id"},
        "body_params": _PROBE_BODY_PARAMS,
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — resolved from your API key.",
                },
                **_PROBE_PROPERTIES,
            },
            "required": ["os_family"],
            "additionalProperties": False,
        },
    },
    "recon_get_context": {
        "description": (
            "The scope to work: its CIDRs, what is already known about it, the tool "
            "catalogue you may use, and a recommended scan sequence. Call this first. "
            "For big scopes the CIDR list is capped — recon_list_subnets is "
            "authoritative, and the target files are downloads, not tools."
        ),
        "workflows": _RECON,
        "method": "GET",
        "path": "/api/v1/agent/recon/context",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "recon_list_subnets": {
        "description": (
            "The authoritative, paginated subnet list for this recon scope — use it "
            "when recon_get_context reports the CIDRs were truncated."
        ),
        "workflows": _RECON,
        "method": "GET",
        "path": "/api/v1/agent/recon/subnets",
        "query_params": ["limit", "offset"],
        "defaults": {"limit": 100},
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    "recon_get_job": {
        "description": (
            "Poll an upload's parse status. Upload itself is a file POST you run with "
            "curl (see the server instructions); this is how you find out whether it "
            "parsed, and what it produced."
        ),
        "workflows": _RECON,
        "method": "GET",
        "path": "/api/v1/agent/recon/jobs/{job_id}",
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
    "recon_get_summary": {
        "description": (
            "What this recon session has discovered so far: hosts, ports, per-host "
            "detail (capped) and derived web targets. Use it to decide the next scan "
            "and to report progress. For the complete lists, use the downloads it "
            "points at rather than paging through here."
        ),
        "workflows": _RECON,
        "method": "GET",
        "path": "/api/v1/agent/recon/summary",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "recon_complete": {
        "description": (
            "Close the recon session with a closing note — coverage achieved, ranges "
            "you could not reach, anything the planning stage should know. This is the "
            "handoff to stage 2, so write it for the next reader."
        ),
        "workflows": _RECON,
        "method": "POST",
        "path": "/api/v1/agent/recon/complete",
        "body_params": ["notes"],
        "input_schema": {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "additionalProperties": False,
        },
    },
}


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
    # A tool is read-only iff it doesn't mutate — method, not capability.  The
    # environment probe is a POST with no capability gate (it writes session
    # metadata, not project data), so keying off `capability` alone would
    # advertise a mutation as safe to auto-approve.
    is_write = spec["method"] != "GET"
    # The spec defines destructiveHint:false as "additive updates only", so the
    # flag lives on the entry rather than being inferred from the name: adding a
    # note or a test result appends, while setting follow state, patching a host
    # or re-probing the environment REPLACES a stored value.  The operative
    # question for idempotency is whether a retry is safe — re-sending a
    # replacement converges, a second append is a second row.
    additive = bool(spec.get("additive"))
    ann: Dict[str, Any] = {
        "readOnlyHint": not is_write,
        "destructiveHint": is_write and not additive,
        "idempotentHint": not additive,
        "openWorldHint": False,
    }
    if spec.get("capability"):
        ann["title"] = f"{name} (requires {spec['capability']})"
    return ann


def tool_list_payload(
    *, workflow: Optional[str] = None, granted: Optional[set] = None
) -> List[Dict[str, Any]]:
    """The ``tools`` array for a ``tools/list`` response.

    ``workflow`` is the caller's key workflow; tools belonging to the other
    workflows are omitted.  ``granted`` is the session's capability set; writes
    it cannot perform are omitted.  ``None`` for either means "unknown" — list
    everything, which is the documentation view an unauthenticated client gets.

    Both filters are presentational (see the module docstring): the endpoint
    behind each tool re-decides on every call.
    """
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": advertised_schema(spec),
            "annotations": annotations(name, spec),
        }
        for name, spec in TOOLS.items()
        if (workflow is None or workflow in spec["workflows"])
        and (
            granted is None
            or not spec.get("capability")
            or spec["capability"] in granted
        )
    ]
