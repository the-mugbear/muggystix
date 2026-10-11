"""Every import format records the hosts it says something about.

Reported from production (2026-10-10): a dnsx upload over a project of known
hosts showed no host figures on the Scans page, no hosts on the scan's page
and nothing under the Hosts filter by scan.  The parser attached its answers
to hosts that ALREADY existed and wrote no ``host_scan_history`` row for
them, and that table is what every "which hosts did this scan touch" reader
asks.  The owner: "There may be other tools that exhibit the same behavior
when uploaded we haven't tested yet."  This file asks every format.

The contract, per ``file_type`` of ``app.services.format_registry.FORMATS``:

(a) a first import into an empty project leaves a history row, for that scan,
    on every host that exists afterwards;
(b) the same content imported AGAIN as a second scan — every host is known by
    then, which is the case the owner hit — leaves a history row for the
    SECOND scan on every host that scan wrote anything about.  "Wrote
    anything about" is read from the data (``WROTE_ABOUT_A_HOST`` below: every
    table with a scan pointer that hangs off a host), never from the parser's
    own bookkeeping; and on every address the fixture names, because a file
    that addresses a host it then changes nothing on still addressed it;
(c) the second import creates no host and claims none (``host_created``);
(d) the owner's exact case: the hosts were brought by ANOTHER tool first.

``VARIANTS`` holds a second file for a format whose first one does not reach
every way its parser attaches something to a host; a gap found there and left
to its owner is a strict xfail naming it.  The last test deletes each import
by both delete paths: the history row asked for here is what the hand delete
starts from, and a host another scan brought must stay.

A format with no case here fails ``test_every_format_has_a_case``; the only
way out is ``FORMATS_THAT_NAME_NO_HOST_BY_SCAN``, with its reason, which
``test_an_excepted_format_writes_nothing_about_a_host_by_scan`` then holds it
to.

The imports run through the real job path (``tests/ingestion_job_harness``:
a queued job, the worker's claim, the dispatcher, the parser, the heartbeat's
commits) with ``format_override`` naming the format, so each registry key is
exactly one parser and the Nessus service's own entry is covered like the
rest.  The harness queues the job row itself, so the upload's duplicate guard
is not involved in the second import.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db import models
from app.services import ingestion_service as ingestion_module
from app.services.format_registry import FORMATS, resolve_parser
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import (
    build_parser_dispatch_map,
    delete_partial_scan,
    hosts_scan_brought,
)
from tests.ingestion_job_harness import run_file
from tests.test_ingestion_partial_scan import (
    _GNMAP,
    THREE,
    _heartbeat_cases,
    nessus_file,
    nmap_file,
)
from tests.test_parser_followups_heartbeat import _read_phase_cases

NATIVE = Path(__file__).parent / "fixtures" / "native"


# ---------------------------------------------------------------------------
# What "this scan wrote something about this host" means, from the schema
# ---------------------------------------------------------------------------

#: Every column of ``app/db/models*.py`` that points at a scan and hangs off a
#: host, as a statement answering host ids for ``:scan``.  The list is
#: ``grep -n scan_id app/db/models*.py`` less:
#:   * ``host_scan_history`` itself (the thing being checked);
#:   * ``scan_info``, ``ingestion_jobs.scan_id`` / ``in_progress_scan_id``
#:     (about the scan or the job, no host);
#:   * ``annotations.scan_id`` (a person's note on a scan, not an import);
#:   * ``conflict_history.previous_scan_id`` (the scan that LOST, i.e. an
#:     earlier one);
#:   * ``dns_records.scan_id`` — handled in ``_dns_records_about_hosts``: a
#:     record has no host column, it maps to a host through its address.
#: ``network_attributions`` (RDAP) carries NO scan pointer at all.
WROTE_ABOUT_A_HOST: Dict[str, str] = {
    "hosts_v2.last_updated_scan_id":
        "SELECT id FROM hosts_v2 WHERE last_updated_scan_id = :scan",
    "ports_v2.last_updated_scan_id":
        "SELECT host_id FROM ports_v2 WHERE last_updated_scan_id = :scan",
    "ports_v2.created_scan_id":
        "SELECT host_id FROM ports_v2 WHERE created_scan_id = :scan",
    "port_scan_history":
        "SELECT p.host_id FROM port_scan_history h JOIN ports_v2 p ON p.id = h.port_id WHERE h.scan_id = :scan",
    "scripts_v2.scan_id":
        "SELECT p.host_id FROM scripts_v2 s JOIN ports_v2 p ON p.id = s.port_id WHERE s.scan_id = :scan",
    "script_sightings":
        "SELECT p.host_id FROM script_sightings g JOIN scripts_v2 s ON s.id = g.script_id "
        "JOIN ports_v2 p ON p.id = s.port_id WHERE g.scan_id = :scan",
    "host_scripts_v2.scan_id":
        "SELECT host_id FROM host_scripts_v2 WHERE scan_id = :scan",
    "host_script_sightings":
        "SELECT s.host_id FROM host_script_sightings g JOIN host_scripts_v2 s ON s.id = g.host_script_id "
        "WHERE g.scan_id = :scan",
    "host_attributes.scan_id":
        "SELECT host_id FROM host_attributes WHERE scan_id = :scan",
    "host_attribute_sightings":
        "SELECT a.host_id FROM host_attribute_sightings g JOIN host_attributes a ON a.id = g.host_attribute_id "
        "WHERE g.scan_id = :scan",
    "vulnerabilities.scan_id":
        "SELECT host_id FROM vulnerabilities WHERE scan_id = :scan",
    "vulnerabilities.last_seen_scan_id":
        "SELECT host_id FROM vulnerabilities WHERE last_seen_scan_id = :scan",
    "vulnerability_sightings":
        "SELECT v.host_id FROM vulnerability_sightings g JOIN vulnerabilities v ON v.id = g.vulnerability_id "
        "WHERE g.scan_id = :scan",
    "web_interfaces":
        "SELECT host_id FROM web_interfaces WHERE scan_id = :scan AND host_id IS NOT NULL",
    "web_paths":
        "SELECT host_id FROM web_paths WHERE scan_id = :scan",
    "netexec_results":
        "SELECT host_id FROM netexec_results WHERE scan_id = :scan",
    "host_confidence":
        "SELECT host_id FROM host_confidence WHERE scan_id = :scan",
    "port_confidence":
        "SELECT p.host_id FROM port_confidence c JOIN ports_v2 p ON p.id = c.port_id WHERE c.scan_id = :scan",
    "conflict_history.new_scan_id (host)":
        "SELECT host_id FROM conflict_history WHERE new_scan_id = :scan AND host_id IS NOT NULL",
    "conflict_history.new_scan_id (port)":
        "SELECT p.host_id FROM conflict_history c JOIN ports_v2 p ON p.id = c.port_id WHERE c.new_scan_id = :scan",
}


def _reverse_name(address: str) -> Optional[str]:
    parts = address.split(".")
    return ".".join(reversed(parts)) + ".in-addr.arpa" if len(parts) == 4 else None


def _dns_records_about_hosts(db, pid: int, scan_id: int) -> Set[int]:
    """Hosts a DNS record of the scan maps to: the record's value is the
    host's address (A / AAAA), or its owner is (a PTR asked by address, or by
    the address's reverse name)."""
    by_address = dict(
        db.query(models.Host.ip_address, models.Host.id).filter(models.Host.project_id == pid)
    )
    by_reverse = {_reverse_name(a): i for a, i in by_address.items() if _reverse_name(a)}
    found: Set[int] = set()
    for domain, value in db.query(models.DNSRecord.domain, models.DNSRecord.value).filter(
        models.DNSRecord.scan_id == scan_id
    ):
        for token in (value, domain):
            token = (token or "").rstrip(".").lower()
            if token in by_address:
                found.add(by_address[token])
            if token in by_reverse:
                found.add(by_reverse[token])
    return found


