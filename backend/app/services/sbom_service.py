"""
SBOM service — software bill of materials for the deployed app.

Built to answer the operational vulnerability-triage questions:

* "Does this app use package X at all?"
* "Is it in the backend, the frontend, or both?"
* "What version is in *this* build?"
* "Did we choose it, or did something we chose pull it in?"

Source of truth — manifests on disk inside the backend container:

* **Python (backend)** — ``importlib.metadata`` walks the installed venv.
  This is the *resolved* tree, so transitive dependencies are visible
  alongside direct ones.  We cross-reference each distribution's name
  against ``backend/requirements.txt`` to set the ``direct`` flag.

* **npm (frontend)** — ``frontend/package-lock.json`` (the resolved
  lockfile, not ``package.json`` alone which is loose).  Mounted into
  the backend container read-only by ``docker-compose.yml``.  Direct
  classification: a package is direct iff it appears in the root
  entry's ``dependencies`` / ``devDependencies`` /
  ``peerDependencies`` / ``optionalDependencies``.

The endpoint serving this data caches the result, keyed by the two
manifest files' mtimes — recompute happens automatically on any change
without restarting the process.

This is intentionally a *snapshot of what's installed*, not an
exploitability statement.  Presence in the list confirms the package is
bundled; it does NOT confirm a vulnerability is reachable from app code.
The UI surfaces that caveat prominently.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# requirements.txt is COPY'd into /app by the backend Dockerfile and
# stays at the image root.  package-lock.json is mounted read-only from
# the host by docker-compose's backend service.
_BACKEND_REQUIREMENTS_PATH = Path("/app/requirements.txt")
_FRONTEND_LOCK_PATH = Path("/app/frontend-package-lock.json")


# ---------------------------------------------------------------------------
# Cache — recompute only when a manifest's mtime changes.
# ---------------------------------------------------------------------------

# Cache key tuple: (requirements.txt mtime, package-lock.json mtime, app_version).
# Including app_version means a deploy that updates platform_version.json
# without touching the manifests still returns a fresh envelope.
_cache: Optional[Dict[str, Any]] = None
_cache_key: Optional[Tuple[float, float, str]] = None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------------------
# Python (backend) — installed packages via importlib.metadata.
# ---------------------------------------------------------------------------

def _normalize_python_name(name: str) -> str:
    """PEP 503 normalisation — lower-case, hyphens for separators."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_requirements_names() -> set[str]:
    """Names of packages explicitly listed in backend/requirements.txt.

    Skips comments, blank lines, and pip option lines (``-r``, ``--hash``,
    etc.).  Strips version specifiers and extras to leave just the
    package name, then PEP-503-normalises so matching against
    importlib metadata is robust to case + separator differences.
    """
    if not _BACKEND_REQUIREMENTS_PATH.exists():
        return set()
    names: set[str] = set()
    for raw in _BACKEND_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if match:
            names.add(_normalize_python_name(match.group(1)))
    return names


def _extract_python_license(meta) -> Optional[str]:
    """Best-effort license string from a Python distribution's metadata.

    Tries License-Expression (PEP 639) first, then the legacy License
    field (capped at 80 chars — some packages stuff the full license
    text in there), then any "License ::" Classifier.  "UNKNOWN" and
    blank values are treated as missing.
    """
    expr = meta.get("License-Expression")
    if expr and expr.upper() != "UNKNOWN":
        return expr.strip()
    lic = meta.get("License")
    if lic and lic.upper() != "UNKNOWN" and len(lic) < 80:
        return lic.strip()
    for cls in meta.get_all("Classifier") or []:
        if cls.startswith("License ::"):
            return cls.split("::")[-1].strip()
    return None


def _list_backend_components() -> List[Dict[str, Any]]:
    direct = _read_requirements_names()
    components: List[Dict[str, Any]] = []
    for dist in importlib_metadata.distributions():
        try:
            name = dist.metadata["Name"]
            if not name:
                continue
            version = dist.version
            components.append({
                "name": name,
                "version": version,
                "ecosystem": "python",
                "application_layer": "backend",
                # ``declared_in`` is where the package is *listed* (the
                # manifest the user edits); ``resolved_from`` is where
                # the exact installed version was *observed*.  For
                # Python the latter is the live venv, not a checked-in
                # lockfile, so we surface that distinction honestly
                # rather than collapsing both into "manifest_source".
                "declared_in": "backend/requirements.txt",
                "resolved_from": "installed venv (importlib.metadata)",
                "direct": _normalize_python_name(name) in direct,
                "license": _extract_python_license(dist.metadata),
            })
        except Exception as exc:  # one bad metadata file shouldn't kill the rest
            logger.debug("Skipping Python distribution due to metadata error: %s", exc)
    return components


