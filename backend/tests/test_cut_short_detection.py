"""How a file that was CUT SHORT is told from one whose content is INVALID.

The rule (``app/parsers/cut_short.py``): by WHERE the reader fails, never by
the library's exception type, code or message — a failure reported only
after the whole file was handed over means every byte was acceptable and
more were expected.

The evidence that the libraries' own words cannot decide it is here too:

* libxml2 (lxml ``iterparse``: nmap, masscan, OpenVAS) reports a cut with
  dozens of different codes ("AttValue: ' expected", "Couldn't find end of
  Start Tag", "Premature end of data"…), and reports some INVALID content
  only at the end of input as well (a bare ``&`` near the end of a file) —
  so "the reader failed at the end" is necessary and not sufficient, and the
  second half of the question is put to expat;
* expat (``defusedxml`` ``iterparse``: Nessus) fails on an invalid byte at
  the feed that holds it, and says "no element found" / "unclosed token" /
  "partial character" only when told the input is over.

``tests/test_truncated_file_keep.py`` holds the same for the JSON reader
(yajl words some cuts as lexical errors).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
import pytest
from defusedxml.common import DefusedXmlException
from lxml import etree

from app.parsers.cut_short import EndTracking, TruncatedFile, begin_read, end_read, xml_cut_short
from app.parsers.masscan_parser import MasscanParser
from app.parsers.streaming_json import ends_early, is_cut_last_line
from app.parsers.xml_stream_helpers import iterparse_safe


def _host(i: int, pad: int = 0) -> str:
    return (
        f'<host starttime="1"><status state="up"/><address addr="10.0.0.{i}" addrtype="ipv4"/>'
        f'<ports><port protocol="tcp" portid="80"><state state="open"/>'
        f'<service name="http" product="café {"x" * pad}"/></port></ports></host>\n'
    )


def _document(hosts: int = 3, pad: int = 0) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE nmaprun>\n'
        '<nmaprun scanner="nmap" args="nmap -oX" start="1700000000" version="7.94">\n'
        '<scaninfo type="syn" protocol="tcp" numservices="1" services="80"/>\n'
        + "".join(_host(i, pad) for i in range(1, hosts + 1))
        + '<runstats><finished time="1700000100"/></runstats>\n</nmaprun>\n'
    ).encode("utf-8")


def _read(path, reader: str):
    """Read the file as a parser does; returns (complete hosts, verdict,
    error) with verdict ``whole`` / ``cut`` / ``invalid``."""
    source = EndTracking.open(str(path))
    hosts = 0
    try:
        context = (
            iterparse_safe(source) if reader == "lxml"
            else DET.iterparse(source, events=("start", "end"))
        )
        for event, element in context:
            if event == "end" and element.tag == "host":
                hosts += 1
    except (etree.XMLSyntaxError, ET.ParseError) as exc:
        return hosts, ("cut" if xml_cut_short(source, str(path)) else "invalid"), exc
    except DefusedXmlException as exc:
        # defusedxml refuses a document that declares entities outright (the
        # Nessus import then fails with that reason, as it always did).
        return hosts, "invalid", exc
    finally:
        source.close()
    return hosts, "whole", None


# pad=9000: a host spans several of the readers' 16 / 32 KiB reads.
@pytest.mark.parametrize("pad,step", [(0, 1), (9000, 211)], ids=["one-read", "many-reads"])
@pytest.mark.parametrize("reader", ["lxml", "expat"])
def test_every_prefix_of_a_document_is_cut_short_with_its_complete_hosts(tmp_path, reader, pad, step):
    whole = _document(3, pad)
    path = tmp_path / "scan.xml"
    path.write_bytes(whole)
    assert _read(path, reader)[:2] == (3, "whole")

    words = set()
    for cut in range(0, len(whole) - 1, step):  # (minus the final newline: that is a whole document)
        prefix = whole[:cut]
        path.write_bytes(prefix)
        hosts, verdict, error = _read(path, reader)
        assert verdict == "cut", (reader, cut, prefix[-40:], error)
        assert hosts == prefix.count(b"</host>"), (reader, cut)
        words.add(str(error).split(",")[0].split(":")[0])
    # The library says it in many ways, which is why its words are not asked.
    assert len(words) >= (4 if reader == "lxml" and not pad else 2), words


_WHOLE = _document(3)
_MIDDLE = b"</host>\n<host"
INVALID_XML = {
    "a mismatched tag": _WHOLE.replace(b"</ports></host>\n<host", b"</port></host>\n<host", 1),
    "garbage between hosts": _WHOLE.replace(_MIDDLE, b"</host>\n<<<@@@ not xml>>>\n<host", 1),
    "a bare ampersand in text": _WHOLE.replace(_MIDDLE, b"</host>\n a & b \n<host", 1),
    "a bare ampersand at the very end": _WHOLE[: _WHOLE.index(b"</host>") + 8] + b" a & b ",
    "NUL bytes between hosts": _WHOLE.replace(_MIDDLE, b"</host>\n" + b"\x00" * 16 + b"<host", 1),
    "a control character as a reference": _WHOLE.replace(b"caf", b"&#x1b;caf", 1),
    "an attribute without quotes": _WHOLE.replace(b'state="up"', b"state=up", 1),
    "text after the document": _WHOLE + b"trailing junk",
    "a second root": _WHOLE + b"<nmaprun/>",
    # A copy that failed part-way and left the rest of the file as zeros.
    "cut, then a tail of NUL bytes": _WHOLE[: len(_WHOLE) // 2] + b"\x00" * 4096,
    "cut after a host, then NUL bytes": _WHOLE[: _WHOLE.index(b"</host>") + 8] + b"\x00" * 4096,
    "not XML at all": b"hello world\n" * 50,
    "a document that declares entities": _WHOLE.replace(
        b"<!DOCTYPE nmaprun>", b'<!DOCTYPE nmaprun [<!ENTITY a "aaaa">]>', 1)[:400],
}


@pytest.mark.parametrize("case", sorted(INVALID_XML))
@pytest.mark.parametrize("reader", ["lxml", "expat"])
def test_invalid_xml_is_never_called_cut_short(tmp_path, reader, case):
    path = tmp_path / "scan.xml"
    path.write_bytes(INVALID_XML[case])
    _hosts, verdict, error = _read(path, reader)
    assert verdict == "invalid", (reader, case, error)


def test_libxml2_reports_some_invalid_content_only_at_the_end_of_input(tmp_path):
    """Why lxml's "it failed after the last read" is not enough on its own."""
    path = tmp_path / "scan.xml"
    path.write_bytes(INVALID_XML["a bare ampersand in text"])
    source = EndTracking.open(str(path))
    try:
        with pytest.raises(etree.XMLSyntaxError):
            for _event, _element in iterparse_safe(source):
                pass
        assert source.ended is True, "libxml2 held the text back until the end of input"
        assert xml_cut_short(source, str(path)) is False
    finally:
        source.close()


