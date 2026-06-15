"""Masscan's bulk-SQL service-name merge mirrors the canonical dedup rule.

masscan_parser used last-non-empty-wins (COALESCE(NULLIF(...))), so a re-scan
with a shorter/worse service name clobbered a longer/better one (e.g. an nmap
"http-proxy" overwritten by masscan "http"). It now mirrors
host_deduplication_service.should_replace_service (empty-or-longer-wins). This
pins the two in lockstep.
"""
import json
from collections import defaultdict

import pytest

from app.db import models
from app.parsers.masscan_parser import MasscanParser
from app.services.host_deduplication_service import should_replace_service
from tests.conftest import USING_POSTGRES


# --- Canonical rule (pure) --------------------------------------------------

@pytest.mark.parametrize("existing,new,take_new", [
    (None, "http", True),               # nothing yet -> take new
    ("", "http", True),                 # empty -> take new
    ("http", "http-proxy", True),       # longer -> take new
    ("http-proxy", "http", False),      # shorter -> keep existing
    ("http", "", False),                # new empty -> keep existing
    ("http", "smtp", False),            # same length, no conf edge -> keep
])
def test_should_replace_service_by_name(existing, new, take_new):
    assert should_replace_service(existing, 0, new, 0) is take_new


def test_should_replace_service_by_confidence():
    # Higher-confidence new wins even if same length.
    assert should_replace_service("http", 5, "smtp", 9) is True
    assert should_replace_service("http", 9, "smtp", 5) is False


# --- Masscan bulk SQL mirrors the rule (Postgres-only) ----------------------

@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="MasscanParser uses PostgreSQL-specific batch SQL (ON CONFLICT, "
    "length(), NULLIF) — runs only against the Postgres test DB.",
)
def test_masscan_merge_keeps_longer_name(db_session, test_project):
    parser = MasscanParser(db_session)
    parser._project_id = test_project.id

    s1 = models.Scan(project_id=test_project.id, filename="a.xml", tool_name="masscan", scan_type="port_scan")
    s2 = models.Scan(project_id=test_project.id, filename="b.xml", tool_name="masscan", scan_type="port_scan")
    db_session.add_all([s1, s2])
    db_session.flush()

    hid = parser._upsert_hosts_batch(s1.id, ["10.9.9.9"])["10.9.9.9"]

    def upsert(scan_id, svc):
        parser._upsert_ports_chunk(scan_id, [
            (hid, {"port_number": 8080, "protocol": "tcp", "state": "open", "service_name": svc}),
        ])

    def svc_name():
        db_session.expire_all()
        return db_session.query(models.Port).filter(
            models.Port.host_id == hid, models.Port.port_number == 8080,
        ).first().service_name

    upsert(s1.id, "http-proxy")
    assert svc_name() == "http-proxy"
    # A shorter masscan name must NOT clobber the longer one (the old bug).
    upsert(s2.id, "http")
    assert svc_name() == "http-proxy"
    # A longer/more-specific name DOES replace.
    upsert(s2.id, "http-proxy-alt")
    assert svc_name() == "http-proxy-alt"
    # An empty name never clobbers.
    upsert(s1.id, "")
    assert svc_name() == "http-proxy-alt"


# --- Memory backstop: collection flushes without losing data (review A-4) ----

def test_collect_json_flushes_without_losing_data(db_session, tmp_path, monkeypatch):
    """A pathological scan flushes the in-memory buffer mid-collection to bound
    RAM.  masscan randomizes scan order, so an IP can reappear AFTER its first
    window was flushed — this pins that no ports are lost and the split IP ends
    up with ALL of them (the persist path upserts the re-seen host)."""
    import app.parsers.masscan_parser as mp
    monkeypatch.setattr(mp, "_COLLECT_FLUSH_IPS", 2)

    entries = [
        {"ip": "10.0.0.1", "ports": [{"port": 80, "proto": "tcp", "status": "open"}]},
        {"ip": "10.0.0.2", "ports": [{"port": 22, "proto": "tcp", "status": "open"}]},
        {"ip": "10.0.0.3", "ports": [{"port": 443, "proto": "tcp", "status": "open"}]},
        # 10.0.0.1 reappears after the first window was already flushed+cleared.
        {"ip": "10.0.0.1", "ports": [{"port": 8080, "proto": "tcp", "status": "open"}]},
    ]
    path = tmp_path / "scan.json"
    path.write_text(json.dumps(entries))

    parser = MasscanParser(db_session)
    collected: dict = defaultdict(list)
    flush_sizes = []

    def flush(buf):
        # Mimic _batch_persist: consume then drain the buffer in place.
        flush_sizes.append(len(buf))
        for ip, ports in buf.items():
            collected[ip].extend(ports)
        buf.clear()

    residual = parser._collect_json(str(path), flush)
    for ip, ports in residual.items():
        collected[ip].extend(ports)

    # The low threshold forced at least one mid-collection flush (not all-at-once).
    assert flush_sizes, "expected a mid-collection flush at the low threshold"
    # No data lost, and the split IP carries BOTH ports across the two windows.
    assert {p["port_number"] for p in collected["10.0.0.1"]} == {80, 8080}
    assert {p["port_number"] for p in collected["10.0.0.2"]} == {22}
    assert {p["port_number"] for p in collected["10.0.0.3"]} == {443}
