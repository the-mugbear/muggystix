import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api import api_router
from app.db.init import initialize_database
from app.startup import (
    agent_api_call_retention_loop,
    ensure_default_project,
    expired_session_cleanup_loop,
    get_instance_id,
    seed_default_admin,
    seed_system_identity,
)

# Configure logging.
#
# v2.44.3 — explicitly attach the stream handler to the `app` package
# logger (in addition to basicConfig on root).  Uvicorn's worker
# startup replaces the root logger's handler list with its own
# (uvicorn / uvicorn.error / uvicorn.access loggers get configured,
# root is left bare), so any `logging.getLogger("app.*")` records that
# propagated to root were silently dropped before this fix — including
# the global exception handler's traceback.  Attaching the handler
# directly to `app` (and setting propagate=False to avoid double-
# emitting if root ever regains a handler) makes our application logs
# survive uvicorn's reconfiguration.
_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _app_handler = logging.StreamHandler(sys.stdout)
    _app_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    _app_logger.addHandler(_app_handler)
_app_logger.propagate = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger(__name__)

initialize_database()

_OPENAPI_DESCRIPTION = """\
BlueStick aggregates output from multiple network-scanning and reconnaissance
tools (Nmap, Nessus, Masscan, Netexec, Eyewitness) into a single, deduplicated
data model and exposes it through a RESTful JSON API.

## Authentication

BlueStick has **two** auth schemes depending on which surface you're
calling:

### 1. JWT (humans + admin tooling)

All endpoints under `/api/v1/*` *except* `/api/v1/auth/login` and the
`/api/v1/agent/*` group require a **Bearer JWT** token in the
`Authorization` header.

```
POST /api/v1/auth/login  {"username": "...", "password": "..."}
→ {"access_token": "<jwt>", "token_type": "bearer", ...}

Authorization: Bearer <jwt>
```

Tokens expire after **8 hours**. Account lockout occurs after 5 failed login
attempts (30-minute cooldown).

### 2. API key (AI agents)

Endpoints under `/api/v1/agent/*` use a project-scoped, time-limited
**API key** in the `X-API-Key` header (not JWT).

```
X-API-Key: nm_agent_abc123...
```

Keys are issued via `POST /test-plans/generate`, default to a 24-hour
TTL (configurable via `AGENT_KEY_TTL_HOURS`), and are scoped to one
project + one agent identity. The agent surface is intentionally
narrow: it can read project context (hosts, scans, vulns) and create /
populate / submit a single test plan, but it cannot manage users,
projects, or other plans. See AGENTS.md for the full integration guide.

## Role hierarchy

| Role       | Level | Capabilities |
|------------|-------|--------------|
| **admin**  | 4     | Full access, user management, system settings |
| **analyst**| 3     | Scope management, upload scans, manage parse errors |
| **auditor**| 2     | Read-only access with audit log visibility |
| **viewer** | 1     | Basic read-only access to scans, hosts, and dashboards |

Higher roles inherit all permissions of lower roles.

## Multi-project architecture

All data endpoints are scoped to a project:
`/api/v1/projects/{project_id}/scans/`, `/api/v1/projects/{project_id}/hosts/`, etc.

Top-level endpoints (no project scope): `/auth/`, `/users/`, `/audit/`, `/projects/`, `/notifications/`.

## Typical workflows

### Human / admin discovery (JWT)

1. **Authenticate** — `POST /auth/login` to obtain a JWT.
2. **Select project** — `GET /projects/` to list available projects, note the `id`.
3. **Discover data** — `GET /projects/{id}/dashboard/stats` for an overview,
   `GET /projects/{id}/scans/` to list ingested scans.
4. **Query hosts** — `GET /projects/{id}/hosts/` with filters (ports, services,
   OS, risk score, vulnerability severity, follow status, etc.).
5. **Drill down** — `GET /projects/{id}/hosts/{host_id}` for full host detail
   including ports, scripts, notes, and vulnerabilities.
6. **Assess risk** — `POST /projects/{id}/risk/hosts/{host_id}/assess-risk` to
   trigger analysis.
7. **Export** — `GET /projects/{id}/export/scope/{scope_id}?format_type=json` or
   `GET /projects/{id}/hosts/tool-ready/{format}` for tool-compatible output.

### Personal review queue (JWT)

1. **My queue** — `GET /projects/{id}/dashboard/my-attention` returns hosts you've
   personally marked `in_review` via `POST /projects/{id}/hosts/{host_id}/follow`.
2. **My tasks** — `GET /projects/{id}/dashboard/my-tasks` returns non-terminal
   test plan entries on hosts in your in-review queue.
3. **New scans alert** — `GET /projects/{id}/dashboard/new-scans-since?since=<iso>`
   counts scans uploaded since your last visit (the frontend tracks the cursor in
   localStorage).

### AI agent workflows (API key)

The `/agent/*` surface supports **three** workflows, each with its own
scope-bound key and its own tag below:

#### Plan generation — tag `agent-plan-generation`

1. **Operator generates a plan** via the UI — `POST /projects/{id}/test-plans/generate`
   returns a plan id, an `X-API-Key` value, and a copy-pasteable instructions block.
2. **Agent fetches candidate hosts** — `GET /agent/test-plans/{plan_id}/context`
   returns the post-policy-filtered candidates plus a selection rubric.
3. **Agent sets the plan description** — `PATCH /agent/test-plans/{plan_id}` (with the
   `🤖 **Agent-generated** — {agent_name}` attribution prefix).
4. **Agent posts entries** — `POST /agent/test-plans/{plan_id}/entries` with structured
   `proposed_tests` (tool, command, expected_result).
5. **Validate + submit** — `GET .../validate` for a dry-run check, then
   `POST .../submit` moves the plan from `draft` to `proposed` for human review.

#### Plan execution — tag `agent-execution`

1. **Operator approves and starts execution** from the UI — backend mints a
   fresh execution-scoped key bound to that plan.
2. **Agent fetches the execution context** — `GET /agent/test-plans/{plan_id}/execution-context`.
3. **Per host, agent records a sanity check then each test result** —
   `POST .../sanity-check`, `POST .../test-results`. `target_ip` is validated against
   the entry's host; results are upserted by `test_index`.
4. **Agent completes each entry** — `POST .../complete`, which also surfaces a
   `sanity_check_missing` flag if no passing verification was recorded.
5. **Progress is visible live to humans** — `GET .../execution-progress`.

#### Reconnaissance — tag `agent-recon`

1. **Operator starts a recon session against a scope** — backend mints a
   scope-bound key (a plan key cannot touch recon endpoints and vice-versa).
2. **Agent reads scope context + suggested tool catalog** —
   `GET /agent/recon/context`.
3. **Agent runs scanners locally and uploads output** — `POST /agent/recon/upload`
   feeds the existing ingestion pipeline; the upload is attributed to the recon
   session in the same transaction that makes it visible.
4. **Agent polls and reads progress** — `GET .../jobs/{id}`, `GET .../summary`.
5. **Agent closes the session** — `POST .../complete`.

Agent keys are project-scoped, time-limited (default 24h, `AGENT_KEY_TTL_HOURS`),
and bound to **either** one plan (plan-gen/execution) **or** one scope (recon).
Cross-workflow calls return 403. The `agent-browse` tag below documents the
read-only host/scope/dashboard surface shared by all three workflows.

## Error conventions

All error responses use `{"detail": "..."}`. Common HTTP status codes:

- **401** — Missing or invalid JWT
- **403** — Valid JWT but insufficient role
- **404** — Resource not found
- **422** — Request validation error (malformed params or body)
"""

