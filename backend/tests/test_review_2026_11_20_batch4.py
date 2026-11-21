"""Review 2026-11-20, batch 4 — parsers at the stated scale.

* C11 — directory-buster, naabu and RustScan held a dict per record for the
  whole file before the first write; they now write at a bound, and a file
  written in several parts ends as the same rows as one written whole;
* C12 — NetExec's uninterpreted-line receipt substituted every known host
  name into every line, one regular expression each;
* C14 — a JSON file behind a byte-order mark was recognised and then misread;
* T2 — a number no port can have is not written, whichever parser states it.
"""
from __future__ import annotations

import json
import time

import pytest

from app.db import models
from app.parsers import dirbuster_parser, parser_utils
from app.parsers.dirbuster_parser import DirBusterParser
from app.parsers.naabu_parser import NaabuParser
from app.parsers.parser_utils import HostPortBuffer, valid_port_number
from app.parsers.rustscan_parser import RustScanParser
from app.parsers.streaming_json import iter_json_records
from app.services.line_shapes import nxc_line_shape

BOM = b"\xef\xbb\xbf"


# --- the buffer ---------------------------------------------------------------

def test_the_buffer_writes_at_either_bound_and_keeps_nothing_after():
    written: list = []
    buffer = HostPortBuffer(lambda address, ports: written.append((address, len(ports))),
                            max_hosts=2, max_records=100)
    buffer.add("10.0.0.1", {"port_number": 22})
    assert written == []
    buffer.add("10.0.0.2", {"port_number": 22})          # the second address: drained
    assert written == [("10.0.0.1", 1), ("10.0.0.2", 1)]
    buffer.add("10.0.0.1", {"port_number": 80})          # the same address again, in a later part
    buffer.drain()
    assert written[-1] == ("10.0.0.1", 1) and buffer.records == 3
    buffer.drain()                                       # nothing left: nothing written twice
    assert len(written) == 3

    few_hosts_many_ports: list = []
    buffer = HostPortBuffer(lambda address, ports: few_hosts_many_ports.append(len(ports)),
                            max_hosts=1000, max_records=3)
    for port in range(1, 8):
        buffer.add("10.0.0.9", {"port_number": port})
    buffer.drain()
    assert few_hosts_many_ports == [3, 3, 1]


@pytest.mark.parametrize("value, ok", [
    (1, True), (65535, True), ("443", True), (0, False), (65536, False), (-1, False),
    (None, False), ("http", False), (True, False),
])
def test_what_a_port_number_is(value, ok):
    assert valid_port_number(value) is ok


def _ports(db, scan):
    return sorted(
        (host.ip_address, port.port_number)
        for host, port in db.query(models.Host, models.Port)
        .join(models.Port, models.Port.host_id == models.Host.id)
        .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
        .filter(models.HostScanHistory.scan_id == scan.id)
    )


def test_naabu_written_in_parts_is_the_file_written_whole(db_session, test_project, tmp_path, monkeypatch):
    lines = [f"10.70.0.{host}:{port}" for port in (22, 80, 443) for host in range(1, 6)]   # interleaved
    lines.append("10.70.0.1:70000")                                                         # no such port
    path = tmp_path / "naabu.txt"
    path.write_text("\n".join(lines) + "\n")

    original = parser_utils.HostPortBuffer.__init__

    def small(self, write, **kwargs):
        original(self, write, max_hosts=2, max_records=4)

    monkeypatch.setattr(parser_utils.HostPortBuffer, "__init__", small)
    scan = NaabuParser(db_session).parse_file(str(path), "naabu.txt", project_id=test_project.id)
    db_session.flush()

    assert _ports(db_session, scan) == sorted(
        (f"10.70.0.{host}", port) for port in (22, 80, 443) for host in range(1, 6))
    # One history row per host, however many parts it arrived in.
    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count() == 5


def test_rustscan_counts_its_hosts_from_the_scan_and_checks_every_ports_range(
        db_session, test_project, tmp_path, monkeypatch):
    path = tmp_path / "rustscan.txt"
    path.write_text(
        "Open 10.71.0.1:22\nOpen 10.71.0.2:80\nOpen 10.71.0.1:443\n"
        "Open 10.71.0.3:99999\n"                       # an IPv4 line took a number of any size
        "10.71.0.2 -> [8080,70000]\n"
    )
    original = parser_utils.HostPortBuffer.__init__
    monkeypatch.setattr(parser_utils.HostPortBuffer, "__init__",
                        lambda self, write, **kwargs: original(self, write, max_hosts=1, max_records=2))
    parser = RustScanParser(db_session)
    scan = parser.parse_file(str(path), "rustscan.txt", project_id=test_project.id)
    db_session.flush()

    assert _ports(db_session, scan) == [("10.71.0.1", 22), ("10.71.0.1", 443), ("10.71.0.2", 80), ("10.71.0.2", 8080)]
    assert parser.last_parse_stats["summary"] == "4 open ports on 2 hosts"


