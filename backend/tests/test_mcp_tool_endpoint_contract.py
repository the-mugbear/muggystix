"""The MCP tool registry is hand-maintained data that must agree with the
endpoints it wraps. Nothing bound the two, so the advertised schema drifted
from reality — a tool would advertise a query param the endpoint dropped
(``execution_get_context`` limit/offset/status), or name the wrong paging
control (``plan_get_context`` offset vs the real after_host_id cursor). A
client reads the advertised schema and gets a 422 or a silent no-op.

These tests bind every ``TOOLS`` entry to the real FastAPI signature via the
OpenAPI schema (``app.openapi()`` — FastAPI 0.141 no longer surfaces included
routes through a flat ``app.routes`` walk), plus a few registry invariants the
dispatcher relies on. They are pure structure checks: no DB, no client.
"""
from __future__ import annotations

from typing import Optional, Set

from app.main import app
from app.api.v1.endpoints.mcp_tools import TOOLS


def _resolve(schema: dict, openapi: dict) -> dict:
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return openapi.get("components", {}).get("schemas", {}).get(name, {})
    return schema


def _schema_properties(schema: dict, openapi: dict) -> Set[str]:
    schema = _resolve(schema, openapi)
    props: Set[str] = set(schema.get("properties", {}))
    for sub in schema.get("allOf", []):
        props |= _schema_properties(sub, openapi)
    return props


def _endpoint_params(openapi: dict, path: str, method: str) -> Optional[Set[str]]:
    """Names the endpoint actually accepts (path + query + JSON body), or None
    if the route is not in the schema at all."""
    op = openapi["paths"].get(path, {}).get(method.lower())
    if op is None:
        return None
    accepted: Set[str] = {p["name"] for p in op.get("parameters", [])}
    body = op.get("requestBody")
    if body:
        schema = body.get("content", {}).get("application/json", {}).get("schema", {})
        accepted |= _schema_properties(schema, openapi)
    return accepted


def _advertised(spec: dict) -> Set[str]:
    return (
        set(spec.get("path_params", []))
        | set(spec.get("query_params", []))
        | set(spec.get("body_params", []))
    )


def test_every_advertised_param_is_accepted_by_its_endpoint():
    """No tool may advertise a path/query/body param the endpoint doesn't take.
    That is the drift that reaches an agent as a 422 or a silently dropped arg."""
    openapi = app.openapi()
    problems = []
    for name, spec in TOOLS.items():
        accepted = _endpoint_params(openapi, spec["path"], spec["method"])
        if accepted is None:
            problems.append(
                f"{name}: {spec['method']} {spec['path']} is not a routed endpoint"
            )
            continue
        extra = _advertised(spec) - accepted
        if extra:
            problems.append(
                f"{name}: advertises {sorted(extra)} that {spec['method']} "
                f"{spec['path']} does not accept (accepts {sorted(accepted)})"
            )
    assert not problems, "MCP tool/endpoint contract drift:\n" + "\n".join(problems)


def test_an_entry_authors_nothing_the_endpoint_already_says():
    """Placement, types and required-ness are read from the endpoint
    (``mcp_tools.derive_tool``).  An authored entry that carried them again
    would be a second copy free to drift — and a ``params`` / ``hidden`` name
    the endpoint does not take fails the derivation itself (every entry is
    derived above; ``derive_tool`` raises on a stale name)."""
    from app.api.v1.endpoints.mcp_tools import _AUTHORED

    derived_keys = {"path_params", "query_params", "body_params", "input_schema"}
    known = derived_keys | {
        "description", "method", "path", "params", "hidden", "defaults", "additive",
        "idempotent", "metadata_write", "retired_params", "path_alternatives", "result",
    }
    for name, entry in _AUTHORED.items():
        assert not derived_keys & set(entry), name
        assert set(entry) <= known, (name, sorted(set(entry) - known))
        for overlay in (entry.get("params") or {}).values():
            assert isinstance(overlay, str) or "type" not in overlay, name
        # A default the tool injects is for an argument it offers.
        assert set(entry.get("defaults", ())) <= set(TOOLS[name]["input_schema"]["properties"]), name
        # A retired argument is not also a live one.
        assert not set(entry.get("retired_params", ())) & set(TOOLS[name]["input_schema"]["properties"]), name


