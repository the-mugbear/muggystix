"""CR4-2 — architecture boundary: services must not import routers.

A service depending on an HTTP router (``app.api.v1.endpoints.*``) inverts
the dependency direction — route changes then ripple into internal code,
and the service can't be exercised without the request layer.  This test
fails if a *new* such import appears.

``operations_read_service`` and ``host_serialization`` were the modules the
review flagged; they are clean now.  ``recon_summary_service`` still imports
two helper symbols from the endpoints package and is the one remaining,
explicitly-tracked exception — tighten it when that surface is next
touched, then drop it from the allow-list here.
"""
from __future__ import annotations

import ast
import pathlib

SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"

# Modules with a KNOWN, accepted service->router import.  Add nothing here
# without a deliberate decision — the point of this test is to stop the
# list from growing silently.
ALLOWED = {"recon_summary_service.py"}


def _imports_router(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "app.api"
        ):
            return True
        if isinstance(node, ast.Import):
            if any(a.name.startswith("app.api") for a in node.names):
                return True
    return False


def test_services_do_not_import_routers():
    offenders = sorted(
        p.name
        for p in SERVICES_DIR.glob("*.py")
        if p.name not in ALLOWED and _imports_router(p)
    )
    assert not offenders, (
        f"service modules importing from app.api (router layer): {offenders}. "
        "Move shared logic into the service layer instead of importing a route."
    )


def test_decoupled_services_stay_clean():
    """Guard the two modules CR4-2 fixed so they can't regress."""
    for name in ("operations_read_service.py", "host_serialization.py"):
        assert not _imports_router(SERVICES_DIR / name), (
            f"{name} regressed — it imports from a router again (CR4-2)."
        )
