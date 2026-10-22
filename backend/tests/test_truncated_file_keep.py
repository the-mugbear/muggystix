"""A file that was cut short: say so, ask, and keep what it holds only when
a person says to (owner decision 2026-10-11: "Ask the user if it's a partial
file whether they want to keep partial results").

Production: a 610 MB WhatWeb JSON failed after 171 s with "Invalid or
truncated whatweb JSON during streaming: parse error: premature EOF" having
read 28,253 hosts, all of which the cleanup then removed.  The same file had
failed once before; nothing told the operator it could never succeed, and
nothing let them keep what was in it.

What is pinned here:

* the shared JSON reader tells a document that ENDS EARLY from invalid
  content, at any size, and counts the complete records before the cut;
* such an import fails as ``truncated_file`` (``parse_errors.error_type``,
  served as ``failure_kind``), leaves nothing behind, and says how much the
  file holds;
* ``POST …/retry {"keep_truncated": true}`` imports exactly the complete
  records, finishes PARTIAL with a warning naming the count, carries its
  upload identity, needs someone until dismissed, and makes an identical
  upload a duplicate;
* an invalid file never gets the kind and keeps nothing, option or not; a
  file cut inside its first record has nothing to keep and is not offered;
* the instruction is one attempt's; a viewer or auditor cannot give it; a
  cancel during the kept attempt cleans up; agents read the kind, and the
  one call that keeps their own upload states that the operator agreed
  (``test_truncated_upload_agent.py``).

This file is the shared JSON reader and the job's life around the question.
The rule for EVERY format — XML, JSON lines, the parsers with their own
readers — is held in ``test_every_format_at_a_cut.py``.

Every import here runs under a real job (``ingestion_job_harness``).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.api.deps import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.parsers import streaming_json
from app.parsers.cut_short import TruncatedFile, begin_read, end_read
from app.parsers.streaming_json import iter_json_records
from app.services.ingestion_service import (
    KEEP_TRUNCATED_OPTION,
    TRUNCATED_FILE_ERROR,
    IngestionService,
)
from app.services.operations_read_service import blocked_import_condition
from tests.ingestion_job_harness import host_ips, job_row, on_heartbeat, run_next, scans_of

# Three heartbeats' worth: the WhatWeb parser is made to heartbeat (and so to
# COMMIT) every BEAT records here instead of every 500, so a streamed import
# has committed hosts before it reaches the cut without a fixture of
# thousands of hosts.
BEAT = 20
COMPLETE = 62


# --------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _heartbeat_every_few_records(monkeypatch):
    from app.parsers import parser_utils, whatweb_parser

    monkeypatch.setattr(
        whatweb_parser, "ProgressBeat",
        lambda label="records", every=500, before=None: parser_utils.ProgressBeat(label, BEAT, before),
    )


def _ip(i: int) -> str:
    return f"10.77.{i // 250}.{i % 250 + 1}"


def _whatweb_record(i: int, pad: int = 0) -> str:
    ip = _ip(i)
    return json.dumps({
        "target": f"http://{ip}/", "http_status": 200,
        "plugins": {"IP": {"string": [ip]}, "Title": {"string": [f"Site {i} " + "x" * pad]},
                    "nginx": {"version": ["1.18.0"]}},
    })


def whatweb_file(complete: int = COMPLETE, cut: str = "after") -> bytes:
    """WhatWeb ``--log-json`` as the tool writes it — ``[``, then one record
    per line with a trailing comma — stopped at ``cut``:

    ``after``  right after the last complete record's line;
    ``inside`` part-way through the record that follows it;
    ``first``  part-way through the very first record;
    ``none``   not cut: a closed document.
    """
    if cut == "first":
        return ("[\n" + _whatweb_record(0)[:40]).encode()
    lines = [_whatweb_record(i) for i in range(complete)]
    if cut == "none":
        return ("[\n" + ",\n".join(lines) + "\n]\n").encode()
    body = "[\n" + "".join(line + ",\n" for line in lines)
    if cut == "inside":
        body += _whatweb_record(complete)[:60]
    return body.encode()


def invalid_whatweb_file(before: int = 70, after: int = 5, pad: int = 4000) -> bytes:
    """A closed document with garbage between two records — invalid, not cut
    short.  The streaming reader hands records over a 64 KiB block at a time
    and meets the garbage when it parses the block that holds it, so the
    records are padded (about 15 to a block): with 70 before the garbage,
    several blocks' worth are delivered — and committed — first."""
    head = "".join(_whatweb_record(i, pad) + ",\n" for i in range(before))
    tail = ",\n".join(_whatweb_record(i, pad) for i in range(before, before + after))
    return ("[\n" + head + "@@@ not json @@@,\n" + tail + "\n]\n").encode()


@pytest.fixture
def streamed(monkeypatch):
    """Send every file through the streaming reader (production: 50 MB up)."""
    monkeypatch.setattr(streaming_json, "_DEFAULT_STREAM_THRESHOLD_BYTES", 1)


@pytest.fixture(params=["eager", "streamed"])
def reader_path(request, monkeypatch):
    if request.param == "streamed":
        monkeypatch.setattr(streaming_json, "_DEFAULT_STREAM_THRESHOLD_BYTES", 1)
    return request.param


def _upload(client, project, data: bytes, name: str = "whatweb-scan.json"):
    return client.post(
        f"/api/v1/projects/{project.id}/upload/",
        files={"file": (name, data, "application/json")},
    )


def _retry(client, project, job_id: int, body=None):
    url = f"/api/v1/projects/{project.id}/upload/jobs/{job_id}/retry"
    return client.post(url) if body is None else client.post(url, json=body)


def _run(db, svc=None):
    """Run the queued job; returns the progress strings its heartbeats sent."""
    svc = svc or IngestionService()
    with on_heartbeat(svc, 10**9, lambda: None) as beats:
        run_next(db, svc)
    return beats


def _parse_error(db, job) -> models.ParseError:
    assert job.parse_error_id is not None, job.error_message
    return db.get(models.ParseError, job.parse_error_id)


def _web_interface_ips(db, project_id: int):
    db.expire_all()
    return sorted(
        ip for (ip,) in db.query(models.WebInterface.ip_address)
        .filter(models.WebInterface.project_id == project_id)
    )


def _nothing_left(db, project_id: int) -> None:
    assert scans_of(db, project_id) == []
    assert host_ips(db, project_id) == []
    assert _web_interface_ips(db, project_id) == []
    assert db.query(models.HostScanHistory).count() == 0
    assert db.query(models.Port).count() == 0


def _needs_someone(db, project_id: int):
    db.expire_all()
    return {
        job_id for (job_id,) in db.query(models.IngestionJob.id)
        .filter(models.IngestionJob.project_id == project_id, blocked_import_condition())
    }


def _results_row(client, project, job_id: int, **params):
    r = client.get(
        f"/api/v1/projects/{project.id}/parse-errors/ingestion-results",
        params={"limit": 200, **params},
    )
    assert r.status_code == 200, r.text
    return next((item for item in r.json()["items"] if item["id"] == job_id), None)


def _job_reads(client, project, job_id: int):
    """The same job as the Scans page reads it: the list and the single GET."""
    base = f"/api/v1/projects/{project.id}/upload/jobs"
    listed = client.get(base, params={"ids": str(job_id)})
    single = client.get(f"{base}/{job_id}")
    assert listed.status_code == 200 and single.status_code == 200
    return listed.json()[0], single.json()


# ------------------------------------------------- the reader, on its own

def _doc(records, closed=True) -> bytes:
    body = "[" + ", ".join(json.dumps(r) for r in records)
    return (body + ("]" if closed else "")).encode()


THREE = [{"a": 1}, {"b": "x"}, {"c": [1, 2]}]

# (bytes, complete records before the cut).  The last five are the cuts the
# library does NOT call "premature EOF": it reports a "lexical error" for a
# cut inside a literal, an escape, a \u escape or a multi-byte character.
CUT_SHORT = {
    "after a record": (_doc(THREE, closed=False), 3),
    "after a comma": (_doc(THREE, closed=False) + b", ", 3),
    "inside a record": (_doc(THREE, closed=False) + b', {"d": 1, "e', 3),
    "inside a string": (_doc(THREE, closed=False) + b', {"d": "unfinish', 3),
    "inside a number": (_doc(THREE, closed=False) + b', {"d": 12', 3),
    "inside the first record": (b'[{"a": "unfin', 0),
    "only the opening bracket": (b"[", 0),
    "inside a literal": (_doc(THREE, closed=False) + b', {"d": tru', 3),
    "inside an escape": (_doc(THREE, closed=False) + b', {"d": "x\\', 3),
    "inside a unicode escape": (_doc(THREE, closed=False) + b', {"d": "x\\u00', 3),
    "inside a multi-byte character": (_doc(THREE, closed=False) + b', {"d": "caf\xc3', 3),
    "inside an object holding the array": (b'{"results": ' + _doc(THREE, closed=False) + b', {"d"', 3),
}

INVALID = {
    "a missing comma": b'[{"a": 1} {"b": 2}]',
    "garbage between records": b'[{"a": 1}, @@@, {"b": 2}]',
    "a bad literal": b'[{"a": 1}, {"b": trux}, {"c": 2}]',
    "text after the document": _doc(THREE) + b" trailing",
    "a tail of NUL bytes": _doc(THREE, closed=False) + b', {"d": "x' + b"\x00" * 32,
}


def _read(tmp_path, data: bytes, *, keep: bool = False, array_keys=("results",)):
    path = tmp_path / "doc.json"
    path.write_bytes(data)
    token = begin_read(keep_truncated=keep)
    records, error = [], None
    try:
        for record in iter_json_records(str(path), array_keys=array_keys, tool_label="whatweb JSON"):
            records.append(record)
    except Exception as exc:  # noqa: BLE001 — the test wants the live exception
        error = exc
    return records, error, end_read(token)


@pytest.mark.parametrize("case", sorted(CUT_SHORT))
def test_a_document_that_ends_early_is_told_apart(tmp_path, reader_path, case):
    data, complete = CUT_SHORT[case]
    records, error, report = _read(tmp_path, data)
    assert isinstance(error, TruncatedFile), (case, error)
    assert error.records_read == complete
    assert report.cut_short is error
    if reader_path == "streamed":
        # The complete records were handed over before the cut was reached.
        assert len(records) == complete
    else:
        # A small file is refused before anything is handed over.
        assert records == []
    said = str(error)
    assert "cut short" in said
    assert ("3 complete records" in said) if complete else ("before its first record was complete" in said)


@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_content_is_never_called_cut_short(tmp_path, reader_path, case):
    for keep in (False, True):
        _records, error, report = _read(tmp_path, INVALID[case], keep=keep)
        assert not isinstance(error, TruncatedFile), (case, error)
        assert report.cut_short is None, case
        if reader_path == "streamed":
            # As before this change: the reader's own sentence, a plain ValueError.
            assert type(error) is ValueError and str(error).startswith("Invalid whatweb JSON during streaming")


@pytest.mark.parametrize("case", sorted(k for k, v in CUT_SHORT.items() if v[1]))
def test_asked_to_keep_the_reader_stops_at_the_cut(tmp_path, reader_path, case):
    data, complete = CUT_SHORT[case]
    records, error, report = _read(tmp_path, data, keep=True)
    assert error is None
    # Exactly the complete records: never half of the one the cut fell in.
    assert records == THREE
    assert report.cut_short is not None and report.cut_short.records_read == complete


@pytest.mark.parametrize("case", sorted(k for k, v in CUT_SHORT.items() if not v[1]))
def test_nothing_complete_is_nothing_to_keep(tmp_path, reader_path, case):
    records, error, _report = _read(tmp_path, CUT_SHORT[case][0], keep=True)
    assert records == [] and isinstance(error, TruncatedFile) and error.records_read == 0


def test_a_closed_document_is_read_whole_either_way(tmp_path, reader_path):
    for keep in (False, True):
        records, error, report = _read(tmp_path, _doc(THREE), keep=keep)
        assert (records, error, report.cut_short, report.rejected_lines) == (THREE, None, None, 0)


def test_json_lines_with_a_cut_last_line_are_asked_about_too(tmp_path, reader_path):
    """Owner, 2026-10-11 (second decision): every format in which a cut can be
    seen.  JSON lines whose LAST line was cut — it does not decode, no line
    ending follows it, and it is the beginning of a JSON value — are a file
    that ends early: raised without the instruction, the complete lines with
    it.  (Until then the line was skipped and counted without asking.)"""
    data = b'{"a": 1}\n{"b": "x"}\n{"c": "unfin'
    records, error, report = _read(tmp_path, data)
    assert isinstance(error, TruncatedFile) and (error.records_read, error.in_lines) == (2, True)
    assert str(error) == (
        "This whatweb JSON file was cut short: it ends after 2 complete records, "
        "part-way through the line that follows."
    )
    assert report.cut_short is error and report.rejected_lines == 0

    records, error, report = _read(tmp_path, data, keep=True)
    assert error is None and records == [{"a": 1}, {"b": "x"}]
    assert report.cut_short is not None and report.rejected_lines == 0
    assert report.cut_short.kept.endswith(
        "Those records were imported; anything the tool wrote after that point is not in the file."
    )


JSON_LINES_NOT_CUT = {
    # A line that does not decode in the MIDDLE: skipped and counted.
    "an undecodable line in the middle": (b'{"a": 1}\n{"b": "unfin\n{"c": 3}\n', [{"a": 1}, {"c": 3}], 1),
    # The last line does not decode but a line ending follows it: the writer
    # finished the line, so it is a bad line, not a cut one.
    "a bad last line that was ended": (b'{"a": 1}\n{"b": "unfin\n', [{"a": 1}], 1),
    # The last line has no line ending but is not the beginning of JSON.
    "a last line that is not JSON": (b'{"a": 1}\n{"b": 2}\n@@@ garbage', [{"a": 1}, {"b": 2}], 1),
    "a last line with text after a value": (b'{"a": 1}\n{"b": 2} trailing', [{"a": 1}], 1),
    # A complete last line needs no line ending.
    "a complete last line with no line ending": (b'{"a": 1}\n{"b": 2}', [{"a": 1}, {"b": 2}], 0),
}


@pytest.mark.parametrize("case", sorted(JSON_LINES_NOT_CUT))
def test_json_lines_that_are_not_cut_are_read_as_they_always_were(tmp_path, reader_path, case):
    """Invalid content in JSON lines is skipped and counted (v2.419.0: the
    import is partial), with or without the instruction, and never called
    cut short."""
    data, expected, rejected = JSON_LINES_NOT_CUT[case]
    for keep in (False, True):
        records, error, report = _read(tmp_path, data, keep=keep)
        assert (error, records) == (None, expected), case
        assert (report.rejected_lines, report.cut_short) == (rejected, None), case


def test_the_parsers_on_the_shared_reader_are_the_documented_ones():
    """The feature covers every parser that reads through the shared reader.
    A twelfth joins documentation/PARSERS.md ("A file that was cut short")
    with this list."""
    parsers = Path(streaming_json.__file__).parent
    found = {
        path.stem.removesuffix("_parser") for path in parsers.glob("*_parser.py")
        if "iter_json_records(" in path.read_text()
    }
    assert found == {
        "amass", "dirbuster", "dnsx", "eyewitness", "httpx", "naabu",
        "nikto", "nuclei", "smbmap", "testssl", "whatweb",
    }


# --------------------------------------------- fail as cut short, then keep

@pytest.mark.parametrize("cut", ["after", "inside"])
def test_a_cut_short_import_fails_as_that_and_is_kept_only_when_asked(
    client, db_session, test_project, test_user, reader_path, cut,
):
    pid = test_project.id
    data = whatweb_file(COMPLETE, cut)
    job_id = _upload(client, test_project, data).json()["job_id"]

    # 1. Without the instruction: a failure, said as what it is, nothing kept.
    beats = _run(db_session)
    job = job_row(db_session, job_id)
    assert job.status == "failed"
    if reader_path == "streamed":
        assert beats, "the import committed hosts before it reached the cut"
    _nothing_left(db_session, pid)
    error = _parse_error(db_session, job)
    assert error.error_type == TRUNCATED_FILE_ERROR == "truncated_file"
    assert error.error_message == (
        f"This WhatWeb JSON file was cut short: it ends after {COMPLETE} complete records, "
        "before the end of the document."
    )
    assert job.error_message == f"{error.error_message} Nothing from this file was imported."
    assert KEEP_TRUNCATED_OPTION not in (job.options or {})

    row = _results_row(client, test_project, job_id)
    assert row["failure_kind"] == "truncated_file"
    assert row["failure_reason"] == error.error_message
    assert row["error"]["error_type"] == "truncated_file"
    assert row["file_retained"] is True
    for read in _job_reads(client, test_project, job_id):
        assert read["failure_kind"] == "truncated_file"
        assert read["failure_reason"] == error.error_message
    assert job_id in _needs_someone(db_session, pid)

    # 2. The operator's answer: import what the file holds.
    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    queued = job_row(db_session, job_id)
    assert queued.status == "queued" and queued.options[KEEP_TRUNCATED_OPTION] is True
    assert queued.parse_error_id is None
    _run(db_session)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert job.partial is True
    assert job.skipped_count == 0
    assert job.parser_warnings == (
        f"This WhatWeb JSON file was cut short: it ends after {COMPLETE} complete records, "
        "before the end of the document. Those records were imported; anything the tool "
        "wrote after that point is not in the file."
    )
    assert job.in_progress_scan_id is None

    # Exactly the complete records; nothing of the record the cut fell in.
    expected = sorted(_ip(i) for i in range(COMPLETE))
    assert _web_interface_ips(db_session, pid) == expected
    assert host_ips(db_session, pid) == expected
    assert _ip(COMPLETE) not in host_ips(db_session, pid)
    (scan,) = scans_of(db_session, pid)
    assert job.scan_id == scan.id
    assert (
        db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count() == COMPLETE
    )

    # Its upload identity, like any completed import.
    assert scan.content_sha256 == hashlib.sha256(data).hexdigest() == job.content_sha256
    assert scan.uploaded_by_id == test_user.id

    # Partial: it needs someone, everywhere that is counted, until dismissed.
    assert job_id in _needs_someone(db_session, pid)
    row = _results_row(client, test_project, job_id, status="needs_attention")
    assert row is not None and row["partial"] is True and row["status"] == "completed"
    assert row["failure_kind"] is None
    assert row["parser_warnings"] == job.parser_warnings
    for read in _job_reads(client, test_project, job_id):
        assert read["partial"] is True and read["failure_kind"] is None

    # Finished: nothing re-queues it, and it cannot be retried into a loop.
    assert run_next(db_session) is False
    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 409

    # 3. The same bytes again are a duplicate of the kept import.
    again = _upload(client, test_project, data)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "duplicate_scan"
    assert again.json()["detail"]["scan_id"] == scan.id

    # 4. Dismissed like any partial import.
    dismissed = client.post(f"/api/v1/projects/{pid}/upload/jobs/{job_id}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert job_id not in _needs_someone(db_session, pid)
    assert job_row(db_session, job_id).partial is True


def test_a_complete_file_imports_whole_with_or_without_the_instruction(
    client, db_session, test_project, streamed,
):
    """The instruction permits stopping at a cut; it changes nothing else."""
    job_id = _upload(client, test_project, whatweb_file(40, "none")).json()["job_id"]
    _run(db_session)
    job = job_row(db_session, job_id)
    assert (job.status, job.partial, job.parser_warnings) == ("completed", False, None)
    assert len(_web_interface_ips(db_session, test_project.id)) == 40


def test_a_file_cut_inside_its_first_record_has_nothing_to_keep(
    client, db_session, test_project, reader_path,
):
    pid = test_project.id
    job_id = _upload(client, test_project, whatweb_file(cut="first")).json()["job_id"]
    sentence = (
        "This WhatWeb JSON file was cut short before its first record was complete: "
        "there is nothing in it to import."
    )
    for attempt in ("as uploaded", "asked to keep"):
        _run(db_session)
        job = job_row(db_session, job_id)
        assert job.status == "failed", attempt
        _nothing_left(db_session, pid)
        error = _parse_error(db_session, job)
        # Said in words; NOT the kind that offers to keep — there is nothing.
        assert error.error_message == sentence, attempt
        assert error.error_type == "parsing_error", attempt
        row = _results_row(client, test_project, job_id)
        assert row["failure_kind"] == "parsing_error" and row["failure_reason"] == sentence
        if attempt == "as uploaded":
            assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200


def test_an_invalid_file_is_never_offered_and_never_kept(
    client, db_session, test_project, streamed,
):
    pid = test_project.id
    job_id = _upload(client, test_project, invalid_whatweb_file()).json()["job_id"]
    for attempt in ("as uploaded", "keep forced through the API"):
        beats = _run(db_session)
        job = job_row(db_session, job_id)
        assert job.status == "failed", attempt
        assert beats, "hosts were committed before the reader met the garbage"
        _nothing_left(db_session, pid)
        assert _parse_error(db_session, job).error_type == "parsing_error", attempt
        row = _results_row(client, test_project, job_id)
        assert row["failure_kind"] == "parsing_error", attempt
        assert "cut short" not in (row["failure_reason"] or ""), attempt
        if attempt == "as uploaded":
            # The route takes the instruction for any failed job; the reader
            # decides, and it only ever stops at a cut.
            assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
            assert job_row(db_session, job_id).options[KEEP_TRUNCATED_OPTION] is True


def test_a_small_invalid_document_is_read_as_it_always_was(client, db_session, test_project):
    """Below the streaming threshold a document ``json.loads`` refuses has
    always gone to the line reader, which keeps the lines that decode on their
    own and counts the rest (v2.419.0: skipped and partial).  That is not
    this change's to alter: it is never called cut short, and it is the same
    import whether or not anything was asked."""
    pid = test_project.id
    job_id = _upload(client, test_project, invalid_whatweb_file(before=20, after=3)).json()["job_id"]
    _run(db_session)
    job = job_row(db_session, job_id)
    # Only the last line stands alone as JSON (every other ends in a comma).
    assert (job.status, job.partial) == ("completed", True)
    assert _web_interface_ips(db_session, pid) == [_ip(22)]
    assert "cut short" not in (job.parser_warnings or "")
    assert "were not valid JSON and were skipped" in job.parser_warnings
    for read in _job_reads(client, test_project, job_id):
        assert read["failure_kind"] is None


def test_another_parser_on_the_shared_reader_keeps_too(client, db_session, test_project, reader_path):
    """naabu collects the whole file before it writes (whatweb writes as it
    reads): the reader stops both the same way."""
    data = b'[{"ip": "10.78.0.1", "port": 80}, {"ip": "10.78.0.2", "port": 443}, {"ip": "10.78.0.3", "po'
    job_id = _upload(client, test_project, data, "naabu-out.json").json()["job_id"]
    _run(db_session)
    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert _parse_error(db_session, job).error_type == "truncated_file"
    assert "2 complete records" in job.error_message
    _nothing_left(db_session, test_project.id)

    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    _run(db_session)
    job = job_row(db_session, job_id)
    assert (job.status, job.partial) == ("completed", True), job.error_message
    assert host_ips(db_session, test_project.id) == ["10.78.0.1", "10.78.0.2"]
    assert "2 complete records" in job.parser_warnings


# ------------------------------------------- one attempt's instruction only

def test_a_cancel_during_the_kept_attempt_cleans_up_and_the_instruction_ends_with_it(
    client, db_session, test_project, streamed,
):
    pid = test_project.id
    job_id = _upload(client, test_project, whatweb_file(COMPLETE, "inside")).json()["job_id"]
    _run(db_session)
    assert job_row(db_session, job_id).status == "failed"

    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    svc = IngestionService()
    # At the second heartbeat the first BEAT records are committed.
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)) as beats:
        run_next(db_session, svc)
    assert len(beats) >= 2
    job = job_row(db_session, job_id)
    assert job.status == "failed" and job.error_message == "Cancelled by user"
    assert job.scan_id is None and job.in_progress_scan_id is None
    _nothing_left(db_session, pid)
    (read, _single) = _job_reads(client, test_project, job_id)
    assert read["failure_kind"] is None  # a cancel is not a cut-short file

    # A plain retry is a plain retry: the instruction was that attempt's.
    assert _retry(client, test_project, job_id).status_code == 200
    assert KEEP_TRUNCATED_OPTION not in job_row(db_session, job_id).options
    _run(db_session)
    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert _parse_error(db_session, job).error_type == "truncated_file"
    _nothing_left(db_session, pid)


