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
A tool per interactive endpoint across all four agent workflows — assist,
reconnaissance, plan generation, execution (v2.278.0; it was assist-only
before).  ``tools/list`` shows the caller's own workflow, resolved from their
key: three separate entry points the operator starts deliberately, not one
merged surface.  The bulk, file-shaped endpoints (``report-context.ndjson``,
the recon target lists, ``recon/upload``) are deliberately *not* tools — they
are meant to move between disk and the server, not through a model's context —
so the server ``instructions`` point at them with curl instead.

The registry itself lives in ``mcp_tools.py``; this module is the transport.

Auth model
----------
``initialize`` / ``tools/list`` / ``ping`` need no key (static, leak nothing).
``tools/call`` reads the key from ``X-API-Key`` or ``Authorization: Bearer`` and
forwards it to the loopback endpoint, which decides.  The outcome splits on
*why* a call was refused:

* **No usable credential** (missing or invalid key) → a real **HTTP 401** with a
  bare ``WWW-Authenticate: Bearer`` challenge.  That is a fact about the
  connection, and a client can act on it: prompt for a key, show a connection
  error, stop retrying.
* **A valid key that may not do this** (capability missing, host not assigned)
  → an ``isError`` tool result carrying the endpoint's 403.  That is a fact
  about one call, which the model should read and work around; re-authenticating
  would not change it.

The challenge is deliberately bare — MCP's authorization spec uses 401 plus
``resource_metadata`` to bootstrap OAuth discovery, and this server is not an
OAuth resource server (see /reference/mcp).  Advertising discovery we don't
implement would send capable clients into a dead end.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.api.v1.endpoints.mcp_tools import (
    TOOLS,
    advertised_schema,
    tool_list_payload,
)
from app.services import mcp_telemetry_service as mcp_telemetry
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
        "BlueStick. Your API key belongs to exactly one workflow, and the tools you "
        "can see are that workflow's — call agent_identity if you are unsure which. "
        "Reconnaissance populates host data from scanners run on this machine; plan "
        "generation proposes tests a human then approves; execution works an approved "
        "plan; assist is interactive read (plus writes, when granted) over what is "
        "already there. Each is a separate session the operator starts deliberately: "
        "there is no key that does all four, and no tool here escalates to another "
        "workflow. All calls are audited.\n\n"
        "Run tools on hosts that are in the project's inventory, using tools "
        "BlueStick has approved (see /reference/tools), and write output into the "
        "directory the session is working in. Anything that reads or writes outside "
        "that directory, or changes machine settings, is for the operator to approve "
        "in your client — not something to do quietly. If you need a tool that is not "
        "approved, call suggest_tool with your reasoning instead of substituting one.\n\n"
        "Bulk data is file-shaped and deliberately not a tool — fetch it with curl "
        "and your X-API-Key header, then read the file locally:\n"
        f"  report over many hosts: GET {base_url}/agent/assist/report-context.ndjson\n"
        f"  recon target list:      GET {base_url}/agent/recon/live-hosts.txt\n"
        f"  recon web targets:      GET {base_url}/agent/recon/web-targets.txt\n"
        f"  recon full host dump:   GET {base_url}/agent/recon/hosts.ndjson\n"
        f"  upload scanner output:  POST {base_url}/agent/recon/upload (multipart file)\n"
        "The tools are for targeted lookups and for recording what you did."
    )

# The tool registry moved to ``mcp_tools.py`` in v2.278.0 — see that module for
# the entry format, and for why workflow filtering is presentational.  This file
# is the transport: framing, auth, dispatch, telemetry.  The alias keeps the
# name the dispatcher and the telemetry summary already read.
_TOOLS = TOOLS