_OPENAPI_TAGS = [
    {
        "name": "authentication",
        "description": "Login, logout, session management, and profile. **Start here** — all other endpoints require a valid token.",
    },
    {
        "name": "upload",
        "description": "Upload scan files (Nmap XML, Nessus, Masscan, Eyewitness, DNS CSV, etc.) for background ingestion. Track job status via job endpoints.",
    },
    {
        "name": "scans",
        "description": "List, inspect, and delete ingested scans. Includes per-scan host/port counts, Eyewitness screenshots, and command explanations.",
    },
    {
        "name": "hosts",
        "description": "Query and filter the deduplicated host inventory. Supports 20+ filter parameters, sorting, and pagination. Primary discovery surface for agents.",
    },
    {
        "name": "host-follow",
        "description": "Track review progress on individual hosts (watching → in_review → reviewed).",
    },
    {
        "name": "host-notes",
        "description": "Threaded notes and comments on hosts, with unread-count tracking and activity feeds.",
    },
    {
        "name": "host-queries",
        "description": "Boolean query DSL for the /hosts page: validate a query without executing it (returns a live match count), fetch the field/operator schema that powers autocomplete, and manage per-user recent-query history.",
    },
    {
        "name": "dashboard",
        "description": "Pre-aggregated statistics: host/port/scan counts, OS distribution, top ports, risk insights, and review progress.",
    },
    {
        "name": "scopes",
        "description": "Manage network scopes and CIDR subnets. Upload subnet files, check coverage, and correlate hosts to scopes.",
    },
    {
        "name": "export",
        "description": "Download host lists from scopes or scans in txt (one-IP-per-line), CSV, or JSON. Responses include Content-Disposition headers for file downloads.",
    },
    {
        "name": "dns",
        "description": "DNS record lookups and zone transfer attempts.",
    },
    {
        "name": "parse-errors",
        "description": "Track and manage file-parsing failures with user-friendly error IDs, status workflows (unresolved → reviewed → fixed → ignored), and statistics.",
    },
    {
        "name": "reports",
        "description": "Generate filtered host reports in CSV, HTML, or JSON. Supports the same filter parameters as the hosts endpoint. Max 10,000 hosts per report.",
    },
    {
        "name": "risk",
        "description": "Security risk assessment — trigger analysis, view risk scores, vulnerabilities, security findings, and recommendations per host.",
    },
    {
        "name": "audit",
        "description": "Audit trail logging and retrieval. Admin-only for log queries and statistics.",
    },
    {
        "name": "users",
        "description": "User account management (admin-only) and self-service profile updates. RBAC with four roles: admin, analyst, auditor, viewer.",
    },
    {
        "name": "projects",
        "description": "Create and manage projects. Each project isolates scans, hosts, scopes, and findings. Users are assigned to projects with per-project roles.",
    },
    {
        "name": "notifications",
        "description": "In-app notifications for @mentions, status changes, and project assignments. Supports read/unread tracking.",
    },
    {
        "name": "portfolio",
        "description": "Cross-project summary surface — health, staleness, host counts, and review state for every project the current user belongs to. Used by the Portfolio page.",
    },
    {
        "name": "agents",
        "description": "AI agent identity and API key management within a project. Create agents, rotate keys, set rate limits. Distinct from `agent-api` (which is what an agent *uses*); this surface is what an admin uses to *provision* agents.",
    },
    {
        "name": "test-plans",
        "description": "Human-facing test plan management — list, view, approve, reject, generate (with AI), edit entries, delete. The agent-facing flip side of this surface is documented under `agent-api`. See the workflow explainer on the Test Plans page or AGENTS.md for the five-phase lifecycle (draft → proposed → approved → in_progress → completed).",
    },
    {
        "name": "agent-browse",
        "description": "Read-only browse surface shared by all three agent workflows: hosts, scans, scopes, dashboard, notes, follows. Authenticated via `X-API-Key`; data is automatically scoped to the agent's project.",
    },
    {
        "name": "agent-plan-generation",
        "description": "Agent-driven test plan population.  **Sequence:** `GET /agent/test-plans/{id}/context` → `PATCH /agent/test-plans/{id}` (set description) → `POST /agent/test-plans/{id}/entries` → `GET /agent/test-plans/{id}/validate` → `POST /agent/test-plans/{id}/submit`. The key is bound to one plan; calls to recon endpoints return 403.",
    },
    {
        "name": "agent-execution",
        "description": "Agent-driven execution of an approved plan.  **Sequence per host:** `GET /agent/test-plans/{id}/execution-context` → `POST .../sanity-check` (target_ip must match the entry's host) → `POST .../test-results` (upserts by `test_index`) → `POST .../complete` (returns `sanity_check_missing` if no passing check was recorded). Humans can watch progress via `GET .../execution-progress`.",
    },
    {
        "name": "agent-recon",
        "description": "Agent-driven reconnaissance against a scope: read context, upload scanner output, poll the parse job, read the rolling summary, then complete the session.  **Sequence:** `GET /agent/recon/context` → `POST /agent/recon/upload` → `GET .../jobs/{id}` → `GET .../summary` → `POST .../complete`. The key is bound to one scope; calls to plan endpoints return 403.",
    },
    {
        "name": "agent-feedback",
        "description": "Agents POST structured feedback at the end of each run (API critiques, friction notes, agent/model identity, token-usage metrics). Used to track prompt-version effectiveness over time.",
    },
]

# v2.68.0 — `@app.on_event("startup")` is deprecated in FastAPI in
# favour of the lifespan context-manager pattern.  Body is identical
# to the prior startup_event handler: wire the worker's stderr to the
# app logger, re-enable disabled loggers, seed first-boot data, spawn
# the long-running housekeeping loops.  Shutdown side (after yield)
# is currently a no-op — the background tasks were fire-and-forget
# under the old hook too, and uvicorn cancels them on process exit.
# Wiring proper cooperative cancellation is a follow-up that needs
# its own slice (each loop would need a sentinel-aware sleep).
@asynccontextmanager
async def _app_lifespan(app_: FastAPI):
    await _run_startup_sequence()
    yield


app = FastAPI(
    title="BlueStick API",
    description=_OPENAPI_DESCRIPTION,
    version=settings.APP_VERSION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=_app_lifespan,
)

cors_origins = [origin for origin in settings.CORS_ORIGINS if origin and origin != "*"]
if not cors_origins:
    logger.warning(
        "CORS_ORIGINS is empty or contained only wildcards; defaulting to localhost origins"
    )
    cors_origins = [
        "https://localhost",
        "https://localhost:3000",
        "https://127.0.0.1",
    ]

if len(cors_origins) != len(settings.CORS_ORIGINS):
    logger.warning(
        "Wildcard origins are not permitted; update CORS_ORIGINS to list explicit domains"
    )

