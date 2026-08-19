"""Telemetry for the MCP transport (v2.275.0).

The agent-API audit log records requests that reach ``/agent/*``.  Everything the
MCP layer rejects short of that — unknown tool, arguments that don't fit the
advertised schema, a refused batch, an unsupported protocol version — reached
nothing and was recorded nowhere.  That gap is not academic: for two releases the
environment-probe tool rejected the exact fields the assist prompt instructs
agents to send, blocking every conforming agent, and no surface in the system
could have shown it.

What this answers that asking agents cannot: which tools are never called, which
fail repeatedly, which arguments agents keep getting wrong, which clients
connect, and whether anyone is failing to connect at all.

Writes happen AFTER the response, on Starlette's background-task path, in a
fresh session — the same pattern as the agent audit log, for the same reason:
telemetry must never add latency to, or fail, the request it describes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import session as _session_module
from app.db.models_agent import McpToolCall

logger = logging.getLogger(__name__)

# Outcome vocabulary — see the column comment on McpToolCall.
OK = "ok"
TOOL_ERROR = "tool_error"
PROTOCOL_ERROR = "protocol_error"
REJECTED = "rejected"

# Key prefixes are for correlation, not authentication; keep them short enough
# that a leaked telemetry dump is not a step toward guessing a key.
_KEY_PREFIX_LEN = 16
_DETAIL_MAX = 500


def key_prefix(api_key: Optional[str]) -> Optional[str]:
    return api_key[:_KEY_PREFIX_LEN] if api_key else None


def build_event(
    *,
    rpc_method: Optional[str],
    outcome: str,
    tool_name: Optional[str] = None,
    error_code: Optional[int] = None,
    detail: Optional[str] = None,
    duration_ms: Optional[int] = None,
    api_key: Optional[str] = None,
    source_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    client_name: Optional[str] = None,
    client_version: Optional[str] = None,
    protocol_version: Optional[str] = None,
) -> Dict[str, Any]:
    """One row's worth of telemetry, as a plain dict.

    Plain dicts rather than ORM objects because these are assembled on the
    request path and written on the background path — a detached ORM instance
    crossing that boundary is a footgun for no benefit.
    """
    return {
        "rpc_method": rpc_method,
        "tool_name": tool_name,
        "outcome": outcome,
        "error_code": error_code,
        "detail": (detail or "")[:_DETAIL_MAX] or None,
        "duration_ms": duration_ms,
        "api_key_prefix": key_prefix(api_key),
        "source_ip": source_ip,
        "user_agent": user_agent,
        "client_name": client_name,
        "client_version": client_version,
        "protocol_version": protocol_version,
    }


def write_events(events: List[Dict[str, Any]]) -> None:
    """Persist a request's events.  Never raises — telemetry must not be able to
    turn a served request into an error after the fact."""
    if not events:
        return
    db: Session = _session_module.SessionLocal()
    try:
        db.add_all([McpToolCall(**e) for e in events])
        db.commit()
    except Exception:
        logger.exception("MCP telemetry write failed (%d event(s) dropped)", len(events))
        db.rollback()
    finally:
        db.close()


def summarise(db: Session, *, hours: int = 24, limit: int = 20) -> Dict[str, Any]:
    """Aggregate view for the admin telemetry endpoint.

    Deliberately shaped around the questions rather than the schema: per-tool
    call counts with their failures, the failure modes ranked, and the clients
    seen.  A raw row dump would answer none of them without further work.
    """
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = db.query(McpToolCall).filter(McpToolCall.created_at >= since)

    per_tool = (
        db.query(
            McpToolCall.tool_name,
            McpToolCall.outcome,
            func.count(McpToolCall.id),
        )
        .filter(McpToolCall.created_at >= since, McpToolCall.tool_name.isnot(None))
        .group_by(McpToolCall.tool_name, McpToolCall.outcome)
        .all()
    )
    tools: Dict[str, Dict[str, int]] = {}
    for name, outcome, count in per_tool:
        tools.setdefault(name, {})[outcome] = count

    failures = (
        db.query(
            McpToolCall.tool_name,
            McpToolCall.error_code,
            McpToolCall.detail,
            func.count(McpToolCall.id).label("count"),
            func.max(McpToolCall.created_at).label("last_seen"),
        )
        .filter(
            McpToolCall.created_at >= since,
            McpToolCall.outcome.in_([PROTOCOL_ERROR, TOOL_ERROR, REJECTED]),
        )
        .group_by(McpToolCall.tool_name, McpToolCall.error_code, McpToolCall.detail)
        .order_by(func.count(McpToolCall.id).desc())
        .limit(limit)
        .all()
    )

    clients = (
        db.query(
            McpToolCall.client_name,
            McpToolCall.client_version,
            McpToolCall.protocol_version,
            func.count(McpToolCall.id),
            func.max(McpToolCall.created_at),
        )
        .filter(McpToolCall.created_at >= since, McpToolCall.client_name.isnot(None))
        .group_by(
            McpToolCall.client_name,
            McpToolCall.client_version,
            McpToolCall.protocol_version,
        )
        .all()
    )

    return {
        "window_hours": hours,
        "total_requests": base.count(),
        "by_outcome": dict(
            db.query(McpToolCall.outcome, func.count(McpToolCall.id))
            .filter(McpToolCall.created_at >= since)
            .group_by(McpToolCall.outcome)
            .all()
        ),
        # Every registered tool appears, including ones with zero calls — "no
        # agent has ever used this tool" is a finding, and a view built only
        # from rows that exist can't show it.
        "tools": tools,
        "top_failures": [
            {
                "tool_name": name,
                "error_code": code,
                "detail": detail,
                "count": count,
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
            for name, code, detail, count, last_seen in failures
        ],
        "clients": [
            {
                "name": name,
                "version": version,
                "protocol_version": protocol,
                "requests": count,
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
            for name, version, protocol, count, last_seen in clients
        ],
    }
