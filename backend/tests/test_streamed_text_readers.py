"""The DNS CSV and RDAP NDJSON readers stream the file; NetExec refuses a file
too large to read whole (review 2026-10-23, R-ING2).

``dns_parser`` read the whole text (``read_tool_text``) into a ``StringIO``,
``rdap_parser`` the whole file plus a list of its lines, before writing
anything — a zone export or lookup output of several hundred MB ran the 4 GB
worker out of memory, three times over with the retries.

* OLD == NEW: the old readers are kept here, frozen, and every fixture (and
  the edge shapes: UTF-16 with and without a mark, NUL bytes, CRLF and bare
  CR, tab / semicolon delimiters, an RDAP document, an array, NDJSON with a
  bad first or middle line, a cut last line) reads to the same rows.
* LAZY: the readers never read the file whole — the first row is written
  before the file's last line has been read.
"""
from __future__ import annotations

import builtins
import csv
import io
import json
import os
from pathlib import Path

import pytest

from app.parsers import parser_utils
from app.parsers.cut_short import TruncatedFile, begin_read, end_read, settle_cut
from app.parsers.dns_parser import DNSParser
from app.parsers.netexec_parser import NETEXEC_MAX_BYTES, NetexecParser
from app.parsers.parser_utils import read_tool_text
from app.parsers.rdap_parser import RdapParser, _ReadTally
from app.parsers.streaming_json import is_cut_last_line, json_lines

FIXTURES = Path(__file__).parent / "fixtures"
BOM = "﻿"


# ---------------------------------------------------------------- frozen old readers

def old_dns_rows(path):
    with io.StringIO(read_tool_text(str(path)).text.lstrip(BOM), newline='') as csvfile:
        sample = csvfile.read(1024)
        csvfile.seek(0)
        delimiter = ','
        if '\t' in sample:
            delimiter = '\t'
        elif ';' in sample:
            delimiter = ';'
        reader = csv.DictReader(csvfile, delimiter=delimiter)
        return reader.fieldnames, [dict(r) for r in reader]


def new_dns_rows(path):
    delimiter = DNSParser._delimiter(str(path))
    with parser_utils.open_tool_lines(str(path), newline='') as lines:
        reader = csv.DictReader(lines, delimiter=delimiter)
        return reader.fieldnames, [dict(r) for r in reader]


