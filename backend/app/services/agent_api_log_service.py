"""
Agent API call logging — middleware + helpers.

A Starlette middleware wraps every /api/v1/agent/* request.  After the
response is returned, the middleware inspects ``request.state`` for the
agent identifiers (populated by ``get_current_agent`` in ``api/deps.py``)
and writes one row to ``agent_api_calls``.  If authentication failed
or the request never reached an agent endpoint, no row is written.

Design constraints:

* **Never block the agent's request loop.**  Writes happen AFTER
  ``call_next`` returns, in the same request context but using a new
  short-lived DB session so the response is already on its way.  A
  failure here logs a warning but never propagates to the agent —
  losing one audit row is preferable to 5xx-ing a real workflow.
* **Never store the raw API key.**  Only the 14-character prefix
  (``nm_agent_xxxxxx``).
* **Bound storage growth.**  Request bodies are captured for mutations
  only, capped at ``MAX_BODY_BYTES`` (8 KiB).  Multipart uploads (file
  data) skip body capture entirely — only the content-type and size
  are recorded.
* **Make 'which hosts did the agent touch?' a one-query answer.**  Path
  params, query strings, and request bodies are scanned for
  ``host_id``, ``host_ids``, ``entry_id``, and IP-shaped values; the
  parsed lists go into ``referenced_*`` columns indexed for filtering.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db import session as _session_module
from app.db.models_agent import AgentApiCall, ExecutionSession

logger = logging.getLogger(__name__)


# Request-body capture cap (bytes).  Past this point the row stores
# ``{"_truncated": true, "_size": N}`` instead of the parsed JSON.
MAX_BODY_BYTES = 8 * 1024


# Only requests under this prefix are logged.
AGENT_API_PREFIX = "/api/v1/agent/"


# Public endpoints that agent tooling calls with its key, which therefore
# belong in the activity log despite sitting outside the agent prefix.
#
# v2.312.0.  The audit log decided what to record from a URL prefix, on the
# assumption that "an agent call" and "a call under /api/v1/agent/" are the same
# set.  Two MCP tools break that: ``read_agent_guide`` fetches
# ``/api/v1/agents-guide`` and ``list_approved_tools`` fetches
# ``/api/v1/references/tools``.  Both are things the agent did, with its own key,
# inside a session — and neither appeared in the operator's session view.  A
# four-call session showed two rows, which reads as a quieter agent rather than
# as a partial record.
#
# Spelled out here rather than derived from the MCP registry because a service
# must not import the router layer (``test_service_router_boundary``).  The
# agreement is enforced instead by
# ``tests/test_agent_activity_covers_mcp_tools.py``, which walks the real
# registry and fails if any tool dispatches somewhere this predicate ignores —
# so adding a tool pointed at a new public endpoint fails loudly rather than
# quietly going unlogged.
#
# Matched exactly: both entries are static, and a templated path would need real
# matching rather than a silent near-miss (the same test rejects one).
#
# Widening the predicate does not widen what is stored.  ``_write_row`` still
# discards any request that did not authenticate as an agent, so the UI's and an
# anonymous client's hits on these public endpoints record nothing.
AGENT_AUDITED_PUBLIC_PATHS = frozenset({
    "/api/v1/agents-guide",
    "/api/v1/references/tools",
})


def is_agent_audited_path(path: str) -> bool:
    """Whether a request path belongs in the agent activity log."""
    return path.startswith(AGENT_API_PREFIX) or path in AGENT_AUDITED_PUBLIC_PATHS


# Methods whose body we capture (mutations) vs skip (reads).
_BODY_CAPTURE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# IPv4 + simple IPv6 — used to pull "target_ip" out of free-form
# request bodies (sanity checks, test results) and query params.
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b[0-9a-fA-F:]{2,39}\b")  # very loose; filtered after match


def _looks_like_ip(s: str) -> bool:
    """Keep only strings that actually parse as IP addresses.

    The regexes are a cheap pre-filter (they over-match — a version
    string like ``1.2.3.4`` or a hex id can pass), so anything that
    survives is confirmed with ``ipaddress.ip_address``.  This is a
    security-relevant audit column (the activity tab filters "every call
    that touched 10.0.0.5"), so correctness wins over the marginal parse
    cost — the pre-filter keeps ipaddress off the hot path for the common
    non-IP string.
    """
    if not s or len(s) > 45:
        return False
    if not (_IPV4_RE.fullmatch(s) or (":" in s and _IPV6_RE.fullmatch(s))):
        return False
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _coerce_int_list(value: Any) -> List[int]:
    """Coerce a value into ``list[int]`` for the referenced_* columns.

    Accepts: ``int``, ``str`` (comma-separated or single), ``list``.
    Drops anything that isn't an int and dedups.  Empty list when the
    input doesn't yield any ints.
    """
    out: List[int] = []
    if value is None:
        return out
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        for piece in value.split(","):
            piece = piece.strip()
            if piece.isdigit():
                out.append(int(piece))
        return list(dict.fromkeys(out))
    if isinstance(value, list):
        for v in value:
            if isinstance(v, int):
                out.append(v)
            elif isinstance(v, str) and v.isdigit():
                out.append(int(v))
        return list(dict.fromkeys(out))
    return out


def _extract_target_ips(blob: Any) -> List[str]:
    """Walk a nested structure looking for IP-shaped string values.

    Used on path params, query params, and request bodies so the call
    log answers "which targets did the agent reference?" without us
    needing a hand-curated schema per endpoint.
    """
    found: List[str] = []
    # Today the 8 KiB MAX_BODY_BYTES cap implicitly bounds recursion depth
    # (~20 levels fit in 8 KB).  The explicit depth limit here is
    # defense-in-depth: if MAX_BODY_BYTES is ever raised, stack exhaustion
    # comes back without this guard.  32 is comfortably above any real
    # request shape and well below Python's ~1000-frame default.
    _MAX_DEPTH = 32

    def _walk(node: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, dict):
            for v in node.values():
                _walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                _walk(v, depth + 1)
        elif isinstance(node, str):
            if _looks_like_ip(node):
                found.append(node)
    _walk(blob)
    # Dedup but preserve order so the first IP an agent referenced
    # appears first in the log row.
    return list(dict.fromkeys(found))


def _collect_referenced_ids(
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    body_json: Any,
) -> Tuple[List[int], List[int], List[str]]:
    """Pull (host_ids, entry_ids, target_ips) from path + query + body.

    Path params take precedence — they're the explicit "this call is
    about host N" signal.  Query strings (e.g. ``?host_ids=1,2,3``)
    layer on top.  Bodies (mutations) are the third source.  All three
    are merged + deduped.
    """
    host_ids: List[int] = []
    entry_ids: List[int] = []

    # Path params
    for k in ("host_id", "entry_id"):
        v = path_params.get(k)
        if v is not None:
            try:
                (host_ids if k == "host_id" else entry_ids).append(int(v))
            except (TypeError, ValueError):
                pass

    # Query params — supports ``host_id`` (singular) + ``host_ids``
    # (comma-separated) since both occur in /agent/test-plans/{id}/context.
    for k in ("host_id", "host_ids"):
        if k in query_params:
            host_ids.extend(_coerce_int_list(query_params[k]))
    for k in ("entry_id", "entry_ids"):
        if k in query_params:
            entry_ids.extend(_coerce_int_list(query_params[k]))

    # Request body — common field names across our mutation endpoints.
    if isinstance(body_json, dict):
        for k, target in (
            ("host_id", host_ids),
            ("host_ids", host_ids),
            ("entry_id", entry_ids),
            ("entry_ids", entry_ids),
        ):
            if k in body_json:
                target.extend(_coerce_int_list(body_json[k]))

    target_ips = _extract_target_ips({
        "path": path_params, "query": query_params, "body": body_json,
    })

    return (
        list(dict.fromkeys(host_ids)),
        list(dict.fromkeys(entry_ids)),
        target_ips,
    )


def _summarise_body(raw: bytes, content_type: str | None) -> Optional[Any]:
    """Return a JSON-safe summary of the request body, or None.

    Strategy:
      * Empty body → None.
      * application/json under the cap → parsed JSON.
      * application/json over the cap → ``{"_truncated": true, "_size": N}``.
      * multipart/form-data (file uploads) → metadata only, no payload.
      * anything else → first MAX_BODY_BYTES bytes as a string.

    Authorization-shaped fields are dropped from any captured JSON.
    """
    if not raw:
        return None
    size = len(raw)
    ct = (content_type or "").lower()

    if ct.startswith("multipart/form-data"):
        # File uploads — capture shape but not bytes.
        return {"_multipart": True, "_size": size, "_content_type": ct}

    if size > MAX_BODY_BYTES:
        return {"_truncated": True, "_size": size, "_content_type": ct}

    if "json" in ct:
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"_unparseable_json": True, "_size": size}
        return _strip_sensitive_fields(parsed)

    # Plain text / form-encoded / unknown — store as a bounded string.
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return {"_size": size, "_content_type": ct}
    return {"_text": _redact_secret_values(text)}


# Field names we strip from captured bodies — these would never appear
# in a /agent/* body (the agent authenticates via X-API-Key, not in the
# body) but a defence-in-depth strip costs almost nothing.
_SENSITIVE_FIELDS = frozenset({
    "api_key", "apikey", "x-api-key", "authorization",
    "password", "secret", "token",
})

# Value-shaped secrets that can hide under a non-allowlisted key (e.g. a
# credential embedded in a recon command string, or a token in a ``_text``
# body).  Redacting by value as well as by key keeps these out of the
# audit table, which is surfaced to every project viewer.
_SECRET_VALUE_RE = re.compile(
    r"nm_agent_[A-Za-z0-9_-]+"                                  # BlueStick agent key
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"      # JWT (header.payload.sig)
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]+",                        # Bearer <token>
)


def _redact_secret_values(s: str) -> str:
    """Replace value-shaped secrets within a string with ``***``."""
    if not s:
        return s
    return _SECRET_VALUE_RE.sub("***", s)


def _strip_sensitive_fields(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: ("***" if k.lower() in _SENSITIVE_FIELDS else _strip_sensitive_fields(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_strip_sensitive_fields(v) for v in node]
    if isinstance(node, str):
        return _redact_secret_values(node)
    return node


def _path_template_from_route(request: Request) -> Optional[str]:
    """Recover the route's path template (``/agent/test-plans/{plan_id}/...``)
    from the scope so the log is groupable by endpoint, not just URL."""
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return route.path
    return None


def _active_execution_session_id(db: Session, plan_id: int) -> Optional[int]:
    """Best-effort: tie this call to the currently-active execution
    session of the scoped plan.  None when there is no active session
    (e.g. plan-generation phase before /execute was clicked)."""
    if not plan_id:
        return None
    row = (
        db.query(ExecutionSession.id)
        .filter(
            ExecutionSession.test_plan_id == plan_id,
            ExecutionSession.status == "active",
        )
        .order_by(ExecutionSession.started_at.desc())
        .first()
    )
    return row[0] if row else None


def _safe_write_row(
    request: Request,
    response: Response,
    body_bytes: bytes,
    duration_ms: int,
) -> None:
    """Wrapper for AgentApiCallLogger._write_row that swallows + logs
    exceptions instead of propagating them.

    v2.91.4 (third code review #5) — when the write moved to a
    Starlette BackgroundTask, an unhandled exception in the writer
    would propagate to the Starlette runner.  The response has
    already been sent, so the agent doesn't notice, but the runner
    error spams logs unhelpfully.  Mirror the pre-fix behaviour: log
    the traceback (so a recurring failure shows the column/constraint
    culprit) and swallow.
    """
    try:
        AgentApiCallLogger._write_row(
            request=request,
            response=response,
            body_bytes=body_bytes,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception(
            "agent_api_call write failed (%s: %s)", type(exc).__name__, exc,
        )


class AgentApiCallLogger(BaseHTTPMiddleware):
    """Capture every /api/v1/agent/* request to ``agent_api_calls``.

    Runs as an HTTP middleware so the same code path catches every
    agent endpoint regardless of which router file declared it.  We
    only record requests that actually authenticated as an agent (so
    401s with no agent context, and non-agent paths, are skipped).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_agent_path = is_agent_audited_path(path)

        # Read the body BEFORE call_next() — request.body() can only
        # be awaited once, and the handler downstream will read it
        # itself.  We re-inject the bytes via a receive() shim so the
        # handler still sees them.
        #
        # v2.90.2 (code review #1) — DO NOT call request.body() for
        # multipart uploads or any request whose advertised
        # content-length exceeds MAX_BODY_BYTES.  Pre-fix the
        # middleware buffered the entire multipart payload in memory
        # before FastAPI's UploadFile streaming kicked in, defeating
        # the chunked-ingestion design and OOM-killing the worker on
        # large recon uploads (4 workers × 1 GB nessus upload >>
        # 2 GB container cap).  ``_summarise_body`` was already
        # discarding multipart payloads ({"_multipart": True}); the
        # actual fix is to skip the read itself.  When we skip, the
        # audit row's body_summary is synthesised from the headers
        # instead so the operator-visible signal (content_type +
        # size) is preserved.
        # v2.240.2 (review A2) — capture is a BOUNDED TEE, never a pre-read.
        #
        # The previous shape decided whether to read from the *declared*
        # Content-Length, which trusts a header an attacker controls.  An
        # absent header (chunked transfer-encoding) and a malformed one both
        # produced ``content_length = None``, which fell through to the else
        # branch and ``await request.body()`` — materialising the whole
        # payload.  That happens in middleware, i.e. BEFORE ``call_next`` and
        # therefore before any agent authentication, so an UNAUTHENTICATED
        # caller could aim a 2 GB chunked body (nginx's ceiling) at any
        # /agent/* mutation and exhaust the container across four workers.
        # The two branches that correctly skipped were the only two the tests
        # covered.
        #
        # Now nothing is read here at all.  We wrap the ASGI receive channel
        # and record at most MAX_BODY_BYTES as the *application* consumes the
        # stream, so memory is bounded by the cap regardless of what the
        # headers claim, what the encoding is, or whether Content-Length lies.
        # If the request is rejected before the body is read (the auth path),
        # nothing is captured — which is the correct audit record anyway.
        captured = bytearray()
        capture_state = {"truncated": False}
        body_captured = False
        body_skip_reason: Optional[str] = None
        if is_agent_path and request.method in _BODY_CAPTURE_METHODS:
            ct_header = (request.headers.get("content-type") or "").lower()
            if ct_header.startswith("multipart/form-data"):
                # File uploads are never summarised; skipping the tee keeps
                # the streaming-upload path allocation-free.
                body_skip_reason = "multipart"
            else:
                original_receive = request.receive

                async def _tee() -> dict:
                    message = await original_receive()
                    if message.get("type") == "http.request":
                        chunk = message.get("body", b"") or b""
                        room = MAX_BODY_BYTES - len(captured)
                        if room > 0:
                            captured.extend(chunk[:room])
                        if len(chunk) > max(room, 0):
                            capture_state["truncated"] = True
                    return message

                request._receive = _tee  # type: ignore[attr-defined]
                body_captured = True
        request.state._agent_audit_body_captured = body_captured  # type: ignore[attr-defined]
        request.state._agent_audit_body_skip_reason = body_skip_reason  # type: ignore[attr-defined]

        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Let the exception propagate; we still want to record
            # the 5xx outcome on the way out via the route handler's
            # error wrapper.  Nothing useful to log here either way.
            raise
        duration_ms = int((time.monotonic() - started) * 1000)

        if not is_agent_path:
            return response

        # Resolved only now — the tee fills as the application reads.
        body_bytes = bytes(captured)
        if capture_state["truncated"] and body_skip_reason is None:
            # The body exceeded the cap in flight. Report it the same way a
            # header-declared oversize was reported, so the audit row keeps a
            # consistent shape whether the size was declared or discovered.
            body_skip_reason = "oversize"
            request.state._agent_audit_body_skip_reason = body_skip_reason  # type: ignore[attr-defined]

        # v2.91.4 (third code review #5) — defer the synchronous DB
        # write off the response path via Starlette's BackgroundTask.
        # Pre-fix the middleware did:
        #     response = await call_next(...)
        #     self._write_row(...)   # ← sync psycopg2 lookup + commit
        #     return response
        # The docstring claimed "Never block the agent's request loop"
        # but the SELECT + INSERT + COMMIT both blocked the event
        # loop AND added DB latency to every agent request before the
        # response was returned to the agent.  BackgroundTask runs
        # AFTER the response is sent, in anyio's thread pool, so the
        # event loop is free during the write and the agent's
        # round-trip time no longer includes audit-log latency.
        # TestClient (httpx) awaits background tasks before returning,
        # so synchronous read-after-write assertions in tests still
        # see the row.
        bg = BackgroundTask(
            _safe_write_row,
            request=request,
            response=response,
            body_bytes=body_bytes,
            duration_ms=duration_ms,
        )
        existing = getattr(response, "background", None)
        if existing is None:
            response.background = bg
        elif isinstance(existing, BackgroundTasks):
            existing.add_task(_safe_write_row,
                              request=request, response=response,
                              body_bytes=body_bytes, duration_ms=duration_ms)
        else:
            # Pre-existing single BackgroundTask from the route handler.
            # Compose into a BackgroundTasks runner so both fire.
            combined = BackgroundTasks(tasks=[existing, bg])
            response.background = combined
        return response

    @staticmethod
    def _write_row(
        request: Request,
        response: Response,
        body_bytes: bytes,
        duration_ms: int,
    ) -> None:
        # Look up SessionLocal lazily so tests can rebind it onto the
        # test engine (conftest monkey-patches app.db.session.SessionLocal
        # to a factory bound to the test connection).
        db: Session = _session_module.SessionLocal()
        try:
            # v2.44.5 — all identity fields are now nullable so a row
            # can be written even when auth never completed (pre-auth
            # 5xx crashes).  Use getattr for safety on every field;
            # request.state attrs may not be set if the auth dependency
            # raised before stamping them.
            agent_id = getattr(request.state, "agent_id", None)
            project_id = getattr(request.state, "agent_project_id", None)
            api_key_id = getattr(request.state, "api_key_id", None)
            api_key_prefix = getattr(request.state, "api_key_prefix", None)
            scoped_plan_id = getattr(request.state, "scoped_plan_id", None)
            scoped_scope_id = getattr(request.state, "scoped_scope_id", None)
            # v2.64.0 — assist-session attribution.  Stamped on
            # request.state by get_current_agent when the key carries
            # assist_session_id; NULL for plan/recon/execution keys
            # and for unscoped legacy keys.  Doubles as the "refresh
            # last_activity_at" trigger below.
            scoped_assist_session_id = getattr(
                request.state, "scoped_assist_session_id", None
            )

            path_params = dict(request.path_params or {})
            query_params = dict(request.query_params or {})

            content_type = request.headers.get("content-type")
            # v2.90.2 — when dispatch skipped the body read (multipart
            # or oversize-by-header), synthesise the same shape
            # _summarise_body would have produced so the audit row's
            # operator-visible signal (content_type + size) stays
            # intact without buffering the payload in memory.
            skip_reason = getattr(
                request.state, "_agent_audit_body_skip_reason", None,
            )
            if skip_reason == "multipart":
                cl_header = request.headers.get("content-length")
                try:
                    declared_size = int(cl_header) if cl_header is not None else 0
                except (TypeError, ValueError):
                    declared_size = 0
                body_summary = {
                    "_multipart": True,
                    "_size": declared_size,
                    "_content_type": content_type,
                    "_skipped_for_memory": True,
                }
            elif skip_reason == "oversize":
                cl_header = request.headers.get("content-length")
                try:
                    declared_size = int(cl_header) if cl_header is not None else 0
                except (TypeError, ValueError):
                    declared_size = 0
                body_summary = {
                    "_truncated": True,
                    "_size": declared_size,
                    "_content_type": content_type,
                    "_skipped_for_memory": True,
                }
            elif request.method in _BODY_CAPTURE_METHODS:
                body_summary = _summarise_body(body_bytes, content_type)
            else:
                body_summary = None
            body_for_id_scan = (
                body_summary if isinstance(body_summary, (dict, list)) else None
            )

            host_ids, entry_ids, target_ips = _collect_referenced_ids(
                path_params, query_params, body_for_id_scan,
            )

            # Tie the audit row to a recon session.
            # v2.45.0 — prefer the key's bound recon_session_id (set on
            # request.state by get_current_agent for v2.45.0+ recon
            # keys).  This is the same fix that makes _load_recon_session
            # collision-safe under concurrent recons.  Falls back to the
            # legacy "newest active session per scope" lookup only when
            # the key predates v2.45.0 (recon_session_id NULL on the
            # APIKey row); those keys carry the original collision risk
            # but the audit attribution is at least consistent with what
            # the request handler resolved.
            recon_session_id = getattr(
                request.state, "scoped_recon_session_id", None
            )
            if recon_session_id is None and scoped_scope_id is not None:
                from app.db.models_agent import ReconSession, ReconSessionStatus
                row = (
                    db.query(ReconSession.id)
                    .filter(
                        ReconSession.scope_id == scoped_scope_id,
                        ReconSession.status == ReconSessionStatus.ACTIVE.value,
                    )
                    .order_by(ReconSession.started_at.desc())
                    .first()
                )
                if row:
                    recon_session_id = row[0]

            execution_session_id = _active_execution_session_id(
                db, scoped_plan_id,
            ) if scoped_plan_id else None

            # Response size (Content-Length header if present; otherwise
            # we leave it null rather than buffer the streaming body).
            cl = response.headers.get("content-length")
            response_bytes = int(cl) if cl and cl.isdigit() else None

            # v2.44.5 — error_class is populated by the global exception
            # handler via request.state on unhandled exceptions.  We only
            # record it on 5xx responses so successful requests don't
            # carry irrelevant data; non-5xx with a stale state attr
            # (shouldn't happen but) is ignored on read by convention.
            error_class = None
            if response.status_code >= 500:
                error_class = getattr(
                    request.state, "unhandled_exception_class", None
                )

            # v2.271.0 — an unauthenticated call has no attribution, and the
            # table's CHECK requires attribution OR an error_class.  We used to
            # attempt the INSERT anyway: Postgres rejected it and _safe_write_row
            # logged a full IntegrityError traceback, so every anonymous probe of
            # an /agent/* path produced an ERROR-level stack trace that looked
            # like a real fault.  A rejected-before-auth request is not an audit
            # record; the correct row is no row.  5xx still gets one (that's the
            # pre-auth crash audit the error_class column exists for).
            if not ((agent_id is not None and project_id is not None) or error_class):
                return

            row = AgentApiCall(
                agent_id=agent_id,
                api_key_id=api_key_id,
                api_key_prefix=api_key_prefix,
                source_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                project_id=project_id,
                test_plan_id=scoped_plan_id,
                execution_session_id=execution_session_id,
                scope_id=scoped_scope_id,
                recon_session_id=recon_session_id,
                assist_session_id=scoped_assist_session_id,
                method=request.method,
                path=request.url.path,
                path_template=_path_template_from_route(request),
                path_params=path_params or None,
                query_params=query_params or None,
                request_body_summary=body_summary,
                status_code=response.status_code,
                response_bytes=response_bytes,
                duration_ms=duration_ms,
                referenced_host_ids=host_ids or None,
                referenced_entry_ids=entry_ids or None,
                referenced_target_ips=target_ips or None,
                error_class=error_class,
            )
            db.add(row)
            # v2.64.0 — refresh AssistSession.last_activity_at on every
            # assist call (any status — even a 4xx is "I tried").
            # Lets the UI show "idle 47m" cheaply without scanning
            # agent_api_calls.  Recon and execution sessions don't
            # need this (their summary endpoints carry their own
            # progress signals); assist is conversational, so an
            # idle marker is meaningful here.
            if scoped_assist_session_id is not None:
                from datetime import datetime as _dt, timezone as _tz
                from app.db.models_agent import AssistSession as _AssistSession
                db.query(_AssistSession).filter(
                    _AssistSession.id == scoped_assist_session_id
                ).update(
                    {"last_activity_at": _dt.now(_tz.utc)},
                    synchronize_session=False,
                )
            db.commit()
        finally:
            db.close()


def purge_older_than(db: Session, days: int) -> int:
    """Delete agent_api_call rows older than ``days``.

    Returns the row count for the operator's records.  Run via a
    daily cron / manual CLI invocation — there is no automatic
    schedule yet, on the principle that the table grows slowly
    relative to disk and we want operators to see the volume before
    we silently prune.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = (
        db.query(AgentApiCall)
        .filter(AgentApiCall.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted
