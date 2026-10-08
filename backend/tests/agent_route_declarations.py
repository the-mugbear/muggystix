"""What each mounted ``/api/v1/agent`` route declares to the operator gate.

The declarations live on the routes (``deps.agent_read_floor``,
``deps.agent_session_metadata_write``); this reads them back for the tests
that pin them, through the same function the gate uses.
"""
from typing import Dict, Tuple

from fastapi.routing import APIRoute

from app.api.deps import AgentRouteAccess, agent_route_access

AGENT_PREFIX = "/api/v1/agent"


def _walk(router, prefix=""):
    for route in router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _walk(included, prefix + route.include_context.prefix)
        elif isinstance(route, APIRoute):
            yield prefix + route.path, route


def agent_route_declarations(app) -> Dict[Tuple[str, str], AgentRouteAccess]:
    """``{(METHOD, mounted path): what the route declares}`` for every agent
    route — checked against the OpenAPI map, so a route the walk cannot see
    fails here rather than passing unexamined."""
    found = {
        (method.upper(), path): agent_route_access(route)
        for path, route in _walk(app.router)
        if path.startswith(AGENT_PREFIX + "/")
        for method in route.methods
    }
    mounted = {
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(AGENT_PREFIX + "/")
        for method in ops
    }
    hidden = {key for key in found if key not in mounted}
    assert mounted <= set(found), f"agent routes the walk did not reach: {sorted(mounted - set(found))}"
    # Routes kept out of the schema are still gated; keep them in the answer.
    assert all(method in {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD"} for method, _ in hidden)
    return found