def tool_catalog(endpoint_url: str) -> Dict[str, Any]:
    """The MCP surface, described for the in-app reference page.

    Derived from the same registry the server dispatches from, so the
    documentation cannot drift from what the server actually exposes — add a
    tool and it appears on the page with no second edit.  Everything here is
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
                # Which session type sees this tool.  The page documents four
                # workflows now; without this a reader can't tell why their
                # client lists eight tools and the page shows thirty.
                "workflows": sorted(spec["workflows"]),
                "input_schema": advertised_schema(spec),
            }
            for name, spec in _TOOLS.items()
        ],
    }


# Identity lookups are server-initiated plumbing, not agent activity, but they
# ride the same audited endpoint — so a client that re-lists tools each turn fills
# the operator's activity view with rows nobody asked for (3 of 7 rows in the
# v2.273.0 end-to-end run).  A short in-process cache collapses those to one per
# key.  Keyed by a hash so raw key material never sits in the cache, and
# short-lived so an ended session stops being listed as writable quickly —
# staleness here is cosmetic anyway, since every actual call re-checks auth.
_IDENTITY_TTL_SECONDS = 60
_identity_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def _cache_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _key_identity(
    app,
    api_key: Optional[str],
    *,
    caller: Optional[Tuple[str, int]] = None,
    user_agent: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """What this key is — workflow, capabilities, bound plan/session ids.

    One lookup answers both questions the server has about a caller: which
    workflow's tools to list, and which arguments it can fill in on the caller's
    behalf.  Before v2.278.0 this read ``/agent/assist/session``, which only an
    assist key could reach — workable while assist was the whole surface, and
    useless for a recon or plan key.

    Returns None when there is no usable key or the lookup fails, which keeps an
    unauthenticated ``tools/list`` returning the full catalog: discovery degrades
    to the documentation view rather than to an empty tool list.
    """
    if not api_key:
        return None
    cached = _identity_cache.get(_cache_key(api_key))
    if cached is not None and (time.monotonic() - cached[0]) < _IDENTITY_TTL_SECONDS:
        return cached[1]
    try:
        resp = await _loopback(
            app,
            method="GET",
            path="/api/v1/agent/identity",
            api_key=api_key,
            caller=caller,
            user_agent=user_agent,
        )
        identity = resp.json() if resp.status_code == 200 else None
        if not isinstance(identity, dict):
            identity = None
    except Exception:  # pragma: no cover - defensive
        logger.exception("MCP could not read the caller's identity")
        return None
    _identity_cache[_cache_key(api_key)] = (time.monotonic(), identity)
    return identity


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

    # Arguments the caller's own key already answers — the session id its probe
    # posts to, the plan it is bound to.  Filling these server-side is not a
    # convenience: a model asked to supply them guesses, and a guessed session id
    # is a 404 (or another session's row) rather than an obvious error.  An
    # explicitly-passed value always wins, so a legitimately unbound key can
    # still say which one it means.
    auto = spec.get("auto_params") or {}
    if auto and any(arguments.get(a) is None for a in auto):
        identity = await _key_identity(
            app, api_key, caller=caller, user_agent=user_agent
        ) or {}
        for arg, field in auto.items():
            if arguments.get(arg) is None and identity.get(field) is not None:
                arguments[arg] = identity[field]

    # Path params (e.g. host_id) -> substitute into the path template.
    path = spec["path"]
    for pname in spec["path_params"]:
        value = arguments.get(pname)
        if value is None:
            if pname in auto:
                return _tool_text_result(
                    f"Could not resolve `{pname}` from your API key — call "
                    f"agent_identity to see what your key is bound to, and pass "
                    f"`{pname}` explicitly.",
                    is_error=True,
                )
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

    # No short-circuit for "write tool, no key" (removed v2.276.0): the endpoint
    # answers 401 with a better message than we can synthesise, and letting it
    # do so means every missing-credential case takes one path — which is what
    # lets the transport turn them all into a real HTTP 401 below.

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
        # With a key, list only what this session can actually do: its own
        # workflow's tools (v2.278.0), minus the writes its capabilities don't
        # cover (v2.271.0).  Advertising the rest invites the model to try them
        # and read a 403 as a bug in itself.  Without a key we list everything —
        # that's the documentation view.
        identity = await _key_identity(
            app, api_key, caller=caller, user_agent=user_agent
        )
        workflow = identity.get("workflow") if identity else None
        granted = (
            frozenset(identity.get("capabilities") or []) if identity else None
        )
        return _rpc_result(
            msg_id,
            {"tools": tool_list_payload(workflow=workflow, granted=granted)},
        )

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


# ---------------------------------------------------------------------------
# Telemetry (v2.275.0)
#
# Derived at the boundary, from the message we received and the response we
# produced, rather than threaded through every branch of the handler — one place
# to read, and the request-handling logic stays untouched by observability.
# ---------------------------------------------------------------------------

_HTTP_STATUS_IN_TOOL_ERROR = re.compile(r"^HTTP (\d{3}) from ")


def _classify(message: Any, response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Outcome, error code and detail for one message/response pair."""
    if not isinstance(message, dict):
        return {"outcome": mcp_telemetry.PROTOCOL_ERROR, "error_code": -32600,
                "detail": "Invalid Request: message was not an object"}

    if response is None:  # notification — nothing was answered, nothing can fail
        return {}

    error = response.get("error")
    if error:
        return {
            "outcome": mcp_telemetry.PROTOCOL_ERROR,
            "error_code": error.get("code"),
            "detail": error.get("message"),
        }

    result = response.get("result") or {}
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or [{}]
        text = content[0].get("text", "") if isinstance(content[0], dict) else ""
        match = _HTTP_STATUS_IN_TOOL_ERROR.match(text)
        return {
            "outcome": mcp_telemetry.TOOL_ERROR,
            # Lift the endpoint's status out of the message so failures group by
            # cause (403 capability vs 404 missing host) instead of by string.
            "error_code": int(match.group(1)) if match else None,
            "detail": text,
        }
    return {"outcome": mcp_telemetry.OK}


