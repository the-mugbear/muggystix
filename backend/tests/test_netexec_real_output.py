"""NetExec console output as nxc really writes it (v2.374.5).

The sample fixture used single-word hostnames and bare lines, so the parser's
anchored ``\\w+`` patterns looked fine — while a real capture (hyphenated
Windows default names, an FQDN in the LDAP column, ANSI colour codes from a
terminal capture, the ``--log`` timestamp prefix) matched NOTHING and imported
as a completed scan with "no hosts".
"""
import pytest

from app.db import models
from app.parsers import content_detection as cd
from app.parsers.netexec_parser import NetexecParser

BANNER = (
    "{proto:<11} {ip:<15} {port:<6} {host:<16} [*] Windows Server 2022 Build 20348 x64 "
    "(name:{host}) (domain:corp.local) (signing:False) (SMBv1:False)"
)


def _parse(db, project, tmp_path, text, name="sweep.txt"):
    path = tmp_path / name
    path.write_text(text)
    scan = NetexecParser(db).parse_file(str(path), name, project_id=project.id)
    return {
        h.ip_address: h
        for h in db.query(models.Host).filter(models.Host.project_id == project.id)
    }, scan


def test_hyphenated_and_fqdn_hostnames_are_parsed(db_session, test_project, tmp_path):
    text = "\n".join([
        BANNER.format(proto="SMB", ip="10.9.1.1", port=445, host="WIN-7A8BC9D"),
        r"SMB         10.9.1.2        445    DESKTOP-AB12     [+] corp.local\bob:Passw0rd (Pwn3d!)",
        r"LDAP        10.9.1.3        389    dc01.corp.local  [+] corp.local\bob:Passw0rd",
    ])
    hosts, _ = _parse(db_session, test_project, tmp_path, text)
    assert set(hosts) == {"10.9.1.1", "10.9.1.2", "10.9.1.3"}
    assert hosts["10.9.1.1"].hostname == "WIN-7A8BC9D"
    assert hosts["10.9.1.1"].smb_signing == "disabled"


def test_ansi_coloured_capture_is_parsed(db_session, test_project, tmp_path):
    line = (
        "\x1b[1m\x1b[34mSMB\x1b[0m         10.9.2.1        445    DC01             "
        "\x1b[1m\x1b[34m[*]\x1b[0m Windows 10 / Server 2019 Build 17763 x64 "
        "(name:DC01) (domain:corp.local) (signing:True) (SMBv1:False)"
    )
    assert cd.looks_like_netexec(line.encode(), "capture.txt")
    hosts, _ = _parse(db_session, test_project, tmp_path, line)
    assert hosts["10.9.2.1"].hostname == "DC01"
    assert hosts["10.9.2.1"].smb_signing == "enabled"


def test_log_file_prefix_is_parsed(db_session, test_project, tmp_path):
    line = "2026-09-21 10:00:01,123 - INFO - " + BANNER.format(
        proto="SMB", ip="10.9.3.1", port=445, host="FILE01"
    )
    hosts, _ = _parse(db_session, test_project, tmp_path, line)
    assert hosts["10.9.3.1"].hostname == "FILE01"


def test_samba_banner_keeps_name_and_signing(db_session, test_project, tmp_path):
    line = (
        "SMB         10.9.4.1        445    NAS              [*] Unix - Samba "
        "(name:NAS) (domain:NAS) (signing:False) (SMBv1:False)"
    )
    hosts, _ = _parse(db_session, test_project, tmp_path, line)
    assert hosts["10.9.4.1"].smb_signing == "disabled"


def test_no_host_lines_fails_instead_of_completing_empty(db_session, test_project, tmp_path):
    with pytest.raises(ValueError, match="no host lines"):
        _parse(db_session, test_project, tmp_path, "[*] Initializing SMB protocol database\n")
