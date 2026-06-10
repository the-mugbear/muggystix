"""NetExec credential parsing — the identity must be captured so the
subnet-insights weak-auth lens works (previously username was never stored,
so every authenticated host falsely read as weak).
"""
import pytest

from app.parsers.netexec_parser import NetexecParser


@pytest.mark.parametrize("details,expected", [
    (r"EXAMPLE\administrator:P@ss (Pwn3d!)", ("EXAMPLE", "administrator")),
    (r"EXAMPLE\guest: ", ("EXAMPLE", "guest")),
    (r"\:", (None, "")),                 # null session — blank username
    (r"WORKGROUP\:", ("WORKGROUP", "")),
    (r"CORP\svc_backup:hunter2", ("CORP", "svc_backup")),
    ("", (None, None)),
])
def test_parse_credential(details, expected):
    assert NetexecParser._parse_credential(details) == expected


def test_auth_success_line_captures_username():
    import re
    rx = re.compile(r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\w+\s+\[\+\]\s+(.*)')
    line = r"SMB  10.0.0.5  445  HOSTX  [+] CORP\administrator:Passw0rd (Pwn3d!)"
    m = rx.match(line)
    assert m is not None
    parsed = NetexecParser(db=None)._parse_auth_success_line(m, line)
    assert parsed["username"] == "administrator"
    assert parsed["domain"] == "CORP"
    assert parsed["auth_success"] is True
