"""Public reference endpoints — extracted from main.py in v2.42.0.

Unauthenticated by design (documentation / environment tooling, not sensitive
data — same stance as ``/agents-guide``), with one exception noted below:

  * ``GET  /api/v1/references/sbom``                — software bill of materials
  * ``GET  /api/v1/references/mcp-tools``           — MCP tool catalog, connect
    recipes, and this deployment's certificate info
  * ``GET  /api/v1/references/tls-certificate``     — deployment TLS cert (PEM)
  * ``GET  /api/v1/references/tools``               — the tool registry
  * ``PATCH /api/v1/references/tools/{name}``       — vet one (**admin only**)
  * ``GET  /api/v1/references/parser-coverage``     — what each import format
    keeps, where it is shown, and what is discarded
  * ``GET  /api/v1/references/``                    — listing of the above
  * ``GET  /api/v1/agents-guide``                   — agent guide slice

The agents-guide endpoint is colocated here because it's part of the
same "things-agents-curl-once" surface, not because of route prefix.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import identify_agent_if_present
from app.api.deps import get_current_user, require_role
from app.core.config import settings
from app.db.models_auth import User, UserRole
from app.db.session import get_db
from app.services.agents_guide_service import read_agent_guide, slice_agents_md
from app.services.agent_prompt_service import PROMPT_VERSION

logger = logging.getLogger(__name__)

# Stands in for a real key on the reference page, which has no session. The
# recipes are otherwise byte-identical to what a session emits.
SAMPLE_KEY_PLACEHOLDER = "<your-session-key>"  # noqa: S105 - not a credential

router = APIRouter()


@router.get("/references/sbom")
def sbom(current_user: User = Depends(get_current_user)):
    """Software bill of materials for the deployed app.

    Returns every package installed in the running backend venv plus every
    entry resolved by the frontend's ``package-lock.json``, each tagged
    with ``direct: bool`` so a user can tell the things we chose apart
    from the things our direct deps pulled in.

    Signed-in users only (v2.392.1).  It was public, like ``/agents-guide``,
    but unlike the guide it lists the EXACT version of every dependency —
    a ready-made CVE shopping list for anyone who can reach port 443
    (review 2026-09-23 R12).  The in-app page sends the user's token.
    Cached by manifest mtimes; the first call after a redeploy walks the
    installed packages, subsequent calls return the memoised result.

    Use case is operational vulnerability triage ("is package X in this
    build?"), NOT exploitability assessment — presence in the list
    confirms bundling, not reachability from app code.
    """
    from app.services.sbom_service import get_sbom
    return get_sbom(settings.APP_VERSION)


# The deployment's own certificate, mounted read-only (public half only — the
# private key is never mounted into this container).  Serving it lets an operator
# inspect it and check its fingerprint against what their client receives.
_TLS_CERT_PATH = Path("/certs/networkmapper.crt")


class TlsCertificateInfo(BaseModel):
    """What the deployment is actually presenting, for the connect instructions.

    v2.288.0 — the page used to assert that BlueStick's certificate is
    self-signed "and always will be".  That is the *default* this project ships,
    not an invariant: an operator can mount an internal-CA or a DNS-validated
    public certificate, and ``ssl-nginx.conf`` already contemplates one (its
    OCSP-stapling block).  Telling that operator to pin a certificate their
    clients already trust is busywork, and the fingerprint they were asked to
    compare was silently null for them — see ``fingerprint``.
    """

    # None when the certificate isn't mounted into the backend container.
    fingerprint_sha256: Optional[str] = None
    # True when the leaf is its own issuer (the bootstrap self-signed cert);
    # False means a CA issued it — normally the local root from ca/local-ca.sh.
    self_signed: Optional[bool] = None
    subject: Optional[str] = None
    expires_at: Optional[str] = None


def tls_certificate_info() -> TlsCertificateInfo:
    """Describe the mounted certificate: fingerprint, and whether it's self-signed.

    Published so an operator who downloaded the certificate over the untrusted
    connection has something to check it against.  Reading the fingerprint from
    the same connection proves nothing on its own — but it is displayed in the
    browser, where the operator can also inspect the certificate the padlock
    shows, and it detects the ordinary failure (fetched the wrong host) even
    when it cannot prove the extraordinary one.

    Parses the FIRST certificate in the file.  A CA-issued deployment usually
    mounts leaf-plus-intermediate in one PEM, and the previous implementation
    passed the whole file to ``ssl.PEM_cert_to_DER_cert``, which raises
    ``binascii.Error`` on a chain (reproduced) — swallowed by the except below
    into a silent ``None``.  So the operators most likely to have a *correct*
    certificate were the ones shown no fingerprint at all.
    """
    if not _TLS_CERT_PATH.is_file():
        return TlsCertificateInfo()
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        pem = _TLS_CERT_PATH.read_bytes()
        # load_pem_x509_certificate takes the leaf and ignores what follows,
        # which is what we want: the leaf is the certificate the client
        # actually validates and the one an operator compares.
        cert = x509.load_pem_x509_certificate(pem)
        digest = cert.fingerprint(hashes.SHA256()).hex().upper()
        return TlsCertificateInfo(
            fingerprint_sha256=":".join(
                digest[i : i + 2] for i in range(0, len(digest), 2)
            ),
            self_signed=cert.issuer == cert.subject,
            subject=cert.subject.rfc4514_string(),
            expires_at=cert.not_valid_after_utc.isoformat(),
        )
    except Exception:  # pragma: no cover - defensive; a bad cert must not 500
        logger.exception("could not read the deployment certificate")
        return TlsCertificateInfo()


@router.get("/references/tls-certificate", response_class=PlainTextResponse)
def tls_certificate():
    """Serve the deployment's public TLS certificate as PEM.

    Clients are meant to trust BlueStick through the organisation's local root
    CA (``ca/local-ca.sh``, installed once per analyst machine), not by pinning
    this leaf per client — the per-client installer (``scripts/trust-cert.sh``)
    is retired.  The leaf stays downloadable for inspection and so
    its fingerprint can be compared with what a client actually receives.

    This is the certificate the server already presents in every TLS handshake,
    so publishing it discloses nothing new.  Fetching it over the same untrusted
    connection is trust-on-first-use, with TOFU's usual caveat: on a network you
    don't trust, copy the file off the deployment host instead.
    """
    if not _TLS_CERT_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No certificate is mounted in this container. Deployments that "
                "terminate TLS elsewhere (a reverse proxy, a load balancer) "
                "should distribute that endpoint's certificate instead."
            ),
        )
    return PlainTextResponse(
        _TLS_CERT_PATH.read_text(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="bluestick.pem"'},
    )


@router.get("/references/tools")
def tool_registry(
    status: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    # Public endpoint; the dependency only stamps attribution when an agent key
    # is presented, so `list_tools` lands in the session's activity log
    # instead of vanishing (v2.312.0).
    _agent=Depends(identify_agent_if_present),
):
    """The tool registry — every tool BlueStick knows about (v2.277.0).

    A catalogue for people and agents alike — install, usage, ports, whether
    BlueStick parses its output (``ingestible``).  ``status`` says whether a
    row is catalogued (``reference``), an agent's suggestion awaiting a
    curator, or a declined suggestion; since v2.433.0 none of them is a
    permission — the operator decides what their agent runs.
    """
    from app.services import tool_registry_service

    tools = tool_registry_service.list_tools(db, status=status, category=category)
    return {
        "count": len(tools),
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "ports": t.ports,
                "install": t.install,
                "url": t.url,
                "kali": t.kali,
                "status": t.status,
                "phases": t.phases or [],
                "intrusive": t.intrusive,
                "requires_privileges": t.requires_privileges,
                "output_format": t.output_format,
                "ingestible": t.ingestible,
                "suggested_rationale": t.suggested_rationale,
            }
            for t in tools
        ],
    }


@router.get("/references/parser-coverage")
def parser_coverage():
    """What BlueStick reads from each tool's output, and where it ends up
    (v2.411.0) — the "What BlueStick reads" page.

    Documentation, public like the tool registry: for every import format,
    each thing the tool reports, how far BlueStick takes it (scanner
    observation / field / raw text / stored, not shown / discarded), where an
    analyst sees it, and the known gaps.
    """
    from app.services.parser_coverage import coverage_payload

    return coverage_payload()


class ToolRegistryUpdate(BaseModel):
    """What an admin may change when vetting a tool.

    ``ingestible`` is deliberately absent: it records whether BlueStick has a
    parser for the tool's output, which is a fact about this codebase, not a
    decision an operator gets to make.  Editing it here would let the UI claim
    an upload will work when nothing can read the file.
    """

    status: Optional[str] = Field(
        None,
        description="reference (add to the catalogue) / rejected (decline a suggestion).",
    )
    description: Optional[str] = Field(None, max_length=4000)
    category: Optional[str] = Field(None, max_length=100)
    ports: Optional[str] = Field(None, max_length=200)
    install: Optional[str] = Field(None, max_length=500)
    url: Optional[str] = Field(None, max_length=500)
    kali: Optional[bool] = None


@router.patch("/references/tools/{name}")
def update_tool_registry_entry(
    name: str,
    body: ToolRegistryUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Curate a tool — the other half of ``suggest_tool`` (v2.280.0).

    Taking a suggestion into the catalogue is a status change on the same row,
    which is why suggestions are stored as rows rather than as notes in a
    separate store.  It usually means writing real prose too — a suggested
    row's description is the agent's rationale — so the human-facing fields
    are editable in the same call.

    Admin-only, and global rather than project-scoped: the catalogue is one
    deployment-wide list.  No status grants or withholds anything (v2.433.0).
    """
    from app.db.models_tools import TOOL_REFERENCE, TOOL_REJECTED, ToolRegistryEntry

    entry = (
        db.query(ToolRegistryEntry).filter(ToolRegistryEntry.name == name).one_or_none()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No registered tool named {name!r}")

    fields = body.model_dump(exclude_unset=True)
    status = fields.pop("status", None)
    if status is not None:
        allowed = {TOOL_REFERENCE, TOOL_REJECTED}
        if status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"status must be one of {sorted(allowed)} — 'suggested' is what an "
                    "agent's ask produces, not a decision you can set."
                ),
            )
        entry.status = status
    for field, value in fields.items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return {
        "name": entry.name,
        "status": entry.status,
        "description": entry.description,
        "category": entry.category,
        "ports": entry.ports,
        "install": entry.install,
        "url": entry.url,
        "kali": entry.kali,
    }


