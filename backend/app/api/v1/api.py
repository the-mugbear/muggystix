from fastapi import APIRouter, Depends
from app.api.v1.endpoints import (
    scans, hosts, host_follow, host_notes, host_tags, host_bulk, host_filter_views,
    host_queries, findings,
    webhooks, dashboard, upload,
    scopes, subnet_labels, export, dns, parse_errors, reports, auth,
    audit, users, projects, notifications,
    portfolio, agents, test_plans, test_plan_bundles, feedback, llm_providers,
    integrations,
    # Per-workflow agent routers (split out of agent_api.py in v2.16.0).
    # Mounted individually below so each gets its own Swagger/Redoc tag —
    # the old single "agent-api" tag made the agent surface unscannable.
    agent_browse, agent_test_plans, agent_execution, agent_recon,
    # v2.64.0 — fourth agent surface: read-only interactive assist.
    # `agent_assist` = X-API-Key surface (agent calls these).
    # `assist` = JWT surface (operator starts/ends sessions, lists them).
    agent_assist, assist,
    # v2.24.0 — human-facing read endpoint for the agent API call log.
    agent_activity,
    # v2.30.0 — unified agent-session timeline (drives v3 UI).
    agent_sessions,
    # v3 alpha.3 — project coverage summary (drives v3 Operations).
    coverage,
    # Refactor P2 — batched Operations workbench + since-last-visit cursor.
    workbench,
    # Site-metrics arc P1 — project "needs help" attention model.
    attention,
    # Site-metrics arc P1.5 — Site entity management (tier / owner / coverage).
    sites,
    # Subnet-insights — per-subnet exposure + neglect + hygiene (the
    # attention model re-grouped by subnet; "which ranges are neglected?").
    insights,
    # v3 alpha.6 — JWT-facing recon-session detail (drives v3 Recon Run Detail).
    recon_sessions,
    # v3 alpha.7 — JWT-facing execution-session lookup by id (drives v3
    # ExecutionDetail page; permalink for /executions/:sessionId).
    execution_sessions,
    # v2.42.0 — public reference docs + agents-guide endpoints, extracted
    # from main.py.  No auth; same stance as the prior @app.get versions.
    references,
    # v2.56.0 — SOC-correlation activity surface.  Cross-project by
    # design; auth scoping is per-call via ProjectMembership.
    activity,
)
from app.api.v1.endpoints.auth import require_password_changed

api_router = APIRouter()

# === Top-level routes (no project scope) ===
# Auth router is exempt from the password-change gate (login, change-password,
# logout, and profile must remain accessible while the flag is set).
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"],
                          dependencies=[Depends(require_password_changed)])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"],
                          dependencies=[Depends(require_password_changed)])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"],
                          dependencies=[Depends(require_password_changed)])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"],
                          dependencies=[Depends(require_password_changed)])
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"],
                          dependencies=[Depends(require_password_changed)])
# Cross-project SOC-correlation activity feed.  Lives at the top
# level (not under /projects/{id}/...) because the whole point is
# "what was running across MY projects at time X" — see
# activity.py for the auth-scoping rationale.
api_router.include_router(activity.router, prefix="/activity", tags=["activity"],
                          dependencies=[Depends(require_password_changed)])
# Agent API uses its own API-key auth (not JWT), so it's exempt from
# the password-change gate — that dependency requires a JWT user.  Each
# of the four workflow routers below is mounted under /agent with its
# own tag (see _OPENAPI_TAGS in main.py for the sequences).
api_router.include_router(agent_browse.router, prefix="/agent", tags=["agent-browse"])
api_router.include_router(agent_test_plans.router, prefix="/agent", tags=["agent-plan-generation"])
api_router.include_router(agent_execution.router, prefix="/agent", tags=["agent-execution"])
api_router.include_router(agent_recon.router, prefix="/agent", tags=["agent-recon"])
api_router.include_router(agent_assist.router, prefix="/agent", tags=["agent-assist"])
# Agent feedback ingest — same API-key auth path as /agent/.
api_router.include_router(feedback.agent_feedback_router, prefix="/agent", tags=["agent-feedback"])
# Admin-only feedback triage (JWT).  Lives at /feedback rather than under
# /projects/{project_id}/... because feedback is cross-project in scope.
api_router.include_router(
    feedback.admin_feedback_router,
    prefix="/feedback",
    tags=["feedback-admin"],
    dependencies=[Depends(require_password_changed)],
)
# Per-user LLM provider credentials (self-service, not admin-gated).
api_router.include_router(
    llm_providers.router,
    prefix="/llm-providers",
    tags=["llm-providers"],
    dependencies=[Depends(require_password_changed)],
)
# Per-user integration credentials (scanner tool creds, self-service).
api_router.include_router(
    integrations.router,
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_password_changed)],
)
# The agent-facing /agent/integrations route was removed in v2.9.5
# (code review critical #2) — see integrations.py for the rationale.

