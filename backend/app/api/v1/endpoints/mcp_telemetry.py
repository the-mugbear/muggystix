"""Admin read surface for MCP transport telemetry (v2.275.0).

Deployment-level diagnostics, not project data — an MCP request is refused (or
served) before anything project-scoped is known, and the rows deliberately carry
no project FK.  So this is admin-only and global, rather than living under
``/projects/{id}/`` next to the agent-activity log.

The shape is built around the questions rather than the table: which tools are
being used, which are failing and how, and which clients are connecting.  A raw
row feed would answer none of those without the reader doing the aggregation by
hand.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_role
from app.api.v1.endpoints.mcp_assist import _TOOLS
from app.db.models_auth import UserRole
from app.db.session import get_db
from app.services import mcp_telemetry_service as mcp_telemetry

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get(
    "/summary",
    summary="MCP transport telemetry — tool usage, failures, and clients",
)
def mcp_telemetry_summary(
    hours: int = Query(24, ge=1, le=24 * 30, description="Look-back window."),
    limit: int = Query(20, ge=1, le=100, description="Max distinct failure modes."),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Aggregate MCP activity for the window.

    Includes **every registered tool**, not just the ones with rows: "no agent
    has ever called this tool" is a finding, and a view built only from recorded
    calls cannot show it. Tools with no activity come back with zero counts.
    """
    summary = mcp_telemetry.summarise(db, hours=hours, limit=limit)

    observed = summary["tools"]
    summary["tools"] = {
        name: {
            "ok": observed.get(name, {}).get(mcp_telemetry.OK, 0),
            "tool_error": observed.get(name, {}).get(mcp_telemetry.TOOL_ERROR, 0),
            "protocol_error": observed.get(name, {}).get(mcp_telemetry.PROTOCOL_ERROR, 0),
            "kind": "read" if spec["method"] == "GET" else "write",
        }
        for name, spec in _TOOLS.items()
    }
    # Calls naming a tool that doesn't exist are the most interesting rows in the
    # table — a client working from a stale or hallucinated tool list — so they
    # are surfaced rather than dropped for not matching the registry.
    summary["unknown_tools_called"] = {
        name: counts for name, counts in observed.items() if name not in _TOOLS
    }
    return summary
