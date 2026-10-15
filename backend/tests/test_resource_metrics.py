"""What the host and each process are using (v2.496.0) — `resource_metrics`.

Pinned here:

* a worker's report carries the kernel's PEAK, and none is written where
  there is no peak (a sampled figure misses the spike it exists to show);
* nothing a heartbeat calls can raise, whatever the host exposes;
* a report that stopped arriving is said to be stale, never shown as calm;
* the metrics route answers with every block absent on a host that exposes
  none of it.
"""
from __future__ import annotations

import json
import logging
import time

import pytest

from app import worker_loop
from app.services import resource_metrics as rm

GB = 1024 ** 3


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    cgroup, proc, uploads = tmp_path / "cgroup", tmp_path / "proc", tmp_path / "uploads"
    for folder in (cgroup, proc, uploads):
        folder.mkdir()
    monkeypatch.setattr(rm, "CGROUP", cgroup)
    monkeypatch.setattr(rm, "PROC", proc)
    monkeypatch.setattr(rm.settings, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(rm, "_last_report", 0.0)
    return cgroup, proc, uploads


def _cgroup(cgroup, current, peak, limit, own=None, oom_kills=None):
    (cgroup / "memory.current").write_text(f"{current}\n")
    if peak is not None:
        (cgroup / "memory.peak").write_text(f"{peak}\n")
    (cgroup / "memory.max").write_text(f"{limit}\n")
    if own is not None:
        (cgroup / "memory.stat").write_text(f"anon {own}\nfile {max(current - own, 0)}\nkernel 4096\n")
    if oom_kills is not None:
        (cgroup / "memory.events").write_text(f"low 0\nhigh 0\nmax 3\noom 1\noom_kill {oom_kills}\n")


def test_a_process_is_near_its_limit_by_its_own_memory_not_its_peak(fake_host):
    """Third review 2026-10-15, S2: the peak counts file cache the kernel
    frees by itself — after one large import the warning stayed red until a
    restart.  Judged on the processes' own memory (``anon``); the peak and
    the kills at the limit are said beside it."""
    cgroup, _, _ = fake_host
    # A large import read 3.7 GB of file: cache, not the process's.
    _cgroup(cgroup, current=int(3.6 * GB), peak=int(3.7 * GB), limit=4 * GB, own=1 * GB, oom_kills=0)
    assert rm.process_memory() == {
        "current_bytes": int(3.6 * GB), "own_bytes": 1 * GB, "peak_bytes": int(3.7 * GB),
        "limit_bytes": 4 * GB, "oom_kills": 0, "near_limit": False,
    }
    _cgroup(cgroup, current=int(3.8 * GB), peak=int(3.9 * GB), limit=4 * GB, own=int(3.7 * GB), oom_kills=2)
    memory = rm.process_memory()
    assert memory["near_limit"] is True and memory["oom_kills"] == 2
    # No memory.stat: the current use is judged.
    (cgroup / "memory.stat").unlink()
    _cgroup(cgroup, current=int(3.7 * GB), peak=int(3.7 * GB), limit=4 * GB)
    assert rm.process_memory()["near_limit"] is True and rm.process_memory()["own_bytes"] is None
    # No limit ("max"): nothing to be near.
    _cgroup(cgroup, current=1 * GB, peak=2 * GB, limit="max", own=1 * GB)
    assert rm.process_memory()["limit_bytes"] is None and rm.process_memory()["near_limit"] is False


def test_no_cgroup_is_no_answer_and_no_peak_is_no_worker_report(fake_host):
    cgroup, _, uploads = fake_host
    assert rm.process_memory() is None
    _cgroup(cgroup, current=1 * GB, peak=None, limit=4 * GB)
    # The backend's own line can do without the peak; a worker's report cannot.
    assert rm.process_memory()["peak_bytes"] is None
    assert rm.process_memory(require_peak=True) is None
    rm.write_worker_report("ingestion_jobs")
    assert not (uploads / rm.REPORTS_DIR).exists()
    assert rm.worker_reports() == []


def test_a_worker_report_is_written_whole_read_back_and_goes_stale(fake_host, monkeypatch):
    cgroup, _, uploads = fake_host
    _cgroup(cgroup, current=1 * GB, peak=int(3.9 * GB), limit=4 * GB, own=int(3.7 * GB), oom_kills=1)
    rm.write_worker_report("ingestion_jobs")

    folder = uploads / rm.REPORTS_DIR
    assert [p.name for p in folder.iterdir()] == ["ingestion_jobs.json"]       # no temp file left
    (report,) = rm.worker_reports()
    assert report["name"] == "ingestion_jobs" and report["stale"] is False
    assert (report["current_bytes"], report["peak_bytes"], report["limit_bytes"]) == (1 * GB, int(3.9 * GB), 4 * GB)
    assert report["near_limit"] is True and report["own_bytes"] == int(3.7 * GB) and report["oom_kills"] == 1

    # The worker was killed: its last report is still there, and is said to be old.
    data = json.loads((folder / "ingestion_jobs.json").read_text())
    data["at"] = time.time() - rm.REPORT_STALE_SECONDS - 5
    (folder / "ingestion_jobs.json").write_text(json.dumps(data))
    (report,) = rm.worker_reports()
    assert report["stale"] is True and report["age_seconds"] > rm.REPORT_STALE_SECONDS

    # A half-written or foreign file is skipped, not an error.
    (folder / "broken.json").write_text("{not json")
    assert [r["name"] for r in rm.worker_reports()] == ["ingestion_jobs"]


def test_a_report_is_rewritten_at_most_every_few_seconds(fake_host, monkeypatch):
    """A job's progress callback touches the heartbeat many times a second."""
    cgroup, _, uploads = fake_host
    _cgroup(cgroup, current=1 * GB, peak=1 * GB, limit=4 * GB)
    clock = {"now": 1000.0}
    monkeypatch.setattr(rm.time, "monotonic", lambda: clock["now"])
    rm.write_worker_report("report_jobs")
    target = uploads / rm.REPORTS_DIR / "report_jobs.json"
    first = target.read_text()

    _cgroup(cgroup, current=2 * GB, peak=2 * GB, limit=4 * GB)
    clock["now"] += 1
    rm.write_worker_report("report_jobs")
    assert target.read_text() == first
    clock["now"] += rm.REPORT_MIN_INTERVAL_SECONDS
    rm.write_worker_report("report_jobs")
    assert json.loads(target.read_text())["memory"]["peak_bytes"] == 2 * GB


def test_the_heartbeat_is_written_whatever_the_memory_report_does(fake_host, monkeypatch, tmp_path):
    """The container's healthcheck reads the heartbeat: nothing about memory
    may keep it from being written."""
    _, _, uploads = fake_host
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker_loop, "_status_name", "ingestion_jobs")

    worker_loop.write_heartbeat(str(heartbeat), logging.getLogger("test"))     # no cgroup at all
    assert heartbeat.exists() and rm.worker_reports() == []

    # The reports folder cannot be made (a file is in its place): still a heartbeat, no error.
    (uploads / rm.REPORTS_DIR).write_text("in the way")
    _cgroup(fake_host[0], current=1 * GB, peak=1 * GB, limit=4 * GB)
    monkeypatch.setattr(rm, "_last_report", 0.0)
    heartbeat.unlink()
    worker_loop.write_heartbeat(str(heartbeat), logging.getLogger("test"))
    assert heartbeat.exists()

    # A name that is not a plain word never becomes a path.
    (uploads / rm.REPORTS_DIR).unlink()
    monkeypatch.setattr(rm, "_last_report", 0.0)
    rm.write_worker_report("../escape")
    assert not (uploads.parent / "escape.json").exists() and rm.worker_reports() == []

    # Outside a running loop (the API process) nothing is reported.
    monkeypatch.setattr(worker_loop, "_status_name", None)
    monkeypatch.setattr(rm, "_last_report", 0.0)
    worker_loop.write_heartbeat(str(heartbeat), logging.getLogger("test"))
    assert rm.worker_reports() == []