# Set up CORS allowing only explicitly configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Explicit allowlist instead of "*": with allow_credentials=True a
    # wildcard expose is over-broad, and only these response headers are
    # actually read by the frontend (download filename + bundle export
    # correlation ids — see services/api/test-plans.ts and services/api.ts).
    expose_headers=["Content-Disposition", "X-Bundle-Id", "X-Execution-Session-Id"],
)

# v2.24.0 — audit every /api/v1/agent/* request that authenticates as
# an agent.  Writes happen AFTER the response is sent, in a fresh DB
# session, so this never blocks the agent's request loop.  See
# app/services/agent_api_log_service.py.
from app.services.agent_api_log_service import AgentApiCallLogger
app.add_middleware(AgentApiCallLogger)

# v2.177.0 (audit B5) — request-correlation id + per-request latency.  Added
# LAST so it sits OUTERMOST: the id is set before the agent logger / exception
# handler run, and the access-log line wraps the whole stack.  Pure ASGI so it
# doesn't buffer (and thus can't break) the streaming CSV export.
from app.core.request_context import RequestContextMiddleware
app.add_middleware(RequestContextMiddleware)


# v2.44.2 — global unhandled-exception handler.  FastAPI's default
# behavior on an unhandled exception is to return a bare 500 with the
# body "Internal Server Error" AND log nothing.  That left a real
# diagnostic blackout: agent A reported "repeated 500 errors" with
# zero matching tracebacks in backend_logs.txt (recon session 1,
# 2026-05-18) because the handler had crashed, FastAPI returned 500,
# and there was no logger that observed the crash on the way out.
#
# This handler logs the full traceback (Python's exception chaining
# preserved), echoes the path + method so it correlates against the
# audit log, and returns a sanitized 5xx envelope that mirrors the
# shape of the existing HTTPException responses without leaking
# internals to the client.
from fastapi import Request
from fastapi.responses import JSONResponse
from app.services.host_query_dsl import DSLError


