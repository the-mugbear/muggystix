"""The OpenAPI tag prose must not contradict what the tool registry serves.

B-Tech-2: main.py's `agent-assist` / `agent-browse` tag descriptions are
hand-written and drifted — they still called the assist surface "read-only"
and claimed "every write endpoint returns 403" long after add-note / set-follow
/ patch-host shipped. The machine-readable truth lives in mcp_tools.TOOLS
(method GET vs POST/PATCH, per-workflow). This guard fails if the prose claims
read-only for a workflow the registry gives write tools — so the description
can't silently fall out of step with the endpoints again.
"""
from app.main import _OPENAPI_TAGS
from app.api.v1.endpoints.mcp_tools import TOOLS, WORKFLOW_ASSIST


def _tag_descriptions() -> dict[str, str]:
    return {t["name"]: t["description"] for t in _OPENAPI_TAGS}


def _assist_has_write_tools() -> bool:
    return any(
        WORKFLOW_ASSIST in spec["workflows"] and spec["method"] != "GET"
        for spec in TOOLS.values()
    )


def test_assist_writes_exist_so_this_guard_is_meaningful():
    # The premise: the registry actually gives the assist workflow write tools.
    # If this ever becomes false the surface really is read-only again and the
    # assertions below should be revisited rather than silently passing.
    assert _assist_has_write_tools()


def test_assist_and_browse_tags_do_not_claim_read_only():
    tags = _tag_descriptions()
    for name in ("agent-assist", "agent-browse"):
        desc = tags[name].lower()
        assert "read-only" not in desc, (
            f"OpenAPI tag {name!r} still calls the surface read-only, but "
            "mcp_tools.TOOLS serves write tools for the assist workflow"
        )
        assert "every write endpoint returns 403" not in desc, (
            f"OpenAPI tag {name!r} still claims every write 403s — stale"
        )


def test_assist_and_browse_tags_mention_the_writes():
    # Accurate, not just not-wrong: the prose should point at the writes so an
    # operator reading the docs knows they exist and how they're gated.
    tags = _tag_descriptions()
    for name in ("agent-assist", "agent-browse"):
        desc = tags[name].lower()
        assert "note" in desc or "write" in desc or "review status" in desc, (
            f"OpenAPI tag {name!r} no longer mentions the assist writes"
        )