def _telemetry_event(
    message: Any,
    response: Optional[Dict[str, Any]],
    *,
    duration_ms: int,
    api_key: Optional[str],
    caller: Optional[Tuple[str, int]],
    user_agent: Optional[str],
) -> Optional[Dict[str, Any]]:
    classified = _classify(message, response)
    if not classified:
        return None

    method = message.get("method") if isinstance(message, dict) else None
    params = message.get("params") if isinstance(message, dict) else None
    params = params if isinstance(params, dict) else {}
    tool_name = params.get("name") if method == "tools/call" else None
    client = params.get("clientInfo") if method == "initialize" else None
    client = client if isinstance(client, dict) else {}

    return mcp_telemetry.build_event(
        rpc_method=method if isinstance(method, str) else None,
        tool_name=tool_name if isinstance(tool_name, str) else None,
        duration_ms=duration_ms,
        api_key=api_key,
        source_ip=caller[0] if caller else None,
        user_agent=user_agent,
        client_name=client.get("name"),
        client_version=client.get("version"),
        protocol_version=params.get("protocolVersion") if method == "initialize" else None,
        **classified,
    )


def _auth_failure_detail(response: Optional[Dict[str, Any]]) -> Optional[str]:
    """The endpoint's message when a call failed for want of a usable credential.

    Only 401 — *authentication*, a fact about the connection: there is no key,
    or the one presented isn't valid here.  A 403 stays an ordinary tool result
    on purpose: capability and row-scope refusals are per-call outcomes the
    model should read and work around ("that host isn't assigned to you"), not
    a signal that the whole connection is unusable.  Promoting those to a
    transport status would tell the client to re-authenticate over something it
    can't fix by re-authenticating.
    """
    if not response:
        return None
    result = response.get("result")
    if not isinstance(result, dict) or not result.get("isError"):
        return None
    content = result.get("content") or [{}]
    text = content[0].get("text", "") if isinstance(content[0], dict) else ""
    match = _HTTP_STATUS_IN_TOOL_ERROR.match(text)
    return text if match and match.group(1) == "401" else None


def _unauthorized(
    detail: str, msg_id: Any, *, key_supplied: bool, events: List[Dict[str, Any]]
) -> JSONResponse:
    """A real HTTP 401 for a call that needs a credential (v2.276.0).

    Until now this came back as HTTP 200 carrying an ``isError`` result — a
    request that succeeded at the transport layer and failed inside.  That reads
    fine to a model and not at all to a client: nothing in the exchange said
    "you are unauthenticated", so a client had no way to prompt for a key,
    surface a connection error, or stop retrying.

    ``WWW-Authenticate`` is deliberately bare.  MCP's authorization spec uses a
    401 plus ``resource_metadata`` to bootstrap OAuth discovery; advertising
    that when this server is not an OAuth resource server would send capable
    clients into a discovery flow that dead-ends.  A plain ``Bearer`` challenge
    (RFC 6750) says "authenticate with a bearer token" and stops there, which is
    exactly true.
    """
    challenge = 'Bearer realm="BlueStick assist"'
    if key_supplied:
        # The client sent something; tell it the credential is the problem
        # rather than letting it assume it forgot the header.
        challenge += ', error="invalid_token"'
    response = JSONResponse(
        # -32001 is in JSON-RPC's implementation-defined server-error range;
        # the spec reserves no code for authorization.
        _rpc_error(msg_id, -32001, detail),
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )
    response.background = BackgroundTask(mcp_telemetry.write_events, events)
    return response