def test_the_host_is_read_from_proc_and_judged(fake_host, monkeypatch):
    _, proc, _ = fake_host
    assert rm.host_snapshot() is None                                          # nothing exposed
    (proc / "meminfo").write_text("MemTotal:       16000000 kB\nMemFree: 1 kB\nMemAvailable:    1000000 kB\n")
    (proc / "loadavg").write_text("9.50 8.25 3.00 2/900 1234\n")
    monkeypatch.setattr(rm.os, "cpu_count", lambda: 4)

    host = rm.host_snapshot()
    assert host["memory_total_bytes"] == 16000000 * 1024 and host["memory_available_bytes"] == 1000000 * 1024
    assert host["memory_low"] is True                                          # 6% available
    assert (host["load_1"], host["load_5"], host["load_15"], host["cpu_count"]) == (9.5, 8.25, 3.0, 4)
    assert host["load_high"] is True                                           # 8.25 over 4 CPUs

    (proc / "meminfo").write_text("MemTotal: 16000000 kB\nMemAvailable: 9000000 kB\n")
    (proc / "loadavg").write_text("0.50 0.75 1.00 1/900 1\n")
    host = rm.host_snapshot()
    assert host["memory_low"] is False and host["load_high"] is False

    # Load without memory is still an answer; the missing half is None, not 0.
    (proc / "meminfo").unlink()
    host = rm.host_snapshot()
    assert host["memory_total_bytes"] is None and host["memory_low"] is False and host["load_5"] == 0.75


def test_the_database_says_its_size_and_its_connections(db_session):
    snapshot = rm.database_snapshot(db_session)
    assert snapshot["size_bytes"] > 0
    assert 1 <= snapshot["connections"] <= snapshot["max_connections"]
    assert snapshot["connections_high"] is (snapshot["connections"] >= snapshot["max_connections"] * 0.85)
    # Client connections only — what max_connections limits; the server's
    # own background processes (checkpointer, WAL writer…) were counted too
    # (review 2026-10-14, nitpick; second review: no test).
    from sqlalchemy import text

    clients, everything = db_session.execute(text(
        "SELECT count(*) FILTER (WHERE backend_type = 'client backend'), count(*) FROM pg_stat_activity"
    )).one()
    if everything > clients:          # background processes are listed on this server
        assert snapshot["connections"] < everything


def test_the_metrics_route_carries_the_blocks_and_loads_where_none_can_be_read(client, fake_host):
    """`client` is a global admin.  This fake host exposes nothing."""
    body = client.get("/api/v1/system/queue-metrics").json()
    assert body["host"] is None and body["backend_memory"] is None and body["workers"] == []
    assert body["database"]["max_connections"] > 0
    assert "ingestion" in body and "report" in body                            # the queues are unaffected

    cgroup, proc, _ = fake_host
    _cgroup(cgroup, current=1 * GB, peak=int(1.5 * GB), limit=2 * GB)
    (proc / "meminfo").write_text("MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\n")
    (proc / "loadavg").write_text("0.10 0.20 0.30 1/10 1\n")
    rm.write_worker_report("ingestion_jobs")
    body = client.get("/api/v1/system/queue-metrics").json()
    assert body["host"]["memory_available_bytes"] == 6000000 * 1024
    assert body["backend_memory"]["limit_bytes"] == 2 * GB
    assert [w["name"] for w in body["workers"]] == ["ingestion_jobs"]