@router.get("/references/mcp-tools")
def mcp_tools(request: Request):
    """The MCP tool catalog, for the in-app MCP reference page.

    Read straight off the live ``_TOOLS`` registry in ``mcp_assist`` — the page
    describes what this deployment actually serves rather than a hand-copied
    list that goes stale the first time a tool is added.  Unauthenticated like
    the rest of this router: the same catalog is already available to anyone
    who can POST ``tools/list`` to /api/v1/mcp without a key.
    """
    from app.api.v1.endpoints.mcp_assist import tool_catalog
    from app.services.agent_prompt_service import resolve_base_url
    from app.services.mcp_client_setup_service import build_mcp_clients

    base_url = resolve_base_url(request)
    catalog = tool_catalog(f"{base_url}/mcp")
    # Connecting is the other half of what this page is for.  The fingerprint
    # rides along here rather than in a second fetch: a fingerprint the
    # operator has to go and find is a fingerprint nobody checks.
    # The connect recipes come from the SAME builder the session dialogs use,
    # with a placeholder in place of a key (v2.289.0).  The page used to carry
    # its own copy in TypeScript, and that pair drifted twice: once on the
    # config wrapper key (the bug the shared builder was created to fix) and
    # once on the Codex TLS note, which had to be corrected in both languages by
    # hand.  Rendering what the server would actually emit removes the second
    # copy rather than re-syncing it.
    catalog["sample_clients"] = build_mcp_clients(
        catalog["endpoint"], SAMPLE_KEY_PLACEHOLDER
    )
    catalog["sample_key_placeholder"] = SAMPLE_KEY_PLACEHOLDER
    catalog["tls_certificate_url"] = f"{base_url}/references/tls-certificate"
    info = tls_certificate_info()
    catalog["tls_fingerprint_sha256"] = info.fingerprint_sha256
    # What the deployment actually presents, so the page can stop asserting
    # "self-signed, always" at an operator who mounted a CA-issued certificate.
    catalog["tls_certificate"] = info.model_dump()
    return catalog


