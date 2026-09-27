"""NetExec command/module results are not logins, and nxc's "None" hostname
is no name (v2.428.4; MCP acceptance run, agent feedback #12 and #15).

From a real capture (scan 547, ``windows-command--block-02.txt``):

    MSSQL  10.10.10.52  1433  None  [-] ERROR(MANTIS\\SQLEXPRESS): Line 1: The EXECUTE permission was denied …
    MSSQL  10.10.10.52  1433  None  [+] Executed command via mssqlexec

The first was stored as a FAILED login by user ``SQLEXPRESS)`` (domain
``ERROR(MANTIS``), the second as a SUCCESSFUL login by user ``Executed command
via mssqlexec``; the host's name became the string "None".
"""
from app.db import models
from app.db.models_confidence import NetexecResult
from app.parsers.netexec_parser import NetexecParser

ERROR_LINE = (
    "MSSQL       10.10.10.52     1433   None             [-] ERROR(MANTIS\\SQLEXPRESS): Line 1: "
    "The EXECUTE permission was denied on the object 'xp_cmdshell', database "
    "'mssqlsystemresource', schema 'sys'."
)
EXEC_LINE = "MSSQL       10.10.10.52     1433   None             [+] Executed command via mssqlexec"
LOGIN_LINE = "MSSQL       10.10.10.52     1433   None             [+] MANTIS\\sqlsvc:Summer2026! (Pwn3d!)"
FAILED_LOGIN = "MSSQL       10.10.10.53     1433   SQL02            [-] MANTIS\\sa:wrong (Login failed for user 'sa'.)"


def _parse(db, project, tmp_path, text):
    path = tmp_path / "windows-command.txt"
    path.write_text(text + "\n")
    NetexecParser(db).parse_file(str(path), path.name, project_id=project.id)
    hosts = {h.ip_address: h for h in db.query(models.Host).filter(models.Host.project_id == project.id)}
    rows = db.query(NetexecResult).all()
    return hosts, rows


def test_command_results_are_not_logins(db_session, test_project, tmp_path):
    hosts, rows = _parse(db_session, test_project, tmp_path, "\n".join([ERROR_LINE, EXEC_LINE]))
    assert "10.10.10.52" in hosts
    usernames = {r.username for r in rows}
    assert "SQLEXPRESS)" not in usernames
    assert "Executed command via mssqlexec" not in usernames
    # Neither line is a login attempt at all.
    assert all(r.auth_success is None for r in rows), [(r.username, r.auth_success) for r in rows]


def test_real_logins_on_the_same_protocol_still_read(db_session, test_project, tmp_path):
    _hosts, rows = _parse(db_session, test_project, tmp_path, "\n".join([LOGIN_LINE, FAILED_LOGIN]))
    by_user = {r.username: r for r in rows}
    assert by_user["sqlsvc"].auth_success is True
    assert by_user["sqlsvc"].local_admin is True
    assert by_user["sa"].auth_success is False


def test_nxc_none_hostname_is_no_name(db_session, test_project, tmp_path):
    hosts, _rows = _parse(db_session, test_project, tmp_path, "\n".join([EXEC_LINE, LOGIN_LINE]))
    assert hosts["10.10.10.52"].hostname in (None, "")


def test_repair_clears_rows_stored_by_the_old_rules(db_session, test_project, tmp_path):
    """Rows written before the fix are corrected from their own line."""
    from app.services.netexec_repair import repair_netexec_results

    scan = models.Scan(project_id=test_project.id, filename="old.txt")
    host = models.Host(project_id=test_project.id, ip_address="10.10.10.52", hostname="None")
    db_session.add_all([scan, host])
    db_session.flush()
    bad = NetexecResult(scan_id=scan.id, host_id=host.id, protocol="mssql", auth_success=False,
                        username="SQLEXPRESS)", raw_output=ERROR_LINE, hostname="None")
    good = NetexecResult(scan_id=scan.id, host_id=host.id, protocol="mssql", auth_success=True,
                         username="sqlsvc", raw_output=LOGIN_LINE)
    db_session.add_all([bad, good])
    db_session.commit()

    dry = repair_netexec_results(db_session, project_id=test_project.id)
    assert dry == {"logins_cleared": 1, "host_names_cleared": 1, "result_names_cleared": 1}
    db_session.refresh(bad)
    assert bad.username == "SQLEXPRESS)"  # a dry run changes nothing

    repair_netexec_results(db_session, project_id=test_project.id, apply=True)
    db_session.commit()
    db_session.refresh(bad); db_session.refresh(good); db_session.refresh(host)
    assert (bad.auth_success, bad.username, bad.hostname) == (None, None, None)
    assert (good.auth_success, good.username) == (True, "sqlsvc")
    assert host.hostname is None
    # Idempotent.
    assert repair_netexec_results(db_session, project_id=test_project.id, apply=True) == {
        "logins_cleared": 0, "host_names_cleared": 0, "result_names_cleared": 0}