def hosts_written_about(db, pid: int, scan_id: int) -> Dict[str, Set[str]]:
    """{address: {the tables holding something of this scan about the host}}."""
    db.expire_all()
    address_of = {
        host_id: address
        for host_id, address in db.query(models.Host.id, models.Host.ip_address).filter(
            models.Host.project_id == pid
        )
    }
    out: Dict[str, Set[str]] = {}
    for table, statement in WROTE_ABOUT_A_HOST.items():
        for (host_id,) in db.execute(text(statement), {"scan": scan_id}):
            if host_id in address_of:
                out.setdefault(address_of[host_id], set()).add(table)
    for host_id in _dns_records_about_hosts(db, pid, scan_id):
        out.setdefault(address_of[host_id], set()).add("dns_records")
    return out


def history_of(db, scan_id: int) -> Dict[str, bool]:
    """{address: host_created} of the scan's ``host_scan_history`` rows."""
    db.expire_all()
    return dict(
        db.query(models.Host.ip_address, models.HostScanHistory.host_created)
        .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
        .filter(models.HostScanHistory.scan_id == scan_id)
    )


def host_addresses(db, pid: int) -> List[str]:
    db.expire_all()
    return sorted(
        row[0] for row in db.query(models.Host.ip_address).filter(models.Host.project_id == pid)
    )


