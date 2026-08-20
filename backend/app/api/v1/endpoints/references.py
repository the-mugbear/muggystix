"""Public reference endpoints — extracted from main.py in v2.42.0.

Three surfaces, all unauthenticated by design (documentation /
environment tooling, not sensitive data — same stance as ``/agents-guide``):

  * ``GET /api/v1/references/preflight-script`` — bash preflight script
  * ``GET /api/v1/references/sbom``             — software bill of materials
  * ``GET /api/v1/references/mcp-tools``        — MCP tool catalog
  * ``GET /api/v1/references/tls-certificate``  — deployment TLS cert (PEM)
  * ``GET /api/v1/references/``                 — listing of references above
  * ``GET /api/v1/agents-guide``                — AGENTS.md slice

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

from app.api.v1.endpoints.auth import get_current_user, require_role
from app.core.config import settings
from app.db.models_auth import User, UserRole
from app.db.session import get_db
from app.services.agents_guide_service import slice_agents_md
from app.services.agent_prompt_history import PROMPT_VERSION

logger = logging.getLogger(__name__)

# Stands in for a real key on the reference page, which has no session. The
# recipes are otherwise byte-identical to what a session emits.
SAMPLE_KEY_PLACEHOLDER = "<your-session-key>"  # noqa: S105 - not a credential

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


@router.get("/references/trust-cert-script", include_in_schema=True)
async def trust_cert_script():
    """Serve scripts/trust-cert.sh as text/x-shellscript (v2.286.0).

    Pinning this deployment's certificate takes a different variable per client
    (``NODE_EXTRA_CA_CERTS`` for the Node-based ones, ``SSL_CERT_DIR`` — a
    directory of hash-named symlinks — for Codex), and the operator running the
    client usually has no reason to have this repository checked out.  Serving
    the script from the deployment is the difference between "follow these six
    steps" and "run this once"::

        curl -sk https://<host>/api/v1/references/trust-cert-script -o trust-cert.sh
        less trust-cert.sh
        bash trust-cert.sh --url https://<host>

    Deliberately NOT advertised as ``curl … | bash``, unlike the preflight
    script: this one installs a trust anchor, and piping an unverified download
    straight into a shell is exactly the habit that makes trust-on-first-use
    dangerous.  The script prints the certificate's SHA-256 so it can be
    compared against the fingerprint the reference page shows.
    """
    candidates = [
        Path("/app/scripts/trust-cert.sh"),
        Path(__file__).resolve().parents[4] / "scripts" / "trust-cert.sh",
    ]
    for p in candidates:
        if p.is_file():
            return PlainTextResponse(
                p.read_text(encoding="utf-8"),
                media_type="text/x-shellscript; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="trust-cert.sh"',
                },
            )
    raise HTTPException(status_code=404, detail="trust-cert.sh not found in deployment")


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


# The deployment's own certificate, mounted read-only (public half only — the
# private key is never mounted into this container).  Serving it lets an operator
# pin it via NODE_EXTRA_CA_CERTS instead of switching TLS verification off.
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
    # True when the leaf is its own issuer.  False means a CA issued it, in
    # which case pinning may be unnecessary — the page softens rather than
    # skips, because "a CA issued it" does not prove the CLIENT trusts that CA
    # (an internal CA is exactly the case where it might not).
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
        # actually validates and the one an operator would pin.
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

    Deployments default to a self-signed certificate, and the Node-based MCP
    clients (VS Code, Claude Code) ignore the OS trust store, so "trust it in
    Keychain" doesn't help.  What does work is ``NODE_EXTRA_CA_CERTS=<this
    file>``, which trusts THIS certificate and nothing else, leaving
    verification on.  The alternative operators reach for,
    ``NODE_TLS_REJECT_UNAUTHORIZED=0``, disables verification for every host
    that process talks to.

    **The variable differs per client.**  Codex is a Rust binary built against
    native-tls; it reads ``SSL_CERT_DIR`` (a directory of hash-named symlinks),
    not ``NODE_EXTRA_CA_CERTS``, and not ``SSL_CERT_FILE`` either — all three
    verified against codex 0.147.0.  ``scripts/trust-cert.sh`` installs this
    certificate in both shapes and prints the exports; both are read at process
    start, so the client has to be restarted afterwards.

    v2.282.0 claimed Codex could not pin at all and needed a CA-signed
    certificate.  That was wrong, and it was also a dead end: an application
    that only ever listens on a private address cannot obtain one.

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
):
    """The tool registry — every tool BlueStick knows about (v2.277.0).

    One source for two views: the reference page renders all of it as a human
    knowledge repo, and the agent catalogue is the ``approved`` subset.  Before
    this, those were separate lists in separate languages that had already
    drifted — ``testssl`` was agent-usable with no human entry.

    ``status`` is a policy fact (may an agent run it) and ``ingestible`` an
    engineering one (does a parser exist for its output); they are independent,
    and a tool can be entirely safe to run without BlueStick parsing a word of
    its output.
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