def test_no_tool_declares_an_argument_filled_from_the_key():
    """The transport no longer fills arguments from the caller's identity, so an
    ``auto_params`` entry would be read by nothing."""
    assert not [name for name, spec in TOOLS.items() if "auto_params" in spec]


def test_every_path_alternative_is_a_real_endpoint():
    """v2.428.0 — ``path_alternatives`` lets one tool reach one of several
    endpoints, chosen by which id the caller passes (``assist_get_image``: an
    attachment or a screenshot).  Each alternative is held to what the main
    path is: a routed endpoint of the same method whose only placeholder is its
    own argument, a declared property, and never also a required one (the
    dispatcher demands exactly one of the ids, so none can be required)."""
    import re
    openapi = app.openapi()
    problems = []
    for name, spec in TOOLS.items():
        alternatives = spec.get("path_alternatives") or {}
        if not alternatives:
            continue
        props = set(spec["input_schema"].get("properties", {}))
        required = set(spec["input_schema"].get("required", []))
        choices = set(spec.get("path_params", [])) | set(alternatives)
        if choices & required:
            problems.append(f"{name}: {sorted(choices & required)} are required, but only one may be passed")
        for arg, path in alternatives.items():
            if arg not in props:
                problems.append(f"{name}: alternative {arg!r} is not a declared property")
            if set(re.findall(r"\{(\w+)\}", path)) != {arg}:
                problems.append(f"{name}: {path} must have exactly the placeholder {{{arg}}}")
            accepted = _endpoint_params(openapi, path, spec["method"])
            if accepted is None:
                problems.append(f"{name}: {spec['method']} {path} is not a routed endpoint")
            elif arg not in accepted:
                problems.append(f"{name}: {path} does not accept {arg!r} (accepts {sorted(accepted)})")
    assert not problems, "MCP path_alternatives drift:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# Tool names mentioned in prose must exist (v2.428.1, agent feedback #9: the
# create_test_plan description sent agents to "submit_test_plan" and
# start_recon to "list_scopes" — neither is a tool).
# ---------------------------------------------------------------------------

import re as _re

# Tool names here start with one of these; a word that does and is not a tool
# is either a field name (listed below, on purpose) or a wrong tool name.
_TOOLISH = _re.compile(
    r"\b(?:assist|plan|execution|recon|start|session|list|get|submit|create|"
    r"read|record|suggest|end|propose|host_tests)_[a-z_]+\b"
)
# Field / value names that look tool-shaped. Add to this only for a real
# payload word — never for a mistyped tool.
_NOT_TOOLS = {
    "read_back", "end_date", "plan_generation",
    # v2.442.0: a retired tool name and a retired value, each named once in a
    # description that says it is gone / still accepted.
}
_FIELD_SUFFIXES = ("_id", "_ids", "_at", "_count", "_total", "_path")


def _unknown_tool_names(text: str) -> Set[str]:
    return {
        t for t in _TOOLISH.findall(text)
        if t not in TOOLS and t not in _NOT_TOOLS and not t.endswith(_FIELD_SUFFIXES)
    }


def test_tool_descriptions_name_only_real_tools():
    bad = {
        name: sorted(_unknown_tool_names(spec["description"]))
        for name, spec in TOOLS.items()
        if _unknown_tool_names(spec["description"])
    }
    assert not bad, f"tool descriptions name tools that do not exist: {bad}"


def test_server_instructions_name_only_real_tools():
    from app.api.v1.endpoints.mcp_assist import _server_instructions

    bad = _unknown_tool_names(_server_instructions("https://example.test/api/v1"))
    assert not bad, f"MCP server instructions name tools that do not exist: {sorted(bad)}"


def test_agent_guide_names_only_real_tools():
    import pytest
    from app.services.agents_guide_service import read_agent_guide

    guide = read_agent_guide()
    if guide is None:
        pytest.skip("the agent guide is not mounted in this environment")
    # Backticked words only — the guide's prose uses words like "plan_generation"
    # as workflow values, which the allowlist covers; agent_* fields never
    # match the tool prefixes above.
    words = set(_re.findall(r"`([a-z_]+)`", guide))
    bad = {w for w in words if _TOOLISH.fullmatch(w)} & _unknown_tool_names(" ".join(words))
    assert not bad, f"the agent guide names tools that do not exist: {sorted(bad)}"
