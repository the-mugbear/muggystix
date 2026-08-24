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


def test_every_declared_property_is_wired_to_the_request():
    """Every input_schema property must be sent somewhere (path/query/body).
    A declared-but-unrouted property passes _validate_arguments then vanishes in
    _dispatch_tool — the agent's argument is accepted and silently ignored."""
    problems = []
    for name, spec in TOOLS.items():
        props = set(spec["input_schema"].get("properties", {}))
        unrouted = props - _advertised(spec)
        if unrouted:
            problems.append(f"{name}: properties {sorted(unrouted)} are not sent anywhere")
    assert not problems, "Declared-but-unrouted MCP tool properties:\n" + "\n".join(problems)


def test_path_placeholders_match_path_params():
    """Every {placeholder} in the path is a declared path_param, and vice versa."""
    import re
    problems = []
    for name, spec in TOOLS.items():
        placeholders = set(re.findall(r"\{(\w+)\}", spec["path"]))
        declared = set(spec.get("path_params", []))
        if placeholders != declared:
            problems.append(
                f"{name}: path has {sorted(placeholders)} but path_params={sorted(declared)}"
            )
    assert not problems, "MCP tool path/param mismatch:\n" + "\n".join(problems)


def test_auto_params_are_optional_and_declared():
    """auto_params are filled from identity AFTER _validate_arguments runs, so an
    auto param that is also `required` would be rejected before the fill ever
    happens. And an auto param must be a declared property so a caller can still
    pass it explicitly."""
    problems = []
    for name, spec in TOOLS.items():
        auto = set(spec.get("auto_params") or {})
        required = set(spec["input_schema"].get("required", []))
        props = set(spec["input_schema"].get("properties", {}))
        if auto & required:
            problems.append(f"{name}: auto_params {sorted(auto & required)} are also required")
        if auto - props:
            problems.append(f"{name}: auto_params {sorted(auto - props)} are not declared properties")
    assert not problems, "MCP auto_param invariant violations:\n" + "\n".join(problems)