@router.get("/references/")
async def references_index():
    """List available reference assets served under /api/v1/references/."""
    return {
        "agents_guide": {
            "url": "/api/v1/agents-guide",
            "description": (
                "The full agent guide; supports "
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
        "tls_certificate": {
            "url": "/api/v1/references/tls-certificate",
            "description": (
                "The deployment's public TLS certificate (PEM), for inspection "
                "and fingerprint checks. Clients trust BlueStick through the "
                "organisation's local root CA (ca/local-ca.sh trust-help), "
                "installed once per machine — not by pinning this certificate."
            ),
        },
        "tools": {
            "url": "/api/v1/references/tools",
            "description": (
                "The tool catalogue — every tool BlueStick knows about, with "
                "install/usage knowledge, phases, intrusiveness and whether "
                "BlueStick parses its output. Filter with "
                "?status=reference|suggested|rejected."
            ),
        },
        "parser_coverage": {
            "url": "/api/v1/references/parser-coverage",
            "description": (
                "For every import format: what the tool reports, how far "
                "BlueStick takes it (scanner observation, field, raw text, "
                "stored but not shown, discarded), where it is shown, and "
                "the known gaps."
            ),
        },
        "mcp_tools": {
            "url": "/api/v1/references/mcp-tools",
            "description": (
                "The MCP tool catalog this deployment serves — every tool "
                "name, its input schema, whether it reads or writes, and the "
                "workflows that see it. Drives the in-app MCP reference page."
            ),
        },
    }


@router.get("/agents-guide")
async def agents_guide(
    request: Request,
    workflow: Optional[str] = None,
    # See tool_registry above — attribution only, never a requirement.
    _agent=Depends(identify_agent_if_present),
):
    """Serve the agent guide (documentation/AGENT_GUIDE.md) with the base URL replaced to match the current deployment.

    Accepts an optional phase query parameter (``plan_generation``,
    ``execution``, ``reconnaissance``, ``assist``, or the short forms
    ``plan``/``exec``/``recon``).  When present, the response is filtered
    to only the sections tagged for that workflow plus any ``shared``
    sections.  The execution slice is roughly a third of the full file;
    the plan_generation / reconnaissance slices are similarly trimmed.
    See ``services.agents_guide_service.slice_agents_md`` for filter
    semantics. Unified project sessions receive the full guide.
    """
    content = read_agent_guide()
    if content is None:
        raise HTTPException(status_code=404, detail="Agent guide not found")

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
        headers={"Content-Disposition": 'attachment; filename="bluestick-agent-guide.md"'},
    )
