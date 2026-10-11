"""Every import format, given a file that was cut short.

Owner decision 2026-10-11: "Let's be consistent when possible and if it's
possible to see that the JSON format ended abruptly let's ask the user if they
wish to import the partial for all scans where it's possible."

ONE rule for every format of ``format_registry.FORMATS`` in which a premature
end can be detected (``app/parsers/cut_short.py``).  For a fixture cut after
at least one complete record:

* the import FAILS as ``truncated_file`` and says how much the file holds;
  nothing of the attempt is left (no scan, no host, no observation);
* the SAME job, re-queued through ``staged_import_service.queue_job`` with
  the instruction to keep what the file holds, completes PARTIAL with exactly
  what a complete file of those records imports, a warning naming the count,
  its upload identity, and is counted in "imports needing someone";
* an INVALID version of the same fixture (garbage in the middle) is never
  called cut short, with or without the instruction.

A format is either in ``CUT_IS_DETECTABLE`` — and held to all of that — or in
``A_CUT_CANNOT_BE_TOLD`` with the reason a cut is not detectable in it.  A
format in neither fails ``test_every_format_is_decided``.

The cuts are made at positions the test computes from the fixture: after
record ``k``, and inside record ``k + 1``.  The fixtures are the ones
``test_every_import_records_the_hosts_it_touches.py`` imports whole, run
through the same real job path (``tests/ingestion_job_harness``) with
``format_override`` naming the format.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

import pytest
from sqlalchemy import text

from app.db import models
from app.db.models_project import Project
from app.parsers import streaming_json
from app.services.format_registry import FORMATS, format_label
from app.services.ingestion_service import KEEP_TRUNCATED_OPTION, TRUNCATED_FILE_ERROR
from app.services.operations_read_service import blocked_import_condition
from app.services.staged_import_service import queue_job
from tests.ingestion_job_harness import job_row, queue_file, run_file, run_next, scans_of
from tests.test_every_import_records_the_hosts_it_touches import CASES, _RDAP

# ---------------------------------------------------------------------------
# Where the records of a fixture end, and what closes a file of the first k
# ---------------------------------------------------------------------------

_DECODER = json.JSONDecoder()
_SPACE = " \t\r\n"


@dataclass(frozen=True)
class Shape:
    """How one fixture is made of records.

    ``ends(text)`` — the offsets just past each complete record, in order.
    ``closed(text, end)`` — a COMPLETE file holding the records before ``end``.
    ``garbage`` — inserted at the first record's end, it makes the file
    invalid without making it shorter.
    ``unit`` — what the format's sentence calls a record.
    ``in_lines`` — one record per line: the cut is a cut last line.
    ``invalid`` — what an invalid file does: ``fails``, or ``skips`` for JSON
    lines, where a line that does not decode in the MIDDLE of a file is
    skipped and counted and the import goes on (unchanged by this rule).
    """
    ends: Callable[[str], List[int]]
    closed: Callable[[str, int], str]
    garbage: str
    unit: str = "record"
    in_lines: bool = False
    invalid: str = "fails"


def _skip_space(source: str, at: int) -> int:
    while at < len(source) and source[at] in _SPACE:
        at += 1
    return at


def _array_item_ends(source: str, opened_at: int) -> List[int]:
    """Offsets just past each item of the JSON array opening at ``opened_at``."""
    assert source[opened_at] == "[", source[opened_at:opened_at + 20]
    ends, at = [], _skip_space(source, opened_at + 1)
    while source[at] != "]":
        _value, at = _DECODER.raw_decode(source, at)
        ends.append(at)
        at = _skip_space(source, at)
        if source[at] == ",":
            at = _skip_space(source, at + 1)
    return ends


def _array(key: str = "") -> Shape:
    """A JSON array of records, at the top or under ``key`` of an object."""
    def opened_at(source: str) -> int:
        if not key:
            return source.index("[")
        return re.search(r'"%s"\s*:\s*\[' % re.escape(key), source).end() - 1

    return Shape(
        ends=lambda source: _array_item_ends(source, opened_at(source)),
        closed=lambda source, end: source[:end] + "]" + ("}" if key else ""),
        garbage=" @@@ not json @@@, ",
    )


def _member_ends(source: str) -> List[int]:
    """Offsets just past each member's value of a top-level JSON object."""
    ends, at = [], _skip_space(source, source.index("{") + 1)
    while source[at] != "}":
        _key, at = _DECODER.raw_decode(source, at)
        at = _skip_space(source, at)
        assert source[at] == ":"
        _value, at = _DECODER.raw_decode(source, _skip_space(source, at + 1))
        ends.append(at)
        at = _skip_space(source, at)
        if source[at] == ",":
            at = _skip_space(source, at + 1)
    return ends


