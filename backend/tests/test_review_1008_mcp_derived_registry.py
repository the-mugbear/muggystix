"""Review 2026-10-08: the MCP registry's parameters are derived from the routes.

``fixtures/mcp_tool_schemas_before_derivation.json`` is what ``tools/list``
advertised, and where each argument was sent, while every tool's parameters
were typed by hand in ``mcp_tools.py`` (captured before the change).  The
derived registry must offer the same tools with the same argument names, the
same required-ness and the same placement, and a schema that is nowhere wider.

Every difference is listed in ``DIFFERENCES`` with its reason; the test fails
on one that is not listed and on a listed one that no longer occurs.  This is
the proof for one change, not a contract: once the derived registry has
shipped, delete this file and its fixture — a route may then gain a parameter
without an edit anywhere, which is the point.
"""
import json
from pathlib import Path

import pytest

from app.api.v1.endpoints import mcp_tools
from app.api.v1.endpoints.mcp_tools import TOOLS, advertised_schema
from app.main import app  # noqa: F401  (the registry reads the loaded app's routes)

BEFORE = json.loads(
    (Path(__file__).parent / "fixtures" / "mcp_tool_schemas_before_derivation.json").read_text()
)

# Why a derived schema may differ from the hand-typed one.
ROUTE_DEFAULT = "the endpoint's own default is now advertised (it was applied before, unsaid)"
ROUTE_BOUND = "the endpoint enforces this bound; the hand-typed schema did not say so (narrower)"
FREE_OBJECT = "the endpoint takes a free-form object; stated instead of left unsaid"
NO_DEFAULT = "the hand-typed default was not the endpoint's: it has none for this argument"

NEW_ARGUMENT = "the endpoint gained this query parameter after the capture; its tool offers it unedited"

NEW_FIELD = "the rows of the endpoint's body gained this optional field after the capture"

#: {tool: {query argument}} — what a route gained since the fixture was taken.
ADDED_SINCE_CAPTURE = {"remediation_list": {"verification", "flag", "q"},
                       "remediation_follow_up": {"upcoming_days"}}
#: {tool: {body argument}} — the same, for an argument sent in the JSON body.
ADDED_TO_BODY_SINCE_CAPTURE = {"remediation_record_follow_up": {"upcoming_days"}}
#: Tools for routes that did not exist when the fixture was taken.
TOOLS_SINCE_CAPTURE = {
    "remediation_assign_from_report", "assist_get_writing_guidance", "assist_list_my_findings",
}

#: {tool: {query argument}} — an endpoint parameter the tool stopped offering
#: (``hidden``): a second name for an argument it already has.
HIDDEN_SINCE_CAPTURE = {"assist_list_scanner_observations": {"skip"}}

HIDDEN = "the tool no longer offers this second name for an argument it has (narrower)"
NARROWED = "the tool names the accepted values where the endpoint takes free text (narrower)"
GUIDE_PART = "the guide gained a part; the endpoint refuses any value that is not one"

