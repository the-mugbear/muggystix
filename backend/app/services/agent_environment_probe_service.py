"""Shared env-probe write path for execution + recon sessions.

Extracted in v2.43.3 (audit AUD-C1 + AUD-O3).  Both
``agent_execution.record_execution_environment`` and
``agent_recon.record_recon_environment`` previously inlined the same
security-sensitive sequence:

  * Pull agent attribution off the request body (``agent_model``,
    ``agent_tool``, ``agent_prompt_version``).
  * Persist the rest as the JSON ``environment`` blob.
  * Stamp the audit trio (``environment_probed_at``,
    ``environment_probed_by_user_id``, ``environment_probed_from_ip``).
  * Stamp attribution columns iff the value is non-null (never
    overwrite a prior probe's value with NULL on a re-probe).

The duplicated logic also missed an audit-integrity guard: neither
endpoint refused writes once the session had moved to a terminal
status (``completed`` / ``abandoned`` / etc.), so a still-valid scoped
key could rewrite historical session metadata after the session was
"done".  This module enforces the status guard once for both surfaces.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def apply_environment_probe(
    *,
    session: Any,
    body: Any,
    request: Request,
    agent: Any,
    active_statuses: Iterable[str],
    session_kind: str,
) -> Optional[str]:
    """Apply an env-probe payload to ``session``.

    * Raises HTTP 409 if the session has already left ``active_statuses``
      (audit-integrity guard — historical records must stay immutable).
    * Writes ``environment`` (the JSON blob), the three probe-audit
      columns, and the three attribution columns (non-NULL preserve
      semantics on re-probe).
    * Returns the prior status (for logging) so the caller can attribute
      the write in any extra audit lines it emits.
    """
    current_status = getattr(session, "status", None)
    if current_status not in set(active_statuses):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot record an environment probe on a {session_kind} "
                f"session in state '{current_status}'.  Probes are only "
                "accepted while the session is active or paused; finalized "
                "sessions are part of the audit trail and stay immutable. "
                "Open a new session if you need a fresh probe."
            ),
        )

    payload = body.model_dump()
    agent_model = payload.pop("agent_model", None)
    agent_tool = payload.pop("agent_tool", None)
    agent_prompt_version = payload.pop("agent_prompt_version", None)

    session.environment = payload
    session.environment_probed_at = datetime.now(timezone.utc)
    session.environment_probed_by_user_id = getattr(agent, "owner_id", None)
    session.environment_probed_from_ip = (
        request.client.host if request.client else None
    )

    # Attribution: never overwrite a prior non-null value with None.
    if agent_model:
        session.generated_by_model = agent_model
    if agent_tool:
        session.generated_by_tool = agent_tool
    if agent_prompt_version:
        session.prompt_version = agent_prompt_version

    return current_status