# ---------------------------------------------------------------------------
# One fixture per format
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """``build(tmp_path, name)`` writes the file and returns its path;
    ``addresses`` are the host addresses the file names; ``source`` says where
    the content comes from (the report quotes it)."""
    filename: str
    build: Callable[[Path, str], Path]
    addresses: Sequence[str]
    source: str


def _text(content: str) -> Callable[[Path, str], Path]:
    def build(tmp_path: Path, name: str) -> Path:
        path = tmp_path / name
        path.write_text(content)
        return path
    return build


def _net(prefix: str, count: int = 6) -> List[str]:
    return [f"{prefix}.{i + 1}" for i in range(count)]


def _borrowed(cases, module: str, filename: str):
    """The sample another test file already runs through this parser."""
    for mod, _cls, name, content in cases:
        if mod == module and name == filename:
            return content
    raise KeyError((module, filename))  # pragma: no cover — the lender changed


_COMMIT = _heartbeat_cases()       # tests/test_ingestion_partial_scan.py
_READ = _read_phase_cases()        # tests/test_parser_followups_heartbeat.py
_PARTIAL = "tests/test_ingestion_partial_scan.py"
_FOLLOWUP = "tests/test_parser_followups_heartbeat.py"


def _second_target(capture: str, address: str, other: str) -> str:
    """A native capture is one target; the same capture again with the
    target's address changed makes the second."""
    assert address in capture
    return capture.replace(address, other)


def _lines_of(path: Path) -> List[str]:
    """A capture's lines, each ending in a newline (EyeWitness ends its
    Requests.csv without one)."""
    return [line + "\n" for line in path.read_text().splitlines()]


def _nikto_json() -> str:
    hosts = json.loads((NATIVE / "nikto-all.json").read_text())
    second = json.loads(_second_target(json.dumps(hosts), "172.30.77.20", "172.30.77.21"))
    return json.dumps(hosts + second)


def _nikto_text() -> str:
    capture = (NATIVE / "nikto-all.txt").read_text()
    return capture + _second_target(capture, "172.30.77.20", "172.30.77.21")


def _nikto_csv() -> str:
    lines = _lines_of(NATIVE / "nikto.csv")
    return "".join(lines) + _second_target("".join(lines[1:]), "192.168.7.222", "192.168.7.223")


def _eyewitness_csv() -> str:
    lines = _lines_of(NATIVE / "eyewitness.csv")
    return "".join(lines) + _second_target("".join(lines[1:]), "192.168.7.245", "192.168.7.246")


def _eyewitness_zip(tmp_path: Path, name: str) -> Path:
    """What EyeWitness leaves in its output directory, zipped: Requests.csv
    (the parser takes screenshots too; none are needed to name a host)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("Requests.csv", _eyewitness_csv())
    path = tmp_path / name
    path.write_bytes(buffer.getvalue())
    return path


_GNMAP_TWO = _GNMAP.replace(
    "# Nmap done",
    "Host: 10.41.0.2 (ns.example.test)\tStatus: Up\n"
    "Host: 10.41.0.2 (ns.example.test)\tPorts: 53/open/tcp//domain///\n"
    "# Nmap done",
)

# The shapes of artifacts/manual/{amass,netexec,smbmap}_sample.json — the
# operator-local samples the dispatcher test routes (git-ignored, so the
# content is repeated here).  Written to fit the parsers, not captured.
_AMASS_JSON = json.dumps([
    {"name": "vpn.lab.example", "addresses": ["10.73.0.1"], "tag": "cert"},
    {"name": "git.lab.example", "addresses": ["10.73.0.2"], "tag": "api"},
])
_NETEXEC_JSON = json.dumps({
    "10.74.0.1": {"ADMIN$": {"read": True, "write": False}, "Software": {"read": True, "write": True}},
    "10.74.0.2": {"NETLOGON": {"read": True, "write": False}, "SYSVOL": {"read": True, "write": False}},
})
_SMBMAP_JSON = json.dumps([
    {"ip": "10.75.0.1", "name": "FILE01", "shares": [
        {"name": "ADMIN$", "permissions": "NO ACCESS"}, {"name": "Data", "permissions": "READ, WRITE"}]},
    {"host": "10.75.0.2", "name": "DC01", "shares": [{"name": "SYSVOL", "permissions": "READ ONLY"}]},
])


_DNS_CSV_FORWARD = _borrowed(_COMMIT, "dns_parser", "dns.csv")      # record_type,name,address / A,…


def _nmap(tmp_path: Path, name: str) -> Path:
    return nmap_file(tmp_path, 2, name=name)


def _nessus(tmp_path: Path, name: str) -> Path:
    return nessus_file(tmp_path, THREE, name=name)


def _copy(source: Path) -> Callable[[Path, str], Path]:
    return lambda tmp_path, name: _text(source.read_text())(tmp_path, name)


CASES: Dict[str, Case] = {
    # --- ports -------------------------------------------------------------
    "nmap_xml": Case("sweep.xml", _nmap, ["10.20.0.1", "10.20.0.2"], f"{_PARTIAL} nmap_file"),
    "nmap_gnmap": Case("out.gnmap", _text(_GNMAP_TWO), ["10.41.0.1", "10.41.0.2"],
                       f"{_PARTIAL} _GNMAP + a second host in the same two lines"),
    "gnmap_txt": Case("out.txt", _text(_GNMAP_TWO), ["10.41.0.1", "10.41.0.2"],
                      f"{_PARTIAL} _GNMAP + a second host in the same two lines"),
    "masscan_xml": Case("masscan.xml", _text(_borrowed(_READ, "masscan_parser", "masscan.xml")),
                        _net("10.80.0"), _FOLLOWUP),
    "masscan_json": Case("masscan.json", _text(_borrowed(_READ, "masscan_parser", "masscan.json")),
                         _net("10.80.1"), _FOLLOWUP),
    "masscan_list": Case("masscan.txt", _text(_borrowed(_READ, "masscan_parser", "masscan.txt")),
                         _net("10.80.2"), _FOLLOWUP),
    "naabu_json": Case("naabu.jsonl", _text(_borrowed(_READ, "naabu_parser", "naabu.jsonl")),
                       _net("10.81.0"), _FOLLOWUP),
    "naabu_output": Case("naabu.txt", _text(_borrowed(_READ, "naabu_parser", "naabu.txt")),
                         _net("10.81.1"), _FOLLOWUP),
    "rustscan_output": Case("rustscan.txt", _text(_borrowed(_READ, "rustscan_parser", "rustscan.txt")),
                            _net("10.85.0"), _FOLLOWUP),
    # --- scanner observations ---------------------------------------------
    "nessus_xml": Case("batch.nessus", _nessus, [ip for ip, _ in THREE], f"{_PARTIAL} nessus_file(THREE)"),
    "openvas_xml": Case("openvas.xml", _text(_borrowed(_COMMIT, "openvas_parser", "openvas.xml")),
                        _net("10.60.0"), _PARTIAL),
    "nikto_json": Case("nikto.json", _text(_nikto_json()), ["172.30.77.20", "172.30.77.21"],
                       "fixtures/native/nikto-all.json (capture) + the same target at a second address"),
    "nikto_csv": Case("nikto.csv", _text(_nikto_csv()), ["192.168.7.222", "192.168.7.223"],
                      "fixtures/native/nikto.csv (capture) + the same rows at a second address"),
    "nikto_output": Case("nikto.txt", _text(_nikto_text()), ["172.30.77.20", "172.30.77.21"],
                         "fixtures/native/nikto-all.txt (capture) + the same report at a second address"),
    "nuclei_json": Case("nuclei.json", _copy(NATIVE / "nuclei-je.json"),
                        ["10.20.0.5", "10.20.0.7", "10.20.0.9"], "fixtures/native/nuclei-je.json (capture)"),
    # --- web ---------------------------------------------------------------
    "httpx_json": Case("httpx.jsonl", _text(_borrowed(_COMMIT, "httpx_parser", "httpx.jsonl")),
                       _net("10.61.0"), _PARTIAL),
    "whatweb_json": Case("whatweb.json", _text(_borrowed(_COMMIT, "whatweb_parser", "whatweb.json")),
                         _net("10.63.0"), _PARTIAL),
    "testssl_json": Case("testssl.json", _text(_borrowed(_COMMIT, "testssl_parser", "testssl.json")),
                         _net("10.64.0"), _PARTIAL),
    "eyewitness_json": Case("eyewitness.json",
                            _text(_borrowed(_COMMIT, "eyewitness_parser", "eyewitness.json")),
                            _net("10.68.0"), _PARTIAL),
    "eyewitness_csv": Case("Requests.csv", _text(_eyewitness_csv()), ["192.168.7.245", "192.168.7.246"],
                           "fixtures/native/eyewitness.csv (capture) + the same row at a second address"),
    "eyewitness_zip": Case("eyewitness.zip", _eyewitness_zip, ["192.168.7.245", "192.168.7.246"],
                           "the eyewitness_csv content as Requests.csv in a zip"),
    "dirbuster_json": Case("ffuf.json", _text(_borrowed(_READ, "dirbuster_parser", "ffuf.json")),
                           _net("10.83.0"), _FOLLOWUP),
    "dirbuster_csv": Case("ffuf.csv", _text(_borrowed(_READ, "dirbuster_parser", "ffuf.csv")),
                          _net("10.83.1"), _FOLLOWUP),
    "dirbuster_output": Case("gobuster.txt", _text(_borrowed(_READ, "dirbuster_parser", "gobuster.txt")),
                             _net("10.83.2"), _FOLLOWUP),
    # --- names ------------------------------------------------------------
    # Reverse rows: the DNS CSV makes a host of a PTR row's address only (its
    # forward rows are in VARIANTS).
    "dns_csv": Case("dns.csv", _text(_DNS_CSV_FORWARD.replace("\nA,", "\nPTR,")), _net("10.72.0"),
                    f"{_PARTIAL} dns.csv with its rows as PTR"),
    "dnsx_json": Case("dnsx.jsonl", _text(_borrowed(_COMMIT, "dnsx_parser", "dnsx.jsonl")),
                      _net("10.65.0"), _PARTIAL),
    "amass_json": Case("amass.json", _text(_AMASS_JSON), ["10.73.0.1", "10.73.0.2"],
                       "WRITTEN HERE in the shape of artifacts/manual/amass_sample.json"),
    "amass_output": Case("names.txt", _text(_borrowed(_COMMIT, "amass_parser", "names.txt")),
                         _net("10.70.0"), _PARTIAL),
    # --- access -----------------------------------------------------------
    "netexec_json": Case("nxc-shares.json", _text(_NETEXEC_JSON), ["10.74.0.1", "10.74.0.2"],
                         "WRITTEN HERE in the shape of artifacts/manual/netexec_sample.json"),
    "netexec_output": Case("nxc.txt", _copy(NATIVE / "netexec-wiki-vnc-ftp-ssh.txt"),
                           ["192.168.56.22", "192.168.56.23", "192.168.0.10"],
                           "fixtures/native/netexec-wiki-vnc-ftp-ssh.txt (nxc wiki output)"),
    "smbmap_json": Case("smbmap.json", _text(_SMBMAP_JSON), ["10.75.0.1", "10.75.0.2"],
                        "WRITTEN HERE in the shape of artifacts/manual/smbmap_sample.json"),
    "smbmap_output": Case("smbmap.txt", _text(_borrowed(_READ, "smbmap_parser", "smbmap.txt")),
                          _net("10.84.0"), _FOLLOWUP),
    "bloodhound_json": Case("computers.json",
                            _text(_borrowed(_COMMIT, "bloodhound_parser", "computers.json")),
                            _net("10.69.0"), _PARTIAL),
}

#: Formats that, by design, write nothing by scan about any host.  Each is
#: held to that by ``test_an_excepted_format_writes_nothing_about_a_host_by_scan``.
FORMATS_THAT_NAME_NO_HOST_BY_SCAN: Dict[str, str] = {
    "rdap_json": (
        "An RDAP answer is about a registered ADDRESS BLOCK: it becomes a network_attributions "
        "row, which belongs to the project's block and carries no scan pointer (ingestion.md, "
        "Heartbeats), and hosts are attached to blocks by a project-wide correlation "
        "(host_network_attributions), not by this scan.  The import creates no host and stamps "
        "nothing on one.  Whether 'the lookup covered this host' should count as the scan having "
        "seen it is an owner decision: it would put every host of a /12 on the scan's page."
    ),
}

_RDAP = Case("rdap.ndjson", _text(_borrowed(_READ, "rdap_parser", "rdap.ndjson")),
             [f"10.86.{i}.1" for i in range(6)], _FOLLOWUP)


# ---------------------------------------------------------------------------
# No format without a case
# ---------------------------------------------------------------------------

def test_every_format_has_a_case():
    covered = set(CASES) | set(FORMATS_THAT_NAME_NO_HOST_BY_SCAN)
    missing = sorted(set(FORMATS) - covered)
    assert not missing, (
        f"import formats with no case in this file: {missing} — add one to CASES (a file that "
        "addresses two hosts), or to FORMATS_THAT_NAME_NO_HOST_BY_SCAN with the reason"
    )
    assert not sorted(covered - set(FORMATS)), "a case for a format the registry no longer has"
    assert not set(CASES) & set(FORMATS_THAT_NAME_NO_HOST_BY_SCAN)

    # The dispatcher's own list: every file_type it can emit, and every parser
    # class it can construct (the Nessus service is dispatched on its own path).
    source = Path(ingestion_module.__file__).read_text()
    emitted = set(re.findall(r'\(\s*"([a-z_]+)",\s*[A-Za-z]+,\s*"', source))
    assert emitted and not sorted(emitted - covered), sorted(emitted - covered)
    classes_with_a_case = {resolve_parser(key)[0] for key in covered}
    without = sorted(cls.__name__ for cls in build_parser_dispatch_map() if cls not in classes_with_a_case)
    assert not without, f"dispatchable parsers no format of this file reaches: {without}"

    for file_type, case in CASES.items():
        assert len(set(case.addresses)) >= 2, f"{file_type}: a case addresses at least two hosts"


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _scan_files_in_the_test_folder(monkeypatch, tmp_path):
    """The EyeWitness bundle extracts under the uploads folder by scan id, and
    a scan delete removes that folder: both stay inside this test's own."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))