# ---------------------------------------------------------------- masscan -oJ

_RECORD = '{{   "ip": "10.80.9.{i}",   "timestamp": "1790124714", "ports": [ {{"port": 80, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 63}} ] }}'


def _masscan(records: int, closed: bool = True) -> str:
    """As masscan 1.3 writes it: ``[``, a record per line, ``,`` between, ``]``."""
    body = "\n,\n".join(_RECORD.format(i=i + 1) for i in range(records))
    return "[\n" + body + "\n" + ("]\n" if closed else "")


def _masscan_entries(tmp_path, content: str, *, keep: bool = False):
    path = tmp_path / "masscan.json"
    path.write_text(content)
    token = begin_read(keep_truncated=keep)
    entries, error = [], None
    try:
        for entry in MasscanParser(None)._iter_json_entries(str(path)):
            entries.append(entry)
    except Exception as exc:  # noqa: BLE001 — the test wants the live exception
        error = exc
    return entries, error, end_read(token)


_OLD_FORMAT = "".join(_RECORD.format(i=i + 1) + ",\n" for i in range(3))

MASSCAN_WHOLE = {
    "an array, closed": (_masscan(3), 3),
    "an array on one line": (json.dumps([{"ip": "10.0.0.1", "ports": [{"port": 80}]}] * 2), 2),
    "an empty array": ("[\n]\n", 0),
    # masscan before 1.0.4: no array, a trailing comma per record, and a
    # closing line that is not JSON.
    "the old format with its closing line": (_OLD_FORMAT + "{finished: 1}\n", 3),
    "the old format with no closing line": (_OLD_FORMAT, 3),
    "two files joined": (_masscan(2) + _masscan(1), 3),
}

MASSCAN_CUT = {
    "between two records": (_masscan(3, closed=False), 3),
    "after the comma": (_masscan(3, closed=False) + ",\n", 3),
    "inside a record": (_masscan(3, closed=False) + ',\n{   "ip": "10.80.9.4",   "timest', 3),
    "inside a string": (_masscan(3, closed=False) + ',\n{   "ip": "10.80', 3),
    "inside the first record": ('[\n{   "ip": "10.80.9.1",   "timest', 0),
    "only the opening bracket": ("[\n", 0),
    "the old format, inside a record": (_OLD_FORMAT + '{   "ip": "10.80.9.4",   "times', 3),
}

