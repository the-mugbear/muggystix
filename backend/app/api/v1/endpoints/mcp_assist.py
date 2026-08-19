"""In-process MCP (Model Context Protocol) server for the AI-Assist surface.

Why this is hand-rolled instead of using the ``mcp`` SDK
--------------------------------------------------------
Originally a hard constraint: the official ``mcp`` package requires
``starlette>=1.0`` and the backend was pinned to ``starlette<0.49``, so
installing it would have broken the running app.  **That constraint is gone as
of v2.267.0** (FastAPI 0.141 / starlette 1.6) — the SDK is now installable.  It
stays unadopted on purpose: what we need is the small, stable *tools-only*
subset of the **Streamable HTTP** transport (JSON-RPC 2.0 over a single POST
endpoint, plain ``application/json`` responses — no SSE) against a frozen wire
format, and taking the SDK would add a dependency (with its own transitive
starlette/anyio pins) plus a second ASGI app to mount, for no capability this
doesn't already have.  Revisit that trade if we ever need SSE streaming,
sampling, or the resources/prompts surfaces.  Implemented directly as a FastAPI
route, which keeps the whole thing
**in-process**, which is the important property: every tool call loops straight
back into the app's own ``/api/v1/agent/assist/*`` (and ``/agent/hosts/*``)
endpoints via an ASGI transport, so authentication (``require_assist_scope`` +
capability gate + row-scope), the agent-API audit log middleware, and the recon
streaming caps all run **unchanged**.  The MCP layer makes no security decision
of its own — it forwards the caller's ``X-API-Key`` and lets the real endpoint
decide.

What it exposes
---------------
A tool per interactive assist endpoint (reads + the three capability-gated
writes).  The bulk ``report-context.ndjson`` stream is deliberately *not* a tool
— it is meant to be downloaded to a file, not materialised into the model's
context — so we point at it in the server ``instructions`` instead.

Auth model
----------
``initialize`` / ``tools/list`` / ``ping`` need no key (static, leak nothing).
``tools/call`` reads ``X-API-Key`` from the POST request and forwards it to the
loopback endpoint; a missing/invalid/wrong-scope key surfaces as the real
endpoint's 401/403 wrapped in an ``isError`` tool result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.services.agent_prompt_service import resolve_base_url

logger = logging.getLogger(__name__)

router = APIRouter()

# The protocol revisions we actually implement.  The tools-only Streamable-HTTP
# subset is identical across these two; 2024-11-05 is deliberately absent because
# it specified the older HTTP+SSE transport, which this server does not serve.
#
# Pre-v2.271.0 the server echoed back whatever version the client asked for, so
# `initialize` with "2099-99-99" was reported as successfully negotiated — the
# client then believes it is talking a revision nobody implements.  The spec
# requires responding with a version the server supports.
_PREFERRED_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2025-06-18", "2025-03-26"})

_SERVER_NAME = "bluestick-assist"

# --- pre-auth request ceilings ---------------------------------------------
# This endpoint is UNAUTHENTICATED at the FastAPI layer (initialize / tools/list
# / ping need no key), so everything that runs before a key is checked has to be
# bounded:
#
#   * The body is read through a **capped stream**, never ``request.json()``.
#     nginx allows 2 GB on /api/ (large scan uploads need it), so a pre-auth
#     ``await request.json()`` would let an anonymous caller materialise 2 GB
#     per worker — the same shape removed from the /agent/* audit middleware in
#     v2.240.2.  Capping bytes *actually read* (not the declared
#     Content-Length) means a lying header or a chunked body can't get past it.
#   * A JSON-RPC batch is **length-capped**.  Every element costs a full
#     in-process ASGI loopback (~3 ms measured), so an uncapped array converts
#     one cheap anonymous request into unbounded server work.
#
# Both ceilings sit far above any real client: the largest legitimate message is
# a tools/call with a note body, and no MCP client batches at all today.
_MAX_REQUEST_BYTES = 1024 * 1024  # 1 MiB
_MAX_BATCH_MESSAGES = 50

# Guidance handed to the agent at initialize time (clients surface this).  The
# bulk report stream lives here, not as a tool, on purpose — see module docstring.
def _server_instructions(base_url: str) -> str:
    """Instructions text with the report-stream URL resolved for this caller.

    ``base_url`` must be a real, externally-reachable ``…/api/v1`` — the agent
    is expected to paste the URL into a curl.  Pre-v2.268.1 this was a module
    constant carrying a literal ``{base}`` that nothing ever substituted, so
    every client was handed an unusable URL.
    """
    return (
        "BlueStick AI-Assist. Use these tools to read the project's hosts, findings, "
        "scopes and scans, and (when your key was granted write access) to add notes, "
        "set a host's review status, or correct a host's hostname/OS. All calls are "
        "audited and scoped to the assist session your key belongs to.\n\n"
        "To write a report over MANY hosts, do NOT page the tools — download the full "
        "per-host dossier stream to a file instead: "
        f"GET {base_url}/agent/assist/report-context.ndjson with your X-API-Key "
        "header (one JSON object per host, uncapped), then read the file locally. "
        "The tools are for targeted lookups and writes."
    )

# ---------------------------------------------------------------------------
# Tool registry — declarative map from MCP tool -> loopback HTTP call.
#
# Each entry:
#   description  : shown to the model in tools/list
#   method       : HTTP verb of the underlying endpoint
#   path         : loopback path; ``{name}`` placeholders filled from path_params
#   path_params  : argument names substituted into the path
#   query_params : argument names sent as querystring (GET filters/pagination)
#   body_params  : argument names sent in the JSON body (writes)
#   input_schema : JSON Schema advertised to the client
#   capability   : the write capability the underlying endpoint requires (docs only)
# ---------------------------------------------------------------------------

_HOST_ID_PROP = {
    "host_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Numeric host id (from assist_list_hosts).",
    }
}

_TOOLS: Dict[str, Dict[str, Any]] = {
    "assist_get_context": {
        "description": (
            "Project orientation for this assist session: host/port/scope/scan "
            "totals, the scope list (capped at 50), and recent scans. It carries "
            "NO findings — use assist_list_hosts to locate hosts and "
            "assist_get_host_findings for the findings on one. Call this first."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/context",
        "path_params": [],
        "query_params": [],
        "body_params": [],
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_hosts": {
        "description": (
            "List/filter hosts in the project. Prefer the `q` boolean DSL (same "
            "vocabulary as the Hosts page: port:, os:, service:, subnet:, tag:, "
            "cve:, vuln:, tech:, has:, follow:, assigned:me — combine with AND/OR/"
            "NOT and parentheses). Paginate with limit/offset. Returns host briefs."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts",
        "path_params": [],
        "query_params": [
            "q", "search", "state", "ports", "services", "subnets",
            "has_critical_vulns", "has_high_vulns", "limit", "offset",
        ],
        "body_params": [],
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
    "assist_get_host": {
        "description": (
            "Full detail for one host: identity, OS, per-port service detail, "
            "severity counts, and your review status. Notes and individual "
            "findings are separate — use assist_get_host_findings for findings."
        ),
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}",
        "path_params": ["host_id"],
        "query_params": [],
        "body_params": [],
        "input_schema": {
            "type": "object",
            "properties": dict(_HOST_ID_PROP),
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
        "method": "GET",
        "path": "/api/v1/agent/assist/hosts/{host_id}/findings",
        "path_params": ["host_id"],
        "query_params": ["severity", "limit", "offset"],
        "body_params": [],
        "defaults": {"limit": 50},
        "input_schema": {
            "type": "object",
            "properties": {
                **_HOST_ID_PROP,
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
    "assist_list_scopes": {
        "description": "List the network scopes (CIDR boundaries) defined for this project.",
        "method": "GET",
        "path": "/api/v1/agent/assist/scopes",
        "path_params": [],
        "query_params": [],
        "body_params": [],
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_list_scans": {
        "description": "List the scans ingested into this project (most recent first).",
        "method": "GET",
        "path": "/api/v1/agent/assist/scans",
        "path_params": [],
        "query_params": ["limit"],
        "body_params": [],
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
        "method": "GET",
        "path": "/api/v1/agent/assist/session",
        "path_params": [],
        "query_params": [],
        "body_params": [],
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "assist_record_environment": {
        "description": (
            "Record the operator's environment (OS family, shell) on this assist "
            "session. REQUIRED FIRST STEP — the guide mandates it before other work, "
            "so BlueStick's guidance matches the machine you're actually on. The "
            "session is resolved from your key; you do not need to pass session_id."
        ),
        "method": "POST",
        "path": "/api/v1/agent/assist/sessions/{session_id}/environment",
        "path_params": ["session_id"],
        # Filled from the key when omitted — see _resolve_session_id.
        "session_param": "session_id",
        "query_params": [],
        # Field names mirror EnvironmentSummary exactly — the schema advertises
        # additionalProperties:false and we now reject unknown arguments, so a
        # name that doesn't exist server-side would be a hard error, not a
        # silently dropped field.
        "body_params": [
            "os_family", "os_release", "arch", "shell",
            "powershell_version", "powershell_execution_policy",
            "python", "python_version", "wsl_available", "notes",
            # The assist prompt tells agents to send these for audit symmetry.
            # They were missing here, and since v2.271.0 rejects unknown
            # arguments, an agent following its own instructions got -32602.
            "agent_model", "agent_tool", "agent_prompt_version",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Usually omit — resolved from your API key.",
                },
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
            },
            # Assist only needs os_family + shell (see AGENTS.md); the rest are
            # accepted so a probe built for recon/execution posts unchanged.
            "required": ["os_family"],
            "additionalProperties": False,
        },
    },
    # --- writes (capability-gated by the underlying endpoint) ---
    "assist_add_note": {
        "description": (
            "Add a note to a host. Requires the write:notes capability on your key. "
            "Notes are stamped agent-authored and appear in the operator's UI and in "
            "client-facing reports — record observations tied to host/port/finding "
            "evidence, mark inferences as inferences."
        ),
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/notes",
        "path_params": ["host_id"],
        "query_params": [],
        "body_params": ["body", "status"],
        "capability": "write:notes",
        "input_schema": {
            "type": "object",
            "properties": {
                **_HOST_ID_PROP,
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
        "method": "POST",
        "path": "/api/v1/agent/hosts/{host_id}/follow",
        "path_params": ["host_id"],
        "query_params": [],
        "body_params": ["status"],
        "capability": "write:follow",
        "input_schema": {
            "type": "object",
            "properties": {
                **_HOST_ID_PROP,
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
        "method": "PATCH",
        "path": "/api/v1/agent/hosts/{host_id}",
        "path_params": ["host_id"],
        "query_params": [],
        "body_params": ["hostname", "os_name"],
        "capability": "write:host",
        "input_schema": {
            "type": "object",
            "properties": {
                **_HOST_ID_PROP,
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
}


def tool_catalog(endpoint_url: str) -> Dict[str, Any]:
    """The MCP surface, described for the in-app reference page.

    Derived from the same ``_TOOLS`` registry the server dispatches from, so
    the documentation cannot drift from what the server actually exposes — add
    a tool and it appears on the page with no second edit.  Everything here is
    already readable without a key (``initialize`` / ``tools/list`` are
    unauthenticated), so serving it from the public references router leaks
    nothing new.
    """
    return {
        "server_name": _SERVER_NAME,
        "protocol_version": _PREFERRED_PROTOCOL_VERSION,
        "endpoint": endpoint_url,
        "max_request_bytes": _MAX_REQUEST_BYTES,
        "max_batch_messages": _MAX_BATCH_MESSAGES,
        "tools": [
            {
                "name": name,
                "description": spec["description"],
                # Mutation is decided by HTTP method, matching readOnlyHint —
                # the environment probe is a capability-free POST, and calling
                # that a "read" on the reference page would be a lie.
                "kind": "read" if spec["method"] == "GET" else "write",
                "capability": spec.get("capability"),
                "method": spec["method"],
                "path": spec["path"],
                "input_schema": _advertised_schema(spec),
            }
            for name, spec in _TOOLS.items()
        ],
    }


def _advertised_schema(spec: Dict[str, Any]) -> Dict[str, Any]:
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


def _annotations(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tool annotations — hints a client uses to pick approval defaults.

    Without these a host has no way to tell a read from a mutation except by
    reading the description, so it must prompt for everything (v2.271.0).
    ``readOnlyHint`` is the one that earns the feature: it's what lets a client
    offer "always allow" on the reads.
    """
    # A tool is read-only iff it doesn't mutate — method, not capability.
    # The environment probe is a POST with no capability gate (it writes session
    # metadata, not project data), so keying off `capability` alone would
    # advertise a mutation as safe to auto-approve.
    is_write = spec["method"] != "GET"
    ann: Dict[str, Any] = {
        "readOnlyHint": not is_write,
        # The spec defines destructiveHint:false as "additive updates only".
        # Only assist_add_note is additive — setting follow, patching a host,
        # and re-probing the environment each REPLACE a stored value, so
        # claiming otherwise understated the risk to a client deciding whether
        # to auto-approve (v2.272.0).  Nothing here deletes project data.
        "destructiveHint": is_write and name != "assist_add_note",
        # The operative question is whether a retry is safe.  Re-sending the
        # same follow status, host patch, or probe converges on the same state;
        # a second add_note is a second note.
        "idempotentHint": name != "assist_add_note",
        "openWorldHint": False,
    }
    if spec.get("capability"):
        ann["title"] = f"{name} (requires {spec['capability']})"
    return ann