def _import(db, pid: int, tmp_path: Path, file_type: str, case: Case, copy: int = 1) -> int:
    """Import the case's file through the job path; return the scan id."""
    folder = tmp_path / f"import-{copy}"
    folder.mkdir()
    stem, dot, suffix = case.filename.partition(".")
    name = case.filename if copy == 1 else f"{stem}-{copy}{dot}{suffix}"
    job = run_file(db, pid, case.build(folder, name), name, format_override=file_type)
    assert job.status == "completed", (file_type, job.status, job.error_message, job.message)
    assert job.final_file_type == file_type
    assert job.scan_id is not None, f"{file_type}: the import finished without a scan"
    return job.scan_id


def _known_hosts(db, pid: int, addresses: Sequence[str]) -> int:
    """The addresses as hosts another tool (nmap) brought earlier."""
    scan = models.Scan(project_id=pid, filename="known.xml", tool_name="nmap", scan_type="nmap_xml")
    db.add(scan)
    db.flush()
    dedup = HostDeduplicationService(db)
    for address in addresses:
        host = dedup.find_or_create_host(address, scan.id, {"state": "up"}, project_id=pid)
        dedup.find_or_create_port(host.id, scan.id, {"port_number": 22, "protocol": "tcp", "state": "open"})
    db.commit()
    return scan.id


