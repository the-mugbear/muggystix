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

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.services.agent_prompt_service import resolve_base_url

logger = logging.getLogger(__name__)

router = APIRouter()

# The newest protocol revision we implement.  We echo the client's requested
# version when it is a string (maximises cross-client compatibility); otherwise
# we advertise this one.
_PREFERRED_PROTOCOL_VERSION = "2025-06-18"

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
            "Project overview for this assist session: counts, top findings, and "
            "orientation. Call this first to understand the engagement."
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
        "description": "Full detail for one host: ports, services, findings summary, notes, review state.",
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


def _tool_list_payload() -> List[Dict[str, Any]]:
    """The ``tools`` array for a ``tools/list`` response."""
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["input_schema"],
        }
        for name, spec in _TOOLS.items()
    ]


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
) -> httpx.Response:
    """Call this app's own endpoint in-process via ASGI (no socket, no nginx).

    Runs the full middleware stack, so the agent-API audit log records the call
    and ``require_assist_scope`` enforces auth exactly as for an external curl.
    """
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://mcp.loopback"
    ) as client:
        return await client.request(
            method, path, params=params or None, json=json_body, headers=headers
        )


async def _dispatch_tool(
    app, name: str, arguments: Dict[str, Any], api_key: Optional[str]
) -> Dict[str, Any]:
    """Run one ``tools/call`` -> loopback endpoint, return an MCP tool result."""
    spec = _TOOLS.get(name)
    if spec is None:
        return _tool_text_result(f"Unknown tool: {name}", is_error=True)

    arguments = arguments or {}

    # Path params (e.g. host_id) -> substitute into the path template.
    path = spec["path"]
    for pname in spec["path_params"]:
        if pname not in arguments or arguments[pname] is None:
            return _tool_text_result(
                f"Missing required argument: {pname}", is_error=True
            )
        path = path.replace("{" + pname + "}", str(arguments[pname]))

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
    return _tool_text_result(text)


# ---------------------------------------------------------------------------
# JSON-RPC method handling
# ---------------------------------------------------------------------------

async def _handle_message(
    app, message: Dict[str, Any], api_key: Optional[str], base_url: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Process one JSON-RPC message.

    Returns ``(response, new_session_id)``.  ``response`` is None for
    notifications (no reply).  ``new_session_id`` is set only by ``initialize``.
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(message.get("id") if isinstance(message, dict) else None,
                          -32600, "Invalid Request"), None

    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message

    # Notifications (initialized, cancelled, progress, ...) get no response.
    if is_notification:
        return None, None

    if method == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        protocol_version = (
            requested if isinstance(requested, str) and requested else _PREFERRED_PROTOCOL_VERSION
        )
        from app.core.config import settings

        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": _SERVER_NAME, "version": settings.APP_VERSION},
            "instructions": _server_instructions(base_url),
        }
        return _rpc_result(msg_id, result), uuid.uuid4().hex

    if method == "ping":
        return _rpc_result(msg_id, {}), None

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": _tool_list_payload()}), None

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _rpc_error(msg_id, -32602, "Invalid params: 'name' is required"), None
        tool_result = await _dispatch_tool(app, name, arguments, api_key)
        return _rpc_result(msg_id, tool_result), None

    return _rpc_error(msg_id, -32601, f"Method not found: {method}"), None


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

    api_key = request.headers.get("X-API-Key")
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
        session_id: Optional[str] = None
        for msg in payload:
            resp, sid = await _handle_message(request.app, msg, api_key, base_url)
            if sid:
                session_id = sid
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(status_code=202)
        headers = {"Mcp-Session-Id": session_id} if session_id else None
        return JSONResponse(responses, headers=headers)

    resp, session_id = await _handle_message(request.app, payload, api_key, base_url)
    if resp is None:
        # Notification-only POST -> 202 Accepted, no body.
        return Response(status_code=202)
    headers = {"Mcp-Session-Id": session_id} if session_id else None
    return JSONResponse(resp, headers=headers)


@router.get("")
async def mcp_get() -> Response:
    """The optional server->client SSE stream is not supported (all tools are
    request/response). Clients handle 405 gracefully and fall back to POST."""
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete("")
async def mcp_delete() -> Response:
    """Session termination. The server is effectively stateless, so accept and
    no-op."""
    return Response(status_code=204)