def _tool_list_payload(granted: Optional[set] = None) -> List[Dict[str, Any]]:
    """The ``tools`` array for a ``tools/list`` response.

    ``granted`` is the session's capability set when the caller supplied a key;
    writes it cannot perform are omitted.  ``None`` means "no key" — list
    everything, which is the documentation view.
    """
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": _advertised_schema(spec),
            "annotations": _annotations(name, spec),
        }
        for name, spec in _TOOLS.items()
        if granted is None
        or not spec.get("capability")
        or spec["capability"] in granted
    ]


# Capability lookups are server-initiated plumbing, not agent activity, but they
# ride the same audited endpoint — so a client that re-lists tools each turn fills
# the operator's activity view with /assist/session rows nobody asked for (3 of 7
# rows in the v2.273.0 end-to-end run).  A short in-process cache collapses those
# to one per key.  Keyed by a hash so raw key material never sits in the cache,
# and short-lived so an ended session stops being listed as writable quickly —
# staleness here is cosmetic anyway, since every actual call re-checks auth.
_CAPABILITY_TTL_SECONDS = 60
_capability_cache: Dict[str, Tuple[float, Optional[frozenset]]] = {}


def _cache_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _cached_capabilities(api_key: str) -> Optional[Tuple[float, Optional[frozenset]]]:
    entry = _capability_cache.get(_cache_key(api_key))
    if entry and (time.monotonic() - entry[0]) < _CAPABILITY_TTL_SECONDS:
        return entry
    return None