_MEMBERS = Shape(ends=_member_ends, closed=lambda source, end: source[:end] + "}", garbage=" @@@ ")

_LINES = Shape(
    ends=lambda source: [m.end() for m in re.finditer(r"\n", source)],
    closed=lambda source, end: source[:end],
    garbage="@@@ not json @@@\n",
    in_lines=True,
    invalid="skips",
)


def _xml(element: str, closing: str, unit: str = "record") -> Shape:
    tag = f"</{element}>"
    return Shape(
        ends=lambda source: [m.end() for m in re.finditer(re.escape(tag), source)],
        closed=lambda source, end: source[:end] + "\n" + closing + "\n",
        garbage="\n<<<@@@ not xml>>>\n",
        unit=unit,
    )


#: Formats in which a premature end is detected, with the shape of the fixture
#: ``CASES`` holds for each (``rdap_json`` has its own there).
CUT_IS_DETECTABLE: Dict[str, Shape] = {
    # --- XML: the reader fails only at the end of input, and expat agrees
    #     that every byte before it is acceptable (cut_short.xml_cut_short) ---
    "nmap_xml": _xml("host", "</nmaprun>", unit="host"),
    "masscan_xml": _xml("host", "</nmaprun>"),
    "nessus_xml": _xml("ReportHost", "</Report>\n</NessusClientData_v2>", unit="host"),
    "openvas_xml": _xml("result", "</results></report>"),
    # --- a JSON document through the shared reader ---
    "amass_json": _array(),
    "dirbuster_json": _array("results"),
    "eyewitness_json": _array(),
    "nikto_json": _array(),
    "nuclei_json": _array(),
    "smbmap_json": _array(),
    "testssl_json": _array(),
    "whatweb_json": _array(),
    # --- JSON lines through the shared reader: a cut LAST line ---
    "dnsx_json": _LINES,
    "httpx_json": _LINES,
    "naabu_json": _LINES,
    # --- parsers with a reader of their own, asking the shared rule ---
    "masscan_json": _array(),
    "bloodhound_json": _array("data"),
    "netexec_json": _MEMBERS,
    "rdap_json": _LINES,
}

#: Formats in which a cut cannot be told from a short or a bad line.  They are
#: read exactly as before this rule; nothing is asked.
A_CUT_CANNOT_BE_TOLD: Dict[str, str] = {
    "nmap_gnmap": (
        "One host per line. A file cut between lines is a shorter valid file, and a cut last "
        "line is a line with fewer ports. (nmap ends the file with a '# Nmap done' comment, but "
        "masscan -oG and a grep of the file do not: its absence does not say the file was cut.)"
    ),
    "gnmap_txt": "The same grepable lines under a .txt name: see nmap_gnmap.",
    "masscan_list": (
        "One 'open tcp PORT IP EPOCH' line per port. A cut last line is a shorter line (counted "
        "as skipped when it has under four fields) or simply an earlier port; nothing closes the file."
    ),
    "naabu_output": "One 'host:port' line per port: a cut last line is another valid, shorter line.",
    "rustscan_output": "One 'Open ip:port' (or 'ip -> [ports]') line per host: a cut line is a shorter valid line.",
    "nikto_csv": "CSV rows: a cut last row is a short row, read as any short row (under seven cells: not a finding).",
    "nikto_output": "The console report, line by line; nothing in it must close the file.",
    "eyewitness_csv": "CSV rows: a cut last row is a short row.",
    "eyewitness_zip": (
        "A zip's directory is at its END: a cut zip cannot be opened at all, so it fails like any "
        "unreadable archive and holds nothing that can be kept."
    ),
    "dirbuster_csv": "CSV rows: a cut last row is a short row.",
    "dirbuster_output": "One URL line per result: a cut last line is a shorter line, or no result.",
    "dns_csv": "CSV rows: a cut last row is a short row.",
    "amass_output": "One name per line: a cut last line is a shorter name.",
    "netexec_output": (
        "Console lines: a cut last line is a line that matches no pattern (listed as not "
        "interpreted) or a shorter valid one."
    ),
    "smbmap_output": "Console lines: a cut last line is a shorter line.",
}


