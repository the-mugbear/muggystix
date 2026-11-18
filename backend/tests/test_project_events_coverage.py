"""The changelog's vocabulary, its routes and its writer are one thing.

``project_events`` holds three lists: ``ACTIONS`` (what a line may say),
``ROUTE_ACTIONS`` (which write route records which action) and
``NOT_RECORDED`` (the write routes that record nothing, each with its
reason).  These tests are what keeps "record a line" from being a rule
someone has to remember:

* every project or agent WRITE route the API publishes is in exactly one of
  the two maps — a new route fails here until someone decides;
* every action a route is said to record exists, and every action that exists
  is recorded by some route and passed to ``record(`` somewhere under
  ``app/``;
* nothing but ``project_events.record`` makes a ``ProjectEvent``.
"""
from __future__ import annotations

import ast
import pathlib

from app.main import app
from app.services import project_events as pe

APP_DIR = pathlib.Path(pe.__file__).resolve().parents[1]
WRITE = {"post", "put", "patch", "delete"}
SCOPED = ("/api/v1/projects/{project_id}", "/api/v1/agent/", "/api/v1/remediation-overview/projects/{project_id}")


def _published_write_routes() -> set:
    keys = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(SCOPED):
            continue
        for method in operations:
            if method in WRITE:
                keys.add(pe.route_key(method, path))
    return keys


def test_every_write_route_is_recorded_or_says_why_not():
    published = _published_write_routes()
    decided = set(pe.ROUTE_ACTIONS) | set(pe.NOT_RECORDED)
    undecided = sorted(published - decided)
    assert undecided == [], (
        "write routes with no changelog decision — add each to project_events.ROUTE_ACTIONS "
        f"(and record the line) or to NOT_RECORDED with its reason: {undecided}"
    )
    stale = sorted(decided - published)
    assert stale == [], f"the changelog names routes the API no longer publishes: {stale}"
    both = sorted(set(pe.ROUTE_ACTIONS) & set(pe.NOT_RECORDED))
    assert both == [], f"in both maps: {both}"
    assert all(reason.strip() for reason in pe.NOT_RECORDED.values())


def test_the_routes_and_the_vocabulary_name_the_same_actions():
    named = {action for actions in pe.ROUTE_ACTIONS.values() for action in actions}
    assert named - set(pe.ACTIONS) == set(), "a route records an action the vocabulary does not have"
    assert set(pe.ACTIONS) - named == set(), "an action no route records"
    for action, (group, words) in pe.ACTIONS.items():
        assert group in pe.GROUPS, action
        assert words and words == words.strip() and len(action) <= 48, action


def test_every_action_is_written_out_where_it_is_recorded():
    """Each action's name appears, as a plain string, in the code that
    records it — in a ``record(...)`` call or handed to a helper that makes
    one.  An action nobody writes out is an action nothing records; a name
    built at run time (an f-string) could not be checked here, and ``record``
    refuses a name that is not in the vocabulary."""
    written = set()
    for path in APP_DIR.rglob("*.py"):
        if path.name == "project_events.py":
            continue
        source = path.read_text()
        if not any(name in source for name in ("project_events", "_changelog", "_note_line", "_host_line")):
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in pe.ACTIONS:
                written.add(node.value)
    assert sorted(set(pe.ACTIONS) - written) == [], "actions in the vocabulary that nothing records"


def test_only_the_one_writer_makes_a_line():
    makers = []
    for path in APP_DIR.rglob("*.py"):
        if path.name in ("project_events.py", "models_project.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ProjectEvent":
                makers.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert makers == []


def test_no_agent_route_reads_the_changelog():
    """Decided 2026-11-18: agents' actions are recorded; agents do not read
    the changelog."""
    for path in app.openapi()["paths"]:
        if "/agent/" in path:
            assert "changelog" not in path, path