async def _granted_capabilities(
    app,
    api_key: Optional[str],
    *,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> Optional[set]:
    """The capabilities this key holds, or None when there's no usable key.

    Returning None on any failure means an unauthenticated or broken-key
    tools/list still returns the full catalog — discovery degrades to the
    documentation view rather than to an empty tool list.
    """
    if not api_key:
        return None
    cached = _cached_capabilities(api_key)
    if cached is not None:
        return cached[1]
    try:
        resp = await _loopback(
            app,
            method="GET",
            path="/api/v1/agent/assist/session",
            api_key=api_key,
            caller=caller,
            user_agent=user_agent,
        )
        granted = (
            frozenset(resp.json().get("capabilities") or [])
            if resp.status_code == 200
            else None
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("MCP could not read capabilities for tools/list")
        return None
    _capability_cache[_cache_key(api_key)] = (time.monotonic(), granted)
    return granted


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _rpc_result(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _tool_text_result(text: str, *, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# ---------------------------------------------------------------------------
# Loopback into the app's own assist endpoints
# ---------------------------------------------------------------------------

async def _loopback(
    app,
    *,
    method: str,
    path: str,
    api_key: Optional[str],
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> httpx.Response:
    """Call this app's own endpoint in-process via ASGI (no socket, no nginx).

    Runs the full middleware stack, so the agent-API audit log records the call
    and ``require_assist_scope`` enforces auth exactly as for an external curl.

    ``caller`` / ``user_agent`` carry the ORIGINAL client's identity into the
    loopback (v2.271.0).  Without them every MCP-driven call was audited as
    ``127.0.0.1`` / ``python-httpx`` — the loopback's own identity — which made
    the agent-activity log useless for the one question it exists to answer:
    who did this.  These are taken from the real inbound request server-side,
    never from a caller-supplied header, so they can't be spoofed.
    """
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if user_agent:
        headers["User-Agent"] = user_agent
    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=False,
        **({"client": caller} if caller else {}),
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://mcp.loopback"
    ) as client:
        return await client.request(
            method, path, params=params or None, json=json_body, headers=headers
        )


async def _dispatch_tool(
    app,
    name: str,
    arguments: Dict[str, Any],
    api_key: Optional[str],
    *,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one ``tools/call`` -> loopback endpoint, return an MCP tool result.

    Unknown tools and malformed arguments are rejected earlier as protocol
    errors; by the time we get here the call is well-formed and anything that
    goes wrong is a tool-execution failure (``isError``).
    """
    spec = _TOOLS[name]
    arguments = dict(arguments or {})

    # Page-size defaults (v2.271.0).  The underlying endpoints default to sizes
    # tuned for a file download (500 hosts, 200 findings); dropped whole into a
    # model's context that is a lot of tokens for a question that usually wants
    # the first handful.  Inject a smaller MCP-side default, still overridable.
    for arg, default in spec.get("defaults", {}).items():
        arguments.setdefault(arg, default)

    # Path params (e.g. host_id) -> substitute into the path template.
    path = spec["path"]
    for pname in spec["path_params"]:
        value = arguments.get(pname)
        if value is None and spec.get("session_param") == pname:
            # Resolve the session from the key instead of making the model carry
            # it: the key is already bound to exactly one assist session.
            value = await _resolve_session_id(
                app, api_key, caller=caller, user_agent=user_agent
            )
            if value is None:
                return _tool_text_result(
                    "Could not resolve this key's assist session — call "
                    "assist_session_info and pass its `id` explicitly.",
                    is_error=True,
                )
        if value is None:
            return _tool_text_result(
                f"Missing required argument: {pname}", is_error=True
            )
        path = path.replace("{" + pname + "}", str(value))

    # Query params (skip omitted).
    params = {
        k: arguments[k]
        for k in spec["query_params"]
        if k in arguments and arguments[k] is not None
    }
    # Body params (skip omitted).
    body = {
        k: arguments[k]
        for k in spec["body_params"]
        if k in arguments and arguments[k] is not None
    }
    json_body = body if (spec["method"] != "GET" ) else None

    if spec["method"] != "GET" and not api_key:
        return _tool_text_result(
            "This tool writes and needs an X-API-Key with the required capability; "
            "none was provided on the MCP connection.",
            is_error=True,
        )

    try:
        resp = await _loopback(
            app,
            method=spec["method"],
            path=path,
            api_key=api_key,
            params=params,
            json_body=json_body,
            caller=caller,
            user_agent=user_agent,
        )
    except Exception:  # pragma: no cover - defensive; loopback should not raise
        logger.exception("MCP loopback failed for tool %s", name)
        return _tool_text_result(
            f"Internal error dispatching tool {name}.", is_error=True
        )

    text = resp.text
    if resp.status_code >= 400:
        # Surface the real endpoint's error (401/403/404/400) as a tool error so
        # the agent sees exactly why — capability missing, wrong scope, etc.
        return _tool_text_result(
            f"HTTP {resp.status_code} from {spec['method']} {path}: {text}",
            is_error=True,
        )
    # 204 (follow) has no body — report success explicitly.
    if resp.status_code == 204 or not text:
        return _tool_text_result("OK")

    # Every endpoint here answers JSON, so hand it back as `structuredContent`
    # as well as text (v2.271.0).  Clients that understand structured results
    # get parsed data instead of re-parsing a string; the text block stays for
    # those that don't.  A JSON array is wrapped — structuredContent must be an
    # object per the spec.
    result = _tool_text_result(text)
    try:
        parsed = resp.json()
    except ValueError:
        return result
    result["structuredContent"] = parsed if isinstance(parsed, dict) else {"items": parsed}
    return result


async def _resolve_session_id(
    app,
    api_key: Optional[str],
    *,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> Optional[int]:
    """This key's assist-session id, via the session endpoint the key already
    has access to.  Returns None when the key is missing/invalid — the caller
    turns that into a tool error rather than guessing an id."""
    if not api_key:
        return None
    try:
        resp = await _loopback(
            app,
            method="GET",
            path="/api/v1/agent/assist/session",
            api_key=api_key,
            caller=caller,
            user_agent=user_agent,
        )
        if resp.status_code != 200:
            return None
        value = resp.json().get("id")
        return int(value) if value is not None else None
    except Exception:  # pragma: no cover - defensive
        logger.exception("MCP could not resolve the assist session for a key")
        return None


# ---------------------------------------------------------------------------
# Request validation
#
# Malformed params used to reach the handlers unchecked: `params: "nope"` made
# `params.get(...)` raise AttributeError, which surfaced as an application HTTP
# 500.  A malformed request is a protocol error (-32602), never a 500.
# ---------------------------------------------------------------------------

class _InvalidParams(Exception):
    """Raised for anything that should answer JSON-RPC -32602."""


def _require_params(message: Dict[str, Any]) -> Dict[str, Any]:
    """The message's ``params`` object, or ``{}`` when absent."""
    params = message.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise _InvalidParams("Invalid params: expected an object")
    return params


def _validate_arguments(name: str, spec: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    """Check arguments against the schema we advertised for this tool.

    Deliberately shallow — required-present and no-unknown-properties, the two
    the schemas actually promise (`additionalProperties: false`).  Type coercion
    stays the underlying endpoint's job: it already validates with pydantic and
    returns a far better message than a hand-rolled checker would.
    """
    schema = spec["input_schema"]
    allowed = set(schema.get("properties", {}))
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise _InvalidParams(
            f"Invalid params for {name}: unknown argument(s) {', '.join(unknown)}. "
            f"Accepted: {', '.join(sorted(allowed)) or 'none'}"
        )
    missing = sorted(a for a in schema.get("required", []) if arguments.get(a) is None)
    if missing:
        raise _InvalidParams(
            f"Invalid params for {name}: missing required argument(s) {', '.join(missing)}"
        )


# ---------------------------------------------------------------------------
# JSON-RPC method handling
# ---------------------------------------------------------------------------

async def _handle_message(
    app,
    message: Dict[str, Any],
    api_key: Optional[str],
    base_url: str,
    *,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC message.

    Returns the response, or None for a notification (no reply).
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(message.get("id") if isinstance(message, dict) else None,
                          -32600, "Invalid Request")

    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message

    # Notifications (initialized, cancelled, progress, ...) get no response.
    if is_notification:
        return None

    # Shape-check params for every method.  Previously only initialize and
    # tools/call looked, so `tools/list` with `params: "invalid"` sailed through
    # (v2.272.0).
    try:
        _require_params(message)
    except _InvalidParams as exc:
        return _rpc_error(msg_id, -32602, str(exc))

    if method == "initialize":
        try:
            params = _require_params(message)
        except _InvalidParams as exc:
            return _rpc_error(msg_id, -32602, str(exc))
        requested = params.get("protocolVersion")
        # Negotiate: echo the client's version only when we implement it,
        # otherwise answer with ours and let the client decide whether to
        # continue (the spec's prescribed behaviour).
        protocol_version = (
            requested
            if isinstance(requested, str) and requested in _SUPPORTED_PROTOCOL_VERSIONS
            else _PREFERRED_PROTOCOL_VERSION
        )
        from app.core.config import settings

        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": _SERVER_NAME, "version": settings.APP_VERSION},
            "instructions": _server_instructions(base_url),
        }
        return _rpc_result(msg_id, result)

    if method == "ping":
        return _rpc_result(msg_id, {})

    if method == "tools/list":
        # With a key, hide the writes this session cannot perform (v2.271.0).
        # Advertising all three writes to a read-only session invites the model
        # to try them and read a 403 as a bug in itself.  Without a key we list
        # everything — that's the documentation view.
        granted = await _granted_capabilities(
            app, api_key, caller=caller, user_agent=user_agent
        )
        return _rpc_result(msg_id, {"tools": _tool_list_payload(granted)})

    if method == "tools/call":
        try:
            params = _require_params(message)
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise _InvalidParams("Invalid params: 'name' must be a tool name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise _InvalidParams("Invalid params: 'arguments' must be an object")
            # An unknown tool, or arguments that don't fit the advertised schema,
            # are PROTOCOL errors — the request was never valid — not tool
            # results.  isError is for a tool that ran and failed.
            spec = _TOOLS.get(name)
            if spec is None:
                raise _InvalidParams(f"Unknown tool: {name}")
            _validate_arguments(name, spec, arguments)
        except _InvalidParams as exc:
            return _rpc_error(msg_id, -32602, str(exc))
        tool_result = await _dispatch_tool(
            app, name, arguments, api_key, caller=caller, user_agent=user_agent
        )
        return _rpc_result(msg_id, tool_result)

    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# HTTP endpoints (Streamable HTTP transport, JSON-only)
# ---------------------------------------------------------------------------

async def _read_capped_body(request: Request) -> Optional[bytes]:
    """The request body, or ``None`` if it exceeds ``_MAX_REQUEST_BYTES``.

    Reads the ASGI stream chunk by chunk and bails the moment the running total
    crosses the cap, so peak memory is bounded by the cap regardless of what the
    headers claim.  See the ceiling note at the top of this module.
    """
    total = 0
    chunks: List[bytes] = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_REQUEST_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _api_key_from(request: Request) -> Optional[str]:
    """The agent key, from either header the agent API accepts.

    ``X-API-Key`` is what most clients send. Codex reads its credential from an
    environment variable and sends it as ``Authorization: Bearer`` — the agent
    auth layer has always accepted that form, but the MCP layer only looked at
    ``X-API-Key``, so a Codex client authenticated as nobody (v2.271.0).
    Supporting bearer is also what lets a client keep the key out of its config
    file entirely.
    """
    direct = request.headers.get("X-API-Key")
    if direct:
        return direct
    auth = request.headers.get("Authorization") or ""
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def _origin_rejected(request: Request) -> bool:
    """True when a browser-supplied ``Origin`` is not one we already trust.

    The MCP transport spec asks servers to validate ``Origin`` as a
    DNS-rebinding defence.  Real MCP clients are not browsers and send no
    Origin at all, so this only fires on a web page trying to drive the
    endpoint out of a user's browser — never legitimate here.  We reuse the
    CORS allowlist rather than inventing a second notion of "trusted origin".
    """
    origin = request.headers.get("origin")
    if not origin:
        return False
    from app.core.config import settings

    allowed = {
        o.rstrip("/")
        for o in (settings.CORS_ORIGINS or [])
        if o and "*" not in o
    }
    return origin.rstrip("/") not in allowed


@router.post("")
# Same handler on the trailing-slash path: FastAPI would otherwise 307 it, and a
# client that drops the body on redirect fails the handshake with no useful
# error.  Hidden from the schema so /docs shows one canonical path.
@router.post("/", include_in_schema=False)
async def mcp_post(request: Request) -> Response:
    """Single Streamable-HTTP endpoint. Accepts one JSON-RPC message (or a
    legacy array), replies with ``application/json``. No SSE stream is offered —
    every tool here is request/response, so the spec permits a direct JSON reply.
    """
    if _origin_rejected(request):
        return JSONResponse(
            _rpc_error(None, -32600, "Origin not allowed"), status_code=403
        )

    # The spec requires rejecting a version we don't implement rather than
    # carrying on and hoping the shapes line up (v2.271.0).
    negotiated = request.headers.get("MCP-Protocol-Version")
    if negotiated and negotiated not in _SUPPORTED_PROTOCOL_VERSIONS:
        return JSONResponse(
            _rpc_error(
                None,
                -32600,
                f"Unsupported MCP-Protocol-Version: {negotiated}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_PROTOCOL_VERSIONS))}",
            ),
            status_code=400,
        )

    api_key = _api_key_from(request)
    # The real caller, captured server-side and carried into every loopback so
    # the audit log records who actually called rather than the loopback itself.
    caller = (request.client.host, request.client.port or 0) if request.client else None
    user_agent = request.headers.get("user-agent")
    # Same resolution the assist-start dialog uses for its curl recipe, so the
    # URL we hand the agent is the one an operator would reach the app on (a
    # container hostname would be useless to a terminal-side agent).
    base_url = resolve_base_url(request)
    raw = await _read_capped_body(request)
    if raw is None:
        return JSONResponse(
            _rpc_error(
                None,
                -32600,
                f"Request body exceeds the {_MAX_REQUEST_BYTES}-byte limit.",
            ),
            status_code=413,
        )
    try:
        payload = json.loads(raw)
    except Exception:
        return JSONResponse(
            _rpc_error(None, -32700, "Parse error"), status_code=200
        )

    # A JSON-RPC batch (array) was allowed pre-2025-06-18; handle it for older
    # clients. A single object is the modern shape.
    if isinstance(payload, list):
        # JSON-RPC batching was REMOVED in the 2025-06-18 revision.  A client
        # that declared that version and then batches is out of spec, and
        # accepting it lets a client believe batching is available on a protocol
        # that dropped it.  Older revisions (2025-03-26) still allow it, so the
        # gate is the version the client declared, not a blanket refusal.
        if negotiated == "2025-06-18":
            return JSONResponse(
                _rpc_error(
                    None,
                    -32600,
                    "JSON-RPC batching was removed in protocol 2025-06-18. "
                    "Send one message per request, or declare 2025-03-26.",
                ),
                status_code=400,
            )
        if not payload:
            return JSONResponse(
                _rpc_error(None, -32600, "Invalid Request: empty batch"),
                status_code=400,
            )
        if len(payload) > _MAX_BATCH_MESSAGES:
            return JSONResponse(
                _rpc_error(
                    None,
                    -32600,
                    f"Batch of {len(payload)} messages exceeds the "
                    f"{_MAX_BATCH_MESSAGES}-message limit.",
                ),
                status_code=413,
            )
        responses: List[Dict[str, Any]] = []
        for msg in payload:
            resp = await _handle_message(
                request.app, msg, api_key, base_url,
                caller=caller, user_agent=user_agent,
            )
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    resp = await _handle_message(
        request.app, payload, api_key, base_url,
        caller=caller, user_agent=user_agent,
    )
    if resp is None:
        # Notification-only POST -> 202 Accepted, no body.
        return Response(status_code=202)
    return JSONResponse(resp)


@router.get("")
async def mcp_get() -> Response:
    """The optional server->client SSE stream is not supported (all tools are
    request/response). Clients handle 405 gracefully and fall back to POST."""
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete("")
async def mcp_delete() -> Response:
    """Session termination.  This server is stateless — it issues no
    ``Mcp-Session-Id`` (v2.271.0: it used to mint one and then ignore it, which
    told clients state existed when none did), so there is nothing to tear
    down.  Accept and no-op for clients that send it unconditionally."""
    return Response(status_code=204)