#: Detectable formats whose parser, given INVALID content in the middle of a
#: file (not a cut), keeps what precedes it and completes partial.  That is
#: what each did before the cut-short rule existed, and the owner's rule is
#: only about files that END ABRUPTLY (2026-10-11: existing behaviour for
#: other files is unchanged) — so it is pinned, not changed.  Every other
#: detectable format fails and keeps nothing on invalid content (JSON lines
#: skip the line and count it): ``test_an_invalid_file_is_never_called_cut_short``.
INVALID_CONTENT_KEEPS_WHAT_PRECEDES: Dict[str, str] = {
    "nmap_xml": (
        "Any reader error ends the read and keeps the hosts before it, partial, with 'Incomplete XML "
        "— … hosts after this point are MISSING, not down'. A complete nmap file can hold a character "
        "XML does not allow (a banner's control character, written as &#x1b;) and must keep importing."
    ),
    "masscan_xml": (
        "Any reader error ends the read and keeps the records before it, partial, with 'The XML ended "
        "early (near line N); …' (v2.419.0)."
    ),
    "masscan_json": (
        "The records before text that is not a record were always imported; the text and everything "
        "after it were dropped with a log line and the import looked complete. They are still "
        "imported; since 2026-10-11 the import is marked partial and says so."
    ),
}


@dataclass(frozen=True)
class Variant:
    """A second file of a detectable format, in a shape (or through a reader)
    its first fixture does not reach.  Held to the same rule."""
    file_type: str
    shape: Shape
    build: Callable[[], str]
    setup: Callable = lambda monkeypatch: None


def _nikto_one_host() -> str:
    # Nikto 2.5's own output for ONE target: a host object, its findings under
    # ``vulnerabilities`` and the target on the object — so the complete items
    # of a cut file carry no target of their own (the corner found
    # 2026-10-11: the kept attempt recorded nothing and failed).
    from tests.test_every_import_records_the_hosts_it_touches import NATIVE

    return json.dumps(json.loads((NATIVE / "nikto-all.json").read_text())[0], indent=3)


def _rdap_array() -> str:
    # The borrowed NDJSON, as one array of responses.
    return json.dumps([json.loads(line) for line in _whole_of("rdap_json").splitlines()])


def _streamed_bloodhound(monkeypatch) -> None:
    from app.parsers import bloodhound_parser

    monkeypatch.setattr(bloodhound_parser, "_BLOODHOUND_STREAM_THRESHOLD_BYTES", 1)


def _streamed_documents(monkeypatch) -> None:
    monkeypatch.setattr(streaming_json, "_DEFAULT_STREAM_THRESHOLD_BYTES", 1)


VARIANTS: Dict[str, Variant] = {
    "nikto_json-one-host-object": Variant(
        "nikto_json",
        Shape(
            ends=lambda source: _array("vulnerabilities").ends(source),
            closed=lambda source, end: source[:end] + "]}",
            garbage=" @@@ not json @@@, ",
        ),
        _nikto_one_host,
    ),
    # An array of RDAP responses (the NDJSON is the format's first fixture).
    "rdap_json-array-of-responses": Variant("rdap_json", _array(), _rdap_array),
    # The parser's streaming path (production: from 50 MB).
    "bloodhound_json-streamed": Variant(
        "bloodhound_json", _array("data"), lambda: _whole_of("bloodhound_json"), _streamed_bloodhound,
    ),
    # A JSON document through the shared reader's streaming path.
    "smbmap_json-streamed": Variant(
        "smbmap_json", _array(), lambda: _whole_of("smbmap_json"), _streamed_documents,
    ),
}


def _whole_of(file_type: str) -> str:
    import tempfile

    case = _case(file_type)
    with tempfile.TemporaryDirectory() as scratch:
        return case.build(Path(scratch), case.filename).read_text()


def _type_of(key: str) -> str:
    return VARIANTS[key].file_type if key in VARIANTS else key


def _shape_of(key: str) -> Shape:
    return VARIANTS[key].shape if key in VARIANTS else CUT_IS_DETECTABLE[key]


EVERY_CUT = sorted(CUT_IS_DETECTABLE) + sorted(VARIANTS)


