#!/usr/bin/env python3
"""A stand-in for the ``docker`` CLI, for the operations-script tests.

``test_ops_scripts.py`` puts a ``docker`` wrapper that runs this file first on
PATH and then runs the REAL ``scripts/deploy.sh`` / ``backup-db.sh`` /
``restore-db.sh`` in a temporary project tree.  Nothing here talks to Docker.

State lives in ``$FAKE_DOCKER_DIR``:

``state.json``   the pretend host — images, containers, the database's schema
                 revision, and the scripted failures.  Read and rewritten on
                 every call, so one command's effect is seen by the next.
``calls.log``    one line per invocation: the arguments, space-joined.

State keys (all optional except ``images`` / ``containers``):

images            {ref: id}            what `docker image inspect` finds
containers        {service: {"id", "image", "status", "restarts"}}
service_images    {service: ref}       what compose builds / names
built_id          id the next successful `compose build` gives backend & co.
healthy_images    [id, …]              the backend answers /health only when
                                       its container runs one of these
alembic_revision  what `SELECT version_num FROM alembic_version` prints
restored_revision what it prints after a successful `pg_restore -d`
fail              [{"match": regex, "rc": n, "times": k}]  a matching call
                  exits n (k times, default always) before doing anything
no_recreate       [regex]  an `up` matching this "succeeds or fails" as
                  scripted but recreates nothing
pending_repairs   text printed by data_repairs.py --pending
dump_body         bytes pg_dump writes (default a complete fake dump)

A fake dump is ``PGDMP`` … ``END``.  ``pg_restore --list`` lists it whenever it
starts with ``PGDMP`` — like the real one, which reads only the table of
contents at the front — and ``pg_restore -f /dev/null`` fails unless it also
ends with ``END``, like the real one on a truncated file.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile

DIR = os.environ["FAKE_DOCKER_DIR"]
STATE = os.path.join(DIR, "state.json")
LOG = os.path.join(DIR, "calls.log")

COMPLETE_DUMP = b"PGDMP fake table of contents\nrow data\nEND"


def load() -> dict:
    with open(STATE, encoding="utf-8") as fh:
        return json.load(fh)


def save(state: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE)


def out(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def tagged(ref: str) -> str:
    """`name` is `name:latest`, as Docker stores it."""
    if ref.startswith("sha256:") or ":" in ref.rsplit("/", 1)[-1]:
        return ref
    return ref + ":latest"


def image_id(state: dict, ref: str) -> str | None:
    images = state.get("images", {})
    if tagged(ref) in images:
        return images[tagged(ref)]
    if ref in images.values():
        return ref
    return None


def pg_restore(args: list[str], state: dict) -> int:
    data = sys.stdin.buffer.read()
    if "--list" in args:
        if not data.startswith(b"PGDMP"):
            return 1
        out("; archive\n1; 0 0 TABLE public hosts\n2; 0 0 TABLE DATA public hosts\n")
        return 0
    if "/dev/null" in args:
        if data.startswith(b"PGDMP") and data.rstrip().endswith(b"END"):
            return 0
        sys.stderr.write("pg_restore: error: could not read from input file: end of file\n")
        return 1
    # a real restore into a database
    state["alembic_revision"] = state.get("restored_revision", state.get("alembic_revision", ""))
    save(state)
    return 0


def compose(args: list[str], state: dict) -> int:
    containers = state.setdefault("containers", {})
    service_images = state.setdefault("service_images", {})
    cmd = args[0] if args else ""
    rest = args[1:]

    if cmd == "version":
        out("Docker Compose version v2.fake\n")
        return 0
    if cmd == "config":
        if "--images" in rest:
            wanted = [a for a in rest if not a.startswith("-")]
            names = wanted or list(service_images)
            lines = []
            for name in names:
                if name != "db" and "db" in service_images:
                    lines.append(service_images["db"])   # the dependency, listed too
                if name in service_images:
                    lines.append(service_images[name])
            out("".join(f"{line}\n" for line in dict.fromkeys(lines)))
            return 0
        out("name: fakeproj\nservices: {}\n")
        return 0
    if cmd == "images":
        for name in [a for a in rest if not a.startswith("-")]:
            if name in containers:
                out(containers[name]["image"] + "\n")
        return 0
    if cmd == "ps":
        for name in [a for a in rest if not a.startswith("-")]:
            if name in containers:
                out(containers[name]["id"] + "\n")
        return 0
    if cmd == "build":
        new_id = state.get("built_id", "sha256:built")
        for name, ref in service_images.items():
            if name != "db":
                state["images"][tagged(ref)] = new_id
        save(state)
        return 0
    if cmd == "pull":
        for name in [a for a in rest if not a.startswith("-")]:
            ref = service_images.get(name)
            if ref:
                state["images"][tagged(ref)] = f"sha256:pulled-{name}"
        save(state)
        return 0
    if cmd == "up":
        joined = " ".join(args)
        if any(re.search(p, joined) for p in state.get("no_recreate", [])):
            return 0
        named = [a for a in rest if not a.startswith("-")]
        for name in named or list(service_images):
            ref = service_images.get(name)
            if not ref:
                continue
            containers[name] = {
                "id": containers.get(name, {}).get("id", f"cid-{name}"),
                "image": state["images"].get(tagged(ref), f"sha256:unknown-{name}"),
                "status": "running",
                "restarts": 0,
            }
        crash = state.get("backend_status_after_up")
        if crash and "backend" in containers and (not named or "backend" in named):
            containers["backend"]["status"] = crash.get("status", "running")
            containers["backend"]["restarts"] = crash.get("restarts", 0)
        save(state)
        return 0
    if cmd in ("stop", "start", "restart"):
        return 0
    if cmd == "down":
        state["containers"] = {}
        save(state)
        return 0
    if cmd == "exec":
        rest = [a for a in rest if a != "-T"]
        service, inner = rest[0], rest[1:]
        if service == "backend":
            if "scripts/data_repairs.py" in inner:
                out(state.get("pending_repairs", ""))
                return 0
            backend = containers.get("backend")
            healthy = backend and backend.get("status") == "running" \
                and backend["image"] in state.get("healthy_images", [])
            return 0 if healthy else 1
        if service == "db":
            if "db" not in containers:
                return 1
            tool = inner[0]
            if tool == "pg_isready":
                return 0
            if tool == "pg_dump":
                sys.stdout.buffer.write(state.get("dump_body", COMPLETE_DUMP.decode()).encode())
                return 0
            if tool == "pg_restore":
                return pg_restore(inner, state)
            if tool == "psql":
                sql = " ".join(inner)
                if "alembic_version" in sql:
                    out(state.get("alembic_revision", "") + "\n")
                elif "pg_locks" in sql:
                    out("0\n")
                elif "SHOW ssl" in sql:
                    out("off\n")
                else:
                    sys.stdin.read()
                return 0
        return 0
    return 0


def main(argv: list[str]) -> int:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + "\n")
    state = load()

    joined = " ".join(argv)
    for rule in state.get("fail", []):
        if re.search(rule["match"], joined):
            times = rule.get("times")
            if times is not None:
                if times <= 0:
                    continue
                rule["times"] = times - 1
                save(state)
            sys.stderr.write(f"fake docker: scripted failure for: {joined}\n")
            return int(rule.get("rc", 1))

    if not argv:
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "compose":
        return compose(rest, state)
    if cmd == "image" and rest[:1] == ["inspect"]:
        ref = next((a for a in rest[1:] if not a.startswith("-") and "{{" not in a), "")
        found = image_id(state, ref)
        if found is None:
            return 1
        fmt = rest[rest.index("--format") + 1] if "--format" in rest else ""
        if ".Id" in fmt:
            out(found + "\n")
        elif "RepoTags" in fmt:
            out("".join(f"{r}\n" for r, i in state["images"].items() if i == found))
        return 0
    if cmd == "image":
        return 0
    if cmd == "inspect":
        cid = next((a for a in rest if not a.startswith("-") and "{{" not in a), "")
        fmt = rest[rest.index("--format") + 1] if "--format" in rest else ""
        for container in state.get("containers", {}).values():
            if container["id"] == cid:
                if ".State.Status" in fmt:
                    out(f"{container.get('status', 'running')} {container.get('restarts', 0)}\n")
                elif ".Mounts" in fmt:
                    out("fakeproj_postgres_data\n")
                elif ".Image" in fmt:
                    out(container["image"] + "\n")
                return 0
        return 1
    if cmd == "tag":
        found = image_id(state, rest[0])
        if found is None:
            return 1
        state["images"][tagged(rest[1])] = found
        save(state)
        return 0
    if cmd == "run":
        if "pg_restore" in rest:
            return pg_restore(rest[rest.index("pg_restore"):], state)
        if "tar" in rest and "czf" in rest:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                info = tarfile.TarInfo("./evidence.txt")
                info.size = 4
                tar.addfile(info, io.BytesIO(b"data"))
            sys.stdout.buffer.write(buf.getvalue())
            return 0
        sys.stdin.buffer.read()
        return 0
    if cmd == "volume" and rest[:1] == ["ls"]:
        out("fakeproj_postgres_data\n")
        return 0
    return 0   # info, images, ps, manifest, builder, network, rmi …


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
