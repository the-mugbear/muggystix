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


def test_port_uses_tcp_transport_with_service_name():
    """NetExec's smb/ldap/winrm/mssql is the SERVICE, not the IP transport —
    the Port row must be tcp with the NXC protocol as service_name. Storing it
    in `protocol` made a physical port (445) collide-then-duplicate against the
    same port from an nmap tcp scan, inflating open_port_count."""
    from types import SimpleNamespace

    parser = NetexecParser(db=None)
    captured: dict = {}
    parser.dedup_service = SimpleNamespace(
        find_or_create_port=lambda host_id, scan_id, port_data: (
            captured.update(port_data) or SimpleNamespace(id=1)
        )
    )
    parser._track_field_confidence = lambda *a, **k: None

    for nxc_proto in ("smb", "ldap", "winrm"):
        captured.clear()
        parser._process_port_with_confidence(
            host_id=1, scan_id=1,
            host_data={"port": 445, "protocol": nxc_proto}, confidence=None,
        )
        assert captured["protocol"] == "tcp", nxc_proto
        assert captured["service_name"] == nxc_proto
        assert captured["port_number"] == 445
