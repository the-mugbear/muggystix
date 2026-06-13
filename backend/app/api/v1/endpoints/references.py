"""Public reference endpoints — extracted from main.py in v2.42.0.

Three surfaces, all unauthenticated by design (documentation /
environment tooling, not sensitive data — same stance as ``/agents-guide``):

  * ``GET /api/v1/references/preflight-script`` — bash preflight script
  * ``GET /api/v1/references/sbom``             — software bill of materials
  * ``GET /api/v1/references/``                 — listing of references above
  * ``GET /api/v1/agents-guide``                — AGENTS.md slice

The agents-guide endpoint is colocated here because it's part of the
same "things-agents-curl-once" surface, not because of route prefix.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.models_auth import User
from app.db.session import get_db
from app.services.agents_guide_service import slice_agents_md
from app.services.agent_prompt_history import PROMPT_VERSION

router = APIRouter()


@router.get("/references/preflight-script", include_in_schema=True)
async def preflight_script():
    """Serve scripts/preflight.sh as text/x-shellscript.

    The script queries the local host for recon-workflow tools and prints
    installation guidance pointing only at official upstream sources
    (project repos, vendor pages, distro packages).  Agents can invoke it
    directly in bash-capable environments::

        curl -sk https://<nm-host>/api/v1/references/preflight-script | bash --
        curl -sk https://<nm-host>/api/v1/references/preflight-script | bash -s -- --json

    PowerShell-only environments fetch + inspect + emit the equivalent
    `tools_status` payload by hand — see AGENTS.md § Environment preflight.
    """
    candidates = [
        Path("/app/scripts/preflight.sh"),
        Path(__file__).resolve().parents[4] / "scripts" / "preflight.sh",
    ]
    for p in candidates:
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            return PlainTextResponse(
                content,
                media_type="text/x-shellscript; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="preflight.sh"',
                },
            )
    raise HTTPException(status_code=404, detail="preflight.sh not found in deployment")


@router.get("/references/sbom")
def sbom():
    """Software bill of materials for the deployed app.

    Returns every package installed in the running backend venv plus every
    entry resolved by the frontend's ``package-lock.json``, each tagged
    with ``direct: bool`` so a user can tell the things we chose apart
    from the things our direct deps pulled in.

    Public surface (no auth), same stance as ``/agents-guide`` and
    ``/preflight-script``: this is documentation, not sensitive data.
    Cached by manifest mtimes; the first call after a redeploy walks the
    installed packages, subsequent calls return the memoised result.

    Use case is operational vulnerability triage ("is package X in this
    build?"), NOT exploitability assessment — presence in the list
    confirms bundling, not reachability from app code.
    """
    from app.services.sbom_service import get_sbom
    return get_sbom(settings.APP_VERSION)


@router.get("/references/tool-readiness")
def tool_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Host readiness — the agent tool catalog cross-referenced against
    the calling user's most recent environment probe.

    Unlike the other ``/references`` endpoints this one is **authenticated**:
    it reflects *your* host's tool inventory, taken from the environment
    probe of your most recent execution or recon session (whichever
    probed last).  Each catalog tool comes back as ``installed`` /
    ``missing`` / ``warn`` / ``unknown`` — ``unknown`` meaning the probe
    never reported on it.  When you have never run an agent workflow,
    ``has_probe`` is false and every tool is ``unknown``.

    The response carries per-tool ``install_hints`` and the probe's
    ``preferred_provider`` so the UI can generate install guidance for
    the tools still missing from this host.
    """
    from app.db.models_agent import ExecutionSession, ReconSession
    from app.services.recon_planning_service import build_tool_readiness

    # The probe lives per-session; pull the caller's most recent one
    # across both workflow types.  One row per model, newest first.
    candidates = []
    for model in (ExecutionSession, ReconSession):
        row = (
            db.query(model.environment, model.environment_probed_at)
            .filter(
                model.environment_probed_by_user_id == current_user.id,
                model.environment.isnot(None),
            )
            .order_by(model.environment_probed_at.desc())
            .first()
        )
        if row and row[0] is not None:
            candidates.append((row[1], row[0]))  # (probed_at, environment)

    timed = [c for c in candidates if c[0] is not None]
    if timed:
        probed_at, probe = max(timed, key=lambda c: c[0])
    elif candidates:
        probed_at, probe = candidates[0]
    else:
        probed_at, probe = None, None

    return build_tool_readiness(probe, probed_at=probed_at)


@router.get("/references/")
async def references_index():
    """List available reference assets served under /api/v1/references/."""
    return {
        "preflight_script": {
            "url": "/api/v1/references/preflight-script",
            "description": (
                "Bash script that queries the local host for recon-workflow "
                "tools and prints installation guidance.  All install URLs "
                "point only at official upstream sources."
            ),
            "flags": {
                "--json": "machine-readable output for agents",
                "--strict": "exit 1 if any essential tool is missing",
                "--help": "show script-level help",
            },
            "usage": [
                "curl -sk <base>/api/v1/references/preflight-script | bash --",
                "curl -sk <base>/api/v1/references/preflight-script | bash -s -- --json",
            ],
        },
        "agents_guide": {
            "url": "/api/v1/agents-guide",
            "description": (
                "Full AGENTS.md reference; supports "
                "?workflow=plan_generation|execution|reconnaissance|assist"
            ),
        },
        "sbom": {
            "url": "/api/v1/references/sbom",
            "description": (
                "Software bill of materials — every backend Python and "
                "frontend npm component bundled with this build, tagged "
                "direct vs transitive.  For operational CVE triage."
            ),
        },
        "tool_readiness": {
            "url": "/api/v1/references/tool-readiness",
            "description": (
                "Host readiness — the agent tool catalog cross-referenced "
                "against your most recent environment probe (installed / "
                "missing / warn / unknown), with install hints.  "
                "Authenticated: reflects the calling user's own host."
            ),
        },
    }


@router.get("/agents-guide")
async def agents_guide(request: Request, workflow: Optional[str] = None):
    """Serve AGENTS.md with the base URL replaced to match the current deployment.

    Accepts an optional ``workflow`` query parameter (``plan_generation``,
    ``execution``, ``reconnaissance``, or the short forms
    ``plan``/``exec``/``recon``).  When present, the response is filtered
    to only the sections tagged for that workflow plus any ``shared``
    sections.  The execution slice is roughly a third of the full file;
    the plan_generation / reconnaissance slices are similarly trimmed.
    See ``services.agents_guide_service.slice_agents_md`` for filter
    semantics.
    """
    candidates = [
        Path("/app/AGENTS.md"),
        Path(__file__).resolve().parents[4] / "AGENTS.md",
    ]
    content = None
    for p in candidates:
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            break
    if content is None:
        raise HTTPException(status_code=404, detail="AGENTS.md not found")

    content = slice_agents_md(content, workflow)

    # Stamp the served guide with the LIVE prompt version (the same
    # PROMPT_VERSION the agent's prompt embeds).  The static file carries a
    # hand-written value; overwriting it with ground truth guarantees the
    # guide and the prompt always report the same contract version, so an
    # agent can tell "guide vs prompt compatible?" by string equality
    # instead of comparing two unrelated numbers (the backend platform
    # version is only a freshness stamp).  See feedback #8 (recon, 1.35.0).
    content = re.sub(
        r"(\*\*Prompt version:\*\*\s*)\S+",
        lambda m: f"{m.group(1)}{PROMPT_VERSION}",
        content,
        count=1,
    )

    # Substitute the default localhost base URL with the actual origin so
    # the agent's curl examples target this deployment instead of the
    # placeholder.
    origin = f"{request.url.scheme}://{request.headers.get('host', 'localhost:3000')}"
    content = content.replace("https://localhost:3000", origin)
    content = content.replace("https://127.0.0.1:3000", origin)

    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="AGENTS.md"'},
    )