def _unrecorded(db, pid: int, scan_id: int, addresses: Sequence[str]) -> Dict[str, List[str]]:
    """What the scan has no history row for: {address: what says it should}."""
    history = history_of(db, scan_id)
    missing: Dict[str, List[str]] = {}
    for address, tables in hosts_written_about(db, pid, scan_id).items():
        if address not in history:
            missing.setdefault(address, []).extend(sorted(tables))
    for address in addresses:
        if address not in history:
            missing.setdefault(address, []).append("the file names this address")
    return missing


@pytest.mark.parametrize("file_type", sorted(CASES))
def test_an_import_and_its_repeat_record_every_host_they_touch(db_session, test_project, tmp_path, file_type):
    case, pid = CASES[file_type], test_project.id

    # (a) the first import, into an empty project
    first = _import(db_session, pid, tmp_path, file_type, case)
    hosts = host_addresses(db_session, pid)
    assert set(case.addresses) <= set(hosts), (
        f"{file_type}: the first import did not create the hosts its file names "
        f"({sorted(set(case.addresses) - set(hosts))})"
    )
    first_history = history_of(db_session, first)
    assert sorted(first_history) == hosts, (
        f"{file_type} (a): hosts with no history row for the scan that created them: "
        f"{sorted(set(hosts) - set(first_history))}"
    )
    assert all(first_history.values()), f"{file_type} (a): the first scan created every host"

    # (b) the same content again: every host is known now
    second = _import(db_session, pid, tmp_path, file_type, case, copy=2)
    assert second != first
    missing = _unrecorded(db_session, pid, second, case.addresses)
    assert not missing, (
        f"{file_type} (b): the second import says something about hosts it has no "
        f"host_scan_history row for: {missing}"
    )

    # (c) nothing was created twice, and the second scan claims no host
    assert host_addresses(db_session, pid) == hosts, f"{file_type} (c): the repeat changed the host list"
    claimed = sorted(a for a, created in history_of(db_session, second).items() if created)
    assert not claimed, f"{file_type} (c): the second scan says it created {claimed}"
    # …and the first scan still has its own.
    assert history_of(db_session, first) == first_history


