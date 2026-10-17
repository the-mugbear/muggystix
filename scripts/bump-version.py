#!/usr/bin/env python3
"""Bump the backend and / or frontend version everywhere it is written.

    ./scripts/bump-version.py --backend minor
    ./scripts/bump-version.py --backend patch --frontend minor
    ./scripts/bump-version.py --frontend patch --dry-run

The versions live in four places (CLAUDE.md, "Versions"), and every change
edited them by hand (comprehensive review 2026-10-16, B-OPS-4):

  1. platform_version.json — the source of truth;
  2. frontend/package.json — "version";
  3. docker-compose.yml — every ${APP_VERSION:-…} / ${FRONTEND_VERSION:-…}
     fallback (the BACKEND_VERSION build arg is one of them);
  4. frontend/package-lock.json — its two root "version" fields.

It refuses, changing nothing, when any place already disagrees with
platform_version.json: a drift is fixed by hand first, never carried on.
Stdlib only; runs on the host.  ``--root`` points it at another tree (tests).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PARTS = ("patch", "minor", "major")


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _compose_pattern(variable: str) -> re.Pattern:
    return re.compile(r"(\$\{" + variable + r":-)([0-9]+\.[0-9]+\.[0-9]+)(\})")


def plan(root: Path, backend: str | None, frontend: str | None) -> dict[Path, str]:
    """The new text of each file, or SystemExit naming what disagrees."""
    versions = json.loads((root / "platform_version.json").read_text())
    old = {"backend": versions["backend"], "frontend": versions["frontend"]}
    new = {"backend": bump(old["backend"], backend) if backend else old["backend"],
           "frontend": bump(old["frontend"], frontend) if frontend else old["frontend"]}
    problems = []

    package_path = root / "frontend" / "package.json"
    package = package_path.read_text()
    if f'"version": "{old["frontend"]}"' not in package:
        problems.append(f"frontend/package.json does not say {old['frontend']}")

    lock_path = root / "frontend" / "package-lock.json"
    lock = lock_path.read_text()
    root_versions = [json.loads(lock).get("version"),
                     json.loads(lock).get("packages", {}).get("", {}).get("version")]
    if root_versions != [old["frontend"], old["frontend"]]:
        problems.append(f"frontend/package-lock.json's root versions are {root_versions}, not {old['frontend']}")

    compose_path = root / "docker-compose.yml"
    compose = compose_path.read_text()
    for variable, side in (("APP_VERSION", "backend"), ("FRONTEND_VERSION", "frontend")):
        found = {m.group(2) for m in _compose_pattern(variable).finditer(compose)}
        if found != {old[side]}:
            problems.append(f"docker-compose.yml's ${{{variable}:-…}} fallbacks are {sorted(found) or 'absent'}, "
                            f"not {old[side]}")
    if problems:
        raise SystemExit("Nothing changed — the versions already disagree:\n  " + "\n  ".join(problems))

    out = {root / "platform_version.json": json.dumps(new, indent=2) + "\n"}
    if new["frontend"] != old["frontend"]:
        out[package_path] = package.replace(f'"version": "{old["frontend"]}"', f'"version": "{new["frontend"]}"', 1)
        # The first two "version" fields are the lock's root and its "" package.
        out[lock_path] = lock.replace(f'"version": "{old["frontend"]}"', f'"version": "{new["frontend"]}"', 2)
    compose_new = compose
    for variable, side in (("APP_VERSION", "backend"), ("FRONTEND_VERSION", "frontend")):
        compose_new = _compose_pattern(variable).sub(lambda m, v=new[side]: m.group(1) + v + m.group(3), compose_new)
    if compose_new != compose:
        out[compose_path] = compose_new
    print(f"backend  {old['backend']} -> {new['backend']}")
    print(f"frontend {old['frontend']} -> {new['frontend']}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", choices=PARTS)
    parser.add_argument("--frontend", choices=PARTS)
    parser.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    if not args.backend and not args.frontend:
        parser.error("name what to bump: --backend and / or --frontend")
    changes = plan(args.root, args.backend, args.frontend)
    for path in changes:
        print(f"  {'would write' if args.dry_run else 'writes'} {path.relative_to(args.root)}")
    if not args.dry_run:
        for path, text in changes.items():
            path.write_text(text)
        print("Then run: cd frontend && npx vitest run src/tests/versionConsistency.test.ts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
