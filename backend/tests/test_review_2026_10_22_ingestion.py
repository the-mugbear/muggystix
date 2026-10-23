"""Comprehensive review 2026-10-22 — the ingestion items.

* A-R6  — masscan ``--banners``: the banners are written with each flush of
  the collected ports (they were held for the whole file and written in one
  loop at its end), with a heartbeat per chunk.
* A-R11 — the cancel route cleans nothing up inside the request and opens no
  second session; the reaper's sweep of failed jobs is the one cleanup.
* B1-1  — one per-record isolation (``parser_utils.write_record`` over
  ``record_savepoint``): a cancel / shutdown / stall raised inside a record
  stops EVERY parser (only gnmap and NetExec did); a lost race is retried
  where the parser asks; a savepoint already rolled back is not a second
  failure.
* B1-2  — the web parsers find hosts through ``ProjectHostsByAddress``:
  a host stored in another spelling of its address is found, a host a
  rolled-back record created is not reused.
* Nitpicks — the dedup working set's port lookups; the masscan JSON reader
  decodes in place (old == new for records split across reads).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db import models
from app.db import session as session_module
from app.parsers import masscan_parser as masscan_module
from app.parsers import parser_utils
from app.parsers.masscan_parser import MasscanParser
from app.parsers.parser_utils import (
    ProjectHostsByAddress,
    ScanHostObservations,
    record_savepoint,
    resolve_host_cached,
    write_record,
)
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import IngestionService, ParseFailure
from tests.ingestion_job_harness import (
    all_scan_ids,
    cancel_from_the_api,
    host_ips,
    job_row,
    kill_worker,
    on_heartbeat,
    queue_file,
    run_file,
    run_until_killed,
)
from tests.test_ingestion_partial_scan import _age_heartbeat, nmap_file


def _scan(db, pid, name="file.txt", tool="masscan"):
    scan = models.Scan(filename=name, tool_name=tool, scan_type="port_scan", project_id=pid)
    db.add(scan)
    db.flush()
    return scan


# ---------------------------------------------------------------------------
# A-R6 — masscan banners are written with each flush, with a heartbeat
# ---------------------------------------------------------------------------

_TS = "1711938600"


def _open(ip, port=80):
    return f"open tcp {port} {ip} {_TS}\n"


def _banner(ip, text, port=80):
    return f"banner tcp {port} {ip} {_TS} http {text}\n"


def _banner_file(tmp_path: Path) -> Path:
    """Flushes every two addresses.  A banner that comes BEFORE its port's
    open line (10.9.0.7), banners after a flush of their port, and one for a
    port that is never open (10.9.0.8)."""
    lines = (
        _banner("10.9.0.7", "early")
        + _open("10.9.0.1") + _banner("10.9.0.1", "b1")
        + _open("10.9.0.2")                                   # flush 1: .1 .2
        + _banner("10.9.0.2", "b2")
        + _open("10.9.0.3") + _open("10.9.0.4")               # flush 2
        + _banner("10.9.0.3", "b3") + _banner("10.9.0.4", "b4")
        + _open("10.9.0.5") + _open("10.9.0.6")               # flush 3
        + _banner("10.9.0.5", "b5") + _banner("10.9.0.6", "b6")
        + _open("10.9.0.7")
        + _banner("10.9.0.8", "never open", port=9999)
    )                                                         # residual: .7
    path = tmp_path / "banners.txt"
    path.write_text(lines)
    return path


def test_masscan_banners_are_written_with_each_flush_and_heartbeat(
    db_session, test_project, tmp_path, monkeypatch,
):
    monkeypatch.setattr(masscan_module, "_COLLECT_FLUSH_IPS", 2)
    monkeypatch.setattr(MasscanParser, "_BANNER_CHUNK", 1)
    progress = []
    from app.services import ingestion_service as ingestion_module
    monkeypatch.setattr(ingestion_module, "report_progress", lambda message=None, *a, **k: progress.append(message))

    parser = MasscanParser(db_session)
    stored_before_each_flush = []
    real_persist = parser._batch_persist

    def persist(scan_id, buf):
        db_session.flush()
        stored_before_each_flush.append(
            db_session.query(models.Script).filter(models.Script.scan_id == scan_id).count()
        )
        return real_persist(scan_id, buf)

    parser._batch_persist = persist  # type: ignore[method-assign]
    scan = parser.parse_file(str(_banner_file(tmp_path)), "banners.txt", project_id=test_project.id)

    # Written as the ports were stored — not all at the end of the file.
    assert stored_before_each_flush == [0, 1, 2, 4]
    assert parser._banners == {}
    outputs = {
        ip: output
        for ip, output in db_session.query(models.Host.ip_address, models.Script.output)
        .join(models.Port, models.Port.host_id == models.Host.id)
        .join(models.Script, models.Script.port_id == models.Port.id)
        .filter(models.Host.project_id == test_project.id, models.Script.script_id == "masscan-http")
    }
    # Every banner whose port is stored, the one read before its port too;
    # the banner of a port that was never open is not.
    assert outputs == {
        "10.9.0.1": "b1", "10.9.0.2": "b2", "10.9.0.3": "b3", "10.9.0.4": "b4",
        "10.9.0.5": "b5", "10.9.0.6": "b6", "10.9.0.7": "early",
    }
    sightings = db_session.query(models.ScriptSighting).filter_by(scan_id=scan.id).count()
    assert sightings == 7
    # A heartbeat per chunk of banner addresses.
    assert any(m and m.endswith("banner addresses") for m in progress), progress


def test_masscan_banner_kept_until_the_last_banner_wins(db_session, test_project, tmp_path, monkeypatch):
    """A banner repeated after its first one was written replaces it — as the
    one write at the end did (the latest banner of a port is kept)."""
    monkeypatch.setattr(masscan_module, "_COLLECT_FLUSH_IPS", 2)
    path = tmp_path / "repeat.txt"
    path.write_text(
        _open("10.9.1.1") + _banner("10.9.1.1", "first") + _open("10.9.1.2")
        + _open("10.9.1.3") + _banner("10.9.1.1", "second") + _open("10.9.1.4")
    )
    MasscanParser(db_session).parse_file(str(path), "repeat.txt", project_id=test_project.id)
    rows = (
        db_session.query(models.Script.output)
        .join(models.Port, models.Port.id == models.Script.port_id)
        .join(models.Host, models.Host.id == models.Port.host_id)
        .filter(models.Host.ip_address == "10.9.1.1", models.Host.project_id == test_project.id)
        .all()
    )
    assert rows == [("second",)]


# ---------------------------------------------------------------------------
# Nitpick — the masscan JSON reader decodes in place: old == new
# ---------------------------------------------------------------------------

_MASSCAN_RECORDS = [
    {"ip": f"10.9.2.{i}", "timestamp": _TS,
     "ports": [{"port": 80 + i, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 64}]}
    for i in range(1, 9)
]


@pytest.mark.parametrize("chunk", [7, 64, 65536])
@pytest.mark.parametrize("shape", ["array", "old"])
def test_masscan_json_records_are_the_same_whatever_the_read_size(tmp_path, monkeypatch, chunk, shape):
    if shape == "array":
        text = "[\n" + ",\n".join(json.dumps(r) for r in _MASSCAN_RECORDS) + "\n]\n"
    else:
        text = "".join(json.dumps(r) + ",\n" for r in _MASSCAN_RECORDS) + "{finished: 1}\n"
    path = tmp_path / "m.json"
    path.write_text(text)
    monkeypatch.setattr(MasscanParser, "_JSON_READ_CHUNK", chunk)
    parser = MasscanParser(None)  # the reader touches no database
    parser._json_unread_after = None
    assert list(parser._iter_json_entries(str(path))) == _MASSCAN_RECORDS
    assert parser._json_unread_after is None


@pytest.mark.parametrize("chunk", [5, 65536])
def test_masscan_json_text_that_is_not_a_record_is_still_told(tmp_path, monkeypatch, chunk):
    text = "[\n" + ",\n".join(json.dumps(r) for r in _MASSCAN_RECORDS[:3]) + ",\nnot a record\n]\n"
    path = tmp_path / "m.json"
    path.write_text(text)
    monkeypatch.setattr(MasscanParser, "_JSON_READ_CHUNK", chunk)
    parser = MasscanParser(None)
    parser._json_unread_after = None
    assert list(parser._iter_json_entries(str(path))) == _MASSCAN_RECORDS[:3]
    assert parser._json_unread_after == 3


# ---------------------------------------------------------------------------
# Nitpick — the dedup working set answers "do I hold this port" by id
# ---------------------------------------------------------------------------

def test_the_working_set_knows_exactly_its_hosts_ports(db_session, test_project):
    pid = test_project.id
    dedup = HostDeduplicationService(db_session)
    scan = _scan(db_session, pid, "a.xml", "nmap")
    host = dedup.find_or_create_host("10.9.3.1", scan.id, {"state": "up"}, project_id=pid)
    other = dedup.find_or_create_host("10.9.3.2", scan.id, {"state": "up"}, project_id=pid)
    ports = [dedup.find_or_create_port(host.id, scan.id, {"port_number": n, "protocol": "tcp"}) for n in (22, 80)]
    foreign = dedup.find_or_create_port(other.id, scan.id, {"port_number": 22, "protocol": "tcp"})
    db_session.commit()

    second = _scan(db_session, pid, "b.xml", "nmap")
    fresh = HostDeduplicationService(db_session)
    fresh.find_or_create_port(host.id, second.id, {"port_number": 22, "protocol": "tcp"})   # loads the set
    new = fresh.find_or_create_port(host.id, second.id, {"port_number": 443, "protocol": "tcp"})
    assert all(fresh._ws_knows_port(p.id) for p in ports)
    assert fresh._ws_knows_port(new.id)
    assert not fresh._ws_knows_port(foreign.id)
    fresh.discard_rolled_back_state()
    assert not fresh._ws_knows_port(ports[0].id)


# ---------------------------------------------------------------------------
# B1-1 — write_record, the one per-record isolation
# ---------------------------------------------------------------------------

def test_a_skipped_record_is_rolled_back_its_notes_restored_and_its_caches_dropped(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    observed = ScanHostObservations()
    dropped = []

    def write():
        host = models.Host(ip_address="10.9.4.1", project_id=pid, state="up")
        db_session.add(host)
        db_session.flush()
        observed.note(host, created=True, state="up")
        raise ValueError("bad record")

    outcome = write_record(db_session, write, observed=observed, on_rollback=lambda: dropped.append(1))
    assert isinstance(outcome.error, ValueError) and outcome.value is None
    assert dropped == [1]
    assert len(observed) == 0
    assert host_ips(db_session, pid) == []
    # The session still works: the next record is written.
    ok = write_record(db_session, lambda: _scan(db_session, pid, "next").id)
    assert ok.error is None and isinstance(ok.value, int)
    assert scan.id is not None


def test_a_cancel_inside_a_record_is_never_a_skipped_record(db_session, test_project):
    calls = []

    def write():
        calls.append(1)
        raise ParseFailure("Job cancelled", user_message="Cancelled by user")

    with pytest.raises(ParseFailure):
        write_record(db_session, write, on_rollback=lambda: calls.append("dropped"), retry_races=True)
    assert calls == [1, "dropped"]   # rolled back, caches dropped, not retried


def test_a_lost_race_is_written_once_more_only_when_asked(db_session):
    def race(log):
        def write():
            log.append(1)
            if len(log) == 1:
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            return "written"
        return write

    asked = []
    assert write_record(db_session, race(asked), retry_races=True) == ("written", None)
    assert asked == [1, 1]
    not_asked = []
    outcome = write_record(db_session, race(not_asked))
    assert isinstance(outcome.error, IntegrityError) and not_asked == [1]


def test_a_savepoint_an_inner_frame_rolled_back_is_not_a_second_failure(db_session, test_project):
    """nmap and gnmap swallowed a failing rollback by hand; the helper does,
    and still restores the notes and drops the caches."""
    observed = ScanHostObservations()
    dropped = []

    def write():
        # An inner frame rolls the record's savepoint back.
        db_session.get_nested_transaction().rollback()
        raise ValueError("after the inner rollback")

    outcome = write_record(db_session, write, observed=observed, on_rollback=lambda: dropped.append(1))
    assert isinstance(outcome.error, ValueError) and str(outcome.error) == "after the inner rollback"
    assert dropped == [1]
    _scan(db_session, test_project.id, "still-usable")


def test_record_savepoint_reraises_for_a_caller_that_does_not_skip(db_session):
    with pytest.raises(ValueError):
        with record_savepoint(db_session):
            raise ValueError("not skipped")


@pytest.fixture
def _uploads_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))


def _stop(*_args, **_kwargs):
    raise ParseFailure("stopped inside a record", user_message="Stopped by the test")


#: Where each format writes one record — patched to raise a ParseFailure
#: (what a cancel, a shutdown or a stall raises).  Before B1-1 every one of
#: these except gnmap and NetExec counted it as a skipped record and carried
#: on.
_RECORD_WRITERS = {
    "nmap_xml": ("app.parsers.nmap_parser.NmapXMLParser._process_host_with_deduplication", True),
    "nmap_gnmap": ("app.parsers.gnmap_parser.GnmapParser._process_host_with_deduplication", True),
    "openvas_xml": ("app.parsers.openvas_parser.OpenVASParser._process_result", True),
    "nuclei_json": ("app.parsers.nuclei_parser.NucleiParser._record", True),
    "testssl_json": ("app.parsers.testssl_parser.bind_hostname", False),
    "nessus_xml": ("app.services.vulnerability_service.VulnerabilityService.process_nessus_vulnerabilities", True),
    "httpx_json": ("app.parsers.httpx_parser.HttpxParser._upsert_record", True),
    "whatweb_json": ("app.parsers.whatweb_parser.WhatwebParser._upsert_record", True),
    "eyewitness_json": ("app.parsers.eyewitness_parser.EyewitnessParser._write_row", True),
    "smbmap_json": ("app.parsers.smbmap_parser.SMBMapParser._write_host", True),
    "dnsx_json": ("app.parsers.dnsx_parser.DnsxParser._persist_row_answers", True),
    "nikto_json": ("app.parsers.nikto_parser.NiktoParser._write_finding", True),
    "dns_csv": ("app.parsers.dns_parser.record_observation", False),
    "naabu_json": ("app.parsers.parser_utils._persist_host_observation_inner", False),
    "netexec_json": ("app.parsers.netexec_parser.NetexecParser._process_json_host_data", True),
}


@pytest.mark.parametrize("file_type", sorted(_RECORD_WRITERS))
def test_a_stop_inside_a_record_stops_every_parser(
    db_session, test_project, tmp_path, monkeypatch, _uploads_in_tmp, file_type,
):
    from tests.test_every_import_records_the_hosts_it_touches import CASES

    target, is_method = _RECORD_WRITERS[file_type]
    module_path, _, attr = target.rpartition(".")
    if is_method:
        module_name, _, class_name = module_path.rpartition(".")
        owner = getattr(__import__(module_name, fromlist=[class_name]), class_name)
    else:
        owner = __import__(module_path, fromlist=[attr])
    assert hasattr(owner, attr), f"{target} is not this format's record writer any more"
    monkeypatch.setattr(owner, attr, _stop)

    case = CASES[file_type]
    folder = tmp_path / "in"
    folder.mkdir()
    job = run_file(db_session, test_project.id, case.build(folder, case.filename), case.filename,
                   format_override=file_type)
    assert job.status == "failed", (file_type, job.status, job.message)
    assert job.error_message == "Stopped by the test", (file_type, job.error_message)


# ---------------------------------------------------------------------------
# B1-2 — the web parsers' hosts come from ProjectHostsByAddress
# ---------------------------------------------------------------------------

def test_a_web_parser_finds_a_host_written_in_another_spelling(db_session, test_project):
    """The backlog's "resolve_host_cached bypasses host_address": an address
    written another way (``2001:DB8::0:1``) is the host every other parser
    stored (``2001:db8::1``) — it was a second host."""
    pid = test_project.id
    dedup = HostDeduplicationService(db_session)
    known = dedup.find_or_create_host("2001:db8::1", _scan(db_session, pid).id, {"state": "up"}, project_id=pid)
    db_session.commit()

    hosts = ProjectHostsByAddress(db_session, pid)
    resolved = resolve_host_cached(db_session, hosts, "2001:DB8::0:1", hostname="v6.example")
    assert resolved.created is False and resolved.host.id == known.id
    assert db_session.query(models.Host).filter_by(project_id=pid).count() == 1


def test_a_row_stored_in_its_old_spelling_is_still_found(db_session, test_project):
    """A row stored before addresses were normalised is found by the spelling
    it was written in, as ``find_or_create_host`` finds it (review S5)."""
    pid = test_project.id
    db_session.add(models.Host(ip_address="2001:DB8::0:3", project_id=pid, state="up"))
    db_session.commit()
    hosts = ProjectHostsByAddress(db_session, pid)
    resolved = resolve_host_cached(db_session, hosts, "2001:DB8::0:3")
    assert resolved.created is False and resolved.host.ip_address == "2001:DB8::0:3"
    # Found again from the map, under the spelling it was asked by.
    assert resolve_host_cached(db_session, hosts, "2001:DB8::0:3").host is resolved.host
    assert db_session.query(models.Host).filter_by(project_id=pid).count() == 1


def test_a_new_host_is_stored_in_the_one_spelling(db_session, test_project):
    hosts = ProjectHostsByAddress(db_session, test_project.id)
    resolved = resolve_host_cached(db_session, hosts, "2001:DB8::0:2")
    assert resolved.created is True and resolved.host.ip_address == "2001:db8::2"


def test_a_host_a_rolled_back_record_created_is_made_again(db_session, test_project):
    """The web parsers cleared their whole host cache after a rollback; the
    map now finds such a host gone by itself."""
    pid = test_project.id
    hosts = ProjectHostsByAddress(db_session, pid)

    def first():
        assert resolve_host_cached(db_session, hosts, "10.9.5.1").created is True
        raise ValueError("the rest of the record failed")

    assert write_record(db_session, first).error is not None
    second = write_record(db_session, lambda: resolve_host_cached(db_session, hosts, "10.9.5.1"))
    assert second.error is None and second.value.created is True
    db_session.flush()
    assert host_ips(db_session, pid) == ["10.9.5.1"]


def test_a_host_another_import_added_since_the_map_was_read_is_found(db_session, test_project):
    pid = test_project.id
    hosts = ProjectHostsByAddress(db_session, pid)
    assert hosts.get("10.9.6.1") is None                 # the map is read
    db_session.add(models.Host(ip_address="10.9.6.1", project_id=pid, state="up"))
    db_session.flush()
    resolved = resolve_host_cached(db_session, hosts, "10.9.6.1")
    assert resolved.created is False
    assert db_session.query(models.Host).filter_by(project_id=pid).count() == 1


def test_a_value_that_is_no_address_is_never_a_host(db_session, test_project):
    hosts = ProjectHostsByAddress(db_session, test_project.id)
    with pytest.raises(ValueError):
        resolve_host_cached(db_session, hosts, "www.example.com")


def test_resolve_host_cached_holds_no_cache_of_its_own():
    """One way to find a host by address during an import: the old per-file
    ``ip -> Host`` dict argument is gone."""
    import inspect

    assert list(inspect.signature(resolve_host_cached).parameters) == ["db", "hosts", "ip", "hostname"]
    assert not hasattr(parser_utils, "_host_cache")


# ---------------------------------------------------------------------------
# A-R11 — the cancel route removes nothing and uses the request's session
# ---------------------------------------------------------------------------

def test_the_cancel_route_cleans_nothing_up_and_opens_no_second_session(
    client, db_session, test_project, tmp_path, monkeypatch,
):
    pid = test_project.id
    worker = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(worker, 2, kill_worker):
        run_until_killed(db_session, worker)
    _age_heartbeat(db_session, job_id)
    db_session.commit()
    assert len(all_scan_ids(db_session)) == 1

    from app.services import ingestion_service as ingestion_module

    def second_session():
        raise AssertionError("the cancel opened a session of its own")

    def cleanup(*_a, **_k):
        raise AssertionError("the cancel ran a cleanup")

    with monkeypatch.context() as patch:
        patch.setattr(session_module, "SessionLocal", second_session)
        patch.setattr(ingestion_module.IngestionService, "_discard_dead_attempt_scan", cleanup)
        response = client.post(f"/api/v1/projects/{pid}/upload/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert len(all_scan_ids(db_session)) == 1          # left to the sweep

    assert worker.reap_orphaned_jobs() == 0
    assert all_scan_ids(db_session) == []
    assert job_row(db_session, job_id).in_progress_scan_id is None


def test_a_cancel_from_the_api_is_on_the_callers_session(db_session, test_project):
    job_id = queue_file(db_session, test_project.id, Path("/nonexistent/x.xml"))
    assert cancel_from_the_api(job_id) is True
    assert job_row(db_session, job_id).status == "failed"
    assert cancel_from_the_api(job_id) is False        # not cancellable twice
