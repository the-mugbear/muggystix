"""CR4-2 — architecture boundary: services must not import routers.

A service depending on an HTTP router (``app.api.v1.endpoints.*``) inverts
the dependency direction — route changes then ripple into internal code,
and the service can't be exercised without the request layer.  This test
fails if a *new* such import appears.

``operations_read_service`` and ``host_serialization`` were the modules the
review flagged; they are clean now.  The last exception,
``recon_summary_service``, became ``scope_targets_service`` in v2.433.1 and
owns its shapes and scope query, so the allow-list is empty.
"""
from __future__ import annotations

import ast
import pathlib

SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"

# Modules with a KNOWN, accepted service->router import.  Add nothing here
# without a deliberate decision — the point of this test is to stop the
# list from growing silently.
ALLOWED: set = set()


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


# ---------------------------------------------------------------------------
# The reverse boundary (review 2026-10-01 B4)
# ---------------------------------------------------------------------------
#
# Routers do not reach into one another's internals, and the shared modules
# under ``app/api`` do not depend on a router.  Before this, eleven private
# names crossed endpoint files (``scans._apply_scan_inventory_filters``,
# ``client_reports._load`` / ``_serialize``, ``agent_assist._load_assist_session``,
# ``assist._build_mcp_clients``, ``mcp_assist._TOOLS``, the two
# ``agent_common._…`` helpers) and ``app/api/deps.py`` imported
# ``get_current_user`` from ``endpoints/auth.py``.

API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"
ENDPOINTS_DIR = API_DIR / "v1" / "endpoints"
ENDPOINTS_PACKAGE = "app.api.v1.endpoints"


def _endpoint_imports(path: pathlib.Path, *, in_endpoints_dir: bool):
    """``(module, name, lineno)`` for every name the file imports from a module
    under ``app/api/v1/endpoints`` (absolute, or relative from inside it)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (
                module == ENDPOINTS_PACKAGE or module.startswith(ENDPOINTS_PACKAGE + ".")
            ):
                source = module[len(ENDPOINTS_PACKAGE) + 1:]
            elif node.level == 1 and in_endpoints_dir:
                source = module  # ``from .scans import x`` / ``from . import scans``
            else:
                continue
            for alias in node.names:
                yield source, alias.name, node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(ENDPOINTS_PACKAGE + "."):
                    yield alias.name[len(ENDPOINTS_PACKAGE) + 1:], "*", node.lineno


def test_endpoint_modules_do_not_import_each_others_private_names():
    """A helper two routers need is a service (or a public name in a shared
    module such as ``agent_common``) — never ``from …endpoints.x import _y``."""
    offenders = []
    for path in sorted(ENDPOINTS_DIR.glob("*.py")):
        for source, name, lineno in _endpoint_imports(path, in_endpoints_dir=True):
            if source.split(".")[0] == path.stem:
                continue
            if name.startswith("_"):
                offenders.append(f"{path.name}:{lineno} imports {name} from {source or '.'}")
    assert not offenders, (
        "endpoint modules importing another endpoint module's private name: "
        f"{offenders}. Move the helper to app/services (or app/api/params.py "
        "if it needs FastAPI's Query/Depends) and give it a public name."
    )


def test_shared_api_modules_do_not_import_routers():
    """``app/api/deps.py``, ``app/api/params.py`` (anything directly under
    ``app/api``) are what routers import FROM.  The auth dependencies live in
    ``deps.py``; ``endpoints/auth.py`` re-exports them."""
    offenders = []
    for path in sorted(API_DIR.glob("*.py")):
        for source, name, lineno in _endpoint_imports(path, in_endpoints_dir=False):
            offenders.append(f"{path.name}:{lineno} imports {name} from endpoints.{source}")
    assert not offenders, (
        f"shared app/api modules importing from a router: {offenders}."
    )


def test_auth_dependencies_are_one_object_under_both_names():
    """FastAPI keys ``dependency_overrides`` and its per-request dependency
    cache on the function OBJECT.  The suite (and older code) imports
    ``get_current_user`` from ``endpoints.auth``; the routers import it from
    ``app.api.deps``.  Two objects would mean an override that silently does
    not apply."""
    from app.api import deps
    from app.api.v1.endpoints import auth

    for name in ("get_current_user", "require_role", "require_password_changed",
                 "get_client_info", "security"):
        assert getattr(auth, name) is getattr(deps, name), name
    assert auth.get_current_user.__module__ == "app.api.deps"