class ToolRegistryUpdate(BaseModel):
    """What an admin may change when vetting a tool.

    ``ingestible`` is deliberately absent: it records whether BlueStick has a
    parser for the tool's output, which is a fact about this codebase, not a
    decision an operator gets to make.  Editing it here would let the UI claim
    an upload will work when nothing can read the file.
    """

    status: Optional[str] = Field(
        None,
        description="approved (agents may run it) / reference / rejected.",
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
    """Vet a tool — the other half of ``suggest_tool`` (v2.280.0).

    An agent can record "I needed a tool you don't approve" since 2.278.0, and
    until now nothing could act on it: the suggestions accumulated in a table
    with no way to approve one short of a SQL prompt.  Vetting is a status
    change on the same row, which is why suggestions were stored as rows rather
    than as notes in a separate store.

    Approving usually means writing real prose too — a suggested row's
    description is the agent's rationale, which reads badly as documentation on
    a page humans use to learn about tools — so the human-facing fields are
    editable in the same call.

    Admin-only, and global rather than project-scoped: the registry is one
    deployment-wide list, so approving a tool in one project approves it
    everywhere, which is exactly what an operator vetting it intends.
    """
    from app.db.models_tools import (
        TOOL_APPROVED,
        TOOL_REFERENCE,
        TOOL_REJECTED,
        ToolRegistryEntry,
    )

    entry = (
        db.query(ToolRegistryEntry).filter(ToolRegistryEntry.name == name).one_or_none()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No registered tool named {name!r}")

    fields = body.model_dump(exclude_unset=True)
    status = fields.pop("status", None)
    if status is not None:
        allowed = {TOOL_APPROVED, TOOL_REFERENCE, TOOL_REJECTED}
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
    # Connecting is the other half of what this page is for, and every client
    # fails at the certificate first (v2.286.0).  The script URL and the
    # fingerprint ride along here rather than in a second fetch: the page needs
    # both exactly when it needs the tool list, and a fingerprint the operator
    # has to go and find is a fingerprint nobody checks.
    # The connect recipes come from the SAME builder the session dialogs use,
    # with a placeholder in place of a key (v2.289.0).  The page used to carry
    # its own copy in TypeScript, and that pair drifted twice: once on the
    # config wrapper key (the bug the shared builder was created to fix) and
    # once on the Codex TLS note, which had to be corrected in both languages by
    # hand.  Rendering what the server would actually emit removes the second
    # copy rather than re-syncing it.
    catalog["sample_clients"] = build_mcp_clients(
        catalog["endpoint"], SAMPLE_KEY_PLACEHOLDER, workflow="assist"
    )
    catalog["sample_key_placeholder"] = SAMPLE_KEY_PLACEHOLDER
    catalog["trust_script_url"] = f"{base_url}/references/trust-cert-script"
    catalog["tls_certificate_url"] = f"{base_url}/references/tls-certificate"
    info = tls_certificate_info()
    catalog["tls_fingerprint_sha256"] = info.fingerprint_sha256
    # What the deployment actually presents, so the page can stop asserting
    # "self-signed, always" at an operator who mounted a CA-issued certificate.
    catalog["tls_certificate"] = info.model_dump()
    return catalog


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
        "trust_cert_script": {
            "url": "/api/v1/references/trust-cert-script",
            "description": (
                "Shell script that installs this deployment's certificate in "
                "both shapes MCP clients need (NODE_EXTRA_CA_CERTS for VS Code "
                "/ Claude Code, SSL_CERT_DIR for Codex) and prints the exports. "
                "Download and read it before running — it installs a trust "
                "anchor: `bash trust-cert.sh --url https://<host>`."
            ),
        },
        "tls_certificate": {
            "url": "/api/v1/references/tls-certificate",
            "description": (
                "The deployment's public TLS certificate (PEM). Pin it so MCP "
                "clients trust this deployment without disabling certificate "
                "verification: NODE_EXTRA_CA_CERTS for the Node-based clients "
                "(VS Code, Claude Code), SSL_CERT_DIR for Codex. "
                "scripts/trust-cert.sh sets up both."
            ),
        },
        "tools": {
            "url": "/api/v1/references/tools",
            "description": (
                "The tool registry — every tool BlueStick knows about, with "
                "install/usage knowledge for humans and, for the approved "
                "subset, the phase/intrusiveness metadata agents key off. "
                "Filter with ?status=approved|reference|suggested."
            ),
        },
        "mcp_tools": {
            "url": "/api/v1/references/mcp-tools",
            "description": (
                "The MCP tool catalog this deployment serves — every tool "
                "name, its input schema, and the write capability it needs. "
                "Drives the in-app MCP reference page."
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