def test_every_format_is_decided():
    decided = set(CUT_IS_DETECTABLE) | set(A_CUT_CANNOT_BE_TOLD)
    missing = sorted(set(FORMATS) - decided)
    assert not missing, (
        f"import formats this file does not decide: {missing} — a format whose reader can tell "
        "that the file ends early goes in CUT_IS_DETECTABLE (and reports it through "
        "cut_short.settle_cut); one that cannot goes in A_CUT_CANNOT_BE_TOLD with the reason"
    )
    assert not sorted(decided - set(FORMATS)), "decided here, but no longer a format"
    assert not set(CUT_IS_DETECTABLE) & set(A_CUT_CANNOT_BE_TOLD)
    assert all(reason.strip() for reason in A_CUT_CANNOT_BE_TOLD.values())
    # What a detectable format does with INVALID content is one of three, and
    # each format is in exactly one: keeps what precedes it (named, with the
    # reason), skips the line (JSON lines), or fails and keeps nothing.
    assert set(INVALID_CONTENT_KEEPS_WHAT_PRECEDES) <= set(CUT_IS_DETECTABLE)
    assert all(reason.strip() for reason in INVALID_CONTENT_KEEPS_WHAT_PRECEDES.values())
    for file_type, shape in CUT_IS_DETECTABLE.items():
        assert shape.invalid in ("fails", "skips"), file_type
        if file_type in INVALID_CONTENT_KEEPS_WHAT_PRECEDES:
            assert shape.invalid == "fails", f"{file_type}: named as keeping, and a JSON-lines shape too"


# ---------------------------------------------------------------------------
# The fixtures, cut
# ---------------------------------------------------------------------------

def _case(file_type: str):
    return _RDAP if file_type == "rdap_json" else CASES[file_type]


def _fixture(tmp_path: Path, key: str) -> str:
    """The whole file of a format (or of one of ``VARIANTS``)."""
    if key in VARIANTS:
        return VARIANTS[key].build()
    folder = tmp_path / "whole"
    folder.mkdir(exist_ok=True)
    case = _case(key)
    return case.build(folder, case.filename).read_text()


def _begin(key: str, monkeypatch):
    """``(file_type, shape)`` of a parametrised case, its reader set up."""
    if key in VARIANTS:
        VARIANTS[key].setup(monkeypatch)
    return _type_of(key), _shape_of(key)


@dataclass(frozen=True)
class Cut:
    """The fixture stopped after ``complete`` records: ``after`` right at the
    record's end, ``inside`` part-way into the next one."""
    complete: int
    after: str
    inside: str
    closed: str
    invalid: str