@app.exception_handler(DSLError)
async def _handle_dsl_error(request: Request, exc: DSLError):
    """A malformed /hosts ``q=`` boolean query → 400 with the message and
    the character ``position`` of the problem so the UI can underline it.

    Registered explicitly because the catch-all ``Exception`` handler
    below would otherwise turn it into a 500 (FastAPI dispatches to the
    most specific handler by MRO)."""
    return JSONResponse(
        status_code=400,
        content={"detail": exc.message, "position": exc.position},
    )


@app.exception_handler(Exception)
async def _log_and_sanitize_unhandled_exception(request: Request, exc: Exception):
    # Stash the exception class on request.state so the agent-API
    # logging middleware can surface it in its structured WARNING for a
    # 5xx that happened before/without auth.  (The agent_api_calls identity
    # columns — agent_id/project_id — are now nullable, so such a row CAN
    # be written; the remaining blind spot is an exception raised inside
    # the middleware body itself, e.g. a client disconnect during
    # request.body(), which re-raises before any row is written.)
    try:
        request.state.unhandled_exception_class = exc.__class__.__name__
    except Exception:  # pragma: no cover — request.state should always be mutable
        pass
    from app.core.request_context import get_request_id
    request_id = get_request_id()
    logger.exception(
        "Unhandled exception serving %s %s (req=%s): %s",
        request.method,
        request.url.path,
        request_id,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_class": exc.__class__.__name__,
            "path": request.url.path,
            # Surfaced so a user/agent can quote it when reporting the failure;
            # it joins the response to the traceback + access-log line.
            "request_id": request_id,
        },
    )


# Include API router
app.include_router(api_router, prefix="/api/v1")