def test_a_file_of_no_port_lines_is_still_refused(db_session, test_project, tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("nothing here\n")
    with pytest.raises(ValueError):
        NaabuParser(db_session).parse_file(str(path), "naabu.txt", project_id=test_project.id)
    with pytest.raises(ValueError):
        RustScanParser(db_session).parse_file(str(path), "rustscan.txt", project_id=test_project.id)


# --- directory-buster ---------------------------------------------------------

def _ffuf(tmp_path, urls):
    path = tmp_path / "ffuf.json"
    path.write_text(json.dumps({"results": [
        {"url": url, "status": 200, "length": 10 + n} for n, url in enumerate(urls)]}))
    return path


def _web_paths(db, scan):
    return sorted(
        (row.url, row.status_code, row.size)
        for row in db.query(models.WebPath).filter(models.WebPath.scan_id == scan.id)
    )


def test_web_paths_written_in_parts_are_the_file_written_whole(db_session, test_project, tmp_path, monkeypatch):
    urls = [f"http://10.72.0.{host}/p{n}" for n in range(7) for host in (1, 2)]
    urls += ["http://10.72.0.1/p0", "http://10.72.0.2:8080/admin"]        # a repeat, in a later part
    path = _ffuf(tmp_path, urls)

    whole = DirBusterParser(db_session).parse_file(str(path), "ffuf-whole.json", project_id=test_project.id)
    db_session.flush()
    monkeypatch.setattr(dirbuster_parser, "_FLUSH_PATHS", 3)
    monkeypatch.setattr(dirbuster_parser, "_INSERT_CHUNK", 2)
    parser = DirBusterParser(db_session)
    parts = parser.parse_file(str(path), "ffuf-parts.json", project_id=test_project.id)
    db_session.flush()

    assert _web_paths(db_session, parts) == _web_paths(db_session, whole)
    assert len(_web_paths(db_session, parts)) == 15                       # the repeat is one row
    assert len(parser._services) == 3


def test_a_file_with_no_url_is_still_refused(db_session, test_project, tmp_path):
    path = tmp_path / "ffuf.json"
    path.write_text(json.dumps({"results": []}))
    with pytest.raises(ValueError):
        DirBusterParser(db_session).parse_file(str(path), "ffuf.json", project_id=test_project.id)


# --- C12: the receipt is linear in the file ------------------------------------

def test_a_line_shape_is_what_it_was():
    names = ("FILESRV01.corp.example", "FILESRV01", "DC01")
    line = "SMB 10.0.0.5 445 DC01 [*] dc01 trusts FILESRV01 and filesrv01.corp.example, not DC011"
    shape = nxc_line_shape(line, names)
    assert "DC01 " not in shape.replace("<HOST>", "") and "filesrv01" not in shape.lower()
    assert "DC011" in shape                                # a longer word is not the name
    assert nxc_line_shape(line, ()) == nxc_line_shape(line)


def test_ten_thousand_host_names_cost_no_more_per_line_than_ten():
    line = "SMB 10.0.0.5 445 HOST00042 [+] module output naming HOST09999 and nothing else"
    names = tuple(f"HOST{n:05d}" for n in range(10_000))
    nxc_line_shape(line, names)                            # the index is built once per file
    started = time.perf_counter()
    for _ in range(2_000):
        shape = nxc_line_shape(line, names)
    assert time.perf_counter() - started < 2.0             # was minutes: 2,000 x 10,000 substitutions
    assert "HOST09999" not in shape and "HOST00042" not in shape


# --- C14: a byte-order mark is not structure -----------------------------------

def test_a_json_document_behind_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "ffuf.json"
    path.write_bytes(BOM + json.dumps({"results": [{"url": "http://10.0.0.1/a"}, {"url": "http://10.0.0.1/b"}]}).encode())
    records = list(iter_json_records(str(path), array_keys=("results",), tool_label="test"))
    assert [r["url"] for r in records] == ["http://10.0.0.1/a", "http://10.0.0.1/b"]

    array = tmp_path / "array.json"
    array.write_bytes(BOM + b'[{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]')
    assert len(list(iter_json_records(str(array), tool_label="test"))) == 2


@pytest.mark.parametrize("threshold", [None, 1])           # read whole, and streamed by line
def test_json_lines_behind_a_byte_order_mark_keep_their_first_record(tmp_path, threshold):
    path = tmp_path / "naabu.jsonl"
    path.write_bytes(BOM + b'{"ip": "10.0.0.1", "port": 22}\n{"ip": "10.0.0.2", "port": 80}\n')
    records = list(iter_json_records(str(path), tool_label="test", threshold_bytes=threshold))
    assert [r["ip"] for r in records] == ["10.0.0.1", "10.0.0.2"]
