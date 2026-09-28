"""NetExec NFS enumeration (v2.430.0).

`nxc nfs --shares` prints each server's exports as a table the parser did not
recognise, so every row was dropped: production 2026-09-28 lost 2 364 lines
over ~600 NFS servers, and the one `(root escape:True)` was kept as text only.
An analyst who confirmed NFS exports with nxc found nothing to filter on.

The fixture is generated from NetExec main's own format strings
(nxc/logger.py `format()`, nxc/protocols/nfs.py `print_host_info()` and
`shares()`), and its rows have the shapes the production import reported.
"""
from __future__ import annotations

import os

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
from app.parsers.netexec_parser import NetexecParser, netexec_line_checks, writable_share
from app.services.misconfig_backfill import backfill_misconfigs
from app.services.misconfig_checks import CHECKS, vuln_kind

NATIVE = os.path.join(os.path.dirname(__file__), "fixtures", "native")


def _parse(db, project):
    parser = NetexecParser(db)
    scan = parser.parse_file(os.path.join(NATIVE, "netexec-nfs.txt"), "netexec-nfs.txt", project_id=project.id)
    return parser, scan


def _host(db, project, ip):
    return db.query(models.Host).filter_by(project_id=project.id, ip_address=ip).one()


def _row(db, scan, host, protocol):
    return db.query(NetexecResult).filter_by(scan_id=scan.id, host_id=host.id, protocol=protocol).one()


def _checks(db, scan, host):
    return {
        (v.plugin_id, v.port.port_number if v.port else None)
        for v in db.query(Vulnerability).filter_by(scan_id=scan.id, host_id=host.id)
    }


