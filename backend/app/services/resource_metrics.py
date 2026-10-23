"""What the host and each process are using — for System settings (v2.496.0).

Read from what a container can already see: ``/proc`` for the host's memory
and load, its own cgroup for the memory it uses against its limit, Postgres
for the database.  No dependency, no Docker socket (that would hand the
application the host), no sampling thread.

**Why per worker.**  Each container has its own memory limit, and the kernel
kills a worker when it reaches ITS limit however much the host has free — a
host figure reads "healthy" while an import is being killed.  So each worker
writes what it uses beside its heartbeat (:func:`write_worker_report`, called
from ``worker_loop.write_heartbeat``), under the uploads mount every container
shares, and the backend reads the reports (:func:`worker_reports`).

Two rules the page depends on:

* **The kernel's own record, not only a sample.**  Parsing a large file can
  rise and fall between two heartbeats; a sampled figure would show a
  comfortable number for a worker killed seconds later.  A report carries
  the kernel's count of processes killed at the limit (``memory.events``
  ``oom_kill``) and its high-water mark (``memory.peak``, shown "since
  start").  A report is written whether or not the memory could be read
  (2.519.0, review 2026-10-21 A2: a kernel without a peak used to mean no
  report at all, and a worker that never reports cannot be seen going
  silent); one per worker INSTANCE (its container), so two replicas of one
  queue do not overwrite each other.  "Near the
  limit" is judged on the processes' OWN memory (``memory.stat`` ``anon``)
  — the peak counts file cache the kernel frees by itself, and one large
  import left the warning red until a restart (third review 2026-10-15,
  S2).
* **Nothing here can fail its caller.**  Every reader returns None and every
  writer swallows its error: a heartbeat must be written whether or not the
  memory could be read, and a metrics page must load on a host that exposes
  none of this.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

CGROUP = Path("/sys/fs/cgroup")
PROC = Path("/proc")

#: Memory "low" on the host: under a tenth of it available.
HOST_MEMORY_LOW_FRACTION = 0.10
#: A process is "near its limit" from this share of it — by its PEAK.
MEMORY_NEAR_LIMIT_FRACTION = 0.90
#: Load is "high" when the 5-minute average exceeds the number of CPUs.
#: Connections are "high" from this share of ``max_connections``.
CONNECTIONS_HIGH_FRACTION = 0.85

#: Where workers leave their reports: under the uploads mount, which the
#: backend and both workers share.  A dot-folder: nothing lists it.
REPORTS_DIR = ".worker-status"
#: A worker rewrites its report at most this often (a job's progress callback
#: can touch the heartbeat many times a second).
REPORT_MIN_INTERVAL_SECONDS = 5.0
#: A report older than this is a worker that is not reporting — killed,
#: stopped, or wedged — and is shown as that, never as a calm worker.
REPORT_STALE_SECONDS = 120.0
#: A report older than this is forgotten: a replica that was scaled away
#: stops being listed (and alerted on) a day after it last spoke.
REPORT_FORGET_SECONDS = 24 * 3600.0
#: When this process started — a worker alerts on a queue with NO report only
#: once it has run long enough for that queue's worker to have written one.
STARTED_AT = time.monotonic()
#: The same moment on the wall clock, written into every report: a silent
#: report is a container that was REPLACED (a rebuild, a recreate), not one
#: that stopped, when another of its queue started after it last spoke.
STARTED_WALL = time.time()

_last_report: float = 0.0


def _read_int(path: Path) -> Optional[int]:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None       # "max" = no limit


def _read_keyed(path: Path) -> Dict[str, int]:
    """A cgroup ``key value`` file (``memory.stat``, ``memory.events``);
    empty when it cannot be read."""
    out: Dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if value.strip().isdigit():
                out[key] = int(value.strip())
    except OSError:
        return {}
    return out


def process_memory() -> Optional[dict]:
    """This container's memory from its cgroup (v2): ``current_bytes``,
    ``own_bytes`` (``memory.stat`` ``anon`` — the processes' own memory,
    which the kernel cannot give back; None where it cannot be read),
    ``peak_bytes`` (the high-water mark since the container started, file
    cache included — information, not a judgement), ``limit_bytes`` (None =
    no limit), ``oom_kills`` (``memory.events``: processes killed at the
    limit since start) and ``near_limit`` — its OWN memory, else its current
    use, at 90% of the limit or more.

    Not the peak (third review 2026-10-15, S2): it counts file cache the
    kernel frees by itself, so one large import left the warning red until a
    restart.  None when the cgroup cannot be read."""
    current = _read_int(CGROUP / "memory.current")
    if current is None:
        return None
    peak = _read_int(CGROUP / "memory.peak")
    limit = _read_int(CGROUP / "memory.max")
    own = _read_keyed(CGROUP / "memory.stat").get("anon")
    judged = own if own is not None else current
    return {
        "current_bytes": current,
        "own_bytes": own,
        "peak_bytes": peak,
        "limit_bytes": limit,
        "oom_kills": _read_keyed(CGROUP / "memory.events").get("oom_kill", 0),
        "near_limit": bool(limit) and judged >= limit * MEMORY_NEAR_LIMIT_FRACTION,
    }


def host_snapshot() -> Optional[dict]:
    """The host's memory and load as this container sees them (``/proc`` is
    the host's): total and available memory, the three load averages and the
    CPU count.  None when neither can be read."""
    memory = {}
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                memory[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        memory = {}
    load = None
    try:
        load = [float(v) for v in (PROC / "loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        load = None
    if len(memory) < 2 and (load is None or len(load) < 3):
        return None
    cpus = os.cpu_count() or None
    total, available = memory.get("MemTotal"), memory.get("MemAvailable")
    have_memory = total is not None and available is not None
    have_load = load is not None and len(load) == 3
    return {
        "memory_total_bytes": total if have_memory else None,
        "memory_available_bytes": available if have_memory else None,
        "memory_low": bool(have_memory and total and available < total * HOST_MEMORY_LOW_FRACTION),
        "load_1": load[0] if have_load else None,
        "load_5": load[1] if have_load else None,
        "load_15": load[2] if have_load else None,
        "cpu_count": cpus,
        "load_high": bool(have_load and cpus and load[1] > cpus),
    }


def database_snapshot(db: Session) -> Optional[dict]:
    """The database's size and its connections in use against the most it
    accepts — one statement.  None when it cannot be asked."""
    try:
        size, used, most = db.execute(text(
            "SELECT pg_database_size(current_database()), "
            # Client connections only: the server's own background processes
            # (checkpointer, WAL writer…) are not what max_connections limits.
            "(SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'), "
            "current_setting('max_connections')::int"
        )).one()
    except Exception:
        db.rollback()
        return None
    return {
        "size_bytes": int(size),
        "connections": int(used),
        "max_connections": int(most),
        "connections_high": bool(most) and used >= most * CONNECTIONS_HIGH_FRACTION,
    }


def _reports_dir() -> Path:
    return Path(settings.UPLOAD_DIR) / REPORTS_DIR


def instance_name() -> str:
    """This worker instance: its container's host name (Docker sets it to
    the container id), letters, digits, ``-`` and ``_`` only."""
    raw = socket.gethostname() or f"pid{os.getpid()}"
    return "".join(c for c in raw if c.isalnum() or c in "-_")[:40] or f"pid{os.getpid()}"


def write_worker_report(name: str) -> None:
    """Leave this worker's report beside its heartbeat — that it is alive,
    and its memory where the kernel says — at most every
    ``REPORT_MIN_INTERVAL_SECONDS``, in a file of its own
    (``<queue>.<instance>.json``).  Called from the heartbeat: it must never
    raise and never matter — any failure is silent."""
    global _last_report
    now = time.monotonic()
    if _last_report and now - _last_report < REPORT_MIN_INTERVAL_SECONDS:
        return
    _last_report = now
    try:
        if not name.replace("-", "").replace("_", "").isalnum():
            return
        instance = instance_name()
        folder = _reports_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{name}.{instance}.json"
        tmp = folder / f".{name}.{instance}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({
            "name": name, "instance": instance, "at": time.time(), "started": STARTED_WALL,
            "memory": process_memory(),
        }))
        os.replace(tmp, target)        # a reader never sees half a report
        # The one-file-per-queue report of before 2.519.0 would read as a
        # worker silent since the upgrade.
        (folder / f"{name}.json").unlink(missing_ok=True)
    except Exception:
        return


def _replaced(reports: List[tuple]) -> set:
    """The paths of silent reports whose container was REPLACED: another
    container of the same queue is reporting and STARTED after the silent one
    last spoke.  That is a rebuild or a recreate (every deploy leaves the old
    container's file behind), not a worker that stopped — so it is neither
    shown as "not reporting" nor alerted on (System settings, 2026-10-22: ten
    such warnings and ten bell alerts after five rebuilds).  A replica that is
    truly stuck is still one: its live siblings started BEFORE its last
    report.  A report without ``started`` (before 2.523.0) replaces nothing."""
    newest_start: dict = {}
    for path, data, age in reports:
        started = data.get("started")
        if age <= REPORT_STALE_SECONDS and isinstance(started, (int, float)):
            name = data["name"]
            newest_start[name] = max(newest_start.get(name, 0.0), float(started))
    return {
        path for path, data, age in reports
        if age > REPORT_STALE_SECONDS and float(data["at"]) < newest_start.get(data["name"], 0.0)
    }


def worker_reports() -> List[dict]:
    """Every worker's last report, by name: its memory, how old the report is
    and whether that is too old to be believed (``stale``).  A report of a
    container that was replaced (``_replaced``) is left out and its file
    removed."""
    out: List[dict] = []
    try:
        files = sorted(_reports_dir().glob("*.json"))
    except OSError:
        return out
    now = time.time()
    read: List[tuple] = []
    for path in files:
        try:
            data = json.loads(path.read_text())
            if "instance" not in data or not isinstance(data.get("name"), str):
                continue                  # a report from before 2.519.0, or not one
            age = max(0.0, now - float(data["at"]))
            if age > REPORT_FORGET_SECONDS:
                continue
            read.append((path, data, age))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    replaced = _replaced(read)
    for path in replaced:
        try:
            path.unlink(missing_ok=True)  # best effort; the next read decides again
        except OSError:
            pass
    for path, data, age in read:
        if path in replaced:
            continue
        try:
            memory = data.get("memory")
            out.append({
                "name": str(data["name"])[:60],
                "instance": str(data["instance"])[:40],
                "age_seconds": round(age, 1),
                "stale": age > REPORT_STALE_SECONDS,
                # Null throughout where the worker could not read its memory.
                "memory_read": memory is not None,
                "current_bytes": int(memory["current_bytes"]) if memory else None,
                "own_bytes": int(memory["own_bytes"]) if memory and memory.get("own_bytes") is not None else None,
                "peak_bytes": int(memory["peak_bytes"]) if memory and memory.get("peak_bytes") is not None else None,
                "limit_bytes": int(memory["limit_bytes"]) if memory and memory.get("limit_bytes") else None,
                "oom_kills": int(memory.get("oom_kills") or 0) if memory else 0,
                "near_limit": bool(memory.get("near_limit")) if memory else False,
            })
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return out