def test_a_dismissed_failure_that_is_kept_needs_someone_again(client, db_session, test_project):
    """The dismissal acknowledged the failure, not the partial import that
    replaced it."""
    pid = test_project.id
    job_id = _upload(client, test_project, whatweb_file(30, "inside")).json()["job_id"]
    _run(db_session)
    assert client.post(f"/api/v1/projects/{pid}/upload/jobs/{job_id}/dismiss").status_code == 200
    assert job_id not in _needs_someone(db_session, pid)

    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    assert job_row(db_session, job_id).dismissed_at is None
    _run(db_session)
    job = job_row(db_session, job_id)
    assert (job.status, job.partial) == ("completed", True)
    assert job_id in _needs_someone(db_session, pid)


def test_a_reprocess_does_not_inherit_the_instruction(client, db_session, test_project):
    pid = test_project.id
    job_id = _upload(client, test_project, whatweb_file(30, "inside")).json()["job_id"]
    _run(db_session)
    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    _run(db_session)
    assert job_row(db_session, job_id).status == "completed"

    r = client.post(f"/api/v1/projects/{pid}/upload/jobs/{job_id}/reprocess", json={})
    assert r.status_code == 200, r.text
    new = job_row(db_session, r.json()["id"])
    assert KEEP_TRUNCATED_OPTION not in new.options
    _run(db_session)
    new = job_row(db_session, new.id)
    assert new.status == "failed"
    assert _parse_error(db_session, new).error_type == "truncated_file"
    # The kept import is untouched by the failed re-process.
    assert len(scans_of(db_session, pid)) == 1
    assert len(host_ips(db_session, pid)) == 30