@pytest.mark.parametrize("file_type", sorted(CASES))
def test_an_import_over_hosts_another_tool_brought_records_them(db_session, test_project, tmp_path, file_type):
    """(d) — the report as it was made: nmap first, then this format."""
    case, pid = CASES[file_type], test_project.id
    known_scan = _known_hosts(db_session, pid, case.addresses)
    known_history = history_of(db_session, known_scan)

    scan_id = _import(db_session, pid, tmp_path, file_type, case)

    missing = _unrecorded(db_session, pid, scan_id, case.addresses)
    assert not missing, (
        f"{file_type} (d): the import says something about known hosts it has no "
        f"host_scan_history row for: {missing}"
    )
    history = history_of(db_session, scan_id)
    claimed = sorted(a for a in case.addresses if history[a])
    assert not claimed, f"{file_type} (d): the import says it created hosts nmap brought: {claimed}"
    assert history_of(db_session, known_scan) == known_history


@dataclass(frozen=True)
class Variant:
    """A second file of a format whose first case does not reach one of the
    parser's ways of attaching something to a host.  ``known`` are the hosts
    another tool brought; the file is imported once over them.  ``open_gap``
    names a gap that is REPORTED and not fixed here (the test is then a
    strict xfail: it turns red the day the gap closes, and the mark goes)."""
    file_type: str
    case: Case
    known: Sequence[str]
    open_gap: Optional[str] = None


def _dnsx_zone_transfer() -> str:
    # What `dnsx -axfr -j` writes; the name server (10.9.0.53) handed the zone over.
    from tests.test_parser_followups_dnsx import _axfr_row

    return json.dumps(_axfr_row()) + "\n"