# ---------------------------------------------------------------------------
# npm (frontend) — package-lock.json (v2/v3 schema).
# ---------------------------------------------------------------------------

def _list_frontend_components() -> List[Dict[str, Any]]:
    if not _FRONTEND_LOCK_PATH.exists():
        return []
    try:
        data = json.loads(_FRONTEND_LOCK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not parse package-lock.json: %s", exc)
        return []

    packages = data.get("packages", {}) or {}
    root = packages.get("", {})
    direct: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        direct.update((root.get(key) or {}).keys())

    # Dedupe (name, version): the same package can appear under multiple
    # nested node_modules paths.  Keep one entry; prefer the one flagged
    # as direct if there's a conflict.
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for path_key, info in packages.items():
        if path_key == "":
            continue  # the root project itself
        # "node_modules/<name>" or "node_modules/parent/node_modules/<name>"
        # — the package name is the segment after the LAST "node_modules/".
        idx = path_key.rfind("node_modules/")
        if idx < 0:
            continue
        name = path_key[idx + len("node_modules/"):]
        version = info.get("version", "")
        if not name or not version:
            continue

        # license can be a string, a list of strings, or a {type: ...} object.
        license_field = info.get("license")
        if isinstance(license_field, list):
            license_str: Optional[str] = " OR ".join(str(x) for x in license_field) or None
        elif isinstance(license_field, str):
            license_str = license_field
        elif isinstance(license_field, dict):
            license_str = license_field.get("type")
        else:
            license_str = None

        key = (name, version)
        is_direct = name in direct
        existing = seen.get(key)
        if existing is None or (is_direct and not existing["direct"]):
            seen[key] = {
                "name": name,
                "version": version,
                "ecosystem": "npm",
                "application_layer": "frontend",
                # Direct deps are *declared* in package.json; the exact
                # version we report was *resolved* from package-lock.json.
                # Transitive deps aren't declared anywhere by the user;
                # leave declared_in empty for them.
                "declared_in": "frontend/package.json" if is_direct else None,
                "resolved_from": "frontend/package-lock.json",
                "direct": is_direct,
                "license": license_str,
            }
    return list(seen.values())


# ---------------------------------------------------------------------------
# Top-level builder + cache.
# ---------------------------------------------------------------------------

def _build_sbom(app_version: str) -> Dict[str, Any]:
    backend = _list_backend_components()
    frontend = _list_frontend_components()
    all_components: List[Dict[str, Any]] = backend + frontend
    # Direct first, then alpha — opens the page on packages the user picked.
    all_components.sort(key=lambda c: (not c["direct"], c["name"].lower()))

    direct_count = sum(1 for c in all_components if c["direct"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": app_version,
        "manifests": {
            "python": str(_BACKEND_REQUIREMENTS_PATH) if _BACKEND_REQUIREMENTS_PATH.exists() else None,
            "npm": str(_FRONTEND_LOCK_PATH) if _FRONTEND_LOCK_PATH.exists() else None,
        },
        "summary": {
            "total": len(all_components),
            "direct": direct_count,
            "transitive": len(all_components) - direct_count,
            "backend": len(backend),
            "frontend": len(frontend),
        },
        "components": all_components,
    }


def get_sbom(app_version: str) -> Dict[str, Any]:
    """Public entry point.  Returns the SBOM, memoised by (manifest mtimes,
    app_version).

    Including app_version in the key means a deploy that bumps
    platform_version.json without changing the manifests still returns a
    fresh envelope — otherwise the cached SBOM would report a stale
    ``app_version`` and ``generated_at`` after the version bump.
    """
    global _cache, _cache_key
    key = (
        _mtime(_BACKEND_REQUIREMENTS_PATH),
        _mtime(_FRONTEND_LOCK_PATH),
        app_version,
    )
    if _cache is not None and _cache_key == key:
        return _cache
    result = _build_sbom(app_version)
    _cache = result
    _cache_key = key
    return result