def test_the_export_table_is_stored_on_the_nfs_row(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    row = _row(db_session, scan, _host(db_session, test_project, "10.8.20.5"), "nfs")
    by_name = {s["name"]: s for s in row.shares}
    assert set(by_name) == {"/srv/export", "/home", "/backup"}
    assert by_name["/srv/export"]["permissions"] == "rwx"
    assert by_name["/srv/export"]["access_list"] == ["Everyone"]
    assert by_name["/srv/export"]["storage"] == "1.2GB/9.8GB"
    assert by_name["/home"]["access_list"] == ["10.8.0.0/16", "10.9.0.0/16"]
    # nxc could not mount it: no permissions, not "---".
    assert by_name["/backup"]["permissions"] is None
    assert "could not mount" in by_name["/backup"]["remark"]
    # The inspector's share list reads {name, permissions, remark}.
    assert "mountable from: Everyone" in by_name["/srv/export"]["remark"]
    assert row.writable_share is True


def test_a_long_access_list_and_a_share_wider_than_its_column(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    row = _row(db_session, scan, _host(db_session, test_project, "10.8.20.6"), "nfs")
    by_name = {s["name"]: s for s in row.shares}
    # "[-] Failed to list share: /scratch" mid-table does not end it, and the
    # --enum-shares listing that follows is not read as exports.
    assert set(by_name) == {"/data", "/var/lib/exports/projects/archive-2024"}
    assert len(by_name["/data"]["access_list"]) == 25
    assert by_name["/var/lib/exports/projects/archive-2024"]["access_list"] == ["10.8.20.0/24"]
    assert row.writable_share is False
    assert row.port == 20048


def test_root_escape_and_open_exports_are_scanner_observations(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    a = _host(db_session, test_project, "10.8.20.5")
    # No NFS service known for the host: on the mountd port nxc reached.
    assert _checks(db_session, scan, a) == {("nfs_root_escape", 32767), ("nfs_export_any_host", 32767)}
    rows = {v.plugin_id: v for v in db_session.query(Vulnerability).filter_by(scan_id=scan.id, host_id=a.id)}
    assert rows["nfs_root_escape"].severity.value == "high"
    assert "(root escape:True)" in rows["nfs_root_escape"].plugin_output
    assert "/srv/export" in rows["nfs_export_any_host"].plugin_output
    assert "/home" not in rows["nfs_export_any_host"].plugin_output
    assert all(v.source == VulnerabilitySource.NETEXEC for v in rows.values())
    # A misconfiguration, like the other NetExec checks — not a vulnerability.
    assert {vuln_kind(v.check_id, v.severity) for v in rows.values()} == {"misconfiguration"}

    # root escape:False / None and restricted exports are not findings; "*"
    # is any host, as "Everyone" is.
    assert _checks(db_session, scan, _host(db_session, test_project, "10.8.20.6")) == set()
    assert _checks(db_session, scan, _host(db_session, test_project, "10.8.20.7")) == set()
    assert _checks(db_session, scan, _host(db_session, test_project, "10.8.20.8")) == {("nfs_export_any_host", 2049)}


def test_the_port_on_an_nfs_line_is_the_mount_daemon(db_session, test_project):
    """nxc logs every NFS line with mountd's port (nxc/protocols/nfs.py
    create_conn_obj), not 2049: it is recorded as mountd, not a second NFS
    service (production 2026-09-28: 20-odd dynamic ports named "nfs")."""
    _parse(db_session, test_project)
    services = {
        (h.ip_address, p.port_number): p.service_name
        for h in db_session.query(models.Host).filter_by(project_id=test_project.id)
        for p in h.ports
    }
    assert services[("10.8.20.5", 32767)] == "mountd"
    assert services[("10.8.20.6", 20048)] == "mountd"
    assert services[("10.8.20.8", 2049)] == "nfs"


def test_findings_go_on_the_nfs_service_the_host_already_has(db_session, test_project, tmp_path):
    """When a port scan recorded 2049/tcp open, the NFS observations belong
    there — the finding pointed at a port the host's list did not show."""
    host = models.Host(project_id=test_project.id, ip_address="10.8.20.5", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=2049, protocol="tcp", state="open", service_name="nfs"))
    db_session.flush()

    _, scan = _parse(db_session, test_project)
    assert _checks(db_session, scan, host) == {("nfs_root_escape", 2049), ("nfs_export_any_host", 2049)}
    rows = db_session.query(Vulnerability).filter_by(scan_id=scan.id, host_id=host.id).all()
    assert all("mount daemon on port 32767" in v.plugin_output for v in rows)
    # The NetExec row keeps the port nxc reported.
    assert _row(db_session, scan, host, "nfs").port == 32767


def test_a_status_line_on_another_service_does_not_end_a_table(db_session, test_project, tmp_path):
    """nxc runs targets in parallel; a line about the address's other service
    arriving mid-table no longer ends the NFS table."""
    lines = [
        "NFS         10.9.0.1        20048  NONE             [*] Supported NFS versions: (3, 4) (root escape:False)",
        "NFS         10.9.0.1        20048  NONE             UID        Perms    Storage Usage    Share                          Access List    ",
        "NFS         10.9.0.1        20048  NONE             ---        -----    -------------    -----                          -----------    ",
        "SMB         10.9.0.1        445    NONE             [*] Unix - Samba (name:X) (domain:y) (signing:True) (SMBv1:False)",
        "NFS         10.9.0.1        20048  NONE             -          ---      ---/---          /srv                           Everyone       ",
    ]
    path = tmp_path / "nxc.txt"
    path.write_text("\n".join(lines) + "\n")
    scan = NetexecParser(db_session).parse_file(str(path), "nxc.txt", project_id=test_project.id)
    host = _host(db_session, test_project, "10.9.0.1")
    assert [s["name"] for s in _row(db_session, scan, host, "nfs").shares] == ["/srv"]


def test_smb_and_nfs_tables_on_one_host_stay_apart(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    host = _host(db_session, test_project, "10.8.20.8")
    assert [s["name"] for s in _row(db_session, scan, host, "smb").shares] == ["public"]
    assert [s["name"] for s in _row(db_session, scan, host, "nfs").shares] == ["/exports/pub"]


def test_the_nfs_lines_are_no_longer_reported_as_uninterpreted(db_session, test_project):
    parser, _ = _parse(db_session, test_project)
    shapes = [s["shape"] for s in parser.uninterpreted["shapes"]]
    # Only the --enum-shares directory listing is left unread: its "[+] /share"
    # heading and its three table rows.
    assert parser.uninterpreted["total"] == 4, shapes
    assert not any("Supported NFS versions" in s for s in shapes)


def test_the_backfill_reads_stored_rows_by_the_same_rule(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    a = _host(db_session, test_project, "10.8.20.5")
    before = _checks(db_session, scan, a)
    assert before == {("nfs_root_escape", 32767), ("nfs_export_any_host", 32767)}
    db_session.query(Vulnerability).filter_by(scan_id=scan.id).delete()
    db_session.flush()
    backfill_misconfigs(db_session, test_project.id)
    assert _checks(db_session, scan, a) == before


def test_repair_renames_mount_daemon_ports_only_netexec_named(db_session, test_project):
    """Ports stored before 2.430.1: renamed "mountd" only when NetExec alone
    called them "nfs" — never 2049, a confident identification, or a port
    another tool also called "nfs"."""
    from app.services.netexec_repair import repair_nfs_mount_ports

    nxc = models.Scan(project_id=test_project.id, filename="old-nxc.txt", tool_name="netexec", scan_type="netexec")
    nmap = models.Scan(project_id=test_project.id, filename="scan.xml", tool_name="nmap", scan_type="nmap")
    host = models.Host(project_id=test_project.id, ip_address="10.8.30.1", state="up")
    db_session.add_all([nxc, nmap, host])
    db_session.flush()

    def port(number, *, conf=None, named_by=(nxc,)):
        p = models.Port(host_id=host.id, port_number=number, protocol="tcp", state="open",
                        service_name="nfs", service_conf=conf)
        db_session.add(p)
        db_session.flush()
        for scan in named_by:
            db_session.add(models.PortScanHistory(port_id=p.id, scan_id=scan.id, state_at_scan="open", service_name="nfs"))
        return p

    mountd = port(32767)
    also_nmap = port(20048, named_by=(nxc, nmap))
    nfs = port(2049)
    confident = port(635, conf=10)
    db_session.commit()

    expected = {"nfs_ports_renamed_mountd": 1, "nfs_port_records_renamed": 1, "nfs_ports_kept_other_tool": 1}
    assert repair_nfs_mount_ports(db_session, project_id=test_project.id) == expected
    db_session.refresh(mountd)
    assert mountd.service_name == "nfs"  # a dry run changes nothing

    assert repair_nfs_mount_ports(db_session, project_id=test_project.id, apply=True) == expected
    db_session.commit()
    for p in (mountd, also_nmap, nfs, confident):
        db_session.refresh(p)
    assert [p.service_name for p in (mountd, also_nmap, nfs, confident)] == ["mountd", "nfs", "nfs", "nfs"]
    history = db_session.query(models.PortScanHistory).filter_by(port_id=mountd.id).one()
    assert history.service_name == "mountd"
    # Idempotent.
    assert repair_nfs_mount_ports(db_session, project_id=test_project.id, apply=True) == {
        "nfs_ports_renamed_mountd": 0, "nfs_port_records_renamed": 0, "nfs_ports_kept_other_tool": 1}


def test_rules():
    line = "NFS 10.0.0.1 2049 NONE [*] Supported NFS versions: (3) (root escape:True)"
    assert netexec_line_checks("nfs", line) == ["nfs_root_escape"]
    assert netexec_line_checks("nfs", line.replace("True", "None")) == []
    assert netexec_line_checks("nfs", "", shares=[
        {"protocol": "nfs", "name": "/a", "access_list": ["10.0.0.0/8"]},
    ]) == []
    # An SMB share is never an NFS export.
    assert netexec_line_checks("smb", "", shares=[{"name": "x", "access_list": ["Everyone"]}]) == []
    assert writable_share([{"protocol": "nfs", "name": "/a", "permissions": "r-x"}]) is False
    assert writable_share([{"protocol": "nfs", "name": "/a", "permissions": "rw-"}]) is True
    assert {"nfs_root_escape", "nfs_export_any_host"} <= set(CHECKS)
