#!/usr/bin/env python3
"""The report-template files an operator installs on the server, one path per line.

    template_assets.py <project-root>

Each template's ``report-templates/<name>/template.json`` declares the images
it expects (``assets``: a logo, a cover …).  They are not in version control,
so a fresh source tree has none: ``upgrade-instance.sh`` carries them from the
old folder, ``backup-db.sh`` archives them and ``restore-db.sh`` puts them
back — all three through this ONE list (review 2026-10-23, R-OPS4).  Files
uploaded on the Reports page are elsewhere (``uploads/template_assets``) and go
with uploads/.

Printed relative to the project root (``report-templates/<name>/<path>``),
declared or not present alike — the caller looks for each file where it needs
it.  A path that is absolute, climbs out (``..``) or holds any character
outside ``[A-Za-z0-9_.-/]`` is never printed.  Standard library only (host
``python3``); a manifest that cannot be read is skipped.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

SAFE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$")


def _read(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def declared(project_root: pathlib.Path) -> list[str]:
    root = project_root / "report-templates"
    found: list[str] = []
    for manifest in sorted(root.glob("*/template.json")):
        own = _read(manifest)
        if own is None:
            continue
        assets = own.get("assets")
        # A template that extends another and declares no assets of its own
        # expects its base's list (the renderer's rule: a manifest key it
        # leaves out is the base's) — but the FILES are its own: branding is
        # never inherited, so the paths are under this template's folder.
        # One level.
        base = own.get("extends")
        if assets is None and isinstance(base, str) and SAFE.match(base) and "/" not in base:
            assets = (_read(root / base / "template.json") or {}).get("assets")
        for a in assets if isinstance(assets, list) else []:
            path = str(a.get("path") or "") if isinstance(a, dict) else ""
            if SAFE.match(path) and ".." not in path.split("/"):
                found.append(f"report-templates/{manifest.parent.name}/{path}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    for rel in declared(pathlib.Path(argv[1])):
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
