"""An MCP tool advertises exactly the values its endpoint accepts (v2.434.1).

The acceptance run of 2026-09-30 (H2) submitted ``status: "completed"`` to
``execution_record_test_result`` because the tool said "e.g. completed,
failed, skipped" — the endpoint's enum has no ``completed`` and answered 422.
``test_phase`` was a free string in MCP and an enum at the endpoint.  A
schema-following agent had to guess and retry.

The registry (``mcp_tools.py``) is plain data by design (no DB imports), so it
cannot share the endpoint's enums; this test is the referee instead.  Every
field an endpoint constrains to an enum — in the body, nested in arrays and
objects, or in the query — must be advertised with an enum by the tool, and
that enum must be a subset of the endpoint's (a tool may narrow, never widen).
"""
from typing import Any, Dict, List, Optional


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


def _request_body_schema(op: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = (op.get("requestBody") or {}).get("content") or {}
    for media in ("application/json", "multipart/form-data"):
        if media in content:
            return content[media]["schema"]
    return None


def test_every_advertised_enum_matches_the_endpoint():
    from app.api.v1.endpoints.mcp_tools import TOOLS

    spec = _components()
    problems: List[str] = []
    for name, tool in TOOLS.items():
        op = spec["paths"][tool["path"]][tool["method"].lower()]
        props = tool["input_schema"].get("properties", {})
        body = _request_body_schema(op)
        if body is not None:
            api_props = _resolve(body, spec).get("properties") or {}
            for param in tool.get("body_params", []):
                if param in props and param in api_props:
                    problems += _mismatches(f"{name}.{param}", props[param], api_props[param], spec)
        for p in op.get("parameters", []):
            if p.get("in") == "query" and p["name"] in props:
                problems += _mismatches(f"{name}?{p['name']}", props[p["name"]], p["schema"], spec)
    assert not problems, "MCP schemas disagree with their endpoints:\n" + "\n".join(problems)


def test_the_result_outcomes_are_the_endpoints():
    """The reported case, pinned by value: no ``completed``."""
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.db.models_agent import TestExecutionStatus

    status = TOOLS["execution_record_test_result"]["input_schema"]["properties"]["status"]
    assert set(status["enum"]) == {s.value for s in TestExecutionStatus}
    assert "completed" not in status["enum"]