VARIANTS: Dict[str, Variant] = {
    # A forward row (A, name, address) whose address is a known host: the
    # record is stored under the scan and the host is its value, so the row
    # notes the host as dnsx does for the same answer (owner, 2026-10-11; it
    # was the one open gap here until then).
    "dns_csv-forward-rows": Variant(
        "dns_csv",
        Case("dns-forward.csv", _text(_DNS_CSV_FORWARD), _net("10.72.0"), f"{_PARTIAL} dns.csv"),
        known=_net("10.72.0"),
    ),
    # A zone transfer: dns_zone_transfer_allowed is recorded on the NAME
    # SERVER's host, which no answer of the file addresses.
    "dnsx_json-zone-transfer": Variant(
        "dnsx_json",
        Case("axfr.jsonl", _text(_dnsx_zone_transfer()), ["10.9.0.53", "10.9.0.10", "10.9.0.20"],
             "tests/test_parser_followups_dnsx.py _axfr_row"),
        known=["10.9.0.53", "10.9.0.10", "10.9.0.20"],
    ),
}


def _variant_params():
    return [
        pytest.param(
            key,
            marks=[pytest.mark.xfail(strict=True, reason=variant.open_gap)] if variant.open_gap else [],
        )
        for key, variant in sorted(VARIANTS.items())
    ]


@pytest.mark.parametrize("key", _variant_params())
def test_a_variant_file_records_the_known_hosts_it_writes_about(db_session, test_project, tmp_path, key):
    variant, pid = VARIANTS[key], test_project.id
    _known_hosts(db_session, pid, variant.known)

    scan_id = _import(db_session, pid, tmp_path, variant.file_type, variant.case)

    missing = _unrecorded(db_session, pid, scan_id, variant.case.addresses)
    assert not missing, (
        f"{key}: the import says something about known hosts it has no host_scan_history row for: {missing}"
    )


@pytest.mark.parametrize("file_type", sorted(FORMATS_THAT_NAME_NO_HOST_BY_SCAN))
def test_an_excepted_format_writes_nothing_about_a_host_by_scan(db_session, test_project, tmp_path, file_type):
    """The reason an exception gives is checked: with the hosts its file
    covers already known, the import leaves no row of its scan on any of
    them and creates none.  The day one of these formats stamps a host, this
    fails and the format needs a case (or a decision)."""
    assert file_type == "rdap_json"
    pid = test_project.id
    _known_hosts(db_session, pid, _RDAP.addresses)

    scan_id = _import(db_session, pid, tmp_path, file_type, _RDAP)

    assert host_addresses(db_session, pid) == sorted(_RDAP.addresses)
    assert hosts_written_about(db_session, pid, scan_id) == {}
    assert history_of(db_session, scan_id) == {}


# ---------------------------------------------------------------------------
# The delete paths: a host that was there before never goes with a later scan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["cleanup", "route"])
@pytest.mark.parametrize("file_type", sorted(CASES))
def test_deleting_an_import_keeps_the_hosts_it_only_addressed(
    client, db_session, test_project, tmp_path, file_type, path,
):
    """The history row this file requires is also what the hand delete starts
    from (``hosts_scan_brought``: every host the scan has history for).  A
    host another scan brought must stay, by both delete paths, and keep the
    first scan's row."""
    case, pid = CASES[file_type], test_project.id
    known_scan = _known_hosts(db_session, pid, case.addresses)
    scan_id = _import(db_session, pid, tmp_path, file_type, case)
    assert set(case.addresses) <= set(history_of(db_session, scan_id))

    brought = [address for _host_id, address in db_session.execute(hosts_scan_brought(scan_id))]
    assert not set(brought) & set(case.addresses), brought

    db_session.commit()
    if path == "cleanup":
        delete_partial_scan(db_session, scan_id)
        db_session.commit()
    else:
        impact = client.get(f"/api/v1/projects/{pid}/scans/{scan_id}/deletion-impact")
        assert impact.status_code == 200, impact.text
        response = client.delete(f"/api/v1/projects/{pid}/scans/{scan_id}")
        assert response.status_code == 200, response.text
    db_session.expire_all()

    assert db_session.get(models.Scan, scan_id) is None
    assert set(case.addresses) <= set(host_addresses(db_session, pid))
    assert sorted(history_of(db_session, known_scan)) == sorted(case.addresses)
    assert history_of(db_session, scan_id) == {}