# Public reference docs (preflight script, sbom, agents-guide).  No auth
# dependency — these are intentionally world-readable so an agent can
# fetch them before authenticating.
api_router.include_router(references.router, tags=["references"])

# === Project-scoped routes ===
# All data endpoints are nested under /projects/{project_id}/...
# The project_id path parameter is validated by get_current_project dependency
# in each endpoint that declares it.
project_router = APIRouter(
    prefix="/projects/{project_id}",
    dependencies=[Depends(require_password_changed)],
)

project_router.include_router(upload.router, prefix="/upload", tags=["upload"])
project_router.include_router(scans.router, prefix="/scans", tags=["scans"])
project_router.include_router(hosts.router, prefix="/hosts", tags=["hosts"])
project_router.include_router(host_follow.router, prefix="/hosts", tags=["host-follow"])
project_router.include_router(host_notes.router, prefix="/hosts", tags=["host-notes"])
project_router.include_router(findings.router, prefix="", tags=["findings"])
project_router.include_router(host_tags.router, prefix="/hosts", tags=["host-tags"])
project_router.include_router(host_bulk.router, prefix="/hosts", tags=["host-bulk"])
project_router.include_router(host_filter_views.router, prefix="/hosts", tags=["host-filter-views"])
project_router.include_router(host_queries.router, prefix="/hosts", tags=["host-queries"])
project_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
project_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
project_router.include_router(workbench.router, prefix="/workbench", tags=["workbench"])
project_router.include_router(attention.router, prefix="/attention", tags=["attention"])
project_router.include_router(sites.router, prefix="/sites", tags=["sites"])
project_router.include_router(insights.router, prefix="/insights", tags=["insights"])
# v2.86.0 — subnet labels share the /scopes prefix so URLs read as
# /projects/{pid}/scopes/subnet-labels and
# /projects/{pid}/scopes/subnets/{sid}/labels.  Separate file from
# scopes.py to keep the latter under the file-size policy (v2.65.0).
# MUST be included BEFORE scopes.router: scopes.py declares a bare
# `/{scope_id}` route (no :int converter) that would otherwise match
# `/scopes/subnet-labels` and 422 on int-parsing the path segment.
# FastAPI resolves routes in include order, so registering this first
# lets the static prefix win.
project_router.include_router(subnet_labels.router, prefix="/scopes", tags=["subnet-labels"])
project_router.include_router(scopes.router, prefix="/scopes", tags=["scopes"])
project_router.include_router(export.router, prefix="/export", tags=["export"])
project_router.include_router(dns.router, prefix="/dns", tags=["dns"])
project_router.include_router(parse_errors.router, prefix="/parse-errors", tags=["parse-errors"])
project_router.include_router(reports.router, prefix="/reports", tags=["reports"])
project_router.include_router(agents.router, prefix="/agents", tags=["agents"])
project_router.include_router(test_plans.router, prefix="/test-plans", tags=["test-plans"])
# Offline-bundle sub-surface carved out of test_plans.py — same prefix so the
# export-bundle / import-results paths are unchanged.
project_router.include_router(test_plan_bundles.router, prefix="/test-plans", tags=["test-plans"])
project_router.include_router(agent_activity.router, tags=["agent-activity"])
project_router.include_router(agent_sessions.router, tags=["agent-sessions"])
project_router.include_router(coverage.router, prefix="/coverage", tags=["coverage"])
project_router.include_router(recon_sessions.router, prefix="/recon-sessions", tags=["recon-sessions"])
project_router.include_router(execution_sessions.router, prefix="/execution-sessions", tags=["execution-sessions"])
# v2.64.0 — operator-side assist session lifecycle.  Mints the
# X-API-Key consumed by /agent/assist/*; not to be confused with
# `agent_assist.router` above.
project_router.include_router(assist.router, prefix="/assist", tags=["assist"])

api_router.include_router(project_router)
