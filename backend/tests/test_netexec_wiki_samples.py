"""NetExec VNC / FTP / SSH lines as the nxc wiki prints them (v2.411.0).

``fixtures/native/netexec-wiki-vnc-ftp-ssh.txt`` is copied from the wiki's
protocol pages (vnc authentication, ftp file listing, ssh command execution).
Imported, they showed that a VNC login — a password alone — stored the
password as the username, and that "(No Auth:True)" is a flag on the banner.

Credentials that worked are what an analyst looks for, so the stored line —
shown in the inspector — keeps them as the tool wrote them.
"""
import os

from app.db import models
from app.db.models_confidence import NetexecResult
from app.parsers.netexec_parser import NetexecParser

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "native", "netexec-wiki-vnc-ftp-ssh.txt")


def _parse(db, project):
    scan = NetexecParser(db).parse_file(FIXTURE, "netexec-wiki.txt", project_id=project.id)
    return db.query(NetexecResult).filter(NetexecResult.scan_id == scan.id).all()


def test_login_lines_keep_the_credentials(db_session, test_project):
    rows = _parse(db_session, test_project)
    ftp = next(r for r in rows if r.protocol == "ftp" and r.auth_success)
    assert ftp.username == "marshall"
    assert ftp.raw_output.endswith("[+] marshall:badpassword")


def test_vnc_password_login_has_no_username(db_session, test_project):
    rows = _parse(db_session, test_project)
    vnc_login = next(r for r in rows if r.protocol == "vnc" and r.auth_success)
    assert vnc_login.username is None
    assert vnc_login.raw_output.endswith("[+] badpassword")


def test_vnc_no_auth_banner_is_kept_as_the_line(db_session, test_project):
    """Until the misconfiguration catalog lands, "(No Auth:True)" survives
    only as the stored line — which the inspector now shows."""
    rows = _parse(db_session, test_project)
    no_auth = [r for r in rows if r.protocol == "vnc" and "(No Auth:True)" in (r.raw_output or "")]
    assert {r.port for r in no_auth} == {5900, 5901}


def test_api_serves_the_line(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.66.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    scan = models.Scan(filename="old.txt", tool_name="netexec", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    db_session.add(NetexecResult(
        scan_id=scan.id, host_id=host.id, protocol="ssh", port=22, auth_success=True, username="user",
        raw_output="SSH 10.66.0.1 22 10.66.0.1 [+] user:hunter2",
    ))
    db_session.commit()
    resp = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/netexec")
    assert resp.status_code == 200
    (row,) = resp.json()
    assert row["raw_output"] == "SSH 10.66.0.1 22 10.66.0.1 [+] user:hunter2"


def test_api_serves_long_output_whole_and_marks_a_parser_cut(client, db_session, test_project):
    """v2.417.0 (review R08) — the API cut every line at 2 000 characters with
    no marker; module output past that point was unreachable."""
    from app.db.models_confidence import NETEXEC_RAW_OUTPUT_LIMIT
    host = models.Host(project_id=test_project.id, ip_address="10.66.0.2", state="up")
    db_session.add(host)
    db_session.flush()
    scan = models.Scan(filename="mod.txt", tool_name="netexec", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    long_line = "SMB 10.66.0.2 445 DC01 " + "A" * 3000
    db_session.add_all([
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", port=445, raw_output=long_line),
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="ldap", port=389,
                      raw_output="L" * NETEXEC_RAW_OUTPUT_LIMIT),
    ])
    db_session.commit()
    rows = {r["protocol"]: r for r in client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/netexec").json()}
    assert rows["smb"]["raw_output"] == long_line and rows["smb"]["raw_output_truncated"] is False
    assert rows["ldap"]["raw_output_truncated"] is True
