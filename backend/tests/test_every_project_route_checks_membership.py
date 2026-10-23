"""Every route under a ``{project_id}`` resolves the project-access check.

A project's data is its members' (and the global admins').  The check is a
dependency — ``deps.get_current_project`` (member or global admin, 404 / 410)
or ``deps.require_project_role(...)`` (a role on the project) — usually on the
router, so a route on a router mounted without one, or a handler that forgets
it, would serve any signed-in account.  Until review 2026-10-23 (R-DB1) the
27 project-scoped routers were checked by hand.  This walks every mounted
route (the walker of ``test_password_change_gate_coverage.py``) and names the
few that check access another way, each with the reason.
"""
from __future__ import annotations

from app.api.deps import get_current_project
from tests.test_password_change_gate_coverage import _all_routes, _resolved


def _checks_membership(calls) -> bool:
    return get_current_project in calls or any(
        getattr(call, "__qualname__", "").startswith("require_project_role.")
        for call in calls
    )


#: (method, path) → why the route needs neither dependency.  The reason is
#: part of the entry: a new line needs one.
OTHERWISE_CHECKED = {
    # projects.py: these must reach an ARCHIVED project (read it, unarchive
    # it, manage its members), which ``get_current_project`` answers with
    # 410; each asks ``deps.membership_role`` / ``has_project_role`` /
    # ``is_project_admin`` in the handler — the same rule.
    ("GET", "/api/v1/projects/{project_id}"): "projects.get_project: membership_role",
    ("PUT", "/api/v1/projects/{project_id}"): "projects.update_project: is_project_admin",
    ("DELETE", "/api/v1/projects/{project_id}"): "projects.delete_project: require_role(ADMIN), global",
    ("PATCH", "/api/v1/projects/{project_id}/ingest-settings"):
        "projects.update_project_ingest_settings: has_project_role(ANALYST)",
    ("GET", "/api/v1/projects/{project_id}/members"): "projects.list_members: has_project_role(VIEWER)",
    ("POST", "/api/v1/projects/{project_id}/members"): "projects.add_member: is_project_admin",
    ("PUT", "/api/v1/projects/{project_id}/members/{user_id}"): "projects.update_member: is_project_admin",
    ("DELETE", "/api/v1/projects/{project_id}/members/{user_id}"): "projects.remove_member: is_project_admin",
    # Constant answers that read nothing of the project (a signed-in user,
    # behind the password-change gate): the Query language's grammar, its
    # catalogue, and the webhook event names.
    ("GET", "/api/v1/projects/{project_id}/query/parse"): "queries.parse_query_for_builder: no database read",
    ("GET", "/api/v1/projects/{project_id}/query/schema"): "queries.query_schema: the language's catalogue",
    ("GET", "/api/v1/projects/{project_id}/webhooks/event-types"): "webhooks.list_event_types: a constant list",
}

#: Path prefixes whose router checks access its own way.
OTHERWISE_CHECKED_PREFIXES = {
    # Remediation across projects (global admin or the project's admin):
    # ``remediation_overview.administered_project`` resolves the project and
    # the caller's admin authority on it.
    "/api/v1/remediation-overview/projects/{project_id}/": "remediation_overview.administered_project",
}


def _unchecked():
    out = []
    for path, route, inherited in _all_routes():
        if "{project_id}" not in path:
            continue
        if any(path.startswith(prefix) for prefix in OTHERWISE_CHECKED_PREFIXES):
            continue
        keys = {(method, path) for method in route.methods}
        if keys <= set(OTHERWISE_CHECKED):
            continue
        if not _checks_membership(_resolved(route, inherited)):
            out.append(f"{sorted(route.methods)} {path}")
    return sorted(out)


def test_every_project_route_resolves_the_access_check():
    unchecked = _unchecked()
    assert unchecked == [], (
        "routes under {project_id} that resolve neither get_current_project nor "
        "require_project_role (add the dependency, or a line above with its reason):\n"
        + "\n".join(unchecked)
    )


def test_every_named_exception_is_a_mounted_route_that_needs_naming():
    """No stale line: each entry is mounted and really resolves neither."""
    found = {}
    for path, route, inherited in _all_routes():
        for method in route.methods:
            found[(method, path)] = _resolved(route, inherited)
    for key, reason in sorted(OTHERWISE_CHECKED.items()):
        assert reason, key
        assert key in found, key
        assert not _checks_membership(found[key]), key
    for prefix in OTHERWISE_CHECKED_PREFIXES:
        assert any(path.startswith(prefix) for path in {p for _m, p in found}), prefix
