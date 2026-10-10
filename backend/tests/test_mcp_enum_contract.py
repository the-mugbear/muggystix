"""An MCP tool advertises exactly the values its endpoint accepts (v2.434.1).

The acceptance run of 2026-09-30 (H2) submitted ``status: "completed"`` to
``execution_record_test_result`` because the tool said "e.g. completed,
failed, skipped" — the endpoint's enum has no ``completed`` and answered 422.
``test_phase`` was a free string in MCP and an enum at the endpoint.  A
schema-following agent had to guess and retry.

Since the review of 2026-10-08 a tool's arguments are read from its endpoint
(``mcp_tools.derive_tool``), so an endpoint's enum is the tool's by
construction.  What these tests still referee is the part that is authored:
what a tool lays over an argument may narrow it, never widen it — and that the
derivation carries an enum through at every depth.
"""
from typing import Any, Dict, List


def _components():
    from app.main import app
    return app.openapi()


def _resolve(schema: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Follow $ref and drop the ``null`` arm of an Optional."""
    while True:
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            schema = spec["components"]["schemas"][name]
            continue
        for key in ("anyOf", "oneOf"):
            if key in schema:
                arms = [s for s in schema[key] if s.get("type") != "null"]
                if len(arms) == 1:
                    schema = {**{k: v for k, v in schema.items() if k != key}, **arms[0]}
                    break
        else:
            if "allOf" in schema and len(schema["allOf"]) == 1:
                schema = schema["allOf"][0]
                continue
            return schema


def _mismatches(path: str, tool: Dict[str, Any], api: Dict[str, Any], spec) -> List[str]:
    api = _resolve(api, spec)
    tool = _resolve(tool, spec)
    out: List[str] = []
    if "enum" in api:
        if "enum" not in tool:
            out.append(f"{path}: endpoint accepts only {sorted(api['enum'])}; tool advertises no enum")
        else:
            extra = set(tool["enum"]) - set(api["enum"])
            if extra:
                out.append(f"{path}: tool advertises {sorted(extra)}, endpoint rejects them")
    if api.get("type") == "array" and "items" in api and "items" in tool:
        out += _mismatches(f"{path}[]", tool["items"], api["items"], spec)
    api_props = api.get("properties") or {}
    for name, sub in (tool.get("properties") or {}).items():
        if name in api_props:
            out += _mismatches(f"{path}.{name}", sub, api_props[name], spec)
    return out


def _widenings(path: str, said: Dict[str, Any], route: Dict[str, Any]) -> List[str]:
    """Where what a tool SAYS about an argument is wider than its endpoint."""
    import re

    out: List[str] = []
    if "type" in said and said["type"] != route.get("type"):
        out.append(f"{path}: the tool says type {said['type']!r}; the endpoint's is {route.get('type')!r}")
    if "enum" in said:
        if "enum" in route:
            extra = set(said["enum"]) - set(route["enum"])
        elif "pattern" in route:
            extra = {v for v in said["enum"] if not re.fullmatch(route["pattern"], str(v))}
        else:
            extra = set()  # free text at the endpoint, validated in its handler
        if extra:
            out.append(f"{path}: tool advertises {sorted(extra)}, endpoint rejects them")
    for key, tighter in (("minimum", max), ("minLength", max), ("minItems", max),
                         ("maximum", min), ("maxLength", min), ("maxItems", min)):
        if key in said and key in route and tighter(said[key], route[key]) != said[key]:
            out.append(f"{path}: {key} {said[key]} is looser than the endpoint's {route[key]}")
    if "items" in said and isinstance(route.get("items"), dict):
        out += _widenings(f"{path}[]", said["items"], route["items"])
    route_props = route.get("properties") or {}
    for name, sub in (said.get("properties") or {}).items():
        if name in route_props:
            out += _widenings(f"{path}.{name}", sub, route_props[name])
    return out


def test_what_a_tool_says_about_an_argument_never_widens_its_endpoint():
    """A tool's arguments are read from its endpoint, so an enum the endpoint
    declares is the tool's by construction.  What is still authored is the
    ``params`` laid over them (an enum where the endpoint takes free text, a
    bound it leaves to a service): each may narrow, never widen, and never
    restate a type."""
    from app.api.v1.endpoints.mcp_tools import _AUTHORED, derive_tool

    openapi = _components()
    problems: List[str] = []
    for name, entry in _AUTHORED.items():
        bare = {k: v for k, v in entry.items() if k != "params"}
        route_props = derive_tool(name, bare, openapi)["input_schema"]["properties"]
        for param, said in (entry.get("params") or {}).items():
            if isinstance(said, dict):
                problems += _widenings(f"{name}.{param}", said, route_props[param])
    assert not problems, "MCP tools widen their endpoints:\n" + "\n".join(problems)


def test_an_endpoints_enum_reaches_the_tool_at_every_depth():
    """Body, nested in arrays and objects, and query: the acceptance run of
    2026-09-30 sent a value the endpoint's enum did not have."""
    from app.api.v1.endpoints.mcp_tools import TOOLS

    spec = _components()
    problems: List[str] = []
    for name, tool in TOOLS.items():
        op = spec["paths"][tool["path"]][tool["method"].lower()]
        props = tool["input_schema"]["properties"]
        sources = {p["name"]: p["schema"] for p in op.get("parameters", []) if p.get("in") == "query"}
        body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
        if body:
            sources.update(_resolve(body["schema"], spec).get("properties") or {})
        for arg, schema in sources.items():
            if arg in props:
                problems += _mismatches(f"{name}.{arg}", props[arg], schema, spec)
    assert not problems, "MCP schemas disagree with their endpoints:\n" + "\n".join(problems)


def test_host_test_and_evidence_states_are_distinct():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    status = TOOLS["host_tests_list"]["input_schema"]["properties"]["status"]
    assert set(status["enum"]) == {"proposed", "in_progress", "done", "dismissed"}
    outcome = TOOLS["record_evidence"]["input_schema"]["properties"]["outcome"]
    assert set(outcome["enum"]) == {"finding", "no_finding", "inconclusive", "failed", "info"}


# The two below came from ``test_review_1008_mcp_derived_registry.py`` — the
# one-off proof that the derived registry matched the hand-typed one, deleted
# on 2026-10-10 as its own header said to once the registry had shipped.  They
# are the part of it that is a lasting contract.

def test_a_stale_authored_name_is_an_error_not_ignored():
    """What a tool authors about an argument its endpoint does not have (a
    renamed or removed parameter) fails loudly, as does a tool for a path that
    is not routed."""
    import pytest

    from app.api.v1.endpoints import mcp_tools

    openapi = _components()
    entry = {"method": "GET", "path": "/api/v1/agent/assist/scans"}
    assert "limit" in mcp_tools.derive_tool("t", entry, openapi)["input_schema"]["properties"]
    for stale in ({"params": {"no_such": "x"}}, {"hidden": ["no_such"]}):
        with pytest.raises(ValueError, match="no_such"):
            mcp_tools.derive_tool("t", {**entry, **stale}, openapi)
    with pytest.raises(ValueError, match="not a routed endpoint"):
        mcp_tools.derive_tool("t", {"method": "GET", "path": "/api/v1/agent/nope"}, openapi)


def test_the_registry_never_imports_the_application():
    """Importing ``app.main`` applies migrations; a process that has not loaded
    it gets an error from the registry, not a migration."""
    import sys

    import pytest

    from app.api.v1.endpoints import mcp_tools

    _components()  # make sure it IS loaded, so there is something to take away
    loaded = sys.modules.pop("app.main")
    try:
        with pytest.raises(RuntimeError, match="API process"):
            mcp_tools._Registry(mcp_tools._AUTHORED)["agent_identity"]
        assert "app.main" not in sys.modules
        # Listing names needs no route.
        assert "agent_identity" in list(mcp_tools._Registry(mcp_tools._AUTHORED))
    finally:
        sys.modules["app.main"] = loaded