def _cut(tmp_path: Path, key: str) -> Cut:
    shape, whole = _shape_of(key), _fixture(tmp_path, key)
    ends = shape.ends(whole)
    assert len(ends) >= 2, f"{key}: the fixture holds {len(ends)} record(s); two are needed"
    # Every record but the last: at least one complete, and one to cut into.
    complete = len(ends) - 1
    end, following = ends[complete - 1], ends[complete]
    # Half-way into the record that follows (past whatever separates the two).
    inside = end + max(2, (following - end) // 2)
    assert end < inside < following
    return Cut(
        complete=complete,
        after=whole[:end],
        inside=whole[:inside],
        closed=shape.closed(whole, end),
        invalid=whole[:ends[0]] + shape.garbage + whole[ends[0]:],
    )


def test_the_cut_positions_come_from_the_fixtures(tmp_path):
    """The shapes above describe the fixtures: a file of the first k records,
    closed, is a complete file, and the whole fixture ends with its last
    record's end followed only by what closes it."""
    for file_type in EVERY_CUT:
        shape, whole = _shape_of(file_type), _fixture(tmp_path, file_type)
        ends = shape.ends(whole)
        assert ends == sorted(ends) and len(set(ends)) == len(ends), file_type
        closed = shape.closed(whole, ends[-1])
        if shape.in_lines:
            assert closed == whole, file_type
            for line in whole.splitlines():
                json.loads(line)
        elif closed.lstrip().startswith(("[", "{")):
            assert json.loads(closed) == json.loads(whole), file_type
        else:
            from lxml import etree
            assert len(etree.fromstring(closed.encode())) and etree.fromstring(whole.encode()) is not None


# ---------------------------------------------------------------------------
# What an import left in a project
# ---------------------------------------------------------------------------

_LEFT = {
    "hosts": "SELECT ip_address FROM hosts_v2 WHERE project_id = :pid",
    "ports": (
        "SELECT h.ip_address || ':' || p.port_number || '/' || p.protocol FROM ports_v2 p "
        "JOIN hosts_v2 h ON h.id = p.host_id WHERE h.project_id = :pid"
    ),
    "host history": (
        "SELECT h.ip_address FROM host_scan_history s JOIN hosts_v2 h ON h.id = s.host_id "
        "WHERE h.project_id = :pid"
    ),
    "scanner observations": (
        "SELECT h.ip_address || ' ' || v.title FROM vulnerabilities v JOIN hosts_v2 h ON h.id = v.host_id "
        "WHERE h.project_id = :pid"
    ),
    "web interfaces": "SELECT url FROM web_interfaces WHERE project_id = :pid",
    "web paths": (
        "SELECT h.ip_address || ' ' || w.path FROM web_paths w JOIN hosts_v2 h ON h.id = w.host_id "
        "WHERE h.project_id = :pid"
    ),
    "netexec results": (
        "SELECT h.ip_address || ' ' || n.protocol FROM netexec_results n JOIN hosts_v2 h ON h.id = n.host_id "
        "WHERE h.project_id = :pid"
    ),
    "dns records": (
        "SELECT r.domain || ' ' || r.record_type || ' ' || r.value FROM dns_records r "
        "JOIN scans s ON s.id = r.scan_id WHERE s.project_id = :pid"
    ),
    "network attributions": "SELECT cidr FROM network_attributions WHERE project_id = :pid",
    "scans": "SELECT filename FROM scans WHERE project_id = :pid",
}


def _left(db, pid: int, *, without=("scans",)) -> Dict[str, List[str]]:
    """Everything an import leaves in the project, as sorted values per table."""
    db.expire_all()
    return {
        table: sorted(str(row[0]) for row in db.execute(text(statement), {"pid": pid}))
        for table, statement in _LEFT.items() if table not in without
    }


def _nothing_left(db, pid: int, label: str) -> None:
    left = {table: rows for table, rows in _left(db, pid, without=()).items() if rows}
    assert not left, f"{label}: the failed attempt left {left}"


def _needs_someone(db, pid: int):
    db.expire_all()
    return {
        job_id for (job_id,) in db.query(models.IngestionJob.id)
        .filter(models.IngestionJob.project_id == pid, blocked_import_condition())
    }


def _parse_error(db, job) -> models.ParseError:
    assert job.parse_error_id is not None, (job.status, job.error_message, job.message)
    return db.get(models.ParseError, job.parse_error_id)


def _queue(db, pid: int, tmp_path: Path, file_type: str, content: str, user_id: int, name: str = "") -> int:
    """A queued job for ``content`` as the upload route leaves one: who sent
    it and the file's SHA-256 on the row."""
    case = _case(file_type)
    stem, dot, suffix = case.filename.partition(".")
    filename = f"{stem}{name}{dot}{suffix}"
    folder = tmp_path / f"upload{name or '-cut'}"
    folder.mkdir()
    path = folder / filename
    path.write_text(content)
    job_id = queue_file(db, pid, path, filename, format_override=file_type)
    job = job_row(db, job_id)
    job.content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    job.submitted_by_id = user_id
    db.commit()
    return job_id


@pytest.fixture
def reference_project(db_session):
    project = Project(name="reference", slug="reference", description="a complete file of the same records")
    db_session.add(project)
    db_session.commit()
    return project


@pytest.fixture(autouse=True)
def _scan_files_in_the_test_folder(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("where", ["after", "inside"])
@pytest.mark.parametrize("key", EVERY_CUT)
def test_a_cut_file_fails_as_cut_short_and_is_kept_only_when_asked(
    db_session, test_project, test_user, reference_project, tmp_path, monkeypatch, key, where,
):
    (file_type, shape), pid = _begin(key, monkeypatch), test_project.id
    cut = _cut(tmp_path, key)
    content = getattr(cut, where)
    if shape.in_lines and where == "after":
        # A JSON-lines file that stops BETWEEN two lines is a complete file of
        # fewer lines: nothing in it says more was coming.
        job = run_file(db_session, pid, _write(tmp_path, file_type, content), format_override=file_type)
        assert job.status == "completed", (job.status, job.error_message)
        assert "cut short" not in (job.parser_warnings or "")
        return

    label, k = format_label(file_type), cut.complete
    records = f"{k:,} complete {shape.unit}{'s' if k != 1 else ''}"
    sentence = (
        f"This {label} file was cut short: it ends after {records}, "
        + ("part-way through the line that follows." if shape.in_lines else "before the end of the document.")
    )

    # 1. As uploaded: it fails, as that, and leaves nothing.
    job_id = _queue(db_session, pid, tmp_path, file_type, content, test_user.id)
    run_next(db_session)
    job = job_row(db_session, job_id)
    assert job.status == "failed", (file_type, job.status, job.message, job.parser_warnings)
    error = _parse_error(db_session, job)
    assert error.error_type == TRUNCATED_FILE_ERROR, (file_type, error.error_type, error.error_message)
    assert error.error_message == sentence
    assert job.error_message == f"{sentence} Nothing from this file was imported."
    assert error.error_details["records_read"] == k and error.error_details["unit"] == shape.unit
    assert KEEP_TRUNCATED_OPTION not in (job.options or {})
    assert job.scan_id is None and job.in_progress_scan_id is None
    _nothing_left(db_session, pid, file_type)
    assert job_id in _needs_someone(db_session, pid)

    # 2. The same job, asked to import what the file holds.
    queue_job(db_session, job_id, allowed_from=("failed",), keep_truncated=True)
    assert job_row(db_session, job_id).options[KEEP_TRUNCATED_OPTION] is True
    run_next(db_session)
    job = job_row(db_session, job_id)
    assert job.status == "completed", (file_type, job.error_message)
    assert job.partial is True
    assert job.final_file_type == file_type
    warning = f"{sentence} {'That' if k == 1 else 'Those'} {shape.unit}{' was' if k == 1 else 's were'} imported;"
    assert warning in (job.parser_warnings or ""), job.parser_warnings
    assert job_id in _needs_someone(db_session, pid)

    # Its upload identity, like any completed import.
    (scan,) = scans_of(db_session, pid)
    assert job.scan_id == scan.id and job.in_progress_scan_id is None
    assert scan.content_sha256 == job.content_sha256 and scan.uploaded_by_id == test_user.id

    # Exactly the complete records: what a complete file of them imports.
    reference = run_file(
        db_session, reference_project.id, _write(tmp_path, file_type, cut.closed, "closed"),
        format_override=file_type,
    )
    assert reference.status == "completed", (file_type, reference.error_message)
    assert "cut short" not in (reference.parser_warnings or "")
    kept, whole = _left(db_session, pid), _left(db_session, reference_project.id)
    assert kept == whole, f"{file_type}: kept differs from a complete file of the same {k} record(s)"
    assert any(kept.values()), f"{file_type}: the kept import holds nothing"

    # 3. A plain retry of it is not possible (it completed); the instruction
    #    was that attempt's and is gone with any other start.
    assert run_next(db_session) is False


def _write(tmp_path: Path, file_type: str, content: str, name: str = "plain") -> Path:
    case = _case(file_type)
    folder = tmp_path / f"file-{name}"
    folder.mkdir(exist_ok=True)
    path = folder / case.filename
    path.write_text(content)
    return path


@pytest.mark.parametrize("key", EVERY_CUT)
def test_an_invalid_file_is_never_called_cut_short(
    db_session, test_project, test_user, reference_project, tmp_path, monkeypatch, key,
):
    """Garbage in the MIDDLE of the same fixture.  It is not a file that ends
    early: it never gets the kind, is never offered the action, and the
    instruction changes nothing about it.  What the format then DOES with the
    invalid content is its own, and unchanged by this rule — one of three:

    * a document fails and keeps nothing (most formats);
    * JSON lines skip the line and count it;
    * ``INVALID_CONTENT_KEEPS_WHAT_PRECEDES``: the records before the bad
      content are kept and the import is partial.

    A JSON document goes through the streaming reader here (production: from
    50 MB): below that size a document ``json.loads`` refuses has always been
    handed to the line reader, which is pinned as it is in
    ``test_truncated_file_keep.py::test_a_small_invalid_document_is_read_as_it_always_was``."""
    monkeypatch.setattr(streaming_json, "_DEFAULT_STREAM_THRESHOLD_BYTES", 1)
    (file_type, shape), pid = _begin(key, monkeypatch), test_project.id
    cut = _cut(tmp_path, key)
    job_id = _queue(db_session, pid, tmp_path, file_type, cut.invalid, test_user.id, name="-invalid")

    if file_type in INVALID_CONTENT_KEEPS_WHAT_PRECEDES:
        run_next(db_session)
        job = job_row(db_session, job_id)
        assert (job.status, job.partial, job.parse_error_id) == ("completed", True, None), (
            file_type, job.status, job.error_message)
        assert job.parser_warnings and "cut short" not in job.parser_warnings

        # The garbage follows the fixture's FIRST record: that record is kept
        # — what a complete file of it imports.
        whole = _fixture(tmp_path, key)
        reference = run_file(
            db_session, reference_project.id,
            _write(tmp_path, file_type, shape.closed(whole, shape.ends(whole)[0]), "first-record"),
            format_override=file_type,
        )
        assert reference.status == "completed", reference.error_message
        expected = _left(db_session, reference_project.id)
        assert expected["hosts"], file_type
        assert _left(db_session, pid) == expected, f"{file_type}: not exactly the records before the bad content"
        assert job_id in _needs_someone(db_session, pid)

        # Asked to keep a cut-short file: the same import, word for word.
        again = Project(name="again", slug="again", description="the same invalid file, asked to keep")
        db_session.add(again)
        db_session.commit()
        kept_id = _queue(db_session, again.id, tmp_path, file_type, cut.invalid, test_user.id, name="-invalid-keep")
        kept_job = job_row(db_session, kept_id)
        kept_job.options = {**kept_job.options, KEEP_TRUNCATED_OPTION: True}
        db_session.commit()
        run_next(db_session)
        kept_job = job_row(db_session, kept_id)
        assert (kept_job.status, kept_job.partial, kept_job.parser_warnings) == (
            "completed", True, job.parser_warnings)
        assert _left(db_session, again.id) == expected
        return

    outcomes = []
    for attempt in ("as uploaded", "asked to keep"):
        run_next(db_session)
        job = job_row(db_session, job_id)
        said = " ".join(filter(None, [job.error_message, job.parser_warnings]))
        assert "cut short" not in said, (file_type, attempt, said)
        if shape.invalid == "fails":
            assert job.status == "failed", (file_type, attempt, job.status, job.parser_warnings)
            assert _parse_error(db_session, job).error_type == "parsing_error", (file_type, attempt)
            _nothing_left(db_session, pid, f"{file_type} ({attempt})")
            if attempt == "as uploaded":
                queue_job(db_session, job_id, allowed_from=("failed",), keep_truncated=True)
        else:
            assert job.status == "completed", (file_type, attempt, job.error_message)
            assert job.skipped_count >= 1, (file_type, attempt)
            assert "invalid JSON" in said or "not valid JSON" in said, said
            outcomes.append((job.partial, job.skipped_count, _left(db_session, pid)))
            break
    if shape.invalid == "skips":
        # The line is skipped, every other record is imported — the same
        # import the instruction would give: it is only ever about a cut.
        whole = _fixture(tmp_path, key)
        again = Project(name="again", slug="again", description="the same invalid file, asked to keep")
        db_session.add(again)
        db_session.commit()
        kept_id = _queue(db_session, again.id, tmp_path, file_type, cut.invalid, test_user.id, name="-invalid-keep")
        kept_job = job_row(db_session, kept_id)
        kept_job.options = {**kept_job.options, KEEP_TRUNCATED_OPTION: True}
        db_session.commit()
        run_next(db_session)
        kept_job = job_row(db_session, kept_id)
        assert kept_job.status == "completed"
        assert (kept_job.partial, kept_job.skipped_count, _left(db_session, again.id)) == outcomes[0]
        assert len(shape.ends(whole)) >= 2


@pytest.mark.parametrize("key", EVERY_CUT)
def test_a_file_cut_inside_its_first_record_has_nothing_to_keep(
    db_session, test_project, test_user, tmp_path, monkeypatch, key,
):
    """Nothing complete precedes the cut: it fails in words, NOT as the kind
    that offers to keep, and asking to keep changes nothing."""
    (file_type, shape), pid = _begin(key, monkeypatch), test_project.id
    whole = _fixture(tmp_path, key)
    first_end = shape.ends(whole)[0]
    # Part-way into the first record: before its end, after whatever opens
    # the file (found as the start of the record, from its end backwards).
    content = whole[:first_end - max(2, min(20, first_end // 4))]
    job_id = _queue(db_session, pid, tmp_path, file_type, content, test_user.id, name="-first")
    sentence = (
        f"This {format_label(file_type)} file was cut short before its first {shape.unit} was complete: "
        "there is nothing in it to import."
    )
    for attempt in ("as uploaded", "asked to keep"):
        run_next(db_session)
        job = job_row(db_session, job_id)
        assert job.status == "failed", (file_type, attempt, job.parser_warnings)
        error = _parse_error(db_session, job)
        assert error.error_type == "parsing_error", (file_type, attempt, error.error_message)
        assert error.error_message == sentence, (file_type, attempt)
        _nothing_left(db_session, pid, f"{file_type} ({attempt})")
        if attempt == "as uploaded":
            queue_job(db_session, job_id, allowed_from=("failed",), keep_truncated=True)


@pytest.mark.parametrize("key", EVERY_CUT)
def test_a_complete_file_imports_the_same_with_the_instruction(
    db_session, test_project, reference_project, tmp_path, monkeypatch, key,
):
    """The instruction permits stopping at a cut; a complete file is imported
    whole and clean either way."""
    file_type, _shape = _begin(key, monkeypatch)
    whole = _fixture(tmp_path, key)
    plain = run_file(
        db_session, test_project.id, _write(tmp_path, file_type, whole), format_override=file_type,
    )
    asked = run_file(
        db_session, reference_project.id, _write(tmp_path, file_type, whole, "asked"),
        format_override=file_type, **{KEEP_TRUNCATED_OPTION: True},
    )
    for job in (plain, asked):
        assert job.status == "completed", (file_type, job.error_message)
        assert "cut short" not in (job.parser_warnings or "")
    # (A capture may hold rows its parser skips: partial or not, it is the
    # same either way.)
    assert (plain.partial, plain.skipped_count, plain.parser_warnings) == (
        asked.partial, asked.skipped_count, asked.parser_warnings)
    assert _left(db_session, test_project.id) == _left(db_session, reference_project.id)


# ---------------------------------------------------------------------------
# Formats in which a cut cannot be told: read as before, nothing asked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_type", sorted(set(A_CUT_CANNOT_BE_TOLD) - {"eyewitness_zip"}))
def test_an_undetectable_format_is_never_called_cut_short(db_session, test_project, tmp_path, file_type):
    """Half of the fixture, stopped in the middle of a line: whatever the
    parser makes of it (a shorter import, a skipped line, or a failure for
    its own reason), it is not the kind that offers to keep."""
    whole = _fixture(tmp_path, file_type)
    content = whole[: len(whole) // 2 + 3]
    job = run_file(db_session, test_project.id, _write(tmp_path, file_type, content), format_override=file_type)
    assert job.status in ("completed", "failed")
    if job.status == "failed":
        assert _parse_error(db_session, job).error_type != TRUNCATED_FILE_ERROR
    assert "cut short" not in " ".join(filter(None, [job.error_message, job.parser_warnings]))


def test_the_agents_door_for_an_interrupted_nmap_run(client, db_session, test_project, tmp_path):
    """The common case, through the agents' door (owner, 2026-10-11: the
    agent asks its operator): the upload fails as cut short, the agent reads
    the kind, and — stating that the operator agreed — has the same job
    import what the file holds.  ``tests/test_truncated_upload_agent.py``
    holds the refusals, the roles and the record."""
    from app.db.models_project import ProjectRole
    from tests.ingestion_job_harness import host_ips
    from tests.test_agent_role_route_matrix import _key_for, _member

    pid, cut = test_project.id, _cut(tmp_path, "nmap_xml")
    operator = _member(db_session, test_project, ProjectRole.ANALYST)
    headers = {"X-API-Key": _key_for(db_session, test_project, operator)}

    uploaded = client.post(
        "/api/v1/agent/uploads", headers=headers,
        files={"file": (CASES["nmap_xml"].filename, cut.inside.encode(), "application/xml")},
    )
    assert uploaded.status_code == 201, uploaded.text
    job_url = f"/api/v1/agent/uploads/{uploaded.json()['job_id']}"
    run_next(db_session)
    polled = client.get(job_url, headers=headers).json()
    assert (polled["status"], polled["failure_kind"]) == ("failed", TRUNCATED_FILE_ERROR)
    assert f"it ends after {cut.complete} complete host" in polled["error_message"]
    _nothing_left(db_session, pid, "nmap_xml through the agents' door")

    assert client.post(f"{job_url}/keep-truncated", json={}, headers=headers).status_code == 422
    kept = client.post(f"{job_url}/keep-truncated", json={"operator_agreed": True}, headers=headers)
    assert kept.status_code == 200 and kept.json()["status"] == "queued", kept.text
    run_next(db_session)

    polled = client.get(job_url, headers=headers).json()
    assert (polled["status"], polled["failure_kind"]) == ("completed", None), polled
    job = job_row(db_session, polled["job_id"])
    assert job.partial is True and f"{cut.complete} complete host" in job.parser_warnings
    assert host_ips(db_session, pid) == sorted(CASES["nmap_xml"].addresses)[:cut.complete]


def test_a_cut_zip_fails_like_any_unreadable_archive(db_session, test_project, tmp_path):
    case = CASES["eyewitness_zip"]
    folder = tmp_path / "zip"
    folder.mkdir()
    whole = case.build(folder, case.filename).read_bytes()
    path = folder / "cut.zip"
    path.write_bytes(whole[: len(whole) // 2])
    job = run_file(db_session, test_project.id, path, format_override="eyewitness_zip")
    assert job.status == "failed"
    assert _parse_error(db_session, job).error_type != TRUNCATED_FILE_ERROR
    _nothing_left(db_session, test_project.id, "eyewitness_zip")