def _with_a_raw_control_character() -> str:
    """Three records; the SECOND one's banner holds a raw 0x01 byte (JSON
    requires it escaped; masscan itself writes ``\\u0001``)."""
    banner = (
        '{   "ip": "10.80.9.2",   "timestamp": "1790124714", "ports": [ {"port": 80, "proto": "tcp", '
        '"service": {"name": "http", "banner": "HTTP/1.0 200\x01OK"}} ] }'
    )
    return "[\n" + _RECORD.format(i=1) + "\n,\n" + banner + "\n,\n" + _RECORD.format(i=3) + "\n]\n"


# (content, records decoded before the text that is not a record)
MASSCAN_INVALID = {
    "garbage between records": (_masscan(3).replace("\n,\n", "\n,\n@@@ not json @@@\n,\n", 1), 1),
    "a record with an unquoted key": (_masscan(3).replace('"ip"', "ip", 1), 0),
    "a stray closing brace at the end": (_masscan(3) + "}\n", 3),
    "a raw control character in a banner": (_with_a_raw_control_character(), 1),
}


@pytest.mark.parametrize("case", sorted(MASSCAN_WHOLE))
def test_a_complete_masscan_json_is_read_whole(tmp_path, case):
    content, records = MASSCAN_WHOLE[case]
    for keep in (False, True):
        entries, error, report = _masscan_entries(tmp_path, content, keep=keep)
        assert error is None and len(entries) == records, (case, error)
        assert report.cut_short is None


@pytest.mark.parametrize("case", sorted(MASSCAN_CUT))
def test_a_cut_masscan_json_is_said_to_be_cut(tmp_path, case):
    """It used to import as if it were whole: the undecodable tail was dropped
    with a log line, and a file that stopped between records said nothing."""
    content, records = MASSCAN_CUT[case]
    entries, error, report = _masscan_entries(tmp_path, content)
    assert isinstance(error, TruncatedFile) and error.records_read == records, (case, error)
    assert len(entries) == records and report.cut_short is error

    entries, error, report = _masscan_entries(tmp_path, content, keep=True)
    if records:
        assert error is None and len(entries) == records
        assert report.cut_short is not None and report.cut_short.records_read == records
    else:
        assert isinstance(error, TruncatedFile)  # nothing complete: nothing to keep


@pytest.mark.parametrize("case", sorted(MASSCAN_INVALID))
def test_invalid_masscan_json_keeps_what_precedes_it_and_says_so(tmp_path, case):
    """Text that is not a record is NOT a cut: the records before it are
    handed over exactly as they always were (nothing fails, nothing is
    asked).  What is new is that the reader notes it, so the import is marked
    partial instead of looking complete."""
    content, before = MASSCAN_INVALID[case]
    for keep in (False, True):
        path = tmp_path / "masscan.json"
        path.write_text(content)
        parser = MasscanParser(None)
        token = begin_read(keep_truncated=keep)
        try:
            entries = list(parser._iter_json_entries(str(path)))
        finally:
            report = end_read(token)
        assert len(entries) == before, case
        assert report.cut_short is None
        assert parser._json_unread_after == before


def test_a_record_with_a_raw_control_character_was_never_read():
    """Which it is (owner's question, 2026-10-11): the masscan reader decodes
    with ``json.JSONDecoder()`` — strict — and always has, so a record whose
    banner holds a raw control character was never imported: it, and every
    record after it, were the tail the old reader dropped with a log line.
    The decoder is unchanged, so that is still what is read; the import now
    says so.  (masscan writes such bytes escaped, ``\\u0001``, which decodes.)"""
    raw = '{"banner": "HTTP/1.0 200\x01OK"}'
    with pytest.raises(json.JSONDecodeError):
        json.JSONDecoder().raw_decode(raw)
    assert json.JSONDecoder().raw_decode('{"banner": "HTTP/1.0 200\\u0001OK"}')[0] == {
        "banner": "HTTP/1.0 200\x01OK"}


# ------------------------------------------------------------ one JSON value

@pytest.mark.parametrize("text,expected", [
    ('{"a": 1', True), ('{"a": "unfin', True), ('{"a": tru', True), ('{"a": "caf\\u00', True),
    ('[1, 2', True), ('"abc', True),
    ('{"a": 1}', False), ("@@@", False), ("]", False), ('{"a": 1},', False), ("{finished: 1}", False),
    ('{"a": 1} {"b"', False), ("", False), ("   ", False),
])
def test_the_beginning_of_a_json_value_is_told_from_text_that_is_not_json(text, expected):
    assert ends_early(text) is expected


def test_only_a_files_last_line_can_be_a_cut_line():
    assert is_cut_last_line('{"a": "unfin') is True
    # A line ending follows: the writer finished that line.
    assert is_cut_last_line('{"a": "unfin\n') is False
    assert is_cut_last_line('{"a": "unfin\r\n') is False
    assert is_cut_last_line("@@@ garbage") is False