# ------------------------------------------------------------------ roles

def _member(db, project, user_id: int, username: str, role: ProjectRole) -> User:
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


@pytest.mark.parametrize("role", [ProjectRole.VIEWER, ProjectRole.AUDITOR])
def test_a_viewer_or_auditor_cannot_ask_to_keep(client, db_session, test_project, role):
    job_id = _upload(client, test_project, whatweb_file(30, "inside")).json()["job_id"]
    _run(db_session)
    reader = _member(db_session, test_project, 700 + len(role.value), f"keep-{role.value}", role)

    app.dependency_overrides[get_current_user] = lambda: reader
    refused = _retry(client, test_project, job_id, {"keep_truncated": True})
    assert refused.status_code == 403, refused.text

    job = job_row(db_session, job_id)
    assert job.status == "failed" and KEEP_TRUNCATED_OPTION not in (job.options or {})
    _nothing_left(db_session, test_project.id)


def test_an_analyst_can_keep_their_own_upload(client, db_session, test_project):
    analyst = _member(db_session, test_project, 710, "keep-analyst", ProjectRole.ANALYST)
    app.dependency_overrides[get_current_user] = lambda: analyst
    job_id = _upload(client, test_project, whatweb_file(30, "inside")).json()["job_id"]
    _run(db_session)
    assert _retry(client, test_project, job_id, {"keep_truncated": True}).status_code == 200
    _run(db_session)
    assert (job_row(db_session, job_id).status, job_row(db_session, job_id).partial) == ("completed", True)


