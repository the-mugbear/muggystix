"""Contract tests that keep the docs tied to the code.

Two doc surfaces drifted badly before these existed:

  * AGENTS.md (the agent contract) ran ~120 backend releases stale — wrong
    pagination limits, a filter that didn't exist, an endpoint shape that
    no longer matched.
  * The OpenAPI description in ``app/main.py`` carried a ``risk`` tag whose
    router had been deleted, and was missing the ``agent-assist`` tag.

Nothing failed when those drifted, so they accumulated silently. These
tests make the next drift fail CI instead:

  1. AGENTS.md section markers stay balanced and every workflow slice keeps
     its workflow-specific body (the slicer is only as good as the markers).
  2. Every hand-described OpenAPI tag is actually used by a route, and the
     agent-workflow tags are all described.
  3. Every agent endpoint documented in AGENTS.md's API-reference tables
     exists as a real route.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.main import app, _OPENAPI_TAGS
from app.services.agents_guide_service import slice_agents_md

WORKFLOWS = ["plan_generation", "execution", "reconnaissance", "assist"]

# A substring each workflow slice MUST contain — its workflow-specific
# heading — proving the slice kept its own body, not just shared preamble.
# ASCII-only on purpose (no em-dash) so the assertion can't fail on encoding.
WORKFLOW_ANCHORS = {
    "plan_generation": "Build a Test Plan (from",
    "execution": "Execute an Approved Plan (from",
    "reconnaissance": "Populate Host Data via Reconnaissance",
    "assist": "Assist workflow (read-only",
}
# A shared-section heading that must survive into EVERY slice.
SHARED_ANCHOR = "Instance Identity (verify once"

# The canonical tags a section may route to (slicer matches these literally).
KNOWN_SECTION_TAGS = {"shared", "plan_generation", "execution", "reconnaissance", "assist"}


def _load_agents_md() -> str:
    candidates = [
        Path(__file__).resolve().parents[1] / "AGENTS.md",  # /app/AGENTS.md (container)
        Path(__file__).resolve().parents[2] / "AGENTS.md",  # repo root (local checkout)
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    pytest.skip("AGENTS.md not mounted in this environment")


# ---------------------------------------------------------------------------
# 1. AGENTS.md slicer integrity
# ---------------------------------------------------------------------------

_SECTION = re.compile(r'<!--\s*agents:section\s+tags\s*=\s*"([^"]+)"\s*-->', re.I)
_END = re.compile(r'<!--\s*agents:end\s*-->', re.I)


def test_section_markers_balanced_and_non_nested():
    """Every ``agents:section`` has a matching ``agents:end`` and they don't
    nest (the slicer doesn't support nesting — a stray start/end silently
    mis-routes whole chunks of the guide)."""
    text = _load_agents_md()
    depth = 0
    for i, line in enumerate(text.split("\n"), 1):
        if _SECTION.search(line):
            depth += 1
            assert depth == 1, f"line {i}: nested agents:section (the slicer can't nest)"
        if _END.search(line):
            depth -= 1
            assert depth == 0, f"line {i}: agents:end with no open agents:section"
    assert depth == 0, "unterminated agents:section — missing agents:end before EOF"


def test_every_section_tag_is_recognised():
    """A section tagged with a name the slicer doesn't know (e.g. the short
    form ``recon`` instead of ``reconnaissance``) routes to no slice and the
    content silently vanishes from every guide."""
    text = _load_agents_md()
    for i, line in enumerate(text.split("\n"), 1):
        m = _SECTION.search(line)
        if not m:
            continue
        tags = {t.strip().lower() for t in m.group(1).split(",") if t.strip()}
        unknown = tags - KNOWN_SECTION_TAGS
        assert not unknown, f"line {i}: section tag(s) {sorted(unknown)} match no workflow slice"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_workflow_slice_is_nonempty_with_anchors(wf):
    full = _load_agents_md()
    sliced = slice_agents_md(full, wf)
    assert sliced.strip(), f"{wf}: slice is empty"
    assert SHARED_ANCHOR in sliced, f"{wf}: slice dropped the shared '{SHARED_ANCHOR}' section"
    assert WORKFLOW_ANCHORS[wf] in sliced, f"{wf}: slice missing its anchor '{WORKFLOW_ANCHORS[wf]}'"
    assert len(sliced) < len(full), f"{wf}: slice is not smaller than the full file — filtering didn't run"


def test_unknown_workflow_returns_shared_only():
    """An unrecognised workflow returns shared + untagged content, never a
    workflow-specific body (the documented safe default)."""
    full = _load_agents_md()
    sliced = slice_agents_md(full, "bogus_workflow_xyz")
    assert SHARED_ANCHOR in sliced
    assert WORKFLOW_ANCHORS["plan_generation"] not in sliced
    assert WORKFLOW_ANCHORS["reconnaissance"] not in sliced


def test_slice_with_no_workflow_returns_full_file():
    full = _load_agents_md()
    assert slice_agents_md(full, None) == full


# ---------------------------------------------------------------------------
# 2. OpenAPI tag coherence
# ---------------------------------------------------------------------------

def _route_tags() -> set[str]:
    used: set[str] = set()
    for r in app.routes:
        if isinstance(r, APIRoute):
            for t in (r.tags or []):
                used.add(str(t))
    return used


def test_no_described_openapi_tag_is_orphaned():
    """Every tag with a hand-written description in ``_OPENAPI_TAGS`` is used
    by at least one route. Catches a deleted router whose tag description
    lingers (the ``risk`` drift). Note we do NOT require the reverse — many
    routers intentionally use tags without a curated description."""
    declared = {t["name"] for t in _OPENAPI_TAGS}
    orphaned = declared - _route_tags()
    assert not orphaned, f"OpenAPI tags described but used by no route (dead): {sorted(orphaned)}"


def test_agent_workflow_tags_are_described():
    """The agent surface is a public contract — each workflow's tag must
    carry a description so it isn't an unlabelled group in Swagger/Redoc.
    Catches the missing ``agent-assist`` drift."""
    declared = {t["name"] for t in _OPENAPI_TAGS}
    required = {
        "agent-browse",
        "agent-plan-generation",
        "agent-execution",
        "agent-recon",
        "agent-assist",
        "agent-feedback",
    }
    missing = required - declared
    assert not missing, f"agent workflow tags missing an OpenAPI description: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 3. AGENTS.md documented agent endpoints exist as routes
# ---------------------------------------------------------------------------

_METHOD = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE)\b")
# First backtick-wrapped token on the row that contains an /agent/ path.
_AGENT_PATH = re.compile(r"`[^`]*?(/agent/[A-Za-z0-9_\-/{}*]+)")


def _norm(path: str) -> str:
    path = path.split("?")[0].rstrip("/")
    return re.sub(r"\{[^}]+\}", "{}", path)


def _documented_agent_endpoints(text: str) -> set[tuple[str, str]]:
    """(METHOD, normalised path) pairs taken from API-reference TABLE rows
    only — a line that starts with ``|``, names an HTTP method, and has a
    backtick ``/agent/...`` path. Prose mentions and wildcard (``/agent/*``)
    rows are ignored. Only the FIRST ``/agent`` path per row (the endpoint
    column) is taken, so paths mentioned in a description cell don't get
    mis-attributed to the row's method."""
    out: set[tuple[str, str]] = set()
    for line in text.split("\n"):
        if not line.lstrip().startswith("|"):
            continue
        method_m = _METHOD.search(line)
        if not method_m:
            continue
        path_m = _AGENT_PATH.search(line)
        if not path_m:
            continue
        path = path_m.group(1)
        if "*" in path:  # wildcard reference (e.g. /agent/recon/*), not an endpoint
            continue
        out.add((method_m.group(1), f"/api/v1{_norm(path)}"))
    return out


def _actual_agent_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/agent/"):
            norm = _norm(r.path)
            for m in r.methods:
                out.add((m, norm))
    return out


def test_served_guide_is_stamped_with_live_prompt_version(client):
    """GET /agents-guide must stamp the served guide with the LIVE
    PROMPT_VERSION, so the guide and the agent's prompt always report the same
    compatibility number (feedback #8: the guide carried the platform version
    while the prompt carried PROMPT_VERSION — two unrelated schemes)."""
    from app.services.agent_prompt_history import PROMPT_VERSION

    resp = client.get("/api/v1/agents-guide?workflow=reconnaissance")
    assert resp.status_code == 200, resp.text
    assert f"**Prompt version:** {PROMPT_VERSION}" in resp.text, (
        "served guide must carry the live PROMPT_VERSION in its header"
    )


def test_documented_agent_endpoints_exist_as_routes():
    documented = _documented_agent_endpoints(_load_agents_md())
    # Guard against a silent regex/format break that would make this pass
    # vacuously — the API-reference tables document dozens of endpoints.
    assert len(documented) >= 20, (
        f"parser only found {len(documented)} documented endpoints — "
        "AGENTS.md table format may have changed"
    )
    missing = documented - _actual_agent_routes()
    assert not missing, (
        "AGENTS.md documents agent endpoints that no longer exist as routes "
        f"(doc drift): {sorted(missing)}"
    )
