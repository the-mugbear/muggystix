"""The operator reads a parser's own diagnosis, not a generic template.

UX review + production diagnostics 2026-09-26: a WhatWeb file the reader
refused raised the reader's own sentence, but the job and the Ingestion
Results row said only "The file may contain invalid JSON syntax". A NetExec
import showed its own reason and then "may be corrupted" after it.
"""
import json

import pytest

from app.parsers.streaming_json import iter_json_records
from app.services.parse_error_service import _generate_user_message


def _raised(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — the test wants the live exception
        return exc
    raise AssertionError("expected an exception")


def test_a_parser_diagnosis_is_the_message(tmp_path):
    # Invalid in the middle (a missing comma).  A file that is CUT SHORT is a
    # different failure with its own kind and sentence, and the dispatcher
    # words it: tests/test_truncated_file_keep.py.
    path = tmp_path / "whatweb.json"
    path.write_text('[{"target": "http://a"} {"target": "http://b"}]')
    err = _raised(lambda: list(iter_json_records(str(path), tool_label="whatweb JSON", threshold_bytes=1)))
    assert type(err) is ValueError
    msg = _generate_user_message(err, "parsing_error", "whatweb_json", "whatweb.json")
    assert msg.startswith("Invalid whatweb JSON during streaming")
    assert "invalid JSON syntax" not in msg


def test_a_value_error_from_outside_the_parsers_keeps_the_template():
    # The database driver raises plain ValueError for NUL in text; that is not
    # an operator's sentence.
    def driver():
        raise ValueError("A string literal cannot contain NUL (0x00) characters.")
    err = _raised(driver)
    msg = _generate_user_message(err, "parsing_error", "netexec_output", "a.txt")
    assert msg.startswith("Failed to parse the file 'a.txt'")


def test_library_json_errors_keep_the_template():
    err = _raised(lambda: json.loads("{not json"))  # JSONDecodeError, a ValueError subclass
    msg = _generate_user_message(err, "parsing_error", "nuclei_json", "x.json")
    assert msg.startswith("Failed to parse the JSON file 'x.json'")


@pytest.mark.parametrize("err", [ValueError(""), ValueError("never raised")])
def test_empty_or_unraised_value_error_keeps_the_template(err):
    msg = _generate_user_message(err, "parsing_error", None, "x.txt")
    assert msg.startswith("Failed to parse the file 'x.txt'")


def test_the_traceback_is_the_errors_own_wherever_it_is_logged(db_session, test_project):
    """Third review 2026-10-15, N12: logged outside an ``except`` (the
    cut-short path), ``format_exc()`` stored "NoneType: None"."""
    from app.services.parse_error_service import log_parse_error

    try:
        raise ValueError("cut here")
    except ValueError as exc:
        caught = exc
    row = log_parse_error(db_session, "f.json", error=caught, project_id=test_project.id)       # outside the except
    trace = row.error_details["traceback"]
    assert "NoneType: None" not in trace and "ValueError: cut here" in trace and "raise ValueError" in trace
    never = log_parse_error(db_session, "g.json", error=ValueError("never raised"), project_id=test_project.id)
    assert "traceback" not in never.error_details