def old_rdap_entries(path):
    """The read phase of rdap_parser before 2026-10-23, verbatim."""
    skipped, warnings = 0, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    entries = []
    try:
        whole = json.loads(content)
    except json.JSONDecodeError:
        whole = RdapParser._document_before_the_cut(content)
    if isinstance(whole, dict):
        entries = [("document", whole)]
    elif isinstance(whole, list):
        entries = [(f"item {i}", item) for i, item in enumerate(whole, start=1)]
    else:
        for lineno, raw_line in enumerate(json_lines(content), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entries.append((f"line {lineno}", json.loads(line)))
            except json.JSONDecodeError as exc:
                if is_cut_last_line(raw_line):
                    settle_cut(len(entries), label="RDAP JSON", in_lines=True, cause=exc)
                    break
                skipped += 1
                if len(warnings) < 5:
                    warnings.append(f"line {lineno}: invalid JSON ({exc})")
    return entries, skipped, warnings


def new_rdap_entries(path):
    read = _ReadTally()
    if RdapParser._is_json_lines(str(path)):
        entries = list(RdapParser._json_lines(str(path), read))
    else:
        entries = RdapParser._document_entries(RdapParser(None), str(path), read)
    return entries, read.skipped, read.warnings


def _outcome(fn, path, keep=False):
    token = begin_read(keep_truncated=keep)
    try:
        return ("ok", fn(path))
    except TruncatedFile as exc:
        return ("cut", exc.records_read)
    finally:
        end_read(token)


# ---------------------------------------------------------------- DNS CSV

DNS_ROWS = "record_type,name,address\nA,www.example.com,10.0.0.1\nPTR,host.example.com,10.0.0.2\n"
DNS_CASES = {
    "plain": DNS_ROWS.encode(),
    "crlf": DNS_ROWS.replace("\n", "\r\n").encode(),
    "bare_cr": DNS_ROWS.replace("\n", "\r").encode(),
    "utf8_bom": (BOM + DNS_ROWS).encode(),
    "utf16_bom": DNS_ROWS.encode("utf-16"),
    "utf16le_no_bom": DNS_ROWS.encode("utf-16-le"),
    "nul_bytes": DNS_ROWS.replace("www", "w\x00ww").encode(),
    "tab": DNS_ROWS.replace(",", "\t").encode(),
    "semicolon": DNS_ROWS.replace(",", ";").encode(),
    "quoted_newline": b'record_type,name,address\nTXT,t.example.com,"a\nb"\n',
    # A delimiter that appears only after the first 1024 characters.
    "late_tab": ("record_type,name,address\n" + "A,x.example.com,10.0.0.9\n" * 60 + "A\ty\t10.0.0.8\n").encode(),
    "empty": b"",
    "many": ("record_type,name,address\n" + "".join(
        f"A,h{i}.example.com,10.1.{i // 256}.{i % 256}\n" for i in range(3000))).encode(),
}


@pytest.mark.parametrize("case", sorted(DNS_CASES))
def test_the_dns_csv_reads_the_same_rows_as_before(tmp_path, case):
    path = tmp_path / f"{case}.csv"
    path.write_bytes(DNS_CASES[case])
    assert new_dns_rows(path) == old_dns_rows(path)


# ---------------------------------------------------------------- RDAP

def _rdap_record(i):
    return {"query": f"10.{i}.0.1", "rdap": {"handle": f"NET-{i}", "startAddress": f"10.{i}.0.0",
                                             "endAddress": f"10.{i}.255.255", "name": f"N{i}"}}


NDJSON = "".join(json.dumps(_rdap_record(i)) + "\n" for i in range(5))
RDAP_CASES = {
    "ndjson": NDJSON.encode(),
    "ndjson_crlf": NDJSON.replace("\n", "\r\n").encode(),
    "ndjson_blank_lines": ("\n" + NDJSON.replace("\n", "\n\n")).encode(),
    "ndjson_bad_middle": (NDJSON + "{not json\n" + json.dumps(_rdap_record(9)) + "\n").encode(),
    "ndjson_bad_first": ("{not json\n" + NDJSON).encode(),
    "ndjson_list_first": ("[1, 2]\n" + NDJSON).encode(),
    "ndjson_cut_last": (NDJSON + json.dumps(_rdap_record(7))[:40]).encode(),
    "ndjson_no_final_newline": NDJSON.rstrip("\n").encode(),
    "one_line": json.dumps(_rdap_record(1)).encode(),
    "pretty_document": json.dumps(_rdap_record(2), indent=2).encode(),
    "array": json.dumps([_rdap_record(i) for i in range(3)], indent=1).encode(),
    "array_cut": json.dumps([_rdap_record(i) for i in range(3)], indent=1)[:-30].encode(),
    "many_bad": ("".join("{bad\n" for _ in range(8)) + NDJSON).encode(),
    "empty": b"",
}
RDAP_FIXTURES = [FIXTURES / "rdap-live.ndjson", FIXTURES / "native" / "rdap-native.json",
                 FIXTURES / "native" / "rdap-compact.json"]


@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize("case", sorted(RDAP_CASES))
def test_rdap_reads_the_same_records_as_before(tmp_path, case, keep):
    path = tmp_path / f"{case}.json"
    path.write_bytes(RDAP_CASES[case])
    assert _outcome(new_rdap_entries, path, keep) == _outcome(old_rdap_entries, path, keep)


@pytest.mark.parametrize("fixture", RDAP_FIXTURES, ids=lambda p: p.name)
def test_rdap_fixtures_read_the_same_records_as_before(fixture):
    assert _outcome(new_rdap_entries, fixture) == _outcome(old_rdap_entries, fixture)


def test_the_helper_scripts_ndjson_takes_the_streamed_path():
    assert RdapParser._is_json_lines(str(FIXTURES / "rdap-live.ndjson"))


# ---------------------------------------------------------------- lazy, never whole

class _Spy:
    """``open`` as the readers call it, counting the lines handed out and
    refusing a whole-file ``read()``."""

    def __init__(self, monkeypatch, module):
        self.lines = 0
        real_open = builtins.open
        spy = self

        class Handle:
            def __init__(self, fh):
                self._fh = fh

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()

            def read(self, n=-1):
                assert n is not None and n >= 0, "the whole file was read"
                return self._fh.read(n)

            def __iter__(self):
                for line in self._fh:
                    spy.lines += 1
                    yield line

        monkeypatch.setattr(module, "open", lambda *a, **k: Handle(real_open(*a, **k)), raising=False)


def test_the_dns_csv_writes_its_first_row_before_the_file_is_read(db_session, test_project, tmp_path, monkeypatch):
    path = tmp_path / "zone.csv"
    # A few hundred rows: each row's savepoint stays on the test's one
    # connection until its teardown, which unwinds them recursively.
    path.write_text("record_type,name,address\n" + "".join(
        f"A,h{i}.example.com,10.1.0.{i}\n" for i in range(1, 250)))
    spy = _Spy(monkeypatch, parser_utils)
    at_first_write = []
    real_write = DNSParser._write_row

    def write_row(self, *args):
        if not at_first_write:
            at_first_write.append(spy.lines)
        return real_write(self, *args)

    monkeypatch.setattr(DNSParser, "_write_row", write_row)
    DNSParser(db_session).parse_file(str(path), "zone.csv", project_id=test_project.id)
    assert at_first_write and at_first_write[0] < 100 < spy.lines


def test_rdap_ndjson_writes_its_first_record_before_the_file_is_read(db_session, test_project, tmp_path, monkeypatch):
    path = tmp_path / "rdap.ndjson"
    real = [ln for ln in (FIXTURES / "rdap-live.ndjson").read_text().splitlines() if ln.strip()]
    path.write_text("".join(real[i % len(real)] + "\n" for i in range(60)))
    spy = _Spy(monkeypatch, parser_utils)
    at_first_ingest = []
    real_ingest = RdapParser._ingest

    def ingest(self, *args):
        if not at_first_ingest:
            at_first_ingest.append(spy.lines)
        return real_ingest(self, *args)

    monkeypatch.setattr(RdapParser, "_ingest", ingest)
    RdapParser(db_session).parse_file(str(path), "rdap.ndjson", project_id=test_project.id)
    assert at_first_ingest and at_first_ingest[0] < 10 < spy.lines


# ---------------------------------------------------------------- NetExec

def test_netexec_refuses_a_file_too_large_to_read_whole(db_session, test_project, tmp_path, monkeypatch):
    from app.services.ingestion_service import ParseFailure

    path = tmp_path / "nxc.txt"
    path.write_text("SMB 10.0.0.1 445 DC01 [*] Windows Server 2019 (name:DC01) (domain:lab)\n")
    real_getsize = os.path.getsize
    monkeypatch.setattr(
        "app.parsers.netexec_parser.os.path.getsize",
        lambda p: NETEXEC_MAX_BYTES + 1 if str(p) == str(path) else real_getsize(p),
    )
    monkeypatch.setattr(
        "app.parsers.netexec_parser.read_tool_text",
        lambda *_a: (_ for _ in ()).throw(AssertionError("read before the refusal")),
    )
    with pytest.raises(ParseFailure) as refused:
        NetexecParser(db_session).parse_file(str(path), "nxc.txt", project_id=test_project.id)
    assert "100 MB" in refused.value.user_message
    assert "Split the capture" in refused.value.user_message


def test_netexec_under_the_limit_imports_as_before(db_session, test_project):
    fixture = FIXTURES / "native" / "netexec-samba.txt"
    assert fixture.stat().st_size < NETEXEC_MAX_BYTES
    scan = NetexecParser(db_session).parse_file(str(fixture), fixture.name, project_id=test_project.id)
    assert scan.id
