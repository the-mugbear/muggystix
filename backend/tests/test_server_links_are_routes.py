"""Every page link the server builds names a route the frontend has.

Posture's reasons linked to `/insights` and `/insights/systemic` long after
those pages moved to `/posture/segments` and `/posture/patterns`; only a
frontend redirect kept the links alive, and removing the retired redirects
(owner, 2026-10-23) would have broken them silently.  This reads every
`link="/…"` the backend writes and checks its path against `App.tsx`'s
`<Route path="…">` list.
"""
import os
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
ROUTE = re.compile(r'<Route\s+path="([^"]+)"')
LINK = re.compile(r'\blink=f?"(/[^"?{]*)')


def _app_tsx() -> pathlib.Path:
    roots = [pathlib.Path(p) for p in (os.getenv("BLUESTICK_REPO_ROOT"), "/repo") if p]
    roots.append(APP.parents[1])
    for root in roots:
        candidate = root / "frontend" / "src" / "App.tsx"
        if candidate.is_file():
            return candidate
    pytest.skip("the repository root is not mounted here (check.sh mounts it)")


def _matches(path: str, route: str) -> bool:
    # `*` is the not-found page and `/*` the signed-in layout: catch-alls, not pages.
    if route in ("*", "/*"):
        return False
    pattern = "^" + re.sub(r":[^/]+", "[^/]+", route).replace("/*", "(/.*)?") + "$"
    return re.match(pattern, path) is not None


def test_every_server_built_page_link_is_a_frontend_route():
    routes = ROUTE.findall(_app_tsx().read_text(encoding="utf-8"))
    assert "/posture/segments" in routes            # the reader works
    links = {
        (str(py.relative_to(APP)), path)
        for py in APP.rglob("*.py")
        for path in LINK.findall(py.read_text(encoding="utf-8"))
    }
    assert links, "found no server-built page link"
    broken = sorted((where, path) for where, path in links if not any(_matches(path, r) for r in routes))
    assert broken == [], f"links to pages the frontend does not have: {broken}"
