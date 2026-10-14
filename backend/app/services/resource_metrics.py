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

* **The peak, not a sample.**  Parsing a large file can rise and fall between
  two heartbeats; a sampled figure would show a comfortable number for a
  worker killed seconds later.  A report carries the kernel's own high-water
  mark (``memory.peak``); on a kernel without one a worker reports nothing
  rather than a number that cannot catch the case it exists for.
* **Nothing here can fail its caller.**  Every reader returns None and every
  writer swallows its error: a heartbeat must be written whether or not the
  memory could be read, and a metrics page must load on a host that exposes
  none of this.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

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

_last_report: float = 0.0


def _read_int(path: Path) -> Optional[int]:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None       # "max" = no limit


def process_memory(*, require_peak: bool = False) -> Optional[dict]:
    """This container's memory from its cgroup (v2): ``current_bytes``,
    ``peak_bytes``, ``limit_bytes`` (None = no limit) and ``near_limit`` —
    the peak, else the current use, at 90% of the limit or more.  None when
    the cgroup cannot be read, or — with ``require_peak`` — has no peak."""
    current = _read_int(CGROUP / "memory.current")
    if current is None:
        return None
    peak = _read_int(CGROUP / "memory.peak")
    if peak is None and require_peak:
        return None
    limit = _read_int(CGROUP / "memory.max")
    worst = peak if peak is not None else current
    return {
        "current_bytes": current,
        "peak_bytes": peak,
        "limit_bytes": limit,
        "near_limit": bool(limit) and worst >= limit * MEMORY_NEAR_LIMIT_FRACTION,
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
            "(SELECT count(*) FROM pg_stat_activity), "
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


def write_worker_report(name: str) -> None:
    """Leave this worker's memory beside its heartbeat, at most every
    ``REPORT_MIN_INTERVAL_SECONDS``.  Called from the heartbeat: it must never
    raise and never matter — no report is written where the kernel keeps no
    peak, and any failure is silent."""
    global _last_report
    now = time.monotonic()
    if _last_report and now - _last_report < REPORT_MIN_INTERVAL_SECONDS:
        return
    _last_report = now
    try:
        memory = process_memory(require_peak=True)
        if memory is None or not name.replace("-", "").replace("_", "").isalnum():
            return
        folder = _reports_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{name}.json"
        tmp = folder / f".{name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"name": name, "at": time.time(), "memory": memory}))
        os.replace(tmp, target)        # a reader never sees half a report
    except Exception:
        return


def worker_reports() -> List[dict]:
    """Every worker's last report, by name: its memory, how old the report is
    and whether that is too old to be believed (``stale``)."""
    out: List[dict] = []
    try:
        files = sorted(_reports_dir().glob("*.json"))
    except OSError:
        return out
    now = time.time()
    for path in files:
        try:
            data = json.loads(path.read_text())
            memory = data["memory"]
            age = max(0.0, now - float(data["at"]))
            out.append({
                "name": str(data["name"])[:60],
                "age_seconds": round(age, 1),
                "stale": age > REPORT_STALE_SECONDS,
                "current_bytes": int(memory["current_bytes"]),
                "peak_bytes": int(memory["peak_bytes"]),
                "limit_bytes": int(memory["limit_bytes"]) if memory.get("limit_bytes") else None,
                "near_limit": bool(memory.get("near_limit")),
            })
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out