#: {tool: {"argument/key": reason}} — every difference, and nothing else.
DIFFERENCES = {
    "assist_get_finding": {"finding_id/minimum": ROUTE_BOUND},
    "assist_get_host": {"ip/minLength": ROUTE_BOUND},
    "assist_list_evidence_gaps": {"domain/maxLength": ROUTE_BOUND, "segment/maxLength": ROUTE_BOUND},
    "assist_list_findings": {
        "offset/default": ROUTE_DEFAULT, "unowned/default": ROUTE_DEFAULT, "source/enum": NARROWED,
    },
    "read_agent_guide": {"workflow/enum": GUIDE_PART},
    "assist_list_scanner_observations": {
        "kind/maxLength": ROUTE_BOUND,
        "severity/maxLength": ROUTE_BOUND,
        "min_hosts/maximum": ROUTE_BOUND,
        # ``offset`` is optional on the endpoint (it falls back to ``skip``,
        # whose default is 0); the hand-typed schema had the two the other way.
        "offset/default": NO_DEFAULT,
        "skip": HIDDEN,
    },
    "assist_list_scans": {"offset/default": ROUTE_DEFAULT},
    "host_tests_list": {
        "active_only/default": ROUTE_DEFAULT,
        "mine/default": ROUTE_DEFAULT,
        "limit/default": ROUTE_DEFAULT,
        "offset/default": ROUTE_DEFAULT,
        "label/maxLength": ROUTE_BOUND,
    },
    "list_evidence": {"offset/default": ROUTE_DEFAULT},
    "list_proposals": {"mine/default": ROUTE_DEFAULT, "offset/default": ROUTE_DEFAULT},
    "remediation_list": {
        "group/default": ROUTE_DEFAULT,
        "limit/default": ROUTE_DEFAULT,
        "offset/default": ROUTE_DEFAULT,
        "unassigned/default": ROUTE_DEFAULT,
        "verification": NEW_ARGUMENT,
        "flag": NEW_ARGUMENT,
        "q": NEW_ARGUMENT,
    },
    "remediation_follow_up": {"upcoming_days": NEW_ARGUMENT},
    "remediation_record_follow_up": {"upcoming_days": NEW_ARGUMENT},
    "remediation_apply": {
        "rows/items/properties/due_override_on": NEW_FIELD,
        "rows/items/properties/deferred_review_on": NEW_FIELD,
    },
    "remediation_timeline": {"limit/default": ROUTE_DEFAULT, "offset/default": ROUTE_DEFAULT},
    "remediation_trend": {"days/default": ROUTE_DEFAULT},
    "submit_feedback": {"agent_metrics/additionalProperties": FREE_OBJECT},
    "suggest_tool": {"name/minLength": ROUTE_BOUND},
}


def _inline(schema):
    """The hand-typed schema with its ``$defs`` inlined, as the derived one is."""
    defs = schema.get("$defs", {})
    return mcp_tools._plain({k: v for k, v in schema.items() if k != "$defs"}, defs)