# ----------------------------------------------------------------- agents

@pytest.fixture
def agent_key(db_session, test_project, test_agent):
    from app.db.models_agent import AgentSessionWorkflow
    from app.db.models_auth import APIKey
    from app.services.agent_session_service import create_agent_session

    session = create_agent_session(
        db_session, workflow=AgentSessionWorkflow.PROJECT.value,
        project_id=test_project.id, agent_id=test_agent.id, started_by_id=None,
    )
    db_session.commit()
    raw = "nm_agent_cut_short_" + "k" * 30
    db_session.add(APIKey(
        agent_id=test_agent.id, agent_session_id=session.id, name="cut-short",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


def test_an_agent_reads_the_kind_in_the_same_words(client, db_session, test_project, agent_key):
    headers = {"X-API-Key": agent_key}
    up = client.post(
        "/api/v1/agent/uploads", headers=headers,
        files={"file": ("whatweb-scan.json", whatweb_file(30, "inside"), "application/json")},
        data={"tool_name": "whatweb"},
    )
    assert up.status_code == 201, up.text
    job_id = up.json()["job_id"]
    _run(db_session)
    sentence = (
        "This WhatWeb JSON file was cut short: it ends after 30 complete records, "
        "before the end of the document. Nothing from this file was imported."
    )

    polled = client.get(f"/api/v1/agent/uploads/{job_id}", headers=headers)
    assert polled.status_code == 200, polled.text
    assert polled.json()["status"] == "failed"
    assert polled.json()["failure_kind"] == "truncated_file"
    assert polled.json()["error_message"] == sentence

    issues = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers)
    assert issues.status_code == 200, issues.text
    (issue,) = [i for i in issues.json()["items"] if i["job_id"] == job_id]
    assert (issue["kind"], issue["failure_kind"], issue["needs_attention"]) == ("failed", "truncated_file", True)
    assert issue["message"] == sentence

    # The page says the same.
    row = _results_row(client, test_project, job_id)
    assert row["failure_kind"] == "truncated_file" and row["error"]["user_message"] == sentence


def test_the_instruction_has_two_doors_and_an_agents_states_the_operator_agreed():
    """"Ask the user": keeping a cut-short file is the operator's answer.
    A person gives it on the retry route (the one body that carries
    ``keep_truncated``).  An agent may give it for its own upload only by
    stating that it asked (owner, 2026-10-11: ``POST
    /agent/uploads/{job_id}/keep-truncated`` takes ``operator_agreed`` and
    nothing else — ``tests/test_truncated_upload_agent.py``); no agent body
    carries the option itself, and an agent's upload cannot smuggle it in."""
    spec = app.openapi()
    carrying = sorted(
        name for name, schema in spec["components"]["schemas"].items()
        if KEEP_TRUNCATED_OPTION in (schema.get("properties") or {})
    )
    assert carrying == ["RetryJobRequest"]
    using = sorted(path for path, ops in spec["paths"].items() if "RetryJobRequest" in json.dumps(ops))
    assert using == ["/api/v1/projects/{project_id}/upload/jobs/{job_id}/retry"]
    assert not [path for path in spec["paths"] if "/agent/" in path and path.endswith("/retry")]
    agents = sorted(path for path in spec["paths"] if "/agent/" in path and "keep" in path)
    assert agents == ["/api/v1/agent/uploads/{job_id}/keep-truncated"]
    body = spec["components"]["schemas"]["KeepTruncatedUploadRequest"]
    assert set(body["properties"]) == {"operator_agreed"}


@pytest.mark.asyncio
async def test_an_upload_cannot_carry_the_instruction(db_session, test_project, monkeypatch, tmp_path):
    import io

    from fastapi import UploadFile

    from app.core.config import settings

    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    job = await IngestionService().create_job(
        db=db_session,
        upload=UploadFile(filename="whatweb-scan.json", file=io.BytesIO(whatweb_file(5, "inside"))),
        submitted_by_id=None,
        options={"project_id": test_project.id, KEEP_TRUNCATED_OPTION: True},
    )
    assert KEEP_TRUNCATED_OPTION not in job.options


def test_a_job_says_whether_its_file_is_still_kept():
    """Third review 2026-10-15, N6: the Scans queue offered "Import what the
    file holds" with no way to know the file was gone (a 409 then).  The job
    read carries the row's answer, ``staged_import_service.file_retained``."""
    from app.schemas.schemas import IngestionJobSchema

    def job(**over):
        return models.IngestionJob(**{
            "id": 1, "filename": "f", "original_filename": "f.json", "status": "failed",
            "created_at": datetime.now(timezone.utc), "storage_path": "/uploads/x", **over,
        })

    assert IngestionJobSchema.model_validate(job()).file_retained is True
    assert IngestionJobSchema.model_validate(job(file_removed_at=datetime.now(timezone.utc))).file_retained is False
    assert IngestionJobSchema.model_validate(job(storage_path=None)).file_retained is False