def _rejected(
    request: Request, status: int, detail: str, *, api_key: Optional[str] = None
) -> JSONResponse:
    """A transport-level refusal, recorded as it is returned.

    These never reach a handler, so before v2.275.0 they were invisible: a
    client stuck on an unsupported protocol version, or one whose config sends
    a body we refuse, failed silently from our side.
    """
    caller = request.client.host if request.client else None
    response = JSONResponse(_rpc_error(None, -32600, detail), status_code=status)
    response.background = BackgroundTask(
        mcp_telemetry.write_events,
        [
            mcp_telemetry.build_event(
                rpc_method=None,
                outcome=mcp_telemetry.REJECTED,
                error_code=status,
                detail=detail,
                api_key=api_key,
                source_ip=caller,
                user_agent=request.headers.get("user-agent"),
            )
        ],
    )
    return response


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
        return _rejected(request, 403, "Origin not allowed")

    # The spec requires rejecting a version we don't implement rather than
    # carrying on and hoping the shapes line up (v2.271.0).
    negotiated = request.headers.get("MCP-Protocol-Version")
    if negotiated and negotiated not in _SUPPORTED_PROTOCOL_VERSIONS:
        return _rejected(
            request,
            400,
            f"Unsupported MCP-Protocol-Version: {negotiated}. "
            f"Supported: {', '.join(sorted(_SUPPORTED_PROTOCOL_VERSIONS))}",
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
        return _rejected(
            request,
            413,
            f"Request body exceeds the {_MAX_REQUEST_BYTES}-byte limit.",
            api_key=api_key,
        )
    try:
        payload = json.loads(raw)
    except Exception:
        response = JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=200)
        response.background = BackgroundTask(
            mcp_telemetry.write_events,
            [
                mcp_telemetry.build_event(
                    rpc_method=None,
                    outcome=mcp_telemetry.PROTOCOL_ERROR,
                    error_code=-32700,
                    detail="Parse error",
                    api_key=api_key,
                    source_ip=caller[0] if caller else None,
                    user_agent=user_agent,
                )
            ],
        )
        return response

    # A JSON-RPC batch (array) was allowed pre-2025-06-18; handle it for older
    # clients. A single object is the modern shape.
    if isinstance(payload, list):
        # JSON-RPC batching was REMOVED in the 2025-06-18 revision.  A client
        # that declared that version and then batches is out of spec, and
        # accepting it lets a client believe batching is available on a protocol
        # that dropped it.  Older revisions (2025-03-26) still allow it, so the
        # gate is the version the client declared, not a blanket refusal.
        if negotiated == "2025-06-18":
            return _rejected(
                request,
                400,
                "JSON-RPC batching was removed in protocol 2025-06-18. "
                "Send one message per request, or declare 2025-03-26.",
                api_key=api_key,
            )
        if not payload:
            return _rejected(request, 400, "Invalid Request: empty batch", api_key=api_key)
        if len(payload) > _MAX_BATCH_MESSAGES:
            return _rejected(
                request,
                413,
                f"Batch of {len(payload)} messages exceeds the "
                f"{_MAX_BATCH_MESSAGES}-message limit.",
                api_key=api_key,
            )
        messages = list(payload)
    else:
        messages = [payload]

    responses: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    for msg in messages:
        started = time.monotonic()
        resp = await _handle_message(
            request.app, msg, api_key, base_url,
            caller=caller, user_agent=user_agent,
        )
        event = _telemetry_event(
            msg,
            resp,
            duration_ms=int((time.monotonic() - started) * 1000),
            api_key=api_key,
            caller=caller,
            user_agent=user_agent,
        )
        if event:
            events.append(event)
        if resp is not None:
            responses.append(resp)

    # A single call that failed for want of a credential answers as a real HTTP
    # 401 rather than a 200 carrying an error (v2.276.0).  Single messages only:
    # a batch has no way to say "this one was unauthorized" in a status code, and
    # collapsing the whole batch to 401 would misreport the messages that
    # succeeded.  Batching is a 2025-03-26-only path anyway.
    if not isinstance(payload, list) and len(responses) == 1:
        detail = _auth_failure_detail(responses[0])
        if detail:
            return _unauthorized(
                detail,
                payload.get("id") if isinstance(payload, dict) else None,
                key_supplied=bool(api_key),
                events=events,
            )

    # Notification-only POST -> 202 Accepted, no body.  A batch collapses to one
    # response array; a single message answers on its own, as before.
    if not responses:
        response: Response = Response(status_code=202)
    elif isinstance(payload, list):
        response = JSONResponse(responses)
    else:
        response = JSONResponse(responses[0])

    # After the response is sent, on the background path — telemetry must never
    # add latency to, or be able to fail, the request it describes.
    response.background = BackgroundTask(mcp_telemetry.write_events, events)
    return response


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
