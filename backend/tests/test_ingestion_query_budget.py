"""Ingestion query budget — the dedup hot path must stay linear in host count.

A-Ref-1: `HostDeduplicationService` does a point SELECT per host and per port
(plus a scan-history SELECT each), so re-ingesting a scan is O(hosts + ports).
That is the known cost. What this guards is a *regression* past linear — most
concretely the relationship N+1 that v2.90.3 killed with `noload(...)` in
`find_or_create_host`: drop that suppression and every per-host lookup fans out
into selectin loads for ports/vulns/web-interfaces/scripts, and the marginal
per-host cost jumps. There was no query-budget test on this path at all; this
pins the shape (mirroring test_host_list_query_budget's before_cursor_execute
harness) so the rewrite, if/when it lands, tightens a real number instead of
starting from nothing.
"""
from sqlalchemy import event

from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService
from tests.conftest import engine


def _count_statements(fn) -> int:
    """Count every statement (incl. SAVEPOINT/RELEASE + flushed INSERTs) that
    the shared test engine executes while ``fn`` runs."""
    counter = {"n": 0}

    def _before(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["n"]


def _scan(db, project_id, name):
    scan = models.Scan(project_id=project_id, filename=name, tool_name="nmap")
    db.add(scan)
    db.flush()
    return scan


def _ingest(db, project_id, scan_id, n_hosts, ports_per_host):
    """Drive the dedup service the way a parser does: one host at a time, each
    with a few ports. Commits once at the end (like the parser's periodic flush)."""
    svc = HostDeduplicationService(db)
    for i in range(n_hosts):
        ip = f"10.90.{i // 256}.{i % 256}"
        host = svc.find_or_create_host(
            ip, scan_id, {"state": "up", "hostname": None}, project_id=project_id,
        )
        for p in range(ports_per_host):
            svc.find_or_create_port(
                host.id, scan_id,
                {"port_number": 1000 + p, "protocol": "tcp", "state": "open",
                 "service_name": "http"},
            )
    db.commit()


def test_reingest_query_count_is_linear_in_host_count(db_session, test_project):
    pid = test_project.id
    ports = 3

    # First ingest establishes the rows (create path — not what we're measuring).
    _ingest(db_session, pid, _scan(db_session, pid, "seed.xml").id, 40, ports)

    # Re-ingest the SAME hosts under two later scans of different sizes. The
    # re-ingest is the hot path (existing-host update + per-scan history rows).
    small_scan = _scan(db_session, pid, "rescan-small.xml").id
    big_scan = _scan(db_session, pid, "rescan-big.xml").id
    small_n, big_n = 15, 40

    c_small = _count_statements(
        lambda: _ingest(db_session, pid, small_scan, small_n, ports)
    )
    c_big = _count_statements(
        lambda: _ingest(db_session, pid, big_scan, big_n, ports)
    )

    # Marginal cost per additional re-ingested host. Linear in hosts+ports means
    # this is a stable small constant; an N+1 relationship reload (or any
    # super-linear regression) blows it up. Current path is ~1 host SELECT + 1
    # host-history SELECT + P*(port SELECT + port-history SELECT) + the flushed
    # history INSERTs ≈ a low-teens constant for P=3. The ceiling is generous
    # enough not to be flaky but an order of magnitude below a relationship fan-out.
    # Current path measures ~12 statements/host for P=3 (host SELECT +
    # host-history SELECT + P·(port SELECT + port-history SELECT) + the flushed
    # history INSERTs). The ceiling sits just above that: a relationship
    # fan-out regression (noload lost) adds a selectin query per suppressed
    # relationship per host and pushes this well past the bound.
    marginal_per_host = (c_big - c_small) / (big_n - small_n)
    assert marginal_per_host <= 15, (
        f"re-ingest marginal cost is {marginal_per_host:.1f} statements/host "
        f"(small={c_small} for {small_n}, big={c_big} for {big_n}); expected linear "
        "and low — a jump usually means the find_or_create_host noload() that "
        "suppresses relationship fan-out was lost"
    )
