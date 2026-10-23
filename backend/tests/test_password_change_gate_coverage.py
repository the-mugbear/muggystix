"""Every signed-in (JWT) route sits behind the account-readiness gate.

``require_password_changed`` keeps an account with a pending forced password
change (or a pending mandatory 2FA enrolment) out of the data until it has
finished setup.  It is attached per router in ``api.py``, so a router mounted
without it is silently outside the gate: ``/mcp-telemetry`` was (review
2026-10-22).  This reads every route's dependencies — its own, its router's
and those given to ``include_router`` — so a new router cannot forget it.
"""

import re

from fastapi.dependencies.utils import get_dependant
from fastapi.routing import APIRoute

from app.api.deps import get_current_user, require_password_changed
from app.main import app

#: Routes that take a signed-in user and are deliberately outside the gate:
#: the ``/auth`` surface is how a blocked account finishes its setup.
_EXEMPT_PREFIXES = ("/api/v1/auth/",)


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def _routes(router, prefix="", inherited=()):
    """``(mounted path, route, include-time Depends)`` — FastAPI keeps an
    included router as one node rather than flattening it into ``app.routes``
    (see ``agent_route_declarations._walk``)."""
    for route in router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            context = route.include_context
            yield from _routes(included, prefix + context.prefix,
                               (*inherited, *context.dependencies))
        elif isinstance(route, APIRoute):
            yield prefix + route.path, route, inherited


def _resolved(route, inherited):
    calls = set(_calls(route.dependant))
    for depends in inherited:
        calls.add(depends.dependency)
        calls.update(_calls(get_dependant(path="", call=depends.dependency)))
    return calls


def _all_routes():
    found = list(_routes(app.router))
    mounted = set(app.openapi()["paths"])
    # The walk must see every mounted route, or the guard passes on nothing.
    # (OpenAPI drops a path converter: ``{host_id:int}`` is ``{host_id}``.)
    walked = {re.sub(r"\{(\w+):\w+\}", r"{\1}", path) for path, _route, _inherited in found}
    assert mounted <= walked, sorted(mounted - walked)
    return found


def test_every_jwt_route_resolves_the_password_change_gate():
    outside = []
    for path, route, inherited in _all_routes():
        if path.startswith(_EXEMPT_PREFIXES):
            continue
        calls = _resolved(route, inherited)
        if get_current_user in calls and require_password_changed not in calls:
            outside.append(f"{sorted(route.methods)} {path}")
    assert outside == [], "signed-in routes outside require_password_changed:\n" + "\n".join(outside)


def test_the_guard_sees_the_routers_it_should():
    gated = {
        path for path, route, inherited in _all_routes()
        if require_password_changed in _resolved(route, inherited)
    }
    assert any(p.startswith("/api/v1/mcp-telemetry") for p in gated)
    assert any(p.startswith("/api/v1/projects/") for p in gated)
    assert not any(p.startswith("/api/v1/auth/") for p in gated)
