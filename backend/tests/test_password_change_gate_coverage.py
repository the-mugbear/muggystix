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
#: the setup routes, by which a blocked account signs out, sees itself,
#: changes its password, enrols (or manages) its second factor and keeps its
#: session alive.  Named one by one (review 2026-10-23, H-DB1): the whole
#: ``/auth/`` prefix was exempt, and ``POST /auth/register`` — creating an
#: account, administration — passed with it.
EXEMPT = {
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/profile"),
    ("POST", "/api/v1/auth/change-password"),
    ("GET", "/api/v1/auth/sessions"),
    ("DELETE", "/api/v1/auth/sessions/{session_id}"),
    ("POST", "/api/v1/auth/session/renew"),
    ("GET", "/api/v1/auth/2fa/status"),
    ("POST", "/api/v1/auth/2fa/setup"),
    ("POST", "/api/v1/auth/2fa/enable"),
    ("POST", "/api/v1/auth/2fa/recovery-codes"),
    ("POST", "/api/v1/auth/2fa/disable"),
}


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


def _keys(path, route):
    return {(method, path) for method in route.methods}


def test_every_jwt_route_resolves_the_password_change_gate():
    outside = []
    for path, route, inherited in _all_routes():
        if _keys(path, route) <= EXEMPT:
            continue
        calls = _resolved(route, inherited)
        if get_current_user in calls and require_password_changed not in calls:
            outside.append(f"{sorted(route.methods)} {path}")
    assert outside == [], "signed-in routes outside require_password_changed:\n" + "\n".join(outside)


def test_every_exempt_route_exists_takes_a_user_and_is_outside_the_gate():
    """No stale line: each exemption names a mounted route that signs in and
    really is outside the gate (else it needs no exemption)."""
    found = {}
    for path, route, inherited in _all_routes():
        for key in _keys(path, route):
            found[key] = _resolved(route, inherited)
    for key in sorted(EXEMPT):
        assert key in found, key
        assert get_current_user in found[key], key
        assert require_password_changed not in found[key], key


def test_the_guard_sees_the_routers_it_should():
    gated = {
        path for path, route, inherited in _all_routes()
        if require_password_changed in _resolved(route, inherited)
    }
    assert any(p.startswith("/api/v1/mcp-telemetry") for p in gated)
    assert any(p.startswith("/api/v1/projects/") for p in gated)
    # Creating an account is behind the gate; the setup routes are not.
    assert "/api/v1/auth/register" in gated
    assert not any(p.startswith("/api/v1/auth/") for p in gated - {"/api/v1/auth/register"})
