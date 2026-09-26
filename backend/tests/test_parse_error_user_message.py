"""The operator reads a parser's own diagnosis, not a generic hedge after it.

Report 2026-09-26: a failed NetExec import showed "NetExec parser found no
host lines …" and then "Failed to parse the file … may be corrupted".
"""
import json

from app.services.parse_error_service import _generate_user_message


def test_parser_value_error_is_the_message():
    err = ValueError("NetExec parser found no host lines in a.txt; file is empty or not NetExec output.")
    msg = _generate_user_message(err, "parsing_error", "netexec", "a.txt")
    assert msg == str(err)
    assert "corrupted" not in msg


def test_library_errors_keep_the_template():
    try:
        json.loads("{not json")
    except json.JSONDecodeError as err:  # a ValueError subclass
        msg = _generate_user_message(err, "parsing_error", "nuclei_json", "x.json")
    assert msg.startswith("Failed to parse the JSON file 'x.json'")


def test_empty_value_error_keeps_the_template():
    msg = _generate_user_message(ValueError(""), "parsing_error", None, "x.txt")
    assert msg.startswith("Failed to parse the file 'x.txt'")
