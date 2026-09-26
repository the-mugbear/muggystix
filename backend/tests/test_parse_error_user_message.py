"""The operator reads a parser's own diagnosis, not a generic template.

UX review + production diagnostics 2026-09-26: a truncated WhatWeb file
raised "Invalid or truncated whatweb JSON during streaming: premature EOF",
but the job and the Ingestion Results row said only "The file may contain
invalid JSON syntax". A NetExec import showed its own reason and then
"may be corrupted" after it.
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
    path = tmp_path / "whatweb.json"
    path.write_text('[{"target": "http://a"}, {"target": "http://b"')  # truncated
    err = _raised(lambda: list(iter_json_records(str(path), tool_label="whatweb JSON", threshold_bytes=1)))
    assert type(err) is ValueError
    msg = _generate_user_message(err, "parsing_error", "whatweb_json", "whatweb.json")
    assert msg.startswith("Invalid or truncated whatweb JSON during streaming")
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