def _shape(schema):
    """A schema without its prose: descriptions and titles removed at every
    level (the keys of ``properties`` are argument names and stay)."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in ("description", "title"):
            continue
        if key in mcp_tools._SUBSCHEMA:
            out[key] = _shape(value)
        elif key in mcp_tools._SUBSCHEMA_LISTS:
            out[key] = [_shape(v) for v in value]
        elif key in mcp_tools._SUBSCHEMA_MAPS:
            out[key] = {k: _shape(v) for k, v in value.items()}
        else:
            out[key] = value
    return out


def _differences(before, derived, path=""):
    """``{"path/key": (before, derived)}`` for every key that differs."""
    if isinstance(before, dict) and isinstance(derived, dict):
        found = {}
        for key in sorted(set(before) | set(derived)):
            here = f"{path}/{key}" if path else key
            if key not in before:
                found[here] = ("(absent)", derived[key])
            elif key not in derived:
                found[here] = (before[key], "(absent)")
            else:
                found.update(_differences(before[key], derived[key], here))
        return found
    if isinstance(before, list) and isinstance(derived, list) and sorted(map(str, before)) == sorted(map(str, derived)):
        return {}
    return {} if before == derived else {path: (before, derived)}


def test_the_same_tools_are_listed():
    assert sorted(TOOLS) == sorted(set(BEFORE) | TOOLS_SINCE_CAPTURE)
    assert [tool["name"] for tool in mcp_tools.tool_list_payload()] == list(TOOLS)


@pytest.mark.parametrize("name", sorted(BEFORE))
def test_names_requiredness_and_placement_are_unchanged(name):
    before, spec = BEFORE[name], TOOLS[name]
    schema = advertised_schema(spec)
    added = {"query_params": ADDED_SINCE_CAPTURE.get(name, set()),
             "body_params": ADDED_TO_BODY_SINCE_CAPTURE.get(name, set())}
    hidden = HIDDEN_SINCE_CAPTURE.get(name, set())
    assert set(schema["properties"]) == (set(before["inputSchema"]["properties"])
                                         | added["query_params"] | added["body_params"]) - hidden
    assert set(schema.get("required", ())) == set(before["inputSchema"].get("required", ()))
    for where in ("path_params", "query_params", "body_params"):
        expected = (set(before[where]) | added.get(where, set())) - hidden
        assert set(spec.get(where, ())) == expected, where
    assert (spec["method"], spec["path"]) == (before["method"], before["path"])
    assert (spec.get("path_alternatives") or {}) == before["path_alternatives"]
    # Unknown arguments are still refused.
    assert schema["additionalProperties"] is False


def test_every_schema_difference_is_a_listed_one():
    actual = {}
    for name, before in BEFORE.items():
        was = _shape(_inline(before["inputSchema"]))["properties"]
        now = _shape(advertised_schema(TOOLS[name]))["properties"]
        found = _differences(was, now)
        if found:
            actual[name] = found
    unexplained = {
        name: {key: value for key, value in found.items() if key not in DIFFERENCES.get(name, {})}
        for name, found in actual.items()
    }
    unexplained = {name: found for name, found in unexplained.items() if found}
    assert not unexplained, json.dumps(unexplained, indent=1, default=str)
    stale = {
        name: sorted(set(listed) - set(actual.get(name, {})))
        for name, listed in DIFFERENCES.items()
        if set(listed) - set(actual.get(name, {}))
    }
    assert not stale, stale


def test_no_listed_difference_widens_a_schema():
    """A difference only ever adds a default or a constraint, or drops a
    default the endpoint never had."""
    adds_constraint = {"minimum", "maximum", "minLength", "maxLength", "pattern", "minItems", "maxItems"}
    for name, listed in DIFFERENCES.items():
        was = _shape(_inline(BEFORE[name]["inputSchema"]))["properties"]
        now = _shape(advertised_schema(TOOLS[name]))["properties"]
        found = _differences(was, now)
        for key, reason in listed.items():
            before, derived = found[key]
            leaf = key.rsplit("/", 1)[-1]
            if reason == ROUTE_DEFAULT:
                assert leaf == "default" and before == "(absent)", (name, key)
            elif reason == ROUTE_BOUND:
                assert leaf in adds_constraint and before == "(absent)", (name, key)
            elif reason == FREE_OBJECT:
                assert leaf == "additionalProperties" and (before, derived) == ("(absent)", True), (name, key)
            elif reason == NO_DEFAULT:
                assert leaf == "default" and derived == "(absent)", (name, key)
            elif reason == NEW_ARGUMENT:
                # A whole optional argument the endpoint gained, not a change to one.
                gained = ADDED_SINCE_CAPTURE.get(name, set()) | ADDED_TO_BODY_SINCE_CAPTURE.get(name, set())
                assert key in gained and before == "(absent)", (name, key)
            elif reason == HIDDEN:
                assert key in HIDDEN_SINCE_CAPTURE.get(name, set()) and derived == "(absent)", (name, key)
            elif reason == NARROWED:
                assert leaf == "enum" and before == "(absent)", (name, key)
            elif reason == GUIDE_PART:
                # The one place a list grew: the route refuses what is not on it.
                assert leaf == "enum" and set(before) < set(derived), (name, key)
            elif reason == NEW_FIELD:
                # A whole optional field of a body row, not a change to one.
                assert "/properties/" in key and before == "(absent)", (name, key)
            else:
                raise AssertionError(f"{name} {key}: unknown reason {reason!r}")


def test_a_stale_authored_name_is_an_error_not_ignored():
    openapi = app.openapi()
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

    loaded = sys.modules.pop("app.main")
    try:
        with pytest.raises(RuntimeError, match="API process"):
            mcp_tools._Registry(mcp_tools._AUTHORED)["agent_identity"]
        assert "app.main" not in sys.modules
        # Listing names needs no route.
        assert "agent_identity" in list(mcp_tools._Registry(mcp_tools._AUTHORED))
    finally:
        sys.modules["app.main"] = loaded