async def _run_startup_sequence() -> None:
    """Called from the lifespan handler.  Promoted from the old
    `@app.on_event("startup")` body in v2.68.0 — same logger wiring,
    seeding, and background-task spawn as before.  Stays async so the
    final `asyncio.create_task(...)` calls land on the running event
    loop the lifespan context manager owns; nothing in the body itself
    awaits, but the create_task calls implicitly use the current
    loop."""
    # v2.44.5 — wire the `app` package logger to a StreamHandler on
    # sys.stderr SO LONG AS WE'RE INSIDE A LIVE UVICORN WORKER.  Why
    # this is harder than it sounds:
    #
    #   * My v2.44.3 fix attached a handler at module import.  But
    #     uvicorn worker startup runs AFTER main.py loads and reassigns
    #     the worker's stdout pipe (via the dictConfig in
    #     uvicorn.config.LOGGING_CONFIG + multiprocessing fork
    #     plumbing).  My handler kept a stale reference to the original
    #     sys.stdout and emitted into a disconnected pipe — invisible
    #     to `docker logs`.
    #
    #   * My first v2.44.5 attempt tried to ADOPT uvicorn's handler
    #     (`logging.getLogger("uvicorn").handlers`).  Empirically that
    #     list is empty at startup_event time in the worker context,
    #     even though it IS populated when we dictConfig() the same
    #     LOGGING_CONFIG in a fresh `python -c` subprocess.  Reason:
    #     uvicorn's `configure_logging()` runs in the PARENT process
    #     before workers are spawned, and the child workers inherit
    #     the dict-configured state — but the `logging` module's
    #     in-process logger registry isn't propagated across fork the
    #     way you'd expect.  Each worker re-resolves the logger by
    #     name and gets a fresh, unconfigured one.
    #
    # Solution: attach our own handler HERE, in the worker's actual
    # startup_event context, capturing sys.stderr AS THE WORKER SEES
    # IT RIGHT NOW.  sys.stderr at this point is the worker's real
    # pipe to docker logs (uvicorn's own "INFO: Uvicorn running" line
    # proves it's connected).  propagate=False to avoid duplicating
    # to root, even though root has no handlers in this environment.
    app_logger = logging.getLogger("app")
    # Clear any module-import-time handler (v2.44.3 leftover) that
    # may be holding a stale stdout reference; replace with one bound
    # to the worker's live sys.stderr.
    app_logger.handlers = []
    worker_handler = logging.StreamHandler(sys.stderr)
    worker_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    app_logger.addHandler(worker_handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    # Diagnostic — print() is a proof-of-life that bypasses logging
    # and proves the worker's stdout/stderr is connected.  logger.info
    # is the actual mechanism we want to verify; if it shows up
    # alongside the print() in `docker logs`, the chain works
    # end-to-end (logger -> our handler -> live worker stderr ->
    # docker stream).  If only the print appears, the handler we
    # just attached doesn't write to the live stream — operators
    # can pin down which worker is broken via the pid in both.
    # ROOT CAUSE found via diagnostic in v2.44.5-debug build:
    # uvicorn's worker startup runs `logging.config.dictConfig(
    # uvicorn.config.LOGGING_CONFIG)` which uses Python logging's
    # default `disable_existing_loggers=True` semantic.  Every logger
    # created at module-import time before uvicorn's dictConfig is
    # silently marked `disabled=True` — including `app` and every
    # `app.*` child we created when main.py loaded.  Disabled loggers
    # drop records BEFORE the handler check, so even attaching a
    # working StreamHandler does nothing.
    #
    # Fix: walk every existing `app.*` logger and clear the disabled
    # flag.  Logger registry lookups are cheap; iterating the manager
    # dict is the canonical way to find descendants of a package
    # logger.  Future loggers created after this (e.g. when a
    # service module is first imported during request handling)
    # won't be disabled — only ones that existed pre-dictConfig were.
    re_enabled = 0
    for name, lg in logging.Logger.manager.loggerDict.items():
        if isinstance(lg, logging.Logger) and (name == "app" or name.startswith("app.")):
            if getattr(lg, "disabled", False):
                lg.disabled = False
                re_enabled += 1
    # Use print so this is visible even if logging is still broken.
    print(
        f"[startup_event] pid={os.getpid()} re-enabled {re_enabled} disabled app.* loggers; "
        f"app handlers={[type(h).__name__ for h in app_logger.handlers]}",
        flush=True,
    )
    # Now verify logger.info works end-to-end.  This line MUST appear
    # in docker logs — if it doesn't, something else is killing
    # records and operators need to escalate.
    logger.info(
        "[startup] pid=%d app logger end-to-end emit verified; "
        "exception tracebacks will reach docker logs",
        os.getpid(),
    )

    logger.info("BlueStick API starting up...")
    logger.info(
        "Running backend v%s (frontend v%s)",
        settings.APP_VERSION,
        settings.FRONTEND_VERSION,
    )
    logger.info(f"CORS origins: {cors_origins}")

    seed_default_admin()
    ensure_default_project()
    seed_system_identity()
    _warn_if_pool_undersized()
    # Hourly housekeeping: mark expired UserSession rows as revoked so they
    # stop accumulating. Idempotent — if multiple uvicorn workers each fire
    # this it's wasteful but not incorrect.
    asyncio.create_task(expired_session_cleanup_loop())
    # v2.65.0 — daily purge of agent_api_calls rows older than the
    # retention window (env: AGENT_API_CALL_RETENTION_DAYS, default 90).
    # Set 0 to disable.  Same multi-worker idempotency as the session
    # reaper — DELETEs are naturally idempotent.
    asyncio.create_task(agent_api_call_retention_loop())


def _warn_if_pool_undersized() -> None:
    """Log a WARNING if DB_POOL_SIZE + DB_MAX_OVERFLOW can't keep up with the
    uvicorn worker count.  Connection starvation manifests as random query
    timeouts that read like "the DB is slow"; this sanity check turns the
    silent-misconfig case into an actionable log line at boot.

    Heuristic: each worker needs at least 2 concurrent connections (request
    + middleware-side audit logger).  Below that the pool will block under
    moderate load even with no real contention.
    """
    workers = int(os.getenv("UVICORN_WORKERS", "4"))
    capacity = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
    needed = workers * 2
    if capacity < needed:
        logger.warning(
            "DB pool capacity (%d = pool_size %d + max_overflow %d) is below "
            "2x UVICORN_WORKERS (%d).  Expect intermittent connection-pool "
            "starvation under load.  Bump DB_POOL_SIZE / DB_MAX_OVERFLOW in "
            ".env, or drop UVICORN_WORKERS to match.",
            capacity, settings.DB_POOL_SIZE, settings.DB_MAX_OVERFLOW, workers,
        )


# --- everything below this point was extracted to app/startup.py +
# --- app/services/agents_guide_service.py + app/api/v1/endpoints/references.py
# --- in v2.42.0.  Stub block kept solely to make the diff legible; the bodies
# --- live in the new modules.  Removed: _expired_session_cleanup_loop,
# --- _seed_default_admin, _ensure_default_project, _seed_system_identity,
# --- _get_instance_id, _cached_instance_id, _slice_agents_md, plus the
# --- /api/v1/agents-guide and /api/v1/references/* endpoints.

@app.get('/')
async def root():
    return {
        'message': 'BlueStick API',
        'version': settings.APP_VERSION,
        'frontend_version': settings.FRONTEND_VERSION,
        'instance_id': get_instance_id(),
        'cors_origins': cors_origins,
    }


@app.get('/health')
def health_check():
    # Real readiness probe.  A static 200 only proved uvicorn was up — not
    # that the app could reach its database — so a DB outage still reported
    # "healthy" (the Dockerfile HEALTHCHECK curls this, and `curl -f` fails on
    # 503, so an unreachable DB now correctly marks the container unhealthy).
    # Sync def → FastAPI runs it in the threadpool, keeping the SELECT 1 off
    # the event loop.  Unhealthy status alone does not restart the container
    # (Docker only restarts on process exit), so a transient blip is visible,
    # not disruptive.
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    db = SessionLocal()
    try:
        db.execute(_sql_text("SELECT 1"))
        return {'status': 'healthy', 'database': 'ok'}
    except Exception:
        logger.exception("Health check DB probe failed")
        return JSONResponse(
            status_code=503,
            content={'status': 'unhealthy', 'database': 'unreachable'},
        )
    finally:
        db.close()


@app.get('/.well-known/networkmapper.json')
async def well_known_identity():
    """Instance identity document — unauthenticated, intentionally public.

    Agents call this once at the start of a session to verify that the URL
    they're being asked to curl belongs to the same entity that generated
    their prompt.  The prompt embeds ``instance_id`` and tells the agent
    to cross-check here.

    Intentionally thin — does not expose user counts, project counts, or
    any enumeration-friendly details.  Just enough for an agent to decide
    "is this the BlueStick instance I'm supposed to be talking to?"
    """
    return {
        'instance_id': get_instance_id(),
        'name': 'BlueStick',
        'version': settings.APP_VERSION,
        'purpose': 'Authorized penetration testing operations platform',
        'documentation_url': '/docs',
        'security_contact': os.getenv('NM_SECURITY_CONTACT'),
        'safety_properties': {
            'all_commands_require_user_approval': True,
            'no_autonomous_execution': True,
            'audit_trail_persistent': True,
            'agent_keys_time_limited': True,
            'agent_keys_scope_bound': True,
        },
    }
